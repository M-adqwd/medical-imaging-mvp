"""分割 SEG 服务（契约 §1.4 / §3.3）。

版本规则：每次保存生成新 version，latest 指向最新，老版本不删除。
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import not_found, state_conflict, validation_error
from app.core.ids import new_dicom_uid
from app.core.security import Principal
from app.adapters.dicomweb import store
from app.dicom import mask as mask_builder
from app.dicom import seg as seg_builder
from app.models.case import Case
from app.models.enums import SegmentAlgorithm, SegmentState
from app.models.segment import Segment, SegmentVersion
from app.models.state_machines import SEGMENT_TRANSITIONS
from app.services import mask_edit
from app.services import segmentation as seg_engine
from app.services import state as state_svc

# 本地分割来源标识。写进 SEG 的私有块与 ContentCreatorName，
# 让"这份像素是谁算的"在任何下游都查得到。
GROW_ALGORITHM = "algo:seed-grow-v1"
CROP_ALGORITHM = "algo:crop-v1"
SMOOTH_ALGORITHM = "algo:smooth-v1"

# 可被编辑的源分割状态。withdrawn 不在其中：撤回的对象再派生出新版本会让
# 审计链看起来像"撤回后又被修改"，而撤回本身是不可逆的临床动作。
EDITABLE_STATES = {
    SegmentState.DRAFT.value,
    SegmentState.SUGGESTED.value,
    SegmentState.CONFIRMED.value,
}


def get(db: Session, seg_uid: str) -> Segment:
    s = db.get(Segment, seg_uid)
    if s is None:
        raise not_found("Segment", seg_uid)
    return s


def get_version(db: Session, seg_uid: str, version: int) -> SegmentVersion | None:
    return db.get(SegmentVersion, {"seg_uid": seg_uid, "version": version})


def latest_of(db: Session, seg_uid: str) -> Segment:
    return get(db, seg_uid)


def list_by_case(db: Session, case_id: str, *, include_withdrawn: bool = False) -> list[Segment]:
    stmt = select(Segment).where(Segment.case_id == case_id)
    if not include_withdrawn:
        stmt = stmt.where(Segment.state != SegmentState.WITHDRAWN)
    return list(db.scalars(stmt.order_by(Segment.created_at.desc())).all())


def create_manual(
    db: Session,
    case: Case,
    principal: Principal,
    *,
    study_instance_uid: str,
    segment_name: str,
    seg_content_b64: str | None,
    series_instance_uid: str | None = None,
) -> Segment:
    if not segment_name.strip():
        raise validation_error("segment_name 不能为空", field="segment_name")
    seg_uid = new_dicom_uid()
    seg = Segment(
        seg_uid=seg_uid,
        case_id=case.id,
        study_instance_uid=study_instance_uid,
        series_instance_uid=series_instance_uid,
        segment_name=segment_name[:60],
        algorithm=SegmentAlgorithm.MANUAL.value,
        state=SegmentState.DRAFT,
        version=1,
        creator_id=principal.user_id,
    )
    db.add(seg)
    db.flush()
    _persist_content(db, case, seg, seg_content_b64)
    _snapshot(db, seg)
    audit.record(
        db,
        category="object",
        action="create",
        actor_id=principal.user_id,
        target_type="segment",
        target_uid=seg_uid,
        after_state=SegmentState.DRAFT.value,
    )
    return seg


def create_from_ai(
    db: Session,
    case: Case,
    principal: Principal,
    *,
    study_instance_uid: str,
    segment_name: str,
    model: str,
    model_version: str,
    seg_content_b64: str | None,
    stats: dict | None = None,
) -> Segment:
    """AI 输出固定为 suggested 态（PRD FR-04：必须由医师确认）。"""
    seg_uid = new_dicom_uid()
    seg = Segment(
        seg_uid=seg_uid,
        case_id=case.id,
        study_instance_uid=study_instance_uid,
        segment_name=segment_name[:60],
        algorithm=f"ai:{model}:{model_version}",
        state=SegmentState.SUGGESTED,
        version=1,
        creator_id=principal.user_id,
        seg_meta={"model": model, "model_version": model_version, "stats": stats or {}},
    )
    db.add(seg)
    db.flush()
    _persist_content(db, case, seg, seg_content_b64)
    _snapshot(db, seg)
    audit.record(
        db,
        category="ai",
        action="ai_segment_created",
        actor_id=principal.user_id,
        target_type="segment",
        target_uid=seg_uid,
        after_state=SegmentState.SUGGESTED.value,
        extra={"model": model, "model_version": model_version, "study": study_instance_uid},
    )
    return seg


def create_from_seed(
    db: Session,
    case: Case,
    principal: Principal,
    *,
    study_instance_uid: str,
    seed_point: dict,
    segment_name: str,
    hu_tolerance: float,
    radius_mm: float,
    series_instance_uid: str | None = None,
) -> Segment:
    """种子点区域生长 → 带像素的 SEG（draft 态，待医师确认）。

    这是系统第一条能产出**真实像素**的路径。它不冒充 AI：种子由人给，
    SEG 里记着 `SEMIAUTOMATIC` 与 `clinical_use=NO`，参数全部回传可复现。
    """
    if not segment_name.strip():
        raise validation_error("segment_name 不能为空", field="segment_name")

    coordinates = seed_point.get("coordinates") if isinstance(seed_point, dict) else None
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) != 3:
        raise validation_error(
            "seed 必须是 {geometry, coordinates:[x, y, z]} 结构", field="seed"
        )
    seed = (float(coordinates[0]), float(coordinates[1]), float(coordinates[2]))

    try:
        volume = seg_engine.load_volume(study_instance_uid, series_instance_uid)
    except seg_engine.SeriesNotFound as exc:
        # 影像不在归档里就不该产出一个空分割——那会让"分割失败"看起来像"分割成功"
        raise validation_error(
            str(exc), field="study_instance_uid", reason="series_not_found"
        ) from exc

    try:
        grown = seg_engine.grow_from_seed(
            volume, seed, hu_tolerance=hu_tolerance, radius_mm=radius_mm
        )
    except ValueError as exc:
        raise validation_error(str(exc), field="seed", reason="seed_outside_volume") from exc

    geometry = volume.geometry
    seg_uid = new_dicom_uid()
    seg = Segment(
        seg_uid=seg_uid,
        case_id=case.id,
        study_instance_uid=study_instance_uid,
        series_instance_uid=series_instance_uid or volume.series_instance_uid,
        segment_name=segment_name[:60],
        algorithm=GROW_ALGORITHM,
        state=SegmentState.DRAFT,
        version=1,
        creator_id=principal.user_id,
        seg_meta={
            "source": "local-grow",
            "seed": {"geometry": seed_point.get("geometry"), "coordinates": list(seed)},
            "stats": grown.stats,
            "sop_uids": volume.sop_uids[:1],
        },
    )
    db.add(seg)
    db.flush()

    seg.seg_content = mask_builder.build_seg_from_mask(
        seg_uid=seg.seg_uid,
        study_instance_uid=study_instance_uid,
        series_instance_uid=seg.series_instance_uid,
        patient_id=case.patient_ref,
        patient_name=case.patient_name or "",
        segment_name=seg.segment_name,
        algorithm=GROW_ALGORITHM,
        mask=grown.mask,
        spacing=geometry.spacing,
        origin=geometry.origin,
        row_dir=geometry.row_dir,
        col_dir=geometry.col_dir,
        referenced_sop_uids=volume.sop_uids,
        clinical_use=False,
        producer="local-seed-region-growing",
        method=grown.stats.get("method", ""),
        study_date=case.study_date,
    )
    store.store_bytes(seg.seg_uid, seg.seg_content)
    _snapshot(db, seg)

    audit.record(
        db,
        category="object",
        action="grow",
        actor_id=principal.user_id,
        target_type="segment",
        target_uid=seg_uid,
        after_state=SegmentState.DRAFT.value,
        extra={
            "seed_hu": grown.stats.get("seed_hu"),
            "hu_window": grown.stats.get("hu_window"),
            "voxel_count": grown.stats.get("voxel_count"),
            "touches_window_edge": grown.stats.get("touches_window_edge"),
        },
    )
    return seg


def merge(
    db: Session,
    case: Case,
    principal: Principal,
    *,
    seg_uids: list[str],
    result_name: str,
    operation: str,
) -> Segment:
    if len(seg_uids) < 2:
        raise validation_error("合并至少需要两个 SEG", field="seg_uids")
    allowed_inputs = {SegmentState.CONFIRMED.value, SegmentState.SUGGESTED.value}
    sources: list[Segment] = []
    for uid in seg_uids:
        s = get(db, uid)
        if s.case_id != case.id:
            raise validation_error("被合并的 SEG 必须属于同一 Case", seg_uid=uid, case_id=case.id)
        if s.state.value not in allowed_inputs:
            raise state_conflict(
                f"SEG {uid} 当前状态 {s.state.value} 不允许参与合并",
                current_state=s.state.value,
                allowed_states=sorted(allowed_inputs),
            )
        sources.append(s)

    # 先把掩膜算出来：布尔运算失败时不该在库里留一个"看起来成功"的空合并对象。
    try:
        merged_mask, geom = mask_builder.merge_masks(
            [s.seg_content for s in sources], operation
        )
    except ValueError as exc:
        raise validation_error(
            str(exc), field="seg_uids", reason="pixel_merge_failed"
        ) from exc

    voxel_count = int(merged_mask.sum())

    seg_uid = new_dicom_uid()
    merged = Segment(
        seg_uid=seg_uid,
        case_id=case.id,
        study_instance_uid=sources[0].study_instance_uid,
        segment_name=result_name[:60],
        algorithm=SegmentAlgorithm.MERGED.value,
        base_seg_uids=[{"uid": s.seg_uid, "version": s.version} for s in sources],
        state=SegmentState.DRAFT,
        version=1,
        creator_id=principal.user_id,
        seg_meta={
            "operation": operation,
            "sources": [s.seg_uid for s in sources],
            "voxel_count": voxel_count,
        },
    )
    db.add(merged)
    db.flush()

    merged.seg_content = mask_builder.build_seg_from_mask(
        seg_uid=merged.seg_uid,
        study_instance_uid=merged.study_instance_uid,
        series_instance_uid=None,
        patient_id=case.patient_ref,
        patient_name=case.patient_name or "",
        segment_name=merged.segment_name,
        algorithm=SegmentAlgorithm.MERGED.value,
        mask=merged_mask,
        spacing=geom.spacing,
        origin=geom.origin,
        row_dir=geom.row_dir,
        col_dir=geom.col_dir,
        referenced_sop_uids=None,
        clinical_use=False,
        producer="boolean-merge",
        method=operation,
        study_date=case.study_date,
    )
    store.store_bytes(merged.seg_uid, merged.seg_content)
    _snapshot(db, merged)

    audit.record(
        db,
        category="object",
        action="merge",
        actor_id=principal.user_id,
        target_type="segment",
        target_uid=seg_uid,
        after_state=SegmentState.DRAFT.value,
        extra={
            "operation": operation,
            "sources": [s.seg_uid for s in sources],
            "voxel_count": voxel_count,
        },
    )
    return merged


def _load_pixels(seg: Segment) -> tuple[np.ndarray, mask_builder.MaskGeometry]:
    """取出源分割的掩膜与几何。没有像素就明确拒绝，不产出空壳派生对象。"""
    mask = mask_builder.seg_mask_array(seg.seg_content)
    geom = mask_builder.seg_geometry(seg.seg_content)
    if mask is None or geom is None:
        raise validation_error(
            f"分割 {seg.seg_uid} 没有像素数据（仅元数据骨架），无法编辑",
            field="seg_uid",
            reason="no_pixel_data",
        )
    return mask, geom


def _ensure_editable(db: Session, case: Case, seg: Segment) -> None:
    if seg.case_id != case.id:
        raise validation_error("被编辑的分割必须属于同一 Case", seg_uid=seg.seg_uid, case_id=case.id)
    if seg.state.value not in EDITABLE_STATES:
        raise state_conflict(
            f"SEG {seg.seg_uid} 当前状态 {seg.state.value} 不允许编辑",
            current_state=seg.state.value,
            allowed_states=sorted(EDITABLE_STATES),
        )


def _persist_derived(
    db: Session,
    case: Case,
    principal: Principal,
    *,
    source: Segment,
    mask,
    geom: mask_builder.MaskGeometry,
    result_name: str,
    algorithm: str,
    producer: str,
    method: str,
    seg_meta: dict,
    action: str,
) -> Segment:
    """把编辑结果写成一份新的 draft 分割。

    编辑不原地改源分割：源对象可能已被规划引用、已确认，甚至属于别人。
    派生一个新对象既保住了版本链（base_seg_uids 指回源），也让"编辑前是什么样"
    永远查得到。
    """
    seg_uid = new_dicom_uid()
    seg = Segment(
        seg_uid=seg_uid,
        case_id=case.id,
        study_instance_uid=source.study_instance_uid,
        series_instance_uid=source.series_instance_uid,
        segment_name=result_name[:60],
        algorithm=algorithm,
        base_seg_uids=[{"uid": source.seg_uid, "version": source.version}],
        state=SegmentState.DRAFT,
        version=1,
        creator_id=principal.user_id,
        seg_meta=seg_meta,
    )
    db.add(seg)
    db.flush()

    seg.seg_content = mask_builder.build_seg_from_mask(
        seg_uid=seg.seg_uid,
        study_instance_uid=seg.study_instance_uid,
        # SEG 自己的 SeriesInstanceUID 由 build_seg_from_mask 生成，
        # 不能沿用源影像序列 —— 那把分割混进了原图序列里
        series_instance_uid=None,
        patient_id=case.patient_ref,
        patient_name=case.patient_name or "",
        segment_name=seg.segment_name,
        algorithm=algorithm,
        mask=mask,
        spacing=geom.spacing,
        origin=geom.origin,
        row_dir=geom.row_dir,
        col_dir=geom.col_dir,
        referenced_sop_uids=None,
        clinical_use=False,
        producer=producer,
        method=method,
        study_date=case.study_date,
    )
    store.store_bytes(seg.seg_uid, seg.seg_content)
    _snapshot(db, seg)

    audit.record(
        db,
        category="object",
        action=action,
        actor_id=principal.user_id,
        target_type="segment",
        target_uid=seg_uid,
        after_state=SegmentState.DRAFT.value,
        extra={
            "source_seg_uid": source.seg_uid,
            "source_version": source.version,
            **seg_meta.get("stats", {}),
        },
    )
    return seg


def crop(
    db: Session,
    case: Case,
    principal: Principal,
    *,
    seg: Segment,
    min_lps: list[float],
    max_lps: list[float],
    result_name: str | None = None,
) -> Segment:
    """按 LPS 盒裁切：保留盒内体素，盒外置 0（几何不变）。"""
    _ensure_editable(db, case, seg)
    mask, geom = _load_pixels(seg)

    try:
        cropped, stats = mask_edit.crop_to_box(mask, geom, min_lps, max_lps)
    except ValueError as exc:
        raise validation_error(str(exc), field="box", reason="crop_failed") from exc

    if not cropped.any():
        # 空结果不写库：一份 0 体素的分割在列表里显示「0.00 mL」，
        # 看起来像"裁得很准"，实际是盒子根本没罩住结构
        raise validation_error(
            "裁切盒内没有属于该分割的体素，请调整中心点或半径",
            field="box",
            reason="empty_after_crop",
        )

    return _persist_derived(
        db,
        case,
        principal,
        source=seg,
        mask=cropped,
        geom=geom,
        result_name=result_name or f"{seg.segment_name}（裁切）",
        algorithm=CROP_ALGORITHM,
        producer="mask-crop",
        method="lps-box-mask-out",
        seg_meta={
            "source": "mask-edit",
            "operation": "crop",
            "stats": stats,
            "source_seg_uid": seg.seg_uid,
            "source_version": seg.version,
        },
        action="crop",
    )


def smooth(
    db: Session,
    case: Case,
    principal: Principal,
    *,
    seg: Segment,
    method: str = "median",
    radius_mm: float = 1.0,
    closing_mm: float = 0.0,
    result_name: str | None = None,
) -> Segment:
    """平滑分割（默认中值滤波，可选形态学开/闭）。几何不变。"""
    _ensure_editable(db, case, seg)
    if method not in ("median", "morphology"):
        raise validation_error(
            f"不支持的平滑方法 {method}，可选 median / morphology", field="method"
        )
    if method == "median" and (radius_mm or 0) <= 0:
        raise validation_error("中值平滑需要大于 0 的半径", field="radius_mm")
    if method == "morphology" and (radius_mm or 0) <= 0 and (closing_mm or 0) <= 0:
        raise validation_error(
            "形态学平滑需要开运算或闭运算半径大于 0，否则这次平滑什么也不会改变",
            field="radius_mm",
        )
    mask, geom = _load_pixels(seg)

    try:
        smoothed, stats = mask_edit.smooth_mask(
            mask, geom, method=method, radius_mm=radius_mm, closing_mm=closing_mm
        )
    except ValueError as exc:
        raise validation_error(str(exc), field="radius_mm", reason="smooth_failed") from exc

    if not smoothed.any():
        raise validation_error(
            "平滑后掩膜为空：结构尺寸小于核半径，整块被吃掉了。请调小半径或改用中值平滑",
            field="radius_mm",
            reason="empty_after_smooth",
        )

    return _persist_derived(
        db,
        case,
        principal,
        source=seg,
        mask=smoothed,
        geom=geom,
        result_name=result_name or f"{seg.segment_name}（平滑）",
        algorithm=SMOOTH_ALGORITHM,
        producer=f"mask-smooth-{method}",
        method=f"{method}-r{stats['radius_mm']}-c{stats['closing_mm']}",
        seg_meta={
            "source": "mask-edit",
            "operation": "smooth",
            "stats": stats,
            "source_seg_uid": seg.seg_uid,
            "source_version": seg.version,
        },
        action="smooth",
    )


def confirm(db: Session, seg: Segment, principal: Principal) -> Segment:
    before = seg.state.value
    state_svc.apply(
        db,
        obj=seg,
        transitions=SEGMENT_TRANSITIONS,
        action="confirm",
        principal=principal,
        target_type="segment",
        target_uid=seg.seg_uid,
        category="ai" if seg.algorithm.startswith("ai:") else "object",
    )
    seg.confirmed_by = principal.user_id
    seg.updated_at = datetime.now(timezone.utc)
    db.flush()
    _sync_snapshot(db, seg)
    audit.record(
        db,
        category="ai",
        action="physician_confirm",
        actor_id=principal.user_id,
        target_type="segment",
        target_uid=seg.seg_uid,
        before_state=before,
        after_state=seg.state.value,
        extra={"algorithm": seg.algorithm},
    )
    return seg


def revise(db: Session, seg: Segment, principal: Principal) -> Segment:
    """修订 = 版本 +1 并回到 draft（契约 §1.8.1）。"""
    before = seg.state.value
    seg.version += 1
    state_svc.apply(
        db,
        obj=seg,
        transitions=SEGMENT_TRANSITIONS,
        action="revise",
        principal=principal,
        target_type="segment",
        target_uid=seg.seg_uid,
    )
    seg.confirmed_by = None
    seg.updated_at = datetime.now(timezone.utc)
    db.flush()
    _snapshot(db, seg)
    audit.record(
        db,
        category="object",
        action="revise",
        actor_id=principal.user_id,
        target_type="segment",
        target_uid=seg.seg_uid,
        before_state=before,
        after_state=seg.state.value,
        extra={"version": seg.version},
    )
    return seg


def withdraw(db: Session, seg: Segment, principal: Principal) -> Segment:
    state_svc.apply(
        db,
        obj=seg,
        transitions=SEGMENT_TRANSITIONS,
        action="withdraw",
        principal=principal,
        target_type="segment",
        target_uid=seg.seg_uid,
    )
    db.flush()
    _sync_snapshot(db, seg)
    return seg


def _persist_content(db: Session, case: Case, seg: Segment, seg_content_b64: str | None) -> None:
    data = seg_builder.decode(seg_content_b64) if seg_content_b64 else None
    if data:
        seg.seg_content = seg_builder.wrap_existing_seg(data, seg.algorithm, seg.segment_name)
    else:
        header = seg_builder.build_seg_header(
            seg_uid=seg.seg_uid,
            study_instance_uid=seg.study_instance_uid,
            series_instance_uid=seg.series_instance_uid,
            patient_id=case.patient_ref,
            patient_name=case.patient_name or "",
            segment_name=seg.segment_name,
            algorithm=seg.algorithm,
            referenced_series=[],
            study_date=case.study_date,
        )
        seg.seg_content = seg_builder.to_bytes(header)
    if seg.seg_content:
        store.store_bytes(seg.seg_uid, seg.seg_content)


def _snapshot(db: Session, seg: Segment) -> None:
    row = get_version(db, seg.seg_uid, seg.version)
    payload = {
        "segment_name": seg.segment_name,
        "algorithm": seg.algorithm,
        "base_seg_uids": seg.base_seg_uids,
        "seg_meta": seg.seg_meta,
        "creator_id": seg.creator_id,
    }
    if row is None:
        db.add(
            SegmentVersion(
                seg_uid=seg.seg_uid,
                version=seg.version,
                state=seg.state,
                seg_content=seg.seg_content,
                snapshot=payload,
            )
        )
    else:
        row.state = seg.state
        row.seg_content = seg.seg_content
        row.snapshot = payload
    db.flush()


def _sync_snapshot(db: Session, seg: Segment) -> None:
    row = get_version(db, seg.seg_uid, seg.version)
    if row is not None:
        row.state = seg.state
        db.flush()
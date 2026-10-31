"""测量 SR 服务（契约 §1.3 / §3.2）。SR 不做版本化，只支持 draft → saved → withdrawn。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.schemas import GEOMETRY_PATIENT_LPS_MM, LEGACY_GEOMETRY_ALIASES
from app.core import audit
from app.core.errors import not_found, state_conflict, validation_error
from app.core.ids import new_dicom_uid
from app.core.security import Principal
from app.dicom import sr as sr_builder
from app.dicom.store import archive
from app.adapters.dicomweb import store
from app.models.case import Case
from app.models.enums import MeasurementState, MeasurementType
from app.models.measurement import Measurement
from app.models.state_machines import MEASUREMENT_TRANSITIONS
from app.services import state as state_svc

NUMERIC_TYPES = {"length", "angle", "area", "volume"}
TEXT_TYPES = {"free-text", "arrow"}
DEFAULT_UNITS = {"length": "mm", "angle": "°", "area": "mm²", "volume": "mm³"}

# 测量类型 → (graphic_type, graphic_data 长度)，坐标契约 §4.1。
# 不匹配一律 422：长度与类型不符的 GraphicData 是非法的，不能"先存下来再说"。
GRAPHIC_SHAPE_BY_TYPE: dict[str, tuple[str, int]] = {
    "length": ("POLYLINE", 6),   # 两端点
    "angle": ("POLYLINE", 9),    # 三点，顶点在中间
    "area": ("POINT", 3),        # ROI 质心（矩形 / 椭圆均是四点质心）
}


def _normalize_coord_ref(coord_ref: dict) -> dict:
    """写入即归一：历史 geometry 写法改写为规范值，库里永远只留规范值。

    坐标契约 §3.3。不建长期别名表 —— 这两个历史值一次性收敛后就不再接受新增。
    """
    if not coord_ref:
        return coord_ref
    geometry = str(coord_ref.get("geometry") or "").strip()
    normalized = LEGACY_GEOMETRY_ALIASES.get(geometry, geometry)
    if normalized != GEOMETRY_PATIENT_LPS_MM:
        # 持久化层再兜一次：库里不允许出现规范值以外的写法（契约 §9 X1）
        raise validation_error(
            f"geometry 必须是 {GEOMETRY_PATIENT_LPS_MM!r}，当前为 {geometry!r}",
            field="coord_ref.geometry",
            allowed=[GEOMETRY_PATIENT_LPS_MM, *sorted(LEGACY_GEOMETRY_ALIASES)],
        )
    coord_ref["geometry"] = normalized
    return coord_ref


def _resolve_frame_of_reference(sop_instance_uid: str) -> str:
    """按 sop_instance_uid 从影像头派生 FrameOfReferenceUID（坐标契约 §6.1 / A9）。

    前端拿不到可信值（Cornerstone 4.22.13 的注解工具从不写
    metadata.frameOfReferenceUID），所以一律服务端派生。取不到就 422 ——
    没有 FOR 的坐标不知道属于哪个空间，写进 SR 是无法解读的产物。
    """
    record = archive.get_instance(sop_instance_uid)
    for_uid = ""
    if record is not None:
        for_uid = str(record.meta.get("FrameOfReferenceUID") or "").strip()
    if not for_uid:
        raise validation_error(
            "无法按 sop_instance_uid 取到 FrameOfReferenceUID，"
            "坐标没有空间归属，拒绝生成 SCOORD3D",
            field="coord_ref.sop_instance_uid",
            sop_instance_uid=sop_instance_uid,
        )
    return for_uid


def _validate_coord_ref(
    coord_ref: dict,
    *,
    type_: str,
    study_instance_uid: str,
    series_instance_uid: str | None,
) -> None:
    """带坐标的测量必须自带完整上下文，缺一样就 422。"""
    shape = GRAPHIC_SHAPE_BY_TYPE.get(type_)
    if shape is not None:
        want_type, want_len = shape
        got_type = coord_ref.get("graphic_type")
        got_len = len(coord_ref.get("graphic_data") or [])
        if got_type != want_type or got_len != want_len:
            raise validation_error(
                f"{type_} 测量要求 graphic_type={want_type} 且 graphic_data 长度为 {want_len}，"
                f"当前为 {got_type} / {got_len}",
                field="coord_ref.graphic_data",
                expected={"graphic_type": want_type, "graphic_data_length": want_len},
                actual={"graphic_type": got_type, "graphic_data_length": got_len},
            )

    if coord_ref.get("study_instance_uid") != study_instance_uid:
        raise validation_error(
            "coord_ref.study_instance_uid 与测量的 study_instance_uid 不一致",
            field="coord_ref.study_instance_uid",
            coord_ref_study=coord_ref.get("study_instance_uid"),
            measurement_study=study_instance_uid,
        )

    # 顶层 series_instance_uid 缺失时不再回落到 f"{sr_uid}.1"（契约 §7.2 / A6）
    if not series_instance_uid:
        raise validation_error(
            "带坐标的测量必须在顶层提供 series_instance_uid",
            field="series_instance_uid",
        )

    _resolve_frame_of_reference(coord_ref["sop_instance_uid"])


def _validate(kind: str, value: float | None, unit: str | None, text: str | None) -> str:
    # TC-08 AC-0：体积只能来自带像素的 SEG 计算，不接受手填。
    # 手填的数字会被落成 SNOMED 118565006 的标准体积测量记录，下游无法证伪 ——
    # 这是"产出看起来有依据的数字"，比报错严重得多。
    if kind == "volume":
        raise validation_error(
            "体积测量必须由带像素的 SEG 计算得出，不接受手工填写",
            field="type",
            reason="volume_requires_seg_pixels",
            hint="请在分割面板确认分割后使用「计算体积」，不要在测量面板手填",
        )
    if kind in NUMERIC_TYPES:
        if value is None:
            raise validation_error(f"{kind} 类型必须有 value", field="value")
        if unit is not None and not unit.strip():
            # 显式传空串视为「不允许无单位」；完全未传则走默认值
            raise validation_error(f"{kind} 类型必须有 unit，不允许无单位", field="unit")
        if not unit:
            return DEFAULT_UNITS.get(kind, "")
        return unit
    if kind in TEXT_TYPES and not (text or "").strip():
        raise validation_error(f"{kind} 类型必须有 text", field="text")
    return unit or ""


def get(db: Session, sr_uid: str) -> Measurement:
    m = db.get(Measurement, sr_uid)
    if m is None:
        raise not_found("Measurement", sr_uid)
    return m


def list_by_case(db: Session, case_id: str, *, include_withdrawn: bool = False) -> list[Measurement]:
    stmt = select(Measurement).where(Measurement.case_id == case_id)
    if not include_withdrawn:
        stmt = stmt.where(Measurement.state != MeasurementState.WITHDRAWN)
    return list(db.scalars(stmt.order_by(Measurement.created_at.desc())).all())


def create(
    db: Session,
    case: Case,
    principal: Principal,
    *,
    study_instance_uid: str,
    type_: str,
    value: float | None,
    unit: str | None,
    text: str | None,
    coord_ref: dict,
    cross_series: bool,
    series_instance_uid: str | None = None,
) -> Measurement:
    try:
        mtype = MeasurementType(type_)
    except ValueError as exc:
        raise validation_error(
            f"非法测量类型：{type_}", field="type", allowed=[e.value for e in MeasurementType]
        ) from exc

    unit = _validate(type_, value, unit if unit is not None else DEFAULT_UNITS.get(type_), text)

    coord_ref = _normalize_coord_ref(dict(coord_ref or {}))
    if coord_ref:
        _validate_coord_ref(
            coord_ref,
            type_=type_,
            study_instance_uid=study_instance_uid,
            series_instance_uid=series_instance_uid,
        )

    sr_uid = new_dicom_uid()
    m = Measurement(
        sr_uid=sr_uid,
        case_id=case.id,
        study_instance_uid=study_instance_uid,
        series_instance_uid=series_instance_uid,
        type=mtype,
        value=value,
        unit=unit,
        text=text,
        coord_ref=coord_ref or {},
        cross_series=cross_series,
        state=MeasurementState.DRAFT,
        creator_id=principal.user_id,
    )
    db.add(m)
    db.flush()
    _regenerate_sr(db, case, m)
    audit.record(
        db,
        category="object",
        action="create",
        actor_id=principal.user_id,
        target_type="measurement",
        target_uid=sr_uid,
        after_state=MeasurementState.DRAFT.value,
        extra={"type": type_, "cross_series": cross_series},
    )
    return m


def update_draft(
    db: Session,
    m: Measurement,
    principal: Principal,
    *,
    payload: dict,
    if_match: str | None,
) -> Measurement:
    if m.state != MeasurementState.DRAFT:
        raise state_conflict(
            f"只有 draft 态测量可更新，当前为 {m.state.value}",
            current_state=m.state.value,
            hint="已 saved 的 SR 需先 :withdraw 再新建",
        )
    if if_match is None:
        raise validation_error("PUT 必须携带 If-Match 头", header="If-Match")

    type_ = payload.get("type", m.type.value)
    value = payload.get("value", m.value)
    unit = payload.get("unit", m.unit)
    text = payload.get("text", m.text)
    if unit is not None and not str(unit).strip():
        raise validation_error(f"{type_} 类型必须有 unit，不允许无单位", field="unit")
    unit = _validate(type_, value, unit, text)
    try:
        m.type = MeasurementType(type_)
    except ValueError as exc:
        raise validation_error(f"非法测量类型：{type_}", field="type") from exc

    m.value = value
    m.unit = unit
    m.text = text
    if "coord_ref" in payload:
        coord_ref = _normalize_coord_ref(dict(payload["coord_ref"] or {}))
        if coord_ref:
            _validate_coord_ref(
                coord_ref,
                type_=type_,
                study_instance_uid=m.study_instance_uid,
                series_instance_uid=m.series_instance_uid,
            )
        m.coord_ref = coord_ref
    if "cross_series" in payload:
        m.cross_series = bool(payload["cross_series"])
    m.updated_at = datetime.now(timezone.utc)
    db.flush()

    case = db.get(Case, m.case_id)
    _regenerate_sr(db, case, m)
    audit.record(
        db,
        category="object",
        action="update",
        actor_id=principal.user_id,
        target_type="measurement",
        target_uid=m.sr_uid,
        before_state=MeasurementState.DRAFT.value,
        after_state=MeasurementState.DRAFT.value,
    )
    return m


def transition(db: Session, m: Measurement, principal: Principal, action: str) -> Measurement:
    if action == "withdraw" and m.state == MeasurementState.SAVED:
        if m.creator_id != principal.user_id and not principal.is_admin:
            from app.core.errors import permission_denied

            raise permission_denied("只有创建者本人或管理员可撤回已保存的测量")
    if action == "save" and m.state == MeasurementState.DRAFT and (m.text or "").strip() == "":
        if m.type.value in TEXT_TYPES:
            raise validation_error("文本类测量缺少 text，不能保存", field="text")

    state_svc.apply(
        db,
        obj=m,
        transitions=MEASUREMENT_TRANSITIONS,
        action=action,
        principal=principal,
        target_type="measurement",
        target_uid=m.sr_uid,
    )
    if action == "save":
        _regenerate_sr(db, db.get(Case, m.case_id), m)
    return m


def _regenerate_sr(db: Session, case: Case | None, m: Measurement) -> None:
    coord_ref = m.coord_ref or {}
    for_uid = None
    if coord_ref:
        # 存量行可能带 coord_ref 却没有 series / sop（改动前就是这种形状）。
        # 这里显式 422，而不是让 SR 构造抛 ValueError 变成 500（契约 §9）。
        sop_uid = coord_ref.get("sop_instance_uid")
        if not sop_uid:
            raise validation_error(
                "coord_ref.sop_instance_uid 缺失，无法派生 FrameOfReferenceUID",
                field="coord_ref.sop_instance_uid",
            )
        if not m.series_instance_uid:
            raise validation_error(
                "带坐标的测量缺少 series_instance_uid，无法生成源图像引用",
                field="series_instance_uid",
            )
        # FOR UID 由服务端派生，不取自 coord_ref（契约 §6.1）；取不到会在
        # _resolve_frame_of_reference 里 422，不会产出无归属的 SCOORD3D。
        for_uid = _resolve_frame_of_reference(sop_uid)
    ds = sr_builder.build_measurement_sr(
        sr_uid=m.sr_uid,
        study_instance_uid=m.study_instance_uid,
        series_instance_uid=m.series_instance_uid,
        patient_id=case.patient_ref if case else "",
        patient_name=case.patient_name or "" if case else "",
        measurement_type=m.type.value,
        value=m.value,
        unit=m.unit,
        text=m.text,
        coord_ref=coord_ref,
        cross_series=m.cross_series,
        creator_id=m.creator_id,
        study_date=case.study_date if case else None,
        frame_of_reference_uid=for_uid,
    )
    data = sr_builder.to_bytes(ds)
    m.sr_content = data
    store.store_bytes(m.sr_uid, data)
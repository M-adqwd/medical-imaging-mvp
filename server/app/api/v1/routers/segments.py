"""分割 SEG API（契约 §3.3）。"""

from __future__ import annotations

import base64
import zlib

import numpy as np
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.v1 import serializers as ser
from app.api.v1.deps import current_user, db_session, idempotent
from app.api.v1.schemas import (
    SegmentCreate,
    SegmentCrop,
    SegmentGrow,
    SegmentMerge,
    SegmentOut,
    SegmentSmooth,
)
from app.core.errors import ApiError, BusinessCode, not_found
from app.core.security import Principal, require
from app.dicom import mask as mask_builder
from app.services import cases as case_svc
from app.services import segments as svc

router = APIRouter(tags=["segment"])


def _load_mask_or_404(seg) -> tuple[np.ndarray, mask_builder.MaskGeometry]:
    """解出 SEG 的掩膜与栅格几何；无像素时明确 404，不返回空壳。"""
    mask = mask_builder.seg_mask_array(seg.seg_content)
    geo = mask_builder.seg_geometry(seg.seg_content)
    if mask is None or geo is None:
        raise ApiError(
            BusinessCode.NOT_FOUND,
            f"分割 {seg.seg_uid} 没有像素数据（仅元数据骨架），无法叠加/建网格",
            {"code": "NO_PIXEL_DATA"},
        )
    return mask, geo


@router.get("/segments/{seg_uid}/mask")
def get_segment_mask(
    request: Request,
    seg_uid: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> dict:
    """掩膜原始数据（给前端 2D labelmap 叠加）。

    data 是 (frames, rows, cols) C 序 uint8 经 zlib 压缩的 base64——体素顺序与
    归档体数据一致，前端解压后直接按 k*(rows*cols)+j*rows+i 写进 derived labelmap。
    掩膜高度稀疏，zlib 后通常只有几十 KB；裸传会到几十 MB。
    """
    require(principal, "case.read")
    seg = svc.get(db, seg_uid)
    case_svc.get_case(db, seg.case_id, principal)
    mask, geo = _load_mask_or_404(seg)
    raw = mask.astype(np.uint8).tobytes()
    return {
        "seg_uid": seg.seg_uid,
        "name": seg.segment_name,
        "shape": [int(x) for x in mask.shape],
        "spacing": [float(x) for x in geo.spacing],
        "origin": [float(x) for x in geo.origin],
        "row_dir": [float(x) for x in geo.row_dir],
        "col_dir": [float(x) for x in geo.col_dir],
        "encoding": "zlib-deflate",
        "raw_bytes": len(raw),
        "data": base64.b64encode(zlib.compress(raw, 6)).decode("ascii"),
    }


@router.get("/segments/{seg_uid}/mesh")
def get_segment_mesh(
    request: Request,
    seg_uid: str,
    step: int = Query(default=2, ge=1, le=4),
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> dict:
    """分割外表面三角网格（marching cubes，LPS 毫米坐标，给 3D 器官网格渲染）。

    step 是 marching cubes 的降采样步长：2 足够平滑且顶点数约为 1/8；
    要更细的表面可传 1。体素数 <2 时没有可提取的表面，明确 404。
    """
    require(principal, "case.read")
    seg = svc.get(db, seg_uid)
    case_svc.get_case(db, seg.case_id, principal)
    mask, geo = _load_mask_or_404(seg)
    if int(mask.sum()) < 2:
        raise ApiError(
            BusinessCode.NOT_FOUND,
            f"分割 {seg.seg_uid} 的体素数不足以提取表面",
            {"code": "EMPTY_SURFACE"},
        )

    from skimage import measure

    sp_frame, sp_row, sp_col = geo.spacing
    verts, faces, _, _ = measure.marching_cubes(
        mask.astype(np.uint8), level=0.5, spacing=(sp_frame, sp_row, sp_col), step_size=step
    )
    normal = np.cross(geo.row_dir, geo.col_dir)
    # verts 的三列是 (帧, 行, 列) 方向的毫米坐标 → 映射回病人 LPS
    lps = (
        np.asarray(geo.origin, dtype=float)
        + np.asarray(geo.row_dir) * verts[:, 1:2]
        + np.asarray(geo.col_dir) * verts[:, 2:3]
        + normal * verts[:, 0:1]
    )
    return {
        "seg_uid": seg.seg_uid,
        "name": seg.segment_name,
        "vertex_count": int(len(lps)),
        "vertices": [[round(float(x), 3) for x in v] for v in lps],
        "faces": faces.astype(int).tolist(),
    }


@router.post("/cases/{case_id}/segments", response_model=SegmentOut, status_code=201)
@idempotent("POST /api/v1/cases/{case_id}/segments", status_code=201)
def create_segment(
    request: Request,
    case_id: str,
    payload: SegmentCreate,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> SegmentOut:
    require(principal, "segment.write")
    case = case_svc.get_case(db, case_id, principal)
    case_svc.ensure_writable(case)
    seg = svc.create_manual(
        db,
        case,
        principal,
        study_instance_uid=payload.study_instance_uid,
        segment_name=payload.segment_name,
        seg_content_b64=payload.seg_content,
        series_instance_uid=payload.series_instance_uid,
    )
    case_svc.touch(case)
    return ser.segment_out(seg)


@router.get("/cases/{case_id}/segments", response_model=list[SegmentOut])
def list_segments(
    request: Request,
    case_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
    include_withdrawn: bool = Query(default=False),
) -> list[SegmentOut]:
    require(principal, "case.read")
    case_svc.get_case(db, case_id, principal)
    return [ser.segment_out(s) for s in svc.list_by_case(db, case_id, include_withdrawn=include_withdrawn)]


@router.post("/cases/{case_id}/segments/merge", response_model=SegmentOut, status_code=201)
@idempotent("POST /api/v1/cases/{case_id}/segments/merge", status_code=201)
def merge_segments(
    request: Request,
    case_id: str,
    payload: SegmentMerge,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> SegmentOut:
    require(principal, "segment.write")
    case = case_svc.get_case(db, case_id, principal)
    case_svc.ensure_writable(case)
    seg = svc.merge(
        db,
        case,
        principal,
        seg_uids=payload.seg_uids,
        result_name=payload.result_name,
        operation=payload.operation,
    )
    case_svc.touch(case)
    return ser.segment_out(seg)


@router.post("/cases/{case_id}/segments/grow", response_model=SegmentOut, status_code=201)
@idempotent("POST /api/v1/cases/{case_id}/segments/grow", status_code=201)
def grow_segment(
    request: Request,
    case_id: str,
    payload: SegmentGrow,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> SegmentOut:
    """种子点区域生长，产出带像素的 draft 态 SEG。

    与 /ai/infer 的区别：这里不排队、不等回调，同步产出像素——本地就能验证
    "分割 → 体积 → 规划"整条链路，不依赖任何外部推理服务。
    """
    require(principal, "segment.write")
    case = case_svc.get_case(db, case_id, principal)
    case_svc.ensure_writable(case)
    seg = svc.create_from_seed(
        db,
        case,
        principal,
        study_instance_uid=payload.study_instance_uid,
        seed_point=payload.seed.model_dump(),
        segment_name=payload.segment_name,
        hu_tolerance=payload.hu_tolerance,
        radius_mm=payload.radius_mm,
        series_instance_uid=payload.series_instance_uid,
    )
    case_svc.touch(case)
    return ser.segment_out(seg)


@router.post("/segments/{seg_uid}:crop", response_model=SegmentOut, status_code=201)
@idempotent("POST /api/v1/segments/{seg_uid}:crop", status_code=201)
def crop_segment(
    request: Request,
    seg_uid: str,
    payload: SegmentCrop,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> SegmentOut:
    """裁切分割（保留 LPS 盒内体素）。产出新的 draft 分割，源分割不动。"""
    require(principal, "segment.write")
    seg = svc.get(db, seg_uid)
    case = case_svc.get_case(db, seg.case_id, principal)
    case_svc.ensure_writable(case)
    try:
        min_lps, max_lps = payload.resolve_box()
    except ValueError as exc:
        raise ApiError(BusinessCode.VALIDATION_ERROR, str(exc), {"code": "INVALID_BOX"}) from exc
    created = svc.crop(
        db,
        case,
        principal,
        seg=seg,
        min_lps=min_lps,
        max_lps=max_lps,
        result_name=payload.result_name,
    )
    case_svc.touch(case)
    return ser.segment_out(created)


@router.post("/segments/{seg_uid}:smooth", response_model=SegmentOut, status_code=201)
@idempotent("POST /api/v1/segments/{seg_uid}:smooth", status_code=201)
def smooth_segment(
    request: Request,
    seg_uid: str,
    payload: SegmentSmooth,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> SegmentOut:
    """平滑分割：默认中值滤波（保薄结构），可选形态学开/闭。产出新的 draft 分割。"""
    require(principal, "segment.write")
    seg = svc.get(db, seg_uid)
    case = case_svc.get_case(db, seg.case_id, principal)
    case_svc.ensure_writable(case)
    created = svc.smooth(
        db,
        case,
        principal,
        seg=seg,
        method=payload.method,
        radius_mm=payload.radius_mm,
        closing_mm=payload.closing_mm,
        result_name=payload.result_name,
    )
    case_svc.touch(case)
    return ser.segment_out(created)


@router.get("/segments/{seg_uid}", response_model=SegmentOut)
def get_segment(
    request: Request,
    seg_uid: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> SegmentOut:
    require(principal, "case.read")
    seg = svc.get(db, seg_uid)
    case_svc.get_case(db, seg.case_id, principal)
    return ser.segment_out(seg)


@router.post("/segments/{seg_uid}:confirm", response_model=SegmentOut)
@idempotent("POST /api/v1/segments/{seg_uid}:confirm")
def confirm_segment(
    request: Request,
    seg_uid: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> SegmentOut:
    """AI 输出必须由医师显式确认后才能被 Plan / Report 引用。"""
    require(principal, "segment.confirm")
    seg = svc.get(db, seg_uid)
    case_svc.get_case(db, seg.case_id, principal)
    svc.confirm(db, seg, principal)
    return ser.segment_out(seg)


@router.post("/segments/{seg_uid}:revise", response_model=SegmentOut)
@idempotent("POST /api/v1/segments/{seg_uid}:revise")
def revise_segment(
    request: Request,
    seg_uid: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> SegmentOut:
    require(principal, "segment.write")
    seg = svc.get(db, seg_uid)
    case_svc.get_case(db, seg.case_id, principal)
    svc.revise(db, seg, principal)
    return ser.segment_out(seg)


@router.post("/segments/{seg_uid}:withdraw", response_model=SegmentOut)
@idempotent("POST /api/v1/segments/{seg_uid}:withdraw")
def withdraw_segment(
    request: Request,
    seg_uid: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> SegmentOut:
    require(principal, "segment.write")
    seg = svc.get(db, seg_uid)
    case_svc.get_case(db, seg.case_id, principal)
    svc.withdraw(db, seg, principal)
    return ser.segment_out(seg)


@router.get("/segments/{seg_uid}/versions/{version}")
def get_segment_version(
    request: Request,
    seg_uid: str,
    version: int,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> Response:
    """历史版本按需取回（契约 §1.8.1：老版本不删除）。"""
    require(principal, "case.read")
    row = svc.get_version(db, seg_uid, version)
    if row is None:
        raise not_found("SegmentVersion", f"{seg_uid}@v{version}")
    return Response(
        content=row.seg_content or b"",
        media_type="application/dicom",
        headers={"Content-Disposition": f'attachment; filename="{seg_uid}_v{version}.dcm"'},
    )
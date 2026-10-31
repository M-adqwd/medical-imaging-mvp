"""测量 SR API（契约 §3.2）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.v1 import serializers as ser
from app.api.v1.deps import current_user, db_session, idempotent
from app.api.v1.schemas import MeasurementCreate, MeasurementOut, MeasurementUpdate
from app.core.errors import ApiError, BusinessCode
from app.core.security import Principal, require
from app.services import cases as case_svc
from app.services import measurements as svc

router = APIRouter(tags=["measurement"])


@router.post("/cases/{case_id}/measurements", response_model=MeasurementOut, status_code=201)
@idempotent("POST /api/v1/cases/{case_id}/measurements", status_code=201)
def create_measurement(
    request: Request,
    case_id: str,
    payload: MeasurementCreate,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> MeasurementOut:
    require(principal, "measurement.write")
    case = case_svc.get_case(db, case_id, principal)
    case_svc.ensure_writable(case)
    m = svc.create(
        db,
        case,
        principal,
        study_instance_uid=payload.study_instance_uid,
        type_=payload.type,
        value=payload.value,
        unit=payload.unit,
        text=payload.text,
        coord_ref=payload.coord_ref,
        cross_series=payload.cross_series,
        series_instance_uid=payload.series_instance_uid,
    )
    case_svc.touch(case)
    return ser.measurement_out(m)


@router.get("/cases/{case_id}/measurements", response_model=list[MeasurementOut])
def list_measurements(
    request: Request,
    case_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
    include_withdrawn: bool = Query(default=False),
) -> list[MeasurementOut]:
    require(principal, "case.read")
    case_svc.get_case(db, case_id, principal)
    rows = svc.list_by_case(db, case_id, include_withdrawn=include_withdrawn)
    return [ser.measurement_out(m) for m in rows]


@router.put("/measurements/{sr_uid}", response_model=MeasurementOut)
@idempotent("PUT /api/v1/measurements/{sr_uid}")
def update_measurement(
    request: Request,
    sr_uid: str,
    payload: MeasurementUpdate,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> MeasurementOut:
    require(principal, "measurement.write")
    m = svc.get(db, sr_uid)
    if m.creator_id != principal.user_id and not principal.is_admin:
        raise ApiError(
            BusinessCode.PERMISSION_DENIED, "只有创建者或管理员可修改测量", {"sr_uid": sr_uid}
        )
    svc.update_draft(db, m, principal, payload=payload.model_dump(exclude_unset=True), if_match=if_match)
    return ser.measurement_out(m)


@router.post("/measurements/{sr_uid}:save", response_model=MeasurementOut)
@idempotent("POST /api/v1/measurements/{sr_uid}:save")
def save_measurement(
    request: Request,
    sr_uid: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> MeasurementOut:
    m = svc.get(db, sr_uid)
    if m.creator_id != principal.user_id and not principal.is_admin:
        raise ApiError(
            BusinessCode.PERMISSION_DENIED, "只有创建者或管理员可保存测量", {"sr_uid": sr_uid}
        )
    svc.transition(db, m, principal, "save")
    return ser.measurement_out(m)


@router.post("/measurements/{sr_uid}:withdraw", response_model=MeasurementOut)
@idempotent("POST /api/v1/measurements/{sr_uid}:withdraw")
def withdraw_measurement(
    request: Request,
    sr_uid: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> MeasurementOut:
    m = svc.get(db, sr_uid)
    svc.transition(db, m, principal, "withdraw")
    return ser.measurement_out(m)


@router.get("/measurements/{sr_uid}/content")
def get_sr_content(
    request: Request,
    sr_uid: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> Response:
    """返回 DICOM SR 原文（单一事实源，契约 §0.1）。"""
    require(principal, "case.read")
    m = svc.get(db, sr_uid)
    case_svc.get_case(db, m.case_id, principal)
    if not m.sr_content:
        raise ApiError(BusinessCode.NOT_FOUND, "SR 内容尚未生成", {"sr_uid": sr_uid})
    return Response(
        content=m.sr_content, media_type="application/dicom", headers={"Content-Disposition": f'attachment; filename="{sr_uid}.dcm"'}
    )
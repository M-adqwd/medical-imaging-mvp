"""规划 Plan API（契约 §3.4）。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.v1 import serializers as ser
from app.api.v1.deps import current_user, db_session, idempotent
from app.api.v1.schemas import PlanCreate, PlanOut, PlanUpdate
from app.core.errors import ApiError, BusinessCode, not_found, validation_error
from app.core.ids import new_dicom_uid
from app.core.security import Principal, require
from app.dicom import sr as sr_builder
from app.models.case import Case
from app.models.segment import Segment
from app.services import cases as case_svc
from app.services import plans as svc

router = APIRouter(tags=["plan"])


@router.post("/cases/{case_id}/plans", response_model=PlanOut, status_code=201)
@idempotent("POST /api/v1/cases/{case_id}/plans", status_code=201)
def create_plan(
    request: Request,
    case_id: str,
    payload: PlanCreate,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> PlanOut:
    require(principal, "plan.write")
    case = case_svc.get_case(db, case_id, principal)
    case_svc.ensure_writable(case)
    plan = svc.create(
        db,
        case,
        principal,
        base_seg_uid=payload.base_seg_uid,
        base_seg_version=payload.base_seg_version,
        entry_point=payload.entry_point.model_dump(),
        target_point=payload.target_point.model_dump(),
        notes=payload.notes,
    )
    case_svc.touch(case)
    return ser.plan_out(plan)


@router.get("/cases/{case_id}/plans", response_model=list[PlanOut])
def list_plans(
    request: Request,
    case_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> list[PlanOut]:
    require(principal, "case.read")
    case_svc.get_case(db, case_id, principal)
    return [ser.plan_out(p) for p in svc.list_by_case(db, case_id)]


@router.put("/plans/{plan_id}", response_model=PlanOut)
@idempotent("PUT /api/v1/plans/{plan_id}")
def update_plan(
    request: Request,
    plan_id: str,
    payload: PlanUpdate,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> PlanOut:
    require(principal, "plan.write")
    plan = svc.get(db, plan_id)
    case_svc.get_case(db, plan.case_id, principal)
    if plan.state.value not in {"draft", "saved", "exported"}:
        from app.core.errors import state_conflict

        raise state_conflict(
            f"Plan 当前状态 {plan.state.value} 不允许编辑",
            current_state=plan.state.value,
        )
    if payload.entry_point is not None:
        plan.entry_point = payload.entry_point.model_dump()
    if payload.target_point is not None:
        plan.target_point = payload.target_point.model_dump()
    if payload.notes is not None:
        plan.notes = payload.notes
    svc.recalculate(db, plan)
    return ser.plan_out(plan)


@router.post("/plans/{plan_id}:save", response_model=PlanOut)
@idempotent("POST /api/v1/plans/{plan_id}:save")
def save_plan(
    request: Request,
    plan_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> PlanOut:
    require(principal, "plan.write")
    plan = svc.get(db, plan_id)
    case_svc.get_case(db, plan.case_id, principal)
    svc.recalculate(db, plan)
    svc.transition(db, plan, principal, "save")
    return ser.plan_out(plan)


@router.post("/plans/{plan_id}:export")
@idempotent("POST /api/v1/plans/{plan_id}:export")
def export_plan(
    request: Request,
    plan_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
    format: str = Query(default="dicom_sr"),
) -> Response:
    require(principal, "plan.export")
    if format not in {"dicom_sr", "pdf"}:
        raise validation_error(
            "format 仅支持 dicom_sr 或 pdf", field="format", allowed=["dicom_sr", "pdf"]
        )
    plan = svc.get(db, plan_id)
    case_svc.get_case(db, plan.case_id, principal)
    svc.transition(db, plan, principal, "export")

    # PRD FR-05：导出必须携带 SEG 版本号，避免引用过期分割
    seg = db.get(Segment, plan.base_seg_uid)

    if format == "pdf":
        # PDF 渲染（PRD FR-05 标记为 [TBD]）在本版不实现。
        # 早前这里返回纯文本却声明 application/pdf，客户端拿到的是无法打开的文件。
        raise ApiError(
            BusinessCode.NOT_IMPLEMENTED,
            "PDF 导出尚未实现（PRD FR-05 [TBD]），请使用 format=dicom_sr 获取 DICOM SR",
            {"format": "pdf", "supported": ["dicom_sr"]},
        )

    case = db.get(Case, plan.case_id)
    study_uid = (case.study_uids or [""])[0] if case is not None else ""
    sr_uid = new_dicom_uid()
    ds = sr_builder.build_plan_sr(
        sr_uid=sr_uid,
        study_instance_uid=study_uid,
        series_instance_uid=seg.series_instance_uid if seg else None,
        patient_id=case.patient_ref if case else "",
        patient_name=(case.patient_name or "") if case else "",
        plan_id=plan.plan_id,
        plan_version=plan.version,
        base_seg_uid=plan.base_seg_uid,
        base_seg_version=plan.base_seg_version,
        base_seg_name=seg.segment_name if seg else "",
        entry_point=plan.entry_point,
        target_point=plan.target_point,
        safe_distance=plan.safe_distance or 0.0,
        angle=plan.angle or 0.0,
        risk_level=plan.risk_level.value,
        creator_id=plan.creator_id,
        study_date=case.study_date if case else None,
    )
    body = sr_builder.to_bytes(ds)
    return Response(
        content=body,
        media_type="application/dicom",
        headers={
            "Content-Disposition": f'attachment; filename="{plan.plan_id}_v{plan.version}.dcm"'
        },
    )


@router.get("/plans/{plan_id}", response_model=PlanOut)
def get_plan(
    request: Request,
    plan_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> PlanOut:
    require(principal, "case.read")
    plan = svc.get(db, plan_id)
    case_svc.get_case(db, plan.case_id, principal)
    return ser.plan_out(plan)
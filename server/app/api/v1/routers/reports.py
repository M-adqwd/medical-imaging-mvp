"""报告 Report API（契约 §3.5）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.v1 import serializers as ser
from app.api.v1.deps import current_user, db_session, idempotent
from app.api.v1.schemas import ReportCreate, ReportOut, ReportUpdate
from app.core.errors import ApiError, BusinessCode
from app.core.security import Principal, require
from app.services import cases as case_svc
from app.services import reports as svc

router = APIRouter(tags=["report"])


@router.post("/cases/{case_id}/reports", response_model=ReportOut, status_code=201)
@idempotent("POST /api/v1/cases/{case_id}/reports", status_code=201)
def create_report(
    request: Request,
    case_id: str,
    payload: ReportCreate,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> ReportOut:
    require(principal, "report.write")
    case = case_svc.get_case(db, case_id, principal)
    case_svc.ensure_writable(case)
    report = svc.create(
        db,
        case,
        principal,
        template_id=payload.template_id,
        findings=payload.findings,
        impression=payload.impression,
        refs=[r.model_dump() for r in payload.refs],
    )
    case_svc.touch(case)
    return ser.report_out(report)


@router.get("/cases/{case_id}/reports", response_model=list[ReportOut])
def list_reports(
    request: Request,
    case_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> list[ReportOut]:
    require(principal, "case.read")
    case_svc.get_case(db, case_id, principal)
    return [ser.report_out(r) for r in svc.list_by_case(db, case_id)]


@router.get("/reports/{report_id}", response_model=ReportOut)
def get_report(
    request: Request,
    report_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> ReportOut:
    require(principal, "case.read")
    report = svc.get(db, report_id)
    case_svc.get_case(db, report.case_id, principal)
    return ser.report_out(report)


@router.put("/reports/{report_id}", response_model=ReportOut)
@idempotent("PUT /api/v1/reports/{report_id}")
def update_report(
    request: Request,
    report_id: str,
    payload: ReportUpdate,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> ReportOut:
    require(principal, "report.write")
    report = svc.get(db, report_id)
    case_svc.get_case(db, report.case_id, principal)
    svc.update_draft(
        db,
        report,
        principal,
        payload=payload.model_dump(exclude_unset=True)
        | ({"refs": [r.model_dump() for r in payload.refs]} if payload.refs is not None else {}),
    )
    return ser.report_out(report)


@router.post("/reports/{report_id}:submit", response_model=ReportOut)
@idempotent("POST /api/v1/reports/{report_id}:submit")
def submit_report(
    request: Request,
    report_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> ReportOut:
    """触发全量校验 + 审计 + DICOM SR 生成。校验失败返回 422。"""
    require(principal, "report.submit")
    report = svc.get(db, report_id)
    case = case_svc.get_case(db, report.case_id, principal)
    svc.submit(db, report, principal, case)
    return ser.report_out(report)


@router.post("/reports/{report_id}:lock", response_model=ReportOut)
@idempotent("POST /api/v1/reports/{report_id}:lock")
def lock_report(
    request: Request,
    report_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> ReportOut:
    require(principal, "report.submit")
    report = svc.get(db, report_id)
    case_svc.get_case(db, report.case_id, principal)
    svc.lock(db, report, principal)
    return ser.report_out(report)


@router.post("/reports/{report_id}:withdraw", response_model=ReportOut)
@idempotent("POST /api/v1/reports/{report_id}:withdraw")
def withdraw_report(
    request: Request,
    report_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> ReportOut:
    require(principal, "report.write")
    report = svc.get(db, report_id)
    case_svc.get_case(db, report.case_id, principal)
    svc.withdraw(db, report, principal)
    return ser.report_out(report)


@router.get("/reports/{report_id}/content")
def get_report_content(
    request: Request,
    report_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> Response:
    """取回报告的 DICOM SR 原文（提交后生成）。"""
    require(principal, "case.read")
    report = svc.get(db, report_id)
    case_svc.get_case(db, report.case_id, principal)
    if not report.sr_content:
        raise ApiError(
            BusinessCode.NOT_FOUND,
            "报告 SR 尚未生成（draft 态无 SR）",
            {"report_id": report_id, "state": report.state.value},
        )
    return Response(
        content=report.sr_content,
        media_type="application/dicom",
        headers={"Content-Disposition": f'attachment; filename="{report_id}.dcm"'},
    )
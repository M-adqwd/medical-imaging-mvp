"""Case API（契约 §3.1）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1 import serializers as ser
from app.api.v1.deps import client_ip, commit_or_409, current_user, db_session, idempotent
from app.api.v1.schemas import CaseCreate, CaseDetailOut, CaseListOut, CaseOut
from app.core import audit
from app.core.security import Principal, require
from app.models.case import Case, StudyRef
from app.services import cases as svc

router = APIRouter(prefix="/cases", tags=["case"])


def _refs(db: Session, case: Case) -> dict[str, StudyRef]:
    uids = case.study_uids or []
    if not uids:
        return {}
    rows = db.scalars(select(StudyRef).where(StudyRef.study_instance_uid.in_(uids))).all()
    return {r.study_instance_uid: r for r in rows}


@router.get("", response_model=CaseListOut)
def list_cases(
    request: Request,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
    query: str | None = Query(default=None),
    source: str | None = Query(default=None),
    modality: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=30, ge=1, le=100),
) -> CaseListOut:
    require(principal, "case.read")
    total, rows = svc.list_cases(
        db, principal, query=query, source=source, modality=modality, page=page, size=size
    )
    items = [ser.case_out(c, _refs(db, c)) for c in rows]
    next_page = page + 1 if page * size < total else None
    return CaseListOut(total=total, next_page=next_page, items=items)


@router.post("", response_model=CaseOut, status_code=201)
@idempotent("POST /api/v1/cases", status_code=201)
def create_case(
    request: Request,
    payload: CaseCreate,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> CaseOut:
    require(principal, "case.create")
    case = svc.create_case(
        db,
        principal,
        patient_ref=payload.patient_ref,
        studies=payload.studies,
        source=payload.source,
        modality=payload.modality,
        title=payload.title,
    )
    return ser.case_out(case, _refs(db, case))


@router.get("/{case_id}", response_model=CaseDetailOut)
def get_case(
    request: Request,
    case_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> CaseDetailOut:
    require(principal, "case.read")
    case = svc.get_case(db, case_id, principal)
    audit.record(
        db,
        category="case_access",
        action="open",
        actor_id=principal.user_id,
        actor_ip=client_ip(request),
        target_type="case",
        target_uid=case.id,
        extra={"source": case.source.value},
    )
    commit_or_409(db)

    counts = {
        "measurements": len(case.measurements),
        "segments": len(case.segments),
        "plans": len(case.plans),
        "reports": len(case.reports),
        "shares": len(case.shares),
    }
    activity = [
        {"at": e.created_at, "action": e.action, "actor": e.actor_id, "target": e.target_uid}
        for e in db.scalars(
            select(audit.AuditEvent)
            .where(audit.AuditEvent.target_uid == case.id)
            .order_by(audit.AuditEvent.created_at.desc())
            .limit(20)
        ).all()
    ]
    return ser.case_detail(case, _refs(db, case), counts, activity)


@router.post("/{case_id}:close", response_model=CaseOut)
@idempotent("POST /api/v1/cases/{case_id}:close")
def close_case(
    request: Request,
    case_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> CaseOut:
    require(principal, "case.write")
    case = svc.get_case(db, case_id, principal)
    svc.close_or_reopen(db, case, principal, "close")
    return ser.case_out(case, _refs(db, case))


@router.post("/{case_id}:reopen", response_model=CaseOut)
@idempotent("POST /api/v1/cases/{case_id}:reopen")
def reopen_case(
    request: Request,
    case_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> CaseOut:
    require(principal, "case.write")
    case = svc.get_case(db, case_id, principal)
    svc.close_or_reopen(db, case, principal, "reopen")
    return ser.case_out(case, _refs(db, case))


@router.post("/{case_id}/participants", response_model=CaseOut)
@idempotent("POST /api/v1/cases/{case_id}/participants")
def add_participant(
    request: Request,
    case_id: str,
    user_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> CaseOut:
    require(principal, "case.write")
    case = svc.get_case(db, case_id, principal)
    svc.add_participant(db, case, principal, user_id)
    db.flush()
    db.refresh(case)
    return ser.case_out(case, _refs(db, case))
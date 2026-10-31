"""共享 Share API（契约 §3.6）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.v1 import serializers as ser
from app.api.v1.deps import current_user, db_session, idempotent
from app.api.v1.schemas import CommentCreate, CommentOut, ShareCreate, ShareCreatedOut, ShareOut
from app.core.security import Principal, require
from app.services import cases as case_svc
from app.services import shares as svc

router = APIRouter(tags=["share"])


@router.post("/cases/{case_id}/shares", response_model=ShareCreatedOut, status_code=201)
@idempotent("POST /api/v1/cases/{case_id}/shares", status_code=201)
def create_share(
    request: Request,
    case_id: str,
    payload: ShareCreate,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> ShareCreatedOut:
    require(principal, "share.create")
    case = case_svc.get_case(db, case_id, principal)
    share, code = svc.create(
        db,
        case,
        principal,
        permission=payload.permission,
        scope=payload.scope,
        scope_refs=payload.scope_refs,
        expires_at=payload.expires_at,
        ttl_days=payload.ttl_days,
    )
    base = str(request.base_url)
    return ser.share_created(share, code, base)


@router.get("/cases/{case_id}/shares", response_model=list[ShareOut])
def list_shares(
    request: Request,
    case_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> list[ShareOut]:
    require(principal, "case.read")
    case = case_svc.get_case(db, case_id, principal)
    return [ser.share_out(s) for s in case.shares]


@router.get("/shares/{share_id}", response_model=ShareOut)
def get_share(
    request: Request,
    share_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> ShareOut:
    share = svc.get(db, share_id)
    if share.created_by != principal.user_id and not principal.is_admin:
        case_svc.get_case(db, share.case_id, principal)
    return ser.share_out(share)


@router.get("/shares/{share_id}/access", response_model=dict)
def access_share(
    request: Request,
    share_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
    access_code: str = Query(..., alias="access_code"),
) -> dict:
    """访问共享链接。过期 / 撤销返回 410，访问码错误返回 401。"""
    share = svc.access(db, share_id, access_code, principal)
    case = db.get(case_svc.Case, share.case_id)
    return {
        "share": ser.share_out(share).model_dump(mode="json"),
        "case": ser.case_out(case, {}).model_dump(mode="json") if case else None,
    }


@router.post("/shares/{share_id}:revoke", response_model=ShareOut)
@idempotent("POST /api/v1/shares/{share_id}:revoke")
def revoke_share(
    request: Request,
    share_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> ShareOut:
    require(principal, "share.create")
    share = svc.get(db, share_id)
    svc.revoke(db, share, principal)
    return ser.share_out(share)


@router.get("/cases/{case_id}/comments", response_model=list[CommentOut])
def list_comments(
    request: Request,
    case_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
    share_id: str | None = Query(default=None),
) -> list[CommentOut]:
    require(principal, "case.read")
    case_svc.get_case(db, case_id, principal)
    return [ser.comment_out(c) for c in svc.list_comments(db, case_id, share_id)]


@router.post("/cases/{case_id}/comments", response_model=CommentOut, status_code=201)
@idempotent("POST /api/v1/cases/{case_id}/comments", status_code=201)
def add_comment(
    request: Request,
    case_id: str,
    payload: CommentCreate,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> CommentOut:
    require(principal, "share.comment")
    case = case_svc.get_case(db, case_id, principal)
    c = svc.add_comment(
        db,
        case,
        principal,
        body=payload.body,
        anchor_type=payload.anchor_type,
        anchor_uid=payload.anchor_uid,
        mention=payload.mention,
    )
    return ser.comment_out(c)
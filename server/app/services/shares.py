"""共享服务（契约 §1.7 / §3.6）。

access_code 只在创建响应出现一次，服务端加盐哈希存储（PRD FR-07）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.config import get_settings
from app.core.errors import gone, not_found, validation_error
from app.core.security import Principal
from app.models.ai_job import Comment
from app.models.case import Case
from app.models.enums import SharePermission, ShareScope
from app.models.share import Share


def get(db: Session, share_id: str) -> Share:
    s = db.get(Share, share_id)
    if s is None:
        raise not_found("Share", share_id)
    return s


def create(
    db: Session,
    case: Case,
    principal: Principal,
    *,
    permission: str,
    scope: str,
    scope_refs: list[dict] | None,
    expires_at: str | None,
    ttl_days: int | None = None,
) -> tuple[Share, str]:
    if scope == ShareScope.CUSTOM.value and not (scope_refs or []):
        raise validation_error("scope=custom 时必须给出 scope_refs", field="scope_refs")

    days = ttl_days or get_settings().share_default_ttl_days
    code, salt, code_hash = Share.issue_code()
    share = Share(
        case_id=case.id,
        scope=ShareScope(scope),
        scope_refs=scope_refs or [],
        permission=SharePermission(permission),
        code_salt=salt,
        code_hash=code_hash,
        created_by=principal.user_id,
        expires_at=(
            datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires_at
            else Share.default_expiry(days)
        ),
        revoked=False,
    )
    db.add(share)
    db.flush()
    audit.record(
        db,
        category="share",
        action="create",
        actor_id=principal.user_id,
        target_type="share",
        target_uid=share.share_id,
        extra={"permission": permission, "scope": scope, "expires_at": share.expires_at.isoformat()},
    )
    return share, code


def access(db: Session, share_id: str, code: str, principal: Principal) -> Share:
    """验证访问码并返回 Share。已撤销 / 已过期一律 410。"""
    share = get(db, share_id)
    if share.revoked:
        raise gone("共享链接已被创建者撤销", share_id=share_id)
    if share.is_expired:
        raise gone("共享链接已过期，请联系分享人重新生成", share_id=share_id)
    if not code or not share.verify(code):
        from app.core.errors import ApiError, BusinessCode

        raise ApiError(
            BusinessCode.UNAUTHENTICATED, "访问码错误或缺失", {"share_id": share_id}
        )
    share.view_count += 1
    db.flush()
    audit.record(
        db,
        category="share",
        action="access",
        actor_id=principal.user_id,
        target_type="share",
        target_uid=share_id,
        extra={"case_id": share.case_id},
    )
    return share


def revoke(db: Session, share: Share, principal: Principal) -> Share:
    if share.created_by != principal.user_id and not principal.is_admin:
        from app.core.errors import permission_denied

        raise permission_denied("只有创建者或管理员可撤销共享链接", share_id=share.share_id)
    share.revoked = True
    db.flush()
    audit.record(
        db,
        category="share",
        action="revoke",
        actor_id=principal.user_id,
        target_type="share",
        target_uid=share.share_id,
        after_state="revoked",
    )
    return share


def resolve_scope(db: Session, share: Share) -> list[str]:
    if share.scope == ShareScope.FULL:
        return []
    return [r.get("uid") for r in (share.scope_refs or []) if r.get("uid")]


def list_comments(db: Session, case_id: str, share_id: str | None = None) -> list[Comment]:
    stmt = select(Comment).where(Comment.case_id == case_id)
    if share_id:
        stmt = stmt.where(Comment.share_id == share_id)
    return list(db.scalars(stmt.order_by(Comment.created_at.desc())).all())


def add_comment(
    db: Session,
    case: Case,
    principal: Principal,
    *,
    body: str,
    share_id: str | None = None,
    anchor_type: str | None = None,
    anchor_uid: str | None = None,
    mention: str | None = None,
) -> Comment:
    if not (body or "").strip():
        raise validation_error("评论内容不能为空", field="body")
    comment = Comment(
        case_id=case.id,
        share_id=share_id,
        body=body,
        anchor_type=anchor_type,
        anchor_uid=anchor_uid,
        mention=mention,
        author_id=principal.user_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(comment)
    db.flush()
    audit.record(
        db,
        category="share",
        action="comment",
        actor_id=principal.user_id,
        target_type="comment",
        target_uid=comment.comment_id,
        extra={"case_id": case.id, "anchor_uid": anchor_uid},
    )
    return comment
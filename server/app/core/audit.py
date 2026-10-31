"""PRD §6.3：审计与留痕。

事件类别与保留周期见 PRD 6.3 表；本模块只负责写入，
保留周期由 infra 的定时清理作业执行。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditEvent

# 事件类别 → 保留天数
RETENTION_DAYS: dict[str, int] = {
    "auth": 365,
    "case_access": 180,
    "object": 1095,
    "ai": 1095,
    "share": 1095,
    "export": 1095,
}


def record(
    db: Session,
    *,
    category: str,
    action: str,
    actor_id: str | None,
    actor_ip: str | None = None,
    target_type: str | None = None,
    target_uid: str | None = None,
    before_state: str | None = None,
    after_state: str | None = None,
    extra: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        category=category,
        action=action,
        actor_id=actor_id,
        actor_ip=actor_ip,
        target_type=target_type,
        target_uid=target_uid,
        before_state=before_state,
        after_state=after_state,
        extra=extra or {},
        created_at=datetime.now(timezone.utc),
    )
    db.add(event)
    return event
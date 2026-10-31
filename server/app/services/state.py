"""状态机执行器：所有状态变更必须经过这里（契约 §0.4 状态转移封闭）。"""

from __future__ import annotations

from typing import Any, TypeVar

from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import ApiError, BusinessCode, state_conflict
from app.core.security import Principal
from app.models.state_machines import allowed_actions, next_state

T = TypeVar("T")


def apply(
    db: Session,
    *,
    obj: Any,
    transitions: dict,
    action: str,
    principal: Principal,
    actor_ip: str | None = None,
    target_type: str,
    target_uid: str,
    category: str = "object",
    extra: dict | None = None,
    state_field: str = "state",
) -> str:
    """执行一次状态转移。返回新状态。

    未在转移表中出现的 (当前态, 动作) 组合一律 409。
    """
    attr = getattr(obj, state_field)
    current = attr.value if hasattr(attr, "value") else str(attr)
    new = next_state(transitions, current, action)
    if new is None:
        raise state_conflict(
            f"{target_type} 当前状态 {current} 不允许执行 {action}",
            current_state=current,
            action=action,
            allowed_actions=allowed_actions(transitions, current),
        )

    setattr(obj, state_field, _coerce(attr, new))
    audit.record(
        db,
        category=category,
        action=action,
        actor_id=principal.user_id,
        actor_ip=actor_ip,
        target_type=target_type,
        target_uid=target_uid,
        before_state=current,
        after_state=new,
        extra=extra or {},
    )
    return new


def _coerce(current_enum: Any, new_value: str) -> Any:
    if hasattr(current_enum, "value"):
        return type(current_enum)(new_value)
    return new_value


def ensure(condition: bool, message: str, **details: Any) -> None:
    if not condition:
        raise state_conflict(message, **details)


def require_facts_source_conflict(message: str, **details: Any) -> ApiError:
    return ApiError(BusinessCode.STATE_CONFLICT, message, details)
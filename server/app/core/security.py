"""PRD §6.2 权限模型。

角色：attending（主读医师）/ planner（规划医师）/ reviewer（审阅者）/
resident（规培）/ admin（管理员）。P0 鉴权以网关注入的用户头为准，
本模块只做角色判定与能力检查，替换 IdP 时不影响业务代码。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from fastapi import Header

from app.core.errors import ApiError, BusinessCode


class Role(str, Enum):
    ATTENDING = "attending"
    PLANNER = "planner"
    REVIEWER = "reviewer"
    RESIDENT = "resident"
    ADMIN = "admin"


@dataclass
class Principal:
    user_id: str
    roles: set[Role] = field(default_factory=set)

    def has(self, *roles: Role) -> bool:
        return bool(self.roles & set(roles))

    @property
    def is_admin(self) -> bool:
        return Role.ADMIN in self.roles


# 能力矩阵：PRD FR-01..FR-07 Permissions 表
CAPABILITIES: dict[str, set[Role]] = {
    "case.read": {Role.ATTENDING, Role.PLANNER, Role.REVIEWER, Role.RESIDENT, Role.ADMIN},
    "case.create": {Role.ATTENDING, Role.PLANNER, Role.RESIDENT, Role.ADMIN},
    "case.write": {Role.ATTENDING, Role.PLANNER, Role.ADMIN},
    # 光盘 / 移动存储介质导入
    "media.import": {Role.ATTENDING, Role.PLANNER, Role.RESIDENT, Role.ADMIN},
    "measurement.write": {Role.ATTENDING, Role.PLANNER, Role.RESIDENT, Role.ADMIN},
    "measurement.delete": {Role.ATTENDING, Role.PLANNER, Role.ADMIN},
    "segment.ai_trigger": {Role.ATTENDING, Role.PLANNER, Role.RESIDENT, Role.ADMIN},
    "segment.confirm": {Role.ATTENDING, Role.PLANNER, Role.ADMIN},
    "segment.write": {Role.ATTENDING, Role.PLANNER, Role.ADMIN},
    "plan.write": {Role.ATTENDING, Role.PLANNER, Role.RESIDENT, Role.ADMIN},
    "plan.export": {Role.ATTENDING, Role.PLANNER, Role.ADMIN},
    "report.write": {Role.ATTENDING, Role.PLANNER, Role.RESIDENT, Role.ADMIN},
    "report.submit": {Role.ATTENDING, Role.PLANNER, Role.ADMIN},
    "share.create": {Role.ATTENDING, Role.PLANNER, Role.RESIDENT, Role.ADMIN},
    "share.comment": {Role.ATTENDING, Role.PLANNER, Role.REVIEWER, Role.RESIDENT, Role.ADMIN},
    "admin.resolve_ai_acl": {Role.ADMIN},
}


def get_principal(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_user_roles: str | None = Header(default=None, alias="X-User-Roles"),
) -> Principal:
    """网关鉴权后透传用户身份头。缺头视为未登录。"""
    if not x_user_id:
        raise ApiError(BusinessCode.UNAUTHENTICATED, "缺少用户身份", {"header": "X-User-Id"})
    roles: set[Role] = set()
    for raw in (x_user_roles or "").replace(" ", "").split(","):
        if not raw:
            continue
        try:
            roles.add(Role(raw.lower()))
        except ValueError:
            continue
    if not roles:
        roles.add(Role.REVIEWER)
    return Principal(user_id=x_user_id, roles=roles)


def require(principal: Principal, capability: str) -> None:
    allowed = CAPABILITIES.get(capability)
    if allowed is None:
        raise ApiError(BusinessCode.INTERNAL_ERROR, f"未定义的能力：{capability}")
    if principal.is_admin or principal.roles & allowed:
        return
    raise ApiError(
        BusinessCode.PERMISSION_DENIED,
        f"当前角色无权执行 {capability}",
        {"capability": capability, "roles": sorted(r.value for r in principal.roles)},
    )


def can(principal: Principal, capability: str) -> bool:
    allowed = CAPABILITIES.get(capability, set())
    return principal.is_admin or bool(principal.roles & allowed)
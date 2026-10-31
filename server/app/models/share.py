from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.ids import new_access_code, new_uuid
from app.models.enums import SharePermission, ShareScope


def hash_access_code(code: str, salt: str) -> str:
    normalized = "".join(code.split()).upper()
    return hashlib.sha256(f"{salt}:{normalized}".encode("utf-8")).hexdigest()


class Share(Base):
    """契约 §1.7：共享链接。access_code 加盐哈希存储，只在创建响应出现一次。"""

    __tablename__ = "shares"

    share_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[ShareScope] = mapped_column(
        Enum(ShareScope, values_callable=lambda e: [x.value for x in e]),
        default=ShareScope.FULL,
        nullable=False,
    )
    scope_refs: Mapped[list] = mapped_column(JSON, default=list)
    permission: Mapped[SharePermission] = mapped_column(
        Enum(SharePermission, values_callable=lambda e: [x.value for x in e]),
        default=SharePermission.READONLY,
        nullable=False,
    )
    code_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    view_count: Mapped[int] = mapped_column(default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    case: Mapped["Case"] = relationship(back_populates="shares")  # noqa: F821

    @staticmethod
    def issue_code() -> tuple[str, str, str]:
        """返回 (明文码, salt, hash)。明文码只在此刻存在。"""
        code = new_access_code()
        salt = new_uuid().replace("-", "")
        return code, salt, hash_access_code(code, salt)

    def verify(self, code: str) -> bool:
        return hash_access_code(code, self.code_salt) == self.code_hash

    @property
    def is_expired(self) -> bool:
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires <= datetime.now(timezone.utc)

    @staticmethod
    def default_expiry(days: int) -> datetime:
        return datetime.now(timezone.utc) + timedelta(days=days)
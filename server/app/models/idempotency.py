from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class IdempotencyRecord(Base):
    """契约 §5.4 幂等记录。同一 (scope, key) 只保留一条，重入回放首次响应。"""

    __tablename__ = "idempotency_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(200), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 二进制响应（DICOM SR / PDF）快照。response_body 是 JSON 列，
    # 存不下原始字节，故用 base64 存这里，重放时还原为同样的字节与 media_type。
    response_binary_b64: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_media_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    response_filename: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    __table_args__ = (UniqueConstraint("scope", "key", name="uq_idem_scope_key"),)
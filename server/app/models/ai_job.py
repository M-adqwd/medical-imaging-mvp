from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.enums import AiJobStatus


class AiJob(Base):
    """契约 §2.4：AI 推理任务。job_id 由客户端生成，用于重试幂等。"""

    __tablename__ = "ai_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    study_instance_uid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    targets: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[AiJobStatus] = mapped_column(
        Enum(AiJobStatus, values_callable=lambda e: [x.value for x in e]),
        default=AiJobStatus.QUEUED,
        nullable=False,
    )
    queue_pos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_seg_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class Comment(Base):
    """PRD FR-07：评论以时间线堆叠，可附着在 SR / SEG / Plan 上。"""

    __tablename__ = "comments"

    comment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    share_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    body: Mapped[str] = mapped_column(String, nullable=False)
    anchor_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    anchor_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mention: Mapped[str | None] = mapped_column(String(64), nullable=True)
    closed: Mapped[bool] = mapped_column(default=False, nullable=False)
    author_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
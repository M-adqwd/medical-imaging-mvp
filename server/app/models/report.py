from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import ReportState


class Report(Base):
    """契约 §1.6：结构化报告。refs 每项必须带 version。"""

    __tablename__ = "reports"

    report_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    findings: Mapped[str] = mapped_column(Text, default="", nullable=False)
    impression: Mapped[str] = mapped_column(Text, default="", nullable=False)
    refs: Mapped[list] = mapped_column(JSON, default=list)

    state: Mapped[ReportState] = mapped_column(
        Enum(ReportState, values_callable=lambda e: [x.value for x in e]),
        default=ReportState.DRAFT,
        nullable=False,
    )
    author_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    sr_content: Mapped[bytes | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    case: Mapped["Case"] = relationship(back_populates="reports")  # noqa: F821


class ReportVersion(Base):
    __tablename__ = "report_versions"

    report_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("reports.report_id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(primary_key=True)
    state: Mapped[ReportState] = mapped_column(
        Enum(ReportState, values_callable=lambda e: [x.value for x in e]), nullable=False
    )
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
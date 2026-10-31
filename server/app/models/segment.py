from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import SegmentAlgorithm, SegmentState


class Segment(Base):
    """契约 §1.4：DICOM SEG。每次保存生成新版本，老版本不删除。"""

    __tablename__ = "segments"

    seg_uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    study_instance_uid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    series_instance_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)

    segment_name: Mapped[str] = mapped_column(String(60), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(128), default=SegmentAlgorithm.MANUAL.value, nullable=False)
    base_seg_uids: Mapped[list] = mapped_column(JSON, default=list)

    state: Mapped[SegmentState] = mapped_column(
        Enum(SegmentState, values_callable=lambda e: [x.value for x in e]),
        default=SegmentState.DRAFT,
        nullable=False,
    )
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    creator_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    seg_content: Mapped[bytes | None] = mapped_column(nullable=True)
    seg_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    case: Mapped["Case"] = relationship(back_populates="segments")  # noqa: F821


class SegmentVersion(Base):
    """历史版本指针：latest 始终指向最大 version，老版本行保留（契约 §1.8.1）。"""

    __tablename__ = "segment_versions"

    seg_uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[SegmentState] = mapped_column(
        Enum(SegmentState, values_callable=lambda e: [x.value for x in e]), nullable=False
    )
    seg_content: Mapped[bytes | None] = mapped_column(nullable=True)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import CaseSource, CaseState


class Case(Base):
    """契约 §1.1：聚合根。不内嵌任何 SR / SEG / Plan 数据。"""

    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    patient_ref: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    patient_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[CaseState] = mapped_column(
        Enum(CaseState, values_callable=lambda e: [x.value for x in e]),
        default=CaseState.OPEN,
        nullable=False,
    )
    source: Mapped[CaseSource] = mapped_column(
        Enum(CaseSource, values_callable=lambda e: [x.value for x in e]),
        default=CaseSource.PACS,
        nullable=False,
    )
    modality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    study_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    study_uids: Mapped[list[str]] = mapped_column(JSON, default=list)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    participants: Mapped[list["CaseParticipant"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    measurements: Mapped[list["Measurement"]] = relationship(  # noqa: F821
        back_populates="case", cascade="all, delete-orphan"
    )
    segments: Mapped[list["Segment"]] = relationship(  # noqa: F821
        back_populates="case", cascade="all, delete-orphan"
    )
    plans: Mapped[list["Plan"]] = relationship(  # noqa: F821
        back_populates="case", cascade="all, delete-orphan"
    )
    reports: Mapped[list["Report"]] = relationship(  # noqa: F821
        back_populates="case", cascade="all, delete-orphan"
    )
    shares: Mapped[list["Share"]] = relationship(  # noqa: F821
        back_populates="case", cascade="all, delete-orphan"
    )

    @property
    def is_writable(self) -> bool:
        return self.status == CaseState.OPEN


class CaseParticipant(Base):
    """被邀请参与的同事。独立关联表以支持 SQL 层权限过滤。"""

    __tablename__ = "case_participants"
    __table_args__ = (UniqueConstraint("case_id", "user_id", name="uq_case_participant"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    case: Mapped[Case] = relationship(back_populates="participants")


class StudyRef(Base):
    """契约 §1.2：不另建 DICOM 副本，只缓存元数据视图。"""

    __tablename__ = "study_refs"

    study_instance_uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    patient_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    patient_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    study_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    modalities: Mapped[list[str]] = mapped_column(JSON, default=list)
    series_count: Mapped[int] = mapped_column(default=0)
    institution_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
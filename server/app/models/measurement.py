from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import MeasurementState, MeasurementType


class Measurement(Base):
    """契约 §1.3：DICOM SR 承载的测量 / 标注。不做版本化。"""

    __tablename__ = "measurements"

    sr_uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    study_instance_uid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    series_instance_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)

    type: Mapped[MeasurementType] = mapped_column(
        Enum(MeasurementType, values_callable=lambda e: [x.value for x in e]), nullable=False
    )
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    coord_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    cross_series: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    state: Mapped[MeasurementState] = mapped_column(
        Enum(MeasurementState, values_callable=lambda e: [x.value for x in e]),
        default=MeasurementState.DRAFT,
        nullable=False,
    )
    creator_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sr_content: Mapped[bytes] = mapped_column(nullable=True)  # 契约 §1.3 sr_content
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    case: Mapped["Case"] = relationship(back_populates="measurements")  # noqa: F821


class MeasurementUpdate(Base):
    """SR 系统内部版本号，用于 If-Match 乐观锁（契约 §3.2）。"""

    __tablename__ = "measurement_versions"

    sr_uid: Mapped[str] = mapped_column(
        String(128), ForeignKey("measurements.sr_uid", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(default=1, nullable=False)
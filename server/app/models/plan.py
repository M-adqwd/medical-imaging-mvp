from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import PlanState, RiskLevel


class Plan(Base):
    """契约 §1.5：手术规划。引用 SEG 时必须同时带 UID + version。"""

    __tablename__ = "plans"

    plan_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    base_seg_uid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    base_seg_version: Mapped[int] = mapped_column(Integer, nullable=False)

    entry_point: Mapped[dict] = mapped_column(JSON, default=dict)
    target_point: Mapped[dict] = mapped_column(JSON, default=dict)
    safe_distance: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 入路轴线到分割外表面的最小距离。与 safe_distance（入点到靶点的直线长度）
    # 是两个不同的量，必须分开存：混用会让"长度"冒充"安全距离"（Q-SAFE-DISTANCE）。
    anatomical_distance_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    angle: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[RiskLevel] = mapped_column(
        Enum(RiskLevel, values_callable=lambda e: [x.value for x in e]),
        # 默认不再是 LOW：任何未经计算的规划都不应自带“低风险”标签
        default=RiskLevel.UNKNOWN,
        nullable=False,
    )
    # 风险分级依据。SEG 无像素时记录原因，避免界面只显示一个孤立的等级值
    risk_basis: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    state: Mapped[PlanState] = mapped_column(
        Enum(PlanState, values_callable=lambda e: [x.value for x in e]),
        default=PlanState.DRAFT,
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    creator_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    case: Mapped["Case"] = relationship(back_populates="plans")  # noqa: F821


class PlanVersion(Base):
    __tablename__ = "plan_versions"

    plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("plans.plan_id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[PlanState] = mapped_column(
        Enum(PlanState, values_callable=lambda e: [x.value for x in e]), nullable=False
    )
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
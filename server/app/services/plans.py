"""手术规划服务（契约 §1.5 / §3.4）。

Plan 引用 SEG 时必须同时引用 UID + version；被引用 SEG 失效时 Plan 进入依赖失效态。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import not_found, state_conflict, validation_error
from app.core.ids import new_uuid
from app.dicom import mask as mask_builder
from app.dicom import seg as seg_builder
from app.core.security import Principal
from app.models.case import Case
from app.models.enums import PlanState, RiskLevel, SegmentState
from app.models.plan import Plan, PlanVersion
from app.models.segment import Segment
from app.models.state_machines import PLAN_BASE_SEG_ALLOWED, PLAN_TRANSITIONS
from app.services import state as state_svc

# 安全距离阈值（mm），用于计算 risk_level。取值为工程初始假设 [Assumption, TBD]。
SAFE_DISTANCE_LOW_MM = 10.0
SAFE_DISTANCE_MEDIUM_MM = 5.0


def _vec(p: dict) -> tuple[float, float, float]:
    """读自声明坐标系的点位：{geometry, coordinates:[x, y, z]}（坐标契约 §5）。

    不再接受裸 {x, y, z} —— 没有 geometry 声明时，体素索引会被当成 LPS 毫米
    参与 safe_distance 计算，结果差一个 spacing 因子而界面毫无异常。
    """
    coords = p.get("coordinates") if isinstance(p, dict) else None
    if not isinstance(coords, (list, tuple)) or len(coords) != 3:
        raise validation_error(
            "点位必须是 {geometry, coordinates:[x, y, z]} 结构",
            field="point",
            expected={"geometry": "patient_lps_mm", "coordinates": "长度为 3 的数组"},
        )
    try:
        return float(coords[0]), float(coords[1]), float(coords[2])
    except (TypeError, ValueError) as exc:
        raise validation_error("coordinates 必须是数值", field="point") from exc


def distance(a: dict, b: dict) -> float:
    ax, ay, az = _vec(a)
    bx, by, bz = _vec(b)
    return math.dist((ax, ay, az), (bx, by, bz))


def angle_between(a: dict, b: dict) -> float:
    """两向量夹角（度），PRD FR-05：入路轴线与关键平面夹角。"""
    ax, ay, az = _vec(a)
    bx, by, bz = _vec(b)
    if math.dist((ax, ay, az), (bx, by, bz)) == 0:
        return 0.0
    la = math.sqrt(ax * ax + ay * ay + az * az)
    lb = math.sqrt(bx * bx + by * by + bz * bz)
    if la == 0 or lb == 0:
        return 0.0
    cos = (ax * bx + ay * by + az * bz) / (la * lb)
    return round(math.degrees(math.acos(max(-1.0, min(1.0, cos)))), 2)


def risk_from_distance(anatomical_distance_mm: float | None) -> RiskLevel:
    """按**入路轴线到分割外表面的最小距离**分级（Q-SAFE-DISTANCE 的收敛口径）。

    入参不再是入点到靶点的直线长度。这两者的区别就是本条未决项的全部内容：
    一条 180mm 的穿刺路径可以紧贴大血管，长度的数值感会把它判成"低风险"。
    输入缺失（无像素 / 无空间定位 / 靶点即在分割内）一律 UNKNOWN ——
    返回 MEDIUM 之类的具体等级，等于把"没数据"说成"评估过了，中等风险"
    （Q-RISK-FALLBACK）。
    """
    if anatomical_distance_mm is None:
        return RiskLevel.UNKNOWN
    if anatomical_distance_mm >= SAFE_DISTANCE_LOW_MM:
        return RiskLevel.LOW
    if anatomical_distance_mm >= SAFE_DISTANCE_MEDIUM_MM:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


def seg_has_pixels(seg: Segment | None) -> bool:
    """SEG 是否携带真实像素数据（作为解剖学计算的依据）。"""
    if seg is None:
        return False
    return seg_builder.has_pixel_data(seg.seg_content)


def anatomical_distance(
    seg: Segment | None, entry: dict, target: dict
) -> tuple[float | None, str]:
    """算"入路到分割表面"的距离，返回 (mm, 依据)。

    三种情况都不能分级，但原因必须写清楚，否则界面只剩一个孤立的等级值：
    无像素 / 无空间定位 / 靶点本身位于分割内。
    """
    if seg is None or not seg_has_pixels(seg):
        return None, "底层 SEG 无像素数据，无法计算解剖安全距离，风险等级未知"

    inside = mask_builder.point_inside_mask(seg.seg_content, _vec(target))
    if inside is None:
        return None, "分割缺少空间定位信息，无法判定靶点与分割的位置关系"
    if inside:
        return (
            None,
            "靶点位于分割结构内部（该分割即靶区），"
            "「到分割表面的距离」不构成风险判据，风险等级未知",
        )

    distance_mm, basis = mask_builder.min_distance_to_segment(
        seg.seg_content, _vec(entry), _vec(target)
    )
    if distance_mm is None:
        return None, f"{basis}，风险等级未知"
    return distance_mm, f"{basis}（阈值 {SAFE_DISTANCE_LOW_MM}/{SAFE_DISTANCE_MEDIUM_MM} mm）"


def get(db: Session, plan_id: str) -> Plan:
    p = db.get(Plan, plan_id)
    if p is None:
        raise not_found("Plan", plan_id)
    return p


def list_by_case(db: Session, case_id: str) -> list[Plan]:
    return list(
        db.scalars(select(Plan).where(Plan.case_id == case_id).order_by(Plan.created_at.desc())).all()
    )


def resolve_base_segment(db: Session, base_seg_uid: str, base_seg_version: int) -> Segment:
    seg = db.get(Segment, base_seg_uid)
    if seg is None:
        raise not_found("Segment", base_seg_uid)
    if seg.version != base_seg_version:
        raise validation_error(
            "base_seg_version 与当前版本不一致，请引用明确版本",
            field="base_seg_version",
            current_version=seg.version,
            requested=base_seg_version,
        )
    if seg.state.value not in PLAN_BASE_SEG_ALLOWED:
        raise state_conflict(
            f"只有 confirmed 态 SEG 可作为规划基础，当前为 {seg.state.value}",
            current_state=seg.state.value,
            allowed_states=sorted(PLAN_BASE_SEG_ALLOWED),
        )
    return seg


def create(
    db: Session,
    case: Case,
    principal: Principal,
    *,
    base_seg_uid: str,
    base_seg_version: int,
    entry_point: dict,
    target_point: dict,
    notes: str | None = None,
) -> Plan:
    seg = resolve_base_segment(db, base_seg_uid, base_seg_version)
    if seg.case_id != case.id:
        raise validation_error("base_seg_uid 必须属于当前 Case", field="base_seg_uid")

    safe_distance = distance(entry_point, target_point)
    angle = angle_between(entry_point, target_point)
    distance_mm, basis = anatomical_distance(seg, entry_point, target_point)
    plan = Plan(
        plan_id=new_uuid(),
        case_id=case.id,
        base_seg_uid=base_seg_uid,
        base_seg_version=base_seg_version,
        entry_point=entry_point,
        target_point=target_point,
        safe_distance=round(safe_distance, 2),
        angle=angle,
        anatomical_distance_mm=None if distance_mm is None else round(distance_mm, 2),
        risk_level=risk_from_distance(distance_mm),
        risk_basis=basis,
        notes=notes,
        state=PlanState.DRAFT,
        version=1,
        creator_id=principal.user_id,
    )
    db.add(plan)
    db.flush()
    _snapshot(db, plan)
    audit.record(
        db,
        category="object",
        action="create",
        actor_id=principal.user_id,
        target_type="plan",
        target_uid=plan.plan_id,
        after_state=PlanState.DRAFT.value,
        extra={"base_seg_uid": base_seg_uid, "base_seg_version": base_seg_version},
    )
    return plan


def recalculate(db: Session, plan: Plan) -> Plan:
    """PRD FR-05：调整入路后实时刷新计算值，不允许静默。"""
    seg = db.get(Segment, plan.base_seg_uid)
    if seg is None or seg.state != SegmentState.CONFIRMED:
        plan.risk_level = RiskLevel.HIGH
        plan.risk_basis = "引用的 SEG 已失效，风险等级按最高保守值处理"
    else:
        plan.safe_distance = round(distance(plan.entry_point, plan.target_point), 2)
        plan.angle = angle_between(plan.entry_point, plan.target_point)
        distance_mm, basis = anatomical_distance(seg, plan.entry_point, plan.target_point)
        plan.anatomical_distance_mm = None if distance_mm is None else round(distance_mm, 2)
        plan.risk_level = risk_from_distance(distance_mm)
        plan.risk_basis = basis
    plan.updated_at = datetime.now(timezone.utc)
    db.flush()
    return plan


def transition(db: Session, plan: Plan, principal: Principal, action: str) -> Plan:
    if action == "export":
        if plan.state != PlanState.SAVED:
            raise state_conflict(
                f"只有 saved 态 Plan 可导出，当前为 {plan.state.value}",
                current_state=plan.state.value,
                allowed_states=[PlanState.SAVED.value],
            )
        seg = db.get(Segment, plan.base_seg_uid)
        if seg is None or seg.state.value not in PLAN_BASE_SEG_ALLOWED:
            raise state_conflict(
                "引用的 SEG 已失效，导出会产生过期规划", base_seg_uid=plan.base_seg_uid
            )
    if action == "revise":
        plan.version += 1
    state_svc.apply(
        db,
        obj=plan,
        transitions=PLAN_TRANSITIONS,
        action=action,
        principal=principal,
        target_type="plan",
        target_uid=plan.plan_id,
    )
    if action == "export":
        plan.exported_at = datetime.now(timezone.utc)
    plan.updated_at = datetime.now(timezone.utc)
    db.flush()
    _snapshot(db, plan)
    return plan


def mark_stale(db: Session, plan: Plan, reason: str) -> Plan:
    """级联失效（契约 §1.8.3）。Plan 不删除，仅标记依赖失效。"""
    plan.risk_level = RiskLevel.HIGH
    plan.risk_basis = f"依赖失效：{reason}"
    plan.notes = (plan.notes or "")
    if reason not in plan.notes:
        plan.notes = (plan.notes + f"\n[依赖失效] {reason}").strip()
    plan.updated_at = datetime.now(timezone.utc)
    db.flush()
    return plan


def _snapshot(db: Session, plan: Plan) -> None:
    payload = {
        "base_seg_uid": plan.base_seg_uid,
        "base_seg_version": plan.base_seg_version,
        "entry_point": plan.entry_point,
        "target_point": plan.target_point,
        "safe_distance": plan.safe_distance,
        "anatomical_distance_mm": plan.anatomical_distance_mm,
        "angle": plan.angle,
        "risk_level": plan.risk_level.value if plan.risk_level else None,
        "risk_basis": plan.risk_basis,
        "notes": plan.notes,
    }
    row = db.get(PlanVersion, {"plan_id": plan.plan_id, "version": plan.version})
    if row is None:
        db.add(PlanVersion(plan_id=plan.plan_id, version=plan.version, state=plan.state, snapshot=payload))
    else:
        row.state = plan.state
        row.snapshot = payload
    db.flush()
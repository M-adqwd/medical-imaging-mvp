"""契约 §1：状态机。状态转移必须封闭，只能通过显式动作端点触发。

每个状态机以 (当前态, 动作) → 目标态 的闭集定义，
任何未列出的组合一律拒绝，不做隐式推断。
"""

from __future__ import annotations

from app.models.enums import (
    CaseState,
    MeasurementState,
    PlanState,
    ReportState,
    SegmentState,
)

# 动作名即 API 端点后缀（契约 §3）：:save / :confirm / :submit / :withdraw / ...
CASE_TRANSITIONS: dict[tuple[CaseState, str], CaseState] = {
    (CaseState.OPEN, "close"): CaseState.CLOSED,
    (CaseState.OPEN, "archive"): CaseState.ARCHIVED,
    (CaseState.CLOSED, "reopen"): CaseState.OPEN,
    (CaseState.CLOSED, "archive"): CaseState.ARCHIVED,
    (CaseState.ARCHIVED, "reopen"): CaseState.OPEN,
}

MEASUREMENT_TRANSITIONS: dict[tuple[MeasurementState, str], MeasurementState] = {
    (MeasurementState.DRAFT, "save"): MeasurementState.SAVED,
    (MeasurementState.DRAFT, "withdraw"): MeasurementState.WITHDRAWN,
    (MeasurementState.SAVED, "withdraw"): MeasurementState.WITHDRAWN,
}

SEGMENT_TRANSITIONS: dict[tuple[SegmentState, str], SegmentState] = {
    (SegmentState.DRAFT, "save"): SegmentState.SUGGESTED,
    (SegmentState.DRAFT, "confirm"): SegmentState.CONFIRMED,
    (SegmentState.DRAFT, "withdraw"): SegmentState.WITHDRAWN,
    (SegmentState.SUGGESTED, "confirm"): SegmentState.CONFIRMED,
    (SegmentState.SUGGESTED, "withdraw"): SegmentState.WITHDRAWN,
    (SegmentState.CONFIRMED, "revise"): SegmentState.DRAFT,
    (SegmentState.CONFIRMED, "withdraw"): SegmentState.WITHDRAWN,
}

PLAN_TRANSITIONS: dict[tuple[PlanState, str], PlanState] = {
    (PlanState.DRAFT, "save"): PlanState.SAVED,
    (PlanState.DRAFT, "withdraw"): PlanState.WITHDRAWN,
    (PlanState.SAVED, "export"): PlanState.EXPORTED,
    (PlanState.SAVED, "revise"): PlanState.DRAFT,
    (PlanState.SAVED, "withdraw"): PlanState.WITHDRAWN,
    (PlanState.EXPORTED, "revise"): PlanState.DRAFT,
    (PlanState.EXPORTED, "withdraw"): PlanState.WITHDRAWN,
}

REPORT_TRANSITIONS: dict[tuple[ReportState, str], ReportState] = {
    (ReportState.DRAFT, "submit"): ReportState.SUBMITTED,
    (ReportState.DRAFT, "withdraw"): ReportState.WITHDRAWN,
    (ReportState.SUBMITTED, "withdraw"): ReportState.WITHDRAWN,
    (ReportState.SUBMITTED, "lock"): ReportState.LOCKED,
    (ReportState.WITHDRAWN, "reopen"): ReportState.DRAFT,
    (ReportState.STALE_REF, "reopen"): ReportState.DRAFT,
}


def next_state(transitions: dict, current: str, action: str) -> str | None:
    for (src, act), dst in transitions.items():
        if src.value == current and act == action:
            return dst.value
    return None


def allowed_actions(transitions: dict, current: str) -> list[str]:
    return [act for (src, act) in transitions if src.value == current]


# --- 级联规则（契约 §1.8.3）：被引用对象失效时，引用方进入 stale-ref ---

# 可被 Plan 引用的 SEG 状态
PLAN_BASE_SEG_ALLOWED: frozenset[str] = frozenset({SegmentState.CONFIRMED.value})

# 可被 Report 引用的对象状态
REPORT_REF_ALLOWED: dict[str, frozenset[str]] = {
    "measurement": frozenset({MeasurementState.SAVED.value}),
    "segment": frozenset({SegmentState.CONFIRMED.value}),
    "plan": frozenset({PlanState.SAVED.value, PlanState.EXPORTED.value}),
    # AI 分类/判读任务：分类模型产出的不是一个 SEG，而是一条「结论 + 概率」。
    # 契约原先没有这一类引用，导致模型结论无法进入报告证据链——
    # 只允许 done 态，避免把未完成/失败的推理当证据。
    "ai_job": frozenset({"done"}),
}
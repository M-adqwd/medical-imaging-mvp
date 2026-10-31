"""结构化报告服务（契约 §1.6 / §3.5）。

提交校验：所有引用必须带版本、存在、状态合法、归属同一 Case（契约 §1.6 状态机 callout）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import not_found, state_conflict, validation_error
from app.core.ids import new_dicom_uid, new_uuid
from app.core.security import Principal
from app.adapters.dicomweb import store
from app.dicom import sr as sr_builder
from app.models.case import Case
from app.models.enums import ReportState
from app.models.ai_job import AiJob
from app.models.measurement import Measurement
from app.models.plan import Plan
from app.models.report import Report, ReportVersion
from app.models.segment import Segment
from app.models.state_machines import REPORT_REF_ALLOWED, REPORT_TRANSITIONS
from app.services import state as state_svc

REF_KINDS = tuple(REPORT_REF_ALLOWED)


def get(db: Session, report_id: str) -> Report:
    r = db.get(Report, report_id)
    if r is None:
        raise not_found("Report", report_id)
    return r


def list_by_case(db: Session, case_id: str) -> list[Report]:
    return list(
        db.scalars(select(Report).where(Report.case_id == case_id).order_by(Report.created_at.desc())).all()
    )


def create(
    db: Session,
    case: Case,
    principal: Principal,
    *,
    template_id: str,
    findings: str = "",
    impression: str = "",
    refs: list[dict] | None = None,
) -> Report:
    refs = refs or []
    for ref in refs:
        _validate_ref_shape(ref)
    report = Report(
        report_id=new_uuid(),
        case_id=case.id,
        template_id=template_id,
        findings=findings,
        impression=impression,
        refs=refs,
        state=ReportState.DRAFT,
        author_id=principal.user_id,
    )
    db.add(report)
    db.flush()
    _snapshot(db, report)
    audit.record(
        db,
        category="object",
        action="create",
        actor_id=principal.user_id,
        target_type="report",
        target_uid=report.report_id,
        after_state=ReportState.DRAFT.value,
    )
    return report


def _validate_ref_shape(ref: dict) -> None:
    kind = ref.get("kind")
    if kind not in REF_KINDS:
        raise validation_error(
            f"非法引用类型：{kind}", field="refs[].kind", allowed=list(REF_KINDS)
        )
    if not ref.get("uid"):
        raise validation_error("引用必须带 uid", field="refs[].uid")
    if ref.get("version") is None:
        raise validation_error("引用必须带 version（契约 §1.8.2）", field="refs[].version")


def validate_for_submit(db: Session, report: Report) -> list[str]:
    """返回失效引用描述列表；为空表示通过。"""
    problems: list[str] = []
    if not (report.findings or "").strip():
        problems.append("findings 不能为空")
    if not (report.impression or "").strip():
        problems.append("impression 不能为空")

    for ref in report.refs or []:
        kind, uid, version = ref.get("kind"), ref.get("uid"), ref.get("version")
        allowed = REPORT_REF_ALLOWED.get(kind, frozenset())
        obj = {"measurement": Measurement, "segment": Segment, "plan": Plan,
               "ai_job": AiJob}.get(kind)
        if obj is None:
            problems.append(f"未知引用类型 {kind}")
            continue
        row = db.get(obj, uid) if kind != "plan" else db.get(Plan, uid)
        if row is None:
            problems.append(f"{kind} {uid} 不存在")
            continue
        if kind == "ai_job":
            # AI 任务是「一次推理」，没有版本概念；引用它的意义在于把
            # 模型名/版本/概率钉进报告证据链，因此只校验存在、同 Case、done 态。
            if row.case_id != report.case_id:
                problems.append(f"ai_job {uid} 不属于同一 Case")
            state = row.status.value if hasattr(row.status, "value") else str(row.status)
            if state not in allowed:
                problems.append(f"ai_job {uid} 当前状态 {state} 不可引用，允许 {sorted(allowed)}")
            continue
        if kind == "plan":
            if row.version != version:
                problems.append(f"plan {uid} 版本不匹配：引用 v{version}，当前 v{row.version}")
        elif kind == "segment":
            if row.version != version:
                problems.append(f"segment {uid} 版本不匹配：引用 v{version}，当前 v{row.version}")
        if row.case_id != report.case_id:
            problems.append(f"{kind} {uid} 不属于同一 Case")
        state = row.state.value if hasattr(row.state, "value") else str(row.state)
        if state not in allowed:
            problems.append(f"{kind} {uid} 当前状态 {state} 不可引用，允许 {sorted(allowed)}")
    return problems


def update_draft(db: Session, report: Report, principal: Principal, payload: dict) -> Report:
    if report.state != ReportState.DRAFT:
        raise state_conflict(
            f"只有 draft 态报告可编辑，当前为 {report.state.value}",
            current_state=report.state.value,
            hint="已提交报告需先 :withdraw",
        )
    if "findings" in payload:
        report.findings = payload["findings"]
    if "impression" in payload:
        report.impression = payload["impression"]
    if "refs" in payload:
        for ref in payload["refs"]:
            _validate_ref_shape(ref)
        report.refs = payload["refs"]
    report.updated_at = datetime.now(timezone.utc)
    db.flush()
    _snapshot(db, report)
    return report


def submit(db: Session, report: Report, principal: Principal, case: Case) -> Report:
    problems = validate_for_submit(db, report)
    if problems:
        raise validation_error(
            "报告提交校验失败", failed_refs=problems, report_id=report.report_id
        )
    state_svc.apply(
        db,
        obj=report,
        transitions=REPORT_TRANSITIONS,
        action="submit",
        principal=principal,
        target_type="report",
        target_uid=report.report_id,
    )
    report.submitted_at = datetime.now(timezone.utc)
    db.flush()
    _generate_sr(db, report, case)
    _snapshot(db, report)
    return report


def lock(db: Session, report: Report, principal: Principal) -> Report:
    state_svc.apply(
        db,
        obj=report,
        transitions=REPORT_TRANSITIONS,
        action="lock",
        principal=principal,
        target_type="report",
        target_uid=report.report_id,
    )
    report.reviewed_by = principal.user_id
    report.locked_at = datetime.now(timezone.utc)
    db.flush()
    _snapshot(db, report)
    return report


def withdraw(db: Session, report: Report, principal: Principal) -> Report:
    if report.state == ReportState.LOCKED:
        from app.core.errors import locked as locked_err

        raise locked_err(
            "报告已锁定，不允许撤回；如需修订请新建 Report V2",
            report_id=report.report_id,
        )
    state_svc.apply(
        db,
        obj=report,
        transitions=REPORT_TRANSITIONS,
        action="withdraw",
        principal=principal,
        target_type="report",
        target_uid=report.report_id,
    )
    db.flush()
    _snapshot(db, report)
    return report


def mark_stale(db: Session, report: Report) -> Report:
    report.state = ReportState.STALE_REF
    report.updated_at = datetime.now(timezone.utc)
    db.flush()
    _snapshot(db, report)
    return report


def _generate_sr(db: Session, report: Report, case: Case) -> None:
    # report_id 是 UUID，不是合法 DICOM UID；SR 文档单独分配 UID 并落库
    sr_uid = new_dicom_uid()
    ds = sr_builder.build_report_sr(
        sr_uid=sr_uid,
        study_instance_uid=(case.study_uids or [""])[0],
        patient_id=case.patient_ref,
        patient_name=case.patient_name or "",
        template_id=report.template_id,
        findings=report.findings,
        impression=report.impression,
        refs=report.refs or [],
        author_id=report.author_id,
        study_date=case.study_date,
    )
    data = sr_builder.to_bytes(ds)
    report.sr_content = data
    store.store_bytes(sr_uid, data)


def _snapshot(db: Session, report: Report) -> None:
    payload = {
        "template_id": report.template_id,
        "findings": report.findings,
        "impression": report.impression,
        "refs": report.refs,
        "author_id": report.author_id,
    }
    row = db.get(ReportVersion, {"report_id": report.report_id, "version": report.version})
    if row is None:
        db.add(
            ReportVersion(
                report_id=report.report_id, version=report.version, state=report.state, snapshot=payload
            )
        )
    else:
        row.state = report.state
        row.snapshot = payload
    db.flush()
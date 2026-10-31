"""Case 聚合根服务。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import not_found, state_conflict, validation_error
from app.core.ids import new_uuid
from app.core.security import Principal
from app.models.case import Case, CaseParticipant, StudyRef
from app.models.enums import CaseSource, CaseState
from app.models.state_machines import CASE_TRANSITIONS
from app.services import state as state_svc


def _visible_cases_filter(principal: Principal):  # noqa: ANN202
    if principal.is_admin:
        return None
    return or_(
        Case.owner_id == principal.user_id,
        Case.id.in_(
            select(CaseParticipant.case_id).where(CaseParticipant.user_id == principal.user_id)
        ),
    )


def visible_cases(db: Session, principal: Principal) -> list[Case]:
    """当前用户可见的全部病例（不分页）。

    给 FHIR 门面用的：它要把病例聚合到患者、再聚合成 Bundle，
    分页会切碎聚合结果（同一患者的两条病例落到两页）。数据量是科室级，
    一次取全比维护游标更简单，也不会因此越权——过滤器与列表接口是同一个。
    """
    stmt = select(Case)
    visible = _visible_cases_filter(principal)
    if visible is not None:
        stmt = stmt.where(visible)
    return list(db.scalars(stmt.order_by(Case.updated_at.desc())).all())


def list_cases(
    db: Session,
    principal: Principal,
    *,
    query: str | None = None,
    source: str | None = None,
    modality: str | None = None,
    page: int = 1,
    size: int = 30,
) -> tuple[int, list[Case]]:
    stmt = select(Case)
    count_stmt = select(func.count(Case.id))

    visible = _visible_cases_filter(principal)
    if visible is not None:
        stmt = stmt.where(visible)
        count_stmt = count_stmt.where(visible)

    if query:
        like = f"%{query}%"
        cond = or_(Case.title.ilike(like), Case.patient_ref.ilike(like))
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    if source:
        try:
            src = CaseSource(source)
        except ValueError as exc:
            raise validation_error(f"非法 source：{source}", allowed=[e.value for e in CaseSource]) from exc
        stmt = stmt.where(Case.source == src)
        count_stmt = count_stmt.where(Case.source == src)
    if modality:
        stmt = stmt.where(Case.modality == modality)
        count_stmt = count_stmt.where(Case.modality == modality)

    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.order_by(Case.updated_at.desc()).offset((page - 1) * size).limit(size)).all()
    return total, list(rows)


def get_case(db: Session, case_id: str, principal: Principal) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise not_found("Case", case_id)
    if not principal.is_admin and case.owner_id != principal.user_id:
        linked = db.scalar(
            select(CaseParticipant.id).where(
                CaseParticipant.case_id == case_id,
                CaseParticipant.user_id == principal.user_id,
            )
        )
        if linked is None:
            raise not_found("Case", case_id)
    return case


def create_case(
    db: Session,
    principal: Principal,
    *,
    patient_ref: str,
    studies: list[str],
    source: str = "pacs",
    modality: str | None = None,
    title: str | None = None,
) -> Case:
    if not studies:
        raise validation_error("至少需要一个 Study Instance UID", field="studies")

    # FR-01 自动归并：同一 Study 已有归属 Case 时直接复用，不产生重复 Case
    for uid in studies:
        ref = db.get(StudyRef, uid)
        if ref is not None and ref.case_id:
            prior = db.get(Case, ref.case_id)
            if prior is not None:
                return prior

    refs = [db.get(StudyRef, uid) for uid in studies]
    study_date = next((r.study_date for r in refs if r is not None and r.study_date), None)
    patient_name = next((r.patient_name for r in refs if r is not None and r.patient_name), None)

    case = Case(
        id=new_uuid(),
        patient_ref=patient_ref,
        patient_name=patient_name,
        title=(title or f"{modality or '影像'} · {patient_ref}")[:80],
        status=CaseState.OPEN,
        source=CaseSource(source),
        modality=modality,
        study_date=study_date,
        owner_id=principal.user_id,
        study_uids=list(studies),
    )
    db.add(case)
    db.flush()

    now = datetime.now(timezone.utc)
    for uid in studies:
        ref = db.get(StudyRef, uid)
        if ref is None:
            ref = StudyRef(study_instance_uid=uid, source=source, modalities=[])
            db.add(ref)
        ref.case_id = case.id
        if modality and modality not in (ref.modalities or []):
            ref.modalities = [*(ref.modalities or []), modality]
        ref.last_synced_at = now

    audit.record(
        db,
        category="case_access",
        action="create",
        actor_id=principal.user_id,
        target_type="case",
        target_uid=case.id,
        after_state=case.status.value,
    )
    return case


def close_or_reopen(db: Session, case: Case, principal: Principal, action: str) -> Case:
    state_svc.apply(
        db,
        obj=case,
        transitions=CASE_TRANSITIONS,
        action=action,
        principal=principal,
        target_type="case",
        target_uid=case.id,
        category="case_access",
        state_field="status",
    )
    return case


def touch(case: Case) -> None:
    case.updated_at = datetime.now(timezone.utc)


def add_participant(db: Session, case: Case, principal: Principal, user_id: str) -> Case:
    exists = db.scalar(
        select(CaseParticipant.id).where(
            CaseParticipant.case_id == case.id, CaseParticipant.user_id == user_id
        )
    )
    if exists:
        return case
    db.add(CaseParticipant(case_id=case.id, user_id=user_id))
    audit.record(
        db,
        category="case_access",
        action="add_participant",
        actor_id=principal.user_id,
        target_type="case",
        target_uid=case.id,
        extra={"participant": user_id},
    )
    return case


def ensure_writable(case: Case) -> None:
    if not case.is_writable:
        raise state_conflict(
            f"Case 状态为 {case.status.value}，不允许新建或修改子对象",
            current_state=case.status.value,
            allowed_states=[CaseState.OPEN.value],
        )
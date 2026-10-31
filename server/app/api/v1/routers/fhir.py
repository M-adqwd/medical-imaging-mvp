"""FHIR R4 只读门面（契约 §2.3 事件矩阵在 FHIR 侧的落地）。

范围声明（刻意写进代码而不是只写文档）：
- 只有 read 与 search-type 两类交互，**没有任何写端点**；
- 搜索参数是「够用就行」的子集，不做链式搜索、`_include`、`_revinclude`、`_sort`；
- 资源 ID 是不透明哈希（见 fhir/builder.py 的 ID 策略说明），不承诺可逆。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import current_user, db_session
from app.core.security import Principal, require
from app.dicom.store import archive
from app.fhir import FHIR_MEDIA_TYPE, FHIR_VERSION
from app.fhir import builder as fhir
from app.fhir.errors import FhirError, not_found
from app.models.case import Case
from app.models.measurement import Measurement
from app.models.report import Report
from app.services import cases as case_svc

router = APIRouter(tags=["fhir"])

DEFAULT_COUNT = 50
MAX_COUNT = 100


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/fhir"


def _respond(payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        content=jsonable_encoder(payload),
        media_type=f"{FHIR_MEDIA_TYPE}; fhirVersion={FHIR_VERSION}",
    )


def _visible_cases(db: Session, principal: Principal) -> list[Case]:
    """所有端点共用同一可见性口径：与病例列表接口完全一致，不另开一套权限逻辑。"""
    require(principal, "case.read")
    return case_svc.visible_cases(db, principal)


def _patient_groups(cases: list[Case]) -> dict[str, dict[str, Any]]:
    """按 patient_ref 聚合。同一患者可能有多条病例（多次入院 / 多次检查）。

    姓名优先取病例上人工录入的 patient_name；为空时回落到 DICOM 头里的
    PatientName —— 否则 FHIR 的 Patient 会是一个没有 name 的空壳，
    集成方按姓名匹配时直接失效。
    """
    groups: dict[str, dict[str, Any]] = {}
    for case in cases:
        entry = groups.get(case.patient_ref)
        if entry is None:
            groups[case.patient_ref] = {
                "name": case.patient_name or _name_from_study(case),
                "case_ids": [case.id],
                "updated": case.updated_at,
            }
            continue
        entry["case_ids"].append(case.id)
        if case.updated_at and (entry["updated"] is None or case.updated_at > entry["updated"]):
            entry["updated"] = case.updated_at
            if case.patient_name:
                entry["name"] = case.patient_name
    return groups


def _name_from_study(case: Case) -> str | None:
    for uid in case.study_uids or []:
        record = archive.get_study(uid)
        if record is not None and record.patient_name:
            return record.patient_name
    return None


def _patient_ref_of(cases: list[Case], value: str) -> str | None:
    """把 `Patient/pat-x`、`pat-x` 或原始 MRN 统一解析成 patient_ref。"""
    token = value.split("/", 1)[1] if value.startswith("Patient/") else value
    for case in cases:
        if case.patient_ref == token or fhir.patient_id(case.patient_ref) == token:
            return case.patient_ref
    return None


def _identifier_match(patient_ref: str, value: str) -> bool:
    raw = value.split("|", 1)[1] if "|" in value else value
    if patient_ref == raw:
        return True
    # 本系统的 patient_ref 常带前缀（如 MRN:123），允许对方只给数字部分
    if ":" in patient_ref:
        return patient_ref.split(":", 1)[1] == raw
    return False


@router.get("/metadata")
def capability_statement(request: Request, principal: Principal = Depends(current_user)) -> JSONResponse:
    require(principal, "case.read")
    return _respond(fhir.capability_statement(_base(request), version="1.0.0"))


@router.get("/Patient")
def search_patients(
    request: Request,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
    identifier: str | None = Query(default=None, description="MRN，支持 system|value"),
    name: str | None = Query(default=None, description="姓名，包含匹配"),
    _count: int = Query(default=DEFAULT_COUNT, ge=1, le=MAX_COUNT, alias="_count"),
) -> JSONResponse:
    cases = _visible_cases(db, principal)
    base = _base(request)
    items: list[dict[str, Any]] = []
    for ref, entry in _patient_groups(cases).items():
        if identifier and not _identifier_match(ref, identifier):
            continue
        if name and name.lower() not in (entry["name"] or "").lower():
            continue
        items.append(
            fhir.patient_resource(
                ref,
                entry["name"],
                case_ids=entry["case_ids"],
                last_updated=entry["updated"],
                fhir_base=base,
            )
        )
    items.sort(key=lambda r: r["id"])
    return _respond(
        fhir.search_bundle(items[:_count], total=len(items), self_url=str(request.url), fhir_base=base)
    )


@router.get("/Patient/{resource_id}")
def read_patient(
    resource_id: str,
    request: Request,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> JSONResponse:
    cases = _visible_cases(db, principal)
    base = _base(request)
    for ref, entry in _patient_groups(cases).items():
        if fhir.patient_id(ref) == resource_id:
            return _respond(
                fhir.patient_resource(
                    ref,
                    entry["name"],
                    case_ids=entry["case_ids"],
                    last_updated=entry["updated"],
                    fhir_base=base,
                )
            )
    raise not_found("Patient", resource_id)


@router.get("/ImagingStudy")
def search_imaging_studies(
    request: Request,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
    patient: str | None = Query(default=None, description="Patient id 或 MRN"),
    started: str | None = Query(default=None, description="检查日期前缀，如 2026-10"),
    modality: str | None = Query(default=None),
    _count: int = Query(default=DEFAULT_COUNT, ge=1, le=MAX_COUNT, alias="_count"),
) -> JSONResponse:
    cases = _visible_cases(db, principal)
    base = _base(request)
    target_ref = _patient_ref_of(cases, patient) if patient else None
    if patient and target_ref is None:
        return _respond(fhir.search_bundle([], total=0, self_url=str(request.url), fhir_base=base))

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case in cases:
        if target_ref and case.patient_ref != target_ref:
            continue
        for uid in case.study_uids or []:
            if uid in seen:
                continue
            record = archive.get_study(uid)
            if started and not (fhir.dicom_date(getattr(record, "study_date", None)) or "").startswith(
                started
            ):
                continue
            if modality:
                modalities = {m.upper() for m in (getattr(record, "modalities", None) or [])}
                if modality.upper() not in modalities:
                    continue
            seen.add(uid)
            items.append(
                fhir.imaging_study_resource(
                    uid, record, patient_ref=case.patient_ref, case_id=case.id, fhir_base=base
                )
            )
    items.sort(key=lambda r: r["id"])
    return _respond(
        fhir.search_bundle(items[:_count], total=len(items), self_url=str(request.url), fhir_base=base)
    )


@router.get("/ImagingStudy/{resource_id}")
def read_imaging_study(
    resource_id: str,
    request: Request,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> JSONResponse:
    cases = _visible_cases(db, principal)
    base = _base(request)
    for case in cases:
        for uid in case.study_uids or []:
            if fhir.study_id(uid) == resource_id:
                return _respond(
                    fhir.imaging_study_resource(
                        uid,
                        archive.get_study(uid),
                        patient_ref=case.patient_ref,
                        case_id=case.id,
                        fhir_base=base,
                    )
                )
    raise not_found("ImagingStudy", resource_id)


@router.get("/DiagnosticReport")
def search_diagnostic_reports(
    request: Request,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
    patient: str | None = Query(default=None),
    status: str | None = Query(default=None, description="preliminary / final / cancelled / partial"),
    _count: int = Query(default=DEFAULT_COUNT, ge=1, le=MAX_COUNT, alias="_count"),
) -> JSONResponse:
    cases = _visible_cases(db, principal)
    base = _base(request)
    by_id = {c.id: c for c in cases}
    target_ref = _patient_ref_of(cases, patient) if patient else None
    if patient and target_ref is None:
        return _respond(fhir.search_bundle([], total=0, self_url=str(request.url), fhir_base=base))

    rows = db.scalars(
        select(Report).where(Report.case_id.in_(list(by_id))).order_by(Report.updated_at.desc())
    ).all()
    items: list[dict[str, Any]] = []
    for report in rows:
        case = by_id[report.case_id]
        if target_ref and case.patient_ref != target_ref:
            continue
        resource = fhir.diagnostic_report_resource(
            report,
            patient_ref=case.patient_ref,
            study_uids=case.study_uids or [],
            fhir_base=base,
        )
        if status and resource.get("status") != status:
            continue
        items.append(resource)
    return _respond(
        fhir.search_bundle(items[:_count], total=len(items), self_url=str(request.url), fhir_base=base)
    )


@router.get("/DiagnosticReport/{resource_id}")
def read_diagnostic_report(
    resource_id: str,
    request: Request,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> JSONResponse:
    cases = _visible_cases(db, principal)
    report = db.get(Report, resource_id)
    by_id = {c.id: c for c in cases}
    if report is None or report.case_id not in by_id:
        # 越权与不存在返回同一个 404：否则 404/403 的差异本身就是一次信息泄露
        raise not_found("DiagnosticReport", resource_id)
    case = by_id[report.case_id]
    return _respond(
        fhir.diagnostic_report_resource(
            report,
            patient_ref=case.patient_ref,
            study_uids=case.study_uids or [],
            fhir_base=_base(request),
        )
    )


@router.get("/Observation")
def search_observations(
    request: Request,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
    patient: str | None = Query(default=None),
    status: str | None = Query(default=None, description="preliminary / final / entered-in-error"),
    _count: int = Query(default=DEFAULT_COUNT, ge=1, le=MAX_COUNT, alias="_count"),
) -> JSONResponse:
    cases = _visible_cases(db, principal)
    base = _base(request)
    by_id = {c.id: c for c in cases}
    target_ref = _patient_ref_of(cases, patient) if patient else None
    if patient and target_ref is None:
        return _respond(fhir.search_bundle([], total=0, self_url=str(request.url), fhir_base=base))

    rows = db.scalars(
        select(Measurement)
        .where(Measurement.case_id.in_(list(by_id)))
        .order_by(Measurement.created_at.desc())
    ).all()
    items: list[dict[str, Any]] = []
    for measurement in rows:
        case = by_id[measurement.case_id]
        if target_ref and case.patient_ref != target_ref:
            continue
        resource = fhir.observation_resource(
            measurement, patient_ref=case.patient_ref, study_instance_uid=measurement.study_instance_uid
        )
        if status and resource.get("status") != status:
            continue
        items.append(resource)
    return _respond(
        fhir.search_bundle(items[:_count], total=len(items), self_url=str(request.url), fhir_base=base)
    )


@router.get("/Observation/{resource_id}")
def read_observation(
    resource_id: str,
    request: Request,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> JSONResponse:
    cases = _visible_cases(db, principal)
    by_id = {c.id: c for c in cases}
    rows = db.scalars(select(Measurement).where(Measurement.case_id.in_(list(by_id)))).all()
    for measurement in rows:
        if fhir.observation_id(measurement.sr_uid) != resource_id:
            continue
        case = by_id[measurement.case_id]
        return _respond(
            fhir.observation_resource(
                measurement, patient_ref=case.patient_ref, study_instance_uid=measurement.study_instance_uid
            )
        )
    raise not_found("Observation", resource_id)


@router.get("/Endpoint/{resource_id}")
def read_endpoint(
    resource_id: str,
    request: Request,
    principal: Principal = Depends(current_user),
) -> JSONResponse:
    require(principal, "case.read")
    if resource_id != "dicomweb":
        raise not_found("Endpoint", resource_id)
    return _respond(fhir.endpoint_resource(_base(request)))

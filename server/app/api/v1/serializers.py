"""ORM → 响应模型转换。"""

from __future__ import annotations

from app.dicom import seg as seg_builder
from app.api.v1.schemas import (
    CaseDetailOut,
    CaseOut,
    CommentOut,
    MeasurementOut,
    MediaImportOut,
    PlanOut,
    ReportOut,
    SegmentOut,
    SegmentVolumeOut,
    ShareCreatedOut,
    ShareOut,
    StudyRefOut,
)
from app.services.imports import ImportOutcome
from app.models.ai_job import Comment
from app.models.case import Case, StudyRef
from app.models.measurement import Measurement
from app.models.plan import Plan
from app.models.report import Report
from app.models.segment import Segment
from app.models.share import Share


def _enum(v) -> str:  # noqa: ANN001
    return v.value if hasattr(v, "value") else str(v)


def study_ref_out(ref: StudyRef | None, uid: str) -> StudyRefOut:
    if ref is None:
        return StudyRefOut(study_instance_uid=uid)
    return StudyRefOut(
        study_instance_uid=ref.study_instance_uid,
        series_count=ref.series_count,
        date=ref.study_date,
        description=ref.description,
        modalities=list(ref.modalities or []),
    )


def case_out(case: Case, refs: dict[str, StudyRef]) -> CaseOut:
    return CaseOut(
        case_id=case.id,
        patient_ref=case.patient_ref,
        patient_name=case.patient_name,
        title=case.title,
        status=_enum(case.status),
        source=_enum(case.source),
        modality=case.modality,
        study_date=case.study_date,
        owner_id=case.owner_id,
        participants=[p.user_id for p in case.participants],
        studies=[study_ref_out(refs.get(uid), uid) for uid in (case.study_uids or [])],
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def case_detail(case: Case, refs: dict[str, StudyRef], counts: dict, activity: list) -> CaseDetailOut:
    base = case_out(case, refs)
    return CaseDetailOut(**base.model_dump(), counts=counts, recent_activity=activity)


def measurement_out(m: Measurement) -> MeasurementOut:
    return MeasurementOut(
        sr_uid=m.sr_uid,
        case_id=m.case_id,
        study_instance_uid=m.study_instance_uid,
        series_instance_uid=m.series_instance_uid,
        type=_enum(m.type),
        value=m.value,
        unit=m.unit,
        text=m.text,
        coord_ref=m.coord_ref or {},
        cross_series=m.cross_series,
        state=_enum(m.state),
        creator_id=m.creator_id,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def segment_volume_out(volume: seg_builder.SegmentVolume | None) -> SegmentVolumeOut:
    if volume is None:
        return SegmentVolumeOut(status="unavailable", reason="not_computed")
    return SegmentVolumeOut(
        status=volume.status,
        reason=volume.reason,
        volume_mm3=volume.volume_mm3,
        volume_ml=volume.volume_ml,
        voxel_count=volume.voxel_count,
        spacing_mm=list(volume.spacing_mm) if volume.spacing_mm else None,
    )


def segment_out(s: Segment) -> SegmentOut:
    return SegmentOut(
        seg_uid=s.seg_uid,
        case_id=s.case_id,
        study_instance_uid=s.study_instance_uid,
        segment_name=s.segment_name,
        algorithm=s.algorithm,
        base_seg_uids=list(s.base_seg_uids or []),
        state=_enum(s.state),
        confirmed_by=s.confirmed_by,
        version=s.version,
        creator_id=s.creator_id,
        created_at=s.created_at,
        updated_at=s.updated_at,
        has_pixels=seg_builder.has_pixel_data(s.seg_content),
        volume=segment_volume_out(seg_builder.compute_volume(s.seg_content)),
    )


def import_out(outcome: ImportOutcome) -> MediaImportOut:
    return MediaImportOut(
        source=outcome.source,
        mode=outcome.mode,
        dicomdir=outcome.dicomdir,
        file_set_id=outcome.file_set_id,
        files_found=outcome.files_found,
        files_imported=outcome.files_imported,
        files_failed=outcome.files_failed,
        studies=outcome.studies,
        series=outcome.series,
        instances=outcome.instances,
        study_instance_uids=outcome.study_instance_uids,
        errors=outcome.errors,
    )


def plan_out(p: Plan) -> PlanOut:
    return PlanOut(
        plan_id=p.plan_id,
        case_id=p.case_id,
        base_seg_uid=p.base_seg_uid,
        base_seg_version=p.base_seg_version,
        entry_point=p.entry_point or {},
        target_point=p.target_point or {},
        safe_distance=p.safe_distance,
        anatomical_distance_mm=p.anatomical_distance_mm,
        angle=p.angle,
        risk_level=_enum(p.risk_level),
        risk_basis=p.risk_basis,
        notes=p.notes,
        state=_enum(p.state),
        version=p.version,
        creator_id=p.creator_id,
        exported_at=p.exported_at,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def report_out(r: Report) -> ReportOut:
    return ReportOut(
        report_id=r.report_id,
        case_id=r.case_id,
        template_id=r.template_id,
        findings=r.findings,
        impression=r.impression,
        refs=list(r.refs or []),
        state=_enum(r.state),
        author_id=r.author_id,
        reviewed_by=r.reviewed_by,
        submitted_at=r.submitted_at,
        version=r.version,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def share_out(s: Share) -> ShareOut:
    return ShareOut(
        share_id=s.share_id,
        case_id=s.case_id,
        scope=_enum(s.scope),
        scope_refs=list(s.scope_refs or []),
        permission=_enum(s.permission),
        created_by=s.created_by,
        expires_at=s.expires_at,
        revoked=s.revoked,
        view_count=s.view_count,
        created_at=s.created_at,
    )


def share_created(s: Share, code: str, base_url: str) -> ShareCreatedOut:
    return ShareCreatedOut(
        **share_out(s).model_dump(),
        url=f"{base_url.rstrip('/')}/s/{s.share_id}",
        access_code=code,
    )


def comment_out(c: Comment) -> CommentOut:
    return CommentOut(
        comment_id=c.comment_id,
        case_id=c.case_id,
        body=c.body,
        anchor_type=c.anchor_type,
        anchor_uid=c.anchor_uid,
        mention=c.mention,
        closed=c.closed,
        author_id=c.author_id,
        created_at=c.created_at,
    )
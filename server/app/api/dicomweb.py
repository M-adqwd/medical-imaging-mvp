"""DICOMweb 端点（契约 §2.1）。

实现 QIDO-RS（检索）、WADO-RS（取回）、STOW-RS（存储）三组语义，
数据源为 app.dicom.store 的进程内归档；配置 DICOMWEB_BASE_URL 后
由 adapters 层代理到 Orthanc，应用层接口不变。

严格遵循 DICOM PS3.18：系统层不自定义 DICOMweb 字段（契约 §5.5）。
"""

from __future__ import annotations

import io
from typing import Any

import pydicom
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from app.core.errors import ApiError, BusinessCode
from app.dicom.store import InstanceRecord, SeriesRecord, StudyRecord, archive

router = APIRouter(prefix="/dicomweb", tags=["dicomweb"])

DICOM_JSON = "application/dicom+json"


# ============================ 工具 ============================

def _ds_to_json(ds: pydicom.dataset.Dataset, tags: list[str]) -> dict[str, Any]:
    """按 DICOMweb JSON 模型输出：{"0020000D": [{"vr": "UI", "Value": [...]}]}。"""
    out: dict[str, Any] = {}
    for tag in tags:
        if tag not in ds:
            continue
        elem = ds[tag]
        vr = elem.VR
        value = elem.value
        if isinstance(value, (list, tuple)) or vr == "SQ":
            values = [v for v in (value or [])]
        elif value is None or value == "":
            continue
        else:
            values = [value]
        out[tag] = [{"vr": vr, "Value": values}]
    return out


def _vf(number: int) -> list[dict]:
    return [{"vr": "IS", "Value": [number]}]


def _uc(value: str) -> list[dict]:
    return [{"vr": "UC", "Value": [value]}]


STUDY_TAGS = [
    "00080020",  # StudyDate
    "00080030",  # StudyTime
    "00080050",  # AccessionNumber
    "00080061",  # ModalitiesInStudy
    "00081030",  # StudyDescription
    "00100010",  # PatientName
    "00100020",  # PatientID
    "00100030",  # PatientBirthDate
    "00100040",  # PatientSex
    "0020000D",  # StudyInstanceUID
    "00201206",  # NumberOfStudyRelatedSeries
    "00201208",  # NumberOfStudyRelatedInstances
]

SERIES_TAGS = [
    "00080060",  # Modality
    "0020000E",  # SeriesInstanceUID
    "00200011",  # SeriesNumber
    "00201209",  # NumberOfSeriesRelatedInstances
    "0008103E",  # SeriesDescription
]

INSTANCE_TAGS = [
    "00080016",  # SOPClassUID
    "00080018",  # SOPInstanceUID
    "00200013",  # InstanceNumber
    "00280010",  # Rows
    "00280011",  # Columns
    "00200032",  # ImagePositionPatient —— MPR 重建按切片位置排序所需
    "00280030",  # PixelSpacing
    "00180050",  # SliceThickness
]


def _json_list(rows: list[dict[str, Any]]) -> JSONResponse:
    return JSONResponse(content=rows, media_type=DICOM_JSON)


def _instance_payload(rec: InstanceRecord) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "00080016": _uc(rec.sop_class_uid),
        "00080018": _uc(rec.sop_instance_uid),
        "00200013": _vf(rec.instance_number),
        "00280010": _vf(rec.rows),
        "00280011": _vf(rec.columns),
    }
    ipp = rec.meta.get("ImagePositionPatient") or []
    if ipp:
        payload["00200032"] = {"vr": "DS", "Value": [float(v) for v in ipp]}
    spacing = rec.meta.get("PixelSpacing") or []
    if spacing:
        payload["00280030"] = {"vr": "DS", "Value": [float(v) for v in spacing]}
    thickness = rec.meta.get("SliceThickness") or 0
    if thickness:
        payload["00180050"] = {"vr": "DS", "Value": [float(thickness)]}
    return payload


def _series_payload(series: SeriesRecord) -> dict[str, Any]:
    payload = {
        "0020000E": _uc(series.series_instance_uid),
        "00200011": _vf(series.series_number),
        "00201209": _vf(len(series.instances)),
    }
    if series.modality:
        payload["00080060"] = _uc(series.modality)
    if series.series_description:
        payload["0008103E"] = _uc(series.series_description)
    return payload


def _study_payload(study: StudyRecord) -> dict[str, Any]:
    instances = sum(len(s.instances) for s in study.series.values())
    modalities = sorted({s.modality for s in study.series.values() if s.modality})
    payload: dict[str, Any] = {
        "0020000D": _uc(study.study_instance_uid),
        "00201206": _vf(len(study.series)),
        "00201208": _vf(instances),
    }
    if study.study_date:
        payload["00080020"] = _uc(study.study_date)
    if study.accession_number:
        payload["00080050"] = _uc(study.accession_number)
    if study.study_description:
        payload["00081030"] = _uc(study.study_description)
    if study.patient_name:
        payload["00100010"] = _uc(study.patient_name)
    if study.patient_id:
        payload["00100020"] = _uc(study.patient_id)
    if modalities:
        payload["00080061"] = [{"vr": "CS", "Value": modalities}]
    return payload


# ============================ QIDO-RS ============================

@router.get("/studies", summary="QIDO-RS 查询 Study")
def qido_studies(
    PatientID: str | None = Query(default=None),  # noqa: N803
    PatientName: str | None = Query(default=None),  # noqa: N803
    StudyDate: str | None = Query(default=None),  # noqa: N803
    StudyDescription: str | None = Query(default=None),  # noqa: N803
    AccessionNumber: str | None = Query(default=None),  # noqa: N803
    ModalitiesInStudy: str | None = Query(default=None),  # noqa: N803
    limit: int = Query(default=0, ge=0),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    studies = archive.list_studies(
        PatientID=PatientID,
        PatientName=PatientName,
        StudyDate=StudyDate,
        StudyDescription=StudyDescription,
        AccessionNumber=AccessionNumber,
        ModalitiesInStudy=ModalitiesInStudy,
    )
    if offset:
        studies = studies[offset:]
    if limit:
        studies = studies[:limit]
    return _json_list([_study_payload(s) for s in studies])


@router.get("/studies/{study_uid}/series", summary="QIDO-RS 查询 Series")
def qido_series(study_uid: str) -> JSONResponse:
    study = archive.get_study(study_uid)
    if study is None:
        raise ApiError(BusinessCode.NOT_FOUND, "Study 不存在", {"study_instance_uid": study_uid})
    return _json_list([_series_payload(s) for s in study.series.values()])


@router.get("/studies/{study_uid}/series/{series_uid}/instances", summary="QIDO-RS 查询 Instance")
def qido_instances(study_uid: str, series_uid: str) -> JSONResponse:
    series = archive.get_series(study_uid, series_uid)
    if series is None:
        raise ApiError(
            BusinessCode.NOT_FOUND, "Series 不存在", {"series_instance_uid": series_uid}
        )
    return _json_list([_instance_payload(i) for i in series.instances])


# ============================ WADO-RS ============================

@router.get("/studies/{study_uid}/metadata", summary="WADO-RS Study 元数据")
def wado_study_metadata(study_uid: str) -> JSONResponse:
    study = archive.get_study(study_uid)
    if study is None:
        raise ApiError(BusinessCode.NOT_FOUND, "Study 不存在", {"study_instance_uid": study_uid})
    rows = []
    for series in study.series.values():
        for inst in series.instances:
            ds = archive.read_instance_dataset(inst)
            rows.append(_ds_to_json(ds, INSTANCE_TAGS + ["0020000D", "0020000E", "00200032"]))
    return _json_list(rows)


@router.get("/studies/{study_uid}/series/{series_uid}/metadata", summary="WADO-RS Series 元数据")
def wado_series_metadata(study_uid: str, series_uid: str) -> JSONResponse:
    series = archive.get_series(study_uid, series_uid)
    if series is None:
        raise ApiError(BusinessCode.NOT_FOUND, "Series 不存在", {"series_instance_uid": series_uid})
    rows = []
    for inst in series.instances:
        ds = archive.read_instance_dataset(inst)
        rows.append(_ds_to_json(ds, INSTANCE_TAGS + ["0020000D", "0020000E"]))
    return _json_list(rows)


@router.get(
    "/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}",
    summary="WADO-RS 取回实例",
    response_class=Response,
)
def wado_instance(
    study_uid: str,
    series_uid: str,
    sop_uid: str,
    Accept: str | None = Query(default=None, alias="Accept"),  # noqa: N803, ARG001
) -> Response:
    record = archive.get_instance(sop_uid)
    if record is None or record.study_instance_uid != study_uid or record.series_instance_uid != series_uid:
        raise ApiError(BusinessCode.NOT_FOUND, "实例不存在", {"sop_instance_uid": sop_uid})
    return Response(
        content=archive.read_instance(record),
        media_type="application/dicom",
        headers={
            "Content-Disposition": f'attachment; filename="{sop_uid}.dcm"',
            "Cache-Control": "private, max-age=86400",
        },
    )


@router.get("/series/{series_uid}/metadata", summary="WADO-RS 按 Series UID 取元数据")
def wado_series_uid_metadata(series_uid: str) -> JSONResponse:
    for study in archive.list_studies():
        series = study.series.get(series_uid)
        if series is not None:
            return wado_series_metadata(study.study_instance_uid, series_uid)
    raise ApiError(BusinessCode.NOT_FOUND, "Series 不存在", {"series_instance_uid": series_uid})


# ============================ STOW-RS ============================

@router.post("/studies", summary="STOW-RS 存储", status_code=202)
async def stow(request: Request) -> JSONResponse:
    body = await request.body()
    if not body:
        raise ApiError(BusinessCode.BAD_REQUEST, "请求体为空")

    # DICOMweb 允许 multipart/related 批量；本 MVP 按单实例 application/dicom 处理
    try:
        ds = pydicom.dcmread(io.BytesIO(body), force=True)
    except Exception as exc:
        raise ApiError(
            BusinessCode.BAD_REQUEST, "无法解析 DICOM 数据", {"detail": str(exc)}
        ) from exc

    if "SOPInstanceUID" not in ds:
        raise ApiError(BusinessCode.BAD_REQUEST, "缺少 SOPInstanceUID")

    record = archive.store_bytes(str(ds.SOPInstanceUID), body)
    return JSONResponse(
        status_code=202,
        content={
            "00081195": {
                "vr": "US",
                "Value": [1],
            }
        },
        media_type=DICOM_JSON,
        headers={"X-Study-Instance-UID": record.study_instance_uid},
    )


@router.post("/studies/{study_uid}", summary="STOW-RS 存储到指定 Study")
async def stow_into_study(study_uid: str, request: Request) -> JSONResponse:
    return await stow(request)
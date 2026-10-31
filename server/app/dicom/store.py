"""本地 DICOM 归档：文件存储 + Study/Series/Instance 索引。

这是 DICOMweb 端点的数据源。生产环境应替换为 Orthanc（配置
DICOMWEB_BASE_URL 后由 adapters 层代理），本模块保证无基础设施时
QIDO / WADO / STOW 语义完整可用。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pydicom

from app.core.config import get_settings

# 实例文件扩展名：.dcm 单实例 / .ima DICOMDIR 目录内单帧
_INSTANCE_SUFFIXES = (".dcm", ".ima")


def store_dir() -> Path:
    """归档根目录。介质导入落盘与索引刷新都以此为界。"""
    path = Path(get_settings().dicom_store_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class InstanceRecord:
    sop_instance_uid: str
    study_instance_uid: str
    series_instance_uid: str
    sop_class_uid: str
    instance_number: int
    rows: int
    columns: int
    path: Path
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SeriesRecord:
    series_instance_uid: str
    study_instance_uid: str
    modality: str
    series_number: int
    series_description: str
    instances: list[InstanceRecord] = field(default_factory=list)


@dataclass
class StudyRecord:
    study_instance_uid: str
    patient_id: str
    patient_name: str
    study_date: str
    study_description: str
    accession_number: str
    institution_name: str
    series: dict[str, SeriesRecord] = field(default_factory=dict)


class DicomArchive:
    """线程安全的进程内 DICOM 索引。写入时解析头信息，读取时按需回源文件。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._studies: dict[str, StudyRecord] = {}
        self._loaded = False

    # ---------- 生命周期 ----------

    def ensure_loaded(self) -> None:
        with self._lock:
            if self._loaded:
                return
            for path in sorted(store_dir().rglob("*")):
                if path.suffix.lower() in _INSTANCE_SUFFIXES:
                    try:
                        self._index_file(path)
                    except Exception:  # noqa: BLE001, S110  损坏文件不应阻断启动
                        pass
            self._loaded = True

    def reload(self) -> None:
        with self._lock:
            self._studies.clear()
            self._loaded = False
        self.ensure_loaded()

    # ---------- 写入 ----------

    def store_file(self, path: Path) -> InstanceRecord:
        with self._lock:
            self.ensure_loaded()
            return self._index_file(path)

    def store_bytes(self, sop_uid: str, data: bytes) -> InstanceRecord:
        target = store_dir() / f"{sop_uid}.dcm"
        with self._lock:
            self.ensure_loaded()
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
            return self._index_file(target)

    def _index_file(self, path: Path) -> InstanceRecord:
        ds = pydicom.dcmread(path, stop_before_pixels=True, force=True)
        sop_uid = str(ds.SOPInstanceUID)
        study_uid = str(ds.StudyInstanceUID)
        series_uid = str(ds.SeriesInstanceUID)

        study = self._studies.get(study_uid)
        if study is None:
            study = StudyRecord(
                study_instance_uid=study_uid,
                patient_id=str(getattr(ds, "PatientID", "") or ""),
                patient_name=str(getattr(ds, "PatientName", "") or ""),
                study_date=str(getattr(ds, "StudyDate", "") or ""),
                study_description=str(getattr(ds, "StudyDescription", "") or ""),
                accession_number=str(getattr(ds, "AccessionNumber", "") or ""),
                institution_name=str(getattr(ds, "InstitutionName", "") or ""),
            )
            self._studies[study_uid] = study

        series = study.series.get(series_uid)
        if series is None:
            series = SeriesRecord(
                series_instance_uid=series_uid,
                study_instance_uid=study_uid,
                modality=str(getattr(ds, "Modality", "") or ""),
                series_number=int(getattr(ds, "SeriesNumber", 0) or 0),
                series_description=str(getattr(ds, "SeriesDescription", "") or ""),
            )
            study.series[series_uid] = series

        record = InstanceRecord(
            sop_instance_uid=sop_uid,
            study_instance_uid=study_uid,
            series_instance_uid=series_uid,
            sop_class_uid=str(getattr(ds, "SOPClassUID", "") or ""),
            instance_number=int(getattr(ds, "InstanceNumber", 0) or 0),
            rows=int(getattr(ds, "Rows", 0) or 0),
            columns=int(getattr(ds, "Columns", 0) or 0),
            path=path,
            meta={
                "InstanceNumber": int(getattr(ds, "InstanceNumber", 0) or 0),
                "Rows": int(getattr(ds, "Rows", 0) or 0),
                "Columns": int(getattr(ds, "Columns", 0) or 0),
                "SliceThickness": float(getattr(ds, "SliceThickness", 0) or 0),
                "SpacingBetweenSlices": float(
                    getattr(ds, "SpacingBetweenSlices", 0) or getattr(ds, "SliceThickness", 0) or 0
                ),
                "ImagePositionPatient": [
                    float(v) for v in getattr(ds, "ImagePositionPatient", [])
                ],
                "ImageOrientationPatient": [
                    float(v) for v in getattr(ds, "ImageOrientationPatient", [])
                ],
                "PixelSpacing": [float(v) for v in getattr(ds, "PixelSpacing", [])],
                "FrameOfReferenceUID": str(getattr(ds, "FrameOfReferenceUID", "") or ""),
                "WindowCenter": float(ds.WindowCenter)
                if "WindowCenter" in ds and not isinstance(ds.WindowCenter, (list, tuple))
                else None,
                "WindowWidth": float(ds.WindowWidth)
                if "WindowWidth" in ds and not isinstance(ds.WindowWidth, (list, tuple))
                else None,
            },
        )

        # 同 UID 覆盖（STOW 重传场景）
        series.instances = [i for i in series.instances if i.sop_instance_uid != sop_uid]
        series.instances.append(record)
        series.instances.sort(key=lambda i: (i.instance_number, i.meta.get("SliceLocation", 0) or 0))
        return record

    # ---------- 查询 ----------

    def list_studies(self, **filters: Any) -> list[StudyRecord]:
        self.ensure_loaded()
        out = []
        for study in self._studies.values():
            if _study_matches(study, filters):
                out.append(study)
        return sorted(out, key=lambda s: (s.study_date, s.study_instance_uid), reverse=True)

    def get_study(self, study_uid: str) -> StudyRecord | None:
        self.ensure_loaded()
        return self._studies.get(study_uid)

    def get_series(self, study_uid: str, series_uid: str) -> SeriesRecord | None:
        study = self.get_study(study_uid)
        return study.series.get(series_uid) if study else None

    def get_instance(self, sop_uid: str) -> InstanceRecord | None:
        self.ensure_loaded()
        for study in self._studies.values():
            for series in study.series.values():
                for inst in series.instances:
                    if inst.sop_instance_uid == sop_uid:
                        return inst
        return None

    def read_instance(self, record: InstanceRecord) -> bytes:
        return record.path.read_bytes()

    def read_instance_dataset(self, record: InstanceRecord):
        return pydicom.dcmread(record.path, force=True)


def _study_matches(study: StudyRecord, filters: dict[str, Any]) -> bool:
    def like(value: str, pattern: Any) -> bool:
        if pattern in (None, ""):
            return True
        text = str(pattern).replace("*", "%")
        return value.upper().find(str(text).replace("%", "").upper()) >= 0 if "%" not in text else True

    if pid := filters.get("PatientID"):
        if study.patient_id != pid and not like(study.patient_id, pid):
            return False
    if name := filters.get("PatientName"):
        if not like(study.patient_name.replace("^", " "), name):
            return False
    if date := filters.get("StudyDate"):
        if not like(study.study_date, date):
            return False
    if desc := filters.get("StudyDescription"):
        if not like(study.study_description, desc):
            return False
    if acc := filters.get("AccessionNumber"):
        if study.accession_number != acc:
            return False
    if modalities := filters.get("ModalitiesInStudy"):
        wanted = {m.strip() for m in str(modalities).split(",") if m.strip()}
        have = {s.modality for s in study.series.values()}
        if not wanted & have:
            return False
    return True


archive = DicomArchive()
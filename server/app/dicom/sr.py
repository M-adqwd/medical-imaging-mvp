"""DICOM SR 生成（测量 / 报告 / 规划）。

契约 §0.1 单一事实源：系统生成的测量与报告必须是可被 PACS 读回的标准
DICOM SR 文档，而不是私有 JSON。本模块用 pydicom 构造 TID 1500 测量报告
与 TID 320 影像测量报告骨架。
"""

from __future__ import annotations

from datetime import datetime

import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian

from app.core.ids import new_dicom_uid

# --- SNOMED / DCM 编码 ---
# 测量类型 → (SNOMED code, SNOMED meaning, UCUM unit)
MEASUREMENT_CODES: dict[str, tuple[str, str, str]] = {
    "length": ("410668003", "Length", "mm"),
    "angle": ("1483009", "Angle", "degree"),
    "area": ("42798000", "Area", "mm2"),
    "volume": ("118565006", "Volume", "mm3"),
}

TEXT_CODES = {"free-text": ("17621005", "Free text"), "arrow": ("111030", "Arrow")}


def _file_dataset(sop_class_uid: str, sop_instance_uid: str) -> FileDataset:
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = sop_class_uid
    meta.MediaStorageSOPInstanceUID = sop_instance_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = "1.2.826.0.1.3680043.10.1337.1"
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.SOPClassUID = sop_class_uid
    ds.SOPInstanceUID = sop_instance_uid
    return ds


def _now_ds() -> Dataset:
    ds = Dataset()
    ds.DT = datetime.now().strftime("%Y%m%d%H%M%S.%f")[:17]
    return ds


def _code(value: str, scheme: str, meaning: str) -> Dataset:
    ds = Dataset()
    ds.CodeValue = value
    ds.CodingSchemeDesignator = scheme
    ds.CodeMeaning = meaning
    return ds


def _numeric_value(value: float, unit: str) -> Dataset:
    ds = Dataset()
    ds.NumericValue = f"{value:.4g}"
    ds.MeasurementUnitsCodeSequence = [_code(unit, "UCUM", unit)]
    return ds


def _text_value(text: str) -> Dataset:
    ds = Dataset()
    ds.TextValue = text
    return ds


def build_measurement_sr(
    *,
    sr_uid: str,
    study_instance_uid: str,
    series_instance_uid: str | None,
    patient_id: str,
    patient_name: str,
    measurement_type: str,
    value: float | None,
    unit: str,
    text: str | None,
    coord_ref: dict,
    cross_series: bool,
    creator_id: str,
    study_date: str | None = None,
    series_description: str = "MEASUREMENT",
    frame_of_reference_uid: str | None = None,
) -> FileDataset:
    """构造一份 TID 320 / TID 1500 风格的测量 SR。

    frame_of_reference_uid: coord_ref 非空时必填。它由服务端按 sop_instance_uid
    从影像头派生，不接受前端传入（坐标契约 §6.1）。
    """
    sop_class = "1.2.840.10008.5.1.4.1.1.88.33"  # Comprehensive SR
    ds = _file_dataset(sop_class, sr_uid)

    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = sr_uid
    ds.Modality = "SR"
    # 不再回落到 f"{sr_uid}.1" —— 那会把 SR 挂在一个凭空捏造的 series 下，
    # PACS 读回时无法与源图像关联（契约 §7.2 / X3）。
    ds.SeriesInstanceUID = series_instance_uid or new_dicom_uid()
    ds.SeriesNumber = 9001
    ds.SeriesDescription = series_description
    ds.InstanceNumber = 1
    ds.StudyInstanceUID = study_instance_uid
    ds.StudyDate = study_date or datetime.now().strftime("%Y%m%d")
    ds.StudyTime = datetime.now().strftime("%H%M%S")
    ds.ContentDate = ds.StudyDate
    ds.ContentTime = ds.StudyTime
    ds.AccessionNumber = ""
    ds.Manufacturer = "MIIS"
    ds.ManufacturerModelName = "MIIS Server"
    ds.SoftwareVersions = "0.1.0"

    # 患者（PRD §6.1：只存引用，此处写入最小必要字段）
    ds.PatientName = patient_name
    ds.PatientID = patient_id
    ds.PatientBirthDate = ""
    ds.PatientSex = ""

    ds.ValueType = "CONTAINER"
    ds.ContinuityOfContent = "SEPARATE"
    ds.ConceptNameCodeSequence = [
        _code("126000", "DCM", "Imaging Measurement Report")
    ]
    ds.ContentTemplateSequence = [
        _code("126001", "DCM", "Imaging Measurement Report Template")
    ]

    # 内容序列：测量项
    content = Dataset()
    if measurement_type in MEASUREMENT_CODES:
        code_value, meaning, default_unit = MEASUREMENT_CODES[measurement_type]
        content.RelationshipType = "CONTAINS"
        content.ValueType = "NUM"
        content.ConceptNameCodeSequence = [_code(code_value, "SNOMED", meaning)]
        content.NumericValue = f"{value:.4g}" if value is not None else ""
        content.MeasurementUnitsCodeSequence = [_code(unit or default_unit, "UCUM", unit or default_unit)]
    else:
        code_value, meaning = TEXT_CODES.get(measurement_type, ("17621005", "Free text"))
        content.RelationshipType = "CONTAINS"
        content.ValueType = "TEXT"
        content.ConceptNameCodeSequence = [_code(code_value, "SNOMED", meaning)]
        content.TextValue = text or ""

    if coord_ref:
        # ReferencedSOPSequence 留在 NUM 内容项上，不要挪到 SCOORD3D 项上：
        # PS3.3 Supplement 162 —— SCOORD3D "has no children, since its
        # coordinates are patient rather than image-relative"。
        content.ReferencedSOPSequence = _referenced_sop(
            study_instance_uid, series_instance_uid, coord_ref.get("sop_instance_uid")
        )
        # 空间坐标子项。只写标量长度的话 SR 里就没有任何「这条线画在哪」的信息，
        # 事后无法复核也无法回溯。MPR 是体渲染，端点属于参考坐标系，
        # 因此用 SCOORD3D（DICOM PS3.3 C.18.6）而非图像像素坐标的 SCOORD。
        content.ContentSequence = [_scoord3d_item(coord_ref, frame_of_reference_uid)]

    ds.ContentSequence = [content]

    # 横跨序列标记：PRD FR-02 Exceptions 要求保存时强制写入
    if cross_series:
        ds.DerivationDescription = "CROSS-SERIES MEASUREMENT"

    # 自定义私有块承载系统元数据，不污染标准字段
    priv = ds.private_block(0x000B, "MIIS SR", create=True)
    priv.add_new(0x01, "LO", creator_id)
    priv.add_new(0x02, "UI", study_instance_uid)
    priv.add_new(0x03, "CS", "CROSS_SERIES" if cross_series else "SINGLE_SERIES")

    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.preamble = b"\0" * 128
    return ds


def _scoord3d_item(coord_ref: dict, frame_of_reference_uid: str | None) -> Dataset:
    """把端点坐标写成 SR 的 SCOORD3D 内容项。

    GraphicData 单位与 FrameOfReferenceUID 指定的坐标系一致（LPS mm）。
    GraphicType=POLYLINE 时 6 个值 = 两个端点的 (x, y, z)。
    """
    data = coord_ref.get("graphic_data") or []
    item = Dataset()
    item.RelationshipType = "CONTAINS"
    item.ValueType = "SCOORD3D"
    item.ConceptNameCodeSequence = [_code("121206", "DCM", "Measurement Location")]
    item.GraphicType = coord_ref.get("graphic_type") or "POLYLINE"
    item.GraphicData = [float(v) for v in data]
    # MPR 体渲染没有单张参照图像，坐标归属于参考坐标系而非某帧。
    # ReferencedFrameOfReferenceUID (3006,0024) 是 PS3.3 C.18.9 的 Type 1：
    # 条件省略它会产出一份「坐标值都在、但没人知道属于哪个空间」的 SCOORD3D。
    if not frame_of_reference_uid:
        raise ValueError(
            "SCOORD3D 缺少 ReferencedFrameOfReferenceUID（PS3.3 C.18.9 Type 1）；"
            "没有空间归属的坐标不得写入 SR"
        )
    item.ReferencedFrameOfReferenceUID = frame_of_reference_uid
    return item


def _referenced_sop(
    study_uid: str, series_uid: str | None, sop_instance_uid: str | None
) -> list[Dataset]:
    # 缺引用时静默返回 [] 会生成一份没有源图像引用的测量 SR —— 引用不全
    # 必须由调用方在入口挡掉，这里再兜一次（契约 §7.3 / X4）。
    if not sop_instance_uid:
        raise ValueError("ReferencedSOPSequence 需要 sop_instance_uid，缺失时不得生成空引用")
    if not series_uid:
        raise ValueError("ReferencedSOPSequence 需要 series_instance_uid，缺失时不得生成空引用")
    sop = Dataset()
    sop.ReferencedSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT Image Storage
    sop.ReferencedSOPInstanceUID = sop_instance_uid
    series = Dataset()
    series.SeriesInstanceUID = series_uid
    series.ReferencedSOPSequence = [sop]
    study = Dataset()
    study.StudyInstanceUID = study_uid
    study.ReferencedSeriesSequence = [series]
    return [study]


def build_report_sr(
    *,
    sr_uid: str,
    study_instance_uid: str,
    patient_id: str,
    patient_name: str,
    template_id: str,
    findings: str,
    impression: str,
    refs: list[dict],
    author_id: str,
    study_date: str | None = None,
) -> FileDataset:
    """构造结构化报告 SR（TID 3200 / Comprehensive SR）。"""
    sop_class = "1.2.840.10008.5.1.4.1.1.88.33"
    ds = _file_dataset(sop_class, sr_uid)

    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = sr_uid
    ds.Modality = "SR"
    # UID 长度上限 64 字符；report_id 是 UUID（含连字符，非合法 UI），故独立生成
    ds.SeriesInstanceUID = new_dicom_uid()
    ds.SeriesNumber = 9002
    ds.SeriesDescription = "REPORT"
    ds.InstanceNumber = 1
    ds.StudyInstanceUID = study_instance_uid
    ds.StudyDate = study_date or datetime.now().strftime("%Y%m%d")
    ds.StudyTime = datetime.now().strftime("%H%M%S")
    ds.ContentDate = ds.StudyDate
    ds.ContentTime = ds.StudyTime
    ds.Manufacturer = "MIIS"
    ds.PatientName = patient_name
    ds.PatientID = patient_id
    ds.PatientBirthDate = ""
    ds.PatientSex = ""

    ds.ValueType = "CONTAINER"
    ds.ContinuityOfContent = "SEPARATE"
    ds.ConceptNameCodeSequence = [_code("126000", "DCM", "Imaging Measurement Report")]
    ds.CompletionFlag = "PARTIAL"
    ds.VerificationFlag = "UNVERIFIED"

    content = Dataset()

    findings_item = Dataset()
    findings_item.RelationshipType = "CONTAINS"
    findings_item.ValueType = "TEXT"
    findings_item.ConceptNameCodeSequence = [_code("121058", "DCM", "Procedure reported")]
    findings_item.TextValue = findings

    impression_item = Dataset()
    impression_item.RelationshipType = "CONTAINS"
    impression_item.ValueType = "TEXT"
    impression_item.ConceptNameCodeSequence = [_code("121051", "DCM", "Impression")]
    impression_item.TextValue = impression

    content.ContentSequence = [findings_item, impression_item]

    for ref in refs:
        item = Dataset()
        item.RelationshipType = "CONTAINS"
        item.ValueType = "TEXT"
        item.ConceptNameCodeSequence = [_code("121049", "DCM", "Language of Content Item")]
        item.TextValue = f"{ref.get('kind', 'unknown')} {ref.get('uid')} v{ref.get('version')}"
        content.ContentSequence.append(item)

    ds.ContentSequence = [content]

    priv = ds.private_block(0x000B, "MIIS SR", create=True)
    priv.add_new(0x01, "LO", author_id)
    priv.add_new(0x02, "LO", template_id)

    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.preamble = b"\0" * 128
    return ds


def build_plan_sr(
    *,
    sr_uid: str,
    study_instance_uid: str,
    series_instance_uid: str | None,
    patient_id: str,
    patient_name: str,
    plan_id: str,
    plan_version: int,
    base_seg_uid: str,
    base_seg_version: int,
    base_seg_name: str,
    entry_point: dict,
    target_point: dict,
    safe_distance: float,
    angle: float,
    risk_level: str,
    creator_id: str,
    study_date: str | None = None,
) -> FileDataset:
    """构造手术规划导出 SR（TID 3200 风格的 Comprehensive SR）。

    PRD FR-05：导出必须携带 SEG 版本号，避免引用过期分割。
    """
    sop_class = "1.2.840.10008.5.1.4.1.1.88.33"
    ds = _file_dataset(sop_class, sr_uid)

    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = sr_uid
    ds.Modality = "SR"
    ds.SeriesInstanceUID = series_instance_uid or new_dicom_uid()
    ds.SeriesNumber = 9002
    ds.SeriesDescription = "PLAN"
    ds.InstanceNumber = 1
    ds.StudyInstanceUID = study_instance_uid
    ds.StudyDate = study_date or datetime.now().strftime("%Y%m%d")
    ds.StudyTime = datetime.now().strftime("%H%M%S")
    ds.ContentDate = ds.StudyDate
    ds.ContentTime = ds.StudyTime
    ds.AccessionNumber = ""
    ds.Manufacturer = "MIIS"
    ds.ManufacturerModelName = "MIIS Server"
    ds.SoftwareVersions = "0.1.0"

    ds.PatientName = patient_name
    ds.PatientID = patient_id

    ds.ValueType = "CONTAINER"
    ds.ContinuityOfContent = "SEPARATE"
    ds.ConceptNameCodeSequence = [_code("126000", "DCM", "Imaging Measurement Report")]

    # 文本结论
    text = (
        f"PLAN {plan_id} v{plan_version}\n"
        f"base_seg: {base_seg_uid} v{base_seg_version} ({base_seg_name})\n"
        f"entry: {entry_point}\n"
        f"target: {target_point}\n"
        f"safe_distance: {safe_distance} mm\n"
        f"angle: {angle} deg\n"
        f"risk: {risk_level}\n"
    )
    ds.ContentSequence = [
        Dataset()
    ]
    content = ds.ContentSequence[0]
    content.RelationshipType = "CONTAINS"
    content.ValueType = "TEXT"
    content.ConceptNameCodeSequence = [_code("125007", "DCM", "Measurement Group")]
    content.TextValue = text

    # 引用基础 SEG（含版本号，PRD FR-05 硬要求）
    ref = Dataset()
    ref.RelationshipType = "CONTAINS"
    ref.ValueType = "IMAGE"
    ref.ConceptNameCodeSequence = [_code("121112", "DCM", "Source of Measurement")]
    ref.ReferencedSOPSequence = [Dataset()]
    ref.ReferencedSOPSequence[0].ReferencedSOPClassUID = "1.2.840.10008.5.1.4.1.1.66.4"
    ref.ReferencedSOPSequence[0].ReferencedSOPInstanceUID = base_seg_uid
    ds.ContentSequence.append(ref)

    # 完成标志
    completion = Dataset()
    completion.RelationshipType = "CONTAINS"
    completion.ValueType = "CODE"
    completion.ConceptNameCodeSequence = [_code("111001", "DCM", "Completion Flag")]
    completion.ConceptCodeSequence = [_code("COMPLETE", "DCM", "Complete")]
    ds.ContentSequence.append(completion)

    ds.CompletionFlag = "COMPLETE"
    ds.VerificationFlag = "UNVERIFIED"

    # 作者
    author = Dataset()
    author.RelationshipType = "HAS CONCEPT MOD"
    author.ValueType = "PN"
    author.ConceptNameCodeSequence = [_code("121006", "DCM", "Person Observer")]
    author.PersonName = creator_id
    ds.ContentSequence.append(author)

    return ds


def to_bytes(ds: FileDataset) -> bytes:
    import io

    buf = io.BytesIO()
    pydicom.dcmwrite(buf, ds, enforce_file_format=True)
    return buf.getvalue()


def from_bytes(data: bytes) -> Dataset:
    return pydicom.dcmread(__import__("io").BytesIO(data))
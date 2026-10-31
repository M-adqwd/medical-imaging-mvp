"""DICOM SEG 生成。

SEG 是分割的唯一事实源（契约 §1.4）。手动与 AI 结果都必须落为
标准 SEG 实例，本模块负责生成元数据骨架；像素数据由客户端/AI 服务
以 base64 传入或从已有 SEG 透传。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian

SEG_STORAGE_UID = "1.2.840.10008.5.1.4.1.1.66.4"  # Segmentation Storage

# 体积状态。只有 VOLUME_OK 时数值字段才有值。
VOLUME_OK = "ok"
VOLUME_UNAVAILABLE = "unavailable"


def _code(value: str, scheme: str, meaning: str) -> Dataset:
    ds = Dataset()
    ds.CodeValue = value
    ds.CodingSchemeDesignator = scheme
    ds.CodeMeaning = meaning
    return ds


def _segment_item(seg_number: int, name: str, algorithm: str, tracking: str = "1.2.826.0.1.3680043.10.1337.1") -> Dataset:
    item = Dataset()
    item.SegmentNumber = seg_number
    item.SegmentLabel = name
    item.SegmentAlgorithmType = "AUTOMATIC" if algorithm.startswith("ai:") else "MANUAL"
    item.SegmentAlgorithmName = algorithm.split(":")[-1] if algorithm.startswith("ai:") else algorithm
    item.SegmentedPropertyCategoryCodeSequence = [_code("T-D0050", "SRT", "Tissue")]
    item.SegmentedPropertyTypeCodeSequence = [_code("T-D4000", "SRT", "Mass")]
    if algorithm.startswith("ai:"):
        tracking_item = Dataset()
        tracking_item.TrackingIdentifier = name
        tracking_item.TrackingUniqueIdentifier = tracking
        item.SegmentIdentificationSequence = [tracking_item]
    return item


def build_seg_header(
    *,
    seg_uid: str,
    study_instance_uid: str,
    series_instance_uid: str | None,
    patient_id: str,
    patient_name: str,
    segment_name: str,
    algorithm: str,
    referenced_series: list[dict],
    study_date: str | None = None,
    body_part: str = "",
) -> FileDataset:
    """生成 SEG 实例的元数据部分（含 PixelData 槽位）。

    referenced_series: [{"series_instance_uid": ..., "sop_instance_uids": [...]}]
    """
    meta = Dataset()
    meta.MediaStorageSOPClassUID = SEG_STORAGE_UID
    meta.MediaStorageSOPInstanceUID = seg_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    ds.SOPClassUID = SEG_STORAGE_UID
    ds.SOPInstanceUID = seg_uid
    ds.Modality = "SEG"
    ds.SeriesInstanceUID = series_instance_uid or f"{seg_uid}.1"
    ds.SeriesNumber = 9100
    ds.SeriesDescription = f"SEG {segment_name}"
    ds.InstanceNumber = 1
    ds.ImageType = ["DERIVED", "PRIMARY"]
    ds.StudyInstanceUID = study_instance_uid
    ds.StudyDate = study_date or datetime.now().strftime("%Y%m%d")
    ds.StudyTime = datetime.now().strftime("%H%M%S")
    ds.ContentDate = ds.StudyDate
    ds.ContentTime = ds.StudyTime
    ds.Manufacturer = "MIIS"
    # 默认字符集 ISO-IR 100 装不下中文，段名会落成一串问号（且不可逆）
    ds.SpecificCharacterSet = "ISO_IR 192"
    ds.PatientName = patient_name
    ds.PatientID = patient_id
    ds.PatientBirthDate = ""
    ds.PatientSex = ""

    ds.ContentLabel = "SEGMENTATION"
    ds.ContentDescription = "Artificial Intelligence Segmentation" if algorithm.startswith("ai:") else "Manual Segmentation"
    ds.ContentCreatorName = algorithm

    ds.SegmentationType = "BINARY"
    ds.SegmentsOverlap = "NO"
    ds.LossyImageCompression = "00"
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 1
    ds.BitsStored = 1
    ds.HighBit = 0
    ds.PixelRepresentation = 0
    ds.SegmentationFractionalType = "NONE"

    ds.SegmentSequence = [_segment_item(1, segment_name, algorithm)]

    per_frame = Dataset()
    ref_series_seq = []
    frame_counter = 1
    for series in referenced_series:
        sop_items = []
        for sop_uid in series.get("sop_instance_uids", []):
            dim = Dataset()
            dim.DimensionIndexValues = [frame_counter]
            dim.FrameContentSequence = [
                Dataset()
            ]
            dim.FrameContentSequence[0].DimensionIndexValues = [frame_counter]
            dim.FrameContentSequence[0].StackID = "1"
            dim.FrameContentSequence[0].InStackPositionNumber = frame_counter
            dim.SegmentIdentificationSequence = [
                Dataset()
            ]
            dim.SegmentIdentificationSequence[0].ReferencedSegmentNumber = 1
            sop = Dataset()
            sop.ReferencedSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT Image Storage
            sop.ReferencedSOPInstanceUID = sop_uid
            sop.DimensionIndexValues = [frame_counter]
            sop.DerivationImageSequence = [dim]
            sop_items.append(sop)
            frame_counter += 1

        series_item = Dataset()
        series_item.SeriesInstanceUID = series.get("series_instance_uid")
        series_item.ReferencedSOPSequence = sop_items
        ref_series_seq.append(series_item)

    study_item = Dataset()
    study_item.StudyInstanceUID = study_instance_uid
    study_item.ReferencedSeriesSequence = ref_series_seq
    ds.ReferencedSeriesSequence = ref_series_seq
    ds.StudyInstanceUID = study_instance_uid

    # 私有块记录系统元数据（模型名/版本/UID），不污染标准语义
    priv = ds.private_block(0x000B, "MIIS SEG", create=True)
    priv.add_new(0x01, "LO", algorithm)
    priv.add_new(0x02, "UI", seg_uid)

    ds.preamble = b"\0" * 128
    return ds


def wrap_existing_seg(seg_bytes: bytes, algorithm: str, segment_name: str) -> bytes:
    """AI 服务返回已有 SEG 时，补写算法元数据后透传。"""
    import io

    import pydicom

    ds = pydicom.dcmread(io.BytesIO(seg_bytes))
    ds.ContentCreatorName = algorithm
    if not getattr(ds, "ContentDescription", None):
        ds.ContentDescription = segment_name
    buf = io.BytesIO()
    pydicom.dcmwrite(buf, ds, enforce_file_format=True)
    return buf.getvalue()


def _read_seg(seg_bytes: bytes | None) -> pydicom.dataset.Dataset | None:
    """解析 SEG 字节。

    注意：不能用 stop_before_pixels=True —— 那样 PixelData 元素根本不会被
    读入 dataset，"PixelData" in ds 恒为 False，带像素的合法 SEG 会被误判成空壳。
    """
    import io

    if not seg_bytes:
        return None
    try:
        return pydicom.dcmread(io.BytesIO(seg_bytes))
    except Exception:  # noqa: BLE001 - 解析失败一律按无像素处理，不掩盖上游错误
        return None


def _has_pixels(ds: pydicom.dataset.Dataset | None) -> bool:
    if ds is None:
        return False
    if "PixelData" not in ds:
        return False
    if not (getattr(ds, "Rows", None) and getattr(ds, "Columns", None)):
        return False
    try:
        return len(ds.PixelData) > 0
    except Exception:  # noqa: BLE001
        return False


def has_pixel_data(seg_bytes: bytes | None) -> bool:
    """SEG 是否真的带像素数据。

    build_seg_header 只产出元数据骨架（无 Rows/Columns/PixelData），
    这类「空壳 SEG」不能作为任何解剖学计算（安全距离 / 风险分级 / 体积）的依据。
    """
    return _has_pixels(_read_seg(seg_bytes))


# ============================ 病灶体积 ============================


@dataclass(frozen=True)
class SegmentVolume:
    """SEG 病灶体积。

    status != VOLUME_OK 时所有数值字段一律为 None。**没有就是 None，
    不是 0，也不是估算值** —— 空壳 SEG 或缺少 spacing 时编一个体积出来，
    等于拿假数字冒充解剖学测量结果。
    """

    status: str
    reason: str | None = None
    volume_mm3: float | None = None
    volume_ml: float | None = None
    voxel_count: int | None = None
    spacing_mm: tuple[float, float, float] | None = None


def _unavailable(reason: str) -> SegmentVolume:
    return SegmentVolume(status=VOLUME_UNAVAILABLE, reason=reason)


def _spacing_of(holder: Any) -> tuple[float, float, float] | None:
    """从 PixelMeasuresSequence 或顶层取 (行距, 列距, 层距)，单位 mm。

    取不到就返回 None：不知道体素物理尺寸时算不出体积，绝不假设 1mm。
    """
    pixel_spacing = getattr(holder, "PixelSpacing", None)
    if pixel_spacing is None or len(pixel_spacing) < 2:
        return None
    try:
        row_mm = float(pixel_spacing[0])
        col_mm = float(pixel_spacing[1])
    except (TypeError, ValueError):
        return None
    if row_mm <= 0 or col_mm <= 0:
        return None
    # SpacingBetweenSlices 才是层间距；只有 SliceThickness 时它会把层间
    # 重叠/间隙算进去，但对无间隙序列两者相等，可接受。
    for attr in ("SpacingBetweenSlices", "SliceThickness"):
        raw = getattr(holder, attr, None)
        if raw is None:
            continue
        try:
            slice_mm = float(raw)
        except (TypeError, ValueError):
            continue
        if slice_mm > 0:
            return (row_mm, col_mm, slice_mm)
    return None


def _pixel_spacing_mm(ds: pydicom.dataset.Dataset) -> tuple[float, float, float] | None:
    """SEG 的像素物理尺寸在功能组序列里，顶层通常没有。"""
    for keyword in ("SharedFunctionalGroupsSequence", "PerFrameFunctionalGroupsSequence"):
        for item in getattr(ds, keyword, None) or []:
            for measures in getattr(item, "PixelMeasuresSequence", None) or []:
                spacing = _spacing_of(measures)
                if spacing is not None:
                    return spacing
    return _spacing_of(ds)


def compute_volume(seg_bytes: bytes | None) -> SegmentVolume:
    """按体素计数 × 三轴间距算病灶体积。

    只接受二值（BINARY）分割：FRACTIONAL / PROBABILITY / OCCUPANCY 的体素值
    是概率或占位率，非零计数不是体素个数，拿它乘间距会得出无意义的体积。
    """
    ds = _read_seg(seg_bytes)
    if not _has_pixels(ds):
        return _unavailable("no_pixel_data")

    segmentation_type = str(getattr(ds, "SegmentationType", "") or "").strip().upper()
    fractional_type = str(getattr(ds, "SegmentationFractionalType", "") or "").strip().upper()
    if segmentation_type == "FRACTIONAL" or fractional_type in ("PROBABILITY", "OCCUPANCY"):
        return _unavailable("fractional_segmentation")

    spacing = _pixel_spacing_mm(ds)
    if spacing is None:
        return _unavailable("missing_pixel_spacing")

    try:
        array = ds.pixel_array
    except Exception:  # noqa: BLE001
        return _unavailable("pixel_decode_failed")

    # BitsAllocated=1 的二值 SEG 是位打包的，pixel_array 已解包成 0/1，
    # 直接 count_nonzero 就是体素个数（不能拿 len(PixelData) 当体素数）。
    voxel_count = int(np.count_nonzero(array))
    row_mm, col_mm, slice_mm = spacing
    volume_mm3 = float(voxel_count) * row_mm * col_mm * slice_mm
    return SegmentVolume(
        status=VOLUME_OK,
        volume_mm3=volume_mm3,
        # 1 mL = 1000 mm³
        volume_ml=volume_mm3 / 1000.0,
        voxel_count=voxel_count,
        spacing_mm=spacing,
    )


def decode(seg_b64: str) -> bytes:
    import base64

    return base64.b64decode(seg_b64)


def to_bytes(ds: FileDataset) -> bytes:
    import io

    import pydicom

    buf = io.BytesIO()
    pydicom.dcmwrite(buf, ds, enforce_file_format=True)
    return buf.getvalue()
"""病灶体积计算（SEG）。

核心约束：算不出来就返回 None / 显式 unavailable，**绝不返回 0，也绝不编数**。
上一轮事故就是空壳 SEG 被当成有解剖学含义的对象参与计算。
"""

from __future__ import annotations

import io

import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian

from app.dicom import seg as seg_builder

API = "/api/v1"

VOXEL_MM = (0.7, 0.7, 1.0)  # 行距, 列距, 层距


def _seg_with_mask(voxel_count: int, *, with_spacing: bool = True) -> bytes:
    """造一个带真实像素的 BINARY SEG：前 voxel_count 个体素置 1。"""
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = seg_builder.SEG_STORAGE_UID
    meta.MediaStorageSOPInstanceUID = "1.2.826.0.1.3680043.10.1337.9020"
    meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.SOPClassUID = seg_builder.SEG_STORAGE_UID
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.Modality = "SEG"
    ds.Rows = 8
    ds.Columns = 8
    ds.NumberOfFrames = 2
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 1
    ds.BitsStored = 1
    ds.HighBit = 0
    ds.PixelRepresentation = 0
    ds.SegmentationType = "BINARY"

    total = ds.Rows * ds.Columns * ds.NumberOfFrames
    flat = np.zeros(total, dtype=np.uint8)
    flat[:voxel_count] = 1
    ds.PixelData = np.packbits(flat, bitorder="little").tobytes()

    if with_spacing:
        measures = Dataset()
        measures.PixelSpacing = [VOXEL_MM[0], VOXEL_MM[1]]
        measures.SpacingBetweenSlices = VOXEL_MM[2]
        shared = Dataset()
        shared.PixelMeasuresSequence = [measures]
        ds.SharedFunctionalGroupsSequence = [shared]
    return seg_builder.to_bytes(ds)


def test_header_only_seg_has_no_volume():
    """空壳 SEG：unavailable，且数值字段必须是 None 而不是 0。"""
    header = seg_builder.build_seg_header(
        seg_uid="1.2.826.0.1.3680043.10.1337.9010",
        study_instance_uid="1.2.826.0.1.3680043.10.1337.9001",
        series_instance_uid=None,
        patient_id="P1",
        patient_name="T^P",
        segment_name="肝",
        algorithm="manual",
        referenced_series=[],
    )
    volume = seg_builder.compute_volume(seg_builder.to_bytes(header))

    assert volume.status == seg_builder.VOLUME_UNAVAILABLE
    assert volume.reason == "no_pixel_data"
    assert volume.volume_mm3 is None
    assert volume.volume_ml is None
    assert volume.voxel_count is None
    assert volume.spacing_mm is None


def test_none_bytes_is_unavailable():
    volume = seg_builder.compute_volume(None)
    assert volume.status == seg_builder.VOLUME_UNAVAILABLE
    assert volume.volume_mm3 is None


def test_garbage_bytes_is_unavailable():
    volume = seg_builder.compute_volume(b"not a dicom file at all")
    assert volume.status == seg_builder.VOLUME_UNAVAILABLE
    assert volume.reason == "no_pixel_data"
    assert volume.volume_mm3 is None


def test_volume_from_voxel_count_and_spacing():
    """体素计数 × 三轴间距：100 体素 × 0.7 × 0.7 × 1.0 = 49 mm³ = 0.049 mL。"""
    volume = seg_builder.compute_volume(_seg_with_mask(100))

    assert volume.status == seg_builder.VOLUME_OK
    assert volume.voxel_count == 100
    assert volume.spacing_mm == VOXEL_MM
    assert volume.volume_mm3 == 49.0
    assert volume.volume_ml == 0.049


def test_bits_are_unpacked_not_counted_as_bytes():
    """BitsAllocated=1 是位打包：len(PixelData) 远小于体素数。"""
    volume = seg_builder.compute_volume(_seg_with_mask(37))
    assert volume.voxel_count == 37
    # 位打包下 128 bit → 16 字节，若把它当体素数会得到 16 而不是 37
    assert volume.volume_mm3 == 37 * 0.7 * 0.7 * 1.0


def test_missing_spacing_is_unavailable_not_zero():
    """有像素但不知道体素物理尺寸 → 算不出体积，绝不能按 1mm 顶上。"""
    volume = seg_builder.compute_volume(_seg_with_mask(100, with_spacing=False))

    assert volume.status == seg_builder.VOLUME_UNAVAILABLE
    assert volume.reason == "missing_pixel_spacing"
    assert volume.volume_mm3 is None
    assert volume.volume_ml is None
    # voxel_count 数得出来，但没有间距就没有物理体积
    assert volume.voxel_count is None


def test_fractional_segmentation_is_unavailable():
    """PROBABILITY / OCCUPANCY 的体素值不是 0/1 归属，不能直接当体素计数。"""
    ds = pydicom.dcmread(io.BytesIO(_seg_with_mask(100)))
    ds.SegmentationType = "FRACTIONAL"
    ds.SegmentationFractionalType = "PROBABILITY"
    volume = seg_builder.compute_volume(seg_builder.to_bytes(ds))

    assert volume.status == seg_builder.VOLUME_UNAVAILABLE
    assert volume.reason == "fractional_segmentation"
    assert volume.volume_mm3 is None


# ============ API 输出 ============


def test_segment_out_exposes_volume_unavailable_for_header_only(client, attending, idem, case, study_uid):
    import base64

    header = seg_builder.build_seg_header(
        seg_uid="1.2.826.0.1.3680043.10.1337.9011",
        study_instance_uid=study_uid,
        series_instance_uid=None,
        patient_id="P1",
        patient_name="T^P",
        segment_name="肝",
        algorithm="manual",
        referenced_series=[],
    )
    created = client.post(
        f"{API}/cases/{case['case_id']}/segments",
        json={
            "study_instance_uid": study_uid,
            "segment_name": "肝",
            "algorithm": "manual",
            "seg_content": base64.b64encode(seg_builder.to_bytes(header)).decode(),
        },
        headers={**attending, **idem()},
    )
    assert created.status_code == 201, created.text
    body = created.json()

    assert body["has_pixels"] is False
    assert body["volume"]["status"] == "unavailable"
    assert body["volume"]["reason"] == "no_pixel_data"
    # 关键：不是 0
    assert body["volume"]["volume_mm3"] is None
    assert body["volume"]["volume_ml"] is None


def test_segment_out_exposes_computed_volume(client, attending, idem, case, study_uid):
    import base64

    created = client.post(
        f"{API}/cases/{case['case_id']}/segments",
        json={
            "study_instance_uid": study_uid,
            "segment_name": "肝",
            "algorithm": "manual",
            "seg_content": base64.b64encode(_seg_with_mask(100)).decode(),
        },
        headers={**attending, **idem()},
    )
    assert created.status_code == 201, created.text
    body = created.json()

    assert body["has_pixels"] is True
    assert body["volume"]["status"] == "ok"
    assert body["volume"]["voxel_count"] == 100
    assert body["volume"]["volume_mm3"] == 49.0
    assert body["volume"]["volume_ml"] == 0.049
    assert body["volume"]["spacing_mm"] == [0.7, 0.7, 1.0]

#!/usr/bin/env python3
"""生成合成腹部 CT 序列，用于 MVP 阅片演示。

产出：单序列（.ima 目录格式，便于 Cornerstone 按 frame 组织）
  - 512x512，128 层，1mm 层厚，真实几何（PixelSpacing / IPP / SliceThickness）
  - 内容：椎体、脊髓、主动脉、肝脏样楔形、双肾样椭球、腹主动脉、脾脏、气腔

合成数据仅用于工程验证，不具备任何临床意义。
"""

from __future__ import annotations

import argparse
import math
import shutil
import uuid
from pathlib import Path

import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian

# --- 几何参数（毫米） ---
COLS = ROWS = 512
SLICES = 128
PIX_SPACING = (0.7, 0.7)  # 行间距(y), 列间距(x)
SLICE_THICK = 1.0
SLICE_GAP = 0.0
FOV = COLS * PIX_SPACING[0]

HU_AIR = -1000.0
HU_BODY = 40.0          # 软组织
HU_LIVER = 60.0        # 肝样楔形
HU_KIDNEY = 50.0
HU_SPINE_BONE = 700.0  # 椎体皮质
HU_MARROW = 200.0
HU_AORTA = 130.0       # 增强血管
HU_GAS = -900.0

# DICOM 组织根 + 本工具标识，长度 21，留出 43 字符给实例后缀
UID_ROOT = "1.2.826.0.1.3680043.10.1337"


def _new_uid() -> str:
    """生成 2.25.<uuid128> 形式的合法 UID（44 字符，留出帧号后缀空间）。"""
    return f"2.25.{uuid.uuid4().int}"


def _voxel_grid() -> tuple[np.ndarray, float, float]:
    """返回 (x_mm, y_mm, z_mm) 一维坐标轴，原点在体中心。"""
    xs = (np.arange(COLS) - (COLS - 1) / 2) * PIX_SPACING[1]
    ys = (np.arange(ROWS) - (ROWS - 1) / 2) * PIX_SPACING[0]
    zs = (np.arange(SLICES) - (SLICES - 1) / 2) * (SLICE_THICK + SLICE_GAP)
    return xs, ys, zs


def _ellipsoid_volume(
    shape: tuple[int, int], xs: np.ndarray, ys: np.ndarray, cx: float, cy: float, rx: float, ry: float
) -> np.ndarray:
    """在当前层的 XY 平面上生成椭圆掩膜。"""
    xx, yy = np.meshgrid(xs - cx, ys - cy)
    return (xx / rx) ** 2 + (yy / ry) ** 2 <= 1.0


def _hu_volume() -> np.ndarray:
    """构造 (SLICES, ROWS, COLS) 的 HU 体数据。"""
    xs, ys, zs = _voxel_grid()
    xx, yy = np.meshgrid(xs, ys)

    # 人体轮廓：纵向为椭圆，横向随 z 变化（上腹窄、下腹宽）
    body_rx = 170.0 + 26.0 * np.cos(zs / 150.0)
    body_ry = 120.0 + 30.0 * np.cos(zs / 130.0)
    body = ((xx[None, :, :] / body_rx[:, None, None]) ** 2
            + (yy[None, :, :] / body_ry[:, None, None]) ** 2) <= 1.0

    vol = np.full((SLICES, ROWS, COLS), HU_AIR, dtype=np.float32)
    vol[body] = HU_BODY

    # 皮下脂肪层
    fat = ((xx[None, :, :] / (body_rx[:, None, None] - 8)) ** 2
           + (yy[None, :, :] / (body_ry[:, None, None] - 8)) ** 2) <= 1.0
    vol[fat & ~body] = -100.0
    vol[fat & body] = -70.0

    # 脊柱：后方正中，椎体 + 脊髓 + 棘突
    spine_z = (zs > -60) & (zs < 130)
    vert = _ellipsoid_volume((ROWS, COLS), xs, ys, 0.0, 105.0, 22.0, 26.0)
    vert_3d = vert[None, :, :] & spine_z[:, None, None]
    vol[vert_3d] = HU_SPINE_BONE

    canal = _ellipsoid_volume((ROWS, COLS), xs, ys, 0.0, 105.0, 9.0, 11.0)
    vol[canal[None, :, :] & spine_z[:, None, None]] = HU_MARROW

    # 主动脉：椎体前方的圆形增强血管
    aorta = _ellipsoid_volume((ROWS, COLS), xs, ys, -12.0, 55.0, 11.0, 11.0)
    aorta_3d = aorta[None, :, :] & (zs > -30)[:, None, None]
    vol[aorta_3d] = HU_AORTA

    # 肝脏：右上腹楔形（患者右侧 = 图像左侧 = 负 x）
    liver_z = (zs > -10) & (zs < 105)
    liver = _ellipsoid_volume((ROWS, COLS), xs, ys, -78.0, 15.0, 82.0, 62.0)
    vol[liver[None, :, :] & liver_z[:, None, None]] = HU_LIVER

    # 双肾：两侧椭球
    kidney_z = (zs > 10) & (zs < 95)
    for sign in (-1, 1):
        k = _ellipsoid_volume((ROWS, COLS), xs, ys, sign * 95.0, 20.0, 26.0, 34.0)
        vol[k[None, :, :] & kidney_z[:, None, None]] = HU_KIDNEY

    # 脾脏：左上腹
    spleen_z = (zs > 30) & (zs < 100)
    spleen = _ellipsoid_volume((ROWS, COLS), xs, ys, 92.0, 25.0, 38.0, 32.0)
    vol[spleen[None, :, :] & spleen_z[:, None, None]] = HU_LIVER

    # 胃气腔：左上腹低密度团（近黑）
    gas_z = (zs > 5) & (zs < 75)
    gas = _ellipsoid_volume((ROWS, COLS), xs, ys, 55.0, 55.0, 34.0, 26.0)
    vol[gas[None, :, :] & gas_z[:, None, None]] = HU_GAS

    # 高斯噪声，模拟 CT 重建噪声
    rng = np.random.default_rng(42)
    vol += rng.normal(0.0, 8.0, vol.shape).astype(np.float32)
    # 轻微 Z 轴 MTF 模糊，模拟层间部分容积效应
    for k in range(1, SLICES - 1):
        vol[k] = (vol[k - 1] + 2 * vol[k] + vol[k + 1]) / 4.0

    return vol


def _dataset(
    idx: int, study_uid: str, series_uid: str, frame_of_ref: str, frame_uid: str, z_mm: float
) -> FileDataset:
    meta = FileMetaDataset()
    sop_class = CTImageStorage
    meta.MediaStorageSOPClassUID = sop_class
    meta.MediaStorageSOPInstanceUID = frame_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = "1.2.826.0.1.3680043.10.1337.1"

    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = frame_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.Modality = "CT"
    ds.FrameOfReferenceUID = frame_of_ref
    ds.PositionReferenceIndicator = ""

    # 患者（合成身份，不含任何真实 PII）
    ds.PatientName = "SYNTHETIC^PHANTOM"
    ds.PatientID = "SYN-CT-001"
    ds.PatientBirthDate = ""
    ds.PatientSex = ""

    ds.StudyID = "1"
    ds.StudyDate = "20261022"
    ds.AccessionNumber = "SYN0001"
    ds.StudyTime = "093000"
    ds.ReferringPhysicianName = ""
    ds.Manufacturer = "MIIS"
    ds.ManufacturerModelName = "SyntheticAbdomenCT"
    ds.InstitutionName = "MIIS Synthetic Lab"
    ds.StudyDescription = "SYNTHETIC ABDOMEN CT"
    ds.SeriesDescription = "Axial 1mm"

    # 几何：像素间距、层厚、方向余弦（标准轴向）
    ds.PixelSpacing = [PIX_SPACING[0], PIX_SPACING[1]]
    ds.SliceThickness = SLICE_THICK
    ds.SpacingBetweenSlices = SLICE_THICK + SLICE_GAP
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.ImagePositionPatient = [-FOV / 2, -FOV / 2, z_mm]
    ds.SliceLocation = z_mm
    ds.InstanceNumber = idx + 1
    ds.ImageType = ["DERIVED", "SECONDARY", "AXIAL"]
    ds.KVP = 120.0
    ds.XRayTubeCurrent = 250
    ds.Exposure = 250
    ds.ConvolutionKernel = "B30f"

    # 像素编码：RescaleSlope/Intercept 把存储值映射到 HU
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows = ROWS
    ds.Columns = COLS
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.RescaleIntercept = -1024.0
    ds.RescaleSlope = 1.0
    ds.RescaleType = "HU"
    ds.WindowCenter = 40.0
    ds.WindowWidth = 400.0

    return ds


def generate(out_dir: Path, patient_id: str, seed: int) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    study_uid = _new_uid()
    series_uid = _new_uid()
    frame_of_ref = _new_uid()

    # DICOM 约定：ImagePositionPatient.z 单调递增
    _, _, zs = _voxel_grid()
    vol = _hu_volume()

    written = 0
    for idx, z in enumerate(zs):
        hu = vol[idx]
        # 存储值 = HU - intercept（intercept=-1024），落在 unsigned 16-bit 范围
        stored = np.clip(hu + 1024.0, 0, 4095).astype(np.uint16)

        frame_uid = f"{series_uid}.{idx + 1}"
        assert len(frame_uid) <= 64, f"SOPInstanceUID 超长: {len(frame_uid)}"
        ds = _dataset(idx, study_uid, series_uid, frame_of_ref, frame_uid, float(z))
        ds.PixelData = stored.tobytes()

        path = out_dir / f"{idx:04d}.ima"
        pydicom.dcmwrite(path, ds, enforce_file_format=True)
        written += 1

    return {
        "study_instance_uid": study_uid,
        "series_instance_uid": series_uid,
        "frame_of_reference_uid": frame_of_ref,
        "instances": written,
        "rows": ROWS,
        "columns": COLS,
        "pixel_spacing": PIX_SPACING,
        "slice_thickness": SLICE_THICK,
        "z_range": [float(zs[0]), float(zs[-1])],
        "patient_id": patient_id,
        "output_dir": str(out_dir),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="生成合成腹部 CT 序列")
    ap.add_argument("--out", default="./data/synthetic-ct", help="输出目录")
    ap.add_argument("--clean", action="store_true", help="输出前清空目录")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    if args.clean and out.exists():
        shutil.rmtree(out)

    meta = generate(out, patient_id="SYN-CT-001", seed=42)
    print("合成 CT 序列已生成")
    for k, v in meta.items():
        print(f"  {k}: {v}")
    print(f"\n体积: {sum(f.stat().st_size for f in out.glob('*.ima')) / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
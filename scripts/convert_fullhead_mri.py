"""把真实体数据（FullHead MRI，VTK examples 公开测试数据）转成 DICOM 序列。

数据来源：https://gitlab.kitware.com/vtk/vtk-examples src/Testing/Data/FullHead.mhd
（真实人体头部 MR 扫描，256×256×94，0.9375/0.9375/1.5mm，12-bit 值域）

产出写到 data_real/staging/mri-head/，随后走 POST /imports/directory 入档。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pydicom
from pydicom.dataset import Dataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data_real" / "FullHead.raw"
OUT = ROOT / "data_real" / "staging" / "mri-head"

SPACING = (0.9375, 0.9375, 1.5)  # row, col, slice（mhd 的 ElementSpacing）
DIMS = (256, 256, 94)


def main() -> None:
    vol = np.fromfile(RAW, dtype="<i2").reshape(DIMS[2], DIMS[1], DIMS[0])
    vol = vol.astype(np.uint16)

    OUT.mkdir(parents=True, exist_ok=True)
    study_uid = generate_uid()
    series_uid = generate_uid()
    frame_uid = generate_uid()
    now = datetime.now()

    base = Dataset()
    base.SpecificCharacterSet = "ISO_IR 100"
    base.SOPClassUID = "1.2.840.10008.5.1.4.1.1.4"  # MR Image Storage
    base.Modality = "MR"
    base.StudyInstanceUID = study_uid
    base.SeriesInstanceUID = series_uid
    base.FrameOfReferenceUID = frame_uid
    base.StudyDate = now.strftime("%Y%m%d")
    base.StudyTime = now.strftime("%H%M%S")
    base.AccessionNumber = ""
    base.StudyID = "1"
    base.StudyDescription = "REAL MRI HEAD (VTK example data, anonymized)"
    base.SeriesDescription = "Real human head MR, 256x256x94"
    base.PatientName = "ANON^REALMRHEAD"
    base.PatientID = "MRN:REAL-MRI-001"
    base.PatientBirthDate = ""
    base.PatientSex = "O"
    base.Manufacturer = "anonymized public dataset"
    base.KVP = ""
    base.Rows = DIMS[1]
    base.Columns = DIMS[0]
    base.PixelSpacing = [SPACING[0], SPACING[1]]
    base.SliceThickness = SPACING[2]
    base.SpacingBetweenSlices = SPACING[2]
    base.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    base.ImageType = ["ORIGINAL", "PRIMARY", "OTHER"]
    base.BitsAllocated = 16
    base.BitsStored = 16
    base.HighBit = 15
    base.PixelRepresentation = 0
    base.SamplesPerPixel = 1
    base.PhotometricInterpretation = "MONOCHROME2"
    base.WindowCenter = 1024
    base.WindowWidth = 2048
    base.RescaleSlope = 1
    base.RescaleIntercept = 0

    for k in range(DIMS[2]):
        ds = Dataset()
        # 逐元素拷贝公共头
        for elem in base:
            ds.add(elem)
        ds.SOPInstanceUID = generate_uid()
        ds.InstanceNumber = k + 1
        ds.ImagePositionPatient = [0.0, 0.0, k * SPACING[2]]
        ds.SliceLocation = k * SPACING[2]
        ds.file_meta = pydicom.dataset.FileMetaDataset()
        ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.is_little_endian = True
        ds.is_implicit_VR = False
        ds.PixelData = np.ascontiguousarray(vol[k]).tobytes()
        ds.save_as(OUT / f"{k:04d}.dcm", write_like_original=False)

    print(f"study_uid={study_uid}")
    print(f"series_uid={series_uid}")
    print(f"wrote {DIMS[2]} slices -> {OUT}")


if __name__ == "__main__":
    sys.exit(main())

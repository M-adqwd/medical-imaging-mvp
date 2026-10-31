"""光盘 / 移动存储介质导入（DICOMDIR）。

用 pydicom 的 FileSet 生成真实 CD 布局（DICOMDIR + PT/ST/SE/IM 无扩展名
实例文件）作为夹具，避免用假数据结构自证。
"""

from __future__ import annotations

import uuid

import pydicom
import pytest
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.fileset import FileSet
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian

from app.core.config import get_settings
from app.dicom import media
from app.dicom.store import archive

API = "/api/v1"

STUDY_UID = "1.2.826.0.1.3680043.10.1337.9001"
SERIES_UID = "1.2.826.0.1.3680043.10.1337.9001.1"


@pytest.fixture
def media_store(tmp_path, monkeypatch):
    """把归档目录指到临时目录，避免测试污染真实的 data/dicom。"""
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("DICOM_STORE_DIR", str(store))
    get_settings.cache_clear()
    archive.reload()
    yield store
    monkeypatch.undo()
    get_settings.cache_clear()
    archive.reload()


def _write_instance(path, instance_number: int) -> FileDataset:
    sop_uid = f"{SERIES_UID}.{instance_number}"
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = sop_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = STUDY_UID
    ds.SeriesInstanceUID = SERIES_UID
    ds.Modality = "CT"
    ds.SeriesNumber = 1
    ds.InstanceNumber = instance_number
    ds.PatientName = "CD^PHANTOM"
    ds.PatientID = "CD-0001"
    ds.StudyDate = "20260101"
    ds.StudyTime = "120000"
    ds.StudyID = "1"
    ds.AccessionNumber = "CD0001"
    ds.StudyDescription = "CD IMPORT"
    ds.Rows = 4
    ds.Columns = 4
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = b"\0" * 16
    ds.save_as(str(path), enforce_file_format=True)
    return ds


def _build_cd(tmp_path, count: int = 4):
    """生成 CD 布局，返回介质根目录。实例文件全部无扩展名。"""
    staging = tmp_path / "staging"
    staging.mkdir()
    for i in range(1, count + 1):
        _write_instance(staging / f"src{i}.dcm", i)

    file_set = FileSet()
    for path in sorted(staging.glob("*.dcm")):
        file_set.add(pydicom.dcmread(path, stop_before_pixels=True))
    media_root = tmp_path / "CDROM"
    media_root.mkdir()
    file_set.write(str(media_root))
    return media_root


# ============ DICOMDIR 解析 ============


def test_dicomdir_attribute_spelling(tmp_path):
    """钉死 DICOMDIR 关键字拼写：pydicom 3.x 用的是这些名字。"""
    root = _build_cd(tmp_path, 2)
    dicomdir = media.find_dicomdir(root)
    assert dicomdir is not None and dicomdir.is_file()

    ds = pydicom.dcmread(dicomdir)
    assert ds.file_meta.MediaStorageSOPClassUID == media.MEDIA_STORAGE_DIRECTORY_UID
    records = ds.DirectoryRecordSequence
    types = [r.DirectoryRecordType for r in records]
    assert types[0] == "PATIENT"
    assert "STUDY" in types and "SERIES" in types and "IMAGE" in types

    image = next(r for r in records if r.DirectoryRecordType == "IMAGE")
    # ReferencedFileID 是多值元素（pydicom 解成 MultiValue），不是拼好的路径字符串
    assert not isinstance(image.ReferencedFileID, str)
    assert len(image.ReferencedFileID) > 1
    assert image.ReferencedSOPInstanceUIDInFile


def test_scan_dicomdir_finds_extensionless_instances(tmp_path):
    root = _build_cd(tmp_path, 4)
    scan = media.scan_directory(root)

    assert scan.mode == media.MODE_DICOMDIR
    assert scan.dicomdir is not None
    assert scan.file_count == 4
    # CD 上的实例文件没有扩展名 —— 后缀扫描是扫不出来的
    assert all(e.path.suffix == "" for e in scan.entries)
    assert all(e.path.is_file() for e in scan.entries)
    assert all(e.sop_instance_uid for e in scan.entries)
    assert {e.study_instance_uid for e in scan.entries} == {STUDY_UID}
    assert {e.series_instance_uid for e in scan.entries} == {SERIES_UID}


def test_scan_falls_back_to_filesystem(tmp_path):
    """无 DICOMDIR 时按后缀递归扫。"""
    root = tmp_path / "usb"
    (root / "series1").mkdir(parents=True)
    _write_instance(root / "series1" / "a.dcm", 1)
    _write_instance(root / "series1" / "b.dcm", 2)
    (root / "README.txt").write_text("not dicom")

    scan = media.scan_directory(root)
    assert scan.mode == media.MODE_FILESYSTEM
    assert scan.dicomdir is None
    assert scan.file_count == 2


def test_scan_filesystem_picks_up_extensionless_with_dicom_magic(tmp_path):
    """无扩展名但有 DICM magic 的文件也要扫到。"""
    root = tmp_path / "usb"
    root.mkdir()
    _write_instance(root / "IM000000", 7)
    (root / "AUTORUN.INF").write_text("[autorun]")

    scan = media.scan_directory(root)
    assert scan.file_count == 1
    assert scan.entries[0].path.name == "IM000000"


def test_has_dicom_magic(tmp_path):
    dicom_file = tmp_path / "x.dcm"
    _write_instance(dicom_file, 1)
    assert media.has_dicom_magic(dicom_file)

    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"JPEGDATA" * 40)
    assert not media.has_dicom_magic(junk)


# ============ 导入端点 ============


def test_import_directory_endpoint(client, attending, idem, media_store, tmp_path):
    root = _build_cd(tmp_path, 4)
    r = client.post(
        f"{API}/imports/directory",
        json={"path": str(root)},
        headers={**attending, **idem()},
    )
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["mode"] == media.MODE_DICOMDIR
    assert body["dicomdir"] is not None
    assert body["files_found"] == 4
    assert body["files_imported"] == 4
    assert body["files_failed"] == 0
    assert body["studies"] == 1
    assert body["series"] == 1
    assert body["instances"] == 4
    assert body["study_instance_uids"] == [STUDY_UID]

    # 导入后索引已刷新：QIDO 能查到（DICOMweb JSON 里每个 tag 是 [{vr, Value}]）
    qido = client.get("/dicomweb/studies").json()
    assert any(s["0020000D"][0]["Value"][0] == STUDY_UID for s in qido)


def test_import_directory_filesystem_mode(client, attending, idem, media_store, tmp_path):
    root = tmp_path / "usb"
    root.mkdir()
    _write_instance(root / "a.dcm", 1)
    _write_instance(root / "b.dcm", 2)

    r = client.post(
        f"{API}/imports/directory",
        json={"path": str(root)},
        headers={**attending, **idem()},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["mode"] == media.MODE_FILESYSTEM
    assert body["instances"] == 2


def test_import_directory_requires_idempotency_key(client, attending, media_store, tmp_path):
    root = _build_cd(tmp_path, 2)
    r = client.post(f"{API}/imports/directory", json={"path": str(root)}, headers=attending)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_import_directory_rejects_missing_path(client, attending, idem, media_store, tmp_path):
    r = client.post(
        f"{API}/imports/directory",
        json={"path": str(tmp_path / "nope")},
        headers={**attending, **idem()},
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_import_directory_rejects_relative_path(client, attending, idem, media_store):
    r = client.post(
        f"{API}/imports/directory",
        json={"path": "relative/dir"},
        headers={**attending, **idem()},
    )
    assert r.status_code == 422


def test_import_directory_denied_for_reviewer(client, reviewer, idem, media_store, tmp_path):
    root = _build_cd(tmp_path, 2)
    r = client.post(
        f"{API}/imports/directory",
        json={"path": str(root)},
        headers={**reviewer, **idem()},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "PERMISSION_DENIED"


def test_import_directory_is_idempotent(client, attending, media_store, tmp_path):
    """同一幂等键重入：回放首次响应，不重复计数。"""
    root = _build_cd(tmp_path, 3)
    headers = {**attending, "Idempotency-Key": str(uuid.uuid4())}

    first = client.post(f"{API}/imports/directory", json={"path": str(root)}, headers=headers)
    assert first.status_code == 201
    again = client.post(f"{API}/imports/directory", json={"path": str(root)}, headers=headers)
    assert again.status_code == 201
    assert again.json()["instances"] == first.json()["instances"]


def test_import_allows_only_configured_roots(client, attending, idem, media_store, tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("DICOM_IMPORT_ROOTS", f'["{allowed.as_posix()}"]')
    get_settings.cache_clear()
    try:
        outside = _build_cd(tmp_path, 2)
        r = client.post(
            f"{API}/imports/directory",
            json={"path": str(outside)},
            headers={**attending, **idem()},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "PERMISSION_DENIED"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()

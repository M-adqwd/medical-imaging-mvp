"""光盘 / 移动存储介质导入：DICOMDIR 解析与目录扫描。

DICOMDIR 是 DICOM Media Storage Directory（SOP Class 1.2.840.10008.1.3.10），
用一个扁平的 DirectoryRecordSequence 描述 PATIENT → STUDY → SERIES → IMAGE
层级，每个 IMAGE 记录用 ReferencedFileID 给出相对介质的路径分量。

属性拼写已用 pydicom 3.0.2 实际生成的 DICOMDIR 核对（见 tests）：
  DirectoryRecordSequence            (0004,1220)
    DirectoryRecordType              (0004,1430)  PATIENT / STUDY / SERIES / IMAGE
    RecordInUseFlag                  (0004,1410)  0xFFFF=在用, 0=已删除
    OffsetOfTheNextDirectoryRecord   (0004,1400)  同级下一条
    OffsetOfReferencedLowerLevelDirectoryEntity (0004,1420) 下级首条
    ReferencedFileID                 (0004,1500)  多值，路径分量
    ReferencedSOPClassUIDInFile      (0004,1510)
    ReferencedSOPInstanceUIDInFile   (0004,1511)
    ReferencedTransferSyntaxUIDInFile(0004,1512)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pydicom

logger = logging.getLogger("miis")

MEDIA_STORAGE_DIRECTORY_UID = "1.2.840.10008.1.3.10"

# 介质索引文件本身不是影像实例
_DICOMDIR_NAMES = ("DICOMDIR", "dicomdir", "Dicomdir")

# 与 store._INSTANCE_SUFFIXES 保持一致；单独声明避免扫描器与归档耦合
_INSTANCE_SUFFIXES = (".dcm", ".ima")

# preamble(128B) + "DICM"
_DICM_MAGIC = b"DICM"
_PREAMBLE_LEN = 128

MODE_DICOMDIR = "dicomdir"
MODE_FILESYSTEM = "filesystem"


@dataclass
class MediaEntry:
    """介质上的一个实例文件。"""

    path: Path
    record_type: str = "IMAGE"
    study_instance_uid: str = ""
    series_instance_uid: str = ""
    sop_instance_uid: str = ""
    instance_number: int = 0


@dataclass
class MediaScan:
    """一次介质扫描的结果。"""

    root: Path
    mode: str
    dicomdir: Path | None = None
    file_set_id: str = ""
    entries: list[MediaEntry] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.entries)


# ---------- 介质探测 ----------


def find_dicomdir(root: Path) -> Path | None:
    """定位 DICOMDIR：先看介质根，再看一层子目录（常见 D:\\DICOM\\DICOMDIR）。"""
    candidates: list[Path] = [root]
    try:
        candidates.extend(sorted(d for d in root.iterdir() if d.is_dir()))
    except OSError:  # noqa: BLE001  光驱未就绪 / 权限不足
        pass
    for directory in candidates:
        for name in _DICOMDIR_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def has_dicom_magic(path: Path) -> bool:
    """读前 132 字节判断是否为带 preamble 的 DICOM 文件。"""
    try:
        with path.open("rb") as handle:
            head = handle.read(_PREAMBLE_LEN + len(_DICM_MAGIC))
    except OSError:  # noqa: BLE001
        return False
    if len(head) < _PREAMBLE_LEN + len(_DICM_MAGIC):
        return False
    return head[_PREAMBLE_LEN:] == _DICM_MAGIC


def read_header(path: Path) -> pydicom.dataset.Dataset | None:
    """只读头信息；非 DICOM 或损坏文件返回 None。"""
    try:
        return pydicom.dcmread(str(path), stop_before_pixels=True, force=True)
    except Exception:  # noqa: BLE001  单个坏文件不应中断整张盘的导入
        logger.debug("介质文件头解析失败: %s", path, exc_info=True)
        return None


# ---------- DICOMDIR 解析 ----------


def _file_id_parts(record: pydicom.dataset.Dataset) -> list[str]:
    """ReferencedFileID 是多值元素（'\\' 分隔的路径分量），也可能是单值字符串。"""
    raw = getattr(record, "ReferencedFileID", None)
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    return [str(part) for part in raw if str(part).strip()]


def parse_dicomdir(dicomdir: Path) -> MediaScan:
    """按 DirectoryRecordType 遍历层级，收集 IMAGE 记录指向的文件。

    pydicom 把 DirectoryRecordSequence 解析成**扁平**列表（记录之间的父子
    关系只体现在偏移量里），所以这里按「深度优先顺序 + 层级上下文」还原
    PATIENT / STUDY / SERIES 归属。
    """
    root = dicomdir.parent
    scan = MediaScan(root=root, mode=MODE_DICOMDIR, dicomdir=dicomdir)

    dataset = read_header(dicomdir)
    if dataset is None:
        return scan
    scan.file_set_id = str(getattr(dataset, "FileSetID", "") or "")

    records = getattr(dataset, "DirectoryRecordSequence", None)
    if not records:
        return scan

    context: dict[str, pydicom.dataset.Dataset | None] = {
        "PATIENT": None,
        "STUDY": None,
        "SERIES": None,
    }

    for record in records:
        record_type = str(getattr(record, "DirectoryRecordType", "") or "").strip().upper()
        if not record_type:
            continue
        # 0 = 该记录已被删除（RecordInUseFlag），不应导入
        if int(getattr(record, "RecordInUseFlag", 0xFFFF) or 0) == 0:
            continue

        if record_type in context:
            context[record_type] = record
            # 回到上层时清空下层上下文
            if record_type == "PATIENT":
                context["STUDY"] = None
                context["SERIES"] = None
            elif record_type == "STUDY":
                context["SERIES"] = None
            continue

        if record_type != "IMAGE":
            continue

        parts = _file_id_parts(record)
        if not parts:
            continue
        path = root.joinpath(*parts)

        study = context["STUDY"]
        series = context["SERIES"]
        try:
            instance_number = int(getattr(record, "InstanceNumber", 0) or 0)
        except (TypeError, ValueError):
            instance_number = 0

        entry = MediaEntry(
            path=path,
            record_type=record_type,
            study_instance_uid=str(getattr(study, "StudyInstanceUID", "") or ""),
            series_instance_uid=str(getattr(series, "SeriesInstanceUID", "") or ""),
            sop_instance_uid=str(getattr(record, "ReferencedSOPInstanceUIDInFile", "") or ""),
            instance_number=instance_number,
        )
        if path.is_file():
            scan.entries.append(entry)
        else:
            scan.missing.append(str(path))

    return scan


# ---------- 目录扫描 ----------


def _scan_filesystem(root: Path) -> MediaScan:
    """无 DICOMDIR 时按文件递归扫描。

    光盘上大量实例文件没有扩展名（IM000000），后缀判断扫不出来；这里用
    128 字节 preamble + "DICM" magic 判定，避免把 AUTORUN.INF 之类的杂项
    文件喂给解析器。
    """
    scan = MediaScan(root=root, mode=MODE_FILESYSTEM)
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not _is_scan_candidate(path):
            continue

        dataset = read_header(path)
        if dataset is None:
            continue
        scan.entries.append(
            MediaEntry(
                path=path,
                study_instance_uid=str(getattr(dataset, "StudyInstanceUID", "") or ""),
                series_instance_uid=str(getattr(dataset, "SeriesInstanceUID", "") or ""),
                sop_instance_uid=str(getattr(dataset, "SOPInstanceUID", "") or ""),
            )
        )
    return scan


def _is_scan_candidate(path: Path) -> bool:
    """是否值得尝试解析。只认已知后缀，或无后缀 + DICM magic 的文件。"""
    if path.name.upper() in {name.upper() for name in _DICOMDIR_NAMES}:
        return False
    if path.suffix.lower() in _INSTANCE_SUFFIXES:
        return True
    return path.suffix == "" and has_dicom_magic(path)


def scan_directory(root: Path | str) -> MediaScan:
    """扫描介质：有 DICOMDIR 走 DICOMDIR，否则按文件递归扫。"""
    directory = Path(root)
    dicomdir = find_dicomdir(directory)
    if dicomdir is not None:
        return parse_dicomdir(dicomdir)
    return _scan_filesystem(directory)

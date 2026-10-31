"""光盘 / 移动存储介质导入服务。

需求：「支持 PACS 调阅及光盘、移动存储导入」。PACS 走 DICOMweb（QIDO/WADO），
介质走本模块：扫描 DICOMDIR 或裸目录 → 拷贝进归档目录 → 刷新索引。

拷贝而非原地索引是刻意的：光盘会被弹出，原地索引会让数据在拔盘后
变成一堆指向不存在路径的悬空记录。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import ApiError, BusinessCode
from app.core.config import get_settings
from app.core.security import Principal
from app.dicom import media
from app.dicom.store import archive, store_dir


@dataclass
class ImportOutcome:
    source: str
    mode: str
    dicomdir: str | None = None
    file_set_id: str = ""
    files_found: int = 0
    files_imported: int = 0
    files_failed: int = 0
    studies: int = 0
    series: int = 0
    instances: int = 0
    study_instance_uids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _resolve_root(path: str) -> Path:
    root = Path(path).expanduser()
    if not root.is_absolute():
        raise ApiError(BusinessCode.VALIDATION_ERROR, "导入路径必须是绝对路径", {"path": path})
    if not root.exists():
        raise ApiError(BusinessCode.NOT_FOUND, "导入路径不存在", {"path": str(root)})
    if not root.is_dir():
        raise ApiError(BusinessCode.VALIDATION_ERROR, "导入路径不是目录", {"path": str(root)})

    # 防目录穿越：允许根目录未配置时不限制（本地单机默认），
    # 一旦配置，导入路径必须落在其中一个里面。
    allowed = [Path(p).expanduser() for p in get_settings().dicom_import_roots if p.strip()]
    if allowed:
        resolved = root.resolve()
        if not any(resolved == a.resolve() or a.resolve() in resolved.parents for a in allowed):
            raise ApiError(
                BusinessCode.PERMISSION_DENIED,
                "导入路径不在允许的根目录之内",
                {"path": str(root), "allowed": [str(a) for a in allowed]},
            )
    return root


def import_directory(
    db: Session,
    path: str,
    principal: Principal,
    *,
    actor_ip: str | None = None,
) -> ImportOutcome:
    """扫描并导入一个本地目录（光盘 / U 盘 / 移动硬盘挂载点）。"""
    root = _resolve_root(path)
    scan = media.scan_directory(root)

    outcome = ImportOutcome(
        source=str(root),
        mode=scan.mode,
        dicomdir=str(scan.dicomdir) if scan.dicomdir else None,
        file_set_id=scan.file_set_id,
        files_found=scan.file_count,
    )
    if scan.missing:
        outcome.errors.append(f"DICOMDIR 引用了 {len(scan.missing)} 个不存在的文件")

    destination_root = store_dir()
    sop_uids: set[str] = set()

    for entry in scan.entries:
        dataset = media.read_header(entry.path)
        if dataset is None:
            outcome.files_failed += 1
            outcome.errors.append(f"无法解析：{entry.path.name}")
            continue
        try:
            sop_uid = str(dataset.SOPInstanceUID)
            study_uid = str(dataset.StudyInstanceUID)
            series_uid = str(dataset.SeriesInstanceUID)
        except AttributeError:
            outcome.files_failed += 1
            outcome.errors.append(f"缺少 UID，跳过：{entry.path.name}")
            continue
        if not sop_uid or not study_uid or not series_uid:
            outcome.files_failed += 1
            outcome.errors.append(f"UID 为空，跳过：{entry.path.name}")
            continue

        try:
            shutil.copyfile(entry.path, destination_root / f"{sop_uid}.dcm")
        except OSError as exc:
            outcome.files_failed += 1
            outcome.errors.append(f"拷贝失败 {entry.path.name}: {exc}")
            continue
        sop_uids.add(sop_uid)
        outcome.files_imported += 1

    # 索引刷新：整库重扫，新拷贝的文件由此进入 QIDO / WADO 可见范围
    archive.reload()

    outcome.studies, outcome.series, outcome.instances, outcome.study_instance_uids = _count(
        sop_uids
    )

    audit.record(
        db,
        category="object",
        action="import_directory",
        actor_id=principal.user_id,
        actor_ip=actor_ip,
        target_type="media",
        target_uid=outcome.file_set_id or str(root),
        extra={
            "mode": outcome.mode,
            "source": outcome.source,
            "files_found": outcome.files_found,
            "files_imported": outcome.files_imported,
            "files_failed": outcome.files_failed,
            "studies": outcome.studies,
            "series": outcome.series,
            "instances": outcome.instances,
        },
    )
    db.flush()
    return outcome


def _count(sop_uids: set[str]) -> tuple[int, int, int, list[str]]:
    """统计归档里属于本次导入的 study / series / instance 数量。

    以 reload() 之后的归档为准 —— 报告的是真正被索引进去的东西，
    而不是扫描阶段看到的文件数。
    """
    studies: set[str] = set()
    series: set[tuple[str, str]] = set()
    instances = 0
    for study in archive.list_studies():
        study_hit = False
        for series_record in study.series.values():
            series_hit = False
            for instance in series_record.instances:
                if instance.sop_instance_uid in sop_uids:
                    instances += 1
                    series_hit = True
                    study_hit = True
            if series_hit:
                series.add((study.study_instance_uid, series_record.series_instance_uid))
        if study_hit:
            studies.add(study.study_instance_uid)
    return len(studies), len(series), instances, sorted(studies)

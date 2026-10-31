#!/usr/bin/env python3
"""把合成 CT 导入 DICOM 存储并建立 Case，让 MVP 可直接演示。

用法：
    .venv/bin/python scripts/seed_demo.py            # 生成数据 + 导入 + 建 Case
    .venv/bin/python scripts/seed_demo.py --skip-generate
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.core.db import create_all, session_scope  # noqa: E402
from app.dicom.store import archive  # noqa: E402


def generate_data(raw_dir: Path) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from make_synthetic_ct import generate  # noqa: PLC0415

    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    return generate(raw_dir, patient_id="SYN-CT-001", seed=42)


def import_to_store(raw_dir: Path, store_dir: Path) -> int:
    store_dir.mkdir(parents=True, exist_ok=True)
    for f in store_dir.glob("*"):
        if f.is_file():
            f.unlink()
    count = 0
    for src in sorted(raw_dir.glob("*.ima")):
        shutil.copy2(src, store_dir / f"{src.stem}.dcm")
        count += 1
    archive.reload()
    return count


def ensure_case(study, series) -> dict:
    """直接在服务层建 Case 与已确认 SEG，不依赖 HTTP 服务是否在跑。"""
    from app.core.security import Principal, Role  # noqa: PLC0415
    from app.models.case import Case  # noqa: PLC0415
    from app.services import cases as case_svc  # noqa: PLC0415
    from app.services import segments as seg_svc  # noqa: PLC0415

    principal = Principal(user_id="u-attending", roles={Role.ATTENDING})
    with session_scope() as db:
        existing = db.query(Case).filter(Case.study_uids.like(f'%"{study.study_instance_uid}"%')).first()
        case = existing or case_svc.create_case(
            db,
            principal,
            patient_ref=study.patient_id,
            studies=[study.study_instance_uid],
            source="upload",
            modality=series.modality,
            title=f"合成腹部 CT · {study.patient_id}",
        )
        seg = seg_svc.create_manual(
            db,
            case,
            principal,
            study_instance_uid=study.study_instance_uid,
            segment_name="肝脏（合成）",
            seg_content_b64=None,
            series_instance_uid=series.series_instance_uid,
        )
        seg.algorithm = "ai:synthetic-ellipsoid:1.0.0"
        db.flush()
        seg_svc.confirm(db, seg, principal)
        return {"case_id": case.id, "segment_uid": seg.seg_uid, "segment_version": seg.version}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-generate", action="store_true")
    ap.add_argument("--skip-import", action="store_true")
    ap.add_argument("--skip-case", action="store_true")
    args = ap.parse_args()

    settings = get_settings()
    raw_dir = Path("./data/synthetic-ct").resolve()
    store_dir = Path(settings.dicom_store_dir).resolve()

    if not args.skip_generate:
        print("→ 生成合成 CT 序列")
        meta = generate_data(raw_dir)
        print(f"   study={meta['study_instance_uid'][:28]}… instances={meta['instances']}")

    if not args.skip_import:
        print(f"→ 导入到 DICOM 存储 {store_dir}")
        n = import_to_store(raw_dir, store_dir)
        print(f"   已导入 {n} 个实例，索引 {len(archive.list_studies())} 个 Study")

    studies = archive.list_studies()
    if not studies:
        print("!! 存储为空，请先去掉 --skip-generate")
        return 1
    study = studies[0]
    series = next(iter(study.series.values()))

    if args.skip_case:
        return 0

    print("→ 建立 Case 与已确认分割")
    create_all()
    result = ensure_case(study, series)
    print(f"   case_id={result['case_id']}")
    print(f"   segment={result['segment_uid'][:20]}… v{result['segment_version']} (confirmed)")

    print("\n完成。启动服务后打开 http://localhost:5173/")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
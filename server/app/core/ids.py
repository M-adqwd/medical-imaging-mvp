"""UID / UUID 生成。DICOM UID 使用 2.25.<uuid int> 形式，符合 DICOM PS3.5 B.2。"""

from __future__ import annotations

import uuid

ROOT = "1.2.840.100099.4"


def new_uuid() -> str:
    return str(uuid.uuid4())


def new_dicom_uid(prefix: str = ROOT) -> str:
    return f"{prefix}.{uuid.uuid4().int}"[:64]


def new_access_code() -> str:
    """8 字符访问码，4+4 便于口述（PRD FR-07）。"""
    raw = uuid.uuid4().hex[:8].upper()
    return f"{raw[:4]} {raw[4:]}"
"""DICOMweb 适配器（契约 §2.1）。

对外暴露标准 WADO / QIDO / STOW；对内优先对接 Orthanc。
当 DIComweb 地址为空（本地开发、无 PACS）时退化为内置文件存储，
保证业务层在无基础设施时仍可跑通。
"""

from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Any

import httpx
import pydicom

from app.core.config import get_settings

_LOCAL_ROOT = Path("./.dicomstore")


class DicomStore:
    """DICOM 事实源抽象。生产用 Orthanc，本地用文件系统。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    @property
    def mode(self) -> str:
        """orthanc = 对接真实 DICOMweb；local = 退化为内置文件存储。

        本地降级本身是合理的，但如果不暴露出来，排障时无法判断影像到底来自哪儿 ——
        界面上看着正常的图像，可能只是 ./dicomstore 下的文件。
        """
        return "orthanc" if get_settings().dicomweb_base_url else "local"

    # --- 写 ---
    def store_instance(self, ds: pydicom.dataset.Dataset) -> str:
        sop_uid = ds.SOPInstanceUID
        settings = get_settings()
        if settings.dicomweb_base_url:
            return self._stow_remote(ds)
        with self._lock:
            (_LOCAL_ROOT / f"{sop_uid}.dcm").parent.mkdir(parents=True, exist_ok=True)
            buf = io.BytesIO()
            pydicom.dcmwrite(buf, ds, enforce_file_format=True)
            (_LOCAL_ROOT / f"{sop_uid}.dcm").write_bytes(buf.getvalue())
        return sop_uid

    def store_bytes(self, sop_uid: str, data: bytes) -> str:
        settings = get_settings()
        if settings.dicomweb_base_url:
            r = httpx.post(
                f"{settings.dicomweb_base_url}/studies",
                content=data,
                headers={"Content-Type": "application/dicom"},
                verify=settings.dicomweb_verify_tls,
                timeout=30,
            )
            r.raise_for_status()
            return sop_uid
        with self._lock:
            (_LOCAL_ROOT / f"{sop_uid}.dcm").parent.mkdir(parents=True, exist_ok=True)
            (_LOCAL_ROOT / f"{sop_uid}.dcm").write_bytes(data)
        return sop_uid

    # --- 读 ---
    def retrieve_instance(self, sop_uid: str) -> bytes | None:
        settings = get_settings()
        if settings.dicomweb_base_url:
            r = httpx.get(
                f"{settings.dicomweb_base_url}/studies/{sop_uid[:64]}/instances/{sop_uid}",
                verify=settings.dicomweb_verify_tls,
                timeout=30,
            )
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        path = _LOCAL_ROOT / f"{sop_uid}.dcm"
        return path.read_bytes() if path.exists() else None

    def search_studies(self, **params: Any) -> list[dict]:
        settings = get_settings()
        if settings.dicomweb_base_url:
            r = httpx.get(
                f"{settings.dicomweb_base_url}/studies",
                params=params,
                verify=settings.dicomweb_verify_tls,
                timeout=30,
            )
            r.raise_for_status()
            return r.json()
        return []

    # --- 远程 STOW ---
    def _stow_remote(self, ds: pydicom.dataset.Dataset) -> str:
        settings = get_settings()
        buf = io.BytesIO()
        pydicom.dcmwrite(buf, ds, enforce_file_format=True)
        r = httpx.post(
            f"{settings.dicomweb_base_url}/studies",
            content=buf.getvalue(),
            headers={"Content-Type": "application/dicom"},
            verify=settings.dicomweb_verify_tls,
            timeout=30,
        )
        r.raise_for_status()
        return ds.SOPInstanceUID


store = DicomStore()
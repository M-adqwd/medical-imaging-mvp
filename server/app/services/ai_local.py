"""本地真实分割推理（TotalSegmentator）—— AiClient 的第三态 mode=local。

为什么要有它：ADR-002 原决定首期不接模型，`/ai/infer` 只有 live（外部
MONAI Deploy）与 stub（明确自曝的本地桩，任务永远不收敛）两态。项目
owner 于 2026-10-28 推翻首期决策（见 ADR-002 修订记录），本模块给出
**本地真实推理**路径：跑的是真实模型，任务会自己收敛为 DONE/FAILED，
产物通过与外部 AI 服务**完全相同**的回调链路落成 suggested 态 SEG。

诚实性约束（ADR-002「桩必须自曝」的延伸，对 local 同样生效）：

- mode=local 不是 live：对外必须如实标注"本地 TotalSegmentator"，
  不许冒充外部 AI 服务
- 预测为空时 job 照样标 done，但 stats 里写明 0 像素与原因，
  **不伪造分割**——一个"看起来有器官"的空壳比报错更难纠正
- 产物固定 suggested 态 + clinical_use=False：模型输出必须经医师确认

实现要点：worker 在后台线程里跑，**不碰数据库**——体数据走归档
（线程安全），结果走 HTTP 回调到自身 /ai-results（与外部 AI 单一代码
路径），状态迁移、审计、快照全部复用既有逻辑。
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import get_settings
from app.dicom.mask import MaskGeometry
from app.services import segmentation as seg_engine

# TS 未指定 targets 时的默认结构。命名按 TotalSegmentator 官方结构表，
# 不自造别名——输出文件名是 <结构名>.nii.gz，对不上就静默拿不到掩膜。
DEFAULT_TARGETS = ("liver", "spleen", "kidney_left", "kidney_right")

# 模型标识。version 由 CLI 实际输出回填，这里只给兜底值。
MODEL_NAME = "totalsegmentator/multi-structure"


class LocalInferNotAvailable(Exception):
    """要求 local 推理但运行环境不具备（CLI 缺失 / 体数据缺失）。"""


def local_infer_enabled() -> bool:
    return bool(get_settings().ai_local_infer)


def local_infer_available() -> bool:
    """环境里能不能真的跑：CLI 存在才算可用，配置开了但没装不算。"""
    return shutil.which("totalsegmentator") is not None


def _nifti_affine(geometry: MaskGeometry) -> np.ndarray:
    """MaskGeometry → NIfTI 仿射（RAS，mm）。

    NIfTI 体素序是 (i, j, k) = (列, 行, 帧)，与 MaskGeometry 的
    (行, 列, 帧) 命名错位——这里按 NIfTI 的顺序拼列：
      第 0 列 = 列方向 × 列间距，第 1 列 = 行方向 × 行间距，
      第 2 列 = 帧方向 × 层间距，平移列 = origin。
    DICOM 是 LPS，NIfTI 惯例 RAS：前两轴取负完成转换。输入输出用同一
    约定，掩膜体素栅格与归档体数据保持逐体素对齐。
    """
    col, row, nrm = geometry.col_dir, geometry.row_dir, geometry.normal
    # spacing 是 (行间距, 列间距, 层间距)——顺序解包反了就是整体偏一个间距因子
    sp_row, sp_col, sp_slice = geometry.spacing
    affine = np.eye(4, dtype=np.float64)
    affine[:3, 0] = np.array(col) * sp_col
    affine[:3, 1] = np.array(row) * sp_row
    affine[:3, 2] = np.array(nrm) * sp_slice
    affine[:3, 3] = np.array(geometry.origin)
    affine[0, :] *= -1  # LPS → RAS
    affine[1, :] *= -1
    return affine


def _write_input_nifti(volume: seg_engine.Volume, path: Path) -> None:
    import nibabel as nib

    # hu 是 (帧, 行, 列)，NIfTI 要 (列, 行, 帧)
    arr = np.ascontiguousarray(volume.hu.transpose(2, 1, 0))
    img = nib.Nifti1Image(arr, _nifti_affine(volume.geometry))
    nib.save(img, str(path))


def _read_output_mask(path: Path, expect_shape: tuple[int, int, int]) -> np.ndarray | None:
    """读回 TS 输出掩膜并转回 (帧, 行, 列)。形状对不上返回 None（宁缺勿错）。"""
    import nibabel as nib

    img = nib.load(str(path))
    arr = np.asarray(img.dataobj)
    if tuple(arr.shape) != (expect_shape[2], expect_shape[1], expect_shape[0]):
        return None
    return (arr.transpose(2, 1, 0) > 0).astype(np.uint8)


def _post_callback(callback_url: str, payload: dict[str, Any]) -> None:
    import httpx

    r = httpx.post(callback_url, json=payload, timeout=60)
    r.raise_for_status()


def _worker(
    job_id: str,
    study_instance_uid: str,
    series_instance_uid: str | None,
    targets: list[str],
    callback_url: str,
    patient_id: str,
    patient_name: str,
    study_date: str | None,
) -> None:
    """后台线程：体数据 → NIfTI → TotalSegmentator → 逐结构回调。

    任何异常都收敛为 status=failed 回调——任务停在 RUNNING 才是事故。
    """
    stats: dict[str, Any] = {"structures": {}, "empty": [], "skipped": []}
    try:
        if not local_infer_available():
            raise LocalInferNotAvailable("totalsegmentator CLI 不在 PATH 中（server/.venv 已安装?）")

        volume = seg_engine.load_volume(study_instance_uid, series_instance_uid)
        use_targets = [t for t in targets if t] or list(DEFAULT_TARGETS)

        with tempfile.TemporaryDirectory(prefix="miis-ai-") as td:
            work = Path(td)
            in_nii = work / "input.nii.gz"
            out_dir = work / "output"
            _write_input_nifti(volume, in_nii)

            cmd = [
                shutil.which("totalsegmentator") or "totalsegmentator",
                "-i", str(in_nii),
                "-o", str(out_dir),
                "-d", "cpu",
                "--fast",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"totalsegmentator 退出码 {proc.returncode}: {proc.stderr[-800:]}"
                )

            seg_count = 0
            for name in use_targets:
                mask_path = out_dir / f"{name}.nii.gz"
                if not mask_path.exists():
                    stats["skipped"].append(name)
                    continue
                mask = _read_output_mask(mask_path, volume.hu.shape)
                if mask is None:
                    # 形状对不上 = TS 重采样后没有回到输入栅格，强行用会整体错位
                    stats["skipped"].append(name)
                    continue
                voxel_count = int(mask.sum())
                stats["structures"][name] = {"voxel_count": voxel_count}
                if voxel_count == 0:
                    stats["empty"].append(name)
                    continue

                from app.core.ids import new_dicom_uid
                from app.dicom.mask import build_seg_from_mask

                seg_uid = new_dicom_uid()
                seg_bytes = build_seg_from_mask(
                    seg_uid=seg_uid,
                    study_instance_uid=study_instance_uid,
                    series_instance_uid=volume.series_instance_uid,
                    patient_id=patient_id,
                    patient_name=patient_name,
                    segment_name=name[:60],
                    algorithm=f"ai:{MODEL_NAME}",
                    mask=mask,
                    spacing=volume.geometry.spacing,
                    origin=volume.geometry.origin,
                    row_dir=volume.geometry.row_dir,
                    col_dir=volume.geometry.col_dir,
                    referenced_sop_uids=volume.sop_uids,
                    clinical_use=False,
                    producer="totalsegmentator",
                    method="nnunet-total-fast-cpu",
                    study_date=study_date,
                )
                _post_callback(
                    callback_url,
                    {
                        "job_id": job_id,
                        "status": "done",
                        "segment_name": name,
                        "seg_content": base64.b64encode(seg_bytes).decode("ascii"),
                        "stats": {
                            "structure": name,
                            "voxel_count": voxel_count,
                            "series_instance_uid": volume.series_instance_uid,
                        },
                        "seg_uid": seg_uid,
                    },
                )
                seg_count += 1

            stats["seg_created"] = seg_count
            done_payload: dict[str, Any] = {"job_id": job_id, "status": "done", "stats": stats}
            if seg_count == 0:
                # 诚实上报：模型没认出任何结构时，stats 必须说清楚，不许静默装成功
                done_payload["error"] = {
                    "code": "EMPTY_PREDICTION",
                    "message": "全部目标结构预测为 0 像素（合成体模数据模型可能无法识别）",
                }
            _post_callback(callback_url, done_payload)
    except Exception as exc:  # noqa: BLE001 —— worker 必须自己兜住一切
        _post_callback(
            callback_url,
            {
                "job_id": job_id,
                "status": "failed",
                "error": {"code": "LOCAL_INFER_FAILED", "message": str(exc), "trace": traceback.format_exc()[-2000:]},
                "stats": stats,
            },
        )


def submit_local(
    *,
    job_id: str,
    study_instance_uid: str,
    series_instance_uid: str | None,
    targets: list[str],
    callback_url: str,
    patient_id: str,
    patient_name: str,
    study_date: str | None,
) -> None:
    """启动后台推理线程。立即返回；状态迁移靠回调链路推进。"""
    thread = threading.Thread(
        target=_worker,
        kwargs={
            "job_id": job_id,
            "study_instance_uid": study_instance_uid,
            "series_instance_uid": series_instance_uid,
            "targets": list(targets),
            "callback_url": callback_url,
            "patient_id": patient_id,
            "patient_name": patient_name,
            "study_date": study_date,
        },
        name=f"ai-local-{job_id}",
        daemon=True,
    )
    thread.start()

"""本地真实分割推理（mode=local）的契约测试。

覆盖三块：
1. 模式自曝：stub / local / 配置了但环境缺失三种状态，mode 与 notice 必须如实
2. 拒绝语义：开了 AI_LOCAL_INFER 但环境不可用 → 422 拒绝，绝不静默退回桩
3. 多结构回调：一次任务逐结构回调，result_seg_uid 记首个、全集收进 stats
4. worker 兜底：任何异常都必须收敛为 failed 回调，不许停在 RUNNING
"""

from __future__ import annotations

import base64

import numpy as np
import pytest

from app.adapters.ai import ai_client
from app.core.config import get_settings
from app.dicom.mask import build_seg_from_mask
from app.services import ai_local
from app.services import segmentation as seg_engine


@pytest.fixture
def local_infer_env(monkeypatch):
    """开启 AI_LOCAL_INFER 并清设置缓存；测试完恢复。"""
    monkeypatch.setenv("AI_LOCAL_INFER", "true")
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


@pytest.fixture
def cli_missing(monkeypatch):
    monkeypatch.setattr(ai_local.shutil, "which", lambda name: None)


@pytest.fixture
def cli_present(monkeypatch):
    monkeypatch.setattr(ai_local.shutil, "which", lambda name: "C:/fake/totalsegmentator.exe")


# ============ 模式自曝 ============


def test_mode_is_stub_by_default():
    assert ai_client.mode == "stub"
    notice = ai_client.stub_notice
    assert notice is not None and "本地桩" in notice


def test_mode_local_when_cli_present(local_infer_env, cli_present):
    assert ai_client.mode == "local"
    notice = ai_client.stub_notice
    assert notice is not None and "TotalSegmentator" in notice
    assert "suggested" in notice


def test_mode_stub_when_enabled_but_cli_missing(local_infer_env, cli_missing):
    # 配置开了但环境没有 CLI：mode 退回 stub，但 notice 必须说明投递会被拒绝
    assert ai_client.mode == "stub"
    assert "拒绝" in ai_client.stub_notice


# ============ 拒绝语义 ============


def test_infer_rejected_when_local_enabled_but_unavailable(client, attending, idem, study_uid, local_infer_env, cli_missing):
    r = client.post(
        "/api/v1/ai/infer",
        params={"study_instance_uid": study_uid},
        headers={**attending, **idem()},
    )
    assert r.status_code == 422
    assert r.json()["error"]["details"].get("reason") == "local_infer_unavailable"


# ============ NIfTI 仿射 ============


def test_nifti_affine_ras_conversion():
    from app.dicom.mask import MaskGeometry

    geo = MaskGeometry(
        rows=4, cols=6, frames=8,
        spacing=(0.7, 0.9, 1.0),
        origin=(100.0, -50.0, 20.0),
        row_dir=(0.0, 1.0, 0.0),
        col_dir=(1.0, 0.0, 0.0),
    )
    affine = ai_local._nifti_affine(geo)
    # LPS → RAS：前两轴取负。LPS 里列方向是 +x，RAS 里应是 -x
    assert affine[0, 0] == pytest.approx(-0.9)
    assert affine[1, 1] == pytest.approx(-0.7)
    # 帧方向不取负（LPS +z 与 RAS +z 同向——S 轴指头侧）
    assert affine[2, 2] == pytest.approx(1.0)
    assert affine[0, 3] == pytest.approx(-100.0)
    assert affine[1, 3] == pytest.approx(50.0)
    assert affine[2, 3] == pytest.approx(20.0)


def test_read_output_mask_rejects_shape_mismatch(tmp_path):
    import nibabel as nib

    # 造一个形状对不上的输出：期望 (2,4,6) 的 (帧,行,列)，给 (3,4,6)
    arr = np.ones((3, 4, 6), dtype=np.uint8)
    nib.save(nib.Nifti1Image(arr, np.eye(4)), str(tmp_path / "liver.nii.gz"))
    assert ai_local._read_output_mask(tmp_path / "liver.nii.gz", (2, 4, 6)) is None

    nib.save(nib.Nifti1Image(np.ones((6, 4, 2), dtype=np.uint8), np.eye(4)), str(tmp_path / "ok.nii.gz"))
    mask = ai_local._read_output_mask(tmp_path / "ok.nii.gz", (2, 4, 6))
    assert mask is not None and mask.shape == (2, 4, 6)


# ============ 多结构回调累积 ============


def _seg_content_b64(study_uid: str, name: str) -> str:
    mask = np.zeros((2, 4, 4), dtype=np.uint8)
    mask[0, 1:3, 1:3] = 1
    seg = build_seg_from_mask(
        seg_uid="2.25.1111111111111111111111111111111123456",
        study_instance_uid=study_uid,
        series_instance_uid=None,
        patient_id="MRN:P001234",
        patient_name="T",
        segment_name=name,
        algorithm="ai:test",
        mask=mask,
        spacing=(1.0, 1.0, 1.0),
        origin=(0.0, 0.0, 0.0),
    )
    return base64.b64encode(seg).decode("ascii")


def test_multi_structure_callbacks_accumulate(client, attending, idem, case, study_uid):
    job_id = "job-multi-1"
    r = client.post(
        "/api/v1/ai/infer",
        params={"study_instance_uid": study_uid},
        headers={**attending, **idem(), "X-Job-Id": job_id},
    )
    assert r.status_code == 202

    for name in ("liver", "spleen"):
        cb = client.post(
            "/api/v1/ai-results",
            json={
                "job_id": job_id,
                "status": "done",
                "segment_name": name,
                "seg_content": _seg_content_b64(study_uid, name),
                "stats": {"structure": name, "voxel_count": 4},
            },
        )
        assert cb.status_code == 200, cb.text

    # 收尾回调（无 seg_content）：不许覆盖掉已收集的产物列表
    cb = client.post(
        "/api/v1/ai-results",
        json={"job_id": job_id, "status": "done", "stats": {"seg_created": 2}},
    )
    assert cb.status_code == 200

    job = client.get(f"/api/v1/ai/jobs/{job_id}", headers=attending).json()
    assert job["status"] == "done"
    uids = job["stats"]["result_seg_uids"]
    assert len(uids) == 2
    assert job["result_seg_uid"] == uids[0]  # 首个结构留在主字段
    assert job["stats"]["seg_created"] == 2


# ============ worker 兜底 ============


def test_worker_failure_converges_to_failed(monkeypatch, tmp_path):
    payloads: list[dict] = []

    def fake_post(url, json=None, timeout=None):  # noqa: ANN001
        payloads.append(json)

        class R:
            def raise_for_status(self):  # noqa: ANN202
                return None

        return R()

    monkeypatch.setattr("httpx.post", fake_post)

    def boom(*a, **k):  # noqa: ANN002
        raise seg_engine.SeriesNotFound("归档中没有 study")

    monkeypatch.setattr(seg_engine, "load_volume", boom)
    ai_local._worker(
        job_id="job-fail-1",
        study_instance_uid="1.2.3",
        series_instance_uid=None,
        targets=["liver"],
        callback_url="http://127.0.0.1:1/api/v1/ai-results",
        patient_id="P",
        patient_name="T",
        study_date=None,
    )
    assert len(payloads) == 1
    assert payloads[0]["status"] == "failed"
    assert payloads[0]["error"]["code"] == "LOCAL_INFER_FAILED"


def test_worker_reports_empty_prediction_honestly(monkeypatch, tmp_path):
    """模型跑完但一个结构都没认出来：job done + stats 写明，不伪造分割。"""
    import nibabel as nib

    payloads: list[dict] = []

    def fake_post(url, json=None, timeout=None):  # noqa: ANN001
        payloads.append(json)

        class R:
            def raise_for_status(self):  # noqa: ANN202
                return None

        return R()

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr(ai_local.shutil, "which", lambda name: "C:/fake/ts.exe")

    hu = np.zeros((2, 4, 6), dtype=np.float32)
    from app.dicom.mask import MaskGeometry

    geo = MaskGeometry(
        rows=4, cols=6, frames=2,
        spacing=(1.0, 1.0, 1.0),
        origin=(0.0, 0.0, 0.0),
        row_dir=(0.0, 1.0, 0.0),
        col_dir=(1.0, 0.0, 0.0),
    )
    volume = seg_engine.Volume(hu=hu, geometry=geo, sop_uids=["1.2.840.x.1", "1.2.840.x.2"], series_instance_uid="1.2.840.s", modality="CT")
    monkeypatch.setattr(seg_engine, "load_volume", lambda *a, **k: volume)

    written: dict[str, np.ndarray] = {}

    def fake_write(volume_, path):  # noqa: ANN001
        # 模拟 TS：只写一个全零的 liver 输出（认不出任何结构）
        out_dir = path.parent / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        arr = np.zeros((6, 4, 2), dtype=np.uint8)  # (列,行,帧)
        nib.save(nib.Nifti1Image(arr, np.eye(4)), str(out_dir / "liver.nii.gz"))
        written["input"] = path

    monkeypatch.setattr(ai_local, "_write_input_nifti", fake_write)

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(ai_local.subprocess, "run", lambda *a, **k: FakeProc())

    ai_local._worker(
        job_id="job-empty-1",
        study_instance_uid="1.2.3",
        series_instance_uid=None,
        targets=["liver"],
        callback_url="http://127.0.0.1:1/api/v1/ai-results",
        patient_id="P",
        patient_name="T",
        study_date=None,
    )
    # 逐结构回调为零（全空不产 SEG），最终 done 回调带 EMPTY_PREDICTION 标注
    assert len(payloads) == 1
    final = payloads[0]
    assert final["status"] == "done"
    assert final["stats"]["empty"] == ["liver"]
    assert final["stats"]["seg_created"] == 0
    assert final["error"]["code"] == "EMPTY_PREDICTION"

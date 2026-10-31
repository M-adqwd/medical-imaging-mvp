"""AI 推理服务适配器（契约 §2.4）。

对系统暴露：submit(job) 与 handle_callback(payload)。
生产对接 MONAI Deploy（ai_base_url）；未配置时使用确定性桩实现，
让 FR-04 的全链路在本地可验证。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings


@dataclass
class InferRequest:
    job_id: str
    model: str
    model_version: str
    study_instance_uid: str
    targets: list[str] = field(default_factory=list)
    callback: str = ""


class AiClient:
    def __init__(self) -> None:
        self._settings = get_settings()

    @property
    def enabled(self) -> bool:
        return bool(self._settings.ai_base_url)

    @property
    def mode(self) -> str:
        """live = 外部 AI 服务；local = 本地 TotalSegmentator；stub = 本地桩。

        stub 下任务永远不会自己变成 done，必须显式告知调用方，
        否则「投递成功」会被读成「分割会自己出现」。
        local 下任务会真实收敛，但产物是本地模型输出，同样必须如实标注。
        """
        if self.enabled:
            return "live"
        from app.services.ai_local import local_infer_available, local_infer_enabled

        if local_infer_enabled():
            return "local" if local_infer_available() else "stub"
        return "stub"

    @property
    def stub_notice(self) -> str | None:
        if self.enabled:
            return None
        from app.services.ai_local import local_infer_available, local_infer_enabled

        if local_infer_enabled():
            if local_infer_available():
                return (
                    "本地 TotalSegmentator 推理（非外部 AI 服务）：任务会自动完成，"
                    "产物为模型自动分割，固定 suggested 态，须经医师确认"
                )
            return (
                "AI_LOCAL_INFER=1 但运行环境缺少 totalsegmentator CLI：投递会被拒绝，"
                "不会退回本地桩"
            )
        return (
            "AI 服务未配置（AI_BASE_URL 为空）且未开启本地推理（AI_LOCAL_INFER），"
            "当前为本地桩：任务不会自动完成，需手动 POST /api/v1/ai-results 回传结果"
        )

    def can_accept_local(self) -> bool:
        """local 模式是否真的能接任务：开了配置且 CLI 存在。"""
        from app.services.ai_local import local_infer_available, local_infer_enabled

        return not self.enabled and local_infer_enabled() and local_infer_available()

    def submit(self, req: InferRequest) -> dict[str, Any]:
        """投递推理任务。返回 {job_id, queue_pos, estimated_sec}。"""
        if self.enabled:
            r = httpx.post(
                f"{self._settings.ai_base_url}/ai/v1/infer",
                json={
                    "model": req.model,
                    "model_version": req.model_version,
                    "study_instance_uid": req.study_instance_uid,
                    "targets": req.targets,
                    "callback": req.callback,
                    "job_id": req.job_id,
                },
                timeout=self._settings.ai_timeout_sec,
            )
            r.raise_for_status()
            return r.json()
        # 桩：本地开发直接给出队列位置，但必须标注自己是桩
        return {
            "job_id": req.job_id,
            "queue_pos": 0,
            "estimated_sec": 30,
            "mode": self.mode,
            "notice": self.stub_notice,
        }

    def status(self, job_id: str) -> dict[str, Any]:
        if self.enabled:
            r = httpx.get(
                f"{self._settings.ai_base_url}/ai/v1/infer/{job_id}",
                timeout=self._settings.ai_timeout_sec,
            )
            r.raise_for_status()
            return r.json()
        return {
            "job_id": job_id,
            "status": "queued",
            "mode": self.mode,
            "notice": self.stub_notice,
        }


ai_client = AiClient()
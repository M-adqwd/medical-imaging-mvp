"""ModelForge（训练平台）适配器。

## 定位

MIIS 原来的 AI 适配器只做一件事：把 DICOM 序列投给分割服务，产出 SEG。
但「眼底分级」「心电图判读」这类任务产出的是**结论**，不是掩膜：
它们的输入是 JPG 或一段波形，输出是「几级 / 有哪些诊断码 + 概率」。

所以这里单独接一条线，而不是硬塞进 `InferRequest`：
- 输入：图像字节 / 波形行（不是 study_instance_uid）
- 输出：结构化结论 + 可直接写入报告的 findings / impression

## 契约

- `catalog()` → 训练平台上有哪些模型、吃什么输入、指标如何
- `classify_image(model, content, filename)` → 统一信封
- `classify_rows(model, rows)` → 统一信封（心电/表格）
- 任何配置缺失都显式报错，不返回假结果
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import validation_error


class ModelForgeError(RuntimeError):
    """训练平台不可用/返回异常。调用方应把它当作失败，而不是「没有结论」。"""


class ModelForgeClient:
    def __init__(self) -> None:
        self._settings = get_settings()

    @property
    def enabled(self) -> bool:
        return bool(self._settings.modelforge_base_url)

    @property
    def base(self) -> str:
        return self._settings.modelforge_base_url.rstrip("/")

    def _login(self) -> str:
        """用账号密码换一张票。令牌会过期，写死在配置里迟早失效。"""
        acc = (self._settings.modelforge_account or "").strip()
        pwd = (self._settings.modelforge_password or "").strip()
        if not (acc and pwd):
            return ""
        try:
            r = httpx.post(f"{self.base}/api/auth/login",
                           json={"account": acc, "password": pwd}, timeout=30)
            r.raise_for_status()
            tok = str(r.json().get("token") or "")
            self._cached_token = tok
            return tok
        except httpx.HTTPError as e:
            raise ModelForgeError(f"训练平台登录失败：{e}") from e

    def _headers(self, refresh: bool = False) -> dict[str, str]:
        tok = "" if refresh else getattr(self, "_cached_token", "")
        if not tok:
            tok = (self._settings.modelforge_token or "").strip() or self._login()
        return {"Authorization": f"Bearer {tok}"} if tok else {}

    def _require(self) -> None:
        if not self.enabled:
            raise validation_error(
                "未配置训练平台网关（MODELFORGE_BASE_URL），无法调用分类模型",
                reason="modelforge_not_configured",
            )

    def catalog(self) -> dict[str, Any]:
        self._require()
        try:
            r = httpx.get(f"{self.base}/api/gateway/catalog",
                          headers=self._headers(),
                          timeout=self._settings.modelforge_timeout_sec)
            if r.status_code == 401:
                r = httpx.get(f"{self.base}/api/gateway/catalog",
                              headers=self._headers(refresh=True),
                              timeout=self._settings.modelforge_timeout_sec)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            raise ModelForgeError(f"读取模型目录失败：{e}") from e

    def classify_image(self, model: str, content: bytes, filename: str = "input.png") -> dict[str, Any]:
        self._require()
        try:
            r = httpx.post(
                f"{self.base}/api/gateway/infer",
                params={"model": model},
                headers=self._headers(),
                files={"file": (filename, content, "application/octet-stream")},
                timeout=self._settings.modelforge_timeout_sec,
            )
            if r.status_code == 401:
                r = httpx.post(
                    f"{self.base}/api/gateway/infer",
                    params={"model": model},
                    headers=self._headers(refresh=True),
                    files={"file": (filename, content, "application/octet-stream")},
                    timeout=self._settings.modelforge_timeout_sec,
                )
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                detail = e.response.json().get("detail", "")
            except Exception:  # noqa: BLE001
                detail = e.response.text[:200]
            raise ModelForgeError(f"模型推理失败（{e.response.status_code}）：{detail}") from e
        except httpx.HTTPError as e:
            raise ModelForgeError(f"模型推理请求失败：{e}") from e

    def classify_rows(self, model: str, rows: list[dict]) -> dict[str, Any]:
        self._require()
        try:
            r = httpx.post(
                f"{self.base}/api/gateway/infer",
                params={"model": model},
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"rows": rows},
                timeout=self._settings.modelforge_timeout_sec,
            )
            if r.status_code == 401:
                r = httpx.post(
                    f"{self.base}/api/gateway/infer",
                    params={"model": model},
                    headers={**self._headers(refresh=True), "Content-Type": "application/json"},
                    json={"rows": rows},
                    timeout=self._settings.modelforge_timeout_sec,
                )
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            raise ModelForgeError(
                f"模型推理失败（{e.response.status_code}）：{e.response.text[:200]}"
            ) from e
        except httpx.HTTPError as e:
            raise ModelForgeError(f"模型推理请求失败：{e}") from e


modelforge_client = ModelForgeClient()

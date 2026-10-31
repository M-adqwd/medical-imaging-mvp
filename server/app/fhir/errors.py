"""FHIR 错误 → OperationOutcome。

为什么不复用契约 §4 的错误体：FHIR 客户端（HAPI、Firely、院方集成引擎）
按 `resourceType == "OperationOutcome"` 判定错误，拿到 `{"error": {...}}`
会当成解析失败而不是业务错误，重试策略也会走错分支。
"""

from __future__ import annotations

from typing import Any

# FHIR IssueType（R4 值集 issue-type）与本项目业务码的对应
ISSUE_BY_STATUS: dict[int, str] = {
    400: "invalid",
    401: "login",
    403: "forbidden",
    404: "not-found",
    409: "conflict",
    410: "deleted",
    422: "invalid",
    429: "throttled",
    500: "exception",
    501: "not-supported",
    503: "transient",
}


class FhirError(Exception):
    """带 FHIR 语义的异常。由 main.py 的 handler 渲染成 OperationOutcome。"""

    def __init__(
        self,
        status: int,
        diagnostics: str,
        *,
        code: str | None = None,
        expression: list[str] | None = None,
    ) -> None:
        super().__init__(diagnostics)
        self.status = status
        self.diagnostics = diagnostics
        self.code = code or ISSUE_BY_STATUS.get(status, "processing")
        self.expression = expression or []

    def to_outcome(self) -> dict[str, Any]:
        issue: dict[str, Any] = {
            "severity": "error",
            "code": self.code,
            "diagnostics": self.diagnostics,
        }
        if self.expression:
            issue["expression"] = self.expression
        return {"resourceType": "OperationOutcome", "issue": [issue]}


def outcome_from_api_status(status: int, message: str, path: str | None = None) -> dict[str, Any]:
    """把契约错误（ApiError / HTTPException）翻译成 OperationOutcome。"""
    issue: dict[str, Any] = {
        "severity": "error",
        "code": ISSUE_BY_STATUS.get(status, "processing"),
        "diagnostics": message,
    }
    if path:
        issue["expression"] = [path]
    return {"resourceType": "OperationOutcome", "issue": [issue]}


def not_found(resource_type: str, resource_id: str) -> FhirError:
    return FhirError(
        404,
        f"{resource_type}/{resource_id} 不存在，或当前用户无权访问",
        expression=[f"{resource_type}/{resource_id}"],
    )

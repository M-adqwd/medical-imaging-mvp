"""契约 §4：统一错误体与业务码。

HTTP 状态码仅作为粗粒度分层，客户端应优先解析业务码。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse


class BusinessCode:
    BAD_REQUEST = "BAD_REQUEST"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NOT_FOUND = "NOT_FOUND"
    STATE_CONFLICT = "STATE_CONFLICT"
    DUPLICATE = "DUPLICATE"
    GONE = "GONE"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    LOCKED = "LOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    NOT_REVIEWABLE = "NOT_REVIEWABLE"
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    IDEMPOTENCY_KEY_CONFLICT = "IDEMPOTENCY_KEY_CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    SERVICE_OVERLOADED = "SERVICE_OVERLOADED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


HTTP_FOR_CODE: dict[str, int] = {
    BusinessCode.BAD_REQUEST: 400,
    BusinessCode.UNAUTHENTICATED: 401,
    BusinessCode.PERMISSION_DENIED: 403,
    BusinessCode.NOT_FOUND: 404,
    BusinessCode.STATE_CONFLICT: 409,
    BusinessCode.DUPLICATE: 409,
    BusinessCode.GONE: 410,
    BusinessCode.PRECONDITION_FAILED: 412,
    BusinessCode.VALIDATION_ERROR: 422,
    BusinessCode.LOCKED: 423,
    BusinessCode.RATE_LIMITED: 429,
    BusinessCode.NOT_REVIEWABLE: 409,
    BusinessCode.IDEMPOTENCY_KEY_REQUIRED: 400,
    BusinessCode.IDEMPOTENCY_KEY_CONFLICT: 409,
    BusinessCode.INTERNAL_ERROR: 500,
    BusinessCode.UPSTREAM_UNAVAILABLE: 502,
    BusinessCode.SERVICE_OVERLOADED: 503,
    BusinessCode.NOT_IMPLEMENTED: 501,
}


class ApiError(HTTPException):
    """业务错误。响应体形如契约 §4 定义。"""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=HTTP_FOR_CODE.get(code, 400), detail=message, headers=headers)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_response(self, request_id: str) -> JSONResponse:
        body = {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "request_id": request_id,
            }
        }
        return JSONResponse(status_code=self.status_code, content=body, headers=self.headers)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


# --- 便捷构造 ---

def not_found(kind: str, ident: str) -> ApiError:
    return ApiError(BusinessCode.NOT_FOUND, f"{kind} 不存在：{ident}", {"kind": kind, "id": ident})


def state_conflict(message: str, **details: Any) -> ApiError:
    return ApiError(BusinessCode.STATE_CONFLICT, message, details)


def validation_error(message: str, **details: Any) -> ApiError:
    return ApiError(BusinessCode.VALIDATION_ERROR, message, details)


def permission_denied(message: str, **details: Any) -> ApiError:
    return ApiError(BusinessCode.PERMISSION_DENIED, message, details)


def gone(message: str, **details: Any) -> ApiError:
    return ApiError(BusinessCode.GONE, message, details)


def locked(message: str, **details: Any) -> ApiError:
    return ApiError(BusinessCode.LOCKED, message, details)
"""路由公共依赖：DB 会话、当前用户、幂等包装。

幂等包装把「首次执行 → 记录响应 → 重入回放」收敛到一处，
避免每个端点重复实现（契约 §5.4）。
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from fastapi import Depends, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import idem
from app.core.db import get_db
from app.core.errors import ApiError, BusinessCode
from app.core.security import Principal, get_principal


def db_session(db: Session = Depends(get_db)) -> Session:
    return db


def current_user(principal: Principal = Depends(get_principal)) -> Principal:
    return principal


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def commit_or_409(db: Session) -> None:
    """统一提交点：唯一约束冲突映射为 409 DUPLICATE（契约 §4）。"""
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(
            BusinessCode.DUPLICATE, "数据唯一性冲突，请检查是否重复提交", {"detail": str(exc.orig)}
        ) from exc


def idempotent(scope: str, status_code: int = 200) -> Callable[[Callable], Callable]:
    """为写端点提供幂等语义。

    首次调用：执行业务逻辑 → 提交 → 记录响应。
    重入（同一 scope + 同一 Idempotency-Key + 相同 payload）：直接回放首次响应。
    同 key 不同 payload：409 IDEMPOTENCY_KEY_CONFLICT。
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            request: Request = kwargs["request"]
            db: Session = kwargs["db"]
            payload = kwargs.get("payload")
            body_hash = idem.payload_hash(
                payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
            )
            key = idem.require_idempotency_key(request.headers.get("Idempotency-Key"))

            record, replayed = idem.lookup(db, scope, key, body_hash)
            if replayed and record is not None:
                # 二进制响应重放：还原首次的字节与 media_type，
                # 否则重放会退化成 JSON 或直接 409（状态机已推进）。
                snap = idem.binary_snapshot(record)
                if snap is not None:
                    raw, media_type, filename = snap
                    headers = {}
                    if filename:
                        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
                    return Response(
                        content=raw,
                        status_code=record.status_code,
                        media_type=media_type or "application/octet-stream",
                        headers=headers,
                    )
                return JSONResponse(status_code=record.status_code, content=record.response_body)

            result = func(*args, **kwargs)
            commit_or_409(db)

            if isinstance(result, JSONResponse):
                return result

            # 二进制响应（DICOM SR / PDF）必须原样透传。
            # 走 jsonable_encoder 会把 bytes 编码成乱码字符串，
            # 并把 Content-Type 改成 application/json —— 客户端拿到的是
            # 一个 JSON 包装壳而不是真正的 SR/PDF。
            if isinstance(result, Response):
                filename = None
                disposition = result.headers.get("content-disposition")
                if disposition and "filename=" in disposition:
                    filename = disposition.split("filename=", 1)[1].strip().strip('"')
                idem.remember(
                    db,
                    scope,
                    key,
                    body_hash,
                    status_code=result.status_code,
                    response={"__binary__": True},
                    binary=result.body,
                    media_type=result.media_type,
                    filename=filename,
                )
                commit_or_409(db)
                return result

            body = jsonable_encoder(result)
            idem.remember(db, scope, key, body_hash, status_code=status_code, response=body)
            commit_or_409(db)
            return JSONResponse(status_code=status_code, content=body)

        return wrapper

    return decorator
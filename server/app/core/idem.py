"""契约 §0.2 / §5.4：幂等写操作。

同一端点 + 同一 Idempotency-Key + 同一 payload hash，在 TTL 窗口内重入
直接返回首次响应，不重复执行副作用。
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ApiError, BusinessCode
from app.models.idempotency import IdempotencyRecord


def payload_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def require_idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str:
    """所有 POST / PUT 必须携带 Idempotency-Key（契约 §3 前言）。"""
    if not idempotency_key or not idempotency_key.strip():
        raise ApiError(
            BusinessCode.IDEMPOTENCY_KEY_REQUIRED,
            "写操作必须携带 Idempotency-Key 请求头",
            {"header": "Idempotency-Key"},
        )
    return idempotency_key.strip()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """SQLite 等后端可能回传 naive datetime，统一补齐时区后再比较。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _expired_at() -> datetime:
    ttl = get_settings().idempotency_ttl_days
    return _now() + timedelta(days=ttl)


def lookup(
    db: Session, scope: str, key: str, body_hash: str
) -> tuple[IdempotencyRecord | None, bool]:
    """返回 (记录, 是否命中重放)。命中但 payload 不同视为冲突。"""
    record = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.key == key,
        )
    )
    if record is None:
        return None, False
    if _as_utc(record.expires_at) <= _now():
        db.delete(record)
        db.flush()
        return None, False
    if record.payload_hash != body_hash:
        raise ApiError(
            BusinessCode.IDEMPOTENCY_KEY_CONFLICT,
            "同一 Idempotency-Key 携带了不同请求体",
            {"scope": scope, "key": key},
        )
    return record, True


def remember(
    db: Session,
    scope: str,
    key: str,
    body_hash: str,
    *,
    status_code: int,
    response: Any,
    binary: bytes | None = None,
    media_type: str | None = None,
    filename: str | None = None,
) -> None:
    db.add(
        IdempotencyRecord(
            scope=scope,
            key=key,
            payload_hash=body_hash,
            status_code=status_code,
            response_body=response,
            response_binary_b64=(
                base64.b64encode(binary).decode("ascii") if binary is not None else None
            ),
            response_media_type=media_type,
            response_filename=filename,
            expires_at=_expired_at(),
        )
    )


def binary_snapshot(record: IdempotencyRecord) -> tuple[bytes, str | None, str | None] | None:
    """若该幂等记录存的是二进制响应，返回 (字节, media_type, filename)，否则 None。"""
    if not record.response_binary_b64:
        return None
    try:
        raw = base64.b64decode(record.response_binary_b64)
    except (ValueError, TypeError):
        return None
    return raw, record.response_media_type, record.response_filename
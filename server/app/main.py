"""应用入口。统一错误体、request_id、CORS、启动建表。"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.dicomweb import router as dicomweb_router
from app.api.v1.routers import ai, cases, fhir, imports, measurements, plans, reports, segments, shares
from app.core.config import get_settings
from app.core.db import init_db
from app.core.errors import ApiError, BusinessCode, new_request_id
from app.fhir.errors import FhirError, outcome_from_api_status
from app.adapters.ai import ai_client
from app.adapters.dicomweb import store
from app.dicom.store import archive

logger = logging.getLogger("miis")
settings = get_settings()

REQUEST_ID_HEADER = "X-Request-Id"
FHIR_MEDIA = "application/fhir+json; fhirVersion=4.0.1"


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    init_db()
    archive.ensure_loaded()
    studies = archive.list_studies()
    logger.info(
        "miis server started | db=%s | dicom_store=%s | studies=%d instances=%d",
        settings.database_url.split("@")[-1],
        settings.dicom_store_dir,
        len(studies),
        sum(len(s.instances) for st in studies for s in st.series.values()),
    )
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="接口与契约规范 v0.9 的参考实现。DICOM 为唯一事实源。",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[REQUEST_ID_HEADER, "Idempotency-Key"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):  # noqa: ANN001, ANN201
    request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
    request.state.request_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    logger.info(
        "%s %s -> %s (%.1fms) rid=%s",
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - started) * 1000,
        request_id,
    )
    return response


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    # FHIR 客户端按 resourceType == OperationOutcome 判错，拿到契约 §4 的错误体会当解析失败。
    # 所以 /fhir 下的错误换一种壳，业务码与 HTTP 状态保持不变。
    if request.url.path.startswith("/fhir"):
        return JSONResponse(
            status_code=exc.status_code,
            content=outcome_from_api_status(exc.status_code, exc.message, request.url.path),
            media_type=FHIR_MEDIA,
        )
    return exc.to_response(getattr(request.state, "request_id", new_request_id()))


@app.exception_handler(FhirError)
async def fhir_error_handler(request: Request, exc: FhirError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.to_outcome(), media_type=FHIR_MEDIA)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    rid = getattr(request.state, "request_id", new_request_id())
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": BusinessCode.VALIDATION_ERROR,
                "message": "请求体字段校验失败",
                "details": {"errors": _clean_errors(exc.errors())},
                "request_id": rid,
            }
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    rid = getattr(request.state, "request_id", new_request_id())
    code = {
        401: BusinessCode.UNAUTHENTICATED,
        403: BusinessCode.PERMISSION_DENIED,
        404: BusinessCode.NOT_FOUND,
        405: BusinessCode.BAD_REQUEST,
    }.get(exc.status_code, BusinessCode.INTERNAL_ERROR)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": code,
                "message": str(exc.detail),
                "details": {},
                "request_id": rid,
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:  # noqa: ANN001
    rid = getattr(request.state, "request_id", new_request_id())
    logger.exception("unhandled error rid=%s", rid)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": BusinessCode.INTERNAL_ERROR,
                "message": "服务端异常",
                "details": {},
                "request_id": rid,
            }
        },
    )


def _clean_errors(errors: list[dict]) -> list[dict]:
    out = []
    for e in errors:
        item = {k: v for k, v in e.items() if k in {"loc", "msg", "type"}}
        item["loc"] = [str(x) for x in item.get("loc", [])]
        out.append(item)
    return out


app.include_router(dicomweb_router)
# FHIR 门面挂在 /fhir 而不是 /api/v1：FHIR 客户端按资源根路径发现能力（/fhir/metadata），
# 套一层 /api/v1 会让 CapabilityStatement 里的相对引用全部对不上。
app.include_router(fhir.router, prefix="/fhir")


for r in (
    cases.router,
    measurements.router,
    segments.router,
    plans.router,
    reports.router,
    shares.router,
    imports.router,
    ai.router,
):
    app.include_router(r, prefix=settings.api_prefix)


@app.get("/healthz", tags=["ops"])
def healthz() -> dict:
    # dicomweb_mode / ai_mode 让"真实对接"与"本地降级"在排障时一眼可辨，
    # 否则界面上看着正常的影像，可能只是本地文件存储里的东西。
    return {
        "status": "ok",
        "version": "0.1.0",
        "dicomweb_mode": store.mode,
        "ai_mode": ai_client.mode,
        "request_id": str(uuid.uuid4())[:8],
    }


@app.get(f"{settings.api_prefix}/contract", tags=["ops"])
def contract() -> dict:
    """契约自描述：便于前端与联调方对齐版本。"""
    return {
        "api_version": "v1",
        "contract_version": "0.9",
        "idempotency_header": "Idempotency-Key",
        "auth_headers": ["X-User-Id", "X-User-Roles"],
        "error_envelope": {
            "error": {"code": "…", "message": "…", "details": {}, "request_id": "…"}
        },
        "actions_use_colon_suffix": True,
    }
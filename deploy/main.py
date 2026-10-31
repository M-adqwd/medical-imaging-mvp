"""单端口部署入口：鉴权门 + 静态站点 + 契约 API。

部署环境只暴露一个 HTTP 端口，因此把三件事合到同一个 ASGI 应用：

  1. 登录门禁（Cookie 会话）——没有它，读到的是真实病人影像数据，不能裸奔
  2. `dist/` 静态资源与 SPA 回退（前端是 history 路由，深链接要回 index.html）
  3. 复用 `app.main:app` 的全部业务路由（/api/v1、/dicomweb、/fhir）

鉴权参数走环境变量，缺省值只用于本地自测；线上以 .env 为准。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    """把 .env 灌进 os.environ，并给出与 CWD 无关的默认路径。

    必须在 import app.main 之前完成：业务配置在模块导入时就被 get_settings()
    固化，而 pydantic 的 env_file 是按当前工作目录找 .env 的，服务管理器拉起进程的
    CWD 不一定就是本目录。
    """
    try:
        from dotenv import dotenv_values
    except ImportError:  # pydantic-settings 会带上它，缺了也不阻塞启动
        dotenv_values = None  # type: ignore[assignment]

    env_file = BASE_DIR / ".env"
    values = {}
    if dotenv_values and env_file.is_file():
        values = dict(dotenv_values(env_file))
    for key, value in values.items():
        if value is not None and key not in os.environ:
            os.environ[key] = value

    # 默认值一律用绝对路径：进程无论从哪儿启动都指向随包发布的数据
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{(BASE_DIR / 'miis.db').as_posix()}")
    os.environ.setdefault("DICOM_STORE_DIR", str(BASE_DIR / "data" / "dicom"))

    # .env 里写的是相对路径，绑到包目录而不是 CWD —— 否则换个目录启动就找不到
    # 库文件和 DICOM 归档，表现为「页面能开、病例列表为空」。
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("sqlite:///./"):
        os.environ["DATABASE_URL"] = "sqlite:///" + str(BASE_DIR / db_url[len("sqlite:///./") :])
    store = os.environ.get("DICOM_STORE_DIR", "")
    if store and not os.path.isabs(store):
        os.environ["DICOM_STORE_DIR"] = str((BASE_DIR / store).resolve())


_load_dotenv()

from fastapi import Form, Request  # noqa: E402
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
from fastapi.responses import (  # noqa: E402
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)

from app.main import app  # noqa: E402  业务路由全部挂在这个实例上
from login_page import render_login  # noqa: E402

# WADO 实例是未压缩的 int16 像素，公网带宽小时整序列下载会非常慢；
# 浏览器对 gzip 透明解压，CT 像素压缩率实测约 2.5 倍。
app.add_middleware(GZipMiddleware, minimum_size=1024)

DIST = BASE_DIR / "dist"
INDEX = DIST / "index.html"

WEB_USER = os.environ.get("MIIS_WEB_USER", "admin")
# 口令只从环境变量取。没配就本次启动随机生成一个并打印到控制台，
# 绝不留一个写死的默认口令——那种东西一旦上线就等于没有登录门。
WEB_PASS = os.environ.get("MIIS_WEB_PASS") or ""
if not WEB_PASS:
    WEB_PASS = secrets.token_urlsafe(9)
    print(f"[miis] 未设置 MIIS_WEB_PASS，本次启动临时口令：{WEB_PASS}")
COOKIE_SECRET = os.environ.get("MIIS_WEB_SECRET") or secrets.token_hex(24)
COOKIE_NAME = "miis_sid"
SESSION_TTL = 7 * 24 * 3600  # 7 天，和共享链接默认有效期一致

# 不需要登录就能访问的路径：登录页本身、健康探针、favicon。
# CORS 预检必须放行，否则浏览器连 401 都拿不到，只会报「网络错误」。
PUBLIC_PATHS = {"/login", "/healthz", "/favicon.ico"}
API_PREFIXES = ("/api/", "/dicomweb", "/fhir")

MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".wasm": "application/wasm",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".dcm": "application/dicom",
}


# ---------- 会话签名 ----------


def _sign(payload: str) -> str:
    digest = hmac.new(COOKIE_SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _issue(user: str) -> str:
    exp = int(time.time()) + SESSION_TTL
    payload = f"{user}.{exp}"
    return f"{payload}.{_sign(payload)}"


def _verify(token: str | None) -> str | None:
    if not token:
        return None
    try:
        user, exp, sig = token.rsplit(".", 2)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(f"{user}.{exp}")):
        return None
    if not exp.isdigit() or int(exp) < time.time():
        return None
    return user


# ---------- 登录限速：口令泄露比暴力破解更现实，但也不该敞开 ----------

_FAILS: dict[str, deque[float]] = defaultdict(deque)
_MAX_FAILS = 8
_WINDOW = 300  # 5 分钟内 8 次失败即锁定


def _too_many(ip: str) -> bool:
    now = time.time()
    q = _FAILS[ip]
    while q and now - q[0] > _WINDOW:
        q.popleft()
    return len(q) >= _MAX_FAILS


def _note_fail(ip: str) -> None:
    _FAILS[ip].append(time.time())


def _safe_next(raw: str | None) -> str:
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/"
    return raw


# ---------- 登录页 ----------


def _login_html(next_path: str, error: str = "") -> str:
    """登录页渲染交给 login_page 模块：版式代码需要大量未被转义的花括号。"""
    return render_login(next_path, error)


# ---------- 路由 ----------


@app.get("/login")
def login_page(request: Request, next: str = "/"):  # noqa: A002
    if _verify(request.cookies.get(COOKIE_NAME)):
        return RedirectResponse(_safe_next(next), status_code=303)
    return HTMLResponse(_login_html(_safe_next(next)))


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),  # noqa: A002
):
    ip = request.client.host if request.client else "unknown"
    if _too_many(ip):
        return HTMLResponse(_login_html(_safe_next(next), "尝试次数过多，请 5 分钟后再试。"), status_code=429)

    ok_user = hmac.compare_digest(username.strip(), WEB_USER)
    ok_pass = hmac.compare_digest(password, WEB_PASS)
    if not (ok_user and ok_pass):
        _note_fail(ip)
        return HTMLResponse(_login_html(_safe_next(next), "用户名或密码不正确。"), status_code=401)

    _FAILS.pop(ip, None)
    resp = RedirectResponse(_safe_next(next), status_code=303)
    resp.set_cookie(
        COOKIE_NAME,
        _issue(username.strip()),
        max_age=SESSION_TTL,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return resp


@app.post("/logout")
def logout() -> Response:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.middleware("http")
async def auth_gate(request: Request, call_next):  # noqa: ANN001, ANN201
    path = request.url.path.rstrip("/") or "/"
    if path in PUBLIC_PATHS or request.method == "OPTIONS":
        return await call_next(request)
    if _verify(request.cookies.get(COOKIE_NAME)):
        return await call_next(request)

    # 接口返回 401 而不是 302：前端 api 客户端按业务码分支，拿到登录页 HTML 会当作解析失败
    if path.startswith(API_PREFIXES):
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": "UNAUTHENTICATED",
                    "message": "会话已失效，请重新登录",
                    "details": {},
                    "request_id": secrets.token_hex(4),
                }
            },
        )
    target = "/login" + ("" if path == "/" else f"?next={path}")
    return RedirectResponse(target, status_code=303)


@app.get("/{full_path:path}")
def spa(full_path: str):  # noqa: ANN201
    """静态资源直出，其余路径交给前端路由（深链接刷新必须回 index.html）。"""
    if full_path.startswith(API_PREFIXES):
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "接口不存在", "details": {}}},
        )
    candidate = (DIST / full_path).resolve() if full_path else INDEX
    if full_path and candidate.is_file() and str(candidate).startswith(str(DIST.resolve())):
        media = MEDIA_TYPES.get(candidate.suffix.lower(), "application/octet-stream")
        return FileResponse(candidate, media_type=media)
    return FileResponse(INDEX, media_type="text/html; charset=utf-8")

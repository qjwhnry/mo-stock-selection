"""FastAPI 应用入口。"""
from __future__ import annotations

import base64
import binascii
import secrets

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

from config.settings import settings
from mo_stock.web.routers import data, reports, stocks, tasks

app = FastAPI(
    title="mo-stock API",
    version="0.1.0",
    description="A 股选股系统 REST API",
)


def _basic_auth_valid(authorization: str | None) -> bool:
    """校验 Basic Auth，但不触发浏览器原生认证弹窗。

    Nginx `auth_basic` 返回的 401 会带 `WWW-Authenticate`，浏览器会强制弹框。
    这里改由 FastAPI 返回普通 JSON 401，让前端登录页可以自己展示错误提示。
    """
    expected_password = settings.web_basic_auth_password
    if not expected_password:
        return True

    if not authorization or not authorization.startswith("Basic "):
        return False

    try:
        decoded = base64.b64decode(authorization.removeprefix("Basic "), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False

    username, sep, password = decoded.partition(":")
    if not sep:
        return False

    return (
        secrets.compare_digest(username, settings.web_basic_auth_username)
        and secrets.compare_digest(password, expected_password)
    )


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    if request.url.path.startswith("/api") and not _basic_auth_valid(request.headers.get("authorization")):
        return JSONResponse(
            status_code=401,
            content={"detail": "账号或密码错误"},
            headers={"Cache-Control": "no-store"},
        )
    return await call_next(request)


app.include_router(reports.router, prefix="/api")
app.include_router(stocks.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(data.router, prefix="/api")


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}

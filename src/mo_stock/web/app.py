"""FastAPI 应用入口。"""
from __future__ import annotations

from fastapi import FastAPI

from mo_stock.web.routers import reports, stocks

app = FastAPI(
    title="mo-stock API",
    version="0.1.0",
    description="A 股选股系统 REST API",
)

app.include_router(reports.router, prefix="/api")
app.include_router(stocks.router, prefix="/api")


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}

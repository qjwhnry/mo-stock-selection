"""单股详情 API 参数校验测试。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from mo_stock.web.app import app

client = TestClient(app)


def test_invalid_strategy() -> None:
    """测试无效的 strategy 参数。"""
    resp = client.get("/api/stocks/600519.SH?strategy=invalid")
    assert resp.status_code in (400, 422)


def test_days_zero() -> None:
    """测试 days=0 应返回 422。"""
    resp = client.get("/api/stocks/600519.SH?days=0")
    assert resp.status_code == 422


def test_days_over_100() -> None:
    """测试 days>100 应返回 422。"""
    resp = client.get("/api/stocks/600519.SH?days=500")
    assert resp.status_code == 422


def test_days_negative() -> None:
    """测试 days 为负数应返回 422。"""
    resp = client.get("/api/stocks/600519.SH?days=-1")
    assert resp.status_code == 422


def test_valid_strategy_short() -> None:
    """测试有效的 strategy=short 参数（即使股票不存在也应返回 404 而非 400）。"""
    resp = client.get("/api/stocks/600519.SH?strategy=short")
    # 可能是 404（股票不存在）或 200（存在测试数据）
    assert resp.status_code in (200, 404)


def test_valid_strategy_swing() -> None:
    """测试有效的 strategy=swing 参数。"""
    resp = client.get("/api/stocks/600519.SH?strategy=swing")
    assert resp.status_code in (200, 404)


def test_valid_days_boundary() -> None:
    """测试 days 边界值。"""
    # days=1 应该有效
    resp = client.get("/api/stocks/600519.SH?days=1")
    assert resp.status_code in (200, 404)

    # days=100 应该有效
    resp = client.get("/api/stocks/600519.SH?days=100")
    assert resp.status_code in (200, 404)

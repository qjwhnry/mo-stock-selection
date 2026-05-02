"""报告详情 API 参数校验测试。"""
from fastapi.testclient import TestClient

from mo_stock.web.app import app

client = TestClient(app)


def test_invalid_strategy():
    resp = client.get("/api/reports/2026-04-30?strategy=invalid")
    assert resp.status_code in (400, 422)


def test_invalid_sort_by():
    resp = client.get("/api/reports/2026-04-30?sort_by=nonexistent")
    assert resp.status_code == 400


def test_invalid_order():
    resp = client.get("/api/reports/2026-04-30?order=sideways")
    assert resp.status_code == 400


def test_valid_params():
    """Valid params should not return 400 (may return 200 or 500 depending on DB)."""
    resp = client.get("/api/reports/2026-04-30?strategy=short&sort_by=final_score&order=desc")
    assert resp.status_code in (200, 500)


def test_valid_strategy_swing():
    """Valid swing strategy should not return 400."""
    resp = client.get("/api/reports/2026-04-30?strategy=swing")
    assert resp.status_code in (200, 500)


def test_valid_dimension_sort_by():
    """Valid dimension sort_by should not return 400."""
    resp = client.get("/api/reports/2026-04-30?sort_by=trend&strategy=swing")
    assert resp.status_code in (200, 500)

    resp = client.get("/api/reports/2026-04-30?sort_by=limit&strategy=short")
    assert resp.status_code in (200, 500)


def test_sector_and_keyword_params():
    """sector and keyword params should be accepted."""
    resp = client.get("/api/reports/2026-04-30?sector=医药&keyword=贵州")
    assert resp.status_code in (200, 500)

"""报告列表 API 参数校验测试。"""
from fastapi.testclient import TestClient

from mo_stock.web.app import app

client = TestClient(app)


def test_page_size_over_100():
    """page_size 超过 100 返回 422。"""
    resp = client.get("/api/reports?page_size=200")
    assert resp.status_code == 422


def test_page_size_zero():
    """page_size 为 0 返回 422。"""
    resp = client.get("/api/reports?page_size=0")
    assert resp.status_code == 422


def test_page_zero():
    """page 必须大于等于 1。"""
    resp = client.get("/api/reports?page=0")
    assert resp.status_code == 422


def test_negative_page():
    """page 为负数返回 422。"""
    resp = client.get("/api/reports?page=-1")
    assert resp.status_code == 422


def test_invalid_strategy():
    """非法 strategy 返回 422（FastAPI Literal 校验）。"""
    resp = client.get("/api/reports?strategy=invalid")
    assert resp.status_code == 422


def test_invalid_order():
    """非法 order 返回 422。"""
    resp = client.get("/api/reports?order=invalid")
    assert resp.status_code == 422


def test_valid_params():
    """合法参数不报错（可能因无 DB 连接失败，但参数校验应通过）。"""
    resp = client.get("/api/reports?strategy=short&page=1&page_size=10&order=desc")
    # 参数校验通过，可能返回 200 或 500（无 DB），但不应该是 422
    assert resp.status_code in (200, 500)

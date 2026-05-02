"""Web API 健康检查测试。"""
from fastapi.testclient import TestClient

from mo_stock.web.app import app

client = TestClient(app)


def test_health_check():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

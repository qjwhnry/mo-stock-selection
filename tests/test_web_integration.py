"""Web API 集成测试——验证完整 API 链路（依赖 PG 容器）。"""
import pytest
from fastapi.testclient import TestClient

from mo_stock.web.app import app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


class TestApiChain:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_report_list(self, client):
        resp = client.get("/api/reports?strategy=short&page=1&page_size=5")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body

    def test_report_detail_empty_date(self, client):
        """一个不太可能有数据的日期。"""
        resp = client.get("/api/reports/2020-01-01?strategy=short")
        assert resp.status_code == 200
        body = resp.json()
        assert "stocks" in body
        assert isinstance(body["stocks"], list)

    def test_stock_detail_not_found(self, client):
        resp = client.get("/api/stocks/999999.SH?strategy=short")
        assert resp.status_code == 404

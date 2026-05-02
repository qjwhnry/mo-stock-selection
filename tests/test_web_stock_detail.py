"""单股详情 API 参数校验测试。"""
from fastapi.testclient import TestClient

from mo_stock.web.app import app

client = TestClient(app)


class TestStockDetailValidation:
    def test_invalid_strategy(self):
        resp = client.get("/api/stocks/600519.SH?strategy=invalid")
        assert resp.status_code == 422

    def test_days_zero(self):
        resp = client.get("/api/stocks/600519.SH?days=0")
        assert resp.status_code == 422

    def test_days_over_100(self):
        resp = client.get("/api/stocks/600519.SH?days=500")
        assert resp.status_code == 422

    def test_negative_days(self):
        resp = client.get("/api/stocks/600519.SH?days=-1")
        assert resp.status_code == 422

"""报告详情 API 参数校验测试。"""
from fastapi.testclient import TestClient

from mo_stock.web.app import app

client = TestClient(app)


class TestReportDetailValidation:
    def test_invalid_strategy(self):
        resp = client.get("/api/reports/2026-04-30?strategy=invalid")
        assert resp.status_code == 400

    def test_invalid_sort_by(self):
        resp = client.get("/api/reports/2026-04-30?sort_by=nonexistent")
        assert resp.status_code == 400

    def test_invalid_order(self):
        resp = client.get("/api/reports/2026-04-30?order=sideways")
        assert resp.status_code == 400

    def test_valid_params_no_db(self):
        """Valid params should return 200 or a DB error (not 400/422)."""
        resp = client.get("/api/reports/2026-04-30?strategy=short&sort_by=final_score&order=desc")
        # Without DB, this will 500 — that's OK, we're testing param validation
        assert resp.status_code in (200, 500)

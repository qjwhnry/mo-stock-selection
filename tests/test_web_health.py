"""Web API 健康检查测试。"""
import base64

from fastapi.testclient import TestClient

from config.settings import settings
from mo_stock.web.app import app

client = TestClient(app)


def test_health_check():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_basic_auth_failure_returns_json_without_browser_challenge():
    old_username = settings.web_basic_auth_username
    old_password = settings.web_basic_auth_password
    settings.web_basic_auth_username = "mo"
    settings.web_basic_auth_password = "secret"
    try:
        resp = client.get("/api/health")
        assert resp.status_code == 401
        assert resp.json() == {"detail": "账号或密码错误"}
        assert "www-authenticate" not in resp.headers

        bad_auth = base64.b64encode(b"mo:wrong").decode()
        resp = client.get("/api/health", headers={"Authorization": f"Basic {bad_auth}"})
        assert resp.status_code == 401
        assert "www-authenticate" not in resp.headers

        good_auth = base64.b64encode(b"mo:secret").decode()
        resp = client.get("/api/health", headers={"Authorization": f"Basic {good_auth}"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
    finally:
        settings.web_basic_auth_username = old_username
        settings.web_basic_auth_password = old_password

"""报告列表 API 测试——参数校验 + SQLite 内存库集成。"""
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from mo_stock.storage.models import Base, SelectionResult
from mo_stock.web.app import app
from mo_stock.web.deps import get_db


def _make_test_db():
    """创建 SQLite 内存库并灌入测试数据。"""
    engine = create_engine(
        "sqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[SelectionResult.__table__])
    test_session = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    with test_session() as s:
        s.add_all([
            SelectionResult(
                trade_date=date(2026, 4, 30), strategy="short", ts_code="600519.SH",
                rank=1, rule_score=82.0, ai_score=90.0, final_score=85.2,
                picked=True, created_at=datetime.now(UTC),
            ),
            SelectionResult(
                trade_date=date(2026, 4, 29), strategy="short", ts_code="000001.SZ",
                rank=2, rule_score=70.0, ai_score=None, final_score=70.0,
                picked=True, created_at=datetime.now(UTC),
            ),
        ])
        s.commit()

    def override():
        db = test_session()
        try:
            yield db
        finally:
            db.close()

    return override


@pytest.fixture()
def client():
    override = _make_test_db()
    app.dependency_overrides[get_db] = override
    tc = TestClient(app)
    yield tc
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 参数校验（不需要 DB）
# ---------------------------------------------------------------------------

def test_page_size_over_100(client):
    resp = client.get("/api/reports?page_size=200")
    assert resp.status_code == 422


def test_page_size_zero(client):
    resp = client.get("/api/reports?page_size=0")
    assert resp.status_code == 422


def test_page_zero(client):
    resp = client.get("/api/reports?page=0")
    assert resp.status_code == 422


def test_negative_page(client):
    resp = client.get("/api/reports?page=-1")
    assert resp.status_code == 422


def test_invalid_strategy(client):
    resp = client.get("/api/reports?strategy=invalid")
    assert resp.status_code == 422


def test_invalid_order(client):
    resp = client.get("/api/reports?order=invalid")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# SQLite 集成——精确断言 200
# ---------------------------------------------------------------------------

def test_valid_params_returns_200(client):
    resp = client.get("/api/reports?strategy=short&page=1&page_size=10&order=desc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert body["items"][0]["trade_date"] == "2026-04-30"
    assert body["items"][0]["count"] == 1
    assert abs(body["items"][0]["avg_score"] - 85.2) < 0.1


def test_order_asc(client):
    resp = client.get("/api/reports?order=asc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["trade_date"] == "2026-04-29"


def test_page_2_empty(client):
    resp = client.get("/api/reports?page=2&page_size=10")
    assert resp.status_code == 200
    assert resp.json()["items"] == []

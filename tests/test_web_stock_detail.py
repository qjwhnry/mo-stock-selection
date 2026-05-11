"""单股详情 API 测试——参数校验 + SQLite 内存库集成。"""
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from mo_stock.web.app import app
from mo_stock.web.deps import get_db

_DDL = """
CREATE TABLE IF NOT EXISTS stock_basic (
    ts_code VARCHAR(12) PRIMARY KEY, symbol VARCHAR(10), name VARCHAR(50),
    area VARCHAR(20), industry VARCHAR(50), sw_l1 VARCHAR(50),
    list_date DATE, is_st BOOLEAN DEFAULT 0, updated_at DATETIME
);
CREATE TABLE IF NOT EXISTS index_member (
    ts_code VARCHAR(12) PRIMARY KEY, l1_code VARCHAR(20), l1_name VARCHAR(50),
    l2_code VARCHAR(20), l2_name VARCHAR(50), l3_code VARCHAR(20), l3_name VARCHAR(50), in_date DATE
);
CREATE TABLE IF NOT EXISTS selection_result (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date DATE,
    strategy VARCHAR(20) DEFAULT 'short', ts_code VARCHAR(12), rank INTEGER,
    rule_score FLOAT, ai_score FLOAT, final_score FLOAT,
    picked BOOLEAN DEFAULT 1, reject_reason VARCHAR(200), created_at DATETIME
);
CREATE TABLE IF NOT EXISTS filter_score_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date DATE,
    strategy VARCHAR(20) DEFAULT 'short', ts_code VARCHAR(12),
    dim VARCHAR(20), score FLOAT, detail TEXT
);
CREATE TABLE IF NOT EXISTS ai_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date DATE,
    strategy VARCHAR(20) DEFAULT 'short', ts_code VARCHAR(12),
    ai_score INTEGER, thesis TEXT, key_catalysts TEXT, risks TEXT,
    suggested_entry VARCHAR(100), stop_loss VARCHAR(100), model VARCHAR(50),
    input_tokens INTEGER, output_tokens INTEGER,
    cache_creation_tokens INTEGER, cache_read_tokens INTEGER, created_at DATETIME
);
CREATE TABLE IF NOT EXISTS ths_index (
    ts_code VARCHAR(20) PRIMARY KEY, name VARCHAR(50),
    count INTEGER, exchange VARCHAR(10), list_date DATE, type VARCHAR(5)
);
CREATE TABLE IF NOT EXISTS ths_member (
    ts_code VARCHAR(20), con_code VARCHAR(12),
    con_name VARCHAR(50), weight FLOAT, in_date DATE, out_date DATE,
    PRIMARY KEY (ts_code, con_code)
);
CREATE TABLE IF NOT EXISTS limit_concept_daily (
    ts_code VARCHAR(20), trade_date DATE,
    name VARCHAR(50), days INTEGER, up_stat VARCHAR(50),
    cons_nums INTEGER, up_nums INTEGER, pct_chg FLOAT, rank INTEGER,
    PRIMARY KEY (ts_code, trade_date)
);
"""


def _make_test_db():
    """创建 SQLite 内存库并灌入测试数据。"""
    engine = create_engine(
        "sqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))

    test_session = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    now = datetime.now(UTC).isoformat()
    with test_session() as s:
        s.execute(text(
            "INSERT INTO stock_basic (ts_code, symbol, name, industry, updated_at) "
            "VALUES ('600519.SH', '600519', '贵州茅台', '食品饮料', :now)"
        ), {"now": now})
        s.execute(text(
            "INSERT INTO index_member (ts_code, l1_code, l1_name) "
            "VALUES ('600519.SH', '801125', '食品饮料')"
        ))
        s.execute(text(
            "INSERT INTO selection_result (trade_date, strategy, ts_code, rank, rule_score, ai_score, final_score, picked, created_at) "
            "VALUES ('2026-04-30', 'short', '600519.SH', 1, 82.0, 90.0, 85.2, 1, :now)"
        ), {"now": now})
        s.execute(text(
            "INSERT INTO filter_score_daily (trade_date, strategy, ts_code, dim, score) "
            "VALUES ('2026-04-30', 'short', '600519.SH', 'limit', 92.0)"
        ))
        s.execute(text(
            "INSERT INTO ths_index (ts_code, name, type) VALUES ('885412.TI', '白酒', 'N')"
        ))
        s.execute(text(
            "INSERT INTO ths_index (ts_code, name, type) VALUES ('885328.TI', '新能源车', 'N')"
        ))
        s.execute(text(
            "INSERT INTO ths_member (ts_code, con_code, con_name) VALUES ('885412.TI', '600519.SH', '贵州茅台')"
        ))
        s.execute(text(
            "INSERT INTO ths_member (ts_code, con_code, con_name) VALUES ('885328.TI', '600519.SH', '贵州茅台')"
        ))
        s.execute(text(
            "INSERT INTO limit_concept_daily (ts_code, trade_date, name, rank, up_nums) VALUES ('885412.TI', '2026-04-30', '白酒', 1, 5)"
        ))
        s.execute(text(
            "INSERT INTO limit_concept_daily (ts_code, trade_date, name, rank, up_nums) VALUES ('885328.TI', '2026-04-30', '新能源车', 2, 3)"
        ))
        for i in range(1, 15):
            concept_code = f"8859{i:02d}.TI"
            concept_name = f"概念{i:02d}"
            s.execute(
                text("INSERT INTO ths_index (ts_code, name, type) VALUES (:code, :name, 'N')"),
                {"code": concept_code, "name": concept_name},
            )
            s.execute(
                text(
                    "INSERT INTO ths_member (ts_code, con_code, con_name) "
                    "VALUES (:code, '600519.SH', '贵州茅台')"
                ),
                {"code": concept_code},
            )
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
# 参数校验（无 DB 依赖）
# ---------------------------------------------------------------------------

class TestStockDetailValidation:
    def test_invalid_strategy(self):
        resp = client_no_db().get("/api/stocks/600519.SH?strategy=invalid")
        assert resp.status_code == 422

    def test_days_zero(self):
        resp = client_no_db().get("/api/stocks/600519.SH?days=0")
        assert resp.status_code == 422

    def test_days_over_100(self):
        resp = client_no_db().get("/api/stocks/600519.SH?days=500")
        assert resp.status_code == 422

    def test_negative_days(self):
        resp = client_no_db().get("/api/stocks/600519.SH?days=-1")
        assert resp.status_code == 422


def client_no_db():
    """不需要 DB 的参数校验用的 client。"""
    return TestClient(app)


# ---------------------------------------------------------------------------
# SQLite 集成——成功返回 + concepts 断言
# ---------------------------------------------------------------------------

def test_stock_detail_success_with_concepts(client):
    resp = client.get("/api/stocks/600519.SH?strategy=short")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ts_code"] == "600519.SH"
    assert body["name"] == "贵州茅台"
    assert body["industry"] == "食品饮料"
    assert body["concept_count"] == 16
    assert len(body["concepts"]) == 15
    assert body["concepts"][:3] == ["白酒", "新能源车", "概念01"]
    assert body["concepts"][-1] == "概念13"
    assert body["latest_scores"]["limit"] == 92
    assert body["recent_picks"][0]["final_score"] == 85.2


def test_stock_not_found(client):
    resp = client.get("/api/stocks/999999.SH?strategy=short")
    assert resp.status_code == 404

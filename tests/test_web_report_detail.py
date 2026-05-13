"""报告详情 API 测试——参数校验 + SQLite 内存库集成。"""
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
CREATE TABLE IF NOT EXISTS daily_kline (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts_code VARCHAR(12), trade_date DATE,
    open FLOAT, high FLOAT, low FLOAT, close FLOAT,
    pre_close FLOAT, pct_chg FLOAT, vol FLOAT, amount FLOAT
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
        stock_rows = [
            ("000001.SZ", "000001", "平安银行", "银行", 0),
            ("000002.SZ", "000002", "万科A", "房地产", 0),
            ("000003.SZ", "000003", "缺日线股", "测试", 0),
            ("000004.SZ", "000004", "*ST测试", "测试", 1),
            ("000005.SZ", "000005", "零量股", "测试", 0),
        ]
        for ts_code, symbol, name, industry, is_st in stock_rows:
            s.execute(
                text(
                    "INSERT INTO stock_basic "
                    "(ts_code, symbol, name, industry, is_st, updated_at) "
                    "VALUES (:ts_code, :symbol, :name, :industry, :is_st, :now)"
                ),
                {
                    "ts_code": ts_code,
                    "symbol": symbol,
                    "name": name,
                    "industry": industry,
                    "is_st": is_st,
                    "now": now,
                },
            )
        s.execute(text(
            "INSERT INTO index_member (ts_code, l1_code, l1_name) "
            "VALUES ('600519.SH', '801125', '食品饮料')"
        ))
        s.execute(text(
            "INSERT INTO daily_kline (ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount) "
            "VALUES ('000001.SH', '2026-04-30', 3200, 3250, 3190, 3245, 3219, 0.8, 1e8, 1e10)"
        ))
        s.execute(text(
            "INSERT INTO daily_kline (ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount) "
            "VALUES ('000300.SH', '2026-04-30', 3850, 3880, 3840, 3876, 3841, 0.9, 5e7, 5e9)"
        ))
        daily_rows = [
            ("600519.SH", 1600, 1.0, 1000),
            ("000001.SZ", 12, 2.0, 2000),
            ("000002.SZ", 8, 1.5, 2000),
            ("000004.SZ", 3, 4.0, 2000),
            ("000005.SZ", 5, 0.0, 0),
        ]
        for ts_code, close, pct_chg, vol in daily_rows:
            s.execute(
                text(
                    "INSERT INTO daily_kline "
                    "(ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount) "
                    "VALUES (:ts_code, '2026-04-30', :close, :close, :close, :close, :close, :pct_chg, :vol, 100000)"
                ),
                {"ts_code": ts_code, "close": close, "pct_chg": pct_chg, "vol": vol},
            )
        s.execute(text(
            "INSERT INTO selection_result (trade_date, strategy, ts_code, rank, rule_score, ai_score, final_score, picked, created_at) "
            "VALUES ('2026-04-30', 'short', '600519.SH', 1, 82.0, 90.0, 85.2, 1, :now)"
        ), {"now": now})
        s.execute(text(
            "INSERT INTO filter_score_daily (trade_date, strategy, ts_code, dim, score) "
            "VALUES ('2026-04-30', 'short', '600519.SH', 'limit', 92.0)"
        ))
        s.execute(text(
            "INSERT INTO filter_score_daily (trade_date, strategy, ts_code, dim, score) "
            "VALUES ('2026-04-30', 'short', '600519.SH', 'moneyflow', 85.0)"
        ))
        s.execute(text(
            "INSERT INTO filter_score_daily (trade_date, strategy, ts_code, dim, score) "
            "VALUES ('2026-04-30', 'short', '600519.SH', 'limit_restart', 76.0)"
        ))
        lhb_scores = [
            ("000004.SZ", 100.0),  # ST，必须过滤
            ("000005.SZ", 99.0),   # vol=0，必须过滤
            ("000003.SZ", 98.0),   # 无当日日线，必须过滤
            ("000001.SZ", 95.0),
            ("000002.SZ", 95.0),   # 同分，按代码排在 000001.SZ 后
            ("600519.SH", 80.0),
        ]
        for ts_code, score in lhb_scores:
            s.execute(
                text(
                    "INSERT INTO filter_score_daily (trade_date, strategy, ts_code, dim, score) "
                    "VALUES ('2026-04-30', 'short', :ts_code, 'lhb', :score)"
                ),
                {"ts_code": ts_code, "score": score},
            )
        s.execute(text(
            "INSERT INTO ths_index (ts_code, name, type) VALUES ('885328.TI', '新能源车', 'N')"
        ))
        s.execute(text(
            "INSERT INTO ths_index (ts_code, name, type) VALUES ('885412.TI', '白酒', 'N')"
        ))
        s.execute(text(
            "INSERT INTO ths_member (ts_code, con_code, con_name) VALUES ('885328.TI', '600519.SH', '贵州茅台')"
        ))
        s.execute(text(
            "INSERT INTO ths_member (ts_code, con_code, con_name) VALUES ('885412.TI', '600519.SH', '贵州茅台')"
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
# 参数校验
# ---------------------------------------------------------------------------

class TestReportDetailValidation:
    def test_invalid_strategy(self, client):
        resp = client.get("/api/reports/2026-04-30?strategy=invalid")
        assert resp.status_code == 400

    def test_invalid_sort_by(self, client):
        resp = client.get("/api/reports/2026-04-30?sort_by=nonexistent")
        assert resp.status_code == 400

    def test_invalid_order(self, client):
        resp = client.get("/api/reports/2026-04-30?order=sideways")
        assert resp.status_code == 400

    def test_dimension_top_invalid_dim(self, client):
        resp = client.get("/api/reports/2026-04-30/dimension-top?strategy=short&dim=bad_dim")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# SQLite 集成——精确断言 200
# ---------------------------------------------------------------------------

def test_valid_detail_returns_200(client):
    resp = client.get("/api/reports/2026-04-30?strategy=short")
    assert resp.status_code == 200
    body = resp.json()
    assert body["trade_date"] == "2026-04-30"
    assert body["strategy"] == "short"
    assert len(body["stocks"]) == 1
    stock = body["stocks"][0]
    assert stock["ts_code"] == "600519.SH"
    assert stock["name"] == "贵州茅台"
    assert stock["final_score"] == 85.2
    assert stock["scores"]["limit"] == 92
    assert stock["scores"]["limit_restart"] == 76
    assert stock["concept_count"] == 16
    assert stock["concepts"] == ["白酒", "新能源车", "概念01", "概念02", "概念03"]


def test_sort_by_limit_restart_returns_200(client):
    resp = client.get("/api/reports/2026-04-30?strategy=short&sort_by=limit_restart")
    assert resp.status_code == 200
    stock = resp.json()["stocks"][0]
    assert stock["scores"]["limit_restart"] == 76


def test_dimension_top_filters_st_suspended_and_orders_by_code(client):
    resp = client.get("/api/reports/2026-04-30/dimension-top?strategy=short&dim=lhb&limit=20")

    assert resp.status_code == 200
    stocks = resp.json()["stocks"]
    codes = [s["ts_code"] for s in stocks]

    assert codes == ["000001.SZ", "000002.SZ", "600519.SH"]
    assert "000003.SZ" not in codes
    assert "000004.SZ" not in codes
    assert "000005.SZ" not in codes
    assert stocks[0]["scores"]["lhb"] == 95
    assert stocks[1]["scores"]["lhb"] == 95
    assert stocks[0]["picked"] is False
    assert stocks[2]["picked"] is True


def test_market_data_present(client):
    resp = client.get("/api/reports/2026-04-30?strategy=short")
    assert resp.status_code == 200
    market = resp.json()["market"]
    assert market["sh_index"]["close"] == 3245
    assert market["hs300_index"]["close"] == 3876


def test_available_sectors(client):
    resp = client.get("/api/reports/2026-04-30?strategy=short")
    assert resp.status_code == 200
    sectors = resp.json()["available_sectors"]
    assert "食品饮料" in sectors


def test_empty_date(client):
    resp = client.get("/api/reports/2020-01-01?strategy=short")
    assert resp.status_code == 200
    assert resp.json()["stocks"] == []

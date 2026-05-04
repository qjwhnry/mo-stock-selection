"""数据洞察 API 测试。"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from mo_stock.storage.models import (
    Base,
    DailyKline,
    FilterScoreDaily,
    IndexMember,
    Lhb,
    LhbSeatDetail,
    Moneyflow,
    SelectionResult,
    StockBasic,
)
from mo_stock.web.app import app
from mo_stock.web.deps import get_db
from tests.conftest import _register_jsonb_on_sqlite

TRADE_DATE = date(2026, 4, 30)


def _make_client() -> TestClient:
    _register_jsonb_on_sqlite()
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    with test_session() as session:
        _seed(session)
        session.commit()

    def override():
        db = test_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app)


def _seed(session: Session) -> None:
    session.add_all([
        StockBasic(
            ts_code="600001.SH",
            symbol="600001",
            name="测试银行",
            area="上海",
            industry="金融",
            sw_l1=None,
            list_date=date(2020, 1, 1),
            is_st=False,
        ),
        StockBasic(
            ts_code="000001.SZ",
            symbol="000001",
            name="测试证券",
            area="深圳",
            industry="金融",
            sw_l1="非银金融",
            list_date=date(2020, 1, 1),
            is_st=False,
        ),
        StockBasic(
            ts_code="300001.SZ",
            symbol="300001",
            name="测试电力",
            area="宁德",
            industry="电力设备",
            sw_l1=None,
            list_date=date(2020, 1, 1),
            is_st=False,
        ),
        IndexMember(ts_code="600001.SH", l1_code="801780.SI", l1_name="银行"),
    ])
    session.add_all([
        DailyKline(
            ts_code="600001.SH",
            trade_date=TRADE_DATE,
            open=10,
            high=11,
            low=9.8,
            close=10.8,
            pre_close=10,
            pct_chg=8.0,
            vol=100000,
            amount=200000,
        ),
        DailyKline(
            ts_code="000001.SZ",
            trade_date=TRADE_DATE,
            open=20,
            high=20.5,
            low=19.5,
            close=19.8,
            pre_close=20,
            pct_chg=-1.0,
            vol=100000,
            amount=100000,
        ),
        DailyKline(
            ts_code="300001.SZ",
            trade_date=TRADE_DATE,
            open=30,
            high=31,
            low=29.5,
            close=30.5,
            pre_close=30,
            pct_chg=1.67,
            vol=100000,
            amount=0,
        ),
    ])
    session.add_all([
        Moneyflow(
            ts_code="600001.SH",
            trade_date=TRADE_DATE,
            net_mf_amount=10000,
            buy_lg_amount=8000,
            sell_lg_amount=3000,
            buy_elg_amount=9000,
            sell_elg_amount=4000,
        ),
        Moneyflow(
            ts_code="000001.SZ",
            trade_date=TRADE_DATE,
            net_mf_amount=-5000,
            buy_lg_amount=1000,
            sell_lg_amount=3000,
            buy_elg_amount=2000,
            sell_elg_amount=5000,
        ),
        Moneyflow(
            ts_code="300001.SZ",
            trade_date=TRADE_DATE,
            net_mf_amount=1000,
        ),
    ])
    session.add(
        Lhb(
            trade_date=TRADE_DATE,
            ts_code="600001.SH",
            name="测试银行",
            close=10.8,
            pct_change=8.0,
            turnover_rate=12.0,
            amount=40000000,
            l_sell=2000000,
            l_buy=7000000,
            l_amount=9000000,
            net_amount=5000000,
            net_rate=12.5,
            amount_rate=22.5,
            reason="日涨幅偏离值达 7%",
        )
    )
    session.add_all([
        LhbSeatDetail(
            trade_date=TRADE_DATE,
            ts_code="600001.SH",
            seat_key="b",
            seat_no=2,
            exalter="活跃营业部",
            side="0",
            buy=2000000,
            sell=500000,
            net_buy=1500000,
            reason="日涨幅偏离值达 7%",
            seat_type="hot_money",
        ),
        LhbSeatDetail(
            trade_date=TRADE_DATE,
            ts_code="600001.SH",
            seat_key="a",
            seat_no=1,
            exalter="机构专用",
            side="0",
            buy=3000000,
            sell=1000000,
            net_buy=2000000,
            reason="日涨幅偏离值达 7%",
            seat_type="institution",
        ),
    ])
    session.add(
        SelectionResult(
            trade_date=TRADE_DATE,
            strategy="short",
            ts_code="600001.SH",
            rank=1,
            rule_score=82,
            ai_score=90,
            final_score=85.2,
            picked=True,
        )
    )
    session.add_all([
        FilterScoreDaily(
            trade_date=TRADE_DATE,
            strategy="short",
            ts_code="600001.SH",
            dim="moneyflow",
            score=88,
            detail={"net_mf_ratio_pct": 50},
        ),
        FilterScoreDaily(
            trade_date=TRADE_DATE,
            strategy="short",
            ts_code="600001.SH",
            dim="lhb",
            score=76,
            detail={"seat_score": 20},
        ),
    ])


@pytest.fixture()
def client():
    test_client = _make_client()
    yield test_client
    app.dependency_overrides.clear()


def test_moneyflow_summary_units_and_summary(client: TestClient) -> None:
    resp = client.get("/api/data/moneyflow-summary?trade_date=2026-04-30&sort_by=net_mf_wan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["summary"]["net_mf_positive_count"] == 2
    assert body["summary"]["total_net_mf_wan"] == 6000

    item = body["items"][0]
    assert item["ts_code"] == "600001.SH"
    assert item["industry"] == "银行"
    assert item["net_mf_wan"] == 10000
    assert item["net_mf_ratio_pct"] == 50
    assert item["scores"]["moneyflow"] == 88


def test_moneyflow_summary_aggregation_not_limited_by_page(client: TestClient) -> None:
    resp = client.get(
        "/api/data/moneyflow-summary"
        "?trade_date=2026-04-30&sort_by=net_mf_wan&page_size=1"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["total"] == 3
    assert body["summary"]["net_mf_positive_count"] == 2
    assert body["summary"]["total_net_mf_wan"] == 6000


def test_moneyflow_summary_keyword_filter(client: TestClient) -> None:
    resp = client.get("/api/data/moneyflow-summary?trade_date=2026-04-30&keyword=证券")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["ts_code"] == "000001.SZ"
    assert body["summary"]["net_mf_positive_count"] == 0
    assert body["summary"]["total_net_mf_wan"] == -5000


def test_moneyflow_ratio_null_when_amount_zero(client: TestClient) -> None:
    resp = client.get("/api/data/moneyflow-summary?trade_date=2026-04-30&sector=电力设备")
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["ts_code"] == "300001.SZ"
    assert item["net_mf_ratio_pct"] is None


def test_lhb_summary_units_no_duplicate_and_seat_summary(client: TestClient) -> None:
    resp = client.get("/api/data/lhb-summary?trade_date=2026-04-30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["summary"]["lhb_count"] == 1
    assert body["summary"]["institution_net_buy_count"] == 1
    assert body["summary"]["total_lhb_net_amount_wan"] == 500

    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["lhb_buy_wan"] == 700
    assert item["lhb_sell_wan"] == 200
    assert item["lhb_net_amount_wan"] == 500
    assert item["seat_summary"] == {"hot_money": 1, "institution": 1}
    assert item["scores"]["lhb"] == 76


def test_lhb_summary_keyword_filter(client: TestClient) -> None:
    hit = client.get("/api/data/lhb-summary?trade_date=2026-04-30&keyword=银行")
    assert hit.status_code == 200
    assert hit.json()["total"] == 1

    miss = client.get("/api/data/lhb-summary?trade_date=2026-04-30&keyword=证券")
    assert miss.status_code == 200
    body = miss.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert body["summary"]["lhb_count"] == 0
    assert body["summary"]["institution_net_buy_count"] == 0


def test_lhb_seats_ordered_by_seat_no(client: TestClient) -> None:
    resp = client.get("/api/data/stocks/600001.SH/lhb-seats?trade_date=2026-04-30")
    assert resp.status_code == 200
    seats = resp.json()["seats"]
    assert [row["seat_no"] for row in seats] == [1, 2]
    assert seats[0]["exalter"] == "机构专用"
    assert seats[0]["net_buy_wan"] == 200


def test_sectors_use_full_market_fallback_chain(client: TestClient) -> None:
    resp = client.get("/api/data/sectors?trade_date=2026-04-30")
    assert resp.status_code == 200
    assert resp.json()["sectors"] == ["电力设备", "银行", "非银金融"]


def test_stock_signals_and_days_limit(client: TestClient) -> None:
    ok = client.get("/api/data/stocks/600001.SH/signals?end_date=2026-04-30&days=20")
    assert ok.status_code == 200
    body = ok.json()
    assert body["industry"] == "银行"
    assert body["moneyflow"][0]["net_mf_ratio_pct"] == 50
    assert body["lhb"][0]["lhb_net_amount_wan"] == 500
    assert body["scores"][0]["dim"] == "lhb" or body["scores"][0]["dim"] == "moneyflow"

    too_many = client.get("/api/data/stocks/600001.SH/signals?end_date=2026-04-30&days=500")
    assert too_many.status_code == 422


def test_validation_errors(client: TestClient) -> None:
    assert client.get("/api/data/moneyflow-summary?trade_date=2026-04-30&page_size=0").status_code == 422
    assert client.get("/api/data/moneyflow-summary?trade_date=2026-04-30&page_size=101").status_code == 422
    assert client.get("/api/data/moneyflow-summary?trade_date=2026-04-30&sort_by=bad").status_code == 400
    assert client.get("/api/data/lhb-summary?trade_date=2026-04-30&order=sideways").status_code == 422

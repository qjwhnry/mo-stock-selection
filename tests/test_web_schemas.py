"""Pydantic response schema 验证测试。"""

from mo_stock.web.schemas import (
    MarketData,
    ReportListItem,
    ReportListResponse,
    StockDetailResponse,
    StockItem,
)


def test_report_list_item():
    item = ReportListItem(
        trade_date="2026-04-30",
        strategy="short",
        count=12,
        avg_score=72.3,
        max_score=85.2,
    )
    assert item.count == 12
    assert item.avg_score == 72.3


def test_report_list_response():
    resp = ReportListResponse(
        items=[
            ReportListItem(
                trade_date="2026-04-30",
                strategy="short",
                count=12,
                avg_score=72.3,
                max_score=85.2,
            ),
        ],
        total=45,
        page=1,
        page_size=20,
    )
    assert resp.total == 45
    assert len(resp.items) == 1


def test_market_data():
    md = MarketData(
        sh_index={"close": 3245.0, "pct_chg": 0.8},
        hs300_index={"close": 3876.0, "pct_chg": 0.9},
        regime_score=72,
    )
    assert md.regime_score == 72


def test_stock_item():
    item = StockItem(
        rank=1,
        ts_code="600519.SH",
        name="贵州茅台",
        industry="食品饮料",
        final_score=85.2,
        rule_score=82.0,
        ai_score=90.0,
        scores={"limit": 92, "moneyflow": 85},
        ai_summary="白酒板块资金回流",
        picked=True,
    )
    assert item.ts_code == "600519.SH"


def test_stock_item_optional_ai():
    """ai_score / ai_summary 可为 None。"""
    item = StockItem(
        rank=2,
        ts_code="000001.SZ",
        name="平安银行",
        industry="银行",
        final_score=65.0,
        rule_score=65.0,
        ai_score=None,
        scores={"limit": 70},
        ai_summary=None,
        picked=True,
    )
    assert item.ai_score is None


def test_stock_detail_ai_null():
    resp = StockDetailResponse(
        ts_code="600519.SH",
        name="贵州茅台",
        industry="食品饮料",
        latest_scores={"limit": 92},
        ai_analysis=None,
        recent_picks=[],
    )
    assert resp.ai_analysis is None

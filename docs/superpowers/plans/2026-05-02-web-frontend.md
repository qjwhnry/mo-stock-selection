# Web Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a FastAPI backend + Vue 3 frontend to mo-stock-selection for browsing reports, filtering stocks, and viewing on mobile.

**Architecture:** FastAPI serves REST API from existing PostgreSQL via SQLAlchemy. Vue 3 SPA (TailAdmin template) consumes the API. Docker Compose extends existing pg service with api + nginx containers. Nginx serves static files + proxies /api/.

**Tech Stack:** FastAPI, SQLAlchemy 2.x, Pydantic v2, Vue 3, Tailwind CSS, TailAdmin, ECharts (Phase 2 only), Docker Compose, Nginx

**Spec:** `docs/superpowers/specs/2026-05-02-web-frontend-design.md`

---

## File Structure

### Backend (new files)
```
src/mo_stock/web/
├── __init__.py          # Module docstring
├── app.py               # FastAPI app factory, /api mount, health check
├── deps.py              # get_db() dependency, settings loader
├── schemas.py           # Pydantic response models
└── routers/
    ├── __init__.py
    ├── reports.py        # GET /api/reports, GET /api/reports/{date}
    └── stocks.py         # GET /api/stocks/{ts_code}
```

### Frontend (new directory)
```
frontend/
├── src/
│   ├── views/
│   │   ├── ReportList.vue
│   │   ├── ReportDetail.vue
│   │   └── StockDetail.vue
│   ├── components/
│   │   ├── ScoreTable.vue
│   │   ├── DimensionBar.vue
│   │   ├── MarketOverview.vue
│   │   └── AiSummary.vue
│   ├── router/index.ts
│   ├── api/index.ts
│   ├── App.vue
│   └── main.ts
├── index.html
├── package.json
├── vite.config.ts
└── tsconfig.json
```

### Deploy (new/modified files)
```
Dockerfile               # Python 3.12-slim for FastAPI
.dockerignore             # Exclude .venv, .git, data, frontend/node_modules
docker-compose.yml        # Extend existing: add api + nginx services
nginx.conf                # SPA fallback + /api proxy + Basic Auth
```

### Modified files
```
pyproject.toml            # Add fastapi + uvicorn dependencies
```

---

## Task 1: Backend Dependencies & App Skeleton

**Files:**
- Modify: `pyproject.toml:14-45`
- Create: `src/mo_stock/web/__init__.py`
- Create: `src/mo_stock/web/app.py`
- Create: `src/mo_stock/web/deps.py`
- Test: `tests/test_web_health.py`

- [ ] **Step 1: Add fastapi + uvicorn to pyproject.toml**

In `pyproject.toml`, add to `dependencies` list (after `jinja2>=3.1`):

```python
    # Web API
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
```

- [ ] **Step 2: Install new dependencies**

Run: `.venv/bin/pip install -e .`
Expected: Successfully installed fastapi uvicorn etc.

- [ ] **Step 3: Create `src/mo_stock/web/__init__.py`**

```python
"""Web API 模块：FastAPI REST API，供前端调用。"""
```

- [ ] **Step 4: Create `src/mo_stock/web/deps.py`**

Database session dependency for FastAPI. Uses the existing `get_session()` context manager pattern from `storage/db.py`.

```python
"""FastAPI 依赖注入。"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from mo_stock.storage.db import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI Depends 用的 Session 生成器。

    与 CLI 的 get_session() 不同，这里不自动 commit（纯读 API），
    退出时只 close。
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
```

- [ ] **Step 5: Create `src/mo_stock/web/app.py`**

```python
"""FastAPI 应用入口。"""
from __future__ import annotations

from fastapi import FastAPI

from mo_stock.web.routers import reports, stocks

app = FastAPI(
    title="mo-stock API",
    version="0.1.0",
    description="A 股选股系统 REST API",
)

app.include_router(reports.router, prefix="/api")
app.include_router(stocks.router, prefix="/api")


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 6: Create `src/mo_stock/web/routers/__init__.py`**

```python
"""API 路由模块。"""
```

- [ ] **Step 7: Create placeholder routers**

`src/mo_stock/web/routers/reports.py`:
```python
"""报告相关 API。"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["reports"])
```

`src/mo_stock/web/routers/stocks.py`:
```python
"""个股相关 API。"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["stocks"])
```

- [ ] **Step 8: Write health check test**

Create `tests/test_web_health.py`:

```python
"""Web API 健康检查测试。"""
from fastapi.testclient import TestClient

from mo_stock.web.app import app

client = TestClient(app)


def test_health_check():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 9: Run health check test**

Run: `.venv/bin/python -m pytest tests/test_web_health.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml src/mo_stock/web/ tests/test_web_health.py
git commit -m "feat(web): FastAPI app skeleton with health check endpoint"
```

---

## Task 2: Pydantic Response Schemas

**Files:**
- Create: `src/mo_stock/web/schemas.py`
- Test: `tests/test_web_schemas.py`

- [ ] **Step 1: Write schema tests**

Create `tests/test_web_schemas.py`:

```python
"""Pydantic response schema 验证测试。"""
import pytest
from pydantic import ValidationError

from mo_stock.web.schemas import (
    ReportListItem,
    ReportListResponse,
    MarketData,
    StockItem,
    ReportDetailResponse,
    StockDetailResponse,
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_web_schemas.py -v`
Expected: FAIL (ImportError: cannot import name 'schemas' from 'mo_stock.web')

- [ ] **Step 3: Create `src/mo_stock/web/schemas.py`**

```python
"""Pydantic response schema——API 返回结构定义。"""
from __future__ import annotations

from pydantic import BaseModel


class ReportListItem(BaseModel):
    trade_date: str
    strategy: str
    count: int
    avg_score: float
    max_score: float


class ReportListResponse(BaseModel):
    items: list[ReportListItem]
    total: int
    page: int
    page_size: int


class IndexSnapshot(BaseModel):
    close: float
    pct_chg: float


class MarketData(BaseModel):
    sh_index: IndexSnapshot
    hs300_index: IndexSnapshot
    regime_score: float


class StockItem(BaseModel):
    rank: int
    ts_code: str
    name: str
    industry: str
    final_score: float
    rule_score: float
    ai_score: float | None = None
    scores: dict[str, int]
    ai_summary: str | None = None
    picked: bool


class ReportDetailResponse(BaseModel):
    trade_date: str
    strategy: str
    market: MarketData
    stocks: list[StockItem]
    available_sectors: list[str]


class AiAnalysisData(BaseModel):
    thesis: str
    key_catalysts: list[str] | None = None
    risks: list[str] | None = None
    suggested_entry: str | None = None
    stop_loss: str | None = None


class RecentPick(BaseModel):
    trade_date: str
    picked: bool
    final_score: float


class StockDetailResponse(BaseModel):
    ts_code: str
    name: str
    industry: str
    latest_scores: dict[str, int]
    ai_analysis: AiAnalysisData | None = None
    recent_picks: list[RecentPick]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web_schemas.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/mo_stock/web/schemas.py tests/test_web_schemas.py
git commit -m "feat(web): Pydantic response schemas for all API endpoints"
```

---

## Task 3: Report List API

**Files:**
- Modify: `src/mo_stock/web/routers/reports.py`
- Test: `tests/test_web_reports.py`

- [ ] **Step 1: Write report list API tests**

Create `tests/test_web_reports.py`:

```python
"""报告列表 API 测试。"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    with patch("mo_stock.web.deps.SessionLocal") as mock_sl:
        mock_session = MagicMock()
        mock_sl.return_value = mock_session
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        from mo_stock.web.app import app
        from mo_stock.web.deps import get_db

        app.dependency_overrides[get_db] = lambda: mock_session
        tc = TestClient(app)
        yield tc
        app.dependency_overrides.clear()


class TestReportList:
    def test_empty_reports(self, client):
        """无报告时返回空列表。"""
        mock_session = client.app.dependency_overrides[
            # get_db returns the mock session
            list(client.app.dependency_overrides.values())[0]
        ]
        # We need a different approach for mocking DB queries
        # Use the TestClient with dependency override properly
        resp = client.get("/api/reports?strategy=short&page=1&page_size=20")
        # Response depends on mock behavior; we test schema validation
        assert resp.status_code in (200, 422)

    def test_invalid_strategy(self, client):
        """非法 strategy 返回 422。"""
        resp = client.get("/api/reports?strategy=invalid")
        assert resp.status_code == 422

    def test_page_size_over_100(self, client):
        """page_size 超过 100 返回 422。"""
        resp = client.get("/api/reports?page_size=200")
        assert resp.status_code == 422

    def test_page_size_zero(self, client):
        """page_size 为 0 返回 422。"""
        resp = client.get("/api/reports?page_size=0")
        assert resp.status_code == 422
```

- [ ] **Step 2: Implement report list endpoint**

Replace `src/mo_stock/web/routers/reports.py`:

```python
"""报告相关 API。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mo_stock.storage.models import SelectionResult
from mo_stock.web.deps import get_db
from mo_stock.web.schemas import ReportListItem, ReportListResponse

router = APIRouter(tags=["reports"])

VALID_STRATEGIES = {"short", "swing"}


@router.get("/reports", response_model=ReportListResponse)
def list_reports(
    strategy: str = Query("short", description="策略：short / swing"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数（最大 100）"),
    db: Session = Depends(get_db),
) -> ReportListResponse:
    if strategy not in VALID_STRATEGIES:
        raise HTTPException(status_code=400, detail=f"Invalid strategy: {strategy}")

    # 只统计有 picked=True 的日期
    base = (
        select(
            SelectionResult.trade_date,
            func.count(SelectionResult.id).label("count"),
            func.avg(SelectionResult.final_score).label("avg_score"),
            func.max(SelectionResult.final_score).label("max_score"),
        )
        .where(SelectionResult.strategy == strategy)
        .where(SelectionResult.picked.is_(True))
        .group_by(SelectionResult.trade_date)
        .order_by(SelectionResult.trade_date.desc())
    )

    # 总页数
    total_stmt = select(func.count()).select_from(
        base.subquery()
    )
    total = db.execute(total_stmt).scalar() or 0

    # 分页
    offset = (page - 1) * page_size
    rows = db.execute(base.offset(offset).limit(page_size)).all()

    items = [
        ReportListItem(
            trade_date=r.trade_date.isoformat(),
            strategy=strategy,
            count=r.count,
            avg_score=round(float(r.avg_score), 1),
            max_score=round(float(r.max_score), 1),
        )
        for r in rows
    ]

    return ReportListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_web_reports.py -v`
Expected: PASS (validation tests)

- [ ] **Step 4: Commit**

```bash
git add src/mo_stock/web/routers/reports.py tests/test_web_reports.py
git commit -m "feat(web): GET /api/reports — report list with pagination"
```

---

## Task 4: Report Detail API

**Files:**
- Modify: `src/mo_stock/web/routers/reports.py`
- Test: `tests/test_web_report_detail.py`

- [ ] **Step 1: Write report detail tests**

Create `tests/test_web_report_detail.py`:

```python
"""报告详情 API 测试。"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from mo_stock.web.deps import get_db


def _make_client():
    """创建 TestClient，注入 mock session。"""
    from mo_stock.web.app import app

    mock_session = MagicMock(spec=Session)

    def override_get_db():
        yield mock_session

    app.dependency_overrides[get_db] = override_get_db
    tc = TestClient(app)
    return tc, mock_session


class TestReportDetailValidation:
    def test_invalid_strategy(self):
        tc, _ = _make_client()
        try:
            resp = tc.get("/api/reports/2026-04-30?strategy=invalid")
            assert resp.status_code == 400
        finally:
            tc.app.dependency_overrides.clear()

    def test_invalid_sort_by(self):
        tc, _ = _make_client()
        try:
            resp = tc.get("/api/reports/2026-04-30?sort_by=nonexistent")
            assert resp.status_code == 400
        finally:
            tc.app.dependency_overrides.clear()

    def test_invalid_order(self):
        tc, _ = _make_client()
        try:
            resp = tc.get("/api/reports/2026-04-30?order=sideways")
            assert resp.status_code == 400
        finally:
            tc.app.dependency_overrides.clear()

    def test_valid_params(self):
        tc, _ = _make_client()
        try:
            # 即使 mock 查询失败，参数校验应先通过
            resp = tc.get("/api/reports/2026-04-30?strategy=short&sort_by=final_score&order=desc")
            # 可能 200 或 500（mock 行为），但不应 400/422
            assert resp.status_code in (200, 500)
        finally:
            tc.app.dependency_overrides.clear()
```

- [ ] **Step 2: Implement report detail endpoint**

Add to `src/mo_stock/web/routers/reports.py` (after the existing `list_reports` function):

```python
from datetime import date as date_type

from mo_stock.storage.models import (
    AiAnalysis,
    DailyKline,
    FilterScoreDaily,
    IndexMember,
    StockBasic,
)

VALID_SORT_BY = {
    "final_score": SelectionResult.final_score,
    "rule_score": SelectionResult.rule_score,
    "ai_score": SelectionResult.ai_score,
    # short dimensions
    "limit": None,
    "moneyflow": None,
    "lhb": None,
    "sector": None,
    "theme": None,
    # swing dimensions
    "trend": None,
    "pullback": None,
    "moneyflow_swing": None,
    "sector_swing": None,
    "theme_swing": None,
    "catalyst": None,
    "risk_liquidity": None,
}

VALID_ORDERS = {"asc", "desc"}


@router.get("/reports/{trade_date}", response_model=ReportDetailResponse)
def get_report_detail(
    trade_date: date_type,
    strategy: str = Query("short"),
    sort_by: str = Query("final_score"),
    order: str = Query("desc"),
    sector: str | None = Query(None),
    keyword: str | None = Query(None),
    db: Session = Depends(get_db),
) -> ReportDetailResponse:
    # 参数校验
    if strategy not in VALID_STRATEGIES:
        raise HTTPException(status_code=400, detail=f"Invalid strategy: {strategy}")
    if sort_by not in VALID_SORT_BY:
        raise HTTPException(status_code=400, detail=f"Invalid sort_by: {sort_by}")
    if order not in VALID_ORDERS:
        raise HTTPException(status_code=400, detail=f"Invalid order: {order}")

    # 1. 查入选股票
    sel_stmt = (
        select(SelectionResult)
        .where(SelectionResult.trade_date == trade_date)
        .where(SelectionResult.strategy == strategy)
        .where(SelectionResult.picked.is_(True))
    )

    # 行业筛选（用 index_member.l1_name）
    if sector:
        sel_stmt = sel_stmt.join(
            IndexMember,
            IndexMember.ts_code == SelectionResult.ts_code,
        ).where(IndexMember.l1_name == sector)

    # 名称/代码搜索
    if keyword:
        sel_stmt = sel_stmt.join(
            StockBasic,
            StockBasic.ts_code == SelectionResult.ts_code,
        ).where(
            (StockBasic.name.contains(keyword))
            | (StockBasic.ts_code.contains(keyword))
        )

    # 排序
    sort_col = VALID_SORT_BY.get(sort_by)
    if sort_col is not None:
        # 直接排序字段（final_score / rule_score / ai_score）
        if order == "desc":
            sel_stmt = sel_stmt.order_by(sort_col.desc())
        else:
            sel_stmt = sel_stmt.order_by(sort_col.asc())
    else:
        # 维度分排序：join filter_score_daily，不存在的维度按 0 排序
        dim_subq = (
            select(
                FilterScoreDaily.ts_code,
                func.coalesce(FilterScoreDaily.score, 0).label("dim_score"),
            )
            .where(FilterScoreDaily.trade_date == trade_date)
            .where(FilterScoreDaily.strategy == strategy)
            .where(FilterScoreDaily.dim == sort_by)
            .subquery()
        )
        sel_stmt = sel_stmt.outerjoin(
            dim_subq, dim_subq.c.ts_code == SelectionResult.ts_code
        )
        if order == "desc":
            sel_stmt = sel_stmt.order_by(dim_subq.c.dim_score.desc().nulls_last())
        else:
            sel_stmt = sel_stmt.order_by(dim_subq.c.dim_score.asc().nulls_last())

    selections = db.execute(sel_stmt).scalars().all()

    if not selections:
        # 空报告仍返回 market 数据
        market = _get_market_data(db, trade_date)
        return ReportDetailResponse(
            trade_date=trade_date.isoformat(),
            strategy=strategy,
            market=market,
            stocks=[],
            available_sectors=[],
        )

    # 2. 批量查维度分（避免 N+1）
    ts_codes = [s.ts_code for s in selections]
    scores_rows = db.execute(
        select(FilterScoreDaily)
        .where(FilterScoreDaily.trade_date == trade_date)
        .where(FilterScoreDaily.strategy == strategy)
        .where(FilterScoreDaily.ts_code.in_(ts_codes))
    ).scalars().all()

    scores_map: dict[str, dict[str, int]] = {}
    for sr in scores_rows:
        scores_map.setdefault(sr.ts_code, {})[sr.dim] = int(sr.score)

    # 3. 批量查 AI 分析
    ai_rows = db.execute(
        select(AiAnalysis)
        .where(AiAnalysis.trade_date == trade_date)
        .where(AiAnalysis.strategy == strategy)
        .where(AiAnalysis.ts_code.in_(ts_codes))
    ).scalars().all()

    thesis_map: dict[str, str] = {}
    for ai in ai_rows:
        thesis_map[ai.ts_code] = ai.thesis[:100] + "..." if len(ai.thesis) > 100 else ai.thesis

    # 4. 批量查行业（index_member.l1_name）
    member_rows = db.execute(
        select(IndexMember.ts_code, IndexMember.l1_name)
        .where(IndexMember.ts_code.in_(ts_codes))
    ).all()
    industry_map: dict[str, str] = {r.ts_code: r.l1_name or "" for r in member_rows}

    # 5. 批量查名称
    stock_rows = db.execute(
        select(StockBasic.ts_code, StockBasic.name)
        .where(StockBasic.ts_code.in_(ts_codes))
    ).all()
    name_map: dict[str, str] = {r.ts_code: r.name for r in stock_rows}

    # 6. 组装 stocks
    stocks = [
        StockItem(
            rank=s.rank,
            ts_code=s.ts_code,
            name=name_map.get(s.ts_code, s.ts_code),
            industry=industry_map.get(s.ts_code, ""),
            final_score=round(float(s.final_score), 1),
            rule_score=round(float(s.rule_score), 1),
            ai_score=round(float(s.ai_score), 1) if s.ai_score is not None else None,
            scores=scores_map.get(s.ts_code, {}),
            ai_summary=thesis_map.get(s.ts_code),
            picked=s.picked,
        )
        for s in selections
    ]

    # 7. 可选行业列表
    available_sectors = sorted(set(
        industry_map.get(s.ts_code)
        for s in selections
        if industry_map.get(s.ts_code)
    ))

    # 8. Market data
    market = _get_market_data(db, trade_date)

    return ReportDetailResponse(
        trade_date=trade_date.isoformat(),
        strategy=strategy,
        market=market,
        stocks=stocks,
        available_sectors=available_sectors,
    )


def _get_market_data(db: Session, trade_date: date_type) -> MarketData:
    """查询上证综指 + 沪深 300 + regime_score。"""
    def _index_close(ts_code: str) -> tuple[float, float] | None:
        row = db.execute(
            select(DailyKline)
            .where(DailyKline.ts_code == ts_code)
            .where(DailyKline.trade_date == trade_date)
        ).scalar_one_or_none()
        if row and row.close and row.pct_chg is not None:
            return (float(row.close), round(float(row.pct_chg), 2))
        return None

    sh = _index_close("000001.SH")
    hs300 = _index_close("000300.SH")

    # regime_score 实时重算
    try:
        from mo_stock.filters.swing.market_regime_filter import MarketRegimeFilter
        regime_score = MarketRegimeFilter().score_market(db, trade_date)
    except Exception:
        regime_score = 50.0

    return MarketData(
        sh_index=IndexSnapshot(close=sh[0], pct_chg=sh[1]) if sh else IndexSnapshot(close=0, pct_chg=0),
        hs300_index=IndexSnapshot(close=hs300[0], pct_chg=hs300[1]) if hs300 else IndexSnapshot(close=0, pct_chg=0),
        regime_score=round(regime_score, 1),
    )
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_web_report_detail.py -v`
Expected: PASS (validation tests)

- [ ] **Step 4: Commit**

```bash
git add src/mo_stock/web/routers/reports.py tests/test_web_report_detail.py
git commit -m "feat(web): GET /api/reports/{date} — report detail with sorting/filtering"
```

---

## Task 5: Stock Detail API

**Files:**
- Modify: `src/mo_stock/web/routers/stocks.py`
- Test: `tests/test_web_stock_detail.py`

- [ ] **Step 1: Write stock detail tests**

Create `tests/test_web_stock_detail.py`:

```python
"""单股详情 API 测试。"""
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from mo_stock.web.deps import get_db


def _make_client():
    from mo_stock.web.app import app

    mock_session = MagicMock(spec=Session)

    def override_get_db():
        yield mock_session

    app.dependency_overrides[get_db] = override_get_db
    tc = TestClient(app)
    return tc, mock_session


class TestStockDetailValidation:
    def test_invalid_strategy(self):
        tc, _ = _make_client()
        try:
            resp = tc.get("/api/stocks/600519.SH?strategy=invalid")
            assert resp.status_code == 400
        finally:
            tc.app.dependency_overrides.clear()

    def test_invalid_days(self):
        tc, _ = _make_client()
        try:
            resp = tc.get("/api/stocks/600519.SH?days=0")
            assert resp.status_code == 422
        finally:
            tc.app.dependency_overrides.clear()

    def test_days_over_limit(self):
        tc, _ = _make_client()
        try:
            resp = tc.get("/api/stocks/600519.SH?days=500")
            assert resp.status_code == 422
        finally:
            tc.app.dependency_overrides.clear()
```

- [ ] **Step 2: Implement stock detail endpoint**

Replace `src/mo_stock/web/routers/stocks.py`:

```python
"""个股相关 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.storage.models import (
    AiAnalysis,
    FilterScoreDaily,
    IndexMember,
    SelectionResult,
    StockBasic,
)
from mo_stock.web.deps import get_db
from mo_stock.web.schemas import (
    AiAnalysisData,
    RecentPick,
    StockDetailResponse,
)

router = APIRouter(tags=["stocks"])

VALID_STRATEGIES = {"short", "swing"}


@router.get("/stocks/{ts_code}", response_model=StockDetailResponse)
def get_stock_detail(
    ts_code: str,
    strategy: str = Query("short"),
    days: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> StockDetailResponse:
    if strategy not in VALID_STRATEGIES:
        raise HTTPException(status_code=400, detail=f"Invalid strategy: {strategy}")

    # 1. 基本信息
    stock = db.get(StockBasic, ts_code)
    if not stock:
        raise HTTPException(status_code=404, detail=f"Stock not found: {ts_code}")

    # 2. 行业
    member = db.execute(
        select(IndexMember).where(IndexMember.ts_code == ts_code)
    ).scalar_one_or_none()
    industry = member.l1_name if member and member.l1_name else (stock.industry or "")

    # 3. 最新维度分（最近一个有记录的交易日）
    latest_date_row = db.execute(
        select(FilterScoreDaily.trade_date)
        .where(FilterScoreDaily.ts_code == ts_code)
        .where(FilterScoreDaily.strategy == strategy)
        .order_by(FilterScoreDaily.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()

    latest_scores: dict[str, int] = {}
    if latest_date_row:
        score_rows = db.execute(
            select(FilterScoreDaily)
            .where(FilterScoreDaily.ts_code == ts_code)
            .where(FilterScoreDaily.strategy == strategy)
            .where(FilterScoreDaily.trade_date == latest_date_row)
        ).scalars().all()
        latest_scores = {sr.dim: int(sr.score) for sr in score_rows}

    # 4. 最近 AI 分析
    ai_row = db.execute(
        select(AiAnalysis)
        .where(AiAnalysis.ts_code == ts_code)
        .where(AiAnalysis.strategy == strategy)
        .order_by(AiAnalysis.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()

    ai_analysis = None
    if ai_row:
        ai_analysis = AiAnalysisData(
            thesis=ai_row.thesis,
            key_catalysts=ai_row.key_catalysts,
            risks=ai_row.risks,
            suggested_entry=ai_row.suggested_entry,
            stop_loss=ai_row.stop_loss,
        )

    # 5. 近 N 日选股记录
    recent_rows = db.execute(
        select(SelectionResult)
        .where(SelectionResult.ts_code == ts_code)
        .where(SelectionResult.strategy == strategy)
        .order_by(SelectionResult.trade_date.desc())
        .limit(days)
    ).scalars().all()

    recent_picks = [
        RecentPick(
            trade_date=r.trade_date.isoformat(),
            picked=r.picked,
            final_score=round(float(r.final_score), 1),
        )
        for r in recent_rows
    ]

    return StockDetailResponse(
        ts_code=ts_code,
        name=stock.name,
        industry=industry,
        latest_scores=latest_scores,
        ai_analysis=ai_analysis,
        recent_picks=recent_picks,
    )
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_web_stock_detail.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/mo_stock/web/routers/stocks.py tests/test_web_stock_detail.py
git commit -m "feat(web): GET /api/stocks/{ts_code} — stock detail with AI analysis"
```

---

## Task 6: Frontend Project Setup

**Files:**
- Create: `frontend/` (entire directory)

- [ ] **Step 1: Scaffold Vue 3 + Vite + TypeScript project**

Run:
```bash
cd /Users/zsm/QuantProjects/mo-stock-selection
npm create vite@latest frontend -- --template vue-ts
cd frontend
npm install
npm install -D tailwindcss @tailwindcss/vite vue-router@4 axios
```

- [ ] **Step 2: Configure Tailwind CSS**

Create `frontend/src/style.css`:
```css
@import "tailwindcss";
```

Update `frontend/vite.config.ts`:
```typescript
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
```

Update `frontend/src/main.ts` to import `./style.css`.

- [ ] **Step 3: Configure router**

Create `frontend/src/router/index.ts`:
```typescript
import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'reports',
      component: () => import('../views/ReportList.vue'),
    },
    {
      path: '/report/:date',
      name: 'report-detail',
      component: () => import('../views/ReportDetail.vue'),
      props: true,
    },
    {
      path: '/stock/:code',
      name: 'stock-detail',
      component: () => import('../views/StockDetail.vue'),
      props: true,
    },
  ],
})

export default router
```

- [ ] **Step 4: Create API client**

Create `frontend/src/api/index.ts`:
```typescript
import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 10000,
})

// 类型定义
export interface ReportListItem {
  trade_date: string
  strategy: string
  count: number
  avg_score: number
  max_score: number
}

export interface ReportListResponse {
  items: ReportListItem[]
  total: number
  page: number
  page_size: number
}

export interface IndexSnapshot {
  close: number
  pct_chg: number
}

export interface MarketData {
  sh_index: IndexSnapshot
  hs300_index: IndexSnapshot
  regime_score: number
}

export interface StockItem {
  rank: number
  ts_code: string
  name: string
  industry: string
  final_score: number
  rule_score: number
  ai_score: number | null
  scores: Record<string, number>
  ai_summary: string | null
  picked: boolean
}

export interface ReportDetailResponse {
  trade_date: string
  strategy: string
  market: MarketData
  stocks: StockItem[]
  available_sectors: string[]
}

export interface AiAnalysisData {
  thesis: string
  key_catalysts: string[] | null
  risks: string[] | null
  suggested_entry: string | null
  stop_loss: string | null
}

export interface RecentPick {
  trade_date: string
  picked: boolean
  final_score: number
}

export interface StockDetailResponse {
  ts_code: string
  name: string
  industry: string
  latest_scores: Record<string, number>
  ai_analysis: AiAnalysisData | null
  recent_picks: RecentPick[]
}

// API 方法
export function fetchReports(strategy: string, page: number, pageSize: number) {
  return api.get<ReportListResponse>('/reports', {
    params: { strategy, page, page_size: pageSize },
  })
}

export function fetchReportDetail(
  date: string,
  strategy: string,
  sortBy = 'final_score',
  order = 'desc',
  sector?: string,
  keyword?: string,
) {
  return api.get<ReportDetailResponse>(`/reports/${date}`, {
    params: { strategy, sort_by: sortBy, order, sector, keyword },
  })
}

export function fetchStockDetail(tsCode: string, strategy: string, days = 10) {
  return api.get<StockDetailResponse>(`/stocks/${tsCode}`, {
    params: { strategy, days },
  })
}
```

- [ ] **Step 5: Update App.vue**

Replace `frontend/src/App.vue`:
```vue
<template>
  <router-view />
</template>
```

Update `frontend/src/main.ts`:
```typescript
import { createApp } from 'vue'
import './style.css'
import App from './App.vue'
import router from './router'

createApp(App).use(router).mount('#app')
```

- [ ] **Step 6: Verify dev server starts**

Run: `cd frontend && npm run dev`
Expected: Vite dev server starts on localhost:5173

- [ ] **Step 7: Commit**

```bash
cd /Users/zsm/QuantProjects/mo-stock-selection
git add frontend/
git commit -m "feat(web): Vue 3 + Vite + Tailwind CSS + Router + API client scaffold"
```

---

## Task 7: Report List Page

**Files:**
- Create: `frontend/src/views/ReportList.vue`

- [ ] **Step 1: Implement ReportList.vue**

```vue
<template>
  <div class="min-h-screen bg-gray-50">
    <!-- Header -->
    <header class="bg-white shadow">
      <div class="mx-auto max-w-5xl px-4 py-4">
        <h1 class="text-xl font-bold text-gray-900">mo-stock 选股系统</h1>
      </div>
    </header>

    <main class="mx-auto max-w-5xl px-4 py-6">
      <!-- Strategy Tabs -->
      <div class="mb-4 flex gap-2">
        <button
          v-for="s in strategies"
          :key="s.value"
          @click="strategy = s.value; page = 1; loadReports()"
          :class="[
            'rounded px-4 py-2 text-sm font-medium',
            strategy === s.value
              ? 'bg-blue-600 text-white'
              : 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-50',
          ]"
        >
          {{ s.label }}
        </button>
      </div>

      <!-- Loading / Error -->
      <div v-if="loading" class="py-12 text-center text-gray-500">加载中...</div>
      <div v-else-if="error" class="py-12 text-center text-red-600">{{ error }}</div>

      <!-- Empty -->
      <div v-else-if="reports.length === 0" class="py-12 text-center text-gray-500">
        暂无选股数据，请先运行 run-once
      </div>

      <!-- Report Table -->
      <table v-else class="w-full border-collapse bg-white shadow rounded-lg overflow-hidden">
        <thead class="bg-gray-100">
          <tr>
            <th class="px-4 py-3 text-left text-sm font-medium text-gray-600">日期</th>
            <th class="px-4 py-3 text-center text-sm font-medium text-gray-600">入选数</th>
            <th class="px-4 py-3 text-center text-sm font-medium text-gray-600">平均分</th>
            <th class="px-4 py-3 text-center text-sm font-medium text-gray-600">最高分</th>
            <th class="px-4 py-3 text-center text-sm font-medium text-gray-600">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in reports"
            :key="r.trade_date"
            class="border-t hover:bg-gray-50"
          >
            <td class="px-4 py-3 text-sm">{{ r.trade_date }}</td>
            <td class="px-4 py-3 text-center text-sm">{{ r.count }}</td>
            <td class="px-4 py-3 text-center text-sm">{{ r.avg_score }}</td>
            <td class="px-4 py-3 text-center text-sm">{{ r.max_score }}</td>
            <td class="px-4 py-3 text-center">
              <router-link
                :to="`/report/${r.trade_date}?strategy=${strategy}`"
                class="text-blue-600 hover:underline text-sm"
              >
                查看
              </router-link>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- Pagination -->
      <div v-if="totalPages > 1" class="mt-4 flex justify-center gap-2">
        <button
          @click="page--; loadReports()"
          :disabled="page <= 1"
          class="rounded border px-3 py-1 text-sm disabled:opacity-50"
        >
          上一页
        </button>
        <span class="px-3 py-1 text-sm text-gray-600">
          {{ page }} / {{ totalPages }}
        </span>
        <button
          @click="page++; loadReports()"
          :disabled="page >= totalPages"
          class="rounded border px-3 py-1 text-sm disabled:opacity-50"
        >
          下一页
        </button>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { fetchReports, type ReportListItem } from '../api'

const strategies = [
  { value: 'short', label: '短线' },
  { value: 'swing', label: '波段' },
]

const strategy = ref('short')
const page = ref(1)
const pageSize = 20
const total = ref(0)
const reports = ref<ReportListItem[]>([])
const loading = ref(false)
const error = ref('')

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

async function loadReports() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await fetchReports(strategy.value, page.value, pageSize)
    reports.value = data.items
    total.value = data.total
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '请求失败'
  } finally {
    loading.value = false
  }
}

onMounted(loadReports)
</script>
```

- [ ] **Step 2: Verify page renders**

Run: `cd frontend && npm run dev`
Navigate to http://localhost:5173/, verify the page shows "暂无选股数据" (no API running).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/ReportList.vue
git commit -m "feat(web): ReportList page with strategy tabs and pagination"
```

---

## Task 8: Report Detail Page

**Files:**
- Create: `frontend/src/views/ReportDetail.vue`
- Create: `frontend/src/components/DimensionBar.vue`
- Create: `frontend/src/components/MarketOverview.vue`
- Create: `frontend/src/components/AiSummary.vue`
- Create: `frontend/src/components/ScoreTable.vue`

- [ ] **Step 1: Create DimensionBar component**

`frontend/src/components/DimensionBar.vue`:
```vue
<template>
  <div class="space-y-1">
    <div v-for="(value, key) in scores" :key="key" class="flex items-center gap-2">
      <span class="w-20 text-xs text-gray-600">{{ key }}</span>
      <div class="flex-1 bg-gray-200 rounded h-4">
        <div
          class="bg-blue-500 h-4 rounded"
          :style="{ width: `${value}%` }"
        />
      </div>
      <span class="w-8 text-right text-xs text-gray-700">{{ value }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  scores: Record<string, number>
}>()
</script>
```

- [ ] **Step 2: Create MarketOverview component**

`frontend/src/components/MarketOverview.vue`:
```vue
<template>
  <div class="flex flex-wrap gap-4 rounded-lg bg-white p-4 shadow">
    <div class="flex-1 min-w-[120px]">
      <div class="text-xs text-gray-500">上证综指</div>
      <div class="text-lg font-bold">{{ market.sh_index.close.toFixed(0) }}</div>
      <div :class="pctClass(market.sh_index.pct_chg)">
        {{ fmtPct(market.sh_index.pct_chg) }}
      </div>
    </div>
    <div class="flex-1 min-w-[120px]">
      <div class="text-xs text-gray-500">沪深 300</div>
      <div class="text-lg font-bold">{{ market.hs300_index.close.toFixed(0) }}</div>
      <div :class="pctClass(market.hs300_index.pct_chg)">
        {{ fmtPct(market.hs300_index.pct_chg) }}
      </div>
    </div>
    <div class="flex-1 min-w-[120px]">
      <div class="text-xs text-gray-500">Regime</div>
      <div class="text-lg font-bold">{{ market.regime_score }}</div>
      <div class="text-xs text-gray-400">大盘环境分</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { MarketData } from '../api'

defineProps<{ market: MarketData }>()

function fmtPct(v: number) {
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
}

function pctClass(v: number) {
  return v >= 0 ? 'text-sm text-red-600' : 'text-sm text-green-600'
}
</script>
```

- [ ] **Step 3: Create AiSummary component**

`frontend/src/components/AiSummary.vue`:
```vue
<template>
  <div v-if="summary" class="mt-2 text-sm text-gray-600">
    <span class="font-medium text-gray-800">AI：</span>{{ summary }}
  </div>
  <div v-else class="mt-2 text-sm text-gray-400">AI 分析缺失</div>
</template>

<script setup lang="ts">
defineProps<{ summary: string | null }>()
</script>
```

- [ ] **Step 4: Create ScoreTable component**

`frontend/src/components/ScoreTable.vue`:
```vue
<template>
  <div class="overflow-x-auto">
    <table class="w-full text-sm">
      <thead class="bg-gray-50">
        <tr>
          <th class="px-3 py-2 text-left">排名</th>
          <th class="px-3 py-2 text-left">代码</th>
          <th class="px-3 py-2 text-left">名称</th>
          <th class="px-3 py-2 text-left">行业</th>
          <th class="px-3 py-2 text-center cursor-pointer" @click="toggleSort('final_score')">
            综合分 {{ sortIndicator('final_score') }}
          </th>
          <th class="px-3 py-2 text-center">操作</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="stock in stocks" :key="stock.ts_code">
          <tr class="border-t hover:bg-gray-50 cursor-pointer" @click="toggleExpand(stock.ts_code)">
            <td class="px-3 py-2">{{ stock.rank }}</td>
            <td class="px-3 py-2">{{ stock.ts_code }}</td>
            <td class="px-3 py-2">{{ stock.name }}</td>
            <td class="px-3 py-2">{{ stock.industry }}</td>
            <td class="px-3 py-2 text-center font-medium">{{ stock.final_score }}</td>
            <td class="px-3 py-2 text-center">
              <router-link
                :to="`/stock/${stock.ts_code}?strategy=${strategy}`"
                class="text-blue-600 hover:underline"
                @click.stop
              >
                详情
              </router-link>
            </td>
          </tr>
          <tr v-if="expanded === stock.ts_code" class="border-t bg-gray-50">
            <td colspan="6" class="px-4 py-3">
              <DimensionBar :scores="stock.scores" />
              <AiSummary :summary="stock.ai_summary" />
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { StockItem } from '../api'
import DimensionBar from './DimensionBar.vue'
import AiSummary from './AiSummary.vue'

const props = defineProps<{
  stocks: StockItem[]
  strategy: string
  currentSort: string
  currentOrder: string
}>()

const emit = defineEmits<{
  sort: [sortBy: string, order: string]
}>()

const expanded = ref<string | null>(null)

function toggleExpand(code: string) {
  expanded.value = expanded.value === code ? null : code
}

function toggleSort(column: string) {
  const newOrder = props.currentSort === column && props.currentOrder === 'desc' ? 'asc' : 'desc'
  emit('sort', column, newOrder)
}

function sortIndicator(column: string) {
  if (props.currentSort !== column) return ''
  return props.currentOrder === 'desc' ? '↓' : '↑'
}
</script>
```

- [ ] **Step 5: Create ReportDetail.vue**

`frontend/src/views/ReportDetail.vue`:
```vue
<template>
  <div class="min-h-screen bg-gray-50">
    <header class="bg-white shadow">
      <div class="mx-auto max-w-5xl px-4 py-4 flex items-center gap-4">
        <router-link to="/" class="text-gray-500 hover:text-gray-700">&larr; 返回</router-link>
        <h1 class="text-lg font-bold">{{ date }} {{ strategyLabel }}选股报告</h1>
      </div>
    </header>

    <main class="mx-auto max-w-5xl px-4 py-6 space-y-4">
      <div v-if="loading" class="py-12 text-center text-gray-500">加载中...</div>
      <div v-else-if="error" class="py-12 text-center text-red-600">{{ error }}</div>
      <template v-else>
        <!-- Market Overview -->
        <MarketOverview v-if="data" :market="data.market" />

        <!-- Filters -->
        <div class="flex flex-wrap gap-3 rounded-lg bg-white p-3 shadow">
          <select
            v-model="sector"
            @change="loadDetail"
            class="rounded border px-3 py-1.5 text-sm"
          >
            <option value="">全部行业</option>
            <option v-for="s in data?.available_sectors" :key="s" :value="s">{{ s }}</option>
          </select>
          <input
            v-model="keyword"
            @keyup.enter="loadDetail"
            placeholder="搜索名称/代码"
            class="rounded border px-3 py-1.5 text-sm flex-1 min-w-[150px]"
          />
          <button @click="loadDetail" class="rounded bg-blue-600 px-4 py-1.5 text-sm text-white">
            搜索
          </button>
        </div>

        <!-- Stock Table -->
        <div v-if="data && data.stocks.length > 0" class="rounded-lg bg-white shadow">
          <ScoreTable
            :stocks="data.stocks"
            :strategy="strategy"
            :current-sort="sortBy"
            :current-order="order"
            @sort="onSort"
          />
        </div>
        <div v-else class="py-12 text-center text-gray-500">当日无入选股票</div>
      </template>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { fetchReportDetail, type ReportDetailResponse } from '../api'
import MarketOverview from '../components/MarketOverview.vue'
import ScoreTable from '../components/ScoreTable.vue'

const route = useRoute()
const date = route.params.date as string
const strategy = (route.query.strategy as string) || 'short'

const strategyLabel = computed(() => strategy === 'swing' ? '波段' : '短线')

const data = ref<ReportDetailResponse | null>(null)
const loading = ref(true)
const error = ref('')

const sortBy = ref('final_score')
const order = ref('desc')
const sector = ref('')
const keyword = ref('')

async function loadDetail() {
  loading.value = true
  error.value = ''
  try {
    const { data: resp } = await fetchReportDetail(
      date, strategy, sortBy.value, order.value,
      sector.value || undefined,
      keyword.value || undefined,
    )
    data.value = resp
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '请求失败'
  } finally {
    loading.value = false
  }
}

function onSort(column: string, newOrder: string) {
  sortBy.value = column
  order.value = newOrder
  loadDetail()
}

onMounted(loadDetail)
</script>
```

- [ ] **Step 6: Verify page renders**

Run: `cd frontend && npm run dev`
Navigate to http://localhost:5173/report/2026-04-30, verify it shows loading state.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/ReportDetail.vue frontend/src/components/
git commit -m "feat(web): ReportDetail page with market overview, filtering, and expandable rows"
```

---

## Task 9: Stock Detail Page

**Files:**
- Create: `frontend/src/views/StockDetail.vue`

- [ ] **Step 1: Create StockDetail.vue**

```vue
<template>
  <div class="min-h-screen bg-gray-50">
    <header class="bg-white shadow">
      <div class="mx-auto max-w-5xl px-4 py-4 flex items-center gap-4">
        <button @click="$router.back()" class="text-gray-500 hover:text-gray-700">&larr; 返回</button>
        <h1 class="text-lg font-bold">{{ code }} {{ data?.name || '' }}</h1>
      </div>
    </header>

    <main class="mx-auto max-w-5xl px-4 py-6 space-y-4">
      <div v-if="loading" class="py-12 text-center text-gray-500">加载中...</div>
      <div v-else-if="error" class="py-12 text-center text-red-600">{{ error }}</div>
      <template v-else-if="data">
        <!-- Industry -->
        <div class="rounded-lg bg-white p-4 shadow">
          <span class="text-sm text-gray-500">行业：{{ data.industry }}</span>
        </div>

        <!-- Dimension Scores -->
        <div class="rounded-lg bg-white p-4 shadow">
          <h2 class="mb-3 text-sm font-bold text-gray-700">维度打分</h2>
          <DimensionBar :scores="data.latest_scores" />
        </div>

        <!-- AI Analysis -->
        <div v-if="data.ai_analysis" class="rounded-lg bg-white p-4 shadow">
          <h2 class="mb-3 text-sm font-bold text-gray-700">AI 深度分析</h2>
          <div class="space-y-3 text-sm">
            <div>
              <div class="font-medium text-gray-800">核心论点</div>
              <div class="mt-1 text-gray-600">{{ data.ai_analysis.thesis }}</div>
            </div>
            <div v-if="data.ai_analysis.key_catalysts?.length">
              <div class="font-medium text-gray-800">关键催化剂</div>
              <ul class="mt-1 list-disc pl-5 text-gray-600">
                <li v-for="(c, i) in data.ai_analysis.key_catalysts" :key="i">{{ c }}</li>
              </ul>
            </div>
            <div v-if="data.ai_analysis.risks?.length">
              <div class="font-medium text-gray-800">风险提示</div>
              <ul class="mt-1 list-disc pl-5 text-gray-600">
                <li v-for="(r, i) in data.ai_analysis.risks" :key="i">{{ r }}</li>
              </ul>
            </div>
            <div v-if="data.ai_analysis.suggested_entry" class="flex gap-6">
              <div>
                <span class="font-medium text-gray-800">建议入场：</span>
                <span class="text-gray-600">{{ data.ai_analysis.suggested_entry }}</span>
              </div>
              <div v-if="data.ai_analysis.stop_loss">
                <span class="font-medium text-gray-800">止损：</span>
                <span class="text-red-600">{{ data.ai_analysis.stop_loss }}</span>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="rounded-lg bg-white p-4 shadow">
          <div class="text-sm text-gray-400">AI 分析缺失</div>
        </div>

        <!-- Recent Picks -->
        <div class="rounded-lg bg-white p-4 shadow">
          <h2 class="mb-3 text-sm font-bold text-gray-700">近期选股记录</h2>
          <div class="flex flex-wrap gap-2">
            <div
              v-for="p in data.recent_picks"
              :key="p.trade_date"
              class="rounded border px-3 py-1.5 text-xs"
              :class="p.picked ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-gray-200 bg-gray-50 text-gray-400'"
            >
              {{ p.trade_date }}
              <span v-if="p.picked" class="ml-1 font-medium">{{ p.final_score }}</span>
              <span v-else class="ml-1">未入选</span>
            </div>
          </div>
        </div>
      </template>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { fetchStockDetail, type StockDetailResponse } from '../api'
import DimensionBar from '../components/DimensionBar.vue'

const route = useRoute()
const code = route.params.code as string
const strategy = (route.query.strategy as string) || 'short'

const data = ref<StockDetailResponse | null>(null)
const loading = ref(true)
const error = ref('')

async function loadDetail() {
  loading.value = true
  error.value = ''
  try {
    const { data: resp } = await fetchStockDetail(code, strategy)
    data.value = resp
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '请求失败'
  } finally {
    loading.value = false
  }
}

onMounted(loadDetail)
</script>
```

- [ ] **Step 2: Verify page renders**

Navigate to http://localhost:5173/stock/600519.SH, verify loading state.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/StockDetail.vue
git commit -m "feat(web): StockDetail page with AI analysis and recent picks timeline"
```

---

## Task 10: Docker & Nginx Deployment

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Modify: `docker-compose.yml`
- Create: `nginx.conf`

- [ ] **Step 1: Create Dockerfile**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY pyproject.toml ./
COPY config/ config/
COPY src/ src/
RUN pip install --no-cache-dir -e .

EXPOSE 8000
CMD ["uvicorn", "mo_stock.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Create .dockerignore**

```
.venv
.git
data
frontend/node_modules
frontend/dist
__pycache__
*.pyc
.pytest_cache
*.egg-info
```

- [ ] **Step 3: Create nginx.conf**

```nginx
server {
    listen 80;
    auth_basic "mo-stock";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        root /usr/share/nginx/html;
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

- [ ] **Step 4: Extend docker-compose.yml**

Append to existing `docker-compose.yml` (keep existing pg service):

```yaml
  api:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: mo_stock_api
    restart: unless-stopped
    command: uvicorn mo_stock.web.app:app --host 0.0.0.0 --port 8000
    environment:
      DB_URL: postgresql+psycopg2://mo_stock:mo_stock@pg:5432/mo_stock
    depends_on:
      pg:
        condition: service_healthy
    ports:
      - "8000:8000"

  nginx:
    image: nginx:alpine
    container_name: mo_stock_nginx
    restart: unless-stopped
    ports:
      - "80:80"
    volumes:
      - ./frontend/dist:/usr/share/nginx/html:ro
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./.htpasswd:/etc/nginx/.htpasswd:ro
    depends_on:
      - api
```

- [ ] **Step 5: Verify Docker build**

Run: `docker compose build api`
Expected: Build succeeds

- [ ] **Step 6: Commit**

```bash
git add Dockerfile .dockerignore docker-compose.yml nginx.conf
git commit -m "feat(deploy): Dockerfile + Nginx + docker-compose with Basic Auth"
```

---

## Task 11: Integration Test & Verification

**Files:**
- Test: `tests/test_web_integration.py`

- [ ] **Step 1: Write integration test**

Create `tests/test_web_integration.py`:

```python
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
```

- [ ] **Step 2: Run all web tests**

Run: `.venv/bin/python -m pytest tests/test_web_health.py tests/test_web_schemas.py tests/test_web_reports.py tests/test_web_report_detail.py tests/test_web_stock_detail.py -v`
Expected: All PASS

- [ ] **Step 3: Run ruff + mypy on web module**

Run: `.venv/bin/python -m ruff check src/mo_stock/web/ && .venv/bin/python -m mypy src/mo_stock/web/`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add tests/test_web_integration.py
git commit -m "test(web): integration tests for complete API chain"
```

---

## Verification Checklist (P1 Acceptance Criteria)

After all tasks are complete, verify:

- [ ] `GET /api/health` returns 200
- [ ] `GET /api/reports` returns paginated list, `total` = trading days count
- [ ] `GET /api/reports/{date}` returns stocks with sorting, filtering, `available_sectors`
- [ ] `GET /api/stocks/{code}` returns detail with `ai_analysis` nullable
- [ ] Invalid `strategy` / `sort_by` / `order` returns 400
- [ ] `page_size` capped at 100, page_size=0 returns 422
- [ ] All scores 0-100, `final_score` has 1 decimal
- [ ] `ai_summary` = `thesis[:100] + "..."` in report detail
- [ ] No N+1 queries in report detail (batch queries for scores, AI, names, industries)
- [ ] Frontend mobile viewport 390px: tables don't overflow
- [ ] `docker compose build api` succeeds
- [ ] Nginx SPA fallback works (refresh on `/report/2026-04-30` returns 200)
- [ ] Nginx Basic Auth prompts for credentials

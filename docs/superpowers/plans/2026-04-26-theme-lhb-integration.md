# Theme And LHB Integration Implementation Plan (v2.1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **v2 修订**：基于 [docs/audit-2026-04-26.md](../../audit-2026-04-26.md) 反思，针对 v1 的字段一致性、维度饱和、表结构选择、席位匹配鲁棒性等问题做系统性返工。详情见 §0.2。
>
> **v2.1 审阅修订**：补齐执行前风险修正：`ThemeFilter` 不再依赖 `ths_daily` 必须存在；`lhb_seat_detail` 主键改为稳定席位键；Windows/PowerShell 命令可直接执行；删除默认 `git push main`；补齐 CLI/scheduler 参数闭环、SQLite 测试策略与占位测试。

**Goal:** 把 Tushare 已有的题材/概念强度（`ths_daily` / `limit_cpt_list` / `moneyflow_cnt_ths`）与龙虎榜席位拆解（`top_inst` / `hm_list` / `hm_detail`）接入选股系统，让"行业强势"和"题材强势"成为**两个独立维度**，让 `LhbFilter` 从"净买入汇总"升级为"基础信号 + 资金身份结构"双层判断。

**Architecture:** 继续保持现有分层：`TushareClient` 只封装接口，`DailyIngestor` 负责清洗并 upsert，`storage.models/repo` 提供结构化表和读写方法，`filters` 只读数据库并输出 0-100 规则分。新增 `ThemeFilter` 与 `sector` 平级，独立持有 `dimension_weights.theme` 权重。LHB 席位明细从 JSONB 升级为独立表 `lhb_seat_detail`，便于 SQL 查询与历史画像。

**Tech Stack:** Python 3.12, Tushare Pro SDK, SQLAlchemy 2.x, PostgreSQL JSONB + 普通表, Alembic, pytest, ruff, mypy.

---

## 0. Background

### 0.1 Current Facts

- Tushare 题材接口：
  - `ths_index` / `ths_member`：项目已封装，可通过 `refresh-basics --with-ths` 入库
  - `ths_daily` / `moneyflow_cnt_ths` / `limit_cpt_list`：项目未封装
- Tushare 龙虎榜接口：
  - `top_list`：项目已接入 `lhb` 表
  - `top_inst`：项目客户端已封装，**采集层未落库**
  - `hm_list` / `hm_detail`：项目未封装
- 项目约定：
  - Tushare 账号 10000 积分（`CLAUDE.md` 一类约定）
  - 项目内置 [vendor/mo-skills/tushare-skills/references/数据接口.md](../../../vendor/mo-skills/tushare-skills/references/数据接口.md)，**接入新接口前先查这里确认参数与字段**
  - 不使用 git worktree，直接 main 分支开发
- 当前限制：
  - `SectorFilter` 只用申万一级行业，文件 docstring 写明"题材增强 P1 不实现"
  - `LhbFilter` 只读 `top_list` 汇总数据；`Lhb.seat` JSONB 字段当前永远 `None`（无消费方）

### 0.2 Why v2 Differs From v1

v1 计划经评审发现 4 大类问题，v2 系统性修正：

| v1 问题 | v2 修法 |
|--------|---------|
| `moneyflow_cnt_ths` fields 与表字段不一致 | 表与 fields 完全对齐，3 个净额字段都入库 |
| `limit_cpt_list` 积分凭印象写 8000 | 不假定积分；Task 1 增加积分实测步骤，结果回写项目内 `docs/tushare-interface-check.md` |
| **题材加分塞进 sector 维度后会触顶（多数股满分 100）** | 新增独立 `theme` 维度，`weights.yaml` 加 `theme: 0.10`，固定分母 1.00 不变 |
| **`Lhb.seat` JSONB 不利于 SQL 查询与历史画像** | 改建独立表 `lhb_seat_detail` (PK=trade_date, ts_code, seat_key)；JSONB 字段在 alembic migration 中 drop |
| `LhbFilter` 现有 base 已经 0-100，加 seat 后量纲不平衡 | LhbFilter 重排：base 60 + seat 40，机构净买给到 +20（v1 仅 +12） |
| 游资匹配用子串 `in` 易误判 | ingest 时把 `hm_list.orgs` 拆成 set，匹配用完全相等 |
| `quant_like` 启发式过粗（"华鑫证券"全打标会误报） | **删除** quant_like 分类，等有行为画像数据再加 |
| `ingest_one_day` 8 个步骤一损俱损 | 拆 CORE / ENHANCED 两组，CLI 加 `--skip-enhanced` |
| Alembic migration 仅"建议使用"未列步骤 | Task 7 强制人工 review autogenerate 输出后再 upgrade |
| 接受标准缺性能边界 | §6 加 run-once 耗时增量、各 filter 单次执行时间上限 |

---

## 1. File Structure

### Create

- `src/mo_stock/filters/theme_filter.py`
  新独立维度，**与 sector 平级**
- `tests/unit/test_theme_filter.py`
- `tests/unit/test_theme_ingest.py`
  `_ths_daily_rows_from_df` / `_limit_concept_rows_from_df` / `_concept_moneyflow_rows_from_df`
- `tests/unit/test_lhb_seat.py`
  `_top_inst_rows_from_df` / `_classify_seat` / `_build_hot_money_orgs_set` / `_seat_structure_score`
- `alembic/versions/{rev}_theme_lhb_integration.py`（autogenerate 后人工修订）

### Modify

- `src/mo_stock/data_sources/tushare_client.py` — 新增 5 个方法
- `src/mo_stock/storage/models.py` — 新增 6 张表，drop `Lhb.seat`
- `src/mo_stock/storage/repo.py` — 新增 upsert + read helpers
- `src/mo_stock/ingest/ingest_daily.py` — 新增 ingest 方法 + CORE/ENHANCED 分组
- `src/mo_stock/filters/sector_filter.py` — **保持不变**（题材搬到 ThemeFilter）
- `src/mo_stock/filters/lhb_filter.py` — base 重排 + seat_structure 新逻辑
- `src/mo_stock/cli.py` — `run-once` / `scheduler` 加 `--skip-enhanced`
- `src/mo_stock/scheduler/daily_job.py` — 通过参数或环境变量把 `skip_enhanced` 传入每日任务
- `config/weights.yaml` — 新增 `dimension_weights.theme` + 各 filter 子参数
- `docs/scoring.md` — 重写第 5/6 章
- `docs/cli.md` — 增加题材增强章节
- `docs/tushare-interface-check.md` — 记录新增接口实测积分/权限/返回字段
- `CLAUDE.md` — 维度速查表更新（5 维 → 6 维）

---

## 2. Data Model Design

### 2.1 Theme Tables (3)

`ThsDaily`（同花顺概念/行业指数日行情）：

```python
class ThsDaily(Base):
    """同花顺概念/行业指数日行情（Tushare ths_daily）。"""

    __tablename__ = "ths_daily"

    ts_code: Mapped[str] = mapped_column(String(20), comment="同花顺板块代码，如 885806.TI")
    trade_date: Mapped[date] = mapped_column(Date, comment="交易日")
    name: Mapped[str | None] = mapped_column(String(50), comment="板块名称（冗余 ths_index）")
    close: Mapped[float | None] = mapped_column(Float, comment="收盘点位")
    open: Mapped[float | None] = mapped_column(Float, comment="开盘点位")
    high: Mapped[float | None] = mapped_column(Float, comment="最高")
    low: Mapped[float | None] = mapped_column(Float, comment="最低")
    pre_close: Mapped[float | None] = mapped_column(Float, comment="昨收")
    avg_price: Mapped[float | None] = mapped_column(Float, comment="平均价")
    change: Mapped[float | None] = mapped_column(Float, comment="涨跌额")
    pct_change: Mapped[float | None] = mapped_column(Float, comment="涨跌幅（%）")
    vol: Mapped[float | None] = mapped_column(Float, comment="成交量")
    turnover_rate: Mapped[float | None] = mapped_column(Float, comment="换手率（%）")
    total_mv: Mapped[float | None] = mapped_column(Float, comment="总市值")
    float_mv: Mapped[float | None] = mapped_column(Float, comment="流通市值")

    __table_args__ = (
        PrimaryKeyConstraint("ts_code", "trade_date"),
        Index("ix_ths_daily_date_pct", "trade_date", "pct_change"),
        {"comment": "同花顺概念/行业指数日行情，ThemeFilter 强度输入"},
    )
```

`LimitConceptDaily`（涨停最强概念板块）：

```python
class LimitConceptDaily(Base):
    """涨停最强概念板块统计（Tushare limit_cpt_list）。"""

    __tablename__ = "limit_concept_daily"

    ts_code: Mapped[str] = mapped_column(String(20), comment="板块代码")
    trade_date: Mapped[date] = mapped_column(Date, comment="交易日")
    name: Mapped[str | None] = mapped_column(String(50), comment="板块名称")
    days: Mapped[int | None] = mapped_column(Integer, comment="上榜天数")
    up_stat: Mapped[str | None] = mapped_column(String(50), comment="连板高度描述")
    cons_nums: Mapped[int | None] = mapped_column(Integer, comment="连板家数")
    up_nums: Mapped[int | None] = mapped_column(Integer, comment="涨停家数")
    pct_chg: Mapped[float | None] = mapped_column(Float, comment="概念涨跌幅（%）")
    rank: Mapped[int | None] = mapped_column(Integer, comment="热点排名，1 最强")

    __table_args__ = (
        PrimaryKeyConstraint("ts_code", "trade_date"),
        Index("ix_limit_concept_date_rank", "trade_date", "rank"),
        {"comment": "每日涨停最强概念板块，短线题材热度输入"},
    )
```

`ThsConceptMoneyflow`（同花顺概念资金流，**字段与 fields 完全对齐**）：

```python
class ThsConceptMoneyflow(Base):
    """同花顺概念板块每日资金流向（Tushare moneyflow_cnt_ths）。"""

    __tablename__ = "ths_concept_moneyflow"

    ts_code: Mapped[str] = mapped_column(String(20), comment="概念板块代码")
    trade_date: Mapped[date] = mapped_column(Date, comment="交易日")
    name: Mapped[str | None] = mapped_column(String(50), comment="板块名称")
    lead_stock: Mapped[str | None] = mapped_column(String(50), comment="领涨股票名称")
    pct_change: Mapped[float | None] = mapped_column(Float, comment="板块涨跌幅（%）")
    company_num: Mapped[int | None] = mapped_column(Integer, comment="成分公司数量")
    pct_change_stock: Mapped[float | None] = mapped_column(Float, comment="领涨股涨跌幅（%）")
    net_buy_amount: Mapped[float | None] = mapped_column(Float, comment="买入额（亿元）")
    net_sell_amount: Mapped[float | None] = mapped_column(Float, comment="卖出额（亿元）")
    net_amount: Mapped[float | None] = mapped_column(Float, comment="净流入额（亿元）")

    __table_args__ = (
        PrimaryKeyConstraint("ts_code", "trade_date"),
        Index("ix_concept_moneyflow_date_net", "trade_date", "net_amount"),
        {"comment": "概念板块资金流向，ThemeFilter 资金确认输入"},
    )
```

### 2.2 LHB Seat Tables (3)

`HotMoneyList`：

```python
class HotMoneyList(Base):
    """游资名录（Tushare hm_list）。"""

    __tablename__ = "hot_money_list"

    name: Mapped[str] = mapped_column(String(100), primary_key=True, comment="游资名称（Tushare 主键）")
    desc: Mapped[str | None] = mapped_column(Text, comment="游资风格说明")
    orgs: Mapped[str | None] = mapped_column(Text, comment="关联营业部，分号/逗号分隔的原始字符串")

    __table_args__ = ({"comment": "Tushare 游资名录，用于龙虎榜席位身份识别"},)
```

`HotMoneyDetail`：

```python
class HotMoneyDetail(Base):
    """游资每日交易明细（Tushare hm_detail）。"""

    __tablename__ = "hot_money_detail"

    trade_date: Mapped[date] = mapped_column(Date, comment="交易日")
    ts_code: Mapped[str] = mapped_column(String(12), comment="股票代码")
    hm_name: Mapped[str] = mapped_column(String(100), comment="游资名称")
    ts_name: Mapped[str | None] = mapped_column(String(50), comment="股票名称")
    buy_amount: Mapped[float | None] = mapped_column(Float, comment="买入金额（元）")
    sell_amount: Mapped[float | None] = mapped_column(Float, comment="卖出金额（元）")
    net_amount: Mapped[float | None] = mapped_column(Float, comment="净买卖金额（元）")
    hm_orgs: Mapped[str | None] = mapped_column(Text, comment="关联营业部，从 hm_list 冗余")
    tag: Mapped[str | None] = mapped_column(String(50), comment="标签（Tushare 原始）")

    __table_args__ = (
        PrimaryKeyConstraint("trade_date", "ts_code", "hm_name"),
        Index("ix_hot_money_detail_date", "trade_date"),
        Index("ix_hot_money_detail_hm", "hm_name", "trade_date"),
        {"comment": "每日游资交易明细，LhbFilter 席位身份加权辅助"},
    )
```

`LhbSeatDetail`（**新独立表，替代 Lhb.seat JSONB**）：

```python
class LhbSeatDetail(Base):
    """龙虎榜席位明细（Tushare top_inst）。

    替代 Lhb.seat JSONB，便于 SQL 直接过滤"今天机构净买 > N 的票"。
    """

    __tablename__ = "lhb_seat_detail"

    trade_date: Mapped[date] = mapped_column(Date, comment="交易日")
    ts_code: Mapped[str] = mapped_column(String(12), comment="股票代码")
    seat_key: Mapped[str] = mapped_column(
        String(64),
        comment="稳定席位键：sha1(ts_code|exalter|side|reason)，避免重跑时 top_inst 返回顺序变化导致覆盖错行",
    )
    seat_no: Mapped[int] = mapped_column(
        Integer,
        comment="展示序号 1-N，按稳定排序（reason,side,exalter,buy,sell,net_buy）后生成。"
                "**消费方排序请用 seat_no**——seat_key 是 sha1 hash，顺序无业务含义",
    )
    exalter: Mapped[str | None] = mapped_column(String(200), comment="席位/营业部名称")
    side: Mapped[str | None] = mapped_column(String(2), comment="0=买榜 / 1=卖榜（仅指榜单位置）")
    buy: Mapped[float | None] = mapped_column(Float, comment="买入金额（元）")
    sell: Mapped[float | None] = mapped_column(Float, comment="卖出金额（元）")
    net_buy: Mapped[float | None] = mapped_column(Float, comment="净买卖金额（元）")
    reason: Mapped[str | None] = mapped_column(String(100), comment="上榜原因")
    seat_type: Mapped[str] = mapped_column(
        String(20),
        comment="ingest 时分类：institution / northbound / hot_money / other",
    )

    __table_args__ = (
        PrimaryKeyConstraint("trade_date", "ts_code", "seat_key"),
        Index("ix_lhb_seat_date_type", "trade_date", "seat_type"),
        Index("ix_lhb_seat_date_code", "trade_date", "ts_code"),
        {"comment": "龙虎榜席位明细，LhbFilter 席位结构打分输入"},
    )
```

### 2.3 Lhb 表 JSONB 字段处理

- `Lhb.seat` JSONB 字段在 alembic migration 中 **DROP**（当前永远 None，没有消费方，不会破坏数据）
- `LhbFilter` 改读 `lhb_seat_detail` 表

---

## 3. Scoring Design

### 3.1 维度拆分总览

**关键变化**：sector 维度只管申万一级行业，**新增独立 `theme` 维度**承载 THS 概念 / 涨停最强概念 / 概念资金流。`weights.yaml` 增加 `dimension_weights.theme`：

```yaml
dimension_weights:
  limit:     0.25
  moneyflow: 0.25
  lhb:       0.20
  sector:    0.10   # 原 0.15 拆出 0.05 给 theme
  theme:     0.10   # 新增
  sentiment: 0.10
# 加和 = 1.00，combine 固定分母不变
```

固定分母 = 1.00（公式不变，见 [docs/scoring.md](../../scoring.md)）。**新增维度后无需改 `_weighted_combine` 逻辑**——天然兼容。

### 3.2 SectorFilter（保持不变）

继承现有实现：`rank_bonus(0-70) + trend_bonus(0-30) = 0-100`。

### 3.3 ThemeFilter（新维度，0-100）

```python
theme_score = ths_rank_bonus + limit_concept_bonus + concept_moneyflow_bonus
final = min(100, theme_score)   # 三类信号都极强时不会爆超 100
```

默认分档：

```yaml
theme_filter:
  top_n_themes: 10
  ths_rank_bonus:
    1: 50
    2: 42
    3: 35
    4: 28
    5: 22
    10: 12
  limit_concept_rank_bonus:
    1: 50
    3: 35
    5: 22
    10: 12
  concept_moneyflow_positive_bonus: 15  # net_amount > 0 时加 15
  max_theme_bonus: 100  # 上限即满分（与其它维度对齐）
```

**多概念股取最大概念加分（不累加）**：

```python
best = 0
for concept in stock_to_concepts.get(ts_code, []):
    s = (
        ths_rank_bonus_table.get(theme_rank.get(concept, 999), 0)
        + limit_concept_bonus_table.get(limit_rank.get(concept, 999), 0)
        + (concept_moneyflow_bonus if concept_moneyflow.get(concept, 0) > 0 else 0)
    )
    best = max(best, s)
score = min(100, best)
```

**为何取 max 而非 sum**：避免"沾边股"（一只票挂在 10 个概念里，每个都加分）天然占优。这是经验取舍，写在 docstring 里供未来回测验证。

### 3.4 LhbFilter（重排，base 60 + seat 40）

当前 LhbFilter `base = 30 + tier(0-30) + purity(0-25) + reason(0-15)` → 0-100。
**v2 重排**让 base 留出 40 分给 seat：

```yaml
lhb_filter:
  # base 部分上限 60
  base_score: 20            # 上榜+净买入基础分（原 30）
  net_rate_tier_bonus:      # 上限 20（原 30）
    rate_2_pct: 10
    rate_5_pct: 15
    rate_10_pct: 20
  purity_bonus:             # 上限 12（原 25）
    amount_rate_15_pct: 6
    amount_rate_30_pct: 12
  reason_bonus:             # 上限 8（原 15）
    "连续三日涨幅": 8
    "无价格涨跌幅限制": 5
    "日涨幅偏离": 5
    "日换手率": 5
  # seat 部分上限 40（新增）
  institution:
    min_net_buy_yuan: 10000000   # ≥ 1000 万
    bonus: 20
  hot_money_buy:
    min_net_buy_yuan: 5000000    # ≥ 500 万
    bonus: 12
  hot_money_sell:                # 知名游资大额卖出 → 扣分（量化次日反向风险）
    min_net_sell_yuan: 10000000
    penalty: 15
  northbound_buy:
    min_net_buy_yuan: 30000000   # 北向 ≥ 3000 万
    bonus: 8
```

`seat_score` 计算：

```python
def _seat_structure_score(seats: list[LhbSeatDetail], cfg: dict) -> tuple[float, dict]:
    """seat 部分上限 40。institution/hot_money/northbound 加分可叠加。"""
    if not seats:
        return 0.0, {}

    inst_net = sum((s.net_buy or 0) for s in seats if s.seat_type == "institution")
    hot_net  = sum((s.net_buy or 0) for s in seats if s.seat_type == "hot_money")
    nb_net   = sum((s.net_buy or 0) for s in seats if s.seat_type == "northbound")

    score = 0.0
    detail: dict[str, Any] = {}

    if inst_net >= cfg["institution"]["min_net_buy_yuan"]:
        score += cfg["institution"]["bonus"]
        detail["institution_net_buy"] = round(inst_net, 0)

    if hot_net >= cfg["hot_money_buy"]["min_net_buy_yuan"]:
        score += cfg["hot_money_buy"]["bonus"]
        detail["hot_money_net_buy"] = round(hot_net, 0)

    if hot_net <= -cfg["hot_money_sell"]["min_net_sell_yuan"]:
        score -= cfg["hot_money_sell"]["penalty"]
        detail["hot_money_sell_penalty"] = -cfg["hot_money_sell"]["penalty"]

    if nb_net >= cfg["northbound_buy"]["min_net_buy_yuan"]:
        score += cfg["northbound_buy"]["bonus"]
        detail["northbound_net_buy"] = round(nb_net, 0)

    # 上限 40，下限 -15（仅 hot_money_sell 一项可扣，且 base ≥ 20 不会负总分）
    score = max(-15.0, min(40.0, score))
    return score, detail
```

席位分类（**无 quant_like，避免误报**）：

```python
def _classify_seat(exalter: str | None, hot_money_orgs: set[str]) -> str:
    name = (exalter or "").strip()
    if not name:
        return "other"
    if "机构专用" in name:
        return "institution"
    if "沪股通专用" in name or "深股通专用" in name:
        return "northbound"
    if name in hot_money_orgs:   # 完全相等匹配
        return "hot_money"
    return "other"


def _build_hot_money_orgs_set(session: Session) -> set[str]:
    """从 hm_list.orgs 拆分号/逗号 → set[str]。

    hm_list.orgs 格式如 "中信证券上海溧阳路营业部, 华泰证券深圳益田路营业部"，
    用完全相等匹配避免子串误判（如 "中信证券" 命中所有中信席位）。
    """
    import re
    orgs: set[str] = set()
    for hm in session.query(HotMoneyList).all():
        if not hm.orgs:
            continue
        for part in re.split(r"[,;，；]", hm.orgs):
            stripped = part.strip()
            if stripped:
                orgs.add(stripped)
    return orgs
```

---

## 4. Tasks

### Task 1: Tushare Client + 积分实测

**Files:**
- Modify: `src/mo_stock/data_sources/tushare_client.py`
- Test: `tests/unit/test_tushare_client.py`
- Create/Modify: `docs/tushare-interface-check.md`（记录新增接口实测积分、权限、返回字段；不写用户私有 `.claude` 路径）

**Step 1: 写失败测试，断言 5 个新方法的 API name + 关键 fields**

```python
def test_ths_daily_calls_tushare_api(monkeypatch):
    client = TushareClient.__new__(TushareClient)
    calls = []
    monkeypatch.setattr(client, "_call", lambda api, **kw: calls.append((api, kw)) or pd.DataFrame())
    client.ths_daily(trade_date="20260424")
    assert calls[0][0] == "ths_daily"
    assert calls[0][1]["trade_date"] == "20260424"
    assert "pct_change" in calls[0][1]["fields"]
    assert "turnover_rate" in calls[0][1]["fields"]


def test_limit_cpt_list_calls_tushare_api(monkeypatch):
    client = TushareClient.__new__(TushareClient)
    calls = []
    monkeypatch.setattr(client, "_call", lambda api, **kw: calls.append((api, kw)) or pd.DataFrame())
    client.limit_cpt_list(trade_date="20260424")
    assert calls[0][0] == "limit_cpt_list"
    assert calls[0][1]["trade_date"] == "20260424"
    assert "up_nums" in calls[0][1]["fields"]
    assert "rank" in calls[0][1]["fields"]

def test_moneyflow_cnt_ths_calls_tushare_api(monkeypatch):
    client = TushareClient.__new__(TushareClient)
    calls = []
    monkeypatch.setattr(client, "_call", lambda api, **kw: calls.append((api, kw)) or pd.DataFrame())
    client.moneyflow_cnt_ths(trade_date="20260424")
    assert calls[0][0] == "moneyflow_cnt_ths"
    assert calls[0][1]["trade_date"] == "20260424"
    assert "net_amount" in calls[0][1]["fields"]
    assert "net_buy_amount" in calls[0][1]["fields"]
    assert "net_sell_amount" in calls[0][1]["fields"]

def test_hm_list_calls_tushare_api(monkeypatch):
    client = TushareClient.__new__(TushareClient)
    calls = []
    monkeypatch.setattr(client, "_call", lambda api, **kw: calls.append((api, kw)) or pd.DataFrame())
    client.hm_list()
    assert calls[0][0] == "hm_list"
    assert "name" in calls[0][1]["fields"]
    assert "orgs" in calls[0][1]["fields"]

def test_hm_detail_calls_tushare_api(monkeypatch):
    client = TushareClient.__new__(TushareClient)
    calls = []
    monkeypatch.setattr(client, "_call", lambda api, **kw: calls.append((api, kw)) or pd.DataFrame())
    client.hm_detail(trade_date="20260424")
    assert calls[0][0] == "hm_detail"
    assert calls[0][1]["trade_date"] == "20260424"
    assert "hm_name" in calls[0][1]["fields"]
    assert "net_amount" in calls[0][1]["fields"]
```

**Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_tushare_client.py -q
```

**Step 3: 实现 5 个 client 方法**

参考 [vendor/mo-skills/tushare-skills/references/数据接口.md](../../../vendor/mo-skills/tushare-skills/references/数据接口.md) 与 [scripts/stock_data_demo.py](../../../vendor/mo-skills/tushare-skills/scripts/stock_data_demo.py) 确认参数与 fields 拼写：

```python
def ths_daily(
    self,
    ts_code: str | None = None,
    trade_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """同花顺概念/行业指数行情。"""
    return self._call(
        "ths_daily",
        ts_code=ts_code,
        trade_date=trade_date,
        start_date=start_date,
        end_date=end_date,
        limiter=_strict_limiter,
        fields=(
            "ts_code,trade_date,close,open,high,low,pre_close,avg_price,"
            "change,pct_change,vol,turnover_rate,total_mv,float_mv"
        ),
    )


def limit_cpt_list(
    self,
    trade_date: str | None = None,
    ts_code: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """涨停最强概念板块统计。"""
    return self._call(
        "limit_cpt_list",
        trade_date=trade_date,
        ts_code=ts_code,
        start_date=start_date,
        end_date=end_date,
        limiter=_strict_limiter,
        fields="ts_code,name,trade_date,days,up_stat,cons_nums,up_nums,pct_chg,rank",
    )


def moneyflow_cnt_ths(
    self,
    ts_code: str | None = None,
    trade_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """同花顺概念板块资金流向。"""
    return self._call(
        "moneyflow_cnt_ths",
        ts_code=ts_code,
        trade_date=trade_date,
        start_date=start_date,
        end_date=end_date,
        limiter=_strict_limiter,
        fields=(
            "trade_date,ts_code,name,lead_stock,pct_change,company_num,"
            "pct_change_stock,net_buy_amount,net_sell_amount,net_amount"
        ),
    )


def hm_list(self, name: str | None = None) -> pd.DataFrame:
    """游资名录。"""
    return self._call(
        "hm_list",
        name=name,
        limiter=_default_limiter,
        fields="name,desc,orgs",
    )


def hm_detail(
    self,
    trade_date: str | None = None,
    ts_code: str | None = None,
    hm_name: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """游资每日交易明细。"""
    return self._call(
        "hm_detail",
        trade_date=trade_date,
        ts_code=ts_code,
        hm_name=hm_name,
        start_date=start_date,
        end_date=end_date,
        limiter=_strict_limiter,
        fields="trade_date,ts_code,ts_name,buy_amount,sell_amount,net_amount,hm_name,hm_orgs,tag",
    )
```

**Step 4: 跑测试确认通过**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_tushare_client.py -q
```

**Step 5: 积分实测脚本（一次性手动跑）**

```bash
.venv/Scripts/python.exe -c "
from mo_stock.data_sources.tushare_client import TushareClient
client = TushareClient()
for fn, kw in [
    ('ths_daily', dict(trade_date='20260424')),
    ('limit_cpt_list', dict(trade_date='20260424')),
    ('moneyflow_cnt_ths', dict(trade_date='20260424')),
    ('hm_list', dict()),
    ('hm_detail', dict(trade_date='20260424')),
]:
    try:
        df = getattr(client, fn)(**kw)
        print(f'OK  {fn}: {len(df)} rows')
    except Exception as e:
        print(f'FAIL {fn}: {e}')
"
```

把实测结果回写到 `tushare_account.md`：

```markdown
## 已验证可调用接口（实测于 2026-04-26）
- ths_daily          ✓
- limit_cpt_list     ✓ / ✗（按实测填）
- moneyflow_cnt_ths  ✓
- hm_list            ✓
- hm_detail          ✓
```

**Step 6: Commit**

```bash
git add src/mo_stock/data_sources/tushare_client.py tests/unit/test_tushare_client.py
git commit -m "feat(client): 接入 ths_daily/limit_cpt_list/moneyflow_cnt_ths/hm_list/hm_detail"
```

---

### Task 2: Storage Models + Repo

**Files:**
- Modify: `src/mo_stock/storage/models.py`
- Modify: `src/mo_stock/storage/repo.py`
- Test: `tests/unit/test_repo_upsert.py`

**Step 1: 写失败测试**

扩展 `_UPSERT_BINDINGS` 列表（参见 [P0-9 现有模式](../../../tests/unit/test_repo_upsert.py)）：

```python
_UPSERT_BINDINGS_NEW = [
    (repo.upsert_ths_daily,             ThsDaily,             ["ts_code", "trade_date"]),
    (repo.upsert_limit_concept_daily,   LimitConceptDaily,    ["ts_code", "trade_date"]),
    (repo.upsert_concept_moneyflow,     ThsConceptMoneyflow,  ["ts_code", "trade_date"]),
    (repo.upsert_hot_money_list,        HotMoneyList,         ["name"]),
    (repo.upsert_hot_money_detail,      HotMoneyDetail,       ["trade_date", "ts_code", "hm_name"]),
    (repo.upsert_lhb_seat_detail,       LhbSeatDetail,        ["trade_date", "ts_code", "seat_key"]),
]

# 加入到主 _UPSERT_BINDINGS 列表，复用现有 parametrize 测试
```

**Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_repo_upsert.py -q
```

**SQLite 测试策略**：`repo.upsert_rows()` 仍以 PostgreSQL `ON CONFLICT` 方言为生产实现；新增 upsert 绑定测试只断言 model 与 conflict key 一致，不在 SQLite engine 上实际执行 upsert。若新增 integration test 需要 SQLite，使用 ORM `session.add_all()` 准备数据，避免把 PostgreSQL 方言行为伪装成 SQLite 行为。

**Step 3: 加 6 个 model 类**（见 §2，照抄即可）。同时修改 `Lhb` model：

```python
class Lhb(Base):
    # ... 其它字段不变 ...
    # seat: 删除（v2 改建独立表 lhb_seat_detail）
```

**Step 4: 加 repo upsert + read helpers**

```python
def upsert_ths_daily(session, rows):
    return upsert_rows(session, ThsDaily, rows, conflict_cols=["ts_code", "trade_date"])

def upsert_limit_concept_daily(session, rows):
    return upsert_rows(session, LimitConceptDaily, rows, conflict_cols=["ts_code", "trade_date"])

def upsert_concept_moneyflow(session, rows):
    return upsert_rows(session, ThsConceptMoneyflow, rows, conflict_cols=["ts_code", "trade_date"])

def upsert_hot_money_list(session, rows):
    return upsert_rows(session, HotMoneyList, rows, conflict_cols=["name"])

def upsert_hot_money_detail(session, rows):
    return upsert_rows(session, HotMoneyDetail, rows, conflict_cols=["trade_date", "ts_code", "hm_name"])

def upsert_lhb_seat_detail(session, rows):
    return upsert_rows(session, LhbSeatDetail, rows, conflict_cols=["trade_date", "ts_code", "seat_key"])


# Read helpers --------

def get_top_ths_themes(session, trade_date: date, n: int = 10) -> list[ThsDaily]:
    """当日 THS 概念按 pct_change 降序 TOP N。"""
    stmt = (
        select(ThsDaily)
        .where(ThsDaily.trade_date == trade_date)
        .where(ThsDaily.pct_change.isnot(None))
        .order_by(ThsDaily.pct_change.desc(), ThsDaily.ts_code)
        .limit(n)
    )
    return list(session.execute(stmt).scalars().all())


def get_limit_concept_rank_map(session, trade_date: date) -> dict[str, int]:
    """{concept_ts_code: rank}（只取 rank IS NOT NULL 的）。"""
    stmt = (
        select(LimitConceptDaily.ts_code, LimitConceptDaily.rank)
        .where(LimitConceptDaily.trade_date == trade_date)
        .where(LimitConceptDaily.rank.isnot(None))
    )
    return {ts: int(rk) for ts, rk in session.execute(stmt).all() if rk is not None}


def get_concept_moneyflow_map(session, trade_date: date) -> dict[str, float]:
    """{concept_ts_code: net_amount}（亿元，None 视为 0）。"""
    stmt = (
        select(ThsConceptMoneyflow.ts_code, ThsConceptMoneyflow.net_amount)
        .where(ThsConceptMoneyflow.trade_date == trade_date)
    )
    return {ts: (na or 0.0) for ts, na in session.execute(stmt).all()}


def get_stock_to_concepts_map(session) -> dict[str, list[str]]:
    """{stock_ts_code: [concept_ts_code, ...]}。慢变量，调用方进程内缓存。"""
    stmt = select(ThsMember.ts_code, ThsMember.con_code)
    result: dict[str, list[str]] = {}
    for concept_code, stock_code in session.execute(stmt).all():
        result.setdefault(stock_code, []).append(concept_code)
    return result


def get_lhb_seats_today(session, trade_date: date) -> dict[str, list[LhbSeatDetail]]:
    """当日全部龙虎榜席位明细按 ts_code 分组。"""
    stmt = (
        select(LhbSeatDetail)
        .where(LhbSeatDetail.trade_date == trade_date)
        .order_by(LhbSeatDetail.ts_code, LhbSeatDetail.seat_no, LhbSeatDetail.seat_key)
    )
    result: dict[str, list[LhbSeatDetail]] = {}
    for seat in session.execute(stmt).scalars().all():
        result.setdefault(seat.ts_code, []).append(seat)
    return result
```

**Step 5: 跑测试确认通过**

**Step 6: Commit**

```bash
git add src/mo_stock/storage/models.py src/mo_stock/storage/repo.py tests/unit/test_repo_upsert.py
git commit -m "feat(storage): 新增 6 张题材+龙虎榜席位表，drop Lhb.seat JSONB"
```

---

### Task 3: Ingest 清洗函数 + Daily Jobs（CORE/ENHANCED 分组）

**Files:**
- Modify: `src/mo_stock/ingest/ingest_daily.py`
- Create: `tests/unit/test_theme_ingest.py`
- Create: `tests/unit/test_lhb_seat.py`

**Step 1: 写失败的清洗函数测试**

`tests/unit/test_theme_ingest.py`：

```python
def test_ths_daily_rows_from_df_maps_fields():
    df = pd.DataFrame([{
        "ts_code": "885806.TI", "trade_date": "20260424",
        "close": 1000.5, "pct_change": 2.46, "turnover_rate": 3.2,
        "total_mv": 1_000_000.0, "float_mv": 800_000.0,
    }])
    rows = _ths_daily_rows_from_df(df, name_map={"885806.TI": "华为概念"})
    assert rows[0]["ts_code"] == "885806.TI"
    assert rows[0]["trade_date"] == date(2026, 4, 24)
    assert rows[0]["name"] == "华为概念"
    assert rows[0]["pct_change"] == 2.46


def test_concept_moneyflow_rows_from_df_keeps_all_three_amounts():
    """v2 修法：三个净额字段都入库，不能像 v1 只存 net_amount。"""
    df = pd.DataFrame([{
        "ts_code": "885806.TI", "trade_date": "20260424",
        "name": "华为", "lead_stock": "600000.SH", "pct_change": 5.0,
        "company_num": 50, "pct_change_stock": 9.99,
        "net_buy_amount": 12.3, "net_sell_amount": 5.6, "net_amount": 6.7,
    }])
    rows = _concept_moneyflow_rows_from_df(df)
    assert rows[0]["net_buy_amount"] == 12.3
    assert rows[0]["net_sell_amount"] == 5.6
    assert rows[0]["net_amount"] == 6.7
```

`tests/unit/test_lhb_seat.py`：

```python
def test_classify_seat_institution():
    assert _classify_seat("机构专用", set()) == "institution"

def test_classify_seat_northbound():
    assert _classify_seat("沪股通专用", set()) == "northbound"
    assert _classify_seat("深股通专用", set()) == "northbound"

def test_classify_seat_hot_money_exact_match():
    """完全相等匹配，避免子串误判。"""
    orgs = {"中信证券上海溧阳路营业部"}
    assert _classify_seat("中信证券上海溧阳路营业部", orgs) == "hot_money"
    # 不同营业部不匹配，即使共享前缀
    assert _classify_seat("中信证券北京东直门外大街营业部", orgs) == "other"

def test_classify_seat_no_quant_like():
    """v2 修法：删除 quant_like 启发式。"""
    assert _classify_seat("华鑫证券上海某营业部", set()) == "other"

def test_classify_seat_empty_or_none():
    assert _classify_seat(None, set()) == "other"
    assert _classify_seat("", set()) == "other"


def test_top_inst_rows_from_df_assigns_seat_no_and_type():
    df = pd.DataFrame([
        {"trade_date": "20260424", "ts_code": "600000.SH",
         "exalter": "机构专用", "side": "0",
         "buy": 12_000_000.0, "sell": 2_000_000.0, "net_buy": 10_000_000.0,
         "reason": "日涨幅偏离值达7%的证券"},
        {"trade_date": "20260424", "ts_code": "600000.SH",
         "exalter": "中信证券上海溧阳路营业部", "side": "0",
         "buy": 5_000_000.0, "sell": 1_000_000.0, "net_buy": 4_000_000.0,
         "reason": "日涨幅偏离值达7%的证券"},
    ])
    rows = _top_inst_rows_from_df(df, hot_money_orgs={"中信证券上海溧阳路营业部"})
    assert len(rows) == 2
    assert rows[0]["seat_no"] == 1
    assert rows[0]["seat_key"]
    assert rows[0]["seat_type"] == "institution"
    assert rows[1]["seat_no"] == 2
    assert rows[1]["seat_key"]
    assert rows[1]["seat_type"] == "hot_money"


def test_top_inst_rows_from_df_generates_stable_keys_when_order_changes():
    """top_inst 返回顺序变化时，seat_key 必须稳定，避免 upsert 覆盖错行。"""
    df = pd.DataFrame([
        {"trade_date": "20260424", "ts_code": "600000.SH",
         "exalter": "中信证券上海溧阳路营业部", "side": "0",
         "buy": 5_000_000.0, "sell": 1_000_000.0, "net_buy": 4_000_000.0,
         "reason": "日涨幅偏离值达7%的证券"},
        {"trade_date": "20260424", "ts_code": "600000.SH",
         "exalter": "机构专用", "side": "0",
         "buy": 12_000_000.0, "sell": 2_000_000.0, "net_buy": 10_000_000.0,
         "reason": "日涨幅偏离值达7%的证券"},
    ])
    rows_a = _top_inst_rows_from_df(df, hot_money_orgs={"中信证券上海溧阳路营业部"})
    rows_b = _top_inst_rows_from_df(df.iloc[::-1].reset_index(drop=True), hot_money_orgs={"中信证券上海溧阳路营业部"})
    assert {r["seat_key"] for r in rows_a} == {r["seat_key"] for r in rows_b}


def test_build_hot_money_orgs_set_splits_separators():
    """从分号/逗号/中文标点切割营业部列表。"""
    raw = "中信证券上海溧阳路营业部,华泰证券深圳益田路营业部;东方财富证券拉萨；招商证券北京"
    parts = _split_orgs_string(raw)
    assert "中信证券上海溧阳路营业部" in parts
    assert "华泰证券深圳益田路营业部" in parts
    assert "东方财富证券拉萨" in parts
    assert "招商证券北京" in parts
```

**Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_theme_ingest.py tests/unit/test_lhb_seat.py -q
```

**Step 3: 实现清洗函数**（仅展示新增；现有 `_parse_date` / `_nf` / `_ni` / `_str_or_none` 复用）

```python
# ingest_daily.py 末尾追加 ---------

def _ths_daily_rows_from_df(df: pd.DataFrame, name_map: dict[str, str] | None = None) -> list[dict[str, Any]]:
    if df.empty:
        return []
    name_map = name_map or {}
    return [{
        "ts_code": r["ts_code"],
        "trade_date": _parse_date(r["trade_date"]),
        "name": name_map.get(r["ts_code"]),
        "close": _nf(r.get("close")),
        "open": _nf(r.get("open")),
        "high": _nf(r.get("high")),
        "low": _nf(r.get("low")),
        "pre_close": _nf(r.get("pre_close")),
        "avg_price": _nf(r.get("avg_price")),
        "change": _nf(r.get("change")),
        "pct_change": _nf(r.get("pct_change")),
        "vol": _nf(r.get("vol")),
        "turnover_rate": _nf(r.get("turnover_rate")),
        "total_mv": _nf(r.get("total_mv")),
        "float_mv": _nf(r.get("float_mv")),
    } for _, r in df.iterrows()]


def _limit_concept_rows_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    df = df.drop_duplicates(subset=["trade_date", "ts_code"], keep="first")
    return [{
        "ts_code": r["ts_code"],
        "trade_date": _parse_date(r["trade_date"]),
        "name": _str_or_none(r.get("name")),
        "days": _ni(r.get("days")),
        "up_stat": _str_or_none(r.get("up_stat")),
        "cons_nums": _ni(r.get("cons_nums")),
        "up_nums": _ni(r.get("up_nums")),
        "pct_chg": _nf(r.get("pct_chg")),
        "rank": _ni(r.get("rank")),
    } for _, r in df.iterrows()]


def _concept_moneyflow_rows_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    """v2 修法：三个净额字段都保留入库。"""
    if df.empty:
        return []
    return [{
        "ts_code": r["ts_code"],
        "trade_date": _parse_date(r["trade_date"]),
        "name": _str_or_none(r.get("name")),
        "lead_stock": _str_or_none(r.get("lead_stock")),
        "pct_change": _nf(r.get("pct_change")),
        "company_num": _ni(r.get("company_num")),
        "pct_change_stock": _nf(r.get("pct_change_stock")),
        "net_buy_amount": _nf(r.get("net_buy_amount")),
        "net_sell_amount": _nf(r.get("net_sell_amount")),
        "net_amount": _nf(r.get("net_amount")),
    } for _, r in df.iterrows()]


def _hm_list_rows_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    return [{
        "name": r["name"],
        "desc": _str_or_none(r.get("desc")),
        "orgs": _str_or_none(r.get("orgs")),
    } for _, r in df.iterrows()]


def _hm_detail_rows_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    return [{
        "trade_date": _parse_date(r["trade_date"]),
        "ts_code": r["ts_code"],
        "hm_name": r["hm_name"],
        "ts_name": _str_or_none(r.get("ts_name")),
        "buy_amount": _nf(r.get("buy_amount")),
        "sell_amount": _nf(r.get("sell_amount")),
        "net_amount": _nf(r.get("net_amount")),
        "hm_orgs": _str_or_none(r.get("hm_orgs")),
        "tag": _str_or_none(r.get("tag")),
    } for _, r in df.iterrows()]


import hashlib
import re

_ORGS_SEP_RE = re.compile(r"[,;，；]")


def _split_orgs_string(orgs: str | None) -> set[str]:
    """从 hm_list.orgs 拆出营业部 set，去 None/空白。"""
    if not orgs:
        return set()
    return {p.strip() for p in _ORGS_SEP_RE.split(orgs) if p.strip()}


def _classify_seat(exalter: str | None, hot_money_orgs: set[str]) -> str:
    """席位身份分类。无 quant_like（避免误报）。"""
    name = (exalter or "").strip()
    if not name:
        return "other"
    if "机构专用" in name:
        return "institution"
    if "沪股通专用" in name or "深股通专用" in name:
        return "northbound"
    if name in hot_money_orgs:
        return "hot_money"
    return "other"


def _stable_seat_key(ts_code: str, exalter: str | None, side: str | None, reason: str | None) -> str:
    """生成稳定席位键；不使用 top_inst 返回顺序，避免重跑时主键漂移。"""
    raw = "|".join([ts_code, exalter or "", side or "", reason or ""])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _top_inst_rows_from_df(df: pd.DataFrame, hot_money_orgs: set[str]) -> list[dict[str, Any]]:
    """top_inst DataFrame → LhbSeatDetail rows。

    PK = (trade_date, ts_code, seat_key)。seat_no 只用于展示，按稳定排序后 1..N。
    """
    if df.empty:
        return []
    rows: list[dict[str, Any]] = []
    counter: dict[tuple[date | None, str], int] = {}
    sort_cols = ["trade_date", "ts_code", "reason", "side", "exalter", "buy", "sell", "net_buy"]
    df_sorted = df.sort_values([c for c in sort_cols if c in df.columns], na_position="last")
    for _, r in df_sorted.iterrows():
        td = _parse_date(r["trade_date"])
        ts = r["ts_code"]
        key = (td, ts)
        counter[key] = counter.get(key, 0) + 1
        exalter = _str_or_none(r.get("exalter"))
        side = _str_or_none(r.get("side"))
        reason = _str_or_none(r.get("reason"))
        rows.append({
            "trade_date": td,
            "ts_code": ts,
            "seat_key": _stable_seat_key(ts, exalter, side, reason),
            "seat_no": counter[key],
            "exalter": exalter,
            "side": side,
            "buy": _nf(r.get("buy")),
            "sell": _nf(r.get("sell")),
            "net_buy": _nf(r.get("net_buy")),
            "reason": reason,
            "seat_type": _classify_seat(exalter, hot_money_orgs),
        })
    return rows
```

**Step 4: 实现 ingest 方法 + CORE/ENHANCED 分组**

```python
# DailyIngestor 内新增方法 ---------

def ingest_ths_daily(self, trade_date: date) -> int:
    df = self.client.ths_daily(trade_date=date_to_tushare(trade_date))
    if df.empty:
        logger.warning("ths_daily {} 返回空", trade_date)
        return 0
    with get_session() as s:
        name_map = {x.ts_code: x.name for x in s.query(ThsIndex).all()}
        rows = _ths_daily_rows_from_df(df, name_map=name_map)
        n = repo.upsert_ths_daily(s, rows)
    logger.info("ths_daily {} upserted {} rows", trade_date, n)
    return n


def ingest_limit_concept(self, trade_date: date) -> int:
    df = self.client.limit_cpt_list(trade_date=date_to_tushare(trade_date))
    rows = _limit_concept_rows_from_df(df)
    if not rows:
        logger.info("limit_cpt_list {} 返回空", trade_date)
        return 0
    with get_session() as s:
        n = repo.upsert_limit_concept_daily(s, rows)
    logger.info("limit_concept_daily {} upserted {} rows", trade_date, n)
    return n


def ingest_concept_moneyflow(self, trade_date: date) -> int:
    df = self.client.moneyflow_cnt_ths(trade_date=date_to_tushare(trade_date))
    rows = _concept_moneyflow_rows_from_df(df)
    if not rows:
        return 0
    with get_session() as s:
        n = repo.upsert_concept_moneyflow(s, rows)
    logger.info("ths_concept_moneyflow {} upserted {} rows", trade_date, n)
    return n


def ingest_top_inst(self, trade_date: date) -> int:
    """龙虎榜席位明细。依赖 hm_list（用 set 加速识别游资席位）。"""
    df = self.client.top_inst(trade_date=date_to_tushare(trade_date))
    if df.empty:
        return 0
    with get_session() as s:
        hot_money_orgs: set[str] = set()
        for hm in s.query(HotMoneyList).all():
            hot_money_orgs |= _split_orgs_string(hm.orgs)
        if not hot_money_orgs:
            logger.warning("hot_money_list 为空，top_inst 席位仍入库，但 hot_money 分类会降级为 other；请先运行 refresh-basics --with-hm-list")
        rows = _top_inst_rows_from_df(df, hot_money_orgs=hot_money_orgs)
        n = repo.upsert_lhb_seat_detail(s, rows)
    logger.info("lhb_seat_detail {} upserted {} rows", trade_date, n)
    return n


def refresh_hot_money_list(self) -> int:
    """游资名录（低频，refresh-basics 调用）。"""
    df = self.client.hm_list()
    rows = _hm_list_rows_from_df(df)
    if not rows:
        return 0
    with get_session() as s:
        n = repo.upsert_hot_money_list(s, rows)
    logger.info("hm_list upserted {} rows", n)
    return n


def ingest_hm_detail(self, trade_date: date) -> int:
    """游资交易明细（可选，ENHANCED 组）。"""
    df = self.client.hm_detail(trade_date=date_to_tushare(trade_date))
    rows = _hm_detail_rows_from_df(df)
    if not rows:
        return 0
    with get_session() as s:
        n = repo.upsert_hot_money_detail(s, rows)
    logger.info("hm_detail {} upserted {} rows", trade_date, n)
    return n


# ingest_one_day 改为分组 ---------

CORE_STEPS: list[tuple[str, str]] = [
    ("daily_kline",   "ingest_daily_kline"),
    ("daily_basic",   "ingest_daily_basic"),
    ("limit_list",    "ingest_limit_list"),
    ("moneyflow",     "ingest_moneyflow"),
    ("lhb",           "ingest_lhb"),
    ("sw_daily",      "ingest_sw_daily"),
]

ENHANCED_STEPS: list[tuple[str, str]] = [
    ("ths_daily",          "ingest_ths_daily"),
    ("limit_concept",      "ingest_limit_concept"),
    ("concept_moneyflow",  "ingest_concept_moneyflow"),
    ("top_inst",           "ingest_top_inst"),
    ("hm_detail",          "ingest_hm_detail"),
]


def ingest_one_day(self, trade_date: date, *, skip_enhanced: bool = False) -> dict[str, int]:
    """拉取指定交易日的全部数据。

    skip_enhanced=True 时只跑 6 个 CORE 步骤，跳过题材/席位增强（用于调试或资源紧张时）。
    """
    logger.info("=== ingest_one_day {} (skip_enhanced={}) ===", trade_date, skip_enhanced)
    steps = list(CORE_STEPS)
    if not skip_enhanced:
        steps.extend(ENHANCED_STEPS)

    stats: dict[str, int] = {}
    for name, attr in steps:
        try:
            stats[name] = getattr(self, attr)(trade_date)
        except Exception as exc:
            stats[name] = -1
            logger.exception("{} {} failed: {}", name, trade_date, exc)
    logger.info("ingest_one_day {} done: {}", trade_date, stats)
    return stats
```

**Step 5: CLI / scheduler 加 `--skip-enhanced`**

```python
# cli.py run_once
@click.option("--skip-enhanced", is_flag=True, help="只跑 6 个 CORE ingest 步骤，跳过题材/席位增强")
def run_once(date_str, skip_ingest, force, skip_enhanced):
    ...
    if not skip_ingest:
        DailyIngestor().ingest_one_day(trade_date, skip_enhanced=skip_enhanced)


# refresh-basics 加 hm_list
@cli.command("refresh-basics")
@click.option("--with-ths", is_flag=True, help="...")
@click.option("--with-hm-list", is_flag=True, help="同时刷新游资名录（hm_list）")
def refresh_basics(with_ths, with_hm_list):
    ingestor = DailyIngestor()
    ingestor.refresh_stock_basic()
    ingestor.refresh_index_member()
    if with_ths:
        ingestor.refresh_ths_concept()
    if with_hm_list:
        ingestor.refresh_hot_money_list()


# cli.py scheduler
@click.option("--skip-enhanced", is_flag=True, help="scheduler 每日任务跳过 ENHANCED ingest")
def scheduler(skip_enhanced):
    # 将配置传给 start_scheduler/run_daily_pipeline；不要只加 click option 而不消费。
    start_scheduler(skip_enhanced=skip_enhanced)


# scheduler/daily_job.py
def run_daily_pipeline(trade_date: date | None = None, *, skip_enhanced: bool = False) -> dict[str, Any]:
    actual_date = trade_date or get_latest_trade_date()
    ingestor = DailyIngestor()
    ingest_stats = ingestor.ingest_one_day(actual_date, skip_enhanced=skip_enhanced)
    # 后续 scoring/report 逻辑保持现有流程。
    return {"trade_date": actual_date, "ingest": ingest_stats}
```

**Step 6: 跑全量测试**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_theme_ingest.py tests/unit/test_lhb_seat.py tests/unit/test_ingest_daily.py -q
```

**Step 7: Commit**

```bash
git add src/mo_stock/ingest/ingest_daily.py src/mo_stock/cli.py \
        tests/unit/test_theme_ingest.py tests/unit/test_lhb_seat.py
git commit -m "feat(ingest): 题材/席位明细采集 + ingest_one_day CORE/ENHANCED 分组"
```

---

### Task 4: ThemeFilter（独立新维度）

**Files:**
- Create: `src/mo_stock/filters/theme_filter.py`
- Create: `tests/unit/test_theme_filter.py`

**Step 1: 写失败测试**

```python
def test_theme_filter_no_data_returns_empty(session):
    """无 ths_daily 数据时不报错。"""
    f = ThemeFilter(weights={"top_n_themes": 10})
    assert f.score_all(session, date(2026, 4, 24)) == []


def test_theme_filter_top_concept_bonus(session):
    """概念排第 1 + 涨停最强排第 1 + 资金净流入 → max bonus。"""
    td = date(2026, 4, 24)
    session.add(ThsDaily(ts_code="885806.TI", trade_date=td, name="华为", pct_change=8.0))
    session.add(LimitConceptDaily(ts_code="885806.TI", trade_date=td, name="华为", rank=1))
    session.add(ThsConceptMoneyflow(ts_code="885806.TI", trade_date=td, name="华为", net_amount=10.5))
    session.add(ThsMember(ts_code="885806.TI", con_code="600000.SH", con_name="浦发"))
    session.flush()

    f = ThemeFilter(weights=DEFAULT_THEME_WEIGHTS)
    results = f.score_all(session, td)
    by_code = {r.ts_code: r for r in results}
    # 50 (ths_rank 1) + 50 (limit_concept 1) + 15 (moneyflow > 0) = 115 → clamp 100
    assert by_code["600000.SH"].score == 100.0


def test_theme_filter_multi_concept_takes_max(session):
    """多概念股取最高概念加分，不累加。"""
    td = date(2026, 4, 24)
    # 概念 A: rank 1（强）
    session.add(ThsDaily(ts_code="885806.TI", trade_date=td, pct_change=8.0))
    session.add(LimitConceptDaily(ts_code="885806.TI", trade_date=td, rank=1))
    # 概念 B: rank 5（中）
    session.add(ThsDaily(ts_code="885900.TI", trade_date=td, pct_change=3.0))
    # 同一只股属于两个概念
    session.add(ThsMember(ts_code="885806.TI", con_code="600000.SH"))
    session.add(ThsMember(ts_code="885900.TI", con_code="600000.SH"))
    session.flush()

    f = ThemeFilter(weights=DEFAULT_THEME_WEIGHTS)
    results = f.score_all(session, td)
    score = next(r.score for r in results if r.ts_code == "600000.SH")
    # 取概念 A 的 max(50+50+0, 22+0+0) = 100
    assert score == 100.0


def test_theme_filter_limit_concept_only(session):
    """没在 ths_daily 但在涨停最强榜的概念也算分。"""
    td = date(2026, 4, 24)
    session.add(LimitConceptDaily(ts_code="885950.TI", trade_date=td, rank=3))
    session.add(ThsMember(ts_code="885950.TI", con_code="600001.SH"))
    session.flush()

    f = ThemeFilter(weights=DEFAULT_THEME_WEIGHTS)
    results = f.score_all(session, td)
    score = next(r.score for r in results if r.ts_code == "600001.SH")
    assert score == 35.0  # rank 3 = 35
```

**Step 2: 跑测试确认失败**

**Step 3: 实现 ThemeFilter**

```python
"""题材/概念维度打分（与 sector 维度平级）。

**思路**（v2 plan §3.3）：
- 数据源：ths_daily（概念涨幅）+ limit_concept_daily（涨停最强）+ ths_concept_moneyflow（资金确认）
- 多概念股取**最高**概念加分，不累加（避免沾边股霸榜）
- 三类信号在同一概念上 sum，跨概念 max：
    score(stock) = max over concepts of:
        rank_bonus(ths_pct_change_rank) + limit_concept_bonus(limit_rank) + moneyflow_bonus(net_amount > 0)
"""
from __future__ import annotations

from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from mo_stock.filters.base import FilterBase, ScoreResult, clamp
from mo_stock.storage import repo


class ThemeFilter(FilterBase):
    """同花顺概念 + 涨停最强 + 资金流三合一题材打分。"""

    dim = "theme"

    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        cfg = self.weights
        top_n = cfg.get("top_n_themes", 10)
        ths_rank_table: dict[int, int] = cfg.get("ths_rank_bonus", {})
        limit_rank_table: dict[int, int] = cfg.get("limit_concept_rank_bonus", {})
        moneyflow_bonus = cfg.get("concept_moneyflow_positive_bonus", 15)

        # 1. THS 概念涨幅 TOP N → {concept_code: rank}
        top_themes = repo.get_top_ths_themes(session, trade_date, n=top_n)
        if not top_themes:
            logger.warning("ThemeFilter: {} 无 ths_daily 数据，将继续使用 limit_cpt_list / moneyflow_cnt_ths 信号", trade_date)
        ths_rank_map = {t.ts_code: i for i, t in enumerate(top_themes, start=1)}

        # 2. 涨停最强概念 → {concept_code: rank}
        limit_rank_map = repo.get_limit_concept_rank_map(session, trade_date)

        # 3. 概念资金流 → {concept_code: net_amount}
        moneyflow_map = repo.get_concept_moneyflow_map(session, trade_date)

        # 4. 股票 → 概念列表（慢变量，全表扫一次）
        stock_concepts = repo.get_stock_to_concepts_map(session)
        if not ths_rank_map and not limit_rank_map and not moneyflow_map:
            logger.warning("ThemeFilter: {} 三类题材信号均为空", trade_date)
            return []

        results: list[ScoreResult] = []
        for ts_code, concepts in stock_concepts.items():
            best = 0
            best_concept: str | None = None
            for c in concepts:
                rb = _bonus_from_table(ths_rank_table, ths_rank_map.get(c, 0))
                lb = _bonus_from_table(limit_rank_table, limit_rank_map.get(c, 0))
                mb = moneyflow_bonus if moneyflow_map.get(c, 0.0) > 0 else 0
                total = rb + lb + mb
                if total > best:
                    best = total
                    best_concept = c
            if best <= 0:
                continue
            results.append(ScoreResult(
                ts_code=ts_code,
                trade_date=trade_date,
                dim=self.dim,
                score=clamp(best),
                detail={
                    "best_concept": best_concept,
                    "ths_rank": ths_rank_map.get(best_concept or "", 0),
                    "limit_rank": limit_rank_map.get(best_concept or "", 0),
                    "concept_net_amount_yi": round(moneyflow_map.get(best_concept or "", 0.0), 2),
                },
            ))

        logger.info("ThemeFilter: {} 加分股 {} 只", trade_date, len(results))
        return results


def _bonus_from_table(table: dict[int, int], rank: int) -> int:
    """从分档表查 rank 对应分数。table 形如 {1: 50, 2: 42, 3: 35, 5: 22, 10: 12}。

    策略：找 >= rank 的最小 key 对应的分数（而非精确等于）。
    rank=4 时若表里只有 1/2/3/5，返回 5 对应分数（保守）。
    rank=0 或 rank > max(keys) → 0。
    """
    if rank <= 0 or not table:
        return 0
    sorted_keys = sorted(table.keys())
    max_key = sorted_keys[-1]
    if rank > max_key:
        return 0
    # 找 ≥ rank 的最小 key
    for k in sorted_keys:
        if k >= rank:
            return table[k]
    return 0
```

**Step 4: 跑测试确认通过**

**Step 5: Commit**

```bash
git add src/mo_stock/filters/theme_filter.py tests/unit/test_theme_filter.py
git commit -m "feat(theme): 新增 ThemeFilter 维度（与 sector 平级）"
```

---

### Task 5: LhbFilter v2 重排（base 60 + seat 40）

**Files:**
- Modify: `src/mo_stock/filters/lhb_filter.py`
- Modify: `config/weights.yaml`
- Test: `tests/unit/test_lhb_filter.py`

**Step 1: 写失败测试**

```python
def test_lhb_filter_base_score_capped_at_60(session):
    """所有 base 子项打满，base 上限 60（不再是 100）。"""
    td = date(2026, 4, 24)
    session.add(Lhb(
        trade_date=td, ts_code="600000.SH",
        net_rate=12.0,            # 极强 → tier_bonus 20
        amount_rate=35.0,         # 极强 → purity 12
        reason="连续三日涨幅",     # 最高 → reason 8
    ))
    session.flush()

    f = LhbFilter(weights=DEFAULT_LHB_WEIGHTS)
    results = f.score_all(session, td)
    score = next(r.score for r in results if r.ts_code == "600000.SH")
    # base = 20 + 20 + 12 + 8 = 60，无 seat 数据 → 60
    assert score == 60.0


def test_lhb_filter_institution_buy_adds_20(session):
    """机构席位净买 ≥ 1000 万 → +20。"""
    td = date(2026, 4, 24)
    session.add(Lhb(trade_date=td, ts_code="600000.SH", net_rate=3.0, amount_rate=15.0,
                    reason="日涨幅偏离值达7%的证券"))
    session.add(LhbSeatDetail(trade_date=td, ts_code="600000.SH", seat_no=1,
                              exalter="机构专用", seat_type="institution",
                              buy=20_000_000, sell=1_000_000, net_buy=19_000_000))
    session.flush()

    f = LhbFilter(weights=DEFAULT_LHB_WEIGHTS)
    row = next(r for r in f.score_all(session, td) if r.ts_code == "600000.SH")
    # base = 20 + 10 + 6 + 5 = 41；seat = +20 institution → 61
    assert row.detail["institution_net_buy"] == 19_000_000
    assert row.score == 61.0


def test_lhb_filter_hot_money_sell_penalty(session):
    """知名游资大额净卖 ≥ 1000 万 → -15。"""
    td = date(2026, 4, 24)
    session.add(Lhb(trade_date=td, ts_code="600001.SH", net_rate=3.0, amount_rate=15.0,
                    reason="日涨幅偏离值达7%的证券"))
    session.add(LhbSeatDetail(trade_date=td, ts_code="600001.SH", seat_no=1,
                              exalter="某知名游资营业部", seat_type="hot_money",
                              buy=1_000_000, sell=20_000_000, net_buy=-19_000_000))
    session.flush()

    f = LhbFilter(weights=DEFAULT_LHB_WEIGHTS)
    row = next(r for r in f.score_all(session, td) if r.ts_code == "600001.SH")
    assert row.detail["hot_money_sell_penalty"] == -15
    # base = 41；seat = -15；总 26（不会 < 0，但需验证 clamp 不误伤）
    assert row.score == 26.0


def test_lhb_filter_no_seat_data_falls_back_to_base(session):
    """lhb_seat_detail 表为空时 LhbFilter 仍按 base 打分。"""
    td = date(2026, 4, 24)
    session.add(Lhb(trade_date=td, ts_code="600000.SH", net_rate=3.0, amount_rate=15.0,
                    reason="日涨幅偏离值达7%的证券"))
    session.flush()

    f = LhbFilter(weights=DEFAULT_LHB_WEIGHTS)
    row = next(r for r in f.score_all(session, td) if r.ts_code == "600000.SH")
    # base = 20 + 10 + 6 + 5 = 41
    assert row.score == 41.0
```

**Step 2: 跑测试确认失败**

**Step 3: 实现新 LhbFilter**

主要修改：把 `_net_rate_tier_bonus` / `_purity_bonus` / `_reason_bonus` 的上限重新分档；新增 `_seat_structure_score`；`score_all` 一次性查 `repo.get_lhb_seats_today` 并按 ts_code 分发。

```python
def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
    results: list[ScoreResult] = []
    rows = repo.get_lhb_today(session, trade_date)
    if not rows:
        return results

    # 一次性查所有席位明细，按 ts_code 分组
    seats_map = repo.get_lhb_seats_today(session, trade_date)

    cfg = self.weights
    skipped = 0
    for r in rows:
        if _is_drop_rebound_reason(r.reason):
            skipped += 1
            continue
        if r.net_rate is None or r.net_rate <= 0:
            continue

        # base (上限 60)
        base = cfg.get("base_score", 20)
        tier = _net_rate_tier_bonus(r.net_rate, cfg.get("net_rate_tier_bonus", {}))
        purity = _purity_bonus(r.amount_rate, cfg.get("purity_bonus", {}))
        reason_b = _reason_bonus(r.reason, cfg.get("reason_bonus", {}))
        score = base + tier + purity + reason_b

        detail: dict[str, Any] = {
            "lhb_formula_version": 2,
            "net_rate_pct": round(r.net_rate, 2),
            "amount_rate_pct": round(r.amount_rate or 0, 2),
            "reason": r.reason,
            "net_rate_tier_bonus": tier,
            "purity_bonus": purity,
            "reason_bonus": reason_b,
        }

        # seat (上限 40，下限 -15)
        seats = seats_map.get(r.ts_code, [])
        seat_delta, seat_detail = _seat_structure_score(seats, cfg)
        score += seat_delta
        detail.update(seat_detail)

        results.append(ScoreResult(
            ts_code=r.ts_code,
            trade_date=trade_date,
            dim=self.dim,
            score=clamp(score),
            detail=detail,
        ))

    logger.info("LhbFilter: {} 共 {} 只股入选（跳过跌幅榜 {} 只）", trade_date, len(results), skipped)
    return results
```

`_seat_structure_score` 见 §3.4 完整实现。

口径说明：v2 把 LHB 从原有 0-100 base 改成 `base 60 + seat 40`，历史 `filter_score_daily.detail` 中没有 `lhb_formula_version=2` 的旧分数不应与新分数直接横向比较。

**Step 4: 改 `config/weights.yaml`**

```yaml
lhb_filter:
  base_score: 20
  net_rate_tier_bonus:
    "2": 10
    "5": 15
    "10": 20
  purity_bonus:
    "15": 6
    "30": 12
  reason_bonus:
    "连续三日涨幅": 8
    "无价格涨跌幅限制": 5
    "日涨幅偏离": 5
    "日换手率": 5
  institution:
    min_net_buy_yuan: 10000000
    bonus: 20
  hot_money_buy:
    min_net_buy_yuan: 5000000
    bonus: 12
  hot_money_sell:
    min_net_sell_yuan: 10000000
    penalty: 15
  northbound_buy:
    min_net_buy_yuan: 30000000
    bonus: 8
```

**Step 5: 跑测试**

**Step 6: Commit**

```bash
git add src/mo_stock/filters/lhb_filter.py config/weights.yaml tests/unit/test_lhb_filter.py
git commit -m "feat(lhb): base 60 + seat 40 重排，机构净买给到 +20"
```

---

### Task 6: weights.yaml 总配置 + scheduler/cli 串联

**Files:**
- Modify: `config/weights.yaml`（dimension_weights 加 theme）
- Modify: `src/mo_stock/cli.py`（导入 ThemeFilter，加入 score_all 序列）
- Modify: `src/mo_stock/scheduler/daily_job.py`（导入 ThemeFilter，scheduler 路径也加入 score_all 序列）

**Step 1: 写失败测试**

```python
# tests/unit/test_scorer_combine.py 末尾追加
def test_six_dim_weights_sum_to_one():
    """v2 后 6 维权重总和 1.0，固定分母不变。"""
    weights = {
        "limit": 0.25, "moneyflow": 0.25, "lhb": 0.20,
        "sector": 0.10, "theme": 0.10, "sentiment": 0.10,
    }
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_combine_score_ignores_dim_with_zero_weight():
    """虽 weights.yaml 总和 1.0，但 _weighted_combine 对 missing dim 按 0 计入分子。
    这跟现有公式一致，无需改 combine.py。"""
    # 4 维有分，theme 维度缺失 → 缺失维度按 0 计入分子但分母不缩
    dim_scores = {"limit": 50, "moneyflow": 50, "lhb": 60, "sector": 30}
    weights = {"limit": 0.25, "moneyflow": 0.25, "lhb": 0.20,
               "sector": 0.10, "theme": 0.10, "sentiment": 0.10}
    final = _weighted_combine(dim_scores, weights)
    # (50*0.25 + 50*0.25 + 60*0.20 + 30*0.10) / 1.0 = 12.5+12.5+12+3 = 40
    assert final == 40.0
```

**Step 2: 改 `config/weights.yaml`**

```yaml
dimension_weights:
  limit:     0.25
  moneyflow: 0.25
  lhb:       0.20
  sector:    0.10
  theme:     0.10
  sentiment: 0.10

theme_filter:
  top_n_themes: 10
  ths_rank_bonus: {1: 50, 2: 42, 3: 35, 4: 28, 5: 22, 10: 12}
  limit_concept_rank_bonus: {1: 50, 3: 35, 5: 22, 10: 12}
  concept_moneyflow_positive_bonus: 15
  max_theme_bonus: 100
```

**Step 3: 改 cli.py / daily_job.py 引入 ThemeFilter**

```python
# 在 limit_filter / mf_filter / lhb_filter / sector_filter 后追加：
from mo_stock.filters.theme_filter import ThemeFilter

theme_filter = ThemeFilter(weights=cfg.get("theme_filter", {}))

with get_session() as session:
    all_scores = [
        *limit_filter.score_all(session, trade_date),
        *mf_filter.score_all(session, trade_date),
        *lhb_filter.score_all(session, trade_date),
        *sector_filter.score_all(session, trade_date),
        *theme_filter.score_all(session, trade_date),  # NEW
    ]
    persist_filter_scores(session, all_scores)
```

**Step 4: 跑全量测试**

**Step 5: Commit**

```bash
git add config/weights.yaml src/mo_stock/cli.py src/mo_stock/scheduler/daily_job.py \
        tests/unit/test_scorer_combine.py
git commit -m "feat(combine): 接入 theme 维度，dimension_weights 6 维总和 1.0"
```

---

### Task 7: Alembic Migration + Docs + Final Verify

**Files:**
- Create: `alembic/versions/{rev}_theme_lhb_integration.py`
- Modify: `docs/scoring.md`
- Modify: `docs/cli.md`
- Modify: `CLAUDE.md`

**Step 1: 生成 migration**

```bash
.venv/Scripts/python.exe -m alembic revision --autogenerate -m "theme and lhb seat tables"
```

**Step 2: 人工 review 生成的 .py 文件**

打开 `alembic/versions/{rev}_theme_lhb_integration.py`，确认：

- [ ] 6 张新表都被 `op.create_table` 了
  - `ths_daily`, `limit_concept_daily`, `ths_concept_moneyflow`,
    `hot_money_list`, `hot_money_detail`, `lhb_seat_detail`
- [ ] `Lhb.seat` 字段被 `op.drop_column('lhb', 'seat')` 了
- [ ] 索引（特别是 `ix_ths_daily_date_pct` / `ix_lhb_seat_date_type`）都正确生成
- [ ] downgrade() 也正确（autogenerate 偶尔忘记反向操作）

如果有遗漏，**手工修订**而不是重新生成。

**Step 3: 应用 migration**

```powershell
# 先备份（PowerShell/Windows 兼容；-T 避免 docker exec 分配 TTY 干扰重定向）
New-Item -ItemType Directory -Force data/backups | Out-Null
docker compose exec -T pg pg_dump -U mo_stock -d mo_stock > data/backups/backup-pre-theme.sql

# 应用
.venv/Scripts/python.exe -m alembic upgrade head

# 验证
docker compose exec -T pg psql -U mo_stock -d mo_stock -c "\dt" | Select-String "ths_daily|limit_concept|hot_money|lhb_seat"
```

**Step 4: 更新 `docs/scoring.md`**

```markdown
## 第 6 章 题材维度（v2 新增）

题材维度（`dim=theme`）独立于 sector 维度，三类信号合并：

1. **THS 概念涨幅排名**（ths_daily）：1-10 名分档加分，rank 1 给 50
2. **涨停最强概念排名**（limit_cpt_list）：rank 1 给 50
3. **概念资金净流入确认**（moneyflow_cnt_ths）：净流入 > 0 加 15

**多概念股取最大概念加分**（不累加，避免沾边股霸榜）：

公式：score(stock) = max over concepts of (ths_rb + limit_rb + moneyflow_bonus)

权重：dimension_weights.theme = 0.10（默认）

## 第 5 章 龙虎榜维度（v2 重排）

base + seat 双层结构：

- base (上限 60)：基础分 20 + net_rate_tier(0-20) + purity(0-12) + reason(0-8)
- seat (上限 40, 下限 -15)：
  - 机构净买 ≥ 1000 万 → +20
  - 知名游资净买 ≥ 500 万 → +12
  - 知名游资净卖 ≥ 1000 万 → -15
  - 北向净买 ≥ 3000 万 → +8

席位身份分类（`LhbSeatDetail.seat_type`）：
- `institution`: 名称含"机构专用"
- `northbound`: 名称含"沪股通专用"或"深股通专用"
- `hot_money`: 完全等于 `hm_list.orgs` 的某个营业部
- `other`: 其它（v2 删除了 quant_like，避免误报）

`filter_score_daily.detail.lhb_formula_version = 2` 表示新口径；旧历史分数缺少该字段，不与新口径直接横比。
```

**Step 5: 更新 `docs/cli.md`**

```markdown
## 题材增强工作流

首次部署：

\`\`\`bash
mo-stock refresh-basics --with-ths --with-hm-list   # 概念元数据 + 游资名录
\`\`\`

每日运行：

\`\`\`bash
mo-stock run-once --date 2026-04-24 [--skip-enhanced]
\`\`\`

`--skip-enhanced` 跳过 5 个 ENHANCED ingest 步骤（题材 + 席位明细），只跑 6 个 CORE 步骤。
调试或 Tushare 限速时使用。

每周一次：

\`\`\`bash
mo-stock refresh-basics --with-hm-list   # 游资名录有新增/改名时
\`\`\`
```

**Step 6: 更新 `CLAUDE.md` 维度速查**

把当前 `5 维度` 描述改为 `6 维度`（limit / moneyflow / lhb / sector / **theme** / sentiment）。

**Step 7: 全量验证**

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m mypy src
```

**Step 8: 端到端冒烟（生产前必跑）**

```powershell
# 1. 首次刷新基础元数据
mo-stock refresh-basics --with-ths --with-hm-list

# 2. 跑一个最近交易日
mo-stock run-once --date 2026-04-24 --force

# 3. 检查 6 个维度得分都有
docker compose exec -T pg psql -U mo_stock -d mo_stock -c "SELECT dim, COUNT(*) FROM filter_score_daily WHERE trade_date='2026-04-24' GROUP BY dim;"
# 期望：limit / moneyflow / lhb / sector / theme 均有数据
```

**Step 9: Commit（不默认 push main）**

```powershell
git add alembic/versions/ docs/ CLAUDE.md
git commit -m "docs(theme-lhb): scoring/cli/CLAUDE 文档同步 + alembic migration"
```

Push 不放入默认执行步骤；只有用户明确确认后再 `git push origin main`。

---

## 5. Rollout Order

1. Task 1: Tushare client 5 个新接口 + 积分实测
2. Task 2: 6 张新表 + repo helpers + drop Lhb.seat
3. Task 3: ingest 清洗函数 + CORE/ENHANCED 分组
4. Task 4: ThemeFilter 独立维度
5. Task 5: LhbFilter v2 重排（base 60 + seat 40）
6. Task 6: weights.yaml + cli/scheduler 串联
7. Task 7: Alembic + docs + 端到端冒烟

每个 Task 完成后立即 commit；Task 4-6 任一不过则停下排查（评分核心，不能带病推进）。

---

## 6. Acceptance Criteria

### 功能

- [ ] `mo-stock refresh-basics --with-ths --with-hm-list` 填好 `ths_index` / `ths_member` / `hot_money_list`
- [ ] `mo-stock run-once --date YYYY-MM-DD` 填 `ths_daily` / `limit_concept_daily` / `ths_concept_moneyflow` / `lhb_seat_detail` / `hot_money_detail` 5 张增强表
- [ ] `mo-stock run-once --skip-enhanced` 只跑 6 个 CORE 步骤
- [ ] `filter_score_daily` 表中 `dim` 列出现 `theme` 数据
- [ ] `LhbFilter` 在有 seat 数据的票上 detail 含 `institution_net_buy` / `hot_money_net_buy` / `northbound_net_buy` 任一字段
- [ ] `Lhb.seat` JSONB 字段已被 alembic drop，模型/查询无引用

### 质量

- [ ] 当前测试套件保持全绿；不允许新增失败。如本地测试数量变化，以执行时 `pytest tests/ -q` 的实际结果为准
- [ ] 新增测试 ≥ 30 个，覆盖 5 个清洗函数 + 2 个 filter + 6 个 upsert + 席位分类
- [ ] ruff check src tests 干净
- [ ] mypy src 仅遗留 pre-existing 的 yaml/requests stub 缺失

### 性能

- [ ] `mo-stock run-once` 总耗时较接入前增量 < 30%（5 个新 ENHANCED 步骤约 +1-3 分钟）
- [ ] `ThemeFilter.score_all` 单次执行 < 5 秒（5500 只股 × 概念查询）
- [ ] `LhbFilter.score_all` 单次执行 < 2 秒（lhb 当日 < 200 只 × 席位查询）

### 文档

- [ ] `docs/scoring.md` 章节 5/6 同步
- [ ] `docs/cli.md` 题材工作流章节
- [ ] `CLAUDE.md` 维度速查 5→6 维
- [ ] `docs/tushare-interface-check.md` 更新已实测接口列表、权限/积分结果和返回字段样例

---

## 7. Risk Notes

- `ths_member` 是当前成分快照，**严谨历史回测需要每日快照**或改用支持 `trade_date` 的 `dc_member`。本次接入是"用现状成分跑当日"，对短线选股可接受。
- `hm_list` 游资名录是经验映射不是交易所官方身份；`seat_type=hot_money` 仅作加权参考，不能一票决定。
- `top_inst.side` 表示买入榜/卖出榜的位置（0/1），不等于买卖方向；本计划完全用 `net_buy` 数值判断方向，仅把 `side` 入库便于人工调试。
- 商业用途使用 THS / hm_list 数据需要联系同花顺/相应数据源额外授权；当前项目定位个人研究。
- `LhbSeatDetail` 表初期数据量约 200 行/天 × 250 交易日 = 5 万行/年，PG 不会有性能问题。
- 删除 `Lhb.seat` JSONB 是**单向迁移**：alembic downgrade 会重新加回字段但**不会恢复历史数据**（当前永远 None，无损失）。

---

## 8. Self-Review

- **Spec coverage**：v2 同时覆盖了原审计报告 P1-4（THS 板块）和 P1-5（机构/游资分离），并修正了 v1 的 10 项问题
- **Placeholder scan**：每个 task 包含具体文件、测试代码片段、命令；无未定义任务
- **Type consistency**：表名（snake_case）、Filter 类名（PascalCase）、repo 方法（`upsert_xxx` / `get_xxx`）、weights.yaml key 风格在所有任务间一致
- **Backward compat**：现有 5 维度逻辑不变；新增 theme 维度通过权重配置启用，旧权重文件加 `theme: 0.10` 后立即生效
- **TDD 流程**：所有 Task 都遵循"写失败测试 → 跑失败 → 实现 → 跑过 → commit"五步

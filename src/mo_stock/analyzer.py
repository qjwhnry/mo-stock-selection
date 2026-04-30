"""单只股票分析：传入一个 ts_code，跑一次规则层打分 + 硬规则判定。

与 `cli.run-once` 流程不同，本模块：
- 不写库（不落 `filter_score_daily` / `selection_result`）
- 不做 TOP N 排序
- 仅返回该股的打分细节，适合即席查询 / 交互式调试 / 被其它模块嵌入调用

核心函数 `analyze_stock(session, ts_code, trade_date)` 返回一个结构化 dict，
字段与 `render_md.render_daily_report` 生成的 JSON 中单只股票条目保持类似风格。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from config.settings import PROJECT_ROOT
from mo_stock.filters.base import load_weights_yaml
from mo_stock.filters.short.lhb_filter import LhbFilter
from mo_stock.filters.short.limit_filter import LimitFilter
from mo_stock.filters.short.moneyflow_filter import MoneyflowFilter
from mo_stock.filters.short.sector_filter import SectorFilter
from mo_stock.filters.short.theme_filter import ThemeFilter
from mo_stock.scorer.combine import _build_hard_reject_map, _weighted_combine
from mo_stock.storage import repo
from mo_stock.storage.models import DailyBasic, DailyKline

# 默认权重文件路径，与 cli.run-once 的加载位置保持一致
DEFAULT_WEIGHTS_PATH: Path = PROJECT_ROOT / "config" / "weights.yaml"


def analyze_stock(
    session: Session,
    ts_code: str,
    trade_date: date,
    weights_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对单只股票在指定交易日跑一次完整规则层分析。

    复用 `LimitFilter` / `MoneyflowFilter` 的 `score_all` 与
    `scorer.combine._build_hard_reject_map` 的已有逻辑，但仅挑出该 ts_code
    的记录返回，**不落库**、**不做 TOP N 排序**。

    参数：
        session: SQLAlchemy 会话（调用方负责 commit / rollback，此方法只读）
        ts_code: 股票代码，如 "600519.SH"
        trade_date: 分析的交易日
        weights_cfg: 权重配置字典；None 则从 `config/weights.yaml` 加载

    返回：
        dict，字段：
        - ts_code, trade_date
        - basic:        股票基础信息（StockBasic 表不存在时为 None）
        - kline:        当日 K 线（daily_kline 表无记录时为 None）
        - daily_basic:  当日基础指标（换手/PE/PB/市值，无记录时为 None）
        - dimensions:   {dim: {score, detail}}，只含该股实际有得分的维度
        - rule_score:   加权综合分 0-100；无任何维度得分时为 0
        - hard_reject:  {"rejected": bool, "reason": str | None}
    """
    # 1. 加载权重配置
    cfg: dict[str, Any] = weights_cfg or load_weights_yaml(DEFAULT_WEIGHTS_PATH)
    dim_weights: dict[str, float] = cfg.get("dimension_weights", {})
    hard_reject_cfg: dict[str, Any] = cfg.get("hard_reject", {})

    # 2. 股票基础信息
    basic = repo.get_stock_basic(session, ts_code)
    basic_out: dict[str, Any] | None = (
        {
            "name": basic.name,
            "industry": basic.industry,
            "sw_l1": basic.sw_l1,
            "list_date": basic.list_date,
            "is_st": basic.is_st,
        }
        if basic is not None
        else None
    )

    # 3. 当日行情 + 基础指标（单点查询，缺失则为 None）
    kline = session.get(DailyKline, (ts_code, trade_date))
    daily_basic = session.get(DailyBasic, (ts_code, trade_date))

    # 4. 规则层：复用 score_all，再按 ts_code 挑出该股得分
    #    说明：score_all 会扫描当日全市场数据，对单只股分析略显浪费，但保持
    #    与 run-once 完全一致的算法路径，避免代码分叉带来的策略漂移。
    limit_filter = LimitFilter(weights=cfg.get("limit_filter", {}))
    mf_filter = MoneyflowFilter(weights=cfg.get("moneyflow_filter", {}))
    lhb_filter = LhbFilter(weights=cfg.get("lhb_filter", {}))
    sector_filter = SectorFilter(weights=cfg.get("sector_filter", {}))
    theme_filter = ThemeFilter(weights=cfg.get("theme_filter", {}))

    all_results = [
        *limit_filter.score_all(session, trade_date),
        *mf_filter.score_all(session, trade_date),
        *lhb_filter.score_all(session, trade_date),
        *sector_filter.score_all(session, trade_date),
        *theme_filter.score_all(session, trade_date),
    ]

    dim_out: dict[str, dict[str, Any]] = {}
    for r in all_results:
        if r.ts_code == ts_code:
            dim_out[r.dim] = {"score": round(r.score, 2), "detail": r.detail}

    # 5. 综合分：复用 combine._weighted_combine（固定分母 = 全部权重之和）
    dim_scores_only = {d: info["score"] for d, info in dim_out.items() if info["score"] > 0}
    rule_score = _weighted_combine(dim_scores_only, dim_weights)

    # 6. 硬规则（复用 combine 层的私有实现，传入 candidates=[ts_code]）
    reject_map = _build_hard_reject_map(
        session, trade_date, hard_reject_cfg, [ts_code]
    )
    reject_reason = reject_map.get(ts_code)

    logger.info(
        "analyze_stock {} @ {} → rule_score={:.2f} dims={} reject={}",
        ts_code,
        trade_date,
        rule_score,
        list(dim_out.keys()),
        reject_reason,
    )

    return {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "basic": basic_out,
        "kline": _kline_dict(kline),
        "daily_basic": _daily_basic_dict(daily_basic),
        "dimensions": dim_out,
        "rule_score": round(rule_score, 2),
        "hard_reject": {
            "rejected": reject_reason is not None,
            "reason": reject_reason,
        },
    }


# ------------------------------------------------------------------
# 内部辅助
# ------------------------------------------------------------------

def _kline_dict(k: DailyKline | None) -> dict[str, Any] | None:
    """序列化 DailyKline 为 dict；无记录时返回 None。"""
    if k is None:
        return None
    return {
        "open": k.open,
        "high": k.high,
        "low": k.low,
        "close": k.close,
        "pre_close": k.pre_close,
        "pct_chg": k.pct_chg,
        "vol": k.vol,        # 手
        "amount": k.amount,  # 千元
    }


def _daily_basic_dict(b: DailyBasic | None) -> dict[str, Any] | None:
    """序列化 DailyBasic 为 dict；无记录时返回 None。"""
    if b is None:
        return None
    return {
        "turnover_rate": b.turnover_rate,
        "turnover_rate_f": b.turnover_rate_f,
        "volume_ratio": b.volume_ratio,
        "pe_ttm": b.pe_ttm,
        "pb": b.pb,
        "total_mv": b.total_mv,
        "circ_mv": b.circ_mv,
    }


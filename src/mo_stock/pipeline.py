"""选股流水线共享规则。

CLI 手动执行与 APScheduler 定时执行必须复用这里的过滤器清单和数据时点保护，
避免两个入口随版本演进产生策略漂移。
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from mo_stock.filters.base import FilterBase
from mo_stock.filters.short.exhaustion_filter import ExhaustionFilter
from mo_stock.filters.short.lhb_filter import LhbFilter
from mo_stock.filters.short.limit_filter import LimitFilter
from mo_stock.filters.short.limit_restart_filter import LimitRestartFilter
from mo_stock.filters.short.moneyflow_filter import MoneyflowFilter
from mo_stock.filters.short.sector_filter import SectorFilter
from mo_stock.filters.short.theme_filter import ThemeFilter
from mo_stock.filters.swing.catalyst_filter import CatalystFilter
from mo_stock.filters.swing.moneyflow_swing_filter import MoneyflowSwingFilter
from mo_stock.filters.swing.pullback_filter import PullbackFilter
from mo_stock.filters.swing.risk_liquidity_filter import RiskLiquidityFilter
from mo_stock.filters.swing.sector_swing_filter import SectorSwingFilter
from mo_stock.filters.swing.theme_swing_filter import ThemeSwingFilter
from mo_stock.filters.swing.trend_filter import TrendFilter

CN_TZ = ZoneInfo("Asia/Shanghai")
LHB_AVAILABLE_AFTER = time(15, 30)


def assert_lhb_data_available(
    trade_date: date,
    now: datetime | None = None,
) -> None:
    """确保当日选股发生在龙虎榜数据发布后。

    历史日期的数据已经完整，不受该保护限制。
    """
    now = now or datetime.now(CN_TZ)
    if trade_date != now.date():
        return
    if now.time() < LHB_AVAILABLE_AFTER:
        raise RuntimeError(
            f"执行时点 {now.strftime('%H:%M:%S')} 早于龙虎榜发布时间 15:30；"
            "提前选股会使用不完整数据，已阻止运行。请晚于 15:30 后再触发。"
        )


def build_filters(
    strategy: str,
    cfg: dict[str, Any],
) -> tuple[list[FilterBase], list[str]]:
    """按策略创建完整过滤器列表，并返回对应的入库维度。"""
    if strategy == "short":
        return [
            LimitFilter(weights=cfg.get("limit_filter", {})),
            LimitRestartFilter(weights=cfg.get("limit_restart_filter", {})),
            MoneyflowFilter(weights=cfg.get("moneyflow_filter", {})),
            LhbFilter(weights=cfg.get("lhb_filter", {})),
            SectorFilter(weights=cfg.get("sector_filter", {})),
            ThemeFilter(weights=cfg.get("theme_filter", {})),
            ExhaustionFilter(weights=cfg.get("exhaustion_filter", {})),
        ], [
            "limit", "limit_restart", "moneyflow", "lhb",
            "sector", "theme", "exhaustion",
        ]

    if strategy == "swing":
        return [
            TrendFilter(weights=cfg.get("trend_filter", {})),
            PullbackFilter(weights=cfg.get("pullback_filter", {})),
            MoneyflowSwingFilter(weights=cfg.get("moneyflow_swing_filter", {})),
            SectorSwingFilter(weights=cfg.get("sector_swing_filter", {})),
            ThemeSwingFilter(weights=cfg.get("theme_swing_filter", {})),
            CatalystFilter(weights=cfg.get("catalyst_filter", {})),
            RiskLiquidityFilter(weights=cfg.get("risk_liquidity_filter", {})),
        ], [
            "trend", "pullback", "moneyflow_swing", "sector_swing",
            "theme_swing", "catalyst", "risk_liquidity",
        ]

    raise ValueError("strategy 仅支持 short / swing")

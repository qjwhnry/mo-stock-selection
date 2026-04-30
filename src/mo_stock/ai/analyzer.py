"""单股 AI 分析入口（v2.2 plan §2.4）。

主函数 `analyze_stock_with_ai`：
- 用 4 段 prompt cache 调 Claude
- pydantic 校验输出（失败重试 1 次）
- 落库 ai_analysis 表（字段映射 schemas.score → AiAnalysis.ai_score 等）
- 任何失败返回 None（让 combine 走 ai_score=None 降级）

字段映射（v2.2 plan §2.1）：
| schemas.StockAiAnalysis | ORM AiAnalysis | 说明 |
|---|---|---|
| score (float 0-100) | ai_score (Integer) | round() + int() |
| thesis | thesis | 原样 |
| key_signals | key_catalysts | JSONB list |
| risks | risks | JSONB list |
| entry_price (float) | suggested_entry (str) | "{:.2f} 元" 或 None |
| stop_loss (float) | stop_loss (str) | "{:.2f} 元" 或 None |
| client.usage 4 字段 | input/output/cache_creation/cache_read_tokens | 直接透传 |
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from loguru import logger
from pydantic import ValidationError
from sqlalchemy.orm import Session

from config.settings import settings
from mo_stock.ai.client import ClaudeClient
from mo_stock.ai.prompts import (
    build_dynamic_stock_prompt,
    build_methodology_prompt,
    build_static_stock_prompt,
    build_system_prompt,
)
from mo_stock.ai.schemas import StockAiAnalysis
from mo_stock.filters.base import ScoreResult
from mo_stock.storage import repo
from mo_stock.storage.models import DailyKline, StockBasic


def _get_claude_client() -> ClaudeClient:
    """工厂函数：返回 ClaudeClient 实例。

    测试通过 monkeypatch 本函数替换真实 SDK（v2.2 plan §0.2.1 第 12 条）。
    用工厂模式避免 analyzer 在 import time 绑定具体类引用导致 monkeypatch 失效。
    """
    return ClaudeClient()


def analyze_stock_with_ai(
    session: Session,
    ts_code: str,
    trade_date: date,
    rule_dim_scores: dict[str, ScoreResult],
    strategy: str = "short",
) -> StockAiAnalysis | None:
    """对单股调 Claude 做深度分析。

    Args:
        session: SQLAlchemy session（读 stock_basic / daily_kline + 写 ai_analysis）
        ts_code: 股票代码，如 "600519.SH"
        trade_date: 选股交易日
        rule_dim_scores: 该股 5 维度得分 {dim: ScoreResult}（来自 filter_score_daily）

    Returns:
        StockAiAnalysis 对象（成功）或 None（任何失败：API 错、JSON 错、schema 错）
    """
    # 1. 构造 4 段 prompt
    try:
        system_p = build_system_prompt()
        method_p = build_methodology_prompt()
        static_p = _build_static_for_stock(session, ts_code)
        dynamic_p = _build_dynamic_for_stock(
            session, ts_code, trade_date, rule_dim_scores,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI prompt 构造失败 ts_code={} trade_date={}: {}",
                         ts_code, trade_date, exc)
        return None

    # 2. 调 Claude（失败重试 1 次：第 1 次结果 schema 错时再调一次）
    try:
        client = _get_claude_client()
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI client 初始化失败，跳过 AI 分析 ts_code={}: {}", ts_code, exc)
        return None
    last_usage: dict[str, int] = {}
    parsed: StockAiAnalysis | None = None

    for attempt in range(2):  # 最多 2 次：1 次原始 + 1 次重试
        try:
            raw, usage = client.analyze(system_p, method_p, static_p, dynamic_p)
            last_usage = usage
        except Exception as exc:  # noqa: BLE001
            # SDK 已经做过网络层重试（tenacity）；走到这层说明真失败
            logger.exception("AI client.analyze 失败 ts_code={} attempt={}: {}",
                             ts_code, attempt + 1, exc)
            return None

        parsed = _try_parse_response(raw, ts_code, attempt + 1)
        if parsed is not None:
            break

    if parsed is None:
        return None

    # 3. 落库 ai_analysis（按 §2.1 映射）
    try:
        _upsert_ai_analysis(session, parsed, trade_date, last_usage, strategy=strategy)
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI 分析落库失败 ts_code={}: {}", ts_code, exc)
        # 落库失败不影响 AI 分本身能用——返回 parsed 让 combine 用 ai_score
        # 但建议外层重试整个 run-once

    return parsed


# ---------------------------------------------------------------------------
# 内部辅助：prompt 上下文构造
# ---------------------------------------------------------------------------

def _build_static_for_stock(session: Session, ts_code: str) -> str:
    """从 DB 拿股票静态背景，构造 static_stock prompt。

    简化版：暂只取 stock_basic 字段；K 线/公告摘要留空（占位 "（无）"）。
    Phase 3+ 可补齐 30 日 K 线统计与公告抽取。
    """
    basic = session.get(StockBasic, ts_code)
    return build_static_stock_prompt(
        ts_code=ts_code,
        name=basic.name if basic else None,
        industry=basic.industry if basic else None,
        sw_l1=basic.sw_l1 if basic else None,
        kline_summary="",
        anns_summary="",
    )


def _build_dynamic_for_stock(
    session: Session,
    ts_code: str,
    trade_date: date,
    rule_dim_scores: dict[str, ScoreResult],
) -> str:
    """从 DB 拿当日行情快照 + 5 维度 detail，构造 dynamic_stock prompt。"""
    kline = session.get(DailyKline, (ts_code, trade_date))
    close = kline.close if kline else None
    pct_chg = kline.pct_chg if kline else None
    # daily_kline.amount 单位是千元，转换为亿元：千元 * 1000 / 1e8 = / 1e5
    amount_yi = (kline.amount / 1e5) if (kline and kline.amount) else None

    return build_dynamic_stock_prompt(
        ts_code=ts_code,
        trade_date=trade_date,
        dim_scores=rule_dim_scores,
        close=close,
        pct_chg=pct_chg,
        amount_yi=amount_yi,
    )


# ---------------------------------------------------------------------------
# 内部辅助：response 解析 + 落库
# ---------------------------------------------------------------------------

def _try_parse_response(raw: str, ts_code: str, attempt: int) -> StockAiAnalysis | None:
    """尝试 json.loads + pydantic validate；失败返回 None。"""
    # 容错：移除可能的 markdown 代码块包裹
    text = raw.strip()
    if text.startswith("```"):
        # 去掉 ```json / ``` 行
        lines = text.split("\n")
        text = "\n".join(line for line in lines if not line.startswith("```")).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("AI 响应 JSON 解析失败 ts_code={} attempt={}: {} | raw[:200]={}",
                       ts_code, attempt, exc, raw[:200])
        return None

    try:
        return StockAiAnalysis.model_validate(data)
    except ValidationError as exc:
        logger.warning("AI 响应 schema 校验失败 ts_code={} attempt={}: {}",
                       ts_code, attempt, exc.errors()[:3])
        return None


def _upsert_ai_analysis(
    session: Session,
    parsed: StockAiAnalysis,
    trade_date: date,
    usage: dict[str, int],
    strategy: str = "short",
) -> None:
    """把 StockAiAnalysis pydantic 模型 + token usage 写入 ai_analysis 表。

    字段映射见 §2.1。
    """
    row: dict[str, Any] = {
        "trade_date": trade_date,
        "strategy": strategy,
        "ts_code": parsed.ts_code,
        "ai_score": int(round(parsed.score)),  # float → Integer
        "thesis": parsed.thesis,
        "key_catalysts": parsed.key_signals,    # → JSONB list
        "risks": parsed.risks,                  # → JSONB list
        "suggested_entry": (
            f"{parsed.entry_price:.2f} 元"
            if parsed.entry_price is not None else None
        ),
        "stop_loss": (
            f"{parsed.stop_loss:.2f} 元"
            if parsed.stop_loss is not None else None
        ),
        "model": settings.anthropic_model,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_creation_tokens": usage.get("cache_creation_tokens"),
        "cache_read_tokens": usage.get("cache_read_tokens"),
    }
    repo.upsert_ai_analysis(session, [row])
    session.flush()

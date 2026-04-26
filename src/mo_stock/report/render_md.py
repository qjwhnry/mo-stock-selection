"""把 selection_result 渲染成 Markdown 日报 + JSON 产出（v2.2 plan §4）。

v2.2 重写：每只入选股展示**完整选出原因**——
- AI 论点 + key_signals + 操作建议 + risks
- 5 维度证据表（detail 翻译为中文人话，例如 institution_net_buy → "机构净买 1900 万"）
- AI 缺失时优雅降级：不渲染 AI 章节，但维度证据仍完整

JSON 同步含结构化 `rationale` 字段。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from mo_stock.storage.models import (
    AiAnalysis,
    DailyKline,
    FilterScoreDaily,
    SelectionResult,
    StockBasic,
)


def render_daily_report(
    session: Session,
    trade_date: date,
    output_dir: Path,
    phase: str = "v2.2（5 维规则 + Claude AI 融合）",
) -> tuple[Path, Path]:
    """渲染指定交易日的报告，返回 (md_path, json_path)。"""
    selections = session.execute(
        select(SelectionResult)
        .where(SelectionResult.trade_date == trade_date)
        .where(SelectionResult.picked.is_(True))
        .order_by(SelectionResult.rank)
    ).scalars().all()

    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"{trade_date.isoformat()}.md"
    json_path = output_dir / f"{trade_date.isoformat()}.json"

    if not selections:
        md_path.write_text(
            f"# {trade_date} 选股报告\n\n**当日无入选股票**。\n",
            encoding="utf-8",
        )
        json_path.write_text("[]", encoding="utf-8")
        logger.warning("render_daily_report: {} 无入选股票", trade_date)
        return md_path, json_path

    # 预取辅助数据（一次性 IN 查询，避免 N+1）
    ts_codes = [s.ts_code for s in selections]
    basics = {
        b.ts_code: b for b in session.execute(
            select(StockBasic).where(StockBasic.ts_code.in_(ts_codes))
        ).scalars()
    }
    klines = {
        (k.ts_code, k.trade_date): k for k in session.execute(
            select(DailyKline)
            .where(DailyKline.ts_code.in_(ts_codes))
            .where(DailyKline.trade_date == trade_date)
        ).scalars()
    }
    scores_by_stock: dict[str, dict[str, FilterScoreDaily]] = {}
    for row in session.execute(
        select(FilterScoreDaily)
        .where(FilterScoreDaily.ts_code.in_(ts_codes))
        .where(FilterScoreDaily.trade_date == trade_date)
    ).scalars():
        scores_by_stock.setdefault(row.ts_code, {})[row.dim] = row

    ai_by_stock: dict[str, AiAnalysis] = {
        a.ts_code: a for a in session.execute(
            select(AiAnalysis)
            .where(AiAnalysis.ts_code.in_(ts_codes))
            .where(AiAnalysis.trade_date == trade_date)
        ).scalars()
    }

    # ---------- 渲染 Markdown ----------
    md_lines: list[str] = [
        f"# {trade_date} A 股选股日报（{phase}）",
        "",
        f"> 产出时间：{trade_date} 15:30 收盘后",
        f"> TOP {len(selections)}，按 `final_score` 排序",
        "",
        "## 候选股清单",
        "",
        "| # | 代码 | 名称 | 收盘 | 涨跌幅 | rule_score | ai_score | final_score |",
        "|---|------|------|------|--------|-----------|----------|-------------|",
    ]
    for sel in selections:
        basic = basics.get(sel.ts_code)
        name = basic.name if basic else "-"
        kline = klines.get((sel.ts_code, trade_date))
        close = f"{kline.close:.2f}" if kline and kline.close else "-"
        pct = f"{kline.pct_chg:+.2f}%" if kline and kline.pct_chg is not None else "-"
        ai_score = f"{sel.ai_score:.1f}" if sel.ai_score is not None else "—"
        md_lines.append(
            f"| {sel.rank} | {sel.ts_code} | {name} | {close} | {pct} "
            f"| {sel.rule_score:.1f} | {ai_score} | **{sel.final_score:.1f}** |"
        )
    md_lines.append("")
    md_lines.append("## 每只股票详情（选出原因）")
    md_lines.append("")

    for sel in selections:
        md_lines.extend(_render_one_stock_section(
            sel,
            basics.get(sel.ts_code),
            klines.get((sel.ts_code, trade_date)),
            scores_by_stock.get(sel.ts_code, {}),
            ai_by_stock.get(sel.ts_code),
        ))

    md_lines.append("---")
    md_lines.append("")
    md_lines.append("**免责声明**：本报告仅用于技术研究，不构成投资建议。投资有风险，入市需谨慎。")
    md_lines.append("")

    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    # ---------- 渲染 JSON（含结构化 rationale）----------
    json_data = [
        _build_json_entry(
            sel,
            basics.get(sel.ts_code),
            scores_by_stock.get(sel.ts_code, {}),
            ai_by_stock.get(sel.ts_code),
        )
        for sel in selections
    ]
    json_path.write_text(
        json.dumps(json_data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    logger.info("报告已生成：{} / {}", md_path, json_path)
    return md_path, json_path


# ---------------------------------------------------------------------------
# 单股 Markdown 块渲染（按 v2.2 plan §4.2 模板）
# ---------------------------------------------------------------------------

def _render_one_stock_section(
    sel: SelectionResult,
    basic: StockBasic | None,
    kline: DailyKline | None,
    dim_scores: dict[str, FilterScoreDaily],
    ai: AiAnalysis | None,
) -> list[str]:
    """渲染单只入选股的完整 markdown 块。

    结构（plan §4.2）：
    1. 标题：rank / ts_code / name + rule/ai/final 三分
    2. AI 论点（如有）
    3. AI 关键信号（如有）
    4. 选出维度证据表（中文翻译）
    5. 操作建议：入场/止损（如有）
    6. 风险（如有）
    """
    name = basic.name if basic else "-"
    ai_part = f"  ai={sel.ai_score:.1f}" if sel.ai_score is not None else "  ai=—"

    lines = [
        f"### {sel.rank}. {sel.ts_code} {name}  rule={sel.rule_score:.1f}{ai_part}  → **final={sel.final_score:.1f}**",
        "",
    ]

    # AI 章节（缺失时整段不渲染）
    if ai:
        lines.append(f"**🤖 AI 论点**：{ai.thesis}")
        lines.append("")
        if ai.key_catalysts:
            lines.append("**关键信号**：")
            for sig in ai.key_catalysts:
                lines.append(f"- ✅ {sig}")
            lines.append("")

    # 选出维度证据表
    lines.append("**📊 选出维度证据**：")
    lines.append("")
    lines.append("| 维度 | 得分 | 命中证据 |")
    lines.append("|------|------|---------|")
    for dim in ("limit", "moneyflow", "lhb", "sector", "theme", "sentiment"):
        if dim in dim_scores:
            r = dim_scores[dim]
            evidences = _translate_dim_detail(dim, r.detail or {})
            evidence_str = "；".join(evidences) if evidences else "—"
            lines.append(f"| {dim} | {r.score:.1f} | {evidence_str} |")
        # 未命中维度不渲染（避免噪音；6 维都全保留就是表过长）
    lines.append("")

    # 操作建议（AI 给出 entry/stop_loss 时才渲染）
    if ai and (ai.suggested_entry or ai.stop_loss):
        lines.append("**💰 操作建议**：")
        if ai.suggested_entry:
            lines.append(f"- 入场：{ai.suggested_entry}")
        if ai.stop_loss:
            lines.append(f"- 止损：{ai.stop_loss}")
        lines.append("")

    # 风险（AI 给出时渲染）
    if ai and ai.risks:
        lines.append("**⚠️ 风险**：")
        for risk in ai.risks:
            lines.append(f"- {risk}")
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# 维度 detail → 中文人话证据列表（v2.2 plan §4.3）
# ---------------------------------------------------------------------------

def _translate_lhb(detail: dict[str, Any]) -> list[str]:
    """LhbFilter detail（v2.1 base + seat 双层）→ 人友好证据。"""
    e: list[str] = []
    if detail.get("net_rate_pct"):
        e.append(f"龙虎榜净买入率 {detail['net_rate_pct']}%")
    if detail.get("amount_rate_pct"):
        e.append(f"席位成交占比 {detail['amount_rate_pct']}%")
    if "institution_net_buy" in detail:
        e.append(f"机构净买 {detail['institution_net_buy'] / 1e4:.0f} 万")
    if "hot_money_net_buy" in detail:
        e.append(f"知名游资净买 {detail['hot_money_net_buy'] / 1e4:.0f} 万")
    if "hot_money_sell_penalty" in detail:
        e.append(f"⚠️ 知名游资大额净卖（扣 {abs(detail['hot_money_sell_penalty'])} 分）")
    if "northbound_net_buy" in detail:
        e.append(f"北向净买 {detail['northbound_net_buy'] / 1e4:.0f} 万")
    if detail.get("reason"):
        e.append(f"上榜原因：{detail['reason']}")
    return e


def _translate_theme(detail: dict[str, Any]) -> list[str]:
    """ThemeFilter detail → 人友好证据。"""
    e: list[str] = []
    concept = detail.get("best_concept")
    ths_rank = detail.get("ths_rank") or 0
    limit_rank = detail.get("limit_rank") or 0
    net_amount = detail.get("concept_net_amount_yi") or 0.0

    if concept:
        parts = [f"命中概念 {concept}"]
        if ths_rank:
            parts.append(f"概念涨幅 TOP {ths_rank}")
        if limit_rank:
            parts.append(f"涨停最强榜 TOP {limit_rank}")
        e.append("，".join(parts))
    if net_amount > 0:
        e.append(f"概念资金净流入 {net_amount} 亿")
    return e


def _translate_moneyflow(detail: dict[str, Any]) -> list[str]:
    """MoneyflowFilter detail → 人友好证据。"""
    e: list[str] = []
    net_mf_wan = detail.get("net_mf_wan")
    if net_mf_wan and net_mf_wan > 0:
        # net_mf_wan 单位是万元；> 1 万 = > 1 亿（万元×万 = 亿）
        if net_mf_wan >= 10000:
            e.append(f"主力净流入 {net_mf_wan / 10000:.2f} 亿")
        else:
            e.append(f"主力净流入 {net_mf_wan:.0f} 万")
    if "net_mf_ratio_pct" in detail:
        e.append(f"占当日成交 {detail['net_mf_ratio_pct']}%")
    if "big_ratio" in detail and detail["big_ratio"] > 0:
        e.append(f"大单+超大单净流入占比 {detail['big_ratio'] * 100:.1f}%")
    if detail.get("rolling_3d_wan"):
        v = detail["rolling_3d_wan"]
        if v > 0:
            unit = f"{v / 10000:.2f} 亿" if abs(v) >= 10000 else f"{v:.0f} 万"
            e.append(f"近 3 日累计 +{unit}")
    if "small_up_big_down_penalty" in detail:
        e.append(f"⚠️ 小单买大单卖（扣 {abs(detail['small_up_big_down_penalty'])} 分）")
    return e


def _translate_limit(detail: dict[str, Any]) -> list[str]:
    """LimitFilter detail → 人友好证据。

    LimitFilter detail 结构灵活；通用做法：把含 _bonus 的 key 翻译成"+N 分"。
    """
    e: list[str] = []
    if detail.get("first_board_bonus"):
        e.append(f"首板封板（+{detail['first_board_bonus']} 分）")
    if detail.get("second_board_bonus"):
        e.append(f"连板（+{detail['second_board_bonus']} 分）")
    if detail.get("broken_board_rebound_bonus"):
        e.append(f"断板反包（+{detail['broken_board_rebound_bonus']} 分）")
    if detail.get("seal_amount_bonus"):
        e.append(f"封单额加分 +{detail['seal_amount_bonus']}")
    if detail.get("open_times_penalty"):
        e.append(f"⚠️ 多次开板（-{detail['open_times_penalty']} 分）")
    return e


def _translate_sector(detail: dict[str, Any]) -> list[str]:
    """SectorFilter detail → 人友好证据。"""
    e: list[str] = []
    rank = detail.get("sector_rank")
    if rank:
        e.append(f"申万行业涨幅 TOP {rank}（+{detail.get('rank_bonus', 0)} 分）")
    avg = detail.get("sector_3d_avg")
    if avg is not None and detail.get("trend_bonus"):
        e.append(f"行业 3 日均涨 {avg}%（+{detail['trend_bonus']} 分）")
    if detail.get("l1_code"):
        e.append(f"行业代码 {detail['l1_code']}")
    return e


# 维度 → 翻译器映射
_DIM_TRANSLATORS = {
    "limit": _translate_limit,
    "moneyflow": _translate_moneyflow,
    "lhb": _translate_lhb,
    "sector": _translate_sector,
    "theme": _translate_theme,
}


def _translate_dim_detail(dim: str, detail: dict[str, Any]) -> list[str]:
    """统一入口：dim → 对应翻译器；未知维度优雅降级（返回空 list）。"""
    fn = _DIM_TRANSLATORS.get(dim)
    if fn is None:
        return []
    try:
        return fn(detail)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_translate_dim_detail({}) 异常 detail={}: {}", dim, detail, exc)
        return []


# ---------------------------------------------------------------------------
# JSON 结构化 rationale
# ---------------------------------------------------------------------------

def _build_json_entry(
    sel: SelectionResult,
    basic: StockBasic | None,
    dim_scores: dict[str, FilterScoreDaily],
    ai: AiAnalysis | None,
) -> dict[str, Any]:
    """构造单只股的 JSON entry，含结构化 `rationale`。"""
    return {
        "rank": sel.rank,
        "ts_code": sel.ts_code,
        "name": basic.name if basic else None,
        "rule_score": float(sel.rule_score),
        "ai_score": float(sel.ai_score) if sel.ai_score is not None else None,
        "final_score": float(sel.final_score),
        "rationale": {
            "ai_thesis": ai.thesis if ai else None,
            "key_signals": ai.key_catalysts if ai else [],
            "dim_evidences": {
                dim: _translate_dim_detail(dim, r.detail or {})
                for dim, r in dim_scores.items()
            },
            "trade_plan": {
                "entry": ai.suggested_entry if ai else None,
                "stop_loss": ai.stop_loss if ai else None,
            } if ai else None,
            "risks": ai.risks if ai else [],
        },
        "dimensions": {
            dim: {"score": r.score, "detail": r.detail}
            for dim, r in dim_scores.items()
        },
        "ai": {
            "model": ai.model,
            "ai_score": ai.ai_score,
            "thesis": ai.thesis,
            "catalysts": ai.key_catalysts,
            "risks": ai.risks,
            "entry": ai.suggested_entry,
            "stop_loss": ai.stop_loss,
            "tokens": {
                "input": ai.input_tokens,
                "output": ai.output_tokens,
                "cache_creation": ai.cache_creation_tokens,
                "cache_read": ai.cache_read_tokens,
            },
        } if ai else None,
    }

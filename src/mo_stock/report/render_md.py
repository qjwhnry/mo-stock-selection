"""把 selection_result 渲染成 Markdown 日报 + JSON 产出。"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

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
    phase: str = "Phase 1（4 维度：limit + moneyflow + lhb + sector）",
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

    # 预取辅助数据
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

    # 渲染 Markdown
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
    md_lines.append("## 每只股票详情")
    md_lines.append("")

    for sel in selections:
        basic = basics.get(sel.ts_code)
        name = basic.name if basic else "-"
        md_lines.append(f"### #{sel.rank} {sel.ts_code} {name}  (final={sel.final_score:.1f})")
        md_lines.append("")

        # 规则分细节
        dim_scores = scores_by_stock.get(sel.ts_code, {})
        if dim_scores:
            md_lines.append("**规则分 5 维度：**")
            md_lines.append("")
            md_lines.append("| 维度 | 得分 | 关键细节 |")
            md_lines.append("|------|------|----------|")
            for dim in ("limit", "moneyflow", "lhb", "sector", "sentiment"):
                if dim in dim_scores:
                    r = dim_scores[dim]
                    detail_str = _format_detail(r.detail)
                    md_lines.append(f"| {dim} | {r.score:.1f} | {detail_str} |")
                else:
                    md_lines.append(f"| {dim} | — | （未覆盖） |")
            md_lines.append("")

        # AI 分析
        ai = ai_by_stock.get(sel.ts_code)
        if ai:
            md_lines.append(f"**AI 分析**（model={ai.model}, score={ai.ai_score}）：")
            md_lines.append("")
            md_lines.append(f"> {ai.thesis}")
            md_lines.append("")
            if ai.key_catalysts:
                md_lines.append(f"- **催化剂**：{'; '.join(ai.key_catalysts)}")
            if ai.risks:
                md_lines.append(f"- **风险**：{'; '.join(ai.risks)}")
            if ai.suggested_entry:
                md_lines.append(f"- **建议入场**：{ai.suggested_entry}")
            if ai.stop_loss:
                md_lines.append(f"- **止损位**：{ai.stop_loss}")
            md_lines.append("")

    md_lines.append("---")
    md_lines.append("")
    md_lines.append("**免责声明**：本报告仅用于技术研究，不构成投资建议。投资有风险，入市需谨慎。")
    md_lines.append("")

    md_content = "\n".join(md_lines)
    md_path.write_text(md_content, encoding="utf-8")

    # 渲染 JSON
    def _name(ts_code: str) -> str | None:
        b = basics.get(ts_code)
        return b.name if b else None

    json_data = [
        {
            "rank": sel.rank,
            "ts_code": sel.ts_code,
            "name": _name(sel.ts_code),
            "rule_score": float(sel.rule_score),
            "ai_score": float(sel.ai_score) if sel.ai_score is not None else None,
            "final_score": float(sel.final_score),
            "dimensions": {
                dim: {"score": r.score, "detail": r.detail}
                for dim, r in scores_by_stock.get(sel.ts_code, {}).items()
            },
            "ai": (
                {
                    "model": ai_by_stock[sel.ts_code].model,
                    "ai_score": ai_by_stock[sel.ts_code].ai_score,
                    "thesis": ai_by_stock[sel.ts_code].thesis,
                    "catalysts": ai_by_stock[sel.ts_code].key_catalysts,
                    "risks": ai_by_stock[sel.ts_code].risks,
                    "entry": ai_by_stock[sel.ts_code].suggested_entry,
                    "stop_loss": ai_by_stock[sel.ts_code].stop_loss,
                }
                if sel.ts_code in ai_by_stock
                else None
            ),
        }
        for sel in selections
    ]
    json_path.write_text(
        json.dumps(json_data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    logger.info("报告已生成：{} / {}", md_path, json_path)
    return md_path, json_path


def _format_detail(detail: dict | None, max_items: int = 4) -> str:
    """把 detail 字典格式化成一行简短字符串。

    对 tuple 值做安全格式化：仅当第二项是 int/float 时才用 `:+g` 格式。
    """
    if not detail:
        return "-"
    parts = []
    for i, (k, v) in enumerate(detail.items()):
        if i >= max_items:
            parts.append("...")
            break
        if isinstance(v, tuple) and len(v) == 2 and isinstance(v[1], (int, float)):
            parts.append(f"{k}={v[0]}({v[1]:+g})")
        elif isinstance(v, float):
            parts.append(f"{k}={v:.2f}")
        else:
            parts.append(f"{k}={v}")
    return ", ".join(parts)

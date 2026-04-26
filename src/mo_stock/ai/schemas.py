"""AI 输出契约（v2.2 plan §2.1）。

StockAiAnalysis 是 Claude 单股分析的 pydantic 输出 schema：
- score / thesis / entry_price / stop_loss / key_signals / risks
- ts_code 严格正则校验（防 AI 幻觉）
- 字段越界 → ValidationError → analyzer 层重试 1 次

落库时映射到 AiAnalysis ORM 表字段，见 v2.1 plan §2.1 映射表（不在本模块负责）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class StockAiAnalysis(BaseModel):
    """Claude 单股分析输出契约。

    所有字段都有约束，越界 → ValidationError。analyzer.py 捕获后重试 1 次。
    """

    ts_code: str = Field(
        pattern=r"^\d{6}\.(SH|SZ|BJ)$",
        description="6 位数字 + .SH/.SZ/.BJ",
    )
    score: float = Field(
        ge=0, le=100,
        description="AI 综合质量分 0-100，AI 的增量判断（不必等于规则分）",
    )
    thesis: str = Field(
        min_length=20, max_length=500,
        description="选股论点（中文，1-3 句）",
    )
    entry_price: float | None = Field(
        default=None, ge=0,
        description="建议入场价（元）；不确定就 null",
    )
    stop_loss: float | None = Field(
        default=None, ge=0,
        description="止损价（元）；不确定就 null",
    )
    key_signals: list[str] = Field(
        default_factory=list, max_length=5,
        description="关键信号列表，每条 < 50 字",
    )
    risks: list[str] = Field(
        default_factory=list, max_length=3,
        description="风险提示列表，每条 < 50 字",
    )

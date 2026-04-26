"""AI 分析层（v2.2 plan 已实现）。

主入口：`analyze_stock_with_ai(session, ts_code, trade_date, rule_dim_scores)`。
- 用 Anthropic Claude SDK + 4 段 prompt cache 做单股深度分析
- 输出 StockAiAnalysis pydantic 对象（schema 校验失败重试 1 次）
- 落库 ai_analysis 表（字段映射见 v2.2 plan §2.1）
- 任何失败返回 None（让 combine 走 ai_score=None 降级）

公共导出：
- ClaudeClient: SDK 包装单例
- StockAiAnalysis: pydantic 输出契约
- analyze_stock_with_ai: 单股入口
- 4 个 prompt builders: build_{system,methodology,static_stock,dynamic_stock}_prompt
"""
from mo_stock.ai.analyzer import analyze_stock_with_ai
from mo_stock.ai.client import ClaudeClient, reset_singleton_for_test
from mo_stock.ai.prompts import (
    build_dynamic_stock_prompt,
    build_methodology_prompt,
    build_static_stock_prompt,
    build_system_prompt,
)
from mo_stock.ai.schemas import StockAiAnalysis

__all__ = [
    "ClaudeClient",
    "StockAiAnalysis",
    "analyze_stock_with_ai",
    "build_dynamic_stock_prompt",
    "build_methodology_prompt",
    "build_static_stock_prompt",
    "build_system_prompt",
    "reset_singleton_for_test",
]

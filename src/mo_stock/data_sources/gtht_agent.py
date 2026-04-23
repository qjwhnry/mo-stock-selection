"""GTHT 辅助 LLM 代理。

使用 OpenAI 兼容 LLM（DeepSeek / Qwen / Kimi 等）+ function calling，
让 LLM 自动选择合适的 GTHT skill tool 并填参，再交给 GthtClient 执行。

详细设计：docs/superpowers/specs/2026-04-23-gtht-client-design.md §4.2
"""
from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI

from config.settings import settings
from mo_stock.data_sources.gtht_client import GthtClient, GthtError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tools schema：OpenAI function calling 格式
# 每个 tool 对应一个 GTHT skill 的核心入口
# ---------------------------------------------------------------------------

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "research_report",
            "description": (
                "查询国泰海通券商研究报告。适用场景："
                "（1）某只股票/板块最近 N 天的研报评级与目标价；"
                "（2）特定主题/概念的研报观点汇总。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "自然语言查询，如 '600519 最近30天研报' 或 'AI 算力研报'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rank_list",
            "description": (
                "查询资金/涨跌幅/换手率等热榜（国泰海通灵犀热榜）。"
                "适用于交叉验证主力资金动向、热门板块识别等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "自然语言查询，如 '今日资金净流入 TOP 20' 或 '近5日涨幅榜'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "financial_search",
            "description": (
                "自然语言查询 A 股行情、技术指标、F10 财务数据、公司基本信息。"
                "适用于多指标筛股、个股深度数据查询。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "自然语言查询，如 '涨幅大于5%且换手率大于3%的股票'",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

# tool name → (skill_dir_name, gateway_name, tool_name)
_TOOL_DISPATCH: dict[str, tuple[str, str, str]] = {
    "research_report": ("lingxi-researchreport-skill", "researchreport", "research"),
    "rank_list": ("lingxi-ranklist-skill", "ranklist", "ranklist"),
    "financial_search": (
        "lingxi-smartstockselection-skill",
        "financial",
        "financial-search",
    ),
}


class GthtAgent:
    """GTHT 辅助 LLM 代理。

    用法：
        agent = GthtAgent()
        result = agent.ask("查 600519 最近研报评级和热榜表现")
        print(result["answer"])
        print(result["tool_trace"])
    """

    def __init__(self, client: GthtClient | None = None) -> None:
        # 校验三个 settings 字段必须全配齐
        if not (
            settings.gtht_llm_api_key
            and settings.gtht_llm_base_url
            and settings.gtht_llm_model
        ):
            raise GthtError(
                "GTHT_LLM_API_KEY / GTHT_LLM_BASE_URL / GTHT_LLM_MODEL 必须全部配置"
            )
        self._client = client or GthtClient()
        self._llm = OpenAI(
            api_key=settings.gtht_llm_api_key,
            base_url=settings.gtht_llm_base_url,
        )
        self._model = settings.gtht_llm_model

    def ask(self, query: str, max_iters: int = 5) -> dict[str, Any]:
        """让辅助 LLM 自主选择并调用 GTHT skill tool 回答 query。

        返回 {"answer": str, "tool_trace": list[dict]}。
        超过 max_iters 抛 GthtError 防止死循环。
        """
        messages: list[dict[str, Any]] = [{"role": "user", "content": query}]
        tool_trace: list[dict[str, Any]] = []

        for it in range(max_iters):
            response = self._llm.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                tools=_TOOLS,  # type: ignore[arg-type]
            )
            msg = response.choices[0].message

            # 无 tool_calls → 终止，返回最终答案
            if not msg.tool_calls:
                return {"answer": msg.content or "", "tool_trace": tool_trace}

            # 把 assistant 的 tool_calls 消息回写到 messages
            messages.append(msg.model_dump(exclude_none=True))

            for tc in msg.tool_calls:
                tool_name = tc.function.name  # type: ignore[union-attr]
                try:
                    args = json.loads(tc.function.arguments or "{}")  # type: ignore[union-attr]
                except json.JSONDecodeError:
                    args = {}

                if tool_name not in _TOOL_DISPATCH:
                    tool_result: Any = {"error": f"未知工具: {tool_name}"}
                else:
                    skill, gateway, tool = _TOOL_DISPATCH[tool_name]
                    try:
                        tool_result = self._client.call(skill, gateway, tool, **args)
                    except GthtError as exc:
                        tool_result = {"error": str(exc)}

                tool_trace.append(
                    {"tool": tool_name, "args": args, "result": tool_result}
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    }
                )

            logger.debug("GthtAgent iter %d done, %d tool_calls", it + 1, len(msg.tool_calls))

        raise GthtError(f"LLM tool loop exceeded max_iters={max_iters}")

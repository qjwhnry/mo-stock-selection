"""GthtAgent 测试：tools schema 与 function calling 循环。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from mo_stock.data_sources.gtht_agent import _TOOL_DISPATCH, _TOOLS, GthtAgent
from mo_stock.data_sources.gtht_client import GthtError


class TestToolsSchema:
    def test_tools_have_required_openai_fields(self) -> None:
        """每个 tool 必须有 type=function + function.name/description/parameters"""
        for t in _TOOLS:
            assert t["type"] == "function"
            fn = t["function"]
            assert isinstance(fn["name"], str) and fn["name"]
            assert isinstance(fn["description"], str) and fn["description"]
            assert fn["parameters"]["type"] == "object"
            assert "query" in fn["parameters"]["properties"]

    def test_dispatch_table_covers_all_tools(self) -> None:
        """_TOOLS 中每个 tool 必须在 _TOOL_DISPATCH 中有 (skill, gateway, tool) 映射"""
        tool_names = {t["function"]["name"] for t in _TOOLS}
        assert tool_names == set(_TOOL_DISPATCH.keys())

    def test_dispatch_targets_are_known_skills(self) -> None:
        """所有 dispatch 目标 skill 都应在 PLAN.md 用到的 3 个里"""
        expected_skills = {
            "lingxi-researchreport-skill",
            "lingxi-ranklist-skill",
            "lingxi-smartstockselection-skill",
        }
        actual_skills = {skill for skill, _, _ in _TOOL_DISPATCH.values()}
        assert actual_skills == expected_skills


@pytest.fixture
def configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 GthtAgent 构造不抛错。"""
    monkeypatch.setattr(
        "mo_stock.data_sources.gtht_agent.settings.gtht_llm_api_key", "sk-test"
    )
    monkeypatch.setattr(
        "mo_stock.data_sources.gtht_agent.settings.gtht_llm_base_url",
        "https://api.example.com/v1",
    )
    monkeypatch.setattr(
        "mo_stock.data_sources.gtht_agent.settings.gtht_llm_model", "test-model"
    )


def _llm_message(content: str | None = None, tool_calls: list | None = None) -> MagicMock:
    """构造 openai 风格的 ChatCompletionMessage 假对象。"""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    msg.model_dump = lambda exclude_none=False, **kwargs: {  # noqa: ARG005
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls,
    }
    return msg


def _llm_response(message: MagicMock) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=message)]
    return resp


def _tool_call(call_id: str, name: str, args: dict) -> MagicMock:
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    return tc


class TestAsk:
    def test_no_tool_calls_returns_direct_answer(
        self, configured_env: None
    ) -> None:
        """LLM 直接回答（不调 tool）→ 返回 answer，tool_trace 为空"""
        client = MagicMock()
        agent = GthtAgent(client=client)

        agent._llm = MagicMock()
        agent._llm.chat.completions.create.return_value = _llm_response(
            _llm_message(content="直接回答")
        )

        result = agent.ask("hello")
        assert result == {"answer": "直接回答", "tool_trace": []}
        client.call.assert_not_called()

    def test_single_tool_call_dispatches_to_client(
        self, configured_env: None
    ) -> None:
        """LLM 选 research_report → 应调 GthtClient.call(researchreport-skill, ...)"""
        client = MagicMock()
        client.call.return_value = {"data": "研报内容"}

        agent = GthtAgent(client=client)
        agent._llm = MagicMock()

        # 第一轮：LLM 返回 tool_call
        first = _llm_response(
            _llm_message(
                tool_calls=[_tool_call("c1", "research_report", {"query": "600519"})]
            )
        )
        # 第二轮：LLM 看到 tool 结果后返回最终答案
        second = _llm_response(_llm_message(content="研报概要：..."))
        agent._llm.chat.completions.create.side_effect = [first, second]

        result = agent.ask("查 600519 研报")
        assert result["answer"] == "研报概要：..."
        client.call.assert_called_once_with(
            "lingxi-researchreport-skill", "researchreport", "research", query="600519"
        )
        assert len(result["tool_trace"]) == 1
        assert result["tool_trace"][0]["tool"] == "research_report"

    def test_unknown_tool_returns_error_to_llm(
        self, configured_env: None
    ) -> None:
        """LLM 调了不在 dispatch 表里的 tool → 错误回传给 LLM 继续推理"""
        client = MagicMock()
        agent = GthtAgent(client=client)
        agent._llm = MagicMock()

        first = _llm_response(
            _llm_message(tool_calls=[_tool_call("c1", "unknown_tool", {})])
        )
        second = _llm_response(_llm_message(content="无法调用"))
        agent._llm.chat.completions.create.side_effect = [first, second]

        result = agent.ask("?")
        client.call.assert_not_called()
        assert "未知工具" in result["tool_trace"][0]["result"]["error"]

    def test_client_error_returned_to_llm(self, configured_env: None) -> None:
        """GthtClient.call 抛 GthtError → 错误回传 LLM 继续推理（不向上抛）"""
        client = MagicMock()
        client.call.side_effect = GthtError("Network down")
        agent = GthtAgent(client=client)
        agent._llm = MagicMock()

        first = _llm_response(
            _llm_message(
                tool_calls=[_tool_call("c1", "research_report", {"query": "x"})]
            )
        )
        second = _llm_response(_llm_message(content="服务暂不可用"))
        agent._llm.chat.completions.create.side_effect = [first, second]

        result = agent.ask("?")
        assert result["answer"] == "服务暂不可用"
        assert result["tool_trace"][0]["result"] == {"error": "Network down"}

    def test_max_iters_exceeded_raises(self, configured_env: None) -> None:
        """LLM 一直返回 tool_calls，超过 max_iters → 抛 GthtError"""
        client = MagicMock(**{"call.return_value": {"ok": True}})
        agent = GthtAgent(client=client)
        agent._llm = MagicMock()

        always_tool = _llm_response(
            _llm_message(
                tool_calls=[_tool_call("c1", "research_report", {"query": "x"})]
            )
        )
        agent._llm.chat.completions.create.return_value = always_tool

        with pytest.raises(GthtError, match="max_iters"):
            agent.ask("?", max_iters=2)

    def test_multiple_tool_calls_in_single_response(
        self, configured_env: None
    ) -> None:
        """LLM 一次返回 2 个 tool_calls → 都派发到 client，loop 继续直到 LLM 给最终答案"""
        client = MagicMock()
        client.call.side_effect = [
            {"data": "研报数据"},
            {"data": "热榜数据"},
        ]
        agent = GthtAgent(client=client)
        agent._llm = MagicMock()

        # 第一轮：LLM 返回 2 个 tool_calls
        first = _llm_response(
            _llm_message(
                tool_calls=[
                    _tool_call("c1", "research_report", {"query": "600519"}),
                    _tool_call("c2", "rank_list", {"query": "今日热榜"}),
                ]
            )
        )
        # 第二轮：LLM 看到 2 个 tool 结果后给出最终答案
        second = _llm_response(_llm_message(content="综合分析：..."))
        agent._llm.chat.completions.create.side_effect = [first, second]

        result = agent.ask("查 600519 + 热榜")

        assert result["answer"] == "综合分析：..."
        assert client.call.call_count == 2
        # 顺序与 tool_calls 一致：研报先，热榜后
        client.call.assert_any_call(
            "lingxi-researchreport-skill", "researchreport", "research", query="600519"
        )
        client.call.assert_any_call(
            "lingxi-ranklist-skill", "ranklist", "ranklist", query="今日热榜"
        )
        assert len(result["tool_trace"]) == 2
        assert {t["tool"] for t in result["tool_trace"]} == {"research_report", "rank_list"}

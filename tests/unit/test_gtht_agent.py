"""GthtAgent 测试：tools schema 与 function calling 循环。"""
from __future__ import annotations

from mo_stock.data_sources.gtht_agent import _TOOL_DISPATCH, _TOOLS


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

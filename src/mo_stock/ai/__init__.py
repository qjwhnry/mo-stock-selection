"""AI 分析层：Claude + 四段 prompt caching。**Phase 3 引入，当前未实现**。

# 当前状态（2026-04-26）

整个目录暂时只有此 `__init__.py`，所有计划文件均**未创建**：
- `client.py`：anthropic SDK 包装 + cache_control
- `prompts.py`：四段 cache 设计（system / methodology / static / dynamic）
- `analyzer.py`：单股深度分析入口
- `schemas.py`：pydantic 输出约束（防止幻觉股票代码）

因此：
- `combine.py` 里的 `ai_score` 永远为 `None`
- `selection_result.final_score` = `rule_score`（已在 `_final_score_from` 显式防御）
- `report/render_md.py` 的 AI 段不会渲染（thesis/key_signals 等字段都为 None）
- `weights.yaml` 中的 `combine.ai_weight` 当前不生效（待 Phase 3 解开）

# Phase 3 启动前必读

实现 AI 调用层时务必同步处理审计报告中 P1-19（**Prompt Injection 风险**）：
- 所有用户/数据驱动文本（新闻、公告、研报）必须 JSON 序列化或 XML 标签包裹
- 输出强制 JSON Schema validation，拒绝越界字段
- System prompt 顶部硬编码免责与不可改写规则

详细计划：`PLAN.md` §Phase 3、`docs/audit-2026-04-26.md` §P0-2 / §P0-17 / §P1-19
"""

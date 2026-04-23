"""数据采集层。

- ``tushare_client``：Tushare Pro API 封装（重试 + 节流）
- ``gtht_client``：国泰海通灵犀 subprocess 封装（auth 落盘 + node 调用）
- ``gtht_agent``：基于 OpenAI 兼容 LLM 的 GTHT skill function-calling 代理
- ``calendar``：交易日 / 停牌 / ST / 次新过滤
"""

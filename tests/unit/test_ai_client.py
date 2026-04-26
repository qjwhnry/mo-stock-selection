"""ClaudeClient 单测（v2.2 plan Task 2）。

覆盖：
- 4 段 cache 结构（system 2 块带 cache_control，messages.user 1 cached + 1 dynamic）
- usage dict 含 cache_creation/read tokens
- tenacity 重试：RateLimitError → 重试，BadRequestError → 立刻抛
- APIStatusError 只对 5xx 重试（4xx 子类不能误重试）

注：所有测试都 monkeypatch anthropic.Anthropic，不真调网络。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _anthropic_response(status_code: int):
    """构造 httpx.Response 给 anthropic 异常用。"""
    import httpx
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.Response(
        status_code, request=request,
        json={"error": {"message": "test error"}},
    )


def _fake_messages_create_success(text: str = '{"score":85}', cache_creation: int = 800, cache_read: int = 0):
    """构造一个成功的 messages.create return_value。"""
    fake_resp = MagicMock()
    fake_resp.content = [MagicMock(text=text)]
    fake_resp.usage = MagicMock(
        input_tokens=1000,
        output_tokens=300,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
    )
    return fake_resp


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    """每个测试前清空 ClaudeClient 单例 + 注入 fake api_key。"""
    from mo_stock.ai.client import reset_singleton_for_test
    # 单测环境通常没真实 key，用 fake 跑通构造逻辑
    monkeypatch.setattr("config.settings.settings.anthropic_api_key", "sk-ant-test-fake-key")
    monkeypatch.setattr("config.settings.settings.anthropic_base_url", "")
    reset_singleton_for_test()
    yield
    reset_singleton_for_test()


class TestClientRequestStructure:
    def test_analyze_sends_4_blocks_with_correct_cache_control(self, monkeypatch) -> None:
        """system 含 2 个 cache_control 块；messages.user 含 1 cached + 1 dynamic。"""
        from mo_stock.ai.client import ClaudeClient

        fake_create = MagicMock(return_value=_fake_messages_create_success())
        fake_anthropic = MagicMock()
        fake_anthropic.messages.create = fake_create
        monkeypatch.setattr("anthropic.Anthropic", lambda **kw: fake_anthropic)

        client = ClaudeClient()
        client.analyze("sys", "method", "static", "dynamic")

        call_kwargs = fake_create.call_args.kwargs
        # system 是 list，2 个块
        assert isinstance(call_kwargs["system"], list)
        assert len(call_kwargs["system"]) == 2
        assert call_kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert call_kwargs["system"][1]["cache_control"] == {"type": "ephemeral"}
        assert call_kwargs["system"][0]["text"] == "sys"
        assert call_kwargs["system"][1]["text"] == "method"

        # messages.user.content 含 2 个块：static (cached) + dynamic (no cache)
        user_content = call_kwargs["messages"][0]["content"]
        assert len(user_content) == 2
        assert user_content[0]["cache_control"] == {"type": "ephemeral"}
        assert user_content[0]["text"] == "static"
        assert user_content[1]["text"] == "dynamic"
        assert "cache_control" not in user_content[1]

    def test_analyze_returns_text_and_usage_with_cache_metrics(self, monkeypatch) -> None:
        """usage dict 含 input_tokens / output_tokens / cache_creation_tokens / cache_read_tokens。"""
        from mo_stock.ai.client import ClaudeClient

        fake_create = MagicMock(return_value=_fake_messages_create_success(
            text='{"score":85}', cache_creation=800, cache_read=200,
        ))
        fake_anthropic = MagicMock()
        fake_anthropic.messages.create = fake_create
        monkeypatch.setattr("anthropic.Anthropic", lambda **kw: fake_anthropic)

        raw, usage = ClaudeClient().analyze("sys", "method", "static", "dynamic")
        assert raw == '{"score":85}'
        assert usage["input_tokens"] == 1000
        assert usage["output_tokens"] == 300
        assert usage["cache_creation_tokens"] == 800
        assert usage["cache_read_tokens"] == 200


class TestClientRetry:
    def test_retries_on_rate_limit(self, monkeypatch) -> None:
        """RateLimitError 触发 tenacity 重试 3 次内成功。"""
        import anthropic
        from mo_stock.ai.client import ClaudeClient

        fake_create = MagicMock(side_effect=[
            anthropic.RateLimitError(
                "rate limited", response=_anthropic_response(429), body=None,
            ),
            anthropic.RateLimitError(
                "rate limited", response=_anthropic_response(429), body=None,
            ),
            _fake_messages_create_success(),  # 第 3 次成功
        ])
        fake_anthropic = MagicMock()
        fake_anthropic.messages.create = fake_create
        monkeypatch.setattr("anthropic.Anthropic", lambda **kw: fake_anthropic)
        # tenacity 用 time.sleep 退避，单测 mock 掉避免真等
        monkeypatch.setattr("tenacity.nap.time.sleep", lambda _s: None)

        ClaudeClient().analyze("sys", "method", "static", "dynamic")
        assert fake_create.call_count == 3

    def test_does_not_retry_on_bad_request(self, monkeypatch) -> None:
        """BadRequestError 不在重试白名单，立刻抛出（避免无限重试错参数）。"""
        import anthropic
        from mo_stock.ai.client import ClaudeClient

        fake_create = MagicMock(side_effect=anthropic.BadRequestError(
            "bad request", response=_anthropic_response(400), body=None,
        ))
        fake_anthropic = MagicMock()
        fake_anthropic.messages.create = fake_create
        monkeypatch.setattr("anthropic.Anthropic", lambda **kw: fake_anthropic)

        with pytest.raises(anthropic.BadRequestError):
            ClaudeClient().analyze("sys", "method", "static", "dynamic")
        assert fake_create.call_count == 1  # 只调一次，没重试

    def test_does_not_retry_on_authentication_error(self, monkeypatch) -> None:
        """AuthenticationError（API key 错）也是 fail fast。"""
        import anthropic
        from mo_stock.ai.client import ClaudeClient

        fake_create = MagicMock(side_effect=anthropic.AuthenticationError(
            "invalid api key", response=_anthropic_response(401), body=None,
        ))
        fake_anthropic = MagicMock()
        fake_anthropic.messages.create = fake_create
        monkeypatch.setattr("anthropic.Anthropic", lambda **kw: fake_anthropic)

        with pytest.raises(anthropic.AuthenticationError):
            ClaudeClient().analyze("sys", "method", "static", "dynamic")
        assert fake_create.call_count == 1

    def test_retries_api_status_error_only_for_5xx(self, monkeypatch) -> None:
        """APIStatusError 5xx 重试；4xx 已被 SDK 转成具体子类（BadRequest 等），
        所以 APIStatusError 实际只会是 5xx。"""
        import anthropic
        from mo_stock.ai.client import ClaudeClient

        # 构造 5xx APIStatusError（注意：anthropic SDK 实际把 5xx 转成 InternalServerError）
        fake_create = MagicMock(side_effect=[
            anthropic.APIStatusError(
                "server busy", response=_anthropic_response(503), body=None,
            ),
            _fake_messages_create_success(),
        ])
        fake_anthropic = MagicMock()
        fake_anthropic.messages.create = fake_create
        monkeypatch.setattr("anthropic.Anthropic", lambda **kw: fake_anthropic)
        monkeypatch.setattr("tenacity.nap.time.sleep", lambda _s: None)

        ClaudeClient().analyze("sys", "method", "static", "dynamic")
        assert fake_create.call_count == 2  # 第 2 次成功

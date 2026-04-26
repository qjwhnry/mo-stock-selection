"""Anthropic Claude SDK 包装（v2.2 plan §2.3）。

设计要点：
- 4 段 prompt cache：system 2 块（system + methodology）+ messages.user 1 cached + 1 dynamic
- 双层重试：本层（SDK 网络错误）用 tenacity；schema 验证失败的重试在 analyzer.py
- 重试白名单：RateLimitError / APIConnectionError / APIStatusError(5xx)
- 不重试：BadRequestError / AuthenticationError → fail fast

注意 APIStatusError 子类陷阱：BadRequest / Authentication 都继承 APIStatusError，
所以不能用 retry_if_exception_type，必须用 predicate + status_code 检查。
"""
from __future__ import annotations

import anthropic
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings


def _is_retryable_anthropic_error(exc: BaseException) -> bool:
    """只重试临时性 Anthropic 错误。

    BadRequestError / AuthenticationError 都继承 APIStatusError，必须用
    predicate 区分 4xx vs 5xx，不能用 retry_if_exception_type(APIStatusError)。
    """
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        # SDK 把 4xx 转成具体子类（BadRequest / Auth / NotFound 等），
        # 所以走到 isinstance(APIStatusError) 这层的通常已经是 5xx。
        # 但保险起见显式 check status_code。
        try:
            return exc.response.status_code >= 500
        except AttributeError:
            return False
    return False


class ClaudeClient:
    """Anthropic Claude SDK 单例包装。"""

    _instance: ClaudeClient | None = None
    _initialized: bool = False

    def __new__(cls) -> ClaudeClient:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # 单例只 init 一次（避免重复构造 SDK 客户端）
        if self._initialized:
            return
        api_key = settings.anthropic_api_key
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY 未配置，请检查 .env")
        # base_url 留空 → 用官方默认；非空（如指向第三方兼容层）→ 透传给 SDK
        base_url = settings.anthropic_base_url.strip() if settings.anthropic_base_url else None
        self.client = anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url or None,
        )
        self.model = settings.anthropic_model
        self._initialized = True
        logger.info("ClaudeClient initialized: model={} base_url={}",
                    self.model, base_url or "official")

    @retry(
        retry=retry_if_exception(_is_retryable_anthropic_error),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        reraise=True,
    )
    def analyze(
        self,
        system_prompt: str,
        methodology_prompt: str,
        static_stock_prompt: str,
        dynamic_stock_prompt: str,
    ) -> tuple[str, dict[str, int]]:
        """单次 Claude 调用，返回 (raw_response_text, usage_dict)。

        - usage_dict 含 input_tokens / output_tokens / cache_creation_tokens / cache_read_tokens
        - 限速 / 5xx / 网络抖动 → tenacity 自动重试 3 次（指数退避 2-20s）
        - BadRequestError / AuthenticationError → 不重试，直接抛
        - schema 校验失败的重试不在本层（在 analyzer.py）
        """
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            temperature=0.3,
            system=[
                {"type": "text", "text": system_prompt,
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": methodology_prompt,
                 "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": static_stock_prompt,
                     "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": dynamic_stock_prompt},
                ],
            }],
        )
        usage: dict[str, int] = {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "cache_creation_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read_tokens": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        }
        # content 可能多块（罕见），取第一个 text 块
        text = ""
        for block in resp.content:
            if hasattr(block, "text") and isinstance(block.text, str):
                text = block.text
                break
        return text, usage


def reset_singleton_for_test() -> None:
    """单测重置入口：清空类级 _instance + _initialized。"""
    ClaudeClient._instance = None
    ClaudeClient._initialized = False

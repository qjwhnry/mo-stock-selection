"""共享 pytest fixtures。"""
from __future__ import annotations

from datetime import date

import pytest


@pytest.fixture
def sample_trade_date() -> date:
    """一个用于测试的固定交易日。"""
    return date(2026, 4, 22)

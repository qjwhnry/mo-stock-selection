"""规则筛选层基类与共享工具。

每个维度实现一个 Filter 子类，统一输出 0-100 分 + 细节字典。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session


@dataclass
class ScoreResult:
    """单只股票在某个维度的打分结果。"""

    ts_code: str
    trade_date: date
    dim: str                               # limit / moneyflow / lhb / sector / sentiment
    score: float                           # 0-100
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts_code": self.ts_code,
            "trade_date": self.trade_date,
            "dim": self.dim,
            "score": round(self.score, 2),
            "detail": self.detail,
        }


class FilterBase(ABC):
    """规则维度基类。

    子类实现 `score_one` 或覆写 `score_all` 以支持批量优化。
    """

    #: 维度标识，与 weights.yaml 的 `dimension_weights.<key>` 对应
    dim: str

    def __init__(self, weights: dict[str, Any] | None = None) -> None:
        """weights: 本维度的细则参数，从 weights.yaml 读取。"""
        self.weights = weights or {}

    @abstractmethod
    def score_all(self, session: Session, trade_date: date) -> list[ScoreResult]:
        """对当日所有相关候选股打分。

        返回的列表只需要包含**本维度得分 > 0** 或本维度有记录的股票，
        没有记录的股票在综合打分时自动按 0 处理。
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 权重配置加载
# ---------------------------------------------------------------------------

def load_weights_yaml(path: Path) -> dict[str, Any]:
    """读取 config/weights.yaml。"""
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """把分数夹到 [lo, hi] 区间。"""
    return max(lo, min(hi, value))

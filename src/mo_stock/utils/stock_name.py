"""股票名称相关工具函数。"""
from __future__ import annotations

import re

# ST 名称识别：锚定开头，ST/*ST 后必须跟中文字符（避免误杀 STAR-V WW 等正常股）
# 大小写不敏感，前导空白容错
_ST_NAME_RE = re.compile(r"^\s*\*?ST\s*[\u4e00-\u9fff]", re.IGNORECASE)


def is_st_name(name: str | None) -> bool:
    """根据股票名称判断是否 ST（以 ST 或 *ST 开头 + 中文字符）。"""
    if not name:
        return False
    return bool(_ST_NAME_RE.match(name.strip()))

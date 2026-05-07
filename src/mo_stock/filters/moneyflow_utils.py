"""资金流打分共享工具。"""
from __future__ import annotations


def has_price_confirmation(
    open_price: float | None,
    close_price: float | None,
    min_intraday_pct: float = 0.0,
) -> bool:
    """判断资金流是否有价格确认。

    Tushare moneyflow 的主动买卖方向在下跌日容易把承接卖盘识别成净流入。
    选股打分只把日内收阳的正净流入视为有效资金信号。
    """
    if open_price is None or close_price is None or open_price <= 0:
        return False
    intraday_pct = (close_price - open_price) / open_price * 100
    return intraday_pct > min_intraday_pct


def confirmed_positive_net(
    net_mf_wan: float | None,
    open_price: float | None,
    close_price: float | None,
    min_intraday_pct: float = 0.0,
) -> float:
    """正净流入必须有价格确认；净流出仍保留为负贡献。"""
    net = net_mf_wan or 0.0
    if net <= 0:
        return net
    if has_price_confirmation(open_price, close_price, min_intraday_pct):
        return net
    return 0.0

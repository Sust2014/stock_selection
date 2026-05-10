from __future__ import annotations

import pandas as pd

from .base_strategy import BaseDailyStrategy


class VShapeReversalStrategy(BaseDailyStrategy):
    """N22-V型反转：急跌后放量反弹，最佳持仓4天，胜率67.27%。"""

    name = "V型反转"
    category = "突破反转"

    def match(self, row: pd.Series) -> bool:
        dist_40d = row["收盘"] / row["过去40日最低价"] - 1
        if dist_40d >= 0.15:
            return False
        if row["昨涨跌"] >= -1:
            return False
        return (
            row["涨跌幅"] > 4
            and row["成交量"] > row["过去20日平均成交量"] * 1.8
            and row["收盘"] > row["开盘"]
        )


class DoubleBottomVolumeReversalStrategy(BaseDailyStrategy):
    """N1-双底放量反转：双底支撑 + 放量反转，最佳持仓4天，胜率57.14%。"""

    name = "双底放量反转"
    category = "突破反转"

    def match(self, row: pd.Series) -> bool:
        dist_40d = row["收盘"] / row["过去40日最低价"] - 1
        dist_60d = row["收盘"] / row["过去60日最低收盘"] - 1
        return (
            dist_40d < 0.15
            and dist_60d < 0.20
            and row["涨跌幅"] > 3
            and row["成交量"] > row["过去20日平均成交量"] * 1.8
            and row["收盘"] > row["开盘"]
        )


class ShrinkPullbackCounterStrategy(BaseDailyStrategy):
    """N2-缩量回踩反击：昨缩量洗盘 + 今放量反击，最佳持仓1天，胜率53.01%。"""

    name = "缩量回踩反击"
    category = "突破反转"

    def match(self, row: pd.Series) -> bool:
        return (
            row["昨量"] < row["过去20日平均成交量"] * 0.5
            and row["成交量"] > row["过去20日平均成交量"] * 1.8
            and row["量比昨"] > 1.3
            and row["涨跌幅"] > 1
            and row["收盘"] > row["SMA5"]
            and row["SMA20"] > row["SMA60"]
        )


class LimitUpShrinkReExpandStrategy(BaseDailyStrategy):
    """N24-涨停缩量再放：有涨停基因 + 缩量回踩 + 再放量启动，胜率52.48%。"""

    name = "涨停缩量再放"
    category = "主升"

    def match(self, row: pd.Series) -> bool:
        return (
            row["近15日涨停次数"] >= 1
            and row["昨量"] < row["过去20日平均成交量"] * 0.7
            and row["量比昨"] > 1.5
            and row["涨跌幅"] > 2
            and row["收盘"] > row["SMA10"]
            and row["SMA20"] > row["SMA60"]
        )


class MAPinchBreakoutStrategy(BaseDailyStrategy):
    """N5-均线粘合突破：MA5/10/20粘合后放量突破，胜率52.39%，信号量大。"""

    name = "均线粘合突破"
    category = "突破反转"

    def match(self, row: pd.Series) -> bool:
        ma_pinch = (
            abs(row["SMA5"] / row["SMA10"] - 1) < 0.03
            and abs(row["SMA10"] / row["SMA20"] - 1) < 0.05
        )
        if not ma_pinch:
            return False
        max_ma = max(row["SMA5"], row["SMA10"], row["SMA20"])
        return (
            row["收盘"] > max_ma
            and row["成交量"] > row["过去20日平均成交量"] * 1.5
            and row["涨跌幅"] > 3
        )
    
class CustomBuySignalStrategy(BaseDailyStrategy):
    """自定义日线买点（T日信号）：
       1. 距40日最低价 < 15%
       2. 今日涨幅 > 5%
       3. 昨日下跌
       4. 放量 > 均量 × 1.5
       5. 收阳线
    """

    name = "T日买点"
    category = "突破反转"

    def match(self, row: pd.Series) -> bool:
        # 检查历史长度
        if "过去40日最低价" not in row or "过去20日平均成交量" not in row:
            return False

        dist_40d = row["收盘"] / row["过去40日最低价"] - 1
        cond1 = dist_40d < 0.15
        cond2 = row["涨跌幅"] > 5
        cond3 = row["昨涨跌"] < -1
        cond4 = row["成交量"] > row["过去20日平均成交量"] * 1.5
        cond5 = row["收阳"] == 1  # 收阳线

        return cond1 and cond2 and cond3 and cond4 and cond5

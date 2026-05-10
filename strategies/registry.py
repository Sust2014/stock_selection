from __future__ import annotations

from .base_strategy import BaseDailyStrategy, BaseMinuteStrategy, StrategySignal, MinuteStrategySignal
from .daily_strategies import (
    VShapeReversalStrategy,
    DoubleBottomVolumeReversalStrategy,
    ShrinkPullbackCounterStrategy,
    LimitUpShrinkReExpandStrategy,
    MAPinchBreakoutStrategy,
    CustomBuySignalStrategy,
)


def get_daily_strategies() -> list[BaseDailyStrategy]:
    """日线策略注册表 — TOP 5 胜率最高策略"""

    return [
        VShapeReversalStrategy(),            # N22 V型反转     67.27%  4天
        DoubleBottomVolumeReversalStrategy(), # N1  双底放量反转 57.14%  4天
        ShrinkPullbackCounterStrategy(),      # N2  缩量回踩反击 53.01%  1天
        LimitUpShrinkReExpandStrategy(),      # N24 涨停缩量再放 52.48%  1天
        MAPinchBreakoutStrategy(),            # N5  均线粘合突破 52.39%  4天
        CustomBuySignalStrategy(),   # 新增
    ]


def evaluate_daily_strategies(row) -> list[StrategySignal]:
    signals: list[StrategySignal] = []
    for strategy in get_daily_strategies():
        signal = strategy.evaluate(row)
        if signal is not None:
            signals.append(signal)
    return signals


from .minute_strategies import (
    PullbackStartMinuteStrategy,
    PlatformBreakoutMinuteStrategy,
    VolumeReversalMinuteStrategy,
    OneMinuteBuyStrategy,
    check_30m_structure,
)
from .chanlun_strategies import (
    ChanlunFirstBuyMinuteStrategy,
    ChanlunSecondBuyMinuteStrategy,
    ChanlunThirdBuyMinuteStrategy,
)


def get_minute_strategies() -> list[BaseMinuteStrategy]:
    return [
        PullbackStartMinuteStrategy(),
        PlatformBreakoutMinuteStrategy(),
        VolumeReversalMinuteStrategy(),
        ChanlunFirstBuyMinuteStrategy(),
        ChanlunSecondBuyMinuteStrategy(),
        ChanlunThirdBuyMinuteStrategy(),
        OneMinuteBuyStrategy(),
    ]


def evaluate_minute_strategies(
    row,
    df1,
    df5,
    df30,
    daily_group: str,
    enable_1m_buy: bool = False,
) -> tuple[bool, list[str], str]:
    structure_ok, structure_msg = check_30m_structure(df30)
    if not structure_ok:
        return False, [], structure_msg

    five_minute_signals: list[MinuteStrategySignal] = []
    one_minute_hit = False

    for strategy in get_minute_strategies():
        if isinstance(strategy, OneMinuteBuyStrategy):
            if not enable_1m_buy:
                continue
            try:
                one_minute_hit = bool(strategy.match(row, df1, df5, df30))
            except Exception:
                one_minute_hit = False
            continue

        signal = strategy.evaluate(row, df5, df30, daily_group)
        if signal is not None:
            five_minute_signals.append(signal)

    if not five_minute_signals:
        return False, [], structure_msg

    buy_points = [signal.name for signal in five_minute_signals]

    if enable_1m_buy:
        if not one_minute_hit:
            return False, [], structure_msg + "；1分钟买点未确认"
        buy_points.append("1分钟精确买点")
    else:
        structure_msg = structure_msg + "；已关闭1分钟精确确认，仅确认到5分钟级别"

    return True, buy_points, structure_msg

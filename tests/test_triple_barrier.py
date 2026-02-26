from __future__ import annotations

from src.analysis.triple_barrier import TripleBarrierSpec, label_with_triple_barrier


def test_long_take_profit_first() -> None:
    highs = [100, 101, 103]
    lows = [100, 99.6, 100]
    closes = [100, 100, 102]
    spec = TripleBarrierSpec(take_profit_pct=0.02, stop_loss_pct=0.01, max_holding_bars=3)
    out = label_with_triple_barrier(
        highs=highs,
        lows=lows,
        closes=closes,
        entry_index=0,
        side=1,
        spec=spec,
    )
    assert out.label == 1
    assert out.touched == "take_profit"
    assert out.touch_bar == 2


def test_long_stop_loss_first() -> None:
    highs = [100, 100.5, 101]
    lows = [100, 98.8, 99]
    closes = [100, 99.5, 100]
    spec = TripleBarrierSpec(take_profit_pct=0.02, stop_loss_pct=0.01, max_holding_bars=3)
    out = label_with_triple_barrier(
        highs=highs,
        lows=lows,
        closes=closes,
        entry_index=0,
        side=1,
        spec=spec,
    )
    assert out.label == -1
    assert out.touched == "stop_loss"
    assert out.touch_bar == 1


def test_time_barrier_timeout() -> None:
    highs = [100, 100.8, 100.7]
    lows = [100, 99.3, 99.4]
    closes = [100, 100, 100]
    spec = TripleBarrierSpec(take_profit_pct=0.02, stop_loss_pct=0.02, max_holding_bars=2)
    out = label_with_triple_barrier(
        highs=highs,
        lows=lows,
        closes=closes,
        entry_index=0,
        side=1,
        spec=spec,
    )
    assert out.label == 0
    assert out.touched == "time"
    assert out.touch_bar == 2


def test_tie_break_stop_first_default() -> None:
    highs = [100, 102.1]
    lows = [100, 98.9]
    closes = [100, 100]
    spec = TripleBarrierSpec(take_profit_pct=0.02, stop_loss_pct=0.01, max_holding_bars=1)
    out = label_with_triple_barrier(
        highs=highs,
        lows=lows,
        closes=closes,
        entry_index=0,
        side=1,
        spec=spec,
    )
    assert out.label == -1
    assert out.touched == "stop_loss"


def test_short_side_inverts_barrier_semantics() -> None:
    highs = [100, 100.5, 101.2]
    lows = [100, 97.8, 98.0]
    closes = [100, 99, 99]
    spec = TripleBarrierSpec(take_profit_pct=0.02, stop_loss_pct=0.01, max_holding_bars=3)
    out = label_with_triple_barrier(
        highs=highs,
        lows=lows,
        closes=closes,
        entry_index=0,
        side=-1,
        spec=spec,
    )
    assert out.label == 1
    assert out.touched == "take_profit"


def test_short_tie_break_modes() -> None:
    highs = [100, 101.1]
    lows = [100, 97.9]
    closes = [100, 100]

    stop_first = TripleBarrierSpec(
        take_profit_pct=0.02,
        stop_loss_pct=0.01,
        max_holding_bars=1,
        tie_break="stop_first",
    )
    out_stop = label_with_triple_barrier(
        highs=highs,
        lows=lows,
        closes=closes,
        entry_index=0,
        side=-1,
        spec=stop_first,
    )
    assert out_stop.label == -1
    assert out_stop.touched == "stop_loss"

    take_first = TripleBarrierSpec(
        take_profit_pct=0.02,
        stop_loss_pct=0.01,
        max_holding_bars=1,
        tie_break="take_first",
    )
    out_take = label_with_triple_barrier(
        highs=highs,
        lows=lows,
        closes=closes,
        entry_index=0,
        side=-1,
        spec=take_first,
    )
    assert out_take.label == 1
    assert out_take.touched == "take_profit"

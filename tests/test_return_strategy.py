import copy
import json
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest
from DonchianReturnStrategy import DonchianReturnStrategy
from freqtrade.enums import CandleType
from pandas.testing import assert_frame_equal

from Donchian4HStrategy import Donchian4HStrategy


def candles(n=80, freq="4h", start="2024-01-01"):
    close = 100 + np.arange(n) * 0.1 + np.sin(np.arange(n))
    return pd.DataFrame(
        {
            "date": pd.date_range(start, periods=n, freq=freq, tz="UTC"),
            "open": close - 0.1,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 10.0,
        }
    )


@pytest.fixture
def strategy():
    config = json.loads(Path("user_data/config.json").read_text())
    config["candle_type_def"] = CandleType.FUTURES
    instance = DonchianReturnStrategy(config)
    instance.dp = Mock()
    instance.dp.market.return_value = {"base": "BTC", "quote": "USDT"}
    return instance


def breakout_frame(strategy):
    frame = strategy.populate_indicators(candles(), {})
    frame["close_1d"] = 110.0
    frame["ema_1d"] = 100.0
    frame["ema_slope_1d"] = 1.0
    frame["adx"] = 30.0
    frame.loc[78, "close"] = frame.loc[78, "entry_upper"] + 1
    frame.loc[79, "close"] = frame.loc[79, "entry_lower"] - 1
    frame.loc[79, ["close_1d", "ema_slope_1d"]] = [90.0, -1.0]
    return frame


@pytest.mark.parametrize("long_only", [False, True])
@pytest.mark.parametrize("adx_filter", [False, True])
def test_direction_and_filter_combinations_preserve_valid_longs(strategy, long_only, adx_filter):
    strategy.config["research"].update(long_only=long_only, adx_filter=adx_filter)
    result = strategy.populate_entry_trend(breakout_frame(strategy), {})
    assert result.loc[78, "enter_long"] == 1
    assert result.loc[78, "enter_tag"] == "LONG_ENTRY"
    assert result.loc[79, "enter_short"] == int(not long_only)
    if long_only:
        assert result["enter_short"].sum() == 0
        assert result["enter_tag"].fillna("").loc[79] == ""
    else:
        assert result.loc[79, "enter_tag"] == "SHORT_ENTRY"


@pytest.mark.parametrize("adx,expected", [(24.99, 0), (25.0, 1), (np.nan, 0)])
def test_adx_threshold_masks_both_directions_and_tags(strategy, adx, expected):
    strategy.config["research"].update(long_only=False, adx_filter=True)
    frame = breakout_frame(strategy)
    frame.loc[[78, 79], "adx"] = adx
    result = strategy.populate_entry_trend(frame, {})
    assert result.loc[78, "enter_long"] == expected
    assert result.loc[79, "enter_short"] == expected
    if not expected:
        assert (result.loc[[78, 79], "enter_tag"].fillna("") == "").all()


def test_disabled_extensions_match_baseline_signals(strategy):
    frame = breakout_frame(strategy)
    frame["adx"] = np.nan
    baseline = Donchian4HStrategy(copy.deepcopy(strategy.config))
    expected = baseline.populate_entry_trend(frame.copy(), {})
    actual = strategy.populate_entry_trend(frame.copy(), {})
    assert_frame_equal(actual[expected.columns], expected)


def test_adx_filter_keeps_existing_daily_direction_guard(strategy):
    strategy.config["research"].update(adx_filter=True)
    frame = breakout_frame(strategy)
    frame["ema_slope_1d"] *= -1
    result = strategy.populate_entry_trend(frame, {})
    assert result.loc[78, "enter_long"] == 0
    assert result.loc[79, "enter_short"] == 0


def test_indicators_are_prefix_invariant_and_future_prices_do_not_leak(strategy):
    frame = candles(150)
    full = strategy.populate_indicators(frame.copy(), {})
    cut = 90
    prefix = strategy.populate_indicators(frame.iloc[:cut].copy(), {})
    assert full["adx"].iloc[cut - 1:].notna().all()
    assert_frame_equal(prefix, full.iloc[:cut])
    changed = frame.copy()
    changed.loc[cut:, ["open", "high", "low", "close"]] *= 10
    changed_result = strategy.populate_indicators(changed, {})
    assert_frame_equal(changed_result.iloc[:cut], full.iloc[:cut])


def test_inherited_daily_filter_uses_only_completed_daily_candles(strategy):
    daily = candles(260, "1D", "2023-01-01")
    frame = candles(260 * 6, "4h", "2023-01-01")
    strategy.dp.get_pair_dataframe.return_value = daily
    metadata = {"pair": "BTC/USDT:USDT"}
    full = strategy.advise_indicators(frame.copy(), metadata)
    day = 220
    assert full.iloc[day * 6 + 4]["close_1d"] == daily.iloc[day - 1]["close"]
    assert full.iloc[day * 6 + 5]["close_1d"] == daily.iloc[day]["close"]
    changed_daily = daily.copy()
    changed_daily.loc[day:, ["open", "high", "low", "close"]] *= 10
    strategy.dp.get_pair_dataframe.return_value = changed_daily
    cut = day * 6 + 3
    prefix = strategy.advise_indicators(frame.iloc[:cut].copy(), metadata)
    assert_frame_equal(prefix, full.iloc[:cut])


@pytest.mark.parametrize("option", ["long_only", "adx_filter"])
@pytest.mark.parametrize("value", ["false", 0, None])
def test_non_boolean_options_fail_closed(strategy, option, value):
    strategy.config["research"][option] = value
    with pytest.raises(ValueError, match=f"{option} must be a boolean"):
        strategy.bot_start()


def test_return_variant_preserves_live_trading_rejection(strategy):
    strategy.config["dry_run"] = False
    with pytest.raises(ValueError, match="forbids live trading"):
        strategy.bot_start()

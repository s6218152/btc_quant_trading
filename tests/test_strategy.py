import json
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest
from freqtrade.enums import CandleType
from pandas.testing import assert_frame_equal

from Donchian4HStrategy import Donchian4HStrategy, risk_stake


def candles(n=80, freq="4h", start="2024-01-01", trend=0.1):
    close = 100 + np.arange(n) * trend + np.sin(np.arange(n))
    return pd.DataFrame(
        {
            "date": pd.date_range(start, periods=n, freq=freq, tz="UTC"),
            "open": close - 0.1,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.full(n, 10.0),
        }
    )


@pytest.fixture
def strategy():
    config = json.loads(Path("user_data/config.json").read_text())
    config["candle_type_def"] = CandleType.FUTURES
    s = Donchian4HStrategy(config)
    s.dp = Mock()
    s.dp.market.return_value = {"base": "BTC", "quote": "USDT"}
    s.wallets = SimpleNamespace(get_total_stake_amount=lambda: 10000)
    return s


def test_donchian_excludes_signal_candle_and_history_is_invariant(strategy):
    frame = candles()
    frame.loc[40, ["high", "close"]] = [200, 199]
    full = strategy.populate_indicators(frame.copy(), {})
    assert full.loc[40, "entry_upper"] < 200
    assert full.loc[41, "entry_upper"] == 200
    prefix = strategy.populate_indicators(frame.iloc[:41].copy(), {})
    assert_frame_equal(prefix, full.iloc[:41])


def test_daily_merge_waits_for_daily_close_and_future_mutations_do_not_leak(strategy):
    daily = candles(260, "1D", "2023-01-01")
    frame = candles(260 * 6, "4h", "2023-01-01")
    strategy.dp.get_pair_dataframe.return_value = daily
    full = strategy.advise_indicators(frame.copy(), {"pair": "BTC/USDT:USDT"})
    # At 20:00 the 4h candle closes at midnight: this daily candle is now known.
    day = 220
    assert full.iloc[day * 6 + 4]["close_1d"] == daily.iloc[day - 1]["close"]
    assert full.iloc[day * 6 + 5]["close_1d"] == daily.iloc[day]["close"]
    cut = day * 6 + 3
    modified = daily.copy()
    modified.loc[day:, ["open", "high", "low", "close"]] *= 10
    strategy.dp.get_pair_dataframe.return_value = modified
    prefix = strategy.advise_indicators(frame.iloc[:cut].copy(), {"pair": "BTC/USDT:USDT"})
    assert_frame_equal(prefix, full.iloc[:cut])


@pytest.mark.parametrize("short", [False, True])
def test_long_short_breakouts_require_daily_direction(strategy, short):
    frame = strategy.populate_indicators(candles(), {})
    frame["close_1d"] = 90 if short else 110
    frame["ema_1d"] = 100
    frame["ema_slope_1d"] = -1 if short else 1
    frame.loc[79, "close"] = frame.loc[79, "entry_lower" if short else "entry_upper"] + (
        -1 if short else 1
    )
    result = strategy.populate_entry_trend(frame.copy(), {})
    assert result.iloc[-1]["enter_short" if short else "enter_long"] == 1
    frame["ema_slope_1d"] *= -1
    result = strategy.populate_entry_trend(frame, {})
    assert result.iloc[-1]["enter_short" if short else "enter_long"] == 0


def sizing(**overrides):
    args = dict(
        equity=10000,
        available_margin=10000,
        price=100,
        atr=2,
        leverage=2,
        risk_fraction=0.005,
        atr_multiplier=2.5,
        cost_per_side=0.001,
        max_effective_leverage=2,
    )
    args.update(overrides)
    return risk_stake(**args)


def test_risk_budget_includes_costs_and_leverage_is_applied_once():
    stake = sizing()
    quantity = stake * 2 / 100
    assert quantity * (5 + 205 * 0.001) == pytest.approx(50)
    assert sizing(leverage=1) == pytest.approx(stake * 2)
    assert stake * 2 <= 20000


@pytest.mark.parametrize(
    "override",
    [
        {"atr": np.nan},
        {"atr": 0},
        {"price": 0},
        {"equity": -1},
        {"leverage": 3},
        {"risk_fraction": 0.02},
        {"min_stake": 1000},
        {"atr": 50},
        {"cost_per_side": -0.001},
    ],
)
def test_unsafe_or_below_minimum_orders_are_rejected(override):
    assert sizing(**override) == 0


def test_margin_and_exposure_caps_and_funding_reserve():
    assert sizing(atr=0.001) * 2 <= 20000
    assert sizing(available_margin=10) <= 9.8
    assert sizing(funding_reserve=0.005) < sizing()


def make_trade(short=False):
    data = {}
    return SimpleNamespace(
        is_short=short,
        leverage=2,
        open_rate=100,
        open_date_utc=pd.Timestamp("2024-01-01 08:00", tz=UTC).to_pydatetime(),
        entry_side="sell" if short else "buy",
        nr_of_successful_entries=1,
        get_custom_data=lambda key: data.get(key),
        set_custom_data=lambda key, value: data.__setitem__(key, value),
    )


def provide_row(strategy, close=100, atr=2, lower=90, upper=110):
    df = pd.DataFrame(
        [
            dict(
                date=pd.Timestamp("2024-01-01 04:00", tz=UTC),
                close=close,
                atr=atr,
                exit_lower=lower,
                exit_upper=upper,
            )
        ]
    )
    strategy.dp.get_analyzed_dataframe.return_value = (df, None)
    return df


@pytest.mark.parametrize("short", [False, True])
def test_initial_absolute_stop_and_leveraged_interface(strategy, short):
    provide_row(strategy)
    trade = make_trade(short)
    strategy.order_filled(
        "BTC/USDT:USDT", trade, SimpleNamespace(ft_order_side=trade.entry_side), trade.open_date_utc
    )
    assert trade.get_custom_data("atr_stop") == (105 if short else 95)
    ratio = strategy.custom_stoploss("BTC/USDT:USDT", trade, trade.open_date_utc, 100, 0, True)
    assert ratio == pytest.approx(0.10)


@pytest.mark.parametrize("short", [False, True])
def test_trailing_stop_can_only_tighten(strategy, short):
    trade = make_trade(short)
    trade.set_custom_data("atr_stop", 105 if short else 95)
    provide_row(strategy, close=90 if short else 110)
    strategy.custom_stoploss(
        "BTC/USDT:USDT", trade, trade.open_date_utc, 90 if short else 110, 0, False
    )
    first = trade.get_custom_data("atr_stop")
    assert first == (95 if short else 105)
    provide_row(strategy, close=94 if short else 106)
    strategy.custom_stoploss(
        "BTC/USDT:USDT", trade, trade.open_date_utc, 94 if short else 106, 0, False
    )
    assert trade.get_custom_data("atr_stop") == first


def test_active_candle_is_ignored_by_exit(strategy):
    trade = make_trade()
    df = provide_row(strategy)
    df.loc[1] = [pd.Timestamp("2024-01-01 08:00", tz=UTC), 1, 2, 90, 110]
    assert strategy.custom_exit("BTC/USDT:USDT", trade, trade.open_date_utc, 1, 0) is None
    df.loc[0, "close"] = 89
    assert strategy.custom_exit("BTC/USDT:USDT", trade, trade.open_date_utc, 1, 0) == (
        "donchian_long_exit"
    )


def test_live_mode_is_rejected(strategy):
    strategy.config["dry_run"] = False
    with pytest.raises(ValueError, match="forbids live"):
        strategy.bot_start()


def test_consecutive_losses_including_custom_exits_block_entry(strategy, monkeypatch):
    now = pd.Timestamp("2024-01-10", tz=UTC).to_pydatetime()
    trades = [
        SimpleNamespace(close_date_utc=now - pd.Timedelta(hours=i), close_profit=-0.01)
        for i in [1, 5, 9]
    ]
    monkeypatch.setattr("Donchian4HStrategy.Trade.get_trades_proxy", lambda **kw: trades)
    args = ("BTC/USDT:USDT", "market", 1, 100, "GTC")
    df = provide_row(strategy)
    df.loc[0, "date"] = now - pd.Timedelta(hours=4)
    assert not strategy.confirm_trade_entry(*args, now, None, "long")
    df.loc[0, "date"] = now + pd.Timedelta(hours=45)
    assert strategy.confirm_trade_entry(*args, now + pd.Timedelta(hours=49), None, "long")


def test_sizing_errors_cannot_fall_back_to_full_wallet(strategy):
    strategy.dp.get_analyzed_dataframe.side_effect = RuntimeError("provider unavailable")
    now = pd.Timestamp("2024-01-01 08:00", tz=UTC).to_pydatetime()
    assert (
        strategy.custom_stake_amount("BTC/USDT:USDT", now, 100, 10000, 5, 10000, 2, None, "long")
        == 0
    )
    assert not strategy.confirm_trade_entry(
        "BTC/USDT:USDT", "market", 200, 100, "GTC", now, None, "long"
    )


def test_oversize_and_stale_entries_are_rejected(strategy):
    provide_row(strategy)
    now = pd.Timestamp("2024-01-01 08:00", tz=UTC).to_pydatetime()
    assert not strategy.confirm_trade_entry(
        "BTC/USDT:USDT", "market", 200, 100, "GTC", now, None, "long"
    )
    assert strategy._closed_row("BTC/USDT:USDT", now + pd.Timedelta(hours=4)) is None


def test_native_lookahead_single_pair_override_is_allowed(strategy):
    strategy.config.update(max_open_trades=-1, minimum_trade_amount=5, targeted_trade_amount=20)
    strategy.bot_start()
    strategy.config["position_stacking"] = True
    with pytest.raises(ValueError, match="stacking"):
        strategy.bot_start()


def test_filter_ablation_only_removes_daily_filter(strategy):
    frame = strategy.populate_indicators(candles(), {})
    frame["close_1d"], frame["ema_1d"], frame["ema_slope_1d"] = 90, 100, -1
    frame.loc[79, "close"] = frame.loc[79, "entry_upper"] + 1
    assert strategy.populate_entry_trend(frame.copy(), {}).iloc[-1]["enter_long"] == 0
    strategy.config["research"]["ema_filter"] = False
    assert strategy.populate_entry_trend(frame.copy(), {}).iloc[-1]["enter_long"] == 1
    assert strategy.populate_entry_trend(frame.iloc[:40].copy(), {}).iloc[-1]["enter_long"] == 0


def test_comparison_contract_leverage_can_be_capped_at_one(strategy):
    strategy.config["research"]["contract_leverage"] = 1
    now = pd.Timestamp("2024-01-01", tz=UTC).to_pydatetime()
    assert strategy.leverage("BTC/USDT:USDT", now, 100, 1, 20, None, "long") == 1

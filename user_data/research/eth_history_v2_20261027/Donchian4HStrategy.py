"""Closed-candle Donchian breakout research; live trading is deliberately disabled."""

import logging
from datetime import datetime
from math import isfinite

import pandas as pd
import talib.abstract as ta
from freqtrade.persistence import Order, Trade
from freqtrade.strategy import IStrategy, informative, stoploss_from_absolute
from pandas import DataFrame


def risk_stake(
    *,
    equity: float,
    available_margin: float,
    price: float,
    atr: float,
    leverage: float,
    risk_fraction: float,
    atr_multiplier: float,
    cost_per_side: float,
    max_effective_leverage: float,
    min_stake: float = 0,
    margin_reserve: float = 0.02,
    funding_reserve: float = 0,
) -> float:
    """Return collateral, not notional. Budget includes adverse round-trip costs.

    Funding reserve is an explicit additional notional cost assumption, not a
    replacement for actual historical funding in the engine. Stop gaps and
    unbounded holding-period funding can exceed a planned risk budget.
    """
    values = (equity, available_margin, price, atr, leverage, risk_fraction, atr_multiplier)
    if any(not isfinite(x) or x <= 0 for x in values):
        return 0.0
    if not (0 < risk_fraction <= 0.01 and 1 <= leverage <= 2):
        return 0.0
    if not (0 < max_effective_leverage <= 2 and 0 <= margin_reserve < 1):
        return 0.0
    if any(not isfinite(x) or x < 0 for x in (cost_per_side, funding_reserve, min_stake)):
        return 0.0
    distance = atr * atr_multiplier
    # Reject a stop on/beyond the 2x liquidation safety region (conservative).
    if distance / price >= 0.4 / leverage:
        return 0.0
    loss_per_unit = distance + (2 * price + distance) * cost_per_side
    loss_per_unit += price * funding_reserve
    quantity = equity * risk_fraction / loss_per_unit
    notional = min(
        quantity * price,
        equity * max_effective_leverage,
        available_margin * (1 - margin_reserve) * leverage,
    )
    stake = notional / leverage
    return float(stake) if stake >= min_stake else 0.0


class Donchian4HStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "4h"
    can_short = True
    process_only_new_candles = True
    startup_candle_count = 1000
    minimal_roi = {}
    stoploss = -0.80  # Emergency fallback; ATR stop is installed immediately after fill.
    use_custom_stoploss = True
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    position_adjustment_enable = False

    @property
    def settings(self) -> dict:
        return self.config.get("research", {})

    @property
    def protections(self) -> list[dict]:
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 1},
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 180,
                "trade_limit": 1,
                "stop_duration_candles": 42,
                "max_allowed_drawdown": 0.10,
            },
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 42,
                "trade_limit": 3,
                "stop_duration_candles": 12,
                "only_per_pair": False,
            },
        ]

    def bot_start(self, **kwargs) -> None:
        if not self.config.get("dry_run", False):
            raise ValueError("Research strategy forbids live trading")
        analysis_mode = (
            "minimum_trade_amount" in self.config
            and "targeted_trade_amount" in self.config
            and self.config.get("max_open_trades") == -1
            and self.config["exchange"]["pair_whitelist"] == ["BTC/USDT:USDT"]
        )
        if self.config.get("max_open_trades") != 1 and not analysis_mode:
            raise ValueError("Research requires max_open_trades=1")
        if self.config.get("position_stacking", False):
            raise ValueError("Position stacking is prohibited")
        if self.config.get("margin_mode") != "isolated":
            raise ValueError("Research requires isolated margin")
        if self.config.get("position_adjustment_enable", False):
            raise ValueError("DCA / pyramiding are prohibited")
        cost = self.settings.get("taker_fee", 0.0005)
        cost += self.settings.get("slippage_per_side", 0.0005)
        if self.config.get("fee", 0) < cost:
            raise ValueError("Engine fee must include the configured fee + slippage proxy")

    @informative("1d")
    def daily_filter(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        period = int(self.settings.get("daily_ema_period", 200))
        dataframe["ema"] = ta.EMA(dataframe, timeperiod=period)
        dataframe["ema_slope"] = dataframe["ema"].diff()
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        entry = int(self.settings.get("entry_period", 20))
        exit_period = int(self.settings.get("exit_period", 10))
        dataframe["entry_upper"] = dataframe["high"].shift(1).rolling(entry).max()
        dataframe["entry_lower"] = dataframe["low"].shift(1).rolling(entry).min()
        dataframe["exit_upper"] = dataframe["high"].shift(1).rolling(exit_period).max()
        dataframe["exit_lower"] = dataframe["low"].shift(1).rolling(exit_period).min()
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        valid = (dataframe["volume"] > 0) & (dataframe["atr"] > 0)
        long_filter = (dataframe["close_1d"] > dataframe["ema_1d"]) & (
            dataframe["ema_slope_1d"] > 0
        )
        short_filter = (dataframe["close_1d"] < dataframe["ema_1d"]) & (
            dataframe["ema_slope_1d"] < 0
        )
        if not self.settings.get("ema_filter", True):
            long_filter = pd.Series(True, index=dataframe.index)
            short_filter = pd.Series(True, index=dataframe.index)
        dataframe["enter_long"] = (
            valid & long_filter & (dataframe["close"] > dataframe["entry_upper"])
        ).astype(int)
        dataframe["enter_short"] = (
            valid & short_filter & (dataframe["close"] < dataframe["entry_lower"])
        ).astype(int)
        dataframe.loc[dataframe["enter_long"] == 1, "enter_tag"] = "LONG_ENTRY"
        dataframe.loc[dataframe["enter_short"] == 1, "enter_tag"] = "SHORT_ENTRY"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Explicit custom_exit supplies tagged, completed-candle Donchian exits.
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    def _closed_row(self, pair: str, current_time: datetime) -> pd.Series | None:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return None
        # Usually the provider already hides the active candle. Filter as a
        # second guard and for deterministic behavior in tests / dry-run.
        completed = dataframe[
            dataframe["date"] + pd.Timedelta(hours=4) <= pd.Timestamp(current_time)
        ]
        if completed.empty:
            return None
        row = completed.iloc[-1]
        if pd.Timestamp(current_time) - row["date"] >= pd.Timedelta(hours=8):
            return None
        return row

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        return min(2.0, self.settings.get("contract_leverage", 2.0), max_leverage)

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        try:
            row = self._closed_row(pair, current_time)
            if row is None:
                return 0.0
            params = self.settings
            return risk_stake(
                equity=self.wallets.get_total_stake_amount(),
                available_margin=max_stake,
                price=current_rate,
                atr=float(row["atr"]),
                leverage=leverage,
                risk_fraction=params.get("risk_per_trade", 0.005),
                atr_multiplier=params.get("atr_multiplier", 2.5),
                cost_per_side=self.config["fee"],
                max_effective_leverage=params.get("max_effective_leverage", 2.0),
                min_stake=min_stake or 0,
                margin_reserve=params.get("margin_reserve", 0.02),
                funding_reserve=params.get("funding_reserve_per_trade", 0),
            )
        except Exception:
            # Freqtrade otherwise falls back to proposed_stake on callback error.
            logging.getLogger(__name__).exception("Sizing failed; rejecting entry")
            return 0.0

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        try:
            row = self._closed_row(pair, current_time)
            if row is None or not isfinite(float(row["atr"])) or row["atr"] <= 0:
                return False
            params = self.settings
            equity = self.wallets.get_total_stake_amount()
            distance = float(row["atr"]) * params.get("atr_multiplier", 2.5)
            planned_loss = amount * (
                distance
                + (2 * rate + distance) * self.config["fee"]
                + rate * params.get("funding_reserve_per_trade", 0)
            )
            if (
                not isfinite(planned_loss)
                or amount <= 0
                or rate <= 0
                or planned_loss > equity * params.get("risk_per_trade", 0.005) + 1e-8
                or amount * rate > equity * params.get("max_effective_leverage", 2) + 1e-8
            ):
                return False
            # Also guard ANY three consecutive realized losses, including custom exits.
            closed = [
                t
                for t in Trade.get_trades_proxy(is_open=False)
                if t.close_date_utc is not None and t.close_date_utc <= current_time
            ]
            recent = sorted(closed, key=lambda t: t.close_date_utc, reverse=True)[:3]
            if len(recent) == 3 and all(t.close_profit < 0 for t in recent):
                return current_time >= recent[0].close_date_utc + pd.Timedelta(hours=48)
            return True
        except Exception:
            logging.getLogger(__name__).exception("Entry validation failed; rejecting entry")
            return False

    def order_filled(
        self, pair: str, trade: Trade, order: Order, current_time: datetime, **kwargs
    ) -> None:
        if order.ft_order_side != trade.entry_side or trade.nr_of_successful_entries != 1:
            return
        row = self._closed_row(pair, current_time)
        if row is None or not isfinite(float(row["atr"])) or row["atr"] <= 0:
            raise ValueError("Cannot initialize ATR stop from a completed candle")
        atr = float(row["atr"])
        direction = 1 if trade.is_short else -1
        stop = trade.open_rate + direction * atr * self.settings.get("atr_multiplier", 2.5)
        trade.set_custom_data("entry_atr", atr)
        trade.set_custom_data("atr_stop", float(stop))

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        row = self._closed_row(pair, current_time)
        stored = trade.get_custom_data("atr_stop")
        if stored is None:
            # Never estimate entry ATR from future candles following a restart.
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            entry_rows = dataframe[dataframe["date"] + pd.Timedelta(hours=4) <= trade.open_date_utc]
            if entry_rows.empty:
                return None
            atr = float(entry_rows.iloc[-1]["atr"])
            if not isfinite(atr) or atr <= 0:
                return None
            stored = trade.open_rate + (1 if trade.is_short else -1) * atr * (
                self.settings.get("atr_multiplier", 2.5)
            )
        stop = float(stored)
        if not after_fill and row is not None and isfinite(float(row["atr"])):
            distance = float(row["atr"]) * self.settings.get("atr_multiplier", 2.5)
            candidate = float(row["close"]) + (1 if trade.is_short else -1) * distance
            stop = min(stop, candidate) if trade.is_short else max(stop, candidate)
        trade.set_custom_data("atr_stop", stop)
        # Freqtrade expects leveraged account-risk ratio, not ATR dollars or
        # unleveraged price percent. Its helper handles short direction too.
        return (
            stoploss_from_absolute(
                stop, current_rate, is_short=trade.is_short, leverage=trade.leverage
            )
            or None
        )

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> str | None:
        row = self._closed_row(pair, current_time)
        if row is None:
            return None
        if not trade.is_short and row["close"] < row["exit_lower"]:
            return "donchian_long_exit"
        if trade.is_short and row["close"] > row["exit_upper"]:
            return "donchian_short_exit"
        return None

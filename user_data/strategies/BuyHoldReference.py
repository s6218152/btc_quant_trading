"""Unleveraged perpetual hold reference; intentionally not an ATR-risk strategy."""

from datetime import datetime

import pandas as pd
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class BuyHoldReference(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "4h"
    startup_candle_count = 1
    can_short = False
    minimal_roi = {}
    stoploss = -0.99
    position_adjustment_enable = False
    use_exit_signal = False

    def bot_start(self, **kwargs) -> None:
        if not self.config.get("dry_run") or self.config.get("max_open_trades") != 1:
            raise ValueError("Hold reference is research-only with a single position")

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        start = pd.Timestamp(self.config["research"]["reference_start"])
        dataframe["enter_long"] = ((dataframe["date"] >= start) & (dataframe["volume"] > 0)).astype(
            int
        )
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = "PERPETUAL_HOLD_1X"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

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
        return 1.0

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
        allocation = self.config["research"].get("hold_allocation", 0.98)
        stake = min(max_stake * allocation, self.wallets.get_total_stake_amount() * allocation)
        return stake if stake >= (min_stake or 0) else 0.0

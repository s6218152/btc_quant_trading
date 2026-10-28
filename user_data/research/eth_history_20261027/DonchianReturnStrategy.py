"""Bounded return-improvement experiment; inherits the frozen V1 risk controls."""

import talib.abstract as ta
from pandas import DataFrame

from Donchian4HStrategy import Donchian4HStrategy


class DonchianReturnStrategy(Donchian4HStrategy):
    def bot_start(self, **kwargs) -> None:
        super().bot_start(**kwargs)
        for option in ("long_only", "adx_filter"):
            if type(self.settings.get(option, False)) is not bool:
                raise ValueError(f"{option} must be a boolean")

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_indicators(dataframe, metadata)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_entry_trend(dataframe, metadata)
        if self.settings.get("long_only", False):
            dataframe["enter_short"] = 0
        if self.settings.get("adx_filter", False):
            # Signal uses the just-completed 4h candle; native fills occur later.
            dataframe.loc[~dataframe["adx"].ge(25), ["enter_long", "enter_short"]] = 0
        inactive = dataframe["enter_long"].eq(0) & dataframe["enter_short"].eq(0)
        dataframe.loc[inactive, "enter_tag"] = None
        return dataframe

"""Diagnostic missed-check scenario; frozen parent strategy remains unchanged."""

from pandas import DataFrame

from Donchian4HStrategy import Donchian4HStrategy


class ExecutionStressStrategy(Donchian4HStrategy):
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_entry_trend(dataframe, metadata)
        delay = int(self.settings.get("execution_delay_bars", 0))
        if delay not in (0, 1):
            raise ValueError("Only registered zero/one-bar execution diagnostics are allowed")
        if delay:
            for column in ("enter_long", "enter_short"):
                dataframe[column] = dataframe[column].shift(delay, fill_value=0).astype(int)
            dataframe["enter_tag"] = dataframe["enter_tag"].shift(delay, fill_value="")
        return dataframe

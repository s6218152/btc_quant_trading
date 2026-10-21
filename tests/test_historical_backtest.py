import pandas as pd
import pytest

from scripts.historical_backtest import evaluation_period


def frames(end="2023-01-01"):
    return {
        key: pd.DataFrame({"date": pd.date_range(
            "2020-01-01", end, freq=interval, inclusive="left", tz="UTC",
        )})
        for key, interval in (("4h", "4h"), ("1d", "1D"), ("mark", "1h"), ("funding", "8h"))
    }


def test_history_reserves_completed_daily_warmup_and_seals_holdout():
    assert evaluation_period(frames(), 1000) == "20220927-20230101"
    assert evaluation_period(frames("2027-01-01"), 1000) == "20220927-20260101"


def test_history_rejects_insufficient_warmup():
    with pytest.raises(ValueError, match="Insufficient"):
        evaluation_period(frames("2022-01-01"), 1000)


def test_history_rejects_incomplete_last_day_of_mark_data():
    data = frames()
    data["mark"] = data["mark"].iloc[:-1]
    with pytest.raises(ValueError, match="complete period"):
        evaluation_period(data, 1000)

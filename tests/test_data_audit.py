import numpy as np
import pandas as pd

from scripts.audit_data import audit_frame


def frame():
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=6, freq="4h", tz="UTC"),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1.0,
        }
    )


def audit(df):
    return audit_frame(
        df,
        start=pd.Timestamp("2024-01-01", tz="UTC"),
        end=pd.Timestamp("2024-01-02", tz="UTC"),
        interval="4h",
    )


def test_valid_utc_grid():
    assert not audit(frame())["errors"]


def test_missing_duplicate_and_unsorted_candles_fail():
    df = frame()
    df.loc[2, "date"] = df.loc[0, "date"]
    result = audit(df)
    assert result["missing"] == 1
    assert result["duplicate_count"] == 1
    assert len(result["errors"]) >= 3


def test_invalid_ohlcv_and_nonfinite_values_fail():
    df = frame()
    df.loc[0, "low"] = 102
    df.loc[1, "close"] = np.nan
    assert len(audit(df)["errors"]) == 2


def test_naive_timestamps_fail():
    df = frame()
    df["date"] = df["date"].dt.tz_localize(None)
    assert audit(df)["errors"] == ["timestamps must be timezone-aware UTC"]


def test_missing_funding_event_is_not_treated_as_zero():
    df = frame().iloc[[0, 2, 4]].copy()
    df = df[["date"]].assign(funding_rate=0.0)
    result = audit_frame(
        df.iloc[:2],
        start=pd.Timestamp("2024-01-01", tz="UTC"),
        end=pd.Timestamp("2024-01-02", tz="UTC"),
        interval="8h",
        funding=True,
    )
    assert result["missing"] == 1
    assert result["errors"]

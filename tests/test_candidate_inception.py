import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from scripts.candidate_inception import (
    assert_unchanged_prefix,
    taipei_end_exclusive,
    trim_frame,
)


def price_frame(dates):
    return pd.DataFrame(
        {"date": pd.to_datetime(dates, utc=True), "open": 100.0, "close": 101.0}
    )


def test_yesterday_cutoff_uses_taipei_midnight_as_an_exclusive_utc_boundary():
    end = taipei_end_exclusive("2026-10-27")
    assert end == pd.Timestamp("2026-10-27T16:00:00Z")
    assert str(end.tz) == "UTC"
    assert end.tz_convert("Asia/Taipei") == pd.Timestamp("2026-10-28T00:00:00+08:00")


@pytest.mark.parametrize(
    "key,dates,last_kept",
    [
        (
            "4h",
            ["2026-10-27T08:00Z", "2026-10-27T12:00Z", "2026-10-27T16:00Z"],
            "2026-10-27T12:00Z",
        ),
        (
            "1d",
            ["2026-10-25T00:00Z", "2026-10-26T00:00Z", "2026-10-27T00:00Z"],
            "2026-10-26T00:00Z",
        ),
        (
            "funding",
            ["2026-10-27T00:00Z", "2026-10-27T08:00Z", "2026-10-27T16:00Z"],
            "2026-10-27T08:00Z",
        ),
    ],
)
def test_trim_keeps_only_closed_candles_or_prior_funding_without_mutating_source(
    key, dates, last_kept
):
    frame = price_frame(dates)
    if key == "funding":
        frame = frame[["date"]].assign(funding_rate=[0.0001, -0.0002, 0.0003])
    frame.index = [4, 5, 6]
    original = frame.copy(deep=True)
    result = trim_frame(frame, key, taipei_end_exclusive("2026-10-27"))
    expected = original.iloc[:2].reset_index(drop=True)
    assert_frame_equal(result, expected)
    assert result["date"].iloc[-1] == pd.Timestamp(last_kept)
    assert_frame_equal(frame, original)


def test_appending_new_candles_preserves_verified_prefix():
    original = price_frame(["2026-10-20T00:00Z", "2026-10-20T04:00Z"])
    extended = pd.concat(
        [original, price_frame(["2026-10-20T08:00Z"])], ignore_index=True
    )
    assert_unchanged_prefix(original, extended)


@pytest.mark.parametrize("damage", ["revised_price", "missing_row", "duplicate_row"])
def test_download_cannot_silently_revise_drop_or_duplicate_verified_history(damage):
    original = price_frame(["2026-10-20T00:00Z", "2026-10-20T04:00Z"])
    extended = pd.concat(
        [original, price_frame(["2026-10-20T08:00Z"])], ignore_index=True
    )
    if damage == "revised_price":
        extended.loc[0, "close"] = 102.0
    elif damage == "missing_row":
        extended = extended.iloc[1:].reset_index(drop=True)
    else:
        extended = pd.concat([extended, extended.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError):
        assert_unchanged_prefix(original, extended)

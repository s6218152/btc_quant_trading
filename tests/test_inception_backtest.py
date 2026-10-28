import pandas as pd
import pytest

from scripts.inception_backtest import consistent_prefix_start


def test_only_initial_vendor_inconsistent_prefix_may_be_excluded():
    start = pd.Timestamp("2019-09-09", tz="UTC")
    bad = pd.DatetimeIndex([pd.Timestamp("2019-09-24", tz="UTC")])
    assert consistent_prefix_start(start, bad) == pd.Timestamp("2019-09-25", tz="UTC")
    assert consistent_prefix_start(start, bad[:0]) == start


def test_do_not_drop_later_history_to_hide_price_errors():
    start = pd.Timestamp("2019-09-09", tz="UTC")
    with pytest.raises(ValueError, match="outside"):
        consistent_prefix_start(start, pd.DatetimeIndex([pd.Timestamp("2024-10-28", tz="UTC")]))

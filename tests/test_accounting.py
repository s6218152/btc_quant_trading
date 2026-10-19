import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.summarize_backtest import reconcile


def fixture_data():
    config = json.loads(Path("user_data/config.json").read_text())
    dates = pd.date_range("2024-01-01", periods=80, freq="4h", tz="UTC")
    candles = pd.DataFrame(
        {"date": dates, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0}
    )
    opens = [dates[50], dates[56]]
    trades = pd.DataFrame(
        [
            dict(
                open_date=opens[0],
                close_date=dates[51],
                open_rate=100,
                close_rate=110,
                amount=1,
                is_short=False,
                leverage=2,
                fee_open=0.001,
                fee_close=0.001,
                funding_fees=-0.01,
                profit_abs=9.78,
                stop_loss_abs=100,
            ),
            dict(
                open_date=opens[1],
                close_date=dates[57],
                open_rate=100,
                close_rate=90,
                amount=1,
                is_short=True,
                leverage=2,
                fee_open=0.001,
                fee_close=0.001,
                funding_fees=0.01,
                profit_abs=9.82,
                stop_loss_abs=100,
            ),
        ]
    )
    funding = pd.DataFrame({"date": opens, "funding_rate": 0.0001})
    marks = pd.DataFrame({"date": opens, "open": 100.0})
    return trades, candles, funding, marks, config, 10000


def test_long_short_accounting_funding_sign_and_budget():
    integrity, trades = reconcile(*fixture_data())
    assert integrity["passed"]
    assert trades["funding_recomputed"].tolist() == pytest.approx([-0.01, 0.01])
    assert trades["engine_cost_usdt"].sum() == pytest.approx(0.4)


def test_corrupt_pnl_and_funding_are_detected():
    data = fixture_data()
    data[0].loc[0, "profit_abs"] += 1
    data[0].loc[1, "funding_fees"] += 0.02
    integrity, _ = reconcile(*data)
    assert not integrity["passed"]
    assert not integrity["checks"]["pnl_reconciled"]
    assert not integrity["checks"]["funding_reconciled"]

import pandas as pd
import pytest

from scripts.mark_to_market import equity_from_fills, equity_metrics


def test_open_position_drawdown_and_entry_cost_are_not_hidden_by_wallet_balance():
    dates = pd.date_range("2025-01-01", periods=4, freq="1h", tz="UTC")
    marks = pd.DataFrame({"date": dates, "open": [100, 150, 80, 90]})
    funding = pd.DataFrame({"date": [dates[1]], "funding_rate": [0.001]})
    # Native final accounting: -10 price PnL - .1 entry - .09 exit - .15 funding.
    result = {
        "backtest_start": "2025-01-01 00:00:00",
        "backtest_end": "2025-01-01 03:00:00",
        "starting_balance": 100,
        "final_balance": 89.66,
        "trades": [
            dict(
                open_date=str(dates[0]),
                close_date=str(dates[3]),
                amount=1,
                open_rate=100,
                fee_open=0.001,
                is_short=False,
                profit_abs=-10.34,
            )
        ],
    }
    equity = equity_from_fills(result, marks, funding)
    assert equity.iloc[1] == pytest.approx(99.9)
    assert equity.iloc[2] == pytest.approx(149.75)
    assert equity.iloc[-1] == pytest.approx(89.66)
    assert equity_metrics(equity)["max_drawdown"] > 0.45


def test_initial_loss_counts_in_drawdown():
    equity = pd.Series(
        [100, 90, 80], index=pd.date_range("2025-01-01", periods=3, freq="1D", tz="UTC")
    )
    assert equity_metrics(equity)["max_drawdown"] == pytest.approx(0.2)

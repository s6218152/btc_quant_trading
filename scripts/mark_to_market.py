"""Reporting-only mark equity from native fills; never generate orders or select models."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.analyze_result import load_result

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "user_data/research/study"


def equity_from_fills(result: dict, marks: pd.DataFrame, funding: pd.DataFrame) -> pd.Series:
    start = pd.Timestamp(result["backtest_start"], tz="UTC")
    end = pd.Timestamp(result["backtest_end"], tz="UTC")
    prices = marks.set_index("date")["open"].loc[start:end]
    equity = pd.Series(result["starting_balance"], index=prices.index, dtype=float)
    events = funding.merge(marks[["date", "open"]], on="date", validate="one_to_one")
    for trade in result["trades"]:
        opened, closed = pd.Timestamp(trade["open_date"]), pd.Timestamp(trade["close_date"])
        direction = -1 if trade["is_short"] else 1
        active = (equity.index >= opened) & (equity.index < closed)
        equity.loc[equity.index >= closed] += trade["profit_abs"]
        if not active.any():
            continue
        charge = events.loc[(events["date"] >= opened) & (events["date"] <= closed)].copy()
        charge["cashflow"] = -direction * charge["funding_rate"] * charge["open"] * trade["amount"]
        accrued = charge.set_index("date")["cashflow"].reindex(equity.index, fill_value=0).cumsum()
        entry_fee = trade["open_rate"] * trade["amount"] * trade["fee_open"]
        equity.loc[active] += (
            direction * trade["amount"] * (prices.loc[active] - trade["open_rate"])
            - entry_fee
            + accrued.loc[active]
        )
    # The native engine force-closes open trades at its final timestamp.
    if not np.isclose(equity.iloc[-1], result["final_balance"], atol=1e-5):
        raise ValueError("Marked final equity does not reconcile to native closing balance")
    initial = pd.Series(
        [float(result["starting_balance"])], index=[equity.index[0] - pd.Timedelta(seconds=1)]
    )
    return pd.concat([initial, equity]).rename("equity_usdt")


def equity_metrics(equity: pd.Series) -> dict:
    drawdown = equity / equity.cummax() - 1
    daily = equity.resample("1D").last().pct_change().dropna()
    deviation = daily.std(ddof=1)
    downside = np.sqrt(np.mean(np.minimum(daily, 0) ** 2))
    return {
        "max_drawdown": float(-drawdown.min()),
        "sharpe_daily": float(daily.mean() / deviation * np.sqrt(365)) if deviation > 0 else None,
        "sortino_daily": float(daily.mean() / downside * np.sqrt(365)) if downside > 0 else None,
        "annualized_volatility": float(deviation * np.sqrt(365)) if len(daily) > 1 else None,
        "final_equity": float(equity.iloc[-1]),
        "valuation": "hourly Mark Price open; native fills and exact funding events",
        "selection_use": "none; reporting only, original training scores stay frozen",
    }


def main():
    summary_path = STUDY / "summary.json"
    summary = json.loads(summary_path.read_text())
    manifest = json.loads((STUDY / "data_manifest.json").read_text())
    marks = pd.read_feather(ROOT / manifest["files"]["mark"]["file"])
    funding = pd.read_feather(ROOT / manifest["files"]["funding"]["file"])
    metrics = {}
    for run_id in pd.read_csv(STUDY / "runs.csv")["run_id"]:
        directory = ROOT / "user_data/backtest_results/study" / run_id
        native = json.loads((directory / "native_result.json").read_text())
        result, _ = load_result(ROOT / native["path"])
        equity = equity_from_fills(result, marks, funding)
        marked = metrics[run_id] = equity_metrics(equity)
        equity.to_csv(directory / "mark_equity_hourly.csv")
        (directory / "mark_metrics.json").write_text(
            json.dumps(marked, indent=2, allow_nan=False) + "\n"
        )

    def attach(value):
        if isinstance(value, dict):
            if value.get("run_id") in metrics:
                value["mark_to_market"] = metrics[value["run_id"]]
            for key, child in list(value.items()):
                if key != "mark_to_market":
                    attach(child)
        elif isinstance(value, list):
            for child in value:
                attach(child)

    attach(summary)
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(f"Hourly mark-equity reports reconciled: {len(metrics)} runs")


if __name__ == "__main__":
    main()

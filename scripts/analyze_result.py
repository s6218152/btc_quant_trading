"""Compact, deterministic Freqtrade JSON/ZIP analytics without printing trades."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def load_result(path: Path, strategy: str | None = None) -> tuple[dict, dict]:
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            result_name = next(n for n in names if n.endswith(".json") and "_config" not in n)
            data = json.loads(archive.read(result_name))
            config_name = next(n for n in names if n.endswith("_config.json"))
            config = json.loads(archive.read(config_name))
    else:
        data = json.loads(path.read_text())
        config = data.get("config", {})
    strategies = data["strategy"]
    if strategy is None and len(strategies) != 1:
        raise ValueError("Choose a strategy for a multi-strategy result")
    return strategies[strategy or next(iter(strategies))], config


def loss_streak(pnl: pd.Series) -> int:
    longest = current = 0
    for value in pnl:
        current = current + 1 if value < 0 else 0
        longest = max(longest, current)
    return longest


def trade_stats(trades: pd.DataFrame) -> dict:
    pnl = trades["profit_abs"]
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    return {
        "trades": len(trades),
        "pnl_usdt": float(pnl.sum()),
        "win_rate": float((pnl > 0).mean()) if len(trades) else None,
        "average_win": float(wins.mean()) if len(wins) else None,
        "average_loss": float(losses.mean()) if len(losses) else None,
        "expectancy": float(pnl.mean()) if len(trades) else None,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) else None,
        "max_consecutive_losses": loss_streak(pnl),
    }


def summarize(result: dict, config: dict) -> dict:
    trades = pd.DataFrame(result["trades"])
    if trades.empty:
        trades = pd.DataFrame(columns=["profit_abs", "is_short", "close_date", "open_date"])
    trades = trades.sort_values("close_date")
    wallet = result.get("wallet_stats", {})
    base = {
        "strategy": result.get("strategy_name"),
        "period": result["timerange"],
        "actual_start": result["backtest_start"],
        "actual_end": result["backtest_end"],
        "starting_balance": result["starting_balance"],
        "final_balance": result["final_balance"],
        "total_return": result["profit_total"],
        "cagr": result["cagr"],
        "max_drawdown": wallet.get("max_drawdown_account", result["max_drawdown_account"]),
        "sharpe": wallet.get("sharpe", result["sharpe"]),
        "sortino": wallet.get("sortino", result["sortino"]),
        "trade_sharpe": result["sharpe"],
        "stats": trade_stats(trades),
        "long": trade_stats(trades[~trades["is_short"].astype(bool)]),
        "short": trade_stats(trades[trades["is_short"].astype(bool)]),
        "yearly_realized": {},
        "costs": {},
    }
    if not trades.empty:
        trades["close_date"] = pd.to_datetime(trades["close_date"], utc=True)
        for year, group in trades.groupby(trades["close_date"].dt.year):
            base["yearly_realized"][str(year)] = trade_stats(group)
        research = config.get("research", {})
        turnover = trades["amount"] * (trades["open_rate"] + trades["close_rate"])
        native_fees = (
            trades["amount"]
            * (
                trades["open_rate"] * trades["fee_open"]
                + trades["close_rate"] * trades["fee_close"]
            )
        ).sum()
        pnl_gross = (
            trades["amount"]
            * (trades["close_rate"] - trades["open_rate"])
            * np.where(trades["is_short"], -1, 1)
        ).sum()
        funding = float(trades["funding_fees"].sum())
        base["costs"] = {
            "engine_fees_including_proxy": float(native_fees),
            "trading_fees": (
                float(turnover.sum() * research["taker_fee"]) if "taker_fee" in research else None
            ),
            "slippage_proxy": (
                float(turnover.sum() * research["slippage_per_side"])
                if "slippage_per_side" in research
                else None
            ),
            "funding_cost_net": -funding,
            "funding_paid": float(-trades.loc[trades["funding_fees"] < 0, "funding_fees"].sum()),
            "funding_received": float(trades.loc[trades["funding_fees"] > 0, "funding_fees"].sum()),
            "pnl_error": float(abs(pnl_gross - native_fees + funding - trades["profit_abs"].sum())),
        }
        equity = result["starting_balance"]
        base["average_entry_exposure"] = float(
            (
                trades["open_rate"]
                * trades["amount"]
                / (equity + trades["profit_abs"].cumsum().shift(1, fill_value=0))
            ).mean()
        )
    return base


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--strategy")
    args = parser.parse_args()
    path = args.result.resolve()
    if not path.is_relative_to(ROOT):
        raise SystemExit("Result must be inside the repository")
    result, config = load_result(path, args.strategy)
    summary = summarize(result, config)
    marked = path.parent / "mark_metrics.json"
    if marked.exists():
        summary["mark_to_market"] = json.loads(marked.read_text())
    summary["risk_metric_scope"] = (
        "Native wallet metrics omit open-position PnL; use Mark metrics where available"
    )
    identity = hashlib.sha256(path.read_bytes()).hexdigest()
    output = ROOT / "user_data/backtest_results/analysis" / identity[:16]
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(result["trades"]).to_csv(output / "trades.csv", index=False)
    summary["source_sha256"] = identity
    (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {k: summary[k] for k in ["period", "total_return", "max_drawdown", "stats", "costs"]}
        )
    )
    print("Saved:", output.relative_to(ROOT) / "summary.json")
    print("Risk scope:", summary["risk_metric_scope"])
    if "mark_to_market" in summary:
        print("Mark risk:", json.dumps(summary["mark_to_market"]))


if __name__ == "__main__":
    main()

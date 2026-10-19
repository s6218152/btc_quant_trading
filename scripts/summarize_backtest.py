"""Export concise research metrics and independently reconcile engine accounting."""

import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import talib.abstract as ta

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "user_data/research"


def reconcile(
    trades: pd.DataFrame,
    candles: pd.DataFrame,
    funding: pd.DataFrame,
    marks: pd.DataFrame,
    config: dict,
    initial: float,
) -> tuple[dict, pd.DataFrame]:
    trades = trades.sort_values("open_date").copy()
    if trades.empty:
        raise ValueError("A zero-trade run is not a validated baseline")
    trades["open_date"] = pd.to_datetime(trades["open_date"], utc=True)
    trades["close_date"] = pd.to_datetime(trades["close_date"], utc=True)
    risk = config["research"]
    candles = candles.copy()
    candles["atr"] = ta.ATR(candles, timeperiod=14)
    candles["available_at"] = candles["date"] + pd.Timedelta(hours=4)
    trades = pd.merge_asof(
        trades,
        candles[["available_at", "atr"]],
        left_on="open_date",
        right_on="available_at",
        direction="backward",
    )
    trades["equity_before"] = initial + trades["profit_abs"].cumsum().shift(1, fill_value=0)
    trades["initial_atr_stop"] = (
        trades["open_rate"]
        + np.where(trades["is_short"], 1, -1) * trades["atr"] * risk["atr_multiplier"]
    )
    trades["notional_entry"] = trades["amount"] * trades["open_rate"]
    trades["turnover"] = trades["amount"] * (trades["open_rate"] + trades["close_rate"])
    trades["trading_fees_usdt"] = trades["turnover"] * risk["taker_fee"]
    trades["slippage_proxy_usdt"] = trades["turnover"] * risk["slippage_per_side"]
    trades["engine_cost_usdt"] = trades["amount"] * (
        trades["open_rate"] * trades["fee_open"] + trades["close_rate"] * trades["fee_close"]
    )
    trades["gross_pnl"] = (
        trades["amount"]
        * (trades["close_rate"] - trades["open_rate"])
        * (np.where(trades["is_short"], -1, 1))
    )
    trades["reconciled_pnl"] = (
        trades["gross_pnl"] - trades["engine_cost_usdt"] + trades["funding_fees"]
    )
    distance = trades["atr"] * risk["atr_multiplier"]
    trades["budgeted_risk"] = trades["amount"] * (
        distance
        + (2 * trades["open_rate"] + distance) * config["fee"]
        + trades["open_rate"] * risk.get("funding_reserve_per_trade", 0)
    )
    trades["risk_fraction"] = trades["budgeted_risk"] / trades["equity_before"]
    events = funding.merge(marks[["date", "open"]], on="date", validate="one_to_one")
    funding_recomputed = []
    for trade in trades.itertuples():
        # Freqtrade Binance 2026.9 includes funding timestamps at both boundaries.
        selected = events[
            (events["date"] >= trade.open_date) & (events["date"] <= trade.close_date)
        ]
        cost = float((selected["funding_rate"] * selected["open"]).sum()) * trade.amount
        funding_recomputed.append(cost if trade.is_short else -cost)
    trades["funding_recomputed"] = funding_recomputed
    overlap = trades["open_date"] < trades["close_date"].shift()
    tight_stop = np.where(
        trades["is_short"],
        trades["stop_loss_abs"] <= trades["initial_atr_stop"] + 0.02,
        trades["stop_loss_abs"] >= trades["initial_atr_stop"] - 0.02,
    )
    checks = {
        "no_overlapping_positions": not bool(overlap.any()),
        "leverage_at_most_2": bool((trades["leverage"] <= 2).all()),
        "effective_exposure_at_most_2": bool(
            (
                trades["notional_entry"]
                <= (trades["equity_before"] * risk["max_effective_leverage"] + 0.01)
            ).all()
        ),
        "budgeted_risk_at_most_limit": bool(
            (trades["risk_fraction"] <= (risk["risk_per_trade"] + 1e-8)).all()
        ),
        "atr_stop_installed_and_tightened": bool(tight_stop.all()),
        "pnl_reconciled": bool(
            np.allclose(trades["reconciled_pnl"], trades["profit_abs"], atol=1e-5)
        ),
        "funding_reconciled": bool(
            np.allclose(trades["funding_recomputed"], trades["funding_fees"], atol=1e-5)
        ),
        "fee_proxy_reconciled": bool(
            np.allclose(
                trades["trading_fees_usdt"] + trades["slippage_proxy_usdt"],
                trades["engine_cost_usdt"],
                atol=1e-5,
            )
        ),
        "funding_mark_join_complete": len(events) == len(funding),
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "max_budgeted_risk_fraction": float(trades["risk_fraction"].max()),
        "max_pnl_error_usdt": float((trades["reconciled_pnl"] - trades["profit_abs"]).abs().max()),
        "max_funding_error_usdt": float(
            (trades["funding_recomputed"] - trades["funding_fees"]).abs().max()
        ),
    }, trades


def main() -> None:
    results = ROOT / "user_data/backtest_results"
    latest = json.loads((results / ".last_result.json").read_text())["latest_backtest"]
    path = results / latest
    with zipfile.ZipFile(path) as archive:
        result_name = next(
            n for n in archive.namelist() if n.endswith(".json") and "_config" not in n
        )
        result = json.loads(archive.read(result_name))["strategy"]["Donchian4HStrategy"]
        config_name = next(n for n in archive.namelist() if n.endswith("_config.json"))
        config = json.loads(archive.read(config_name))
        bundled_hashes = {
            n: hashlib.sha256(archive.read(n)).hexdigest()
            for n in archive.namelist()
            if not n.endswith(".feather")
        }
    manifest = json.loads((OUT / "data_manifest.json").read_text())
    if manifest["status"] != "PASS":
        raise SystemExit("Data audit did not pass")
    for info in manifest["files"].values():
        source = ROOT / info["file"]
        if hashlib.sha256(source.read_bytes()).hexdigest() != info["sha256"]:
            raise SystemExit("Data changed since audit")
    frames = {k: pd.read_feather(ROOT / v["file"]) for k, v in manifest["files"].items()}
    integrity, trades = reconcile(
        pd.DataFrame(result["trades"]),
        frames["4h"],
        frames["funding"],
        frames["mark"],
        config,
        result["starting_balance"],
    )
    trades.to_csv(results / "baseline-trades.csv", index=False)
    winning, losing = trades.loc[trades["profit_abs"] > 0], trades.loc[trades["profit_abs"] <= 0]
    wallet = result["wallet_stats"]
    summary = {
        "study": manifest["plan"]["study"],
        "period": result["timerange"],
        "starting_balance": result["starting_balance"],
        "final_balance": result["final_balance"],
        "total_return": result["profit_total"],
        "cagr": result["cagr"],
        "max_drawdown": wallet["max_drawdown_account"],
        "sharpe_daily_wallet": wallet["sharpe"],
        "sortino_daily_wallet": wallet["sortino"],
        "sharpe_native_trade_based": result["sharpe"],
        "profit_factor": result["profit_factor"],
        "win_rate": result["winrate"],
        "average_win_usdt": float(winning["profit_abs"].mean()) if len(winning) else None,
        "average_loss_usdt": float(losing["profit_abs"].mean()) if len(losing) else None,
        "expectancy_usdt": float(trades["profit_abs"].mean()),
        "total_trades": result["total_trades"],
        "long_trades": result["trade_count_long"],
        "short_trades": result["trade_count_short"],
        "max_consecutive_losses": result["max_consecutive_losses"],
        "trading_fees_usdt": float(trades["trading_fees_usdt"].sum()),
        "slippage_proxy_usdt": float(trades["slippage_proxy_usdt"].sum()),
        "funding_costs_usdt": float(-trades["funding_fees"].sum()),
        "integrity": integrity,
        "data_warnings": manifest["warnings"],
        "artifacts": {
            "result_zip": str(path.relative_to(ROOT)),
            "zip_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bundled_files_sha256": bundled_hashes,
            "data_manifest_sha256": hashlib.sha256(
                (OUT / "data_manifest.json").read_bytes()
            ).hexdigest(),
            "market_rules_sha256": hashlib.sha256(
                (OUT / "market_rules.json").read_bytes()
            ).hexdigest(),
            "execution_files_sha256": {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [
                    ROOT / "Dockerfile.research",
                    ROOT / "requirements-research.txt",
                    ROOT / "compose.yaml",
                    ROOT / "user_data/config.json",
                    *sorted((ROOT / "scripts").glob("*.py")),
                ]
            },
        },
        "limitations": manifest["plan"]["notes"]
        + [
            "4h intrabar sequence and stop fills follow Freqtrade assumptions; "
            "no order-book replay.",
            "Liquidation prices/buffer are modeled; liquidation fees are not modeled by Freqtrade.",
            "Exchange market metadata/precision are current, not point-in-time historical rules.",
            "Export initial_stop_loss fields show emergency fallback; "
            "initial_atr_stop is reconstructed independently.",
            "No walk-forward, parameter robustness or held-out OOS claim is made in Phase 6.",
        ],
    }
    (OUT / "baseline_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: summary[k]
                for k in [
                    "period",
                    "total_return",
                    "max_drawdown",
                    "total_trades",
                    "trading_fees_usdt",
                    "slippage_proxy_usdt",
                    "funding_costs_usdt",
                ]
            }
        )
    )
    print("Accounting / risk integrity:", integrity)
    raise SystemExit(not integrity["passed"])


if __name__ == "__main__":
    main()

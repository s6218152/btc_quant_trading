"""Frozen, resumable research orchestration around native Freqtrade backtests."""

import argparse
import copy
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.analyze_result import load_result, summarize
from scripts.summarize_backtest import reconcile

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "user_data/research/study"
PROTOCOL = STUDY / "protocol.json"
BASE_CONFIG = ROOT / "user_data/config.json"
DATA = ROOT / "user_data/data/walkforward/futures"
RESULTS = ROOT / "user_data/backtest_results/study"


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def coarse_candidates(base: dict, space: dict) -> list[dict]:
    candidates = {canonical(base): base.copy()}
    for key in ("entry_period", "exit_period", "atr_multiplier", "risk_per_trade"):
        for value in space[key]:
            candidate = {**base, key: value}
            candidates[canonical(candidate)] = candidate
    return list(candidates.values())


def select_candidate(results: list[tuple[dict, dict]]) -> dict | None:
    eligible = [
        (p, s) for p, s in results if s["stats"]["trades"] >= 10 and np.isfinite(s["sharpe"])
    ]
    if not eligible:
        return None
    return min(eligible, key=lambda v: (-v[1]["sharpe"], v[1]["max_drawdown"], canonical(v[0])))[0]


def refine_candidates(best: dict, rule: dict) -> list[dict]:
    refined = []
    for key, deltas in [
        ("entry_period", rule["entry_delta"]),
        ("exit_period", rule["exit_delta"]),
        ("atr_multiplier", rule["atr_delta"]),
    ]:
        for delta in deltas:
            value = best[key] + delta
            if value > 0:
                refined.append({**best, key: value})
    return refined[: rule["max_extra_candidates"]]


def assert_training_before_test(train: str, test: str) -> None:
    train_start, train_end = train.split("-")
    test_start, test_end = test.split("-")
    if not train_start < train_end <= test_start < test_end:
        raise ValueError("Training must end before or at the OOS start")


def native_timerange(period: str) -> str:
    start, end = period.split("-")
    begin = int(pd.to_datetime(start, format="%Y%m%d", utc=True).timestamp())
    exclusive_end = int(pd.to_datetime(end, format="%Y%m%d", utc=True).timestamp())
    return f"{begin}-{exclusive_end - 1}"


class Study:
    def __init__(self, results_root: Path | None = None):
        self.results_root = results_root if results_root is not None else RESULTS
        self.protocol = json.loads(PROTOCOL.read_text())
        self.config = json.loads(BASE_CONFIG.read_text())
        if not self.config["dry_run"] or any(
            self.config["exchange"].get(k) for k in ("key", "secret")
        ):
            raise ValueError("Study requires credential-free dry-run configuration")
        self.manifest = json.loads((STUDY / "data_manifest.json").read_text())
        if self.manifest["status"] != "PASS":
            raise ValueError("Data audit must pass")
        for info in self.manifest["files"].values():
            if sha(ROOT / info["file"]) != info["sha256"]:
                raise ValueError("Data changed after validation")
        self.identity = {
            "protocol": sha(PROTOCOL),
            "base_config": sha(BASE_CONFIG),
            "strategy": sha(ROOT / "user_data/strategies/Donchian4HStrategy.py"),
            "reference": sha(ROOT / "user_data/strategies/BuyHoldReference.py"),
            "environment": sha(ROOT / "user_data/research/environment.lock.txt"),
            "market_rules": sha(ROOT / "user_data/research/market_rules.json"),
            "data": {k: v["sha256"] for k, v in self.manifest["files"].items()},
        }
        frozen = STUDY / "frozen_identity.json"
        if frozen.exists() and json.loads(frozen.read_text()) != self.identity:
            raise ValueError(
                "Frozen study changed: create a new study, do not silently reuse results"
            )
        if not frozen.exists():
            frozen.write_text(json.dumps(self.identity, indent=2) + "\n")
        self.frames = {
            k: pd.read_feather(ROOT / v["file"]) for k, v in self.manifest["files"].items()
        }
        self.runs = {}
        self.base = {
            k: self.config["research"][k]
            for k in ("entry_period", "exit_period", "atr_multiplier", "risk_per_trade")
        }

    def run(self, params: dict, period: str, kind: str, strategy="Donchian4HStrategy") -> dict:
        if period.split("-")[1] > "20260101":
            raise ValueError("The 2026 final holdout is sealed")
        config = copy.deepcopy(self.config)
        config["research"].update(params)
        config["fee"] = config["research"]["taker_fee"] + config["research"]["slippage_per_side"]
        key = {
            "identity": self.identity,
            "params": params,
            "period": period,
            "strategy": strategy,
            "boundary_policy": "open_time_strictly_before_end_v1",
        }
        run_id = hashlib.sha256(canonical(key).encode()).hexdigest()[:20]
        directory = self.results_root / run_id
        directory.mkdir(parents=True, exist_ok=True)
        config_path = directory / "config.json"
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        native = directory / "native_result.json"
        if native.exists():
            native_info = json.loads(native.read_text())
            zip_path = ROOT / native_info["path"]
            if sha(zip_path) != native_info["sha256"]:
                raise ValueError("Cached result checksum mismatch")
        else:
            command = [
                "freqtrade",
                "backtesting",
                "--config",
                str(config_path),
                "--datadir",
                str(DATA.parent),
                "--timerange",
                native_timerange(period),
                "--strategy",
                strategy,
                "--enable-protections",
                "--cache",
                "none",
                "--export",
                "trades",
                "--backtest-directory",
                str(directory),
            ]
            logfile = directory / "native.log"
            with logfile.open("w") as handle:
                process = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT)
            latest = directory / ".last_result.json"
            if process.returncode or " - ERROR - " in logfile.read_text() or not latest.exists():
                raise RuntimeError(f"Native backtest failed; see {logfile.relative_to(ROOT)}")
            zip_path = directory / json.loads(latest.read_text())["latest_backtest"]
            native.write_text(
                json.dumps(
                    {"path": str(zip_path.relative_to(ROOT)), "sha256": sha(zip_path)}, indent=2
                )
                + "\n"
            )
        result, saved_config = load_result(zip_path, strategy)
        metrics = summarize(result, saved_config)
        metrics["period"] = period
        trades = pd.DataFrame(result["trades"])
        if not trades.empty:
            cap = config["research"].get("contract_leverage", 2)
            if (trades["leverage"] > cap + 1e-9).any():
                raise ValueError("Actual contract leverage exceeds the configured comparison cap")
            end = pd.to_datetime(period.split("-")[1], format="%Y%m%d", utc=True)
            if (pd.to_datetime(trades["close_date"], utc=True) >= end).any():
                raise ValueError("Trade leaked beyond the requested evaluation boundary")
            integrity, audited = reconcile(
                trades,
                self.frames["4h"],
                self.frames["funding"],
                self.frames["mark"],
                saved_config,
                result["starting_balance"],
            )
            if strategy == "BuyHoldReference":
                not_applicable = {"budgeted_risk_at_most_limit", "atr_stop_installed_and_tightened"}
                integrity["not_applicable_to_hold_reference"] = sorted(not_applicable)
                integrity["passed"] = all(
                    v for k, v in integrity["checks"].items() if k not in not_applicable
                )
            if not integrity["passed"]:
                raise ValueError(f"Accounting / risk audit failed in {run_id}: {integrity}")
            audited.to_csv(directory / "trades.csv", index=False)
        else:
            integrity = {"passed": True, "note": "No trades; not eligible for selection"}
        metrics.update(
            run_id=run_id,
            params=params,
            kind=kind,
            integrity=integrity,
            source=str(zip_path.relative_to(ROOT)),
        )
        (directory / "summary.json").write_text(
            json.dumps(metrics, indent=2, allow_nan=False) + "\n"
        )
        self.runs[run_id] = metrics
        return metrics

    def regime_breakdown(self, run: dict) -> list[dict]:
        path = self.results_root / run["run_id"] / "trades.csv"
        if not path.exists():
            return []
        trades = pd.read_csv(path)
        trades["open_date"] = pd.to_datetime(trades["open_date"], utc=True)
        daily = self.frames["1d"].copy()
        days = self.protocol["regimes"]["direction_lookback_days"]
        daily["return_90d"] = daily["close"].pct_change(days)
        daily["volatility"] = daily["close"].pct_change().rolling(days).std() * np.sqrt(365)
        daily["regime"] = np.select(
            [daily["return_90d"] > 0.15, daily["return_90d"] < -0.15],
            ["bull", "bear"],
            default="sideways",
        )
        daily["volatility_regime"] = np.where(daily["volatility"] > 0.6, "high", "normal")
        daily["available_at"] = daily["date"] + pd.Timedelta(days=1)
        assigned = pd.merge_asof(
            trades.sort_values("open_date"),
            daily[["available_at", "regime", "volatility_regime"]],
            left_on="open_date",
            right_on="available_at",
        )
        return [
            {
                "run_id": run["run_id"],
                "period": run["period"],
                "regime": a,
                "volatility": b,
                "trades": len(g),
                "pnl_usdt": float(g["profit_abs"].sum()),
                "long_trades": int((~g["is_short"]).sum()),
                "short_trades": int(g["is_short"].sum()),
            }
            for (a, b), g in assigned.groupby(["regime", "volatility_regime"])
        ]

    def execute(self) -> None:
        coarse = coarse_candidates(self.base, self.protocol["coarse_search"])
        development = [
            (p, self.run(p, self.protocol["development_range"], "coarse")) for p in coarse
        ]
        best = select_candidate(development)
        refined = []
        if best is not None:
            winner = next(s for p, s in development if p == best)
            if winner["sharpe"] > 0.5:
                refined = refine_candidates(best, self.protocol["refinement"])
        pool = {canonical(p): p for p in [*coarse, *refined]}
        refined_results = [
            self.run(params, self.protocol["development_range"], "refined") for params in refined
        ]
        candidates = {
            "coarse": coarse,
            "refined": refined,
            "pool": list(pool.values()),
            "generated_from": "2024 development ONLY",
            "identity": self.identity,
        }
        pool_path = STUDY / "frozen_candidates.json"
        if pool_path.exists() and json.loads(pool_path.read_text()) != candidates:
            raise ValueError("Candidate pool changed after freezing")
        if not pool_path.exists():
            pool_path.write_text(json.dumps(candidates, indent=2) + "\n")
        print(
            f"Candidate pool frozen: {len(coarse)} coarse + {len(refined)} local refinements",
            flush=True,
        )
        # Cost sensitivity is development-only and cannot alter the candidate pool.
        costs = []
        for field in ("taker_fee", "slippage_per_side"):
            for value in self.protocol["cost_scenarios"][field]:
                params = {**self.base, field: value}
                costs.append(
                    self.run(params, self.protocol["development_range"], "cost_sensitivity")
                )
        folds = []
        for fold in self.protocol["folds"]:
            assert_training_before_test(fold["train"], fold["test"])
            choices = list(pool.values()) if fold["pristine"] else coarse
            training = [(p, self.run(p, fold["train"], "training")) for p in choices]
            selected = select_candidate(training)
            selection = {
                **fold,
                "selected": selected,
                "training_runs": [s["run_id"] for _, s in training],
            }
            decision = STUDY / f"selection-{fold['name']}.json"
            # Save decisions BEFORE invoking the corresponding OOS backtest.
            if decision.exists() and json.loads(decision.read_text()) != selection:
                raise ValueError("OOS selection changed; no retrospective re-selection allowed")
            if not decision.exists():
                decision.write_text(json.dumps(selection, indent=2) + "\n")
            if selected is None:
                selection["test_result"] = {"total_return": 0, "cash_only": True}
            else:
                selection["test_result"] = self.run(selected, fold["test"], "walk_forward_oos")
            folds.append(selection)
            print(f"Fold {fold['name']} complete", flush=True)
        historical = [
            self.run(self.base, period, "fixed_baseline_year")
            for period in self.protocol["regimes"]["historical_baseline_ranges"]
        ]
        comparison = self.protocol["comparison"]
        common = {**self.base, "contract_leverage": 1.0, "max_effective_leverage": 1.0}
        comparisons = [
            self.run({**common, "ema_filter": flag}, comparison["range"], "comparison_1x")
            for flag in (True, False)
        ]
        comparisons.append(
            self.run(
                {
                    **common,
                    "hold_allocation": comparison["hold_allocation"],
                    "reference_start": "2025-01-01T00:00:00Z",
                },
                comparison["range"],
                "comparison_1x",
                "BuyHoldReference",
            )
        )
        all_runs = list(self.runs.values())
        pd.DataFrame(
            [
                {
                    "run_id": r["run_id"],
                    "kind": r["kind"],
                    "period": r["period"],
                    **r["params"],
                    "return": r["total_return"],
                    "drawdown": r["max_drawdown"],
                    "sharpe": r["sharpe"],
                    "trades": r["stats"]["trades"],
                }
                for r in all_runs
            ]
        ).to_csv(STUDY / "runs.csv", index=False)
        pd.DataFrame([row for run in historical for row in self.regime_breakdown(run)]).to_csv(
            STUDY / "regimes.csv", index=False
        )
        summary = {
            "identity": self.identity,
            "candidate_count": len(pool),
            "folds": folds,
            "pristine_2025_linked_return": float(
                np.prod([1 + f["test_result"]["total_return"] for f in folds if f["pristine"]]) - 1
            ),
            "independent_fold_reset": True,
            "costs": costs,
            "historical": historical,
            "comparisons_1x": comparisons,
            "development": [s for _, s in development],
            "refined_development": refined_results,
            "unique_backtests": len(self.runs),
            "final_holdout_opened": False,
        }
        (STUDY / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
        print(
            json.dumps(
                {
                    k: summary[k]
                    for k in [
                        "candidate_count",
                        "unique_backtests",
                        "pristine_2025_linked_return",
                        "final_holdout_opened",
                    ]
                }
            ),
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    Study().execute()


if __name__ == "__main__":
    main()

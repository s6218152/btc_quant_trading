"""Validate saved study provenance and chronology without rerunning backtests."""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pandas as pd

from scripts.analyze_result import load_result
from scripts.study import select_candidate

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "user_data/research/study"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    summary = json.loads((STUDY / "summary.json").read_text())
    identity = json.loads((STUDY / "frozen_identity.json").read_text())
    manifest = json.loads((STUDY / "data_manifest.json").read_text())
    checks = {
        "frozen_identity": summary["identity"] == identity,
        "data_integrity": manifest["status"] == "PASS",
        "source_identity": digest(ROOT / "user_data/strategies/Donchian4HStrategy.py")
        == identity["strategy"],
        "protocol_identity": digest(STUDY / "protocol.json") == identity["protocol"],
        "all_native_hashes": True,
        "all_native_source_matches": True,
        "all_bounds_exclusive": True,
        "all_contract_caps": True,
        "accounting_and_risk": True,
        "selections_match_training": True,
        "decisions_precede_oos": True,
        "final_2026_sealed": not summary["final_holdout_opened"],
        "marked_equity_reconciles": True,
    }
    for key, info in manifest["files"].items():
        checks["data_integrity"] &= digest(ROOT / info["file"]) == identity["data"][key]
    actual = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    frozen = (ROOT / "user_data/research/environment.lock.txt").read_text().splitlines()
    checks["environment_matches_lock"] = sorted(actual) == sorted(frozen)
    ids = pd.read_csv(STUDY / "runs.csv")["run_id"].tolist()
    runs = {}
    for run_id in ids:
        folder = ROOT / "user_data/backtest_results/study" / run_id
        run = runs[run_id] = json.loads((folder / "summary.json").read_text())
        native = json.loads((folder / "native_result.json").read_text())
        path = ROOT / native["path"]
        checks["all_native_hashes"] &= digest(path) == native["sha256"]
        result, config = load_result(path)
        mark = json.loads((folder / "mark_metrics.json").read_text())
        checks["marked_equity_reconciles"] &= (
            abs(mark["final_equity"] - result["final_balance"]) < 1e-5
        )
        start, end = [
            pd.to_datetime(v, format="%Y%m%d", utc=True) for v in run["period"].split("-")
        ]
        checks["final_2026_sealed"] &= end <= pd.Timestamp("2026-01-01", tz="UTC")
        trades = pd.DataFrame(result["trades"])
        if not trades.empty:
            opened = pd.to_datetime(trades["open_date"], utc=True)
            closed = pd.to_datetime(trades["close_date"], utc=True)
            checks["all_bounds_exclusive"] &= bool(((opened >= start) & (closed < end)).all())
            cap = config["research"].get("contract_leverage", 2)
            checks["all_contract_caps"] &= bool((trades["leverage"] <= cap + 1e-9).all())
        checks["accounting_and_risk"] &= run["integrity"]["passed"]
        with zipfile.ZipFile(path) as archive:
            filename = result["strategy_name"] + ".py"
            source = next(n for n in archive.namelist() if n.endswith(filename))
            expected = identity[
                "reference" if result["strategy_name"] == "BuyHoldReference" else "strategy"
            ]
            checks["all_native_source_matches"] &= (
                hashlib.sha256(archive.read(source)).hexdigest() == expected
            )
    for fold in summary["folds"]:
        file = STUDY / f"selection-{fold['name']}.json"
        decision = json.loads(file.read_text())
        training = [runs[i] for i in decision["training_runs"]]
        selected = select_candidate([(r["params"], r) for r in training])
        checks["selections_match_training"] &= selected == decision["selected"] == fold["selected"]
        test_start = pd.to_datetime(fold["test"].split("-")[0], format="%Y%m%d", utc=True)
        checks["selections_match_training"] &= all(
            pd.to_datetime(r["period"].split("-")[1], format="%Y%m%d", utc=True) <= test_start
            for r in training
        )
        if not fold["test_result"].get("cash_only"):
            test_zip = ROOT / fold["test_result"]["source"]
            checks["decisions_precede_oos"] &= (
                file.stat().st_mtime_ns <= test_zip.stat().st_mtime_ns
            )
    report = {
        "passed": all(checks.values()),
        "checks": checks,
        "unique_native_runs": len(ids),
        "implementation_sha256": {
            str(p.relative_to(ROOT)): digest(p)
            for p in [
                ROOT / "scripts/study.py",
                ROOT / "scripts/analyze_result.py",
                ROOT / "scripts/audit_data.py",
                ROOT / "scripts/validate_study.py",
                ROOT / "scripts/mark_to_market.py",
            ]
        },
    }
    (STUDY / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, separators=(",", ":")))
    raise SystemExit(not report["passed"])


if __name__ == "__main__":
    main()

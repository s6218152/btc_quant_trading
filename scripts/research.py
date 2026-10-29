"""Small, credential-free entrypoint to the pinned Freqtrade environment."""

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = "user_data/config.json"
COMMANDS = {
    "download": [
        "download-data",
        "--config",
        CONFIG,
        "--timerange",
        "20210101-20250101",
        "--timeframes",
        "4h",
        "1d",
        "--trading-mode",
        "futures",
    ],
    "backtest": [
        "backtesting",
        "--config",
        CONFIG,
        "--timerange",
        "20240101-20250101",
        "--strategy",
        "Donchian4HStrategy",
        "--enable-protections",
        "--cache",
        "none",
        "--export",
        "trades",
    ],
    "lookahead": [
        "lookahead-analysis",
        "--config",
        CONFIG,
        "--timerange",
        "20240101-20250101",
        "--strategy",
        "Donchian4HStrategy",
        "--minimum-trade-amount",
        "5",
        "--targeted-trade-amount",
        "20",
        "--lookahead-analysis-exportfilename",
        "user_data/research/lookahead.csv",
    ],
    "list": ["list-strategies", "--config", CONFIG],
    "study-download": [
        "download-data",
        "--config",
        CONFIG,
        "--datadir",
        "user_data/data/walkforward",
        "--timerange",
        "20200101-20260101",
        "--timeframes",
        "4h",
        "1d",
        "--trading-mode",
        "futures",
    ],
    "stress-download": [
        "download-data",
        "--config",
        CONFIG,
        "--datadir",
        "user_data/data/stress_archive",
        "--timerange",
        "20240101-20260101",
        "--timeframes",
        "1h",
        "15m",
        "--trading-mode",
        "futures",
        "--candle-types",
        "futures",
        "mark",
    ],
}


def run_logged(cmd: list[str], action: str) -> int:
    logs = ROOT / "user_data/logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    logfile = logs / f"{action}-{stamp}.log"
    with logfile.open("w") as handle:
        status = subprocess.run(cmd, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT).returncode
    text = logfile.read_text()
    if " - ERROR - " in text:
        status = status or 1  # Some native CLI config failures return zero.
    print(f"{action}: {'PASS' if status == 0 else 'FAIL'}; log: {logfile.relative_to(ROOT)}")
    if status:
        print("\n".join(text.splitlines()[-30:]))
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            *COMMANDS,
            "build",
            "test",
            "lint",
            "audit",
            "report",
            "analyze",
            "study",
            "study-report",
            "study-validate",
            "stress",
            "stress-prepare",
            "stress-report",
            "stress-validate",
            "dryrun-start",
            "dryrun-stop",
            "dryrun-status",
            "dryrun-logs",
            "history",
            "inception-download",
            "inception",
            "return-study",
            "candidate-inception-download",
            "candidate-inception",
            "eth-history-download",
            "eth-history",
        ],
    )
    parser.add_argument("--result", help="Repository-relative native result JSON or ZIP")
    parser.add_argument("--allow-final-holdout", action="store_true")
    parser.add_argument("--test-path", help="Run one focused test file inside the pinned image")
    parser.add_argument("--profile", choices=["long-only"])
    args = parser.parse_args()
    if args.test_path and args.action != "test":
        parser.error("--test-path is only valid with test")
    if args.profile and args.action != "dryrun-start":
        parser.error("--profile is only valid with dryrun-start")
    if args.action in {"inception", "inception-download",
                       "candidate-inception", "candidate-inception-download",
                       "eth-history", "eth-history-download"}:
        if not args.allow_final_holdout:
            parser.error("This isolated evaluation requires --allow-final-holdout")
        module = "inception_backtest"
        if args.action.startswith("candidate-"):
            module = "candidate_inception"
        elif args.action.startswith("eth-"):
            module = "eth_history"
        command = ["docker", "compose", "run", "--rm", "--no-deps", "--entrypoint", "python",
                   "research", f"scripts/{module}.py",
                   "download" if args.action.endswith("-download") else "run",
                   "--allow-final-holdout"]
        raise SystemExit(run_logged(command, args.action))
    if args.action.startswith("dryrun-"):
        from dryrun import main as dryrun_main

        raise SystemExit(dryrun_main(args.action, args.profile or "long-only"))
    if args.action == "analyze" and not args.result:
        parser.error("analyze requires --result")
    config = json.loads((ROOT / CONFIG).read_text())
    if (
        not config.get("dry_run")
        or config["exchange"].get("key")
        or config["exchange"].get("secret")
    ):
        raise SystemExit("Research runner rejects live configuration or exchange credentials")
    prefix = ["docker", "compose", "run", "--rm", "--no-deps"]
    if args.action == "build":
        cmd = ["docker", "compose", "build", "research"]
    elif args.action in COMMANDS:
        cmd = [*prefix, "research", *COMMANDS[args.action]]
        if args.action == "lookahead":
            # Freqtrade 2026.9 + Pandas 3 cannot reliably append string fields
            # to a previous all-empty CSV column inferred as float. Keep each
            # native export immutable and publish a separate latest copy.
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
            lookahead_file = f"user_data/backtest_results/lookahead-{stamp}.csv"
            cmd[cmd.index("--lookahead-analysis-exportfilename") + 1] = lookahead_file
    else:
        commands = {
            "return-study": ["--entrypoint", "python", "research", "scripts/return_study.py"],
            "history": ["--entrypoint", "python", "research", "scripts/historical_backtest.py"],
            "test": ["--entrypoint", "python", "research", "-m", "pytest"],
            "lint": ["--entrypoint", "ruff", "research", "check", "."],
            "audit": ["--entrypoint", "python", "research", "scripts/audit_data.py"],
            "report": ["--entrypoint", "python", "research", "scripts/summarize_backtest.py"],
            "study": ["--entrypoint", "python", "research", "scripts/study.py"],
            "study-report": ["--entrypoint", "python", "research", "scripts/study_report.py"],
            "study-validate": ["--entrypoint", "python", "research", "scripts/validate_study.py"],
            "stress": ["--entrypoint", "python", "research", "scripts/stress.py", "run"],
            "stress-prepare": [
                "--entrypoint",
                "python",
                "research",
                "scripts/stress.py",
                "prepare",
            ],
            "stress-report": ["--entrypoint", "python", "research", "scripts/stress.py", "report"],
            "stress-validate": [
                "--entrypoint",
                "python",
                "research",
                "scripts/stress.py",
                "validate",
            ],
            "analyze": [
                "--entrypoint",
                "python",
                "research",
                "scripts/analyze_result.py",
                "--result",
                args.result,
            ],
        }
        cmd = [*prefix, *commands[args.action]]
        if args.action == "test" and args.test_path:
            cmd += [args.test_path]
    if args.action in {"backtest", "lookahead", "study"}:
        audit = [*prefix, "--entrypoint", "python", "research", "scripts/audit_data.py"]
        if args.action == "study":
            audit += [
                "--plan",
                "user_data/research/study/protocol.json",
                "--datadir",
                "user_data/data/walkforward/futures",
                "--output",
                "user_data/research/study/data_manifest.json",
            ]
        status = subprocess.run(audit, cwd=ROOT).returncode
        if status:
            raise SystemExit(status)
        market = [*prefix, "--entrypoint", "python", "research", "scripts/snapshot_market.py"]
        if status := subprocess.run(market, cwd=ROOT).returncode:
            raise SystemExit(status)
    previous_result = None
    latest = ROOT / "user_data/backtest_results/.last_result.json"
    if args.action == "backtest" and latest.exists():
        previous_result = latest.read_text()
    status = (
        run_logged(cmd, args.action)
        if args.action
        in {
            "backtest",
            "lookahead",
            "download",
            "study-download",
            "study",
            "stress",
            "stress-download",
            "history",
            "return-study",
        }
        else subprocess.run(cmd, cwd=ROOT).returncode
    )
    if args.action == "download" and status == 0:
        freeze = [
            *prefix,
            "--entrypoint",
            "python",
            "research",
            "scripts/audit_data.py",
            "--freeze",
        ]
        status = subprocess.run(freeze, cwd=ROOT).returncode
    if args.action == "study-download" and status == 0:
        freeze = [
            *prefix,
            "--entrypoint",
            "python",
            "research",
            "scripts/audit_data.py",
            "--freeze",
            "--plan",
            "user_data/research/study/protocol.json",
            "--datadir",
            "user_data/data/walkforward/futures",
            "--output",
            "user_data/research/study/data_manifest.json",
        ]
        status = subprocess.run(freeze, cwd=ROOT).returncode
    if args.action == "stress-download" and status == 0:
        rest = [
            *prefix,
            "research",
            "download-data",
            "--config",
            "user_data/research/stress/download_config.json",
            "--datadir",
            "user_data/data/stress_rest",
            "--timerange",
            "20240101-20260101",
            "--timeframes",
            "1h",
            "15m",
            "--trading-mode",
            "futures",
            "--candle-types",
            "futures",
        ]
        status = run_logged(rest, "stress-download-rest")
        if status == 0:
            prepare = [
                *prefix,
                "--entrypoint",
                "python",
                "research",
                "scripts/stress.py",
                "prepare",
            ]
            status = subprocess.run(prepare, cwd=ROOT).returncode
    if args.action == "backtest" and status == 0:
        if not latest.exists() or latest.read_text() == previous_result:
            raise SystemExit("Backtest produced no new result artifact")
        report = [*prefix, "--entrypoint", "python", "research", "scripts/summarize_backtest.py"]
        status = subprocess.run(report, cwd=ROOT).returncode
    if args.action == "lookahead" and status == 0:
        import csv

        export = ROOT / lookahead_file
        with export.open() as handle:
            rows = list(csv.DictReader(handle))
        result = rows[-1]
        print("Lookahead:", result)
        if result["has_bias"].lower() != "false" or int(result["total_signals"]) < 5:
            status = 1
        (ROOT / "user_data/research/lookahead.csv").write_text(export.read_text())
    if args.action == "study" and status == 0:
        for module in ["mark_to_market", "validate_study", "study_report"]:
            step = [*prefix, "--entrypoint", "python", "research", f"scripts/{module}.py"]
            status = subprocess.run(step, cwd=ROOT).returncode
            if status:
                break
    raise SystemExit(status)


if __name__ == "__main__":
    main()

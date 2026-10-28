"""Predeclared return-focused experiment using known 2023/2024 development data only."""

import copy
import csv
import json
import math
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from scripts.analyze_result import load_result
from scripts.mark_to_market import equity_from_fills, equity_metrics
from scripts.study import ROOT, Study, canonical, native_timerange, sha

OUTPUT = ROOT / "user_data/research/return_revision_20261028"
RESULTS = ROOT / "user_data/backtest_results/return_revision_20261028"
VARIANTS = {
    "baseline": {"long_only": False, "adx_filter": False},
    "long_only": {"long_only": True, "adx_filter": False},
    "adx_only": {"long_only": False, "adx_filter": True},
    "long_adx": {"long_only": True, "adx_filter": True},
}
PERIODS = {
    "selection": "20230101-20240101",
    "confirmation": "20240101-20250101",
    "continuous": "20230101-20250101",
}


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def freeze(path: Path, value) -> None:
    if path.exists() and json.loads(path.read_text()) != value:
        raise ValueError(f"Frozen revision artifact changed: {path.name}; create a new study")
    write_json(path, value)


def guard_period(period: str) -> None:
    start, end = period.split("-")
    if not "20230101" <= start < end <= "20250101":
        raise ValueError("Return study may only evaluate 2023/2024 development data")


def qualifies(candidate: dict, baseline: dict) -> bool:
    for key in ("actual_start", "actual_end", "starting_balance"):
        if candidate[key] != baseline[key]:
            raise ValueError(f"Unfair comparison: {key} differs")
    cagr = candidate["cagr"]
    dd = candidate["mark_to_market"]["max_drawdown"]
    limit = min(0.10, baseline["mark_to_market"]["max_drawdown"] + 0.02)
    return (
        candidate["integrity"]["passed"]
        and candidate["stats"]["trades"] >= 10
        and math.isfinite(cagr)
        and math.isfinite(dd)
        and cagr > max(0.0, baseline["cagr"]) + 1e-9
        and dd <= limit
    )


def choose(runs: dict) -> str | None:
    eligible = [
        name for name, run in runs.items()
        if name != "baseline" and qualifies(run, runs["baseline"])
    ]
    return min(
        eligible,
        key=lambda name: (-runs[name]["cagr"], runs[name]["mark_to_market"]["max_drawdown"], name),
        default=None,
    )


def run_variant(study: Study, name: str, phase: str) -> dict:
    period = PERIODS[phase]
    guard_period(period)
    strategy = "Donchian4HStrategy" if name == "baseline" else "DonchianReturnStrategy"
    params = {**study.base, **VARIANTS[name]}
    run = study.run(params, period, f"return_revision_{phase}", strategy=strategy)
    result, _ = load_result(ROOT / run["source"], strategy)
    expected_start, expected_end = period.split("-")
    if pd.Timestamp(run["actual_start"], tz="UTC") != pd.to_datetime(expected_start, utc=True):
        raise ValueError("Native warmup unexpectedly shortened the comparison period")
    expected_last = pd.to_datetime(expected_end, utc=True) - pd.Timedelta(hours=4)
    if pd.Timestamp(run["actual_end"], tz="UTC") != expected_last:
        raise ValueError("Native result does not cover the last closed 4h candle")
    equity = equity_from_fills(result, study.frames["mark"], study.frames["funding"])
    run["mark_to_market"] = equity_metrics(equity)
    run["mark_to_market"]["selection_use"] = (
        "Drawdown eligibility for this NEW experiment only; frozen original study unchanged"
    )
    run["source_sha256"] = sha(ROOT / run["source"])
    directory = RESULTS / run["run_id"]
    equity.to_csv(directory / "mark_equity_hourly.csv")
    write_json(directory / "summary.json", run)
    print(f"{phase}/{name}: return={run['total_return']:.4%}, "
          f"CAGR={run['cagr']:.4%}, markDD={run['mark_to_market']['max_drawdown']:.4%}, "
          f"trades={run['stats']['trades']}", flush=True)
    return run


def check_lookahead(config_path: Path, identity: dict) -> dict:
    saved = OUTPUT / "lookahead_validation.json"
    if saved.exists():
        result = json.loads(saved.read_text())
        if result["identity"] != identity or sha(ROOT / result["export"]) != result["sha256"]:
            raise ValueError("Lookahead evidence changed")
        return result
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    export = RESULTS / f"lookahead-{stamp}.csv"
    log = RESULTS / f"lookahead-{stamp}.log"
    command = [
        "freqtrade", "lookahead-analysis", "--config", str(config_path),
        "--datadir", str(ROOT / "user_data/data/walkforward"),
        "--strategy", "DonchianReturnStrategy", "--timerange",
        native_timerange(PERIODS["confirmation"]), "--minimum-trade-amount", "5",
        "--targeted-trade-amount", "20", "--lookahead-analysis-exportfilename", str(export),
    ]
    with log.open("w") as handle:
        status = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT).returncode
    if status or " - ERROR - " in log.read_text() or not export.exists():
        raise RuntimeError(f"Native lookahead failed; see {log.relative_to(ROOT)}")
    with export.open() as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise ValueError("Expected one native lookahead result")
    row = rows[0]
    passed = row["has_bias"].lower() == "false" and int(row["total_signals"]) >= 5
    result = {
        "passed": passed, "native": row, "identity": identity,
        "export": str(export.relative_to(ROOT)), "sha256": sha(export),
        "note": "Native lookahead plus unit prefix-invariance checks; not proof of profitability",
    }
    write_json(saved, result)
    return result


def protected_hashes() -> dict:
    paths = list((ROOT / "user_data/research/study").glob("*"))
    paths += [ROOT / "user_data/config.json", ROOT / "user_data/strategies/Donchian4HStrategy.py"]
    return {str(p.relative_to(ROOT)): sha(p) for p in paths if p.is_file()}


def report(summary: dict) -> str:
    lines = [
        "# 報酬率改善實驗（2026-10-28）", "",
        "固定每筆 0.5% 預算風險、最高 2 倍槓桿，測試只做多及 ADX(14) ≥ 25。",
        "2023 排序候選；2024 為已知開發資料確認。兩者都不是全新樣本外結果。",
        "2025／2026 未用於本次比較、挑選或調參。", "",
        "資格：至少 10 筆、淨年化為正且高於基準，時價回撤 ≤ 10% 且 ≤ 基準 + 2 個百分點。",
        "選擇完成後不遞補候選；2024 或連續期間未通過就保留原策略。", "",
        "| 階段 | 版本 | 淨總報酬 | 年化 | 時價最大回撤 | 交易數 | 勝率 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for phase, runs in summary["runs"].items():
        for name, run in runs.items():
            win = run["stats"]["win_rate"]
            win_text = f"{win:.2%}" if win is not None else "—"
            lines.append(
                f"| {phase} | {name} | {run['total_return']:.2%} | {run['cagr']:.2%} | "
                f"{run['mark_to_market']['max_drawdown']:.2%} | "
                f"{run['stats']['trades']} | {win_text} |"
            )
    lines += ["", f"開發期選擇：{summary['selected'] or '無'}。",
              f"研究驗收通過：{summary['accepted']}。{summary['reason']}", "",
              "各單年回測從 10,000 USDT 重設；continuous 是同一帳戶連續交易、無年度重設。",
              "每邊 5 bps taker + 5 bps 滑價現金代理，含實際歷史 Funding。",
              "回撤按每小時 Mark Price 抽樣，可能漏掉小時內更深回撤；未模擬真實撮合與清算費。",
              "只有少量候選與交易，結果仍可能過度擬合，不能據此承諾未來報酬或年化 10%。",
              "原策略、原研究選擇、PVE 服務未替換；通過的候選也需要後續模擬交易驗證。", ""]
    return "\n".join(lines)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    before = protected_hashes()
    study = Study(results_root=RESULTS)
    source = ROOT / "user_data/strategies/DonchianReturnStrategy.py"
    protocol = {
        "user_request": "Improve net return; bounded strategy revision without increasing risk",
        "created_on": "2026-10-28", "variants": VARIANTS, "periods": PERIODS,
        "fixed_params": study.config["research"], "adx_period": 14, "adx_threshold": 25,
        "minimum_trades": 10, "max_mark_dd": 0.10, "max_extra_mark_dd": 0.02,
        "selection": "2023 positive net CAGR strictly above baseline; rank CAGR, DD, name",
        "confirmation": "Selected candidate only vs baseline in 2024; no runner-up on failure",
        "continuous": "Additional acceptance gate on 2023-2024 continuous account; no reselection",
        "data_status": "Known development, not pristine OOS; 2025/2026 excluded",
        "identity": study.identity, "strategy_sha256": sha(source),
        "runner_sha256": sha(Path(__file__)),
        "supporting_code": {name: sha(ROOT / "scripts" / name) for name in (
            "study.py", "analyze_result.py", "summarize_backtest.py", "mark_to_market.py"
        )},
    }
    freeze(OUTPUT / "protocol.json", protocol)
    study.identity = {**study.identity, "return_revision": sha(source),
                      "return_protocol": sha(OUTPUT / "protocol.json"),
                      "return_runner": sha(Path(__file__))}
    (OUTPUT / "strategy_snapshot.py").write_bytes(source.read_bytes())
    (OUTPUT / "runner_snapshot.py").write_bytes(Path(__file__).read_bytes())
    # Verify current public market precision/limits against the frozen native rules.
    subprocess.run(["python", "scripts/snapshot_market.py"], check=True)
    selection = {name: run_variant(study, name, "selection") for name in VARIANTS}
    selected = choose(selection)
    freeze(OUTPUT / "selection.json", {
        "selected": selected, "protocol_sha256": sha(OUTPUT / "protocol.json"),
        "selection_runs": {name: run["run_id"] for name, run in selection.items()},
    })
    summary = {
        "selected": selected, "accepted": False, "runs": {"selection": selection},
        "reason": "沒有候選通過 2023 開發期門檻，保留原策略。",
        "identity": study.identity, "deployment_changed": False,
    }
    if selected:
        confirmation = {name: run_variant(study, name, "confirmation")
                        for name in ("baseline", selected)}
        summary["runs"]["confirmation"] = confirmation
        summary["reason"] = "候選未通過 2024 開發確認，保留原策略。"
        if qualifies(confirmation[selected], confirmation["baseline"]):
            continuous = {name: run_variant(study, name, "continuous")
                          for name in ("baseline", selected)}
            summary["runs"]["continuous"] = continuous
            summary["reason"] = "候選未通過連續帳戶比較，保留原策略。"
            if qualifies(continuous[selected], continuous["baseline"]):
                candidate = copy.deepcopy(study.config)
                candidate["strategy"] = "DonchianReturnStrategy"
                candidate["research"].update(VARIANTS[selected])
                candidate_path = OUTPUT / "candidate_config.json"
                freeze(candidate_path, candidate)
                lookahead = check_lookahead(candidate_path, study.identity)
                summary["lookahead"] = lookahead
                summary["accepted"] = lookahead["passed"]
                summary["reason"] = (
                    "候選通過開發期比較與前視檢查，可供後續獨立模擬驗證。"
                    if lookahead["passed"] else "前視檢查未通過，保留原策略。"
                )
    if before != protected_hashes():
        raise ValueError("Original frozen study/config/strategy changed")
    summary["protected_artifacts_unchanged"] = True
    summary["result_identity"] = canonical(study.identity)
    write_json(OUTPUT / "summary.json", summary)
    (OUTPUT / "REPORT.md").write_text(report(summary))
    print(report(summary))


if __name__ == "__main__":
    main()

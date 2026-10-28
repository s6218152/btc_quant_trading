"""Fixed accepted candidate and baseline through 2026-10-27 Taipei; no reselection."""

import argparse
import copy
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.analyze_result import load_result, summarize
from scripts.audit_data import FILES, audit_frame
from scripts.mark_to_market import equity_from_fills, equity_metrics
from scripts.study import ROOT, Study, sha
from scripts.summarize_backtest import reconcile

OUT = ROOT / "user_data/research/candidate_inception_20261027"
DATA = ROOT / "user_data/data/candidate_inception_20261027"
RESULTS = ROOT / "user_data/backtest_results/candidate_inception_20261027"
PREVIOUS = ROOT / "user_data/research/inception_20261021"
REVISION = ROOT / "user_data/research/return_revision_20261028"
DAY = "2026-10-27"


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def freeze(path: Path, value) -> None:
    if path.exists() and json.loads(path.read_text()) != value:
        raise ValueError(f"Frozen evaluation changed: {path.name}")
    write_json(path, value)


def taipei_end_exclusive(day: str) -> pd.Timestamp:
    local = pd.Timestamp(day, tz="Asia/Taipei")
    if local != local.normalize():
        raise ValueError("Expected a local calendar date")
    return (local + pd.Timedelta(days=1)).tz_convert("UTC")


def trim_frame(frame: pd.DataFrame, key: str, end: pd.Timestamp) -> pd.DataFrame:
    cutoff = end.floor("1D") if key == "1d" else end
    return frame.loc[frame["date"] < cutoff].copy().reset_index(drop=True)


def assert_unchanged_prefix(original: pd.DataFrame, extended: pd.DataFrame) -> None:
    if original["date"].duplicated().any() or extended["date"].duplicated().any():
        raise ValueError("Duplicate historical timestamps")
    old = original.set_index("date")
    new = extended.set_index("date").reindex(old.index)
    try:
        pd.testing.assert_frame_equal(old, new, check_exact=True, check_freq=False)
    except AssertionError as error:
        raise ValueError("Previously audited historical prefix changed") from error


def setup() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    (DATA / "futures").mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    study = Study()  # Verify the original frozen data, base code and environment.
    accepted = json.loads((REVISION / "summary.json").read_text())
    validation = json.loads((REVISION / "validation.json").read_text())
    revision = json.loads((REVISION / "protocol.json").read_text())
    if not accepted["accepted"] or accepted["selected"] != "long_only":
        raise ValueError("Expected the previously accepted long-only candidate")
    for filename, field in (("summary.json", "summary_sha256"),
                            ("candidate_config.json", "candidate_config_sha256"),
                            ("protocol.json", "protocol_sha256")):
        if sha(REVISION / filename) != validation[field]:
            raise ValueError(f"Accepted revision evidence changed: {filename}")
    strategy = ROOT / "user_data/strategies/DonchianReturnStrategy.py"
    if sha(strategy) != revision["strategy_sha256"]:
        raise ValueError("Accepted strategy source changed")
    source_manifest = json.loads((PREVIOUS / "data_manifest.json").read_text())
    if source_manifest["status"] != "PASS":
        raise ValueError("Historical seed data were not audited")
    for info in source_manifest["files"].values():
        if sha(ROOT / info["file"]) != info["sha256"]:
            raise ValueError("Historical seed data changed")
    candidate = json.loads((REVISION / "candidate_config.json").read_text())
    if not candidate["dry_run"] or any(candidate["exchange"].get(k)
                                       for k in ("key", "secret", "password", "uid")):
        raise ValueError("Fixed evaluation requires credential-free simulation")
    candidate["exchange"]["only_from_ccxt"] = True
    baseline = copy.deepcopy(study.config)
    baseline["exchange"]["only_from_ccxt"] = True
    freeze(OUT / "candidate_config.json", candidate)
    freeze(OUT / "baseline_config.json", baseline)
    plan = {
        "authorization": "User requested candidate inception through yesterday, on 2026-10-28",
        "mode": "Fixed descriptive evaluation; 2025/2026 opened, no tuning or reselection",
        "requested_start": "2019-09-09T00:00:00Z", "through_local_date": DAY,
        "timezone": "Asia/Taipei", "end_exclusive": taipei_end_exclusive(DAY).isoformat(),
        "verified_price_start": "2019-09-25T00:00:00+00:00", "warmup_daily_candles": 1000,
        "selected_candidate": accepted["selected"], "original_identity": study.identity,
        "candidate_source_sha256": sha(strategy),
        "candidate_config_sha256": sha(OUT / "candidate_config.json"),
        "baseline_config_sha256": sha(OUT / "baseline_config.json"),
        "accepted_revision_sha256": sha(REVISION / "summary.json"),
        "seed_manifest_sha256": sha(PREVIOUS / "data_manifest.json"),
        "runner_sha256": sha(Path(__file__)),
        "supporting_code": {name: sha(ROOT / "scripts" / name) for name in (
            "study.py", "audit_data.py", "analyze_result.py", "summarize_backtest.py",
            "mark_to_market.py", "snapshot_market.py"
        )},
    }
    freeze(OUT / "protocol.json", plan)
    for filename in ("Donchian4HStrategy.py", "DonchianReturnStrategy.py"):
        shutil.copy2(ROOT / "user_data/strategies" / filename, OUT / filename)
    shutil.copy2(Path(__file__), OUT / "runner_snapshot.py")
    shutil.copy2(PREVIOUS / "price_discrepancy.json", OUT / "inherited_price_discrepancy.json")
    return plan


def download(plan: dict) -> None:
    source_manifest = json.loads((PREVIOUS / "data_manifest.json").read_text())
    for info in source_manifest["files"].values():
        source = ROOT / info["file"]
        destination = DATA / "futures" / source.name
        if not destination.exists():
            shutil.copy2(source, destination)
    previous_end = pd.Timestamp(source_manifest["evaluation_end_exclusive"])
    start = previous_end.floor("1D") - pd.Timedelta(days=1)
    end = pd.Timestamp(plan["end_exclusive"])
    command = [
        "freqtrade", "download-data", "--config", str(OUT / "candidate_config.json"),
        "--datadir", str(DATA), "--trading-mode", "futures", "--timeframes", "4h", "1d",
        "--timerange", f"{int(start.timestamp())}-{int(end.timestamp())}",
    ]
    log = OUT / "download.log"
    with log.open("w") as handle:
        process = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT)
    if process.returncode or " - ERROR - " in log.read_text():
        raise RuntimeError(f"Native download failed; inspect {log.relative_to(ROOT)}")


def audit(plan: dict) -> tuple[dict, dict]:
    end = pd.Timestamp(plan["end_exclusive"])
    source_manifest = json.loads((PREVIOUS / "data_manifest.json").read_text())
    manifest = {"files": {}, "errors": [], "end_exclusive": end.isoformat(),
                "inherited_quality_warning": "2019-09-24 discrepancy; seed begins 2019-09-25"}
    frames = {}
    for key, (filename, interval) in FILES.items():
        path = DATA / "futures" / filename
        original = pd.read_feather(ROOT / source_manifest["files"][key]["file"])
        extended = pd.read_feather(path)
        assert_unchanged_prefix(original, extended)
        frame = trim_frame(extended, key, end)
        if frame["date"].min() != original["date"].min():
            raise ValueError("Extended data changed the audited warmup start")
        cutoff = end.floor("1D") if key == "1d" else end
        info = audit_frame(frame, start=original["date"].min(), end=cutoff,
                           interval=interval, funding=key == "funding")
        frame.to_feather(path)
        info.update(file=str(path.relative_to(ROOT)), sha256=sha(path), seed_unchanged=True)
        manifest["files"][key] = info
        manifest["errors"].extend(f"{key}: {error}" for error in info["errors"])
        frames[key] = frame
    daily = frames["1d"].set_index("date")
    columns = ["open", "high", "low", "close"]
    aggregate = frames["4h"].set_index("date").resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).reindex(daily.index)
    if not np.isclose(aggregate[columns], daily[columns], rtol=1e-8, atol=1e-6).all():
        manifest["errors"].append("Completed daily OHLC disagrees with 4h prices")
    start = frames["1d"]["date"].iloc[plan["warmup_daily_candles"]]
    for key in ("4h", "mark", "funding"):
        if frames[key]["date"].min() > start:
            manifest["errors"].append(f"{key} does not cover the evaluation period")
    manifest.update(evaluation_start=start.isoformat(),
                    status="FAIL" if manifest["errors"] else "PASS")
    if manifest["errors"]:
        write_json(OUT / "data_manifest.failed.json", manifest)
        raise ValueError(f"Extended data audit failed: {manifest['errors']}")
    freeze(OUT / "data_manifest.json", manifest)
    return frames, manifest


def run_one(name: str, plan: dict, frames: dict, manifest: dict) -> dict:
    directory = RESULTS / name
    directory.mkdir(exist_ok=True)
    config_path = OUT / f"{name}_config.json"
    strategy = "DonchianReturnStrategy" if name == "candidate" else "Donchian4HStrategy"
    start, end = pd.Timestamp(manifest["evaluation_start"]), pd.Timestamp(plan["end_exclusive"])
    identity = {"protocol": sha(OUT / "protocol.json"), "data": sha(OUT / "data_manifest.json"),
                "config": sha(config_path)}
    receipt = directory / "receipt.json"
    if receipt.exists():
        recorded = json.loads(receipt.read_text())
        archive = ROOT / recorded["source"]
        if recorded["identity"] != identity or sha(archive) != recorded["sha256"]:
            raise ValueError("Cached native result changed")
    else:
        command = [
            "freqtrade", "backtesting", "--config", str(config_path), "--datadir", str(DATA),
            "--timerange", f"{int(start.timestamp())}-{int(end.timestamp()) - 1}",
            "--strategy", strategy, "--enable-protections", "--cache", "none",
            "--export", "trades", "--backtest-directory", str(directory),
        ]
        log = directory / "native.log"
        with log.open("w") as handle:
            process = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT)
        if process.returncode or " - ERROR - " in log.read_text():
            raise RuntimeError(f"Native backtest failed; inspect {log.relative_to(ROOT)}")
        latest = json.loads((directory / ".last_result.json").read_text())
        archive = directory / latest["latest_backtest"]
        write_json(receipt, {"identity": identity, "source": str(archive.relative_to(ROOT)),
                             "sha256": sha(archive)})
    result, config = load_result(archive, strategy)
    if pd.Timestamp(result["backtest_start"], tz="UTC") != start:
        raise ValueError("Actual evaluation start changed")
    if pd.Timestamp(result["backtest_end"], tz="UTC") != end - pd.Timedelta(hours=4):
        raise ValueError("Actual evaluation end changed")
    trades = pd.DataFrame(result["trades"])
    if not trades.empty:
        if ((pd.to_datetime(trades["open_date"], utc=True) < start).any()
                or (pd.to_datetime(trades["close_date"], utc=True) >= end).any()):
            raise ValueError("Trade outside fixed evaluation window")
        if name == "candidate" and trades["is_short"].any():
            raise ValueError("Long-only candidate produced a short trade")
    integrity, audited = reconcile(trades, frames["4h"], frames["funding"], frames["mark"],
                                   config, result["starting_balance"])
    if not integrity["passed"]:
        raise ValueError(f"Accounting/risk check failed: {integrity}")
    audited.to_csv(directory / "trades.csv", index=False)
    equity = equity_from_fills(result, frames["mark"], frames["funding"])
    equity.to_csv(directory / "mark_equity_hourly.csv")
    summary = summarize(result, config)
    summary.update(integrity=integrity, mark_to_market=equity_metrics(equity),
                   source=str(archive.relative_to(ROOT)), source_sha256=sha(archive))
    write_json(directory / "summary.json", summary)
    print(f"{name}: net={summary['total_return']:.4%}, CAGR={summary['cagr']:.4%}, "
          f"trades={summary['stats']['trades']}", flush=True)
    return summary


def report(summary: dict, plan: dict) -> str:
    candidate = summary["candidate"]
    lines = [
        "# 上市至 2026-10-27：固定只做多版本評估", "",
        "台北日期 2026-10-27 已結束；資料截止（不含）為 UTC 2026-10-27 16:00。",
        f"策略原生績效期間（UTC）：{candidate['actual_start']} 至 {candidate['actual_end']}。",
        "最後 4h K 線開盤為台北 10/27 20:00，收盤為 10/28 00:00；",
        "引擎期末強平使用最後 K 線的時間戳，因此最後原生績效時間是台北 10/27 20:00。", "",
        "| 指標 | 原策略 | 只做多版本 |", "|---|---:|---:|",
    ]
    for title, key in (("累積淨報酬", "total_return"), ("交易期間年化", "cagr")):
        lines.append(f"| {title} | {summary['baseline'][key]:.2%} | {candidate[key]:.2%} |")
    for title, key in (("每小時時價最大回撤", "max_drawdown"),):
        lines.append(f"| {title} | {summary['baseline']['mark_to_market'][key]:.2%} | "
                     f"{candidate['mark_to_market'][key]:.2%} |")
    for title, key, spec in (("交易次數", "trades", "d"), ("勝率", "win_rate", ".2%")):
        lines.append(f"| {title} | {summary['baseline']['stats'][key]:{spec}} | "
                     f"{candidate['stats'][key]:{spec}} |")
    lines += ["", f"候選本金 {candidate['starting_balance']:,.2f} → "
              f"{candidate['final_balance']:,.2f} USDT。", "",
              "## 期間及研究限制", "",
              "請求上市起點為 2019-09-09。原始 2019-09-24 多週期價格差異已保留，",
              f"使用 {plan['verified_price_start']} 起的已核對資料，保留 1000 根日線暖機。",
              "因此真正可交易期間自 2022-06-21 起，不能聲稱從上市首日便開始交易。",
              "正式交易期間 Funding／Mark 網格完整，未以零補缺；原有已稽核歷史逐列未變。",
              "固定 2023／2024 開發期選出的只做多設定；此完整期間包含開發資料及已觀察的",
              "2025／2026，不是新的獨立樣本外績效，未依結果調參、重選策略或增加槓桿。", "",
              "每邊 5 bps taker + 5 bps 滑價現金代理，含實際 Funding。",
              "每小時 Mark 回撤可能漏掉小時內極端值；未模擬真實撮合與清算費。",
              "PVE 服務與 GitHub 未變更。", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["download", "run"])
    parser.add_argument("--allow-final-holdout", action="store_true", required=True)
    args = parser.parse_args()
    plan = setup()
    if args.action == "download":
        download(plan)
        print("Native tail download complete; pending full audit")
        return
    frames, manifest = audit(plan)
    subprocess.run(["python", "scripts/snapshot_market.py"], check=True)
    summary = {name: run_one(name, plan, frames, manifest) for name in ("candidate", "baseline")}
    summary.update(protocol_sha256=sha(OUT / "protocol.json"),
                   data_manifest_sha256=sha(OUT / "data_manifest.json"),
                   selection_use="none; fixed evaluation only")
    # Recheck frozen source data and accepted candidate after evaluation.
    setup()
    write_json(OUT / "summary.json", summary)
    (OUT / "REPORT.md").write_text(report(summary, plan))
    print(report(summary, plan))


if __name__ == "__main__":
    main()

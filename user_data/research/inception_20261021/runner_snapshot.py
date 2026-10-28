"""Explicit, isolated final evaluation requested on 2026-10-21; never tune parameters."""

import argparse
import copy
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.analyze_result import load_result, summarize
from scripts.audit_data import FILES, audit_frame
from scripts.mark_to_market import equity_from_fills, equity_metrics
from scripts.study import ROOT, Study, sha
from scripts.summarize_backtest import reconcile
from user_data.strategies.Donchian4HStrategy import Donchian4HStrategy

OUT = ROOT / "user_data/research/inception_20261021"
DATA = ROOT / "user_data/data/inception_20261021"
RESULTS = ROOT / "user_data/backtest_results/inception_20261021"


def consistent_prefix_start(start: pd.Timestamp, bad_days: pd.DatetimeIndex) -> pd.Timestamp:
    if len(bad_days) == 0:
        return start
    # Only the newly obtained 2019 warmup prefix may be excluded. Never drop
    # performance-period inconsistencies to improve a result.
    if bad_days.max() >= pd.Timestamp("2020-01-01", tz="UTC"):
        raise ValueError("Inconsistent prices outside the 2019 warmup prefix")
    return max(start, bad_days.max() + pd.Timedelta(days=1))


def setup() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    existing = OUT / "protocol.json"
    if existing.exists():
        plan = json.loads(existing.read_text())
        if plan["strategy_sha256"] != sha(ROOT / "user_data/strategies/Donchian4HStrategy.py"):
            raise ValueError("Fixed evaluation strategy changed")
        if plan["config_sha256"] != sha(OUT / "config.json"):
            raise ValueError("Fixed evaluation config changed")
        return plan
    study = Study()  # Verify all frozen inputs before extending copies of the data.
    config = copy.deepcopy(study.config)
    config["exchange"]["only_from_ccxt"] = True
    (OUT / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    plan = {
        "authorization": "User explicitly requested inception through today on 2026-10-21",
        "final_holdout": "2026 opened for fixed-model evaluation only; no selection or tuning",
        "requested_start": "2019-09-09T00:00:00Z",
        "end_exclusive": pd.Timestamp(datetime.now(UTC)).floor("4h").isoformat(),
        "strategy_sha256": study.identity["strategy"],
        "base_config_sha256": study.identity["base_config"],
        "config_sha256": sha(OUT / "config.json"),
        "source_identity": study.identity,
        "params": study.base,
        "warmup_daily_candles": Donchian4HStrategy.startup_candle_count,
        "environment": study.identity["environment"],
    }
    existing.write_text(json.dumps(plan, indent=2) + "\n")
    return plan


def download() -> None:
    plan = setup()
    (DATA / "futures").mkdir(parents=True, exist_ok=True)
    frozen = json.loads((ROOT / "user_data/research/study/data_manifest.json").read_text())
    for info in frozen["files"].values():
        src = ROOT / info["file"]
        if sha(src) != info["sha256"]:
            raise ValueError("Frozen source data changed")
        dest = DATA / "futures" / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
    common = ["freqtrade", "download-data", "--config", str(OUT / "config.json"),
              "--datadir", str(DATA), "--trading-mode", "futures"]
    # Extend copies only. No 2026 data is written into the old sealed study.
    commands = [
        [*common, "--prepend", "--timerange", "20190901-20200101",
         "--timeframes", "4h", "1d", "--candle-types", "futures"],
        [*common, "--timerange",
         f"{int(pd.Timestamp('2026-01-01', tz='UTC').timestamp())}-"
         f"{int(pd.Timestamp(plan['end_exclusive']).timestamp())}",
         "--timeframes", "4h", "1d"],
    ]
    for index, command in enumerate(commands):
        log = OUT / f"download-{index}.log"
        with log.open("w") as handle:
            result = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT)
        if result.returncode or " - ERROR - " in log.read_text():
            raise RuntimeError(f"Native download failed; inspect {log.relative_to(ROOT)}")
    print("Native download complete; raw data will be trimmed and audited before evaluation")


def audit(plan: dict) -> tuple[dict, dict]:
    end = pd.Timestamp(plan["end_exclusive"])
    frames = {k: pd.read_feather(DATA / "futures" / name) for k, (name, _) in FILES.items()}
    price_start = max(pd.Timestamp(plan["requested_start"]),
                      frames["4h"]["date"].min().ceil("1D"), frames["1d"]["date"].min())
    manifest = {"files": {}, "errors": [], "warnings": []}
    reference = frames["1d"].set_index("date")
    reference = reference.loc[
        (reference.index >= price_start) & (reference.index < end.floor("1D"))
    ]
    aggregate = frames["4h"].set_index("date").resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).reindex(reference.index)
    mismatches = ~np.isclose(aggregate, reference[list(aggregate)], rtol=1e-8, atol=1e-6)
    bad_days = reference.index[mismatches.any(axis=1)]
    discrepancy_path = OUT / "price_discrepancy.json"
    if len(bad_days):
        discrepancy = {
            "dates": [str(d) for d in bad_days],
            "mismatched_cells": {c: int(mismatches[:, i].sum())
                                 for i, c in enumerate(aggregate.columns)},
            "policy": "Exclude only the initial 2019 warmup prefix; do not repair vendor prices",
            "source_sha256": {k: sha(DATA / "futures" / FILES[k][0]) for k in ("4h", "1d")},
            "hourly_probe": "Native REST probe of 2019-09-24 also disagrees across timeframes",
        }
        # Preserve the downloaded records containing the discrepancy.
        raw = DATA / "pre_quality_check"
        raw.mkdir(exist_ok=True)
        for key in ("4h", "1d"):
            target = raw / FILES[key][0]
            if not target.exists():
                shutil.copy2(DATA / "futures" / FILES[key][0], target)
        discrepancy_path.write_text(json.dumps(discrepancy, indent=2) + "\n")
        price_start = consistent_prefix_start(price_start, bad_days)
    if discrepancy_path.exists():
        manifest["warnings"].append(json.loads(discrepancy_path.read_text()))
    plan["verified_price_start"] = price_start.isoformat()
    (OUT / "protocol.json").write_text(json.dumps(plan, indent=2) + "\n")
    for key, (filename, interval) in FILES.items():
        start = price_start if key in {"4h", "1d"} else frames[key]["date"].min()
        stop = end.floor("1D") if key == "1d" else end
        frame = frames[key]
        frame = frame.loc[(frame["date"] >= start) & (frame["date"] < stop)].reset_index(drop=True)
        path = DATA / "futures" / filename
        frame.to_feather(path)
        info = audit_frame(
            frame, start=start, end=stop, interval=interval, funding=key == "funding"
        )
        info.update(file=str(path.relative_to(ROOT)), sha256=sha(path))
        manifest["files"][key] = info
        manifest["errors"].extend(f"{key}: {e}" for e in info["errors"])
        frames[key] = frame
    daily = frames["4h"].set_index("date").resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    )
    daily = daily.loc[frames["1d"]["date"]]
    other = frames["1d"].set_index("date")
    if not np.isclose(daily, other[list(daily)], rtol=1e-8, atol=1e-6).all():
        manifest["errors"].append("Daily prices disagree with aggregated 4h prices")
    start = frames["1d"]["date"].iloc[plan["warmup_daily_candles"]]
    for key in ("mark", "funding"):
        if frames[key]["date"].min() > start:
            manifest["errors"].append(f"{key} does not cover the trading period")
    manifest.update(status="FAIL" if manifest["errors"] else "PASS",
                    evaluation_start=start.isoformat(), evaluation_end_exclusive=end.isoformat(),
                    note="Pre-2020 auxiliary gaps are outside possible trades; never zero-filled")
    (OUT / "data_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if manifest["errors"]:
        raise ValueError(f"Data audit failed: {manifest['errors']}")
    return frames, manifest


def run() -> None:
    plan = setup()
    shutil.copy2(Path(__file__), OUT / "runner_snapshot.py")
    if sha(OUT / "config.json") != plan["config_sha256"]:
        raise ValueError("Fixed evaluation config changed")
    frames, manifest = audit(plan)
    start, end = map(pd.Timestamp, (manifest["evaluation_start"], plan["end_exclusive"]))
    timerange = f"{int(start.timestamp())}-{int(end.timestamp()) - 1}"
    RESULTS.mkdir(parents=True, exist_ok=True)
    subprocess.run(["python", "scripts/snapshot_market.py"], check=True)
    command = ["freqtrade", "backtesting", "--config", str(OUT / "config.json"),
               "--datadir", str(DATA), "--timerange", timerange,
               "--strategy", "Donchian4HStrategy", "--enable-protections", "--cache", "none",
               "--export", "trades", "--backtest-directory", str(RESULTS)]
    log = OUT / "native.log"
    with log.open("w") as handle:
        process = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT)
    if process.returncode or " - ERROR - " in log.read_text():
        raise RuntimeError(f"Native backtest failed; see {log.relative_to(ROOT)}")
    archive = RESULTS / json.loads((RESULTS / ".last_result.json").read_text())["latest_backtest"]
    result, config = load_result(archive)
    trades = pd.DataFrame(result["trades"])
    if not trades.empty and ((pd.to_datetime(trades["open_date"], utc=True) < start).any()
                             or (pd.to_datetime(trades["close_date"], utc=True) >= end).any()):
        raise ValueError("Trades outside evaluation bounds")
    integrity, audited = reconcile(trades, frames["4h"], frames["funding"], frames["mark"],
                                   config, result["starting_balance"])
    if not integrity["passed"]:
        raise ValueError(f"Accounting/risk reconciliation failed: {integrity}")
    audited.to_csv(RESULTS / "trades.csv", index=False)
    equity = equity_from_fills(result, frames["mark"], frames["funding"])
    equity.to_csv(RESULTS / "mark_equity.csv")
    summary = summarize(result, config)
    summary.update(integrity=integrity, mark_to_market=equity_metrics(equity),
                   source=str(archive.relative_to(ROOT)), source_sha256=sha(archive),
                   protocol_sha256=sha(OUT / "protocol.json"),
                   runner_sha256=sha(Path(__file__)))
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    report = (
        "# 上市至 2026-10-21：固定策略評估\n\n"
        f"請求起點：{plan['requested_start']}；資料截止（UTC，不含）：{plan['end_exclusive']}。\n"
        f"原生引擎績效期間（UTC）：{summary['actual_start']} 至 {summary['actual_end']}。\n\n"
        f"- 累積淨報酬：{summary['total_return']:.4%}\n"
        f"- 起始／最終資金：{summary['starting_balance']:,.2f}／"
        f"{summary['final_balance']:,.2f} USDT\n"
        f"- 完整交易：{summary['stats']['trades']} 筆；多單 {summary['long']['trades']}、"
        f"空單 {summary['short']['trades']}；勝率 {summary['stats']['win_rate']:.2%}\n"
        f"- 正式交易期間年化報酬：{summary['cagr']:.2%}\n"
        f"- 每小時 Mark Price 淨值最大回撤："
        f"{summary['mark_to_market']['max_drawdown']:.2%}\n\n"
        "## 期間與資料限制\n\n"
        "2019-09-24 的交易所 1D／4H／1H 價格存在不一致，包含日線收盤價；"
        "原始差異已保留，沒有修補或補造 K 線。\n"
        f"只以 {plan['verified_price_start']} 起的一致價格資料暖機；策略維持 1000 根日線"
        "暖機，因此正式績效自 2022-06-21 開始，不能稱作上市第一日起就交易的績效。\n"
        "2020 前缺少的 Mark／Funding 只落在不可交易的暖機區，沒有填零；"
        "正式交易期間價格與 Funding 網格完整，損益、資金費與風控核對通過。\n"
        "截止點只包含已收盤 4H K 線；日線濾網只使用已完成日線。"
        "原生引擎於最後一根 K 線的時間戳強制平倉，時間戳是 K 線開盤時間。\n\n"
        "## 研究與成本限制\n\n"
        "依使用者 2026-10-21 的新授權開啟 2026，只跑固定 V1，沒有搜尋、選參或調參。"
        "舊 WFA／2025 選參紀錄保持不變。2026 現已被觀察，不能再稱為未見保留樣本。\n"
        "每邊 5 bps taker + 5 bps 滑價現金代理，加上實際 Funding；"
        "沒有真實流動性撮合或清算費模擬；歷史逐時交易規格也非完整重建。\n"
    )
    (OUT / "REPORT.md").write_text(report)
    print(json.dumps({k: summary[k] for k in ["actual_start", "actual_end", "starting_balance",
                                            "final_balance", "total_return", "stats",
                                            "mark_to_market"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["download", "run"])
    parser.add_argument("--allow-final-holdout", action="store_true", required=True)
    args = parser.parse_args()
    download() if args.action == "download" else run()

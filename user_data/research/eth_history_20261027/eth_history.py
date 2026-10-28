"""Fixed BTC-selected long-only strategy on ETH; isolated descriptive evaluation."""

import argparse
import copy
import json
import shutil
import subprocess
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

from scripts.analyze_result import load_result, summarize
from scripts.audit_data import audit_frame
from scripts.candidate_inception import freeze, taipei_end_exclusive, trim_frame, write_json
from scripts.mark_to_market import equity_from_fills, equity_metrics
from scripts.study import ROOT, sha
from scripts.summarize_backtest import reconcile

PAIR = "ETH/USDT:USDT"
DAY = "2026-10-27"
OUT = ROOT / "user_data/research/eth_history_20261027"
DATA = ROOT / "user_data/data/eth_history_20261027"
RESULTS = ROOT / "user_data/backtest_results/eth_history_20261027"
REVISION = ROOT / "user_data/research/return_revision_20261028"
FILES = {
    "4h": ("ETH_USDT_USDT-4h-futures.feather", "4h"),
    "1d": ("ETH_USDT_USDT-1d-futures.feather", "1D"),
    "mark": ("ETH_USDT_USDT-1h-mark.feather", "1h"),
    "funding": ("ETH_USDT_USDT-1h-funding_rate.feather", "8h"),
}


def market_snapshot() -> dict:
    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    market = exchange.load_markets()[PAIR]
    first = exchange.fetch_ohlcv(PAIR, "4h", exchange.parse8601("2019-11-01T00:00:00Z"), 1)
    return {
        "source": "Binance USDT-M public exchangeInfo and earliest available 4h kline",
        "ccxt_version": ccxt.__version__, "onboard_ms": int(market["info"]["onboardDate"]),
        "first_4h_open": exchange.iso8601(first[0][0]),
        "rules": {k: market[k] for k in (
            "id", "symbol", "active", "contractSize", "linear", "inverse", "precision", "limits"
        )},
    }


def setup() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (DATA / "futures").mkdir(parents=True, exist_ok=True)
    validation = json.loads((REVISION / "validation.json").read_text())
    revision = json.loads((REVISION / "protocol.json").read_text())
    accepted = json.loads((REVISION / "summary.json").read_text())
    for filename, field in (("summary.json", "summary_sha256"),
                            ("candidate_config.json", "candidate_config_sha256"),
                            ("protocol.json", "protocol_sha256")):
        if sha(REVISION / filename) != validation[field]:
            raise ValueError("BTC-selected candidate evidence changed")
    if not accepted["accepted"] or accepted["selected"] != "long_only":
        raise ValueError("Require the already selected long-only candidate")
    source = ROOT / "user_data/strategies/DonchianReturnStrategy.py"
    base = ROOT / "user_data/strategies/Donchian4HStrategy.py"
    if (sha(source) != revision["strategy_sha256"]
            or sha(base) != revision["identity"]["strategy"]):
        raise ValueError("Frozen strategy code changed")
    config = copy.deepcopy(json.loads((REVISION / "candidate_config.json").read_text()))
    config["exchange"]["pair_whitelist"] = [PAIR]
    config["exchange"]["only_from_ccxt"] = True
    if not config["dry_run"] or any(config["exchange"].get(k)
                                   for k in ("key", "secret", "password", "uid", "account_id")):
        raise ValueError("ETH evaluation requires credential-free dry-run")
    freeze(OUT / "config.json", config)
    snapshot = OUT / "market_rules.json"
    if not snapshot.exists():
        freeze(snapshot, market_snapshot())
    market = json.loads(snapshot.read_text())
    plan = {
        "authorization": "User requested ETH Binance inception through yesterday on 2026-10-28",
        "pair": PAIR, "strategy": "DonchianReturnStrategy",
        "parameters": config["research"], "selection": "none; BTC-selected parameters unchanged",
        "requested_start": market["first_4h_open"], "through_local_date": DAY,
        "end_exclusive": taipei_end_exclusive(DAY).isoformat(), "timezone": "Asia/Taipei",
        "warmup_daily_candles": 1000,
        "final_holdout": "2025/2026 explicitly evaluated only; no tuning or BTC deployment changes",
        "announcement": "https://www.binance.com/en-PH/support/announcement/detail/360036964392",
        "announcement_date": "2019-11-29; publication says the contract has already launched",
        "market_snapshot_sha256": sha(snapshot), "config_sha256": sha(OUT / "config.json"),
        "strategy_sha256": sha(source), "base_strategy_sha256": sha(base),
        "accepted_candidate_sha256": sha(REVISION / "summary.json"),
        "runner_sha256": sha(Path(__file__)),
        "supporting_code": {name: sha(ROOT / "scripts" / name) for name in (
            "candidate_inception.py", "audit_data.py", "study.py", "analyze_result.py",
            "mark_to_market.py", "summarize_backtest.py"
        )},
        "environment_sha256": sha(ROOT / "user_data/research/environment.lock.txt"),
    }
    freeze(OUT / "protocol.json", plan)
    for path in (source, base, Path(__file__)):
        shutil.copy2(path, OUT / path.name)
    return plan


def download(plan: dict) -> None:
    end = pd.Timestamp(plan["end_exclusive"])
    command = [
        "freqtrade", "download-data", "--config", str(OUT / "config.json"),
        "--datadir", str(DATA), "--trading-mode", "futures", "--timeframes", "4h", "1d",
        "--timerange", f"{int(pd.Timestamp('2019-11-01', tz='UTC').timestamp())}-"
        f"{int(end.timestamp())}",
    ]
    log = OUT / "download.log"
    with log.open("w") as handle:
        process = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT)
    if process.returncode or " - ERROR - " in log.read_text():
        raise RuntimeError(f"ETH download failed; inspect {log.relative_to(ROOT)}")
    print("Native ETH futures, mark and funding download complete", flush=True)


def audit(plan: dict) -> tuple[dict, dict]:
    end = pd.Timestamp(plan["end_exclusive"])
    raw = {key: pd.read_feather(DATA / "futures" / filename)
           for key, (filename, _) in FILES.items()}
    # Discard only the incomplete first listing day, not any later bad prices.
    start = max(raw[key]["date"].min().ceil("1D") for key in ("4h", "1d"))
    frames, errors, files = {}, [], {}
    raw_dir = DATA / "raw_download"
    raw_dir.mkdir(exist_ok=True)
    for key, (filename, interval) in FILES.items():
        path = DATA / "futures" / filename
        if not (raw_dir / filename).exists():
            shutil.copy2(path, raw_dir / filename)
        frame = trim_frame(raw[key], key, end)
        begin = start if key in {"4h", "1d"} else frame["date"].min()
        frame = frame.loc[frame["date"] >= begin].reset_index(drop=True)
        stop = end.floor("1D") if key == "1d" else end
        info = audit_frame(frame, start=begin, end=stop,
                           interval=interval, funding=key == "funding")
        frame.to_feather(path)
        info.update(file=str(path.relative_to(ROOT)), sha256=sha(path))
        files[key], frames[key] = info, frame
        errors.extend(f"{key}: {error}" for error in info["errors"])
    daily = frames["1d"].set_index("date")
    columns = ["open", "high", "low", "close"]
    aggregate = frames["4h"].set_index("date").resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).reindex(daily.index)
    mismatch = ~np.isclose(aggregate[columns], daily[columns], rtol=1e-8, atol=1e-6)
    if mismatch.any():
        errors.append("ETH daily OHLC disagrees with aggregated 4h prices")
    evaluation_start = daily.index[plan["warmup_daily_candles"]]
    for key in ("mark", "funding"):
        if frames[key]["date"].min() > evaluation_start:
            errors.append(f"{key} missing from possible trading period")
    manifest = {
        "status": "FAIL" if errors else "PASS", "files": files, "errors": errors,
        "verified_price_start": start.isoformat(), "evaluation_start": evaluation_start.isoformat(),
        "end_exclusive": end.isoformat(), "mismatched_price_cells": int(mismatch.sum()),
        "inconsistent_days": [str(day) for day in daily.index[mismatch.any(axis=1)]],
        "policy": "Incomplete listing day excluded; no price repairs or funding zero fill",
    }
    if errors:
        write_json(OUT / "data_manifest.failed.json", manifest)
        raise ValueError(f"ETH data audit failed: {errors}")
    freeze(OUT / "data_manifest.json", manifest)
    return frames, manifest


def run(plan: dict) -> None:
    frames, manifest = audit(plan)
    if market_snapshot() != json.loads((OUT / "market_rules.json").read_text()):
        raise ValueError("ETH public execution rules changed")
    start, end = pd.Timestamp(manifest["evaluation_start"]), pd.Timestamp(plan["end_exclusive"])
    command = [
        "freqtrade", "backtesting", "--config", str(OUT / "config.json"),
        "--datadir", str(DATA), "--strategy", "DonchianReturnStrategy",
        "--timerange", f"{int(start.timestamp())}-{int(end.timestamp()) - 1}",
        "--enable-protections", "--cache", "none", "--export", "trades",
        "--backtest-directory", str(RESULTS),
    ]
    log = OUT / "native.log"
    with log.open("w") as handle:
        process = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT)
    if process.returncode or " - ERROR - " in log.read_text():
        raise RuntimeError(f"ETH native backtest failed; inspect {log.relative_to(ROOT)}")
    archive = RESULTS / json.loads((RESULTS / ".last_result.json").read_text())["latest_backtest"]
    result, config = load_result(archive, "DonchianReturnStrategy")
    if (pd.Timestamp(result["backtest_start"], tz="UTC") != start
            or pd.Timestamp(result["backtest_end"], tz="UTC") != end - pd.Timedelta(hours=4)):
        raise ValueError("ETH native evaluation window differs from the protocol")
    trades = pd.DataFrame(result["trades"])
    if trades.empty:
        raise ValueError("No ETH trades: do not claim a validated result")
    if (trades["is_short"].any() or set(trades["pair"]) != {PAIR}
            or (pd.to_datetime(trades["open_date"], utc=True) < start).any()
            or (pd.to_datetime(trades["close_date"], utc=True) >= end).any()):
        raise ValueError("ETH trades violate the fixed pair/direction/window")
    integrity, audited = reconcile(trades, frames["4h"], frames["funding"], frames["mark"],
                                   config, result["starting_balance"])
    if not integrity["passed"]:
        raise ValueError(f"ETH accounting/risk audit failed: {integrity}")
    audited.to_csv(RESULTS / "trades.csv", index=False)
    equity = equity_from_fills(result, frames["mark"], frames["funding"])
    equity.to_csv(RESULTS / "mark_equity_hourly.csv")
    summary = summarize(result, config)
    summary.update(integrity=integrity, mark_to_market=equity_metrics(equity),
                   source=str(archive.relative_to(ROOT)), source_sha256=sha(archive),
                   protocol_sha256=sha(OUT / "protocol.json"),
                   manifest_sha256=sha(OUT / "data_manifest.json"))
    write_json(OUT / "summary.json", summary)
    report = (
        "# ETHUSDT 固定只做多策略：上市歷史至 2026-10-27\n\n"
        f"最早 API 4h 資料：{plan['requested_start']}；"
        f"完整日線起點：{manifest['verified_price_start']}。\n"
        "官方 2019-11-29 公告表示 ETH/USDT 永續已推出；API 最早資料為 2019-11-27。\n"
        f"正式績效期間 UTC：{summary['actual_start']} 至 {summary['actual_end']}。\n"
        "資料截止（不含）UTC 2026-10-27 16:00＝台北 10/28 00:00；"
        "引擎期末強平時間戳為最後 4h K 線開盤，即台北 10/27 20:00。\n\n"
        f"- 起始／最終資金：{summary['starting_balance']:,.2f}／"
        f"{summary['final_balance']:,.2f} USDT\n"
        f"- 累積淨報酬：{summary['total_return']:.4%}\n"
        f"- 正式績效期間年化：{summary['cagr']:.4%}\n"
        f"- 每小時 Mark 最大回撤：{summary['mark_to_market']['max_drawdown']:.4%}\n"
        f"- 完整交易：{summary['stats']['trades']} 筆；勝率 {summary['stats']['win_rate']:.2%}\n"
        f"- 獲利因子：{summary['stats']['profit_factor']:.4f}\n\n"
        "固定 BTC 選出的設定，只做多、ADX 關閉、每筆預算風險 0.5%、最高 2 倍。"
        "直接執行相同策略原始碼，沒有以 ETH 結果選參。保留 1000 根日線暖機，"
        "因此不能稱為上市首日即交易；上市第一天不完整價格只用原始下載保留。\n"
        "每邊 5 bps taker + 5 bps 滑價現金代理及實際 Funding；核帳與風控通過。"
        "未模擬真實撮合、清算費及完整歷史逐時交易規格；小時回撤可能漏掉日內極端值。\n"
        "本次屬跨幣種固定模型描述性評估，並非新鮮獨立樣本外證明。"
        "BTC VM 與設定未變，ETH 沒有啟動模擬或實盤服務。\n"
    )
    (OUT / "REPORT.md").write_text(report)
    setup()  # Verify sources remained frozen throughout the evaluation.
    print(report, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["download", "run"])
    parser.add_argument("--allow-final-holdout", action="store_true", required=True)
    args = parser.parse_args()
    plan = setup()
    download(plan) if args.action == "download" else run(plan)


if __name__ == "__main__":
    main()

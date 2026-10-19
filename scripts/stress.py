"""Frozen post-study diagnostics, using native Freqtrade execution only."""

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.analyze_result import load_result, summarize
from scripts.audit_data import audit_frame
from scripts.mark_to_market import equity_from_fills, equity_metrics
from scripts.study import canonical, native_timerange, sha
from scripts.summarize_backtest import reconcile

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "user_data/research/stress"
PARENT = ROOT / "user_data/research/study"
DATA = ROOT / "user_data/data/stress/futures"
RESULTS = ROOT / "user_data/backtest_results/stress"


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def freeze(path, value):
    if path.exists():
        if read(path) != value:
            raise ValueError(f"Frozen diagnostic changed: {path}")
    else:
        write(path, value)


def check_period(period):
    start, end = period.split("-")
    if not ("20240101" <= start < end <= "20260101"):
        raise ValueError("Diagnostics must remain within 2024/2025")


def scaled_params(params, scale, hold=False):
    if not np.isfinite(scale) or not 0 < scale <= 1:
        raise ValueError("Only finite positive exposure reductions are allowed")
    result = dict(params)
    key = "hold_allocation" if hold else "risk_per_trade"
    result[key] *= scale
    return result


def prepare():
    """Freeze additional downloaded bars; never mutate parent data."""
    protocol = read(HERE / "protocol.json")
    start, end = pd.Timestamp(protocol["data_start"]), pd.Timestamp(protocol["data_end_exclusive"])
    manifest = {"files": {}, "cross_timeframe": {}}
    DATA.mkdir(parents=True, exist_ok=True)
    for key, info in read(PARENT / "data_manifest.json")["files"].items():
        source = ROOT / info["file"]
        if sha(source) != info["sha256"]:
            raise ValueError("Parent data checksum mismatch")
        target = DATA / source.name
        if not target.exists() or sha(target) != info["sha256"]:
            shutil.copyfile(source, target)
        manifest["files"][key] = {
            "file": str(target.relative_to(ROOT)),
            "sha256": sha(target),
            "rows": info["rows"],
            "source": "unchanged frozen parent data",
        }
    frames = {}
    for key, name, interval in [
        ("detail_1h", "BTC_USDT_USDT-1h-futures.feather", "1h"),
        ("detail_15m", "BTC_USDT_USDT-15m-futures.feather", "15min"),
        ("mark_15m", "BTC_USDT_USDT-15m-mark.feather", "15min"),
    ]:
        path = DATA / name
        if key.startswith("detail_"):
            source = ROOT / "user_data/data/stress_rest/futures" / name
            shutil.copyfile(source, path)
        else:
            source = ROOT / "user_data/data/stress_archive/futures" / name
            if source.exists():
                shutil.copyfile(source, path)
        frame = pd.read_feather(path)
        inside = frame.loc[(frame.date >= start) & (frame.date < end)].reset_index(drop=True)
        if len(inside) != len(frame):
            inside.to_feather(path)
        audit = audit_frame(inside, start=start, end=end, interval=interval)
        if audit["errors"]:
            raise ValueError(f"{key}: {audit}")
        if key.startswith("detail_") and (inside.volume <= 0).any():
            raise ValueError("Zero-volume futures bars may be downloader-filled; reject them")
        frames[key] = inside
        manifest["files"][key] = {
            **audit,
            "file": str(path.relative_to(ROOT)),
            "sha256": sha(path),
        }
    aggregation = {"open": "first", "high": "max", "low": "min", "close": "last"}
    for key, interval, target in [
        ("detail_1h", "1h", frames["detail_1h"]),
        ("4h", "4h", pd.read_feather(DATA / "BTC_USDT_USDT-4h-futures.feather")),
    ]:
        coarse = frames["detail_15m"].set_index("date").resample(interval).agg(aggregation)
        aligned = target.set_index("date").reindex(coarse.index)
        equal = np.allclose(
            coarse.to_numpy(), aligned[list(aggregation)].to_numpy(), atol=1e-8, rtol=0
        )
        manifest["cross_timeframe"][f"15m_to_{key}_ohlc"] = bool(equal)
        if not equal:
            difference = (coarse - aligned[list(aggregation)]).abs()
            dates = difference.index[difference.gt(1e-8).any(axis=1)]
            registered = {pd.Timestamp(v) for v in protocol["source_quality"]["allowed_bar_dates"]}
            if not set(dates) <= registered:
                raise ValueError(f"Unregistered price mismatch in {key}: {dates.tolist()}")
            manifest.setdefault("warnings", []).append(
                {
                    "comparison": f"15m_to_{key}",
                    "dates": [str(d) for d in dates],
                    "max_price_difference": {k: float(v) for k, v in difference.max().items()},
                    "action": "Keep native REST details and frozen parent main bars unchanged",
                }
            )
    hourly = pd.read_feather(DATA / "BTC_USDT_USDT-1h-mark.feather").set_index("date")
    fine = frames["mark_15m"].set_index("date").open.reindex(hourly.loc[start:end].index)
    common = fine.dropna()
    equal = np.allclose(common, hourly.open.reindex(common.index), atol=1e-8, rtol=0)
    manifest["cross_timeframe"]["mark_hourly_open_equal"] = bool(equal)
    if not equal:
        raise ValueError("Fine Mark prices disagree with frozen hourly valuation/funding prices")
    manifest["detail_source"] = "Freqtrade Binance only_from_ccxt REST; no synthetic price repair"
    manifest["status"] = "PASS_WITH_WARNINGS" if manifest.get("warnings") else "PASS"
    freeze(HERE / "data_manifest.json", manifest)
    print(f"Detail data audit {manifest['status']}; 2026 excluded", flush=True)


class Diagnostics:
    def __init__(self):
        self.protocol = read(HERE / "protocol.json")
        manifest = read(HERE / "data_manifest.json")
        if manifest["status"] not in {"PASS", "PASS_WITH_WARNINGS"}:
            raise ValueError("Fine data audit must pass")
        self.frames = {}
        for key, info in manifest["files"].items():
            path = ROOT / info["file"]
            if sha(path) != info["sha256"]:
                raise ValueError("Diagnostic data changed")
            self.frames[key] = pd.read_feather(path)
        self.config = read(ROOT / "user_data/config.json")
        if not self.config["dry_run"] or any(
            self.config["exchange"].get(k) for k in ("key", "secret")
        ):
            raise ValueError("Credential-free dry-run only")
        parent = read(PARENT / "frozen_identity.json")
        for field, file in {
            "strategy": "user_data/strategies/Donchian4HStrategy.py",
            "reference": "user_data/strategies/BuyHoldReference.py",
            "base_config": "user_data/config.json",
            "environment": "user_data/research/environment.lock.txt",
        }.items():
            if sha(ROOT / file) != parent[field]:
                raise ValueError(f"Parent {field} changed")
        self.identity = {
            "parent": sha(PARENT / "frozen_identity.json"),
            "protocol": sha(HERE / "protocol.json"),
            "strategy": parent["strategy"],
            "reference": parent["reference"],
            "diagnostic_strategy": sha(ROOT / "user_data/strategies/ExecutionStressStrategy.py"),
            "base_config": parent["base_config"],
            "environment": parent["environment"],
            "data": {k: v["sha256"] for k, v in manifest["files"].items()},
            "selection": {
                name: sha(PARENT / f"selection-{name}.json")
                for name in ("oos_2025_h1", "oos_2025_h2")
            },
        }
        freeze(HERE / "frozen_identity.json", self.identity)
        self.runs = {}

    def run(self, params, period, detail="15m", strategy="Donchian4HStrategy"):
        check_period(period)
        config = copy.deepcopy(self.config)
        config["research"].update(params)
        config["fee"] = config["research"]["taker_fee"] + config["research"]["slippage_per_side"]
        key = {
            "identity": self.identity,
            "params": params,
            "period": period,
            "detail": detail,
            "strategy": strategy,
            "boundary_policy": "exclusive_end_v1",
        }
        if strategy == "BuyHoldReference":
            start = pd.to_datetime(period.split("-")[0], format="%Y%m%d", utc=True).isoformat()
            config["research"]["reference_start"] = start
            key["reference_start"] = start
        run_id = hashlib.sha256(canonical(key).encode()).hexdigest()[:20]
        directory = RESULTS / run_id
        directory.mkdir(parents=True, exist_ok=True)
        freeze(directory / "config.json", config)
        native = directory / "native_result.json"
        if native.exists():
            info = read(native)
            path = ROOT / info["path"]
            if sha(path) != info["sha256"]:
                raise ValueError("Diagnostic native result changed")
        else:
            command = [
                "freqtrade",
                "backtesting",
                "--config",
                str(directory / "config.json"),
                "--datadir",
                str(DATA.parent),
                "--timerange",
                native_timerange(period),
                "--strategy",
                strategy,
                "--timeframe-detail",
                detail,
                "--enable-protections",
                "--cache",
                "none",
                "--export",
                "trades",
                "--backtest-directory",
                str(directory),
            ]
            log = directory / "native.log"
            with log.open("w") as handle:
                process = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT)
            latest = directory / ".last_result.json"
            if process.returncode or " - ERROR - " in log.read_text() or not latest.exists():
                raise RuntimeError(f"Native diagnostic failed: {log.relative_to(ROOT)}")
            path = directory / read(latest)["latest_backtest"]
            write(
                native,
                {
                    "path": str(path.relative_to(ROOT)),
                    "sha256": sha(path),
                    "created_at": datetime.now(UTC).isoformat(),
                    "key": key,
                },
            )
        result, saved = load_result(path, strategy)
        trades = pd.DataFrame(result["trades"])
        if trades.empty:
            raise ValueError("Registered diagnostic unexpectedly produced no trades")
        end = pd.to_datetime(period.split("-")[1], format="%Y%m%d", utc=True)
        start = pd.to_datetime(period.split("-")[0], format="%Y%m%d", utc=True)
        if (pd.to_datetime(trades.close_date, utc=True) >= end).any() or (
            pd.to_datetime(trades.open_date, utc=True) < start
        ).any():
            raise ValueError("Diagnostic date boundary violation")
        cap = config["research"].get("contract_leverage", 2)
        if (trades.leverage > cap + 1e-9).any():
            raise ValueError("Diagnostic contract cap violation")
        integrity, audited = reconcile(
            trades,
            self.frames["4h"],
            self.frames["funding"],
            self.frames["mark"],
            saved,
            result["starting_balance"],
        )
        if strategy == "BuyHoldReference":
            skip = {"budgeted_risk_at_most_limit", "atr_stop_installed_and_tightened"}
            integrity["not_applicable_to_hold_reference"] = sorted(skip)
            integrity["passed"] = all(v for k, v in integrity["checks"].items() if k not in skip)
        if not integrity["passed"]:
            raise ValueError(f"Diagnostic risk/accounting check failed {run_id}: {integrity}")
        equity = equity_from_fills(result, self.frames["mark_15m"], self.frames["funding"])
        if abs(equity.iloc[-1] - result["final_balance"]) > 1e-5:
            raise ValueError("Fine Mark equity does not reconcile")
        marked = equity_metrics(equity)
        marked.update(
            valuation="15m Mark Price open; native fills and exact funding events",
            selection_use="diagnostics; risk weights use 2024 volatility only",
        )
        metrics = summarize(result, saved)
        metrics.update(
            run_id=run_id,
            params=params,
            detail=detail,
            period=period,
            source=str(path.relative_to(ROOT)),
            integrity=integrity,
            mark_to_market=marked,
        )
        audited.to_csv(directory / "trades.csv", index=False)
        equity.to_csv(directory / "mark_equity_15m.csv")
        write(directory / "mark_metrics.json", marked)
        write(directory / "summary.json", metrics)
        self.runs[run_id] = metrics
        print(
            f"{run_id}: {period} {detail} {strategy} return={metrics['total_return']:.4%}",
            flush=True,
        )
        return metrics

    def risk_matching(self):
        rule = self.protocol["risk_matching"]
        common = {
            **rule["fixed_parameters"],
            "contract_leverage": 1.0,
            "max_effective_leverage": 1.0,
            "slippage_per_side": 0.0005,
        }
        variants = {
            "Donchian EMA": ("Donchian4HStrategy", {**common, "ema_filter": True}),
            "Donchian without EMA": ("Donchian4HStrategy", {**common, "ema_filter": False}),
            "1x perpetual hold": ("BuyHoldReference", {**common, "hold_allocation": 0.98}),
        }
        unscaled = {
            name: self.run(params, rule["training"], strategy=strategy)
            for name, (strategy, params) in variants.items()
        }
        target = 0.8 * min(r["mark_to_market"]["annualized_volatility"] for r in unscaled.values())
        calibrated, weights, passes, history = {}, {}, {}, []
        for name, (strategy, params) in variants.items():
            scale = min(1.0, target / unscaled[name]["mark_to_market"]["annualized_volatility"])
            passes[name] = []
            for _ in range(rule["max_calibration_passes"]):
                adjusted = scaled_params(params, scale, strategy == "BuyHoldReference")
                run = self.run(adjusted, rule["training"], strategy=strategy)
                history.append(run)
                passes[name].append(run["run_id"])
                vol = run["mark_to_market"]["annualized_volatility"]
                if abs(vol / target - 1) <= rule["training_relative_vol_tolerance"]:
                    break
                scale = min(1.0, scale * target / vol)
            calibrated[name], weights[name] = run, adjusted
        decision = {
            "identity": self.identity,
            "training": rule["training"],
            "evaluation": rule["evaluation"],
            "target_annualized_volatility": target,
            "unscaled_training_runs": {k: v["run_id"] for k, v in unscaled.items()},
            "calibration_runs": passes,
            "weights": weights,
            "training_tolerance_met": all(
                abs(r["mark_to_market"]["annualized_volatility"] / target - 1)
                <= rule["training_relative_vol_tolerance"]
                for r in calibrated.values()
            ),
        }
        # This write precedes every evaluation run, including when resuming cached work.
        freeze(HERE / "risk_weights.json", decision)
        stamp = HERE / "risk_weights_created.json"
        if not stamp.exists():
            write(
                stamp,
                {
                    "created_at": datetime.now(UTC).isoformat(),
                    "sha256": sha(HERE / "risk_weights.json"),
                },
            )
        evaluation = {
            name: self.run(weights[name], rule["evaluation"], strategy=variants[name][0])
            for name in variants
        }
        return {
            "decision": decision,
            "training_unscaled": unscaled,
            "training_calibrated": calibrated,
            "calibration_history": history,
            "evaluation": evaluation,
        }

    def execute(self):
        parent = read(PARENT / "summary.json")
        folds = [f for f in parent["folds"] if f["name"].startswith("oos_2025")]
        scenarios = {}
        for scenario in self.protocol["stress_scenarios"]:
            runs = []
            for fold in folds:
                selection = read(PARENT / f"selection-{fold['name']}.json")
                if selection["selected"] != fold["selected"]:
                    raise ValueError("Frozen parent selection mismatch")
                params = {
                    **selection["selected"],
                    "slippage_per_side": scenario["slippage_per_side"],
                    "execution_delay_bars": scenario["delay_bars"],
                }
                strategy = (
                    "ExecutionStressStrategy" if scenario["delay_bars"] else "Donchian4HStrategy"
                )
                runs.append(self.run(params, fold["test"], scenario["detail"], strategy))
            scenarios[scenario["name"]] = {
                "definition": scenario,
                "folds": runs,
                "linked_return": float(np.prod([1 + r["total_return"] for r in runs]) - 1),
                "reset_policy": "Half-year resets to 10000 and force-closes; not continuous",
            }
        summary = {
            "identity": self.identity,
            "classification": self.protocol["classification"],
            "baseline_4h_linked_return": parent["pristine_2025_linked_return"],
            "scenarios": scenarios,
            "risk_matching": self.risk_matching(),
            "unique_native_runs": len(self.runs),
            "final_holdout_opened": False,
        }
        write(HERE / "summary.json", summary)
        validate()
        report()


def collect_runs(value):
    found = {}
    if isinstance(value, dict):
        if "run_id" in value:
            found[value["run_id"]] = value
        for child in value.values():
            found.update(collect_runs(child))
    elif isinstance(value, list):
        for child in value:
            found.update(collect_runs(child))
    return found


def validate():
    diagnostic = Diagnostics()
    summary = read(HERE / "summary.json")
    weights = read(HERE / "risk_weights.json")
    stamp = read(HERE / "risk_weights_created.json")
    if sha(HERE / "risk_weights.json") != stamp["sha256"]:
        raise ValueError("Risk weights checksum changed")
    runs = collect_runs(summary)
    checks = {
        "frozen_identity": summary["identity"] == diagnostic.identity,
        "2026_sealed": summary["final_holdout_opened"] is False,
        "all_native_results_accounted": len(runs) == summary["unique_native_runs"],
        "risk_training_tolerance": weights["training_tolerance_met"],
    }
    for run_id, metrics in runs.items():
        check_period(metrics["period"])
        native = read(RESULTS / run_id / "native_result.json")
        key = native["key"]
        if key["identity"] != diagnostic.identity or any(
            key[field] != metrics[field] for field in ("params", "period", "detail", "strategy")
        ):
            raise ValueError("Native cache identity does not match the diagnostic")
        if sha(ROOT / native["path"]) != native["sha256"]:
            raise ValueError("Native ZIP checksum mismatch")
        with zipfile.ZipFile(ROOT / native["path"]) as archive:
            code = next(n for n in archive.namelist() if n.endswith(f"_{metrics['strategy']}.py"))
            bundled = hashlib.sha256(archive.read(code)).hexdigest()
        source = ROOT / f"user_data/strategies/{metrics['strategy']}.py"
        if bundled != sha(source):
            raise ValueError("Native strategy bundle differs from frozen source")
        result, config = load_result(ROOT / native["path"])
        end = pd.to_datetime(metrics["period"].split("-")[1], format="%Y%m%d", utc=True)
        start = pd.to_datetime(metrics["period"].split("-")[0], format="%Y%m%d", utc=True)
        trades = pd.DataFrame(result["trades"])
        if (pd.to_datetime(trades.close_date, utc=True) >= end).any() or (
            pd.to_datetime(trades.open_date, utc=True) < start
        ).any():
            raise ValueError("Native dates cross diagnostic boundary")
        if config["research"] != read(RESULTS / run_id / "config.json")["research"]:
            raise ValueError("Native configuration does not match requested risk weights")
        actual, _ = reconcile(
            trades,
            diagnostic.frames["4h"],
            diagnostic.frames["funding"],
            diagnostic.frames["mark"],
            config,
            result["starting_balance"],
        )
        skip = (
            {"budgeted_risk_at_most_limit", "atr_stop_installed_and_tightened"}
            if metrics["strategy"] == "BuyHoldReference"
            else set()
        )
        if not all(v for k, v in actual["checks"].items() if k not in skip):
            raise ValueError("Independent native risk/accounting audit failed")
        equity = equity_from_fills(
            result, diagnostic.frames["mark_15m"], diagnostic.frames["funding"]
        )
        recomputed = equity_metrics(equity)
        for key in ("max_drawdown", "sharpe_daily", "annualized_volatility", "final_equity"):
            if not np.isclose(recomputed[key], metrics["mark_to_market"][key], atol=1e-9, rtol=0):
                raise ValueError(f"Independent Mark metric mismatch: {key}")
        if (
            not metrics["integrity"]["passed"]
            or abs(metrics["mark_to_market"]["final_equity"] - result["final_balance"]) > 1e-5
        ):
            raise ValueError("Diagnostic integrity check failed")
    for name, run in summary["risk_matching"]["evaluation"].items():
        native = read(RESULTS / run["run_id"] / "native_result.json")
        if native["created_at"] <= stamp["created_at"]:
            raise ValueError("Evaluation ran before risk weights were frozen")
        if run["params"] != weights["weights"][name]:
            raise ValueError("Evaluation risk weights changed")
    checks.update(
        native_zip_hashes=True,
        native_configurations=True,
        accounting_and_mark_equity=True,
        weights_frozen_before_evaluation=True,
        no_2025_recalibration=True,
        native_strategy_bundles=True,
    )
    validation = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "unique_native_runs": len(runs),
        "implementation_sha256": sha(Path(__file__)),
    }
    write(HERE / "validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError(f"Diagnostic validation failed: {checks}")
    print(f"Diagnostics validation PASS: {len(checks)} checks, {len(runs)} native runs", flush=True)


def report():
    summary = read(HERE / "summary.json")
    lines = [
        "# 細週期執行與事前波動預算補充診斷",
        "",
        "2024／2025 已觀察，本輪不屬於新的獨立樣本外證據。2026 未開封。",
        "",
        "## 固定選參的成交／停損壓力情境",
        "",
        "4H 訊號不變；原生 Freqtrade 使用較細 K 線處理持倉事件。",
        "",
        "| 情境 | 2025 H1 | 2025 H2 | 串接報酬 | 較差半年度 Mark 回撤 |",
        "|---|---:|---:|---:|---:|",
        f"| 原始 4H | — | — | {summary['baseline_4h_linked_return']:.2%} | — |",
    ]
    for name, scenario in summary["scenarios"].items():
        a, b = scenario["folds"]
        dd = max(r["mark_to_market"]["max_drawdown"] for r in (a, b))
        lines.append(
            f"| {name} | {a['total_return']:.2%} | {b['total_return']:.2%} | "
            f"{scenario['linked_return']:.2%} | {dd:.2%} |"
        )
    risk = summary["risk_matching"]
    target = risk["decision"]["target_annualized_volatility"]
    lines += [
        "",
        "每半年本金重設 10,000 並期末平倉；串接報酬不是全年連續資金模擬。",
        "",
        "## 事前波動預算比較",
        "",
        f"只用 2024 校準，目標年化波動 {target:.2%}；先凍結比重再執行 2025。",
        "固定 V1（20／10／2.5）及 1x 合約槓桿，與上表的逐期選參策略分開解讀。",
        "",
        "| 策略 | 2024 校準波動 | 2025 波動 | 2025 報酬 | Mark 回撤 | 日 Sharpe |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, run in risk["evaluation"].items():
        marked = run["mark_to_market"]
        train = risk["training_calibrated"][name]["mark_to_market"]["annualized_volatility"]
        lines.append(
            f"| {name} | {train:.2%} | {marked['annualized_volatility']:.2%} | "
            f"{run['total_return']:.2%} | {marked['max_drawdown']:.2%} | "
            f"{marked['sharpe_daily']:.3f} |"
        )
    lines += [
        "",
        "## 解讀範圍",
        "",
        "- 15m Mark 開盤價計算浮動損益；交易、費用、資金費率與原生期末資產逐筆核對。",
        "- 較高滑價以單邊 20bps 現金成本代理，另加 5bps 手續費；未模擬實際成交價衝擊。",
        "- 漏檢情境延後整輪 4H 進場訊號，當時重新計算部位；不代表毫秒延遲或固定數量掛單。",
        "- 細週期仍無委託簿、排隊、跳空停損成交重播；不能據此認定可實盤獲利。",
        "- 相同事前波動預算不保證 2025 實現波動、尾部風險或停損風險相同。",
        "- 持有參考為 1x 永續合約，包含資金費率；不是現貨。",
        "- 未因這些結果修改原選參、策略規則，未使用 2026 資料或送出實際委託。",
        "",
        f"原生回測 {summary['unique_native_runs']} 次；"
        f"驗證：{read(HERE / 'validation.json')['status']}。",
        "權重與結果見 risk_weights.json、summary.json；逐筆交易保留於本機。",
        "",
    ]
    manifest = read(HERE / "data_manifest.json")
    if manifest.get("warnings"):
        lines += [
            "## 資料來源警告",
            "",
            "細週期改用原生 Binance REST 下載，排除 archive 下載中的零量平價 K 線。",
            "2024-10-28 的 REST 15m／1H 與凍結 4H 仍有少數價格不一致；"
            "未補造價格，未改舊研究資料。詳見 data_manifest.json。",
            "本輪資料狀態為 PASS_WITH_WARNINGS，結果應連同此限制解讀。",
            "",
        ]
    (HERE / "REPORT.md").write_text("\n".join(lines))
    print("Report: user_data/research/stress/REPORT.md", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run", "validate", "report"])
    action = parser.parse_args().action
    if action == "run":
        Diagnostics().execute()
    else:
        {"prepare": prepare, "validate": validate, "report": report}[action]()


if __name__ == "__main__":
    main()

"""One continuous fixed-baseline run over audited history; keep 2026 sealed."""

import json
from pathlib import Path

import pandas as pd

from scripts.study import ROOT, Study, sha
from user_data.strategies.Donchian4HStrategy import Donchian4HStrategy


def evaluation_period(frames: dict, warmup: int) -> str:
    if warmup < 1 or any(len(frames[k]) <= warmup for k in ("4h", "1d")):
        raise ValueError("Insufficient completed warmup candles")
    start = max(frames[k]["date"].iloc[warmup] for k in ("4h", "1d"))
    end = min(
        frames[k]["date"].iloc[-1] + pd.Timedelta(interval)
        for k, interval in (("4h", "4h"), ("1d", "1D"), ("mark", "1h"), ("funding", "8h"))
    )
    end = min(end, pd.Timestamp("2026-01-01", tz="UTC"))
    if start >= end or start != start.normalize() or end != end.normalize():
        raise ValueError("Require a nonempty, UTC midnight-aligned complete period")
    return f"{start:%Y%m%d}-{end:%Y%m%d}"


def main() -> None:
    output = ROOT / "user_data/research/long_history"
    output.mkdir(parents=True, exist_ok=True)
    study = Study(results_root=ROOT / "user_data/backtest_results/long_history")
    period = evaluation_period(study.frames, Donchian4HStrategy.startup_candle_count)
    protocol = {
        "purpose": "User-requested longest available continuous baseline; not a ten-year result",
        "period": period,
        "warmup_daily_candles": Donchian4HStrategy.startup_candle_count,
        "params": study.base,
        "identity": study.identity,
        "runner_sha256": sha(Path(__file__)),
        "holdout": "2026 remains sealed; no selection or tuning",
    }
    path = output / "protocol.json"
    if path.exists() and json.loads(path.read_text()) != protocol:
        raise ValueError("History protocol changed; archive the old run before proceeding")
    path.write_text(json.dumps(protocol, indent=2) + "\n")
    summary = study.run(study.base, period, "continuous_fixed_baseline")
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    report = (
        "# 固定策略連續歷史回測\n\n"
        f"UTC 半開期間：{period}，非十年回測。2026 未開封。\n\n"
        f"- 起始模擬資金：{summary['starting_balance']:,.2f} USDT\n"
        f"- 最終資金：{summary['final_balance']:,.2f} USDT\n"
        f"- 累積淨報酬：{summary['total_return']:.4%}\n"
        f"- 完整交易：{summary['stats']['trades']} 筆（進場至出場合計一筆）\n"
        f"- 多單：{summary['long']['trades']} 筆；空單：{summary['short']['trades']} 筆\n"
        f"- 損益／風控核對：{summary['integrity']['passed']}\n\n"
        "使用現有 2020–2025 已稽核合約資料，前 1000 根日線為暖機。\n"
        "固定 V1，不調參，不串接年度重設資金的結果；這不是新的獨立樣本外證據。\n"
        "含每邊 5 bps taker + 5 bps 滑價現金代理與實際 Funding；\n"
        "不代表真實滑價撮合或含清算費的實盤結果。\n"
    )
    (output / "REPORT.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()

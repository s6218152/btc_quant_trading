"""Audit raw Freqtrade futures data; never fabricate or zero-fill funding."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "user_data/research/plan.json"
FILES = {
    "4h": ("BTC_USDT_USDT-4h-futures.feather", "4h"),
    "1d": ("BTC_USDT_USDT-1d-futures.feather", "1D"),
    "mark": ("BTC_USDT_USDT-1h-mark.feather", "1h"),
    # Freqtrade's filename timeframe is NOT the funding-event interval.
    "funding": ("BTC_USDT_USDT-1h-funding_rate.feather", "8h"),
}


def audit_frame(
    df: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    interval: str,
    funding: bool = False,
) -> dict:
    errors = []
    required = (
        {"date", "funding_rate"} if funding else {"date", "open", "high", "low", "close", "volume"}
    )
    if not required <= set(df):
        return {"errors": ["missing columns"]}
    dates = df["date"]
    if not isinstance(dates.dtype, pd.DatetimeTZDtype) or str(dates.dt.tz) != "UTC":
        return {"errors": ["timestamps must be timezone-aware UTC"]}
    duplicate_count = int(dates.duplicated().sum())
    if duplicate_count:
        errors.append(f"duplicate timestamps: {duplicate_count}")
    if not dates.is_monotonic_increasing:
        errors.append("timestamps are not sorted")
    expected = pd.date_range(start, end, freq=interval, inclusive="left")
    missing = expected.difference(pd.DatetimeIndex(dates))
    if len(missing):
        errors.append(f"missing expected timestamps: {len(missing)}")
    offset = (dates.dt.as_unit("ns").astype("int64") % pd.Timedelta(interval).value) != 0
    if offset.any():
        errors.append(f"off-grid timestamps: {int(offset.sum())}")
    numeric = df[["funding_rate"]] if funding else df[["open", "high", "low", "close", "volume"]]
    if not np.isfinite(numeric.to_numpy()).all():
        errors.append("non-finite OHLCV/funding values")
    if not funding:
        invalid = (
            (df["low"] > df[["open", "close"]].min(axis=1))
            | (df["high"] < df[["open", "close"]].max(axis=1))
            | (df["low"] <= 0)
            | (df["volume"] < 0)
        )
        if invalid.any():
            errors.append(f"invalid OHLCV rows: {int(invalid.sum())}")
    return {
        "rows": len(df),
        "start": str(dates.min()),
        "end": str(dates.max()),
        "expected_rows": len(expected),
        "missing": len(missing),
        "duplicate_count": duplicate_count,
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true", help="Trim downloader batch overshoot")
    parser.add_argument("--plan", type=Path, default=PLAN)
    parser.add_argument("--datadir", type=Path, default=ROOT / "user_data/data/binance/futures")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "user_data/research/data_manifest.json"
    )
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    start, end = pd.Timestamp(plan["data_start"]), pd.Timestamp(plan["data_end_exclusive"])
    eval_start = pd.Timestamp(plan["evaluation_start"])
    base = args.datadir.resolve()
    manifest = {"plan": plan, "files": {}, "errors": [], "warnings": []}
    frames = {}
    for key, (filename, interval) in FILES.items():
        path = base / filename
        if not path.exists():
            manifest["errors"].append(f"missing file: {filename}")
            continue
        df = pd.read_feather(path)
        in_range = (df["date"] >= start) & (df["date"] < end)
        if args.freeze and not in_range.all():
            df = df.loc[in_range].reset_index(drop=True)
            df.to_feather(path)
        elif not in_range.all():
            manifest["errors"].append(f"{filename}: data outside frozen range; run --freeze")
        result = audit_frame(df, start=start, end=end, interval=interval, funding=key == "funding")
        result["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        result["file"] = str(path.relative_to(ROOT))
        manifest["files"][key] = result
        manifest["errors"].extend(f"{key}: {e}" for e in result["errors"])
        frames[key] = df
    if {"4h", "1d"} <= frames.keys():
        agg = (
            frames["4h"]
            .set_index("date")
            .resample("1D")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        )
        daily = frames["1d"].set_index("date").reindex(agg.index)
        price_columns = ["open", "high", "low", "close"]
        mismatch = ~np.isclose(agg[price_columns], daily[price_columns], rtol=1e-8, atol=1e-6)
        manifest["daily_4h_price_mismatched_cells"] = int(mismatch.sum())
        if mismatch.any():
            manifest["errors"].append("daily prices disagree with aggregated 4h data")
        volume_mismatch = ~np.isclose(agg["volume"], daily["volume"], rtol=1e-8, atol=1e-6)
        if volume_mismatch.any():
            manifest["warnings"].append(
                {
                    "issue": "Exchange daily volumes disagree with aggregated 4h volumes",
                    "dates": [str(d) for d in agg.index[volume_mismatch]],
                    "max_absolute_difference": float((agg["volume"] - daily["volume"]).abs().max()),
                    "impact": (
                        "Daily EMA uses close only; entry uses 4h volume > 0; no volume sizing"
                    ),
                }
            )
        warmup = int((frames["1d"]["date"] < eval_start).sum())
        manifest["daily_warmup_candles"] = warmup
        if warmup < 1000:
            manifest["errors"].append("fewer than 1000 completed daily warmup candles")
    manifest["status"] = "PASS" if not manifest["errors"] else "FAIL"
    output = args.output
    output.write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "rows": {k: v.get("rows") for k, v in manifest["files"].items()},
                "errors": manifest["errors"],
                "warnings": len(manifest["warnings"]),
            }
        )
    )
    raise SystemExit(bool(manifest["errors"]))


if __name__ == "__main__":
    main()

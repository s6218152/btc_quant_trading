import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from DonchianReturnStrategy import DonchianReturnStrategy
from freqtrade.enums import CandleType

from scripts import eth_history
from scripts.dryrun import validate


def test_same_strategy_accepts_eth_backtesting_but_paper_service_remains_btc_only():
    config = json.loads(Path("user_data/config.paper-long-only.json").read_text())
    params = config["research"].copy()
    config["exchange"]["pair_whitelist"] = [eth_history.PAIR]
    config["candle_type_def"] = CandleType.FUTURES
    strategy = DonchianReturnStrategy(config)
    strategy.bot_start()
    assert strategy.settings == params
    assert strategy.startup_candle_count == 1000
    with pytest.raises(ValueError, match="Only Binance BTC"):
        validate(config, {})
    config["dry_run"] = False
    with pytest.raises(ValueError, match="forbids live"):
        strategy.bot_start()


def bars(dates):
    return pd.DataFrame({"date": dates, "open": 3000.0, "high": 3001.0,
                         "low": 2999.0, "close": 3000.0, "volume": 10.0})


@pytest.mark.parametrize("defect", [None, "funding_gap", "daily_price"])
def test_eth_audit_rejects_missing_funding_and_inconsistent_prices(tmp_path, monkeypatch, defect):
    out, data = tmp_path / "out", tmp_path / "data"
    out.mkdir()
    (data / "futures").mkdir(parents=True)
    monkeypatch.setattr(eth_history, "OUT", out)
    monkeypatch.setattr(eth_history, "DATA", data)
    monkeypatch.setattr(eth_history, "ROOT", tmp_path)
    start = pd.Timestamp("2019-11-28", tz="UTC")
    evaluation = start + pd.Timedelta(days=1000)
    end = evaluation + pd.Timedelta(days=2)
    frames = {
        "1d": bars(pd.date_range(start, end, freq="1D", inclusive="left")),
        "4h": bars(pd.date_range(start, end, freq="4h", inclusive="left")),
        "mark": bars(pd.date_range(evaluation, end, freq="1h", inclusive="left")),
        "funding": pd.DataFrame({
            "date": pd.date_range(evaluation, end, freq="8h", inclusive="left"),
            "funding_rate": np.full(6, 0.0001),
        }),
    }
    if defect == "funding_gap":
        frames["funding"] = frames["funding"].drop(index=2)
    elif defect == "daily_price":
        frames["1d"].loc[1000, "close"] = 3000.5
    for key, (name, _) in eth_history.FILES.items():
        frames[key].to_feather(data / "futures" / name)
    plan = {"end_exclusive": end.isoformat(), "warmup_daily_candles": 1000}
    if defect:
        with pytest.raises(ValueError, match="ETH data audit failed|outside the 2019"):
            eth_history.audit(plan)
        if defect == "funding_gap":
            failed = json.loads((out / "data_manifest.failed.json").read_text())
            assert failed["status"] == "FAIL"
    else:
        audited, manifest = eth_history.audit(plan)
        assert manifest["status"] == "PASS"
        assert pd.Timestamp(manifest["evaluation_start"]) == evaluation
        assert len(audited["funding"]) == 6


def test_auxiliary_warmup_mismatch_never_zero_fills_formal_funding():
    dates = pd.date_range("2022-09-06", periods=4, freq="8h", tz="UTC")
    funding = pd.DataFrame({"date": dates, "funding_rate": [0.001] * 4})
    marks = pd.DataFrame({"date": dates[2:], "open": [1500.0, 1501.0]})
    end = dates[-1] + pd.Timedelta(hours=8)
    formal = eth_history.trading_funding(funding, marks, dates[2], end)
    assert formal["funding_rate"].tolist() == [0.001, 0.001]
    with pytest.raises(ValueError, match="no matching Mark"):
        eth_history.trading_funding(funding, marks.iloc[1:], dates[2], end)

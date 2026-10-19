import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from ExecutionStressStrategy import ExecutionStressStrategy
from pandas.testing import assert_frame_equal

from Donchian4HStrategy import Donchian4HStrategy
from scripts import stress
from scripts.stress import check_period, freeze, scaled_params


def test_delayed_signals_use_only_the_previous_complete_main_candle():
    config = json.loads(Path("user_data/config.json").read_text())
    config["research"]["execution_delay_bars"] = 1
    strategy = ExecutionStressStrategy(config)
    signals = pd.DataFrame(
        {
            "enter_long": [0, 1, 0, 1],
            "enter_short": [1, 0, 0, 0],
            "enter_tag": ["short", "long", "", "long"],
        }
    )
    with patch.object(Donchian4HStrategy, "populate_entry_trend", side_effect=lambda x, _: x):
        full = strategy.populate_entry_trend(signals.copy(), {})
        prefix = strategy.populate_entry_trend(signals.iloc[:3].copy(), {})
    assert full.enter_long.tolist() == [0, 0, 1, 0]
    assert full.enter_short.tolist() == [0, 1, 0, 0]
    assert full.enter_tag.tolist() == ["", "short", "long", ""]
    assert_frame_equal(prefix, full.iloc[:3])


@pytest.mark.parametrize("scale", [0, -1, 1.001, np.nan, np.inf])
def test_calibration_cannot_increase_exposure_or_use_nonfinite_weights(scale):
    with pytest.raises(ValueError):
        scaled_params({"risk_per_trade": 0.005}, scale)


def test_strategy_risk_and_hold_allocation_scale_separately():
    params = {"risk_per_trade": 0.005, "hold_allocation": 0.98}
    assert scaled_params(params, 0.5)["risk_per_trade"] == 0.0025
    hold = scaled_params(params, 0.1, hold=True)
    assert hold["hold_allocation"] == pytest.approx(0.098)
    assert hold["risk_per_trade"] == params["risk_per_trade"]
    assert params["risk_per_trade"] == 0.005


@pytest.mark.parametrize("period", ["20250101-20270101", "20230101-20250101", "20260101-20260101"])
def test_diagnostics_cannot_open_final_holdout_or_expand_training(period):
    with pytest.raises(ValueError):
        check_period(period)


def test_existing_risk_decision_cannot_be_overwritten(tmp_path):
    path = tmp_path / "weights.json"
    freeze(path, {"weight": 0.1})
    timestamp = path.stat().st_mtime_ns
    freeze(path, {"weight": 0.1})
    assert path.stat().st_mtime_ns == timestamp
    with pytest.raises(ValueError, match="Frozen diagnostic changed"):
        freeze(path, {"weight": 0.2})


def test_risk_weights_are_saved_before_evaluation_and_ignore_evaluation_volatility(
    tmp_path,
    monkeypatch,
):
    diagnostic = stress.Diagnostics.__new__(stress.Diagnostics)
    diagnostic.protocol = json.loads(Path("user_data/research/stress/protocol.json").read_text())
    diagnostic.identity = {"test": "training-only"}
    monkeypatch.setattr(stress, "HERE", tmp_path)
    evaluations = []

    def native(params, period, detail="15m", strategy="Donchian4HStrategy"):
        hold = strategy == "BuyHoldReference"
        base_vol = 0.8 if hold else (0.10 if params["ema_filter"] else 0.15)
        scale = params["hold_allocation"] / 0.98 if hold else params["risk_per_trade"] / 0.005
        if period == "20250101-20260101":
            assert (tmp_path / "risk_weights.json").exists()
            evaluations.append(params.copy())
            vol = 0.99  # Evaluation vol must never influence the saved training weights.
        else:
            assert period == "20240101-20250101"
            vol = base_vol * scale
        return {
            "run_id": f"{strategy}-{period}-{scale}-{base_vol}",
            "mark_to_market": {"annualized_volatility": vol},
        }

    diagnostic.run = native
    result = diagnostic.risk_matching()
    saved = json.loads((tmp_path / "risk_weights.json").read_text())
    assert len(evaluations) == 3
    assert saved["target_annualized_volatility"] == pytest.approx(0.08)
    assert saved["weights"]["Donchian EMA"]["risk_per_trade"] == pytest.approx(0.004)
    assert saved["weights"]["1x perpetual hold"]["hold_allocation"] == pytest.approx(0.098)
    assert result["decision"] == saved

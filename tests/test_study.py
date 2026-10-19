import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.analyze_result import loss_streak
from scripts.study import (
    assert_training_before_test,
    coarse_candidates,
    native_timerange,
    select_candidate,
)


def test_coarse_search_is_bounded_and_covers_each_requested_value():
    protocol = json.loads(Path("user_data/research/study/protocol.json").read_text())
    base = dict(entry_period=20, exit_period=10, atr_multiplier=2.5, risk_per_trade=0.005)
    candidates = coarse_candidates(base, protocol["coarse_search"])
    assert len(candidates) == 12
    for key in base:
        assert {p[key] for p in candidates} == set(protocol["coarse_search"][key])
    assert all(sum(p[k] != base[k] for k in base) <= 1 for p in candidates)


def test_training_cannot_overlap_future_evaluation():
    assert_training_before_test("20231001-20250101", "20250101-20250701")
    with pytest.raises(ValueError):
        assert_training_before_test("20240101-20250102", "20250101-20250701")


def test_native_timerange_excludes_the_boundary_candle():
    start, end = map(int, native_timerange("20240101-20250101").split("-"))
    assert pd.Timestamp(start, unit="s", tz="UTC") == pd.Timestamp("2024-01-01", tz="UTC")
    assert pd.Timestamp(end + 1, unit="s", tz="UTC") == pd.Timestamp("2025-01-01", tz="UTC")


def test_selection_uses_training_metrics_and_rejects_tiny_samples():
    def metrics(sharpe, count, dd=0.1):
        return {"sharpe": sharpe, "max_drawdown": dd, "stats": {"trades": count}}

    a, b, c = {"entry_period": 10}, {"entry_period": 20}, {"entry_period": 30}
    assert select_candidate([(a, metrics(100, 2)), (b, metrics(1, 20)), (c, metrics(0.5, 30))]) == b
    assert select_candidate([(a, metrics(100, 2))]) is None
    assert select_candidate([(b, metrics(1, 20, 0.2)), (c, metrics(1, 20, 0.1))]) == c


def test_loss_streak_resets_on_zero_or_win():
    assert loss_streak(pd.Series([-1, -2, 0, -3, -4, -5, 1])) == 3


def test_folds_never_open_the_final_holdout_and_label_known_development():
    protocol = json.loads(Path("user_data/research/study/protocol.json").read_text())
    for fold in protocol["folds"]:
        assert_training_before_test(fold["train"], fold["test"])
        assert fold["test"].split("-")[1] <= "20260101"
        if fold["pristine"]:
            assert fold["test"].split("-")[0] >= "20250101"

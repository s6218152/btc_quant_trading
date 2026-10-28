import pytest

from scripts.return_study import PERIODS, VARIANTS, choose, guard_period, qualifies


def metric(cagr=0.05, dd=0.03, trades=20):
    return {
        "actual_start": "2023-01-01 00:00:00",
        "actual_end": "2023-12-31 20:00:00",
        "starting_balance": 10000,
        "cagr": cagr,
        "mark_to_market": {"max_drawdown": dd},
        "stats": {"trades": trades},
        "integrity": {"passed": True},
    }


@pytest.mark.parametrize("period", ["20240101-20260101", "20250101-20260101",
                                    "20260101-20270101", "20240101-20230101"])
def test_holdout_periods_and_invalid_order_rejected(period):
    with pytest.raises(ValueError, match="development"):
        guard_period(period)


def test_bounded_design_and_development_windows():
    assert len(VARIANTS) == 4
    assert {(v["long_only"], v["adx_filter"]) for v in VARIANTS.values()} == {
        (False, False), (True, False), (False, True), (True, True)
    }
    for period in PERIODS.values():
        guard_period(period)


def test_selection_prefers_net_return_over_win_rate_or_sharpe():
    runs = {"baseline": metric(), "a": metric(0.07), "b": metric(0.08, 0.04)}
    assert choose(runs) == "b"


@pytest.mark.parametrize("candidate", [metric(0.05), metric(0.06, trades=9),
                                      metric(0.20, dd=0.051), metric(float("nan")),
                                      metric(0.20, dd=float("nan"))])
def test_larger_return_does_not_override_risk_or_sample_gates(candidate):
    assert not qualifies(candidate, metric())
    assert choose({"baseline": metric(), "candidate": candidate}) is None


def test_less_negative_is_not_a_profitable_improvement():
    assert not qualifies(metric(-0.01), metric(-0.02))


@pytest.mark.parametrize("key,value", [("actual_start", "2023-06-01 00:00:00"),
                                      ("actual_end", "2024-01-01 00:00:00"),
                                      ("starting_balance", 20000)])
def test_unfair_comparison_rejected(key, value):
    candidate = metric(0.10)
    candidate[key] = value
    with pytest.raises(ValueError, match="Unfair comparison"):
        qualifies(candidate, metric())


def test_accounting_failure_and_absolute_drawdown_cap_rejected():
    candidate = metric(0.10)
    candidate["integrity"]["passed"] = False
    assert not qualifies(candidate, metric())
    assert not qualifies(metric(0.10, dd=0.11), metric(0.05, dd=0.10))



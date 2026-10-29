import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import dryrun
from scripts.dryrun import healthy, validate


@pytest.fixture
def config():
    return json.loads(Path("user_data/config.paper-long-only.json").read_text())


def test_selected_profile_is_credential_free(config):
    validate(config, {})


def test_research_baseline_is_not_available_to_paper_service():
    baseline = json.loads(Path("user_data/config.json").read_text())
    with pytest.raises(ValueError, match="Only approved Donchian long-only"):
        validate(baseline, {})


def test_long_only_profile_matches_accepted_candidate():
    config = json.loads(Path("user_data/config.paper-long-only.json").read_text())
    accepted = json.loads(Path(
        "user_data/research/return_revision_20261028/candidate_config.json"
    ).read_text())
    assert config == accepted
    validate(config, {})


@pytest.mark.parametrize("override", [
    {"long_only": False}, {"long_only": 1}, {"adx_filter": True},
    {"risk_per_trade": 0.01},
])
def test_unapproved_return_profile_rejected(config, override):
    config["strategy"] = "DonchianReturnStrategy"
    config["research"].update(long_only=True, adx_filter=False)
    config["research"].update(override)
    with pytest.raises(ValueError, match="approved long-only"):
        validate(config, {})


def test_unknown_paper_strategy_rejected(config):
    config["strategy"] = "SomeOtherStrategy"
    with pytest.raises(ValueError, match="approved Donchian"):
        validate(config, {})


def test_prepare_explicit_long_only_profile_and_records_both_sources(tmp_path, monkeypatch):
    config = json.loads(Path("user_data/config.paper-long-only.json").read_text())
    user = tmp_path / "user_data"
    (user / "strategies").mkdir(parents=True)
    (user / "config.paper-long-only.json").write_text(json.dumps(config))
    for name in ("Donchian4HStrategy", "DonchianReturnStrategy"):
        (user / f"strategies/{name}.py").write_text(f"# {name}\n")
    monkeypatch.setattr(dryrun, "ROOT", tmp_path)
    monkeypatch.setattr(dryrun, "RUNTIME", user / "dryrun")
    monkeypatch.setattr(dryrun.os, "environ", {})
    dryrun.prepare()
    runtime = json.loads((user / "dryrun/config.json").read_text())
    session = json.loads((user / "dryrun/sessions.jsonl").read_text())
    assert runtime["strategy"] == "DonchianReturnStrategy"
    assert runtime["research"]["long_only"] is True
    assert session["strategy_sha256"] != session["base_strategy_sha256"]
    assert session["profile"] == "long-only"


def test_removed_profile_is_rejected_before_creating_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(dryrun, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="Only the long-only"):
        dryrun.prepare("baseline")
    assert not (tmp_path / "user_data").exists()


@pytest.mark.parametrize("key", ["key", "secret", "password", "uid", "account_id"])
def test_credentials_are_rejected(config, key):
    config["exchange"][key] = "not-a-real-credential"
    with pytest.raises(ValueError, match="credentials"):
        validate(config, {})


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_live_or_ambiguous_mode_rejected(config, value):
    config["dry_run"] = value
    with pytest.raises(ValueError, match="dry_run"):
        validate(config, {})


def test_environment_and_config_injection_rejected(config):
    with pytest.raises(ValueError, match="environment"):
        validate(config, {"FREQTRADE__DRY_RUN": "false"})
    config["add_config_files"] = ["live.json"]
    with pytest.raises(ValueError, match="Additional"):
        validate(config, {})


def test_health_requires_recent_running_heartbeat(tmp_path):
    log = tmp_path / "bot.log"
    now = datetime(2026, 10, 20, 12, tzinfo=UTC)
    assert not healthy(log, now)

    for age, state, expected in [(30, "RUNNING", True), (181, "RUNNING", False),
                                 (10, "STOPPED", False), (-30, "RUNNING", False)]:
        stamp = (now - timedelta(seconds=age)).strftime("%Y-%m-%d %H:%M:%S")
        log.write_text(f"{stamp},000 - freqtrade.worker - INFO - Bot heartbeat. "
                       f"PID=1, version=2026.9, state='{state}'\n")
        assert healthy(log, now) is expected
    log.write_text("ordinary log activity\n")
    assert not healthy(log, now)


def test_prepare_isolates_runtime_and_refuses_changed_config(tmp_path, monkeypatch, config):
    user = tmp_path / "user_data"
    (user / "strategies").mkdir(parents=True)
    (user / "config.paper-long-only.json").write_text(json.dumps(config))
    (user / "strategies/Donchian4HStrategy.py").write_text("# frozen strategy\n")
    (user / "strategies/DonchianReturnStrategy.py").write_text("# selected strategy\n")
    monkeypatch.setattr(dryrun, "ROOT", tmp_path)
    monkeypatch.setattr(dryrun, "RUNTIME", user / "dryrun")
    monkeypatch.setattr(dryrun.os, "environ", {})
    dryrun.prepare()
    runtime = json.loads((user / "dryrun/config.json").read_text())
    assert runtime["dry_run"] is True
    assert runtime["initial_state"] == "running"
    assert runtime["db_url"].endswith("/user_data/dryrun/trades.sqlite")
    assert json.loads((user / "config.paper-long-only.json").read_text()) == config
    runtime["dry_run"] = False
    (user / "dryrun/config.json").write_text(json.dumps(runtime))
    with pytest.raises(ValueError, match="differs"):
        dryrun.prepare()


def test_stopped_container_cannot_report_healthy(monkeypatch):
    monkeypatch.setattr(dryrun, "healthy", lambda _: True)
    monkeypatch.setattr(
        dryrun.subprocess, "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout='{"Service":"paper","State":"exited","Health":""}\n',
        ),
    )
    assert dryrun.main("dryrun-status") == 1

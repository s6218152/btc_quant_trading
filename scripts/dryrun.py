"""Credential-free paper trading, isolated from frozen research artifacts."""

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "user_data/dryrun"
COMPOSE = ["docker", "compose", "-p", "btc-paper", "-f", "compose.dryrun.yaml"]


def validate(config: dict, environ: dict) -> None:
    if any(key.startswith("FREQTRADE__") for key in environ):
        raise ValueError("FREQTRADE__ environment overrides are forbidden")
    exchange = config.get("exchange", {})
    if config.get("dry_run") is not True:
        raise ValueError("Only dry_run=true is permitted")
    if any(exchange.get(key) for key in ("key", "secret", "password", "uid", "account_id")):
        raise ValueError("Exchange credentials are forbidden")
    if config.get("add_config_files"):
        raise ValueError("Additional configuration files are forbidden")
    if config.get("strategy") != "Donchian4HStrategy":
        raise ValueError("Only the baseline Donchian strategy is permitted")
    if exchange.get("name") != "binance" or exchange.get("pair_whitelist") != ["BTC/USDT:USDT"]:
        raise ValueError("Only Binance BTC perpetual paper trading is permitted")
    if config.get("api_server", {}).get("enabled") or config.get("telegram", {}).get("enabled"):
        raise ValueError("Remote control is disabled")


def prepare() -> None:
    config = json.loads((ROOT / "user_data/config.json").read_text())
    validate(config, os.environ)
    config.update(
        initial_state="running",
        db_url="sqlite:////freqtrade/user_data/dryrun/trades.sqlite",
        logfile="/freqtrade/user_data/dryrun/bot.log",
        internals={"process_throttle_secs": 5, "heartbeat_interval": 30},
    )
    RUNTIME.mkdir(parents=True, exist_ok=True)
    target = RUNTIME / "config.json"
    # Avoid changing configuration under a running bot: stop before editing it.
    payload = json.dumps(config, indent=2) + "\n"
    if target.exists() and target.read_text() != payload:
        raise ValueError("Runtime config differs; stop the bot and archive config.json first")
    target.write_text(payload)
    manifest = {
        "started_at": datetime.now(UTC).isoformat(),
        "purpose": "prospective operational paper trading; not holdout evaluation",
        "config_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "strategy_sha256": hashlib.sha256(
            (ROOT / "user_data/strategies/Donchian4HStrategy.py").read_bytes()
        ).hexdigest(),
    }
    with (RUNTIME / "sessions.jsonl").open("a") as handle:
        handle.write(json.dumps(manifest) + "\n")


def healthy(log: Path, now: datetime | None = None) -> bool:
    """A recent RUNNING heartbeat is required; file writes alone are insufficient."""
    if not log.exists():
        return False
    with log.open("rb") as handle:
        handle.seek(max(0, log.stat().st_size - 65536))
        lines = handle.read().decode(errors="replace").splitlines()
    for line in reversed(lines):
        if "Bot heartbeat" in line:
            match = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", line)
            if not match or "RUNNING" not in line:
                return False
            stamp = datetime.strptime(match[1], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            age = ((now or datetime.now(UTC)) - stamp).total_seconds()
            return 0 <= age <= 180
    return False


def main(action: str) -> int:
    if action == "dryrun-start":
        validate(json.loads((ROOT / "user_data/config.json").read_text()), os.environ)
        # The runtime guard also checks the effective config inside the container.
        prepare()
        return subprocess.run([*COMPOSE, "up", "-d"], cwd=ROOT).returncode
    if action == "dryrun-stop":
        return subprocess.run([*COMPOSE, "stop", "-t", "120"], cwd=ROOT).returncode
    if action == "dryrun-status":
        result = subprocess.run(
            [*COMPOSE, "ps", "--all", "--format", "json"], cwd=ROOT,
            capture_output=True, text=True,
        )
        if result.returncode:
            print(result.stderr, file=sys.stderr)
            return result.returncode
        rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        # Compose versions may emit an array instead of JSON lines.
        if len(rows) == 1 and isinstance(rows[0], list):
            rows = rows[0]
        running = any(row.get("Service") == "paper" and row.get("State") == "running"
                      for row in rows)
        for row in rows:
            print(f"{row['Service']}: {row['State']}; health={row.get('Health', '')}")
        ready = running and healthy(RUNTIME / "bot.log")
        print("Heartbeat:", "HEALTHY" if ready else "STOPPED / NOT READY / STALE")
        return 0 if ready else 1
    if action == "dryrun-logs":
        return subprocess.run([*COMPOSE, "logs", "--tail", "60"], cwd=ROOT).returncode
    raise ValueError(f"Unknown action: {action}")


if __name__ == "__main__":
    if sys.argv[1:] == ["health"]:
        raise SystemExit(0 if healthy(RUNTIME / "bot.log") else 1)
    if sys.argv[1:] == ["run"]:
        path = RUNTIME / "config.json"
        config = json.loads(path.read_text())
        validate(config, os.environ)
        if (secret := RUNTIME / "telegram.json").exists():
            from paper_notify import run

            run(config, secret)
            raise SystemExit(0)
        os.execvp("freqtrade", ["freqtrade", "trade", "--config", str(path)])
    raise SystemExit(main(sys.argv[1]))

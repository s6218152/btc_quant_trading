"""Outbound-only Telegram notifications through native Freqtrade webhook events."""

import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path


def load_secret(path: Path) -> dict:
    secret = json.loads(path.read_text())
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", secret.get("token", "")):
        raise ValueError("Invalid Telegram bot token format (value withheld)")
    if not re.fullmatch(r"-?\d+", str(secret.get("chat_id", ""))):
        raise ValueError("Telegram recipient must be a numeric chat ID")
    return secret


def webhook_config(secret: dict) -> dict:
    templates = {
        "status": "服務狀態：{status}",
        "warning": "警告：{status}",
        "exception": "引擎例外：{status}",
        "entry_fill": "模擬進場 #{trade_id}\n{pair} {direction}\n成交價：{open_rate}",
        "exit_fill": "模擬出場 #{trade_id}\n{pair} {direction}\n成交價：{close_rate}\n"
                     "損益：{profit_amount} {stake_currency}\n原因：{exit_reason}",
    }
    return {
        "enabled": True,
        "url": f"https://api.telegram.org/bot{secret['token']}/sendMessage",
        "format": "json",
        "timeout": 5,
        "retries": 1,
        "retry_delay": 1,
        **{event: {"chat_id": str(secret["chat_id"]), "text": f"[BTC 模擬交易]\n{text}"}
           for event, text in templates.items()},
    }


def redact_factory(previous, token: str):
    """Redact before handlers see messages, including requests' URL errors."""
    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        record.msg = record.getMessage().replace(token, "[REDACTED]")
        record.args = ()
        return record
    return factory


def run(config: dict, path: Path) -> None:
    from freqtrade.main import main

    secret = load_secret(path)
    config["webhook"] = webhook_config(secret)
    logging.setLogRecordFactory(redact_factory(logging.getLogRecordFactory(), secret["token"]))
    # Never put notification credentials in the tracked config, argv, or host logs.
    fd, filename = tempfile.mkstemp(prefix="paper-notify-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(config, handle)
        sys.argv = ["freqtrade", "trade", "--config", filename]
        main()
    finally:
        Path(filename).unlink(missing_ok=True)

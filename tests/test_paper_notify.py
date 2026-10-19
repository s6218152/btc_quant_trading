import json
import logging

import pytest
from freqtrade.enums import RPCMessageType
from freqtrade.rpc.webhook import Webhook

from scripts.paper_notify import load_secret, redact_factory, webhook_config


def test_secret_validation_does_not_expose_values(tmp_path):
    p = tmp_path / "secret.json"
    p.write_text(json.dumps({"token": "sensitive-invalid-value", "chat_id": "1"}))
    with pytest.raises(ValueError, match="withheld") as error:
        load_secret(p)
    assert "sensitive-invalid-value" not in str(error.value)


def test_logs_redact_url_credentials():
    factory = redact_factory(logging.getLogRecordFactory(), "123:fake")
    record = factory("requests", logging.WARNING, "", 1, "Failure %s",
                     ("https://api.telegram.org/bot123:fake/sendMessage",), None)
    assert "123:fake" not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()


@pytest.mark.parametrize("kind,fields", [
    ("status", {"status": "running"}),
    ("warning", {"status": "Dry run is enabled"}),
    ("exception", {"status": "test exception"}),
    ("entry_fill", {"trade_id": 1, "pair": "BTC/USDT:USDT", "direction": "Long",
                    "open_rate": 100}),
    ("exit_fill", {"trade_id": 1, "pair": "BTC/USDT:USDT", "direction": "Long",
                   "close_rate": 110, "profit_amount": 10, "stake_currency": "USDT",
                   "exit_reason": "test"}),
])
def test_templates_work_with_pinned_native_webhook(kind, fields):
    wh = object.__new__(Webhook)
    wh._config = {"webhook": webhook_config({"token": "123:fake", "chat_id": "456"})}
    messages = []
    wh._send_msg = messages.append
    wh.send_msg({"type": RPCMessageType(kind), **fields})
    assert len(messages) == 1
    assert messages[0]["chat_id"] == "456"
    assert messages[0]["text"].startswith("[BTC 模擬交易]")


def test_no_remote_telegram_commands_enabled():
    assert "telegram" not in webhook_config({"token": "123:fake", "chat_id": "456"})

"""Freeze public exchange execution rules; reject silent rule drift on reruns."""

import hashlib
import json
from pathlib import Path

import ccxt

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    market = exchange.load_markets()["BTC/USDT:USDT"]
    rules = {
        k: market[k]
        for k in [
            "id",
            "symbol",
            "active",
            "contractSize",
            "linear",
            "inverse",
            "precision",
            "limits",
        ]
    }
    snapshot = ROOT / "user_data/research/market_rules.json"
    if snapshot.exists():
        if json.loads(snapshot.read_text())["rules"] != rules:
            raise SystemExit("Exchange rules changed: freeze a NEW study before rerunning")
        print("Exchange execution rules match frozen snapshot")
    else:
        data = {
            "ccxt_version": ccxt.__version__,
            "source": "Binance USDT-M public exchangeInfo",
            "rules": rules,
        }
        snapshot.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        print("Exchange execution rules snapshot saved")
    print("Rules SHA256:", hashlib.sha256(snapshot.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()

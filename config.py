import os
from dotenv import load_dotenv

# 載入 .env 檔案
load_dotenv()

# --- API Keys ---
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")

# --- Telegram Notification Settings ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_USER_ID = os.getenv("TELEGRAM_USER_ID", "")

# --- Trading Strategy Settings (CTA) ---
# 測試模式 (Dry Run)：開啟時不發送真實交易請求，只在本地記錄與模擬
DRY_RUN = True

# 交易對
SYMBOL = "BTCUSDT"
# 確保我們交易的是 USDT 本位合約
IS_FUTURES = True 

# K 線時間框架 (例如: '15m', '1h', '4h', '1d')
TIMEFRAME = "1h"

# 槓桿倍數 (BTC 流動性好，但仍建議低槓桿，如 1x-3x)
LEVERAGE = 1

# 單次開倉金額 (USDT)
TRADE_AMOUNT_USDT = 100.0

# --- File Paths ---
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
TRADE_LOG_FILE = os.path.join(LOG_DIR, "trade_history.csv")
SYSTEM_LOG_FILE = os.path.join(LOG_DIR, "system.log")

# 確保 log 資料夾存在
os.makedirs(LOG_DIR, exist_ok=True)

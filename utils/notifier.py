import urllib.request
import urllib.parse
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID

def send_telegram_message(message: str, parse_mode: str = None) -> bool:
    """發送 Telegram 通知"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_USER_ID:
        print("⚠️ Telegram 通知未配置 (TELEGRAM_USER_ID 或 TELEGRAM_BOT_TOKEN 未在 .env 中設定)")
        return False
        
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_USER_ID, "text": message}
        if parse_mode:
            payload['parse_mode'] = parse_mode
            
        data = urllib.parse.urlencode(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data)
        
        with urllib.request.urlopen(req) as response:
            return response.getcode() == 200
            
    except Exception as e:
        print(f"❌ 發送 Telegram 通知失敗: {e}")
        return False

def send_alert(message: str):
    """發送緊急/錯誤警報"""
    full_message = f"🚨 [BTC 量化機器人警報] {message}"
    print(full_message)
    send_telegram_message(full_message)

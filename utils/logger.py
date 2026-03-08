import logging
import os
import csv
from datetime import datetime
from logging.handlers import RotatingFileHandler
from config import SYSTEM_LOG_FILE, TRADE_LOG_FILE

def setup_logger(name: str) -> logging.Logger:
    """設定系統日誌記錄器"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # 防止重複添加 handler
    if not logger.handlers:
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        # 輸出到控制台
        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        logger.addHandler(ch)
        
        # 輸出到檔案 (使用自動輪替機制：最大 5MB，保留最新 5 份紀錄檔)
        fh = RotatingFileHandler(SYSTEM_LOG_FILE, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        
    return logger

def log_trade(action: str, symbol: str, price: float, quantity: float, usdt_value: float, message: str = "", pnl: float = 0.0):
    """將交易記錄寫入 CSV 檔案"""
    file_exists = os.path.exists(TRADE_LOG_FILE)
    
    with open(TRADE_LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Timestamp', 'Action', 'Symbol', 'Price', 'Quantity', 'USDT_Value', 'PnL', 'Message'])
            
        writer.writerow([
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            action,
            symbol,
            price,
            quantity,
            usdt_value,
            pnl,
            message
        ])

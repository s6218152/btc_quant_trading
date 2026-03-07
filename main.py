import time
import schedule
import json
import os
from config import BINANCE_API_KEY, BINANCE_SECRET_KEY, IS_FUTURES, SYMBOL, TIMEFRAME, LEVERAGE, TRADE_AMOUNT_USDT, DRY_RUN
from core.exchange import BinanceExchange
from strategies.cta_macd_strategy import MACDTrendStrategy
from utils.logger import setup_logger, log_trade
from utils.notifier import send_telegram_message, send_alert

logger = setup_logger("Main")

SIMULATED_POS_FILE = "simulated_position.json"

def get_simulated_position(symbol):
    if os.path.exists(SIMULATED_POS_FILE):
        try:
            with open(SIMULATED_POS_FILE, "r") as f:
                data = json.load(f)
                return data.get(symbol, {'positionAmt': 0.0, 'entryPrice': 0.0, 'unRealizedProfit': 0.0, 'side': ''})
        except Exception as e:
            logger.error(f"讀取模擬倉位失敗: {e}")
    return {'positionAmt': 0.0, 'entryPrice': 0.0, 'unRealizedProfit': 0.0, 'side': ''}

def update_simulated_position(symbol, amount, price, side):
    data = {}
    if os.path.exists(SIMULATED_POS_FILE):
        try:
            with open(SIMULATED_POS_FILE, "r") as f:
                data = json.load(f)
        except Exception:
            pass
    
    data[symbol] = {
        'positionAmt': amount,
        'entryPrice': price,
        'unRealizedProfit': 0.0,
        'side': side
    }
    try:
        with open(SIMULATED_POS_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"寫入模擬倉位失敗: {e}")

def execute_bot():
    """主要執行邏輯 (每次觸發時執行)"""
    logger.info("=".center(50, "="))
    mode_str = "[開發/模擬模式 DRY_RUN=True]" if DRY_RUN else "[🔥實盤交易模式🔥]"
    logger.info(f"開始執行量化檢查 {mode_str} - 交易對: {SYMBOL}")
    
    try:
        # 1. 初始化交易所連線
        exchange = BinanceExchange(
            api_key=BINANCE_API_KEY,
            secret_key=BINANCE_SECRET_KEY,
            is_futures=IS_FUTURES
        )
        
        # 2. 初始化策略 (傳入設定參數)
        config_params = {
            'SYMBOL': SYMBOL,
            'TIMEFRAME': TIMEFRAME,
            'LEVERAGE': LEVERAGE,
            'TRADE_AMOUNT_USDT': TRADE_AMOUNT_USDT
        }
        strategy = MACDTrendStrategy(exchange, config_params)
        
        # 3. 獲取資料並執行策略判斷
        df = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME)
        if df is None:
            logger.error("無法取得 K 線資料，略過本次執行")
            return
            
        if DRY_RUN:
            current_pos = get_simulated_position(SYMBOL)
        else:
            current_pos = exchange.get_position(SYMBOL)
            
        action = strategy.check_entry_exit(strategy.generate_signals(df), current_pos)
        
        logger.info(f"產出訊號: [{action.upper()}], 目前倉位: {current_pos['positionAmt']}")
        
        # 4. 根據訊號執行交易
        if action == 'buy':
            if current_pos['positionAmt'] < 0:
                # 處於空手狀態，先平空倉
                logger.info(f"訊號轉向，準備平空單...")
                if DRY_RUN:
                    logger.info("[模擬] 已執行市價平空單")
                    update_simulated_position(SYMBOL, 0.0, 0.0, '')
                else:
                    exchange.close_position(SYMBOL)
                msg = f"🟢 [模擬平倉轉向] 已平空單 {SYMBOL} | 模擬平倉價: {exchange.get_current_price(SYMBOL)}" if DRY_RUN else f"🟢 [平倉轉向] 已平空單 {SYMBOL}"
                send_telegram_message(msg)
                
            if current_pos['positionAmt'] <= 0:
                 # 開多倉
                logger.info(f"執行作多...")
                if DRY_RUN:
                    current_price = exchange.get_current_price(SYMBOL)
                    sim_amt = TRADE_AMOUNT_USDT / current_price if current_price else 0.001
                    logger.info(f"[模擬] 成功作多 {SYMBOL} | 模擬價格: {current_price} | 金額: {TRADE_AMOUNT_USDT} USDT")
                    msg = f"🚀 [模擬作多進場] {SYMBOL} | 價格: {current_price} | 約 {TRADE_AMOUNT_USDT} USDT"
                    send_telegram_message(msg)
                    log_trade("Simulate_Open_Long", SYMBOL, current_price, sim_amt, TRADE_AMOUNT_USDT, "DRY_RUN")
                    update_simulated_position(SYMBOL, sim_amt, current_price, 'long')
                else:
                    order = exchange.create_market_order(SYMBOL, 'buy', TRADE_AMOUNT_USDT)
                    if order:
                        exec_price = order.get('price', exchange.get_current_price(SYMBOL))
                        msg = f"🚀 [作多進場] {SYMBOL} | 價格: {exec_price} | 約 {TRADE_AMOUNT_USDT} USDT"
                        send_telegram_message(msg)
                        log_trade("Open_Long", SYMBOL, exec_price, order.get('amount', 0), TRADE_AMOUNT_USDT, f"ID:{order.get('id')}")

        elif action == 'sell':
            if current_pos['positionAmt'] > 0:
                # 處於多單狀態，先平多倉
                logger.info(f"訊號轉向，準備平多單...")
                if DRY_RUN:
                    logger.info("[模擬] 已執行市價平多單")
                    update_simulated_position(SYMBOL, 0.0, 0.0, '')
                else:
                    exchange.close_position(SYMBOL)
                msg = f"🔴 [模擬平倉轉向] 已平多單 {SYMBOL} | 模擬平倉價: {exchange.get_current_price(SYMBOL)}" if DRY_RUN else f"🔴 [平倉轉向] 已平多單 {SYMBOL}"
                send_telegram_message(msg)
                
            if current_pos['positionAmt'] >= 0:
                 # 開空倉
                logger.info(f"執行作空...")
                if DRY_RUN:
                    current_price = exchange.get_current_price(SYMBOL)
                    sim_amt = -(TRADE_AMOUNT_USDT / current_price) if current_price else -0.001
                    logger.info(f"[模擬] 成功作空 {SYMBOL} | 模擬價格: {current_price} | 金額: {TRADE_AMOUNT_USDT} USDT")
                    msg = f"📉 [模擬作空進場] {SYMBOL} | 價格: {current_price} | 約 {TRADE_AMOUNT_USDT} USDT"
                    send_telegram_message(msg)
                    log_trade("Simulate_Open_Short", SYMBOL, current_price, sim_amt, TRADE_AMOUNT_USDT, "DRY_RUN")
                    update_simulated_position(SYMBOL, sim_amt, current_price, 'short')
                else:
                    order = exchange.create_market_order(SYMBOL, 'sell', TRADE_AMOUNT_USDT)
                    if order:
                        exec_price = order.get('price', exchange.get_current_price(SYMBOL))
                        msg = f"📉 [作空進場] {SYMBOL} | 價格: {exec_price} | 約 {TRADE_AMOUNT_USDT} USDT"
                        send_telegram_message(msg)
                        log_trade("Open_Short", SYMBOL, exec_price, order.get('amount', 0), TRADE_AMOUNT_USDT, f"ID:{order.get('id')}")
        
        else: # hold
            logger.info("維持現狀，無交易動作。")
            
    except Exception as e:
        error_msg = f"執行迴圈發生未預期錯誤: {e}"
        logger.error(error_msg)
        send_alert(error_msg)

def main():
    send_telegram_message(f"✅ BTC 量化機器人 (CTA - MACD) 已啟動！\n監控: {SYMBOL} ({TIMEFRAME})")
    logger.info("系統啟動，開始排程監聽...")
    
    # 立即執行第一次
    execute_bot()
    
    # 設定排程器：每分鐘檢查一次 (若您的 Timeline 是 1h，其實可以每小時檢查，這裡以 1 分鐘為例演示高頻度檢測)
    # 若要準確對齊 K 線收盤，應在每小時的 00 份執行
    # schedule.every().hour.at(":00").do(execute_bot)
    schedule.every(5).minutes.do(execute_bot)
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("機器人已手動停止")
        send_telegram_message("🛑 BTC 量化機器人已停止運作")

if __name__ == "__main__":
    main()

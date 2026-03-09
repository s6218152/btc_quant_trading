import os
import pandas as pd
from datetime import datetime
from config import BINANCE_API_KEY, BINANCE_SECRET_KEY, IS_FUTURES, SYMBOL, TIMEFRAME, TRADE_AMOUNT_USDT
from core.exchange import BinanceExchange
from strategies.cta_harmonic_strategy import HarmonicPatternStrategy
from strategies.multi_strategy import MultiStrategyCombiner
import logging
import ccxt

# 關閉多餘的 Log 輸出，避免洗版
logging.basicConfig(level=logging.WARNING)

def fetch_historical_data(exchange_wrapper, symbol, timeframe, start_str, end_str):
    """抓取指定區間的歷史 K 線資料"""
    print(f"正在下載 {symbol} {timeframe} 從 {start_str} 到 {end_str} 的歷史資料...")
    start_ts = exchange_wrapper.exchange.parse8601(start_str)
    end_ts = exchange_wrapper.exchange.parse8601(end_str)
    
    all_ohlcv = []
    
    # 每次最多抓取 1000 根 (Binance 限制)
    while start_ts < end_ts:
        try:
            ohlcv = exchange_wrapper.exchange.fetch_ohlcv(symbol, timeframe, since=start_ts, limit=1000)
            if not ohlcv:
                break
                
            all_ohlcv.extend(ohlcv)
            
            # 更新下一次抓取的起點
            last_ts = ohlcv[-1][0]
            if last_ts == start_ts:
                break
            start_ts = last_ts + 1
            
        except Exception as e:
            print(f"下載資料時發生錯誤: {e}")
            break
            
    df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    
    # 過濾出確實在結束時間之前的資料
    end_dt = pd.to_datetime(end_ts, unit='ms')
    df = df[df['timestamp'] <= end_dt]
    
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
        
    print(f"下載完成！共取得 {len(df)} 根 K 線。")
    return df

def run_backtest():
    # 1. 初始化 Exchange API
    exchange = BinanceExchange(BINANCE_API_KEY, BINANCE_SECRET_KEY, is_futures=IS_FUTURES)
    
    # 2. 下載 2025 年的數據 (從 2024-01-01 到現在)
    start_date = "2024-01-01T00:00:00Z"
    import datetime as dt
    end_date = datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    df = fetch_historical_data(exchange, SYMBOL, TIMEFRAME, start_date, end_date)
    
    if df.empty:
        print("無法取得歷史資料，終止回測。")
        return
        
    # 3. 初始化策略 (過半決策制)
    config_params = {
        'SYMBOL': SYMBOL,
        'TIMEFRAME': TIMEFRAME,
        'TRADE_AMOUNT_USDT': TRADE_AMOUNT_USDT
    }
    harmonic_strategy = HarmonicPatternStrategy(exchange, config_params, order_size=21, err_tolerance=0.10)
    strategy = MultiStrategyCombiner(exchange, config_params, [harmonic_strategy], mode='all', signal_memory_bars=1)
    
    # 先一次性計算所有指標，加快回測速度
    df = strategy.generate_signals(df)
    
    # 4. 開始模擬時光機 (Bar by bar)
    current_pos = {'positionAmt': 0.0, 'entryPrice': 0.0, 'side': ''}
    
    trade_history = []
    total_pnl = 0.0
    win_trades = 0
    loss_trades = 0
    
    print("\n--- 開始歷史回測 (2024-01-01 ~ 至今) ---")
    
    # 從第 50 根起跑，確保均線(如 EMA 50) 指標已經產生
    for i in range(50, len(df)):
        # 截取到當前時間 i 為止的切片 (模擬當下)
        current_slice = df.iloc[:i+1]
        close_price = current_slice.iloc[-1]['close']
        time_str = current_slice.iloc[-1]['timestamp'].strftime("%Y-%m-%d %H:%M")
        
        action = strategy.check_entry_exit(current_slice, current_pos)
        
        # 處理平倉邏輯
        if action == 'buy' and current_pos['side'] == 'short':
             # 平空倉
             pnl = (current_pos['entryPrice'] - close_price) * abs(current_pos['positionAmt'])
             total_pnl += pnl
             if pnl > 0: win_trades += 1
             else: loss_trades += 1
             trade_history.append((time_str, 'Close_Short', close_price, pnl))
             current_pos = {'positionAmt': 0.0, 'entryPrice': 0.0, 'side': ''}
             
        elif action == 'sell' and current_pos['side'] == 'long':
             # 平多倉
             pnl = (close_price - current_pos['entryPrice']) * abs(current_pos['positionAmt'])
             total_pnl += pnl
             if pnl > 0: win_trades += 1
             else: loss_trades += 1
             trade_history.append((time_str, 'Close_Long', close_price, pnl))
             current_pos = {'positionAmt': 0.0, 'entryPrice': 0.0, 'side': ''}
             
        # 處理開倉邏輯
        if current_pos['positionAmt'] == 0:
            if action == 'buy':
                amount = TRADE_AMOUNT_USDT / close_price
                current_pos = {'positionAmt': amount, 'entryPrice': close_price, 'side': 'long'}
                trade_history.append((time_str, 'Open_Long', close_price, 0))
            elif action == 'sell':
                amount = -(TRADE_AMOUNT_USDT / close_price)
                current_pos = {'positionAmt': amount, 'entryPrice': close_price, 'side': 'short'}
                trade_history.append((time_str, 'Open_Short', close_price, 0))
                
    # 5. 輸出績效報告
    print("\n===============================")
    print("📈 回測績效報告 (單一策略：和諧交易法 Gartley/Bat)")
    print("===============================")
    print(f"交易對: {SYMBOL}")
    print(f"時間級別: {TIMEFRAME}")
    print(f"每筆下單金額: {TRADE_AMOUNT_USDT} USDT")
    print(f"測試期間: {df.iloc[0]['timestamp'].strftime('%Y-%m-%d')} ~ {df.iloc[-1]['timestamp'].strftime('%Y-%m-%d')}")
    print("-------------------------------")
    
    total_trades = win_trades + loss_trades
    print(f"總平倉次數: {total_trades} 次")
    if total_trades > 0:
        win_rate = (win_trades / total_trades) * 100
        print(f"獲利次數: {win_trades} 次")
        print(f"虧損次數: {loss_trades} 次")
        print(f"勝率: {win_rate:.2f}%")
        print(f"總盈虧 (Net PnL): {total_pnl:+.2f} USDT")
    else:
        print("期間內無觸發任何平倉交易。")
        
    print("===============================\n")

if __name__ == "__main__":
    run_backtest()

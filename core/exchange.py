import ccxt
import time
import pandas as pd
from typing import Optional, Dict, List, Any
import logging

class BinanceExchange:
    """
    Wrapper for Binance API using ccxt.
    Handles both Spot and USD-M Futures.
    """
    def __init__(self, api_key: str, secret_key: str, is_futures: bool = True, testnet: bool = False):
        self.is_futures = is_futures
        
        exchange_class = ccxt.binanceusdm if is_futures else ccxt.binance
        self.exchange = exchange_class({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future' if is_futures else 'spot',
                'adjustForTimeDifference': True,
            }
        })
        
        if testnet:
            self.exchange.set_sandbox_mode(True)
            
        self.logger = logging.getLogger(__name__)

    def get_balance(self, asset: str = 'USDT') -> float:
        """獲取帳戶可用餘額"""
        try:
            balance = self.exchange.fetch_balance()
            if asset in balance['free']:
                return float(balance['free'][asset])
            return 0.0
        except Exception as e:
            self.logger.error(f"獲取餘額失敗: {e}")
            return 0.0

    def fetch_ohlcv(self, symbol: str, timeframe: str = '1h', limit: int = 200) -> Optional[pd.DataFrame]:
        """獲取 K 線歷史數據並轉為 pandas DataFrame"""
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            # 將價格轉為 float
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)
            return df
        except Exception as e:
            self.logger.error(f"獲取 K 線數據 {symbol} {timeframe} 失敗: {e}")
            return None

    def get_current_price(self, symbol: str) -> Optional[float]:
        """獲取當前市價"""
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return float(ticker['last'])
        except Exception as e:
            self.logger.error(f"獲取市價 {symbol} 失敗: {e}")
            return None

    def create_market_order(self, symbol: str, side: str, amount_usdt: float) -> Optional[Dict[str, Any]]:
        """
        按 USDT 金額下市價單
        side: 'buy' (做多/買入) 或 'sell' (做空/賣出)
        """
        try:
            # 1. 獲取當前價格以計算數量
            price = self.get_current_price(symbol)
            if not price:
                return None
            
            # 2. 獲取市場規格 (最小下單量、精度等)
            self.exchange.load_markets()
            market = self.exchange.market(symbol)
            
            # 3. 計算並格式化下單數量
            raw_qty = amount_usdt / price
            qty = self.exchange.amount_to_precision(symbol, raw_qty)
            
            self.logger.info(f"準備下單: {side} {qty} {symbol} (約 {amount_usdt} USDT)")
            
            # 4. 執行下單
            order = self.exchange.create_market_order(symbol, side, float(qty))
            return order
            
        except Exception as e:
            self.logger.error(f"下單失敗 ({side} {amount_usdt} USDT of {symbol}): {e}")
            return None

    def set_leverage(self, symbol: str, leverage: int):
        """設定合約槓桿倍數"""
        if not self.is_futures:
            return
        try:
            self.exchange.set_leverage(leverage, symbol)
            self.logger.info(f"已設定 {symbol} 槓桿為 {leverage}x")
        except Exception as e:
            self.logger.error(f"設定槓桿失敗 {symbol}: {e}")
            
    def get_position(self, symbol: str) -> Dict[str, Any]:
        """獲取特定交易對的合約倉位資訊"""
        if not self.is_futures:
            return {'positionAmt': 0.0, 'entryPrice': 0.0, 'unRealizedProfit': 0.0}
            
        try:
            positions = self.exchange.fetch_positions([symbol])
            if positions:
                pos = positions[0]
                return {
                    'positionAmt': float(pos.get('contracts', 0)) * (1 if pos.get('side') == 'long' else -1),
                    'entryPrice': float(pos.get('entryPrice', 0)),
                    'unRealizedProfit': float(pos.get('unrealizedPnl', 0)),
                    'side': pos.get('side', '') # 'long' or 'short'
                }
            return {'positionAmt': 0.0, 'entryPrice': 0.0, 'unRealizedProfit': 0.0}
        except Exception as e:
            self.logger.error(f"獲取倉位失敗 {symbol}: {e}")
            return {'positionAmt': 0.0, 'entryPrice': 0.0, 'unRealizedProfit': 0.0}

    def close_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """市價平倉"""
        try:
            pos = self.get_position(symbol)
            amt = pos['positionAmt']
            
            if amt == 0:
                self.logger.info(f"{symbol} 目前無倉位，無需平倉")
                return None
                
            side = 'sell' if amt > 0 else 'buy'
            qty = abs(amt)
            
            self.logger.info(f"準備平倉: {side} {qty} {symbol}")
            
            # 使用 reduceOnly 確保只平倉不開反向倉
            params = {'reduceOnly': True} if self.is_futures else {}
            order = self.exchange.create_market_order(symbol, side, qty, params=params)
            return order
            
        except Exception as e:
            self.logger.error(f"平倉失敗 {symbol}: {e}")
            return None

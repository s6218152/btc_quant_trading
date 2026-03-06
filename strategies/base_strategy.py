import pandas as pd
from typing import Dict, Any

class BaseStrategy:
    """
    所有量化策略的基礎類別
    """
    def __init__(self, exchange, config_params: Dict[str, Any]):
        self.exchange = exchange
        self.symbol = config_params.get('SYMBOL', 'BTCUSDT')
        self.timeframe = config_params.get('TIMEFRAME', '1h')
        self.leverage = config_params.get('LEVERAGE', 1)
        self.trade_amount_usdt = config_params.get('TRADE_AMOUNT_USDT', 100.0)
        
        # 初始化交易所設定 (例如槓桿)
        self.exchange.set_leverage(self.symbol, self.leverage)
        
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        計算技術指標並產生交易訊號
        需要在子類別中實作
        """
        raise NotImplementedError("Subclasses must implement generate_signals method")
        
    def check_entry_exit(self, df: pd.DataFrame, current_position: Dict[str, Any]) -> str:
        """
        檢查是否滿足進出場條件
        回傳: 'buy', 'sell', 'close', 或 'hold'
        需要在子類別中實作
        """
        raise NotImplementedError("Subclasses must implement check_entry_exit method")

    def execute(self) -> str:
        """
        執行策略的主要邏輯
        """
        # 1. 獲取資料
        df = self.exchange.fetch_ohlcv(self.symbol, self.timeframe)
        if df is None or df.empty:
            return "獲取資料失敗"
            
        # 2. 獲取當前倉位
        position = self.exchange.get_position(self.symbol)
        
        # 3. 計算訊號
        df = self.generate_signals(df)
        
        # 4. 判斷行動
        action = self.check_entry_exit(df, position)
        
        return action

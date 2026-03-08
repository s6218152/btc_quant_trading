import pandas as pd
from typing import Dict, Any
from .base_strategy import BaseStrategy

class RSIStrategy(BaseStrategy):
    """
    RSI (相對強弱指標) 超買超賣策略
    當 RSI 小於 30 (進入超賣區) 時，視為底部發出作多訊號 (BUY)
    當 RSI 大於 70 (進入超買區) 時，視為頂部發出作空/平倉訊號 (SELL)
    """
    
    def __init__(self, exchange, config_params: Dict[str, Any], length=14, overbought=70, oversold=30):
        super().__init__(exchange, config_params)
        self.length = length
        self.overbought = overbought
        self.oversold = oversold

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        # 使用 pandas_ta 計算 RSI
        rsi = df.ta.rsi(length=self.length)
        
        if rsi is not None:
            df[f'rsi_{self.length}'] = rsi
            
        return df
        
    def check_entry_exit(self, df: pd.DataFrame, current_position: Dict[str, Any]) -> str:
        r_col = f'rsi_{self.length}'
        
        if r_col not in df.columns or len(df) < 2:
            return 'hold'
            
        # 取得最新已完成的 K 線數值
        last_rsi = df.iloc[-1][r_col]
        
        pos_amt = current_position.get('positionAmt', 0.0)
        
        # --- 交易邏輯 ---
        if pos_amt == 0:
            if last_rsi < self.oversold:
                return 'buy'
            elif last_rsi > self.overbought:
                return 'sell'
                
        elif pos_amt > 0: # 多單平倉
            if last_rsi > self.overbought:
                return 'sell'
                
        elif pos_amt < 0: # 空單平倉
            if last_rsi < self.oversold:
                return 'buy'
                
        return 'hold'

import pandas as pd
from typing import Dict, Any
from .base_strategy import BaseStrategy

class BollingerBandsStrategy(BaseStrategy):
    """
    布林通道 (Bollinger Bands) 策略
    當價格觸及或跌破下軌 (Lower Band) 時，視為超賣發出作多訊號 (BUY)
    當價格觸及或突破上軌 (Upper Band) 時，視為超買發出作空/平倉訊號 (SELL)
    """
    
    def __init__(self, exchange, config_params: Dict[str, Any], length=20, std_dev=2.0):
        super().__init__(exchange, config_params)
        self.length = length
        self.std_dev = std_dev

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        # 使用 pandas_ta 計算 Bollinger Bands
        bbands = df.ta.bbands(length=self.length, std=self.std_dev)
        
        if bbands is not None:
            df = pd.concat([df, bbands], axis=1)
            
            # 簡化欄位名稱
            # pandas_ta 輸出的名稱大約是: BBL_20_2.0 (下軌), BBM_20_2.0 (中軌), BBU_20_2.0 (上軌)
            lower_col = [c for c in df.columns if c.startswith('BBL_')][0]
            upper_col = [c for c in df.columns if c.startswith('BBU_')][0]
            
            df['bb_lower'] = df[lower_col]
            df['bb_upper'] = df[upper_col]
            
        return df
        
    def check_entry_exit(self, df: pd.DataFrame, current_position: Dict[str, Any]) -> str:
        if 'bb_lower' not in df.columns or 'bb_upper' not in df.columns or len(df) < 2:
            return 'hold'
            
        # 取得最新已完成的 K 線收盤價
        last_close = df.iloc[-1]['close']
        last_lower = df.iloc[-1]['bb_lower']
        last_upper = df.iloc[-1]['bb_upper']
        
        pos_amt = current_position.get('positionAmt', 0.0)
        
        # --- 交易邏輯 ---
        if pos_amt == 0:
            if last_close <= last_lower:
                return 'buy'
            elif last_close >= last_upper:
                return 'sell'
                
        elif pos_amt > 0: # 多單平倉
            if last_close >= last_upper:
                return 'sell'
                
        elif pos_amt < 0: # 空單平倉
            if last_close <= last_lower:
                return 'buy'
                
        return 'hold'

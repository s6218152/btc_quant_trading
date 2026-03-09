import pandas as pd
import pandas_ta_classic as ta
from typing import Dict, Any
from .base_strategy import BaseStrategy

class EMACrossStrategy(BaseStrategy):
    """
    EMA (指數移動平均線) 雙均線交叉策略
    當 短線 (EMA 10) 向上交叉 長線 (EMA 50) 時做多 (金叉)
    當 短線 (EMA 10) 向下交叉 長線 (EMA 50) 時做空或平倉 (死叉)
    """
    
    def __init__(self, exchange, config_params: Dict[str, Any], short_window=10, long_window=50):
        super().__init__(exchange, config_params)
        self.short_window = short_window
        self.long_window = long_window

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        # 使用 pandas_ta 計算 EMA
        ema_short = df.ta.ema(length=self.short_window)
        ema_long = df.ta.ema(length=self.long_window)
        
        if ema_short is not None and ema_long is not None:
            df[f'ema_short_{self.short_window}'] = ema_short
            df[f'ema_long_{self.long_window}'] = ema_long
            
        return df
        
    def check_entry_exit(self, df: pd.DataFrame, current_position: Dict[str, Any]) -> str:
        s_col = f'ema_short_{self.short_window}'
        l_col = f'ema_long_{self.long_window}'
        
        if s_col not in df.columns or l_col not in df.columns or len(df) < 2:
            return 'hold'
            
        # 取得最新已完成的 K 線和前一根
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]
        
        pos_amt = current_position.get('positionAmt', 0.0)
        
        # 判斷金叉與死叉 (為了避免連續觸發，必須是「剛發生」的交叉)
        gold_cross = (prev_row[s_col] <= prev_row[l_col]) and (last_row[s_col] > last_row[l_col])
        dead_cross = (prev_row[s_col] >= prev_row[l_col]) and (last_row[s_col] < last_row[l_col])
        
        # --- 交易邏輯 ---
        if pos_amt == 0:
            if gold_cross:
                return 'buy'
            elif dead_cross:
                return 'sell'
                
        elif pos_amt > 0:
            if dead_cross:
                return 'sell'
                
        elif pos_amt < 0:
            if gold_cross:
                return 'buy'
                
        return 'hold'

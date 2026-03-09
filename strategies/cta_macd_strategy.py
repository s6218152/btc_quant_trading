import pandas as pd
import pandas_ta_classic as ta
from typing import Dict, Any
from .base_strategy import BaseStrategy

class MACDTrendStrategy(BaseStrategy):
    """
    簡單的 MACD 趨勢跟蹤策略
    當 MACD 線向上交叉信號線 (金叉) 且柱狀圖大於 0 時做多
    當 MACD 線向下交叉信號線 (死叉) 且柱狀圖小於 0 時做空 (或平多倉)
    """
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        # 使用 pandas_ta 計算 MACD (預設參數: 12, 26, 9)
        macd = df.ta.macd(fast=12, slow=26, signal=9)
        
        if macd is not None:
            # pandas_ta 會產生以底線分隔的欄位名，我們將其合併回原 df
            df = pd.concat([df, macd], axis=1)
            
            # 簡化欄位名稱方便後續使用
            # 通常名稱會是 MACD_12_26_9, MACDh_12_26_9 (Histogram), MACDs_12_26_9 (Signal)
            macd_col = [c for c in df.columns if c.startswith('MACD_')][0]
            sig_col = [c for c in df.columns if c.startswith('MACDs_')][0]
            hist_col = [c for c in df.columns if c.startswith('MACDh_')][0]
            
            df['macd_line'] = df[macd_col]
            df['macd_signal'] = df[sig_col]
            df['macd_hist'] = df[hist_col]
            
        return df
        
    def check_entry_exit(self, df: pd.DataFrame, current_position: Dict[str, Any]) -> str:
        if 'macd_line' not in df.columns or len(df) < 2:
            return 'hold'
            
        # 取得最新已完成的 K 線 (通常是倒數第二根，最後一根可能還在變動)
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]
        
        pos_amt = current_position.get('positionAmt', 0.0)
        
        # 判斷金叉與死叉 (為了避免連續觸發，必須是「剛發生」的交叉)
        gold_cross = (prev_row['macd_line'] <= prev_row['macd_signal']) and (last_row['macd_line'] > last_row['macd_signal'])
        dead_cross = (prev_row['macd_line'] >= prev_row['macd_signal']) and (last_row['macd_line'] < last_row['macd_signal'])
        
        # --- 交易邏輯 ---
        
        if pos_amt == 0:
            if gold_cross and last_row['macd_hist'] > 0:
                return 'buy'
            elif dead_cross and last_row['macd_hist'] < 0:
                return 'sell'
                
        elif pos_amt > 0:
            if dead_cross:
                return 'sell'
                
        elif pos_amt < 0:
            if gold_cross:
                return 'buy'
                
        return 'hold'

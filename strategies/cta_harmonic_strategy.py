import pandas as pd
import numpy as np
from typing import Dict, Any, List, Tuple
from .base_strategy import BaseStrategy
from scipy.signal import argrelextrema

class HarmonicPatternStrategy(BaseStrategy):
    """
    和諧交易法 (Harmonic Trading) 策略
    透過尋找 K 線的局部高低點 (Swing High/Low) 來定義 X, A, B, C, D 五個轉折點。
    並計算這五個點的斐波那契回撤比例，判斷是否符合 Gartley 或 Bat 等型態。
    """
    
    def __init__(self, exchange, config_params: Dict[str, Any], order_size=21, err_tolerance=0.10):
        super().__init__(exchange, config_params)
        self.order_size = order_size # 尋找局部極值時的左右視窗大小 (越大過濾掉越小的雜訊)
        self.err_tolerance = err_tolerance # 斐波那契比例的容錯範圍 (放寬為 10%)
        self.last_traded_d_idx = -1 # 紀錄上一次觸發交易的 D 點索引，避免同一個型態連續進場
        
        # 追蹤停利相關變數
        self.highest_since_entry = 0.0
        self.lowest_since_entry = float('inf')
        self.trailing_stop_pct = 0.02 # 從最高點回撤 2% 也就是破線平倉

    def get_extrema(self, data: pd.Series, order: int) -> Tuple[List[int], List[int]]:
        """找出序列的局部高點與低點索引"""
        local_max = argrelextrema(data.values, np.greater_equal, order=order)[0]
        local_min = argrelextrema(data.values, np.less_equal, order=order)[0]
        return list(local_max), list(local_min)

    def is_valid_ratio(self, actual: float, target: float) -> bool:
        """判斷實際比例是否落在目標比例的容錯範圍內"""
        return abs(actual - target) <= self.err_tolerance

    def check_pattern(self, points: List[float], pattern_type: str) -> bool:
        """
        給定 5 個價格點 [X, A, B, C, D]，判斷是否符合指定的和諧型態比例
        回傳: 是否符合 (True/False)
        """
        if len(points) != 5:
            return False
            
        X, A, B, C, D = points
        
        # 確保有明顯的波段方向以免除零錯誤
        if X == A or A == B or B == C or C == D:
            return False

        # 計算斐波那契回撤比例
        try:
            XA = abs(X - A)
            AB = abs(A - B)
            BC = abs(B - C)
            CD = abs(C - D)
            AD = abs(A - D) # 對於 XA 的總回撤
            
            ab_xa = AB / XA
            bc_ab = BC / AB
            cd_bc = CD / BC
            ad_xa = AD / XA
        except ZeroDivisionError:
            return False

        # 驗證 Gartley Pattern
        if pattern_type == 'Gartley':
            # 理想比例: AB=0.618 XA, BC=0.382-0.886 AB, CD=1.272-1.618 BC, AD=0.786 XA
            if self.is_valid_ratio(ab_xa, 0.618):
                if 0.382 - self.err_tolerance <= bc_ab <= 0.886 + self.err_tolerance:
                    if 1.272 - self.err_tolerance <= cd_bc <= 1.618 + self.err_tolerance:
                        if self.is_valid_ratio(ad_xa, 0.786):
                            return True
            return False

        # 驗證 Bat Pattern
        elif pattern_type == 'Bat':
            # 理想比例: AB=0.382-0.50 XA, BC=0.382-0.886 AB, CD=1.618-2.618 BC, AD=0.886 XA
            if 0.382 - self.err_tolerance <= ab_xa <= 0.50 + self.err_tolerance:
                if 0.382 - self.err_tolerance <= bc_ab <= 0.886 + self.err_tolerance:
                    if 1.618 - self.err_tolerance <= cd_bc <= 2.618 + self.err_tolerance:
                        if self.is_valid_ratio(ad_xa, 0.886):
                            return True
            return False
            
        # 驗證 Butterfly Pattern
        elif pattern_type == 'Butterfly':
            # 理想比例: AB=0.786 XA, BC=0.382-0.886 AB, CD=1.618-2.24 BC, AD=1.27-1.618 XA
            if self.is_valid_ratio(ab_xa, 0.786):
                if 0.382 - self.err_tolerance <= bc_ab <= 0.886 + self.err_tolerance:
                    if 1.618 - self.err_tolerance <= cd_bc <= 2.24 + self.err_tolerance:
                        if 1.27 - self.err_tolerance <= ad_xa <= 1.618 + self.err_tolerance:
                            return True
            return False
            
        # 驗證 Crab Pattern
        elif pattern_type == 'Crab':
            # 理想比例: AB=0.382-0.618 XA, BC=0.382-0.886 AB, CD=2.24-3.618 BC, AD=1.618 XA
            if 0.382 - self.err_tolerance <= ab_xa <= 0.618 + self.err_tolerance:
                if 0.382 - self.err_tolerance <= bc_ab <= 0.886 + self.err_tolerance:
                    if 2.24 - self.err_tolerance <= cd_bc <= 3.618 + self.err_tolerance:
                        if self.is_valid_ratio(ad_xa, 1.618):
                            return True
            return False
            
        return False

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        前置計算: 在這裡計算 200 日均線作為大趨勢過濾器。
        使用 pandas 原生 ewm 計算，避免 pandas-ta 的版本相容性問題。
        """
        if not df.empty and len(df) >= 200:
            df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
            
        return df
        
    def check_entry_exit(self, df: pd.DataFrame, current_position: Dict[str, Any]) -> str:
        if len(df) < 50: # 需要足夠的 K 線來尋找 5 個轉折點
            return 'hold'
            
        pos_amt = current_position.get('positionAmt', 0.0)
        current_close = df.iloc[-1]['close']
        
        # 1. 持倉狀態下，執行追蹤停利與固定停損
        if pos_amt != 0:
            entry_price = current_position.get('entryPrice', 0.0)
            if entry_price > 0:
                if pos_amt > 0:
                    # 做多: 更新歷史最高價
                    if current_close > self.highest_since_entry:
                        self.highest_since_entry = current_close
                        
                    pnl_pct = (current_close - entry_price) / entry_price
                    drawdown_pct = (self.highest_since_entry - current_close) / self.highest_since_entry
                    
                    # 條件 1: 固定 3% 停損 (防守)
                    if pnl_pct <= -0.03:
                        self.highest_since_entry = 0.0  # 重置
                        return 'sell'
                        
                    # 條件 2: 追蹤停利 (前提是至少已經賺了一點，例如賺超過 1%)
                    if pnl_pct >= 0.01 and drawdown_pct >= self.trailing_stop_pct:
                        self.highest_since_entry = 0.0  # 重置
                        return 'sell'
                        
                else:
                    # 做空: 更新歷史最低價
                    if current_close < self.lowest_since_entry:
                        self.lowest_since_entry = current_close
                        
                    pnl_pct = (entry_price - current_close) / entry_price
                    drawdown_pct = (current_close - self.lowest_since_entry) / self.lowest_since_entry
                    
                    if pnl_pct <= -0.03:
                        self.lowest_since_entry = float('inf') # 重置
                        return 'buy'
                        
                    if pnl_pct >= 0.01 and drawdown_pct >= self.trailing_stop_pct:
                        self.lowest_since_entry = float('inf') # 重置
                        return 'buy'
                        
            return 'hold'

        # 空手狀態: 重置高低點記憶，並偵測新和諧型態
        self.highest_since_entry = 0.0
        self.lowest_since_entry = float('inf')
        closed_df = df.iloc[:-1].copy()
        
        # 使用 scipy argrelextrema 尋找峰谷值
        max_idx, min_idx = self.get_extrema(closed_df['close'], self.order_size)
        
        # 將高低點合併並加上絕對時間索引按順序排列
        extrema = []
        for idx in max_idx:
            extrema.append({'idx': idx, 'type': 'high', 'val': closed_df.iloc[idx]['close']})
        for idx in min_idx:
            extrema.append({'idx': idx, 'type': 'low', 'val': closed_df.iloc[idx]['close']})
            
        extrema.sort(key=lambda x: x['idx'])
        
        # 嚴格過濾連續同類型的點，以及過濾掉振幅太小 (小於 0.5%) 的無意義雜訊波段
        filtered_extrema = []
        for e in extrema:
            if not filtered_extrema:
                filtered_extrema.append(e)
                continue
                
            last_e = filtered_extrema[-1]
            if last_e['type'] == e['type']:
                # 同類型，保留數值更極端的
                if (e['type'] == 'high' and e['val'] > last_e['val']) or \
                   (e['type'] == 'low' and e['val'] < last_e['val']):
                    filtered_extrema[-1] = e
            else:
                # 檢查振幅，如果跟上一個極端點的距離小於 0.5%，就視為盤整雜訊
                val_e = float(e['val'])
                val_last = float(last_e['val'])
                swing_pct = abs(val_e - val_last) / val_last
                if swing_pct >= 0.005:
                    filtered_extrema.append(e)
                else:
                    # 振幅太小，代表這只是一個假突破/雜訊，我們不把它當作新反轉點。
                    # 如果我們不加它，那目前最後一個點 (last_e) 的狀態就保持不變
                    # 但等一下出現的如果跟 e 同向，它就會與 last_e 變成同向，在下一個迴圈受到檢查與合併。
                    pass
                    
        # 第二次清理：因為上面 pass 捨棄了反轉點，可能導致「未來」加進來的點變成跟最後一點同類型
        # 所以再跑一次合併同類型的邏輯，確保嚴格的 High-Low-High-Low 交錯
        final_extrema = []
        for e in filtered_extrema:
            if not final_extrema:
                final_extrema.append(e)
            else:
                last_e = final_extrema[-1]
                if last_e['type'] == e['type']:
                    if (e['type'] == 'high' and e['val'] > last_e['val']) or \
                       (e['type'] == 'low' and e['val'] < last_e['val']):
                        final_extrema[-1] = e
                else:
                    final_extrema.append(e)
        
        filtered_extrema = final_extrema
                    
        # 和諧型態需要完整的 5 個轉折點 (X, A, B, C, D)
        if len(filtered_extrema) < 5:
            return 'hold'
            
        # 取最近的 5 個轉折點
        # Fix Pyre typing by explicitly isolating the last 5
        n_points = len(filtered_extrema)
        recent_5 = []
        for i in range(n_points - 5, n_points):
            recent_5.append(filtered_extrema[i])
            
        points = [float(p['val']) for p in recent_5]  # type: ignore
        
        # 檢查 D 點 (PRZ 反轉區) 是否在最近發生
        # 如果最近一次波段的高低轉折發生在 10 根 K 線前，這個型態已經過期
        d_idx = int(recent_5[-1]['idx']) # type: ignore
        last_valid_idx = len(closed_df) - 1
        if (last_valid_idx - d_idx) > 5:
            return 'hold'
            
        # 判斷這個型態大方向
        d_type = str(recent_5[-1]['type']) # type: ignore
        
        # 避免針對同一個 D 點重複進場
        if d_idx == self.last_traded_d_idx:
            return 'hold'
            
        # 取得大趨勢 EMA-200
        current_ema_200 = df.iloc[-1].get('ema_200', None)
        
        # 簡化簡化: 如果 D 點是低點，而且符合斐波那契點位，這是一個強烈看漲反轉訊號 (Bullish)
        # 注意: 為了增加進場機率，我們放寬了過度嚴格的 "未噴出" 條件
        
        is_gartley = self.check_pattern(points, 'Gartley')
        is_bat = self.check_pattern(points, 'Bat')
        is_butterfly = self.check_pattern(points, 'Butterfly')
        is_crab = self.check_pattern(points, 'Crab')
        
        if is_gartley or is_bat or is_butterfly or is_crab:
            if d_type == 'low':
                # Bullish: 準備做多，必須符合大趨勢 (價格大於 EMA-200)
                if pd.notna(current_ema_200) and current_close > current_ema_200:
                    self.last_traded_d_idx = d_idx
                    return 'buy'
            elif d_type == 'high':
                # Bearish: 準備做空，必須符合大趨勢 (價格小於 EMA-200)
                if pd.notna(current_ema_200) and current_close < current_ema_200:
                    self.last_traded_d_idx = d_idx
                    return 'sell'
                    
        return 'hold'

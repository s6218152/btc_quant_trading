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
    
    def __init__(self, exchange, config_params: Dict[str, Any], order_size=5, err_tolerance=0.08):
        super().__init__(exchange, config_params)
        self.order_size = order_size # 尋找局部極值時的左右視窗大小 (越大過濾掉越小的雜訊)
        self.err_tolerance = err_tolerance # 斐波那契比例的容錯範圍 (放寬為 8%)

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
            
        return False

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        # 和諧型態尋找極值的邏輯牽涉到動態的序列片段
        # 只依靠指標欄位可能導致未來函數 (在K線還沒收盤就將其列為高點)，
        # 所以直接讓 check_entry_exit 每次從歷史資料截斷找極點最乾淨。
        return df
        
    def check_entry_exit(self, df: pd.DataFrame, current_position: Dict[str, Any]) -> str:
        if len(df) < 50: # 需要足夠的 K 線來尋找 5 個轉折點
            return 'hold'
            
        pos_amt = current_position.get('positionAmt', 0.0)
        current_close = df.iloc[-1]['close']
        
        # 1. 簡單的固定 5% 停利與 3% 停損 (為了測試和諧進場的效率，不加複雜追蹤停利)
        if pos_amt != 0:
            entry_price = current_position.get('entryPrice', 0.0)
            if entry_price > 0:
                if pos_amt > 0:
                    pnl_pct = (current_close - entry_price) / entry_price
                    if pnl_pct >= 0.05 or pnl_pct <= -0.03:
                        return 'sell'
                else:
                    pnl_pct = (entry_price - current_close) / entry_price
                    if pnl_pct >= 0.05 or pnl_pct <= -0.03:
                        return 'buy'
            return 'hold'

        # 2. 空手狀態下，偵測和諧型態
        # 扣除最後一根跳動的 K 線，避免未來函數
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
        
        # 過濾連續同類型的點，以及過濾掉振幅太小 (小於 0.5%) 的無意義雜訊波段
        filtered_extrema = []
        for e in extrema:
            if not filtered_extrema:
                filtered_extrema.append(e)
            else:
                last_e = filtered_extrema[-1]
                if last_e['type'] == e['type']:
                    # 同類型，保留數值更極端的
                    if (e['type'] == 'high' and e['val'] > last_e['val']) or \
                       (e['type'] == 'low' and e['val'] < last_e['val']):
                        filtered_extrema[-1] = e
                else:
                    # 檢查振幅，如果跟上一個極端點的距離小於 0.5%，就視為盤整雜訊，不把它當作新的反轉點
                    # 避免微小的抖動被當成波段
                    swing_pct = abs(e['val'] - last_e['val']) / last_e['val']
                    if swing_pct > 0.005:
                        filtered_extrema.append(e)
                    else:
                        # 如果振幅太小，代表這只是一個假突破/雜訊，我們捨棄這個點。
                        # 但為保持結構連續，直接丟棄可能會導致高低反相，
                        # 這裡簡化處理：如果振幅太小，直接把原本的最後一個點也 pop 掉，重新找下一個明顯的轉折
                        filtered_extrema.pop()
                    
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
        
        # 牛市看漲 (Bullish Pattern): 結構長得像變形的 W 頂或 M 頂
        # 為了簡化，只要符合斐波那契點位，且 D 點是近期的區域最低點 (Low)，就是一個強烈看漲反轉訊號 (Bullish)
        
        is_gartley = self.check_pattern(points, 'Gartley')
        is_bat = self.check_pattern(points, 'Bat')
        
        if is_gartley or is_bat:
            if d_type == 'low':
                # Bullish: D點是區域低點，價格跌到 D 準備反轉向上 => Buy
                # 確保現在價格還沒有離 D 點太遠 (未噴出)
                if current_close <= points[-1] * 1.01: 
                    return 'buy'
            elif d_type == 'high':
                # Bearish: D點是區域高點，價格漲到 D 準備反轉向下 => Sell
                if current_close >= points[-1] * 0.99:
                    return 'sell'
                    
        return 'hold'

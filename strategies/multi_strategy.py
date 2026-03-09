import pandas as pd
from typing import Dict, Any, List
from .base_strategy import BaseStrategy
import logging

class MultiStrategyCombiner(BaseStrategy):
    """
    多重策略組合器
    將多個獨立策略合併，當且僅當所有子策略發出相同訊號時，才真正發出進出場動作。
    """
    
    def __init__(self, exchange, config_params: Dict[str, Any], strategies: List[BaseStrategy], mode: str = 'all', signal_memory_bars: int = 5):
        super().__init__(exchange, config_params)
        self.strategies = strategies
        self.mode = mode
        self.signal_memory_bars = signal_memory_bars # 允許訊號殘存的 K 線根數 (例如: 5 小時內發生的都算數)
        self.logger = logging.getLogger("MultiStrategyCombiner")
        
        # 紀錄子策略歷史投票: { strategy_name: [ 'hold', 'buy', 'hold', ... ] }
        self.vote_history = {s.__class__.__name__: [] for s in strategies}
        self.cooldown_bars = 5 # 平倉後強制休息 5 根 K 線不進場
        self.current_cooldown = 0

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """依序讓每個子策略在 DataFrame 上補充自己的指標"""
        for strategy in self.strategies:
            df = strategy.generate_signals(df)
        return df
        
    def check_entry_exit(self, df: pd.DataFrame, current_position: Dict[str, Any]) -> str:
        if not self.strategies:
            return 'hold'
            
        # 收集所有子策略的當下投票並存入歷史
        votes = []
        for strategy in self.strategies:
            strat_name = strategy.__class__.__name__
            vote = strategy.check_entry_exit(df, current_position)
            
            # 紀錄歷史
            self.vote_history[strat_name].append(vote)
            
            # 只保留最近 N 根的歷史
            if len(self.vote_history[strat_name]) > self.signal_memory_bars:
                self.vote_history[strat_name].pop(0)
                
            # 判斷這 N 根內是否曾出現過訊號 (最新訊號優先, 若有衝突以最新的為主)
            # 為了避免 buy / sell 互相混淆，我們只檢查這段視窗內有沒有我們需要的訊號
            votes.append((strat_name, vote))
            
        # 重新評估這 N 根 K 線內的「有效投票數」
        buy_votes = 0
        sell_votes = 0
        active_buy_reasons = []
        active_sell_reasons = []
        
        for strat_name, current_vote in votes:
            history = self.vote_history[strat_name]
            
            # 反向訊號會提早中斷記憶 (e.g. 就算 3 小時前喊 buy，剛剛喊了 sell，那 buy 就不算數了)
            # 找出最近的一個非 hold 的明確訊號
            effective_vote = 'hold'
            for v in reversed(history):
                if v != 'hold':
                    effective_vote = v
                    break
                    
            if effective_vote == 'buy':
                buy_votes += 1
                active_buy_reasons.append(strat_name)
            elif effective_vote == 'sell':
                sell_votes += 1
                active_sell_reasons.append(strat_name)
                
        total_strategies = len(self.strategies)
        
        # 決定進場過關門檻 (過半或全票)
        if self.mode == 'majority':
            required_entry_votes = (total_strategies // 2) + 1
        else:
            required_entry_votes = total_strategies  # default 'all'
        
        # 紀錄各策略的意見 (供觀察)
        vote_details = ", ".join([f"{name}: {vote}" for name, vote in votes])
        
        # 取得目前倉位狀態
        pos_amt = current_position.get('positionAmt', 0.0)
        
        # 決策邏輯
        final_action = 'hold'
        self.last_agreeing_strategies = []
        
        def clean_name(n):
            return n.replace('Strategy', '').replace('Trend', '').replace('Cross', '').replace('Bands', '')
        
        # 處理冷卻期
        if self.current_cooldown > 0 and pos_amt == 0:
            self.current_cooldown -= 1
            return 'hold'
            
        # 方案 A: 進場嚴格 (需達門檻)，出場寬鬆 (只要有 1 票反向就跑)
        if pos_amt == 0:
            # 空手狀態：尋找進場
            if buy_votes >= required_entry_votes:
                final_action = 'buy'
                self.last_agreeing_strategies = [clean_name(name) for name in active_buy_reasons]
            elif sell_votes >= required_entry_votes:
                final_action = 'sell'
                self.last_agreeing_strategies = [clean_name(name) for name in active_sell_reasons]
                
        elif pos_amt > 0:
            # 持有多單
            entry_price = current_position.get('entryPrice', 0.0)
            current_price = df.iloc[-1]['close']
            pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
            
            # 停損停利保護：跌破3%無條件停損 / 獲利超過5%且有反向訊號才平倉
            if pnl_pct <= -0.03:
                final_action = 'sell'
                self.last_agreeing_strategies = ['Stop Loss (3%)']
                self.current_cooldown = self.cooldown_bars
            else:
                current_sell_count = sum(1 for _, v in votes if v == 'sell')
                if current_sell_count >= 1:
                    final_action = 'sell'
                    self.last_agreeing_strategies = [clean_name(name) for name, v in votes if v == 'sell']
                    self.current_cooldown = self.cooldown_bars # 觸發平倉後進入冷卻
                
        elif pos_amt < 0:
            # 持有空單
            entry_price = current_position.get('entryPrice', 0.0)
            current_price = df.iloc[-1]['close']
            pnl_pct = (entry_price - current_price) / entry_price if entry_price > 0 else 0
            
            if pnl_pct <= -0.03:
                final_action = 'buy'
                self.last_agreeing_strategies = ['Stop Loss (3%)']
                self.current_cooldown = self.cooldown_bars
            else:
                current_buy_count = sum(1 for _, v in votes if v == 'buy')
                if current_buy_count >= 1:
                    final_action = 'buy'
                    self.last_agreeing_strategies = [clean_name(name) for name, v in votes if v == 'buy']
                    self.current_cooldown = self.cooldown_bars # 觸發平倉後進入冷卻
            
        self.logger.info(f"[票數統計] {vote_details} => 最終: {final_action} (進場門檻: {required_entry_votes}/{total_strategies}) [CD:{self.current_cooldown}]")
        return final_action

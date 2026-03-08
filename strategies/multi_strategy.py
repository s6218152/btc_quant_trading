import pandas as pd
from typing import Dict, Any, List
from .base_strategy import BaseStrategy
import logging

class MultiStrategyCombiner(BaseStrategy):
    """
    多重策略組合器
    將多個獨立策略合併，當且僅當所有子策略發出相同訊號時，才真正發出進出場動作。
    """
    
    def __init__(self, exchange, config_params: Dict[str, Any], strategies: List[BaseStrategy], mode: str = 'all'):
        super().__init__(exchange, config_params)
        self.strategies = strategies
        self.mode = mode
        self.logger = logging.getLogger("MultiStrategyCombiner")

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """依序讓每個子策略在 DataFrame 上補充自己的指標"""
        for strategy in self.strategies:
            df = strategy.generate_signals(df)
        return df
        
    def check_entry_exit(self, df: pd.DataFrame, current_position: Dict[str, Any]) -> str:
        if not self.strategies:
            return 'hold'
            
        # 收集所有子策略的投票結果
        votes = []
        for strategy in self.strategies:
            vote = strategy.check_entry_exit(df, current_position)
            votes.append((strategy.__class__.__name__, vote))
            
        buy_votes = sum(1 for _, v in votes if v == 'buy')
        sell_votes = sum(1 for _, v in votes if v == 'sell')
        total_strategies = len(self.strategies)
        
        # 決定過關門檻
        if self.mode == 'majority':
            required_votes = (total_strategies // 2) + 1  # 4 個需 3 票，3 個需 2 票
        else:
            required_votes = total_strategies  # default 'all'
        
        # 紀錄各策略的意見 (供觀察)
        vote_details = ", ".join([f"{name}: {vote}" for name, vote in votes])
        
        # 決策邏輯：超過門檻才進出場
        final_action = 'hold'
        self.last_agreeing_strategies = []
        
        def clean_name(n):
            return n.replace('Strategy', '').replace('Trend', '').replace('Cross', '').replace('Bands', '')
                
        if buy_votes >= required_votes:
            final_action = 'buy'
            self.last_agreeing_strategies = [clean_name(name) for name, v in votes if v == 'buy']
        elif sell_votes >= required_votes:
            final_action = 'sell'
            self.last_agreeing_strategies = [clean_name(name) for name, v in votes if v == 'sell']
            
        self.logger.info(f"[票數統計] {vote_details} => 最終: {final_action} (門檻: {required_votes}/{total_strategies})")
        return final_action

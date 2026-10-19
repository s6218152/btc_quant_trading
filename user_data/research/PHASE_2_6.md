# Phase 2–6 開發驗證紀錄

建立日期：2026-10-19（Asia/Taipei）。本紀錄是基礎實作驗證，不是最終策略研究報告。

## 已完成

- Freqtrade 2026.9 官方映像以 digest 鎖定；容器 Python 3.14.7。
- 新增研究設定、策略、公開資料下載、資料稽核、原生回測與損益核對流程。
- 10,000 USDT、0.5% 預算風險、最大 2x、isolated、單一持倉，無加碼。
- 收盤後 Donchian 20 / 10、ATR14 × 2.5、已完成日線 EMA200 與斜率過濾。
- 拒絕實盤、憑證、過期行情、超額風險及倉位計算錯誤。
- 舊系統、舊環境、全域 MCP 與 hooks.json 均未修改。

## 資料驗證

資料凍結於 `[2021-01-01, 2025-01-01)` UTC；2024 為開發回測，之前只作暖機。

| 資料 | 筆數 | 缺漏 / 重複 |
|---|---:|---:|
| 4H Futures | 8,766 | 0 / 0 |
| 1D Futures | 1,461 | 0 / 0 |
| 1H Mark Price | 35,064 | 0 / 0 |
| 8H Funding 事件 | 4,383 | 0 / 0 |

2023-08-16、2023-11-10 的官方日線成交量與 4H 加總不一致；價格全部一致。
保留原始值與警告，沒有修造資料。日線只用收盤價計算 EMA，進場量能只看 4H 是否大於零。
Funding 完整性通過，沒有以假設零費率補資料。

## 驗證結果

- Pytest：32 passed。
- Ruff：All checks passed。
- Freqtrade lookahead-analysis：20 筆訊號；biased entry / exit / indicator 均為零。
- 9 項獨立核對全部通過：持倉重疊、槓桿、有效曝險、預算風險、ATR 停損、
  PnL、Funding、費用拆分與 Funding/Mark 對齊。
- 最大預算風險：0.499223%；逐筆損益核對最大誤差小於 0.00000001 USDT。
- 最終 ZIP 中的策略原始碼與目前策略檔案雜湊一致。

Lookahead 工具會暫時改變資金與持倉設定以檢測訊號；單交易對、禁止 stacking 的
工具例外已單獨測試。這不會變更基準設定中的最大一個持倉。

## 2024 開發回測

原生引擎區間：2024-01-01 00:00 → 2024-12-31 20:00 UTC。

| 指標 | 結果 |
|---|---:|
| Total Return | 6.5559% |
| CAGR（原生引擎） | 6.5559% |
| Final Balance | 10,655.59 USDT |
| Max Drawdown（wallet） | 2.8372% |
| Sharpe（daily wallet） | 1.2365 |
| Sortino（daily wallet） | 2.4029 |
| Profit Factor | 2.3416 |
| Win Rate | 38.7097% |
| Average Win | 95.36 USDT |
| Average Loss | -25.72 USDT |
| Expectancy | 21.15 USDT / trade |
| Total / Long / Short Trades | 31 / 27 / 4 |
| Max Consecutive Losses | 5 |
| Trading Fees | 40.92 USDT |
| Slippage Proxy | 40.92 USDT |
| Net Funding Costs | 67.23 USDT |

21 筆由移動 ATR 停損出場，9 筆多單與 1 筆空單由 Donchian 出場，沒有清算出場。
連續虧損保護是延後進場，不會清除既有虧損序列，因此仍可能出現超過三筆連續虧損。
Freqtrade 另提供 trade-based Sharpe 0.3624，與上表 daily-wallet 方法不同，不混用。

## 問題與限制

- 滑價以每邊 5 bps 現金成本代理，加上 taker 5 bps；不是成交價格或流動性重播。
- Maker 2 bps 僅為記錄的比較假設，市價基準未使用 maker 費率。
- Funding 已按歷史事件計入，倉位 Funding reserve 為零；持有期資金費與跳空可能超出預算。
- 停損的 K 線內時序與成交價遵循原生回測假設；尚未做更細週期壓力驗證。
- 原生引擎計算清算價與 buffer，但不計清算費用。
- 使用凍結時的交易規格，並在再次回測前檢查是否改變；不是歷史逐時點規格。
- Freqtrade 2026.9 搭配 Pandas 3 重複更新既有 lookahead CSV 會發生型別錯誤。
  執行器改成每次保存獨立原生 CSV，再更新精簡的 latest 副本；未修改框架。
- 現有全域 Node 動態函式庫問題仍未修復；研究使用 Docker，不依賴該 Node。

完整機器摘要見 [baseline_summary.json](baseline_summary.json)，資料雜湊見
[data_manifest.json](data_manifest.json)。原生 ZIP 與逐筆 CSV 保留在本地
`user_data/backtest_results/`，詳細成功／失敗日誌在 `user_data/logs/`。

## 下一步

Phase 7 起建立兩個按需載入的專案 Skill，再依凍結計畫進行 Walk-Forward、
成本與參數穩健度、同條件比較。2025 OOS 與 2026 最終測試尚未評估，
目前不能據此判定策略優於 Buy & Hold、無 EMA 版本或具有長期獲利能力。

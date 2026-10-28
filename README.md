# BTCUSDT Donchian 4H 研究

以 Freqtrade 2026.9 研究 BTC/USDT:USDT 永續合約。已完成開發基準與凍結的 Walk-Forward 研究，
**僅允許回測與無交易金鑰的 dry-run，禁止真實下單。持續模擬驗證尚未完成。**
根目錄既有 `main.py`、`backtest.py` 與 `strategies/` 是舊系統，沒有改寫或啟動。

研究結果與限制見 [Phase 7–10 報告](user_data/research/study/REPORT.md)。
2025 樣本外兩折串聯報酬約 **-0.27%**，目前不支持穩定獲利結論。
原研究保留 2026；使用者於 2026-10-21 另行授權固定 V1 評估至當日，
結果與資料差異見 [上市至今評估](user_data/research/inception_20261021/REPORT.md)。
此後 2026 已被觀察，不能再稱為未見保留樣本；舊研究與選參紀錄不變。

## 環境與執行

使用 Apple Silicon 可執行的官方 Docker 映像，基底以 digest 鎖定；開發依賴
另鎖定於 `requirements-research.txt`。不使用舊 `venv` 或 `.env`，沒有交易憑證。
請先啟動既有 Docker Desktop，再於專案根目錄執行：

```sh
python3 scripts/research.py build
python3 scripts/research.py download
python3 scripts/research.py audit
python3 scripts/research.py test
python3 scripts/research.py lint
python3 scripts/research.py backtest
python3 scripts/research.py lookahead
python3 scripts/research.py report
```

完整研究使用獨立資料與結果，保留原本 Phase 6 產物：

```sh
python3 scripts/research.py study-download
python3 scripts/research.py study
python3 scripts/research.py study-validate
python3 scripts/research.py study-report
```

`study` 依凍結計畫續跑缺少的原生回測，已有且雜湊相符的結果不重跑；
之後重建 Mark 淨值、驗證來源與時序，再產生報告。若要分析單一原生 JSON/ZIP，
使用 `research.py analyze --result` 指定專案內結果檔案。

補充的細週期執行壓力與事前波動預算比較，使用已觀察的 2024／2025：

```sh
python3 scripts/research.py stress-download
python3 scripts/research.py stress
python3 scripts/research.py stress-validate
python3 scripts/research.py stress-report
```

計畫、權重與結果獨立保存在 `user_data/research/stress/`；
[補充報告](user_data/research/stress/REPORT.md) 說明資料來源警告與適用範圍。
`stress-download` 保留 archive 資料，細週期合約價格另由原生 REST 下載，
再與凍結的 4H／1D／Funding／Mark 資料核對。部分 2024-10-28 價格跨週期不一致，
已列警告，沒有補造價格或改動舊資料。

下載、回測與 lookahead 的詳細日誌保存至 `user_data/logs/`；終端只顯示摘要。
`backtest` 會先驗證資料與交易規格，再執行原生引擎，最後核對風險與損益。
執行器不提供任意 `trade` 指令；dry-run 使用獨立安全入口，策略也會拒絕 `dry_run=false`。

## 持續模擬交易

先啟動 Docker Desktop，完成上面的 `build`，再執行：

```sh
python3 scripts/research.py dryrun-start
python3 scripts/research.py dryrun-status
python3 scripts/research.py dryrun-logs
python3 scripts/research.py dryrun-stop
```

`dryrun-start` 在背景啟動，重複呼叫不會建立第二個服務。使用原本 V1 基準策略、
10,000 USDT 模擬資金，最多一個 BTC 永續部位；不是根據 2025 結果重新選參。
只連公開行情，不需提供 API key。入口拒絕交易金鑰、實盤模式、額外設定檔與
`FREQTRADE__*` 環境覆寫，不開放 API／網頁管理連接埠。

資料庫、輪替日誌與每次啟動的設定／策略雜湊位於 `user_data/dryrun/`，不提交 Git；
停止、重啟會保留模擬部位。請勿在運行時刪除或更改這個目錄。
要修改執行設定，先停止、備份該目錄，再移走其中 `config.json` 讓入口重新產生。
備份資料庫時先正常停止，避免 SQLite 寫入中的不一致。

初次取得 4H／日線暖機資料可能需要數分鐘；1000 根暖機 K 線需要多次 API 請求，
引擎會提示這項效能警告。這些即時運行所需的暖機資料不寫入凍結研究資料集；
不執行 2026 回測、績效選參或最終保留集評估。

Telegram 可選用「僅發送」通知：在 runtime 目錄放置權限 `600`、由容器使用者
UID 1000 可讀取的 `telegram.json`，欄位為 `token` 與數字型字串 `chat_id`。
此檔案已排除 Git，勿貼入聊天或版本控制。修改後需重新建立 paper 容器。
原生 webhook 會通知啟動／停止、警告、引擎例外、模擬進場／出場成交；
訊息均標示模擬交易。原生 Telegram 指令接收器仍停用，不能透過 Bot 遠端下單。
通知權杖只在容器內權限 600 的暫存設定中使用，日誌訊息會遮蔽權杖。
發送失敗最多重試一次，沒有永久佇列，斷網可能漏訊或重複；整台主機斷電時
不能依靠同機通知，外部離線告警仍需另行配置。

每 5 秒進行原生主迴圈，策略指標只在新 K 線更新。4H 進場訊號依已完成 K 線，
不代表每 5 秒開倉；持倉管理仍持續運作。每 30 秒記錄心跳，180 秒內的
`RUNNING` 心跳才算存活；`dryrun-status` 未就緒、已停止或過期時回傳非零。
心跳不能保證交易所行情新鮮或整個策略正確，也不是外部告警系統。

容器程序退出時由 Docker 自動重啟；健康檢查失敗只標示 unhealthy，**不會自行重啟**。
手動停止後不會立刻開回；電腦睡眠、關機或 Docker 未啟動時也不會運行。
已部署 N5105 的專用 PVE VM 102，並驗證 VM 重開後服務自動恢復。
長時間斷線、整台 PVE 主機重開及外部告警仍未完成驗證。
PVE 日常操作見 [部署說明](PVE_DEPLOYMENT.md)；Mac 副本已停止，日常由 PVE 執行。

模擬成交採 Freqtrade 的 dry-run 撮合假設；沿用研究 `fee=0.001` 成本代理，
可能與原生模擬撮合價格影響重疊，不代表真實交易費率或可成交績效。

## 研究切分

Phase 6 計畫固定於 `user_data/research/plan.json`，當時的設定與紀錄保持不變：

- 資料：2021-01-01 至 2025-01-01，結束時間不含當下，全部 UTC。
- 開發回測：2024 全年；之前的資料只用於指標暖機，含 1,095 根日線。
- Phase 6 當時尚未評估 2025／2026，也沒有進行參數搜尋。

Phase 7–10 計畫另存 `user_data/research/study/protocol.json`：

- 資料 2020–2025，依 UTC 半開區間凍結；2026 不參與評估或選參。
- 12 組 OFAT 粗測、依 2024 開發結果預定門檻產生 6 組鄰近測試；沒有窮舉完整組合。
- 四折：前 15 個月訓練、後 6 個月測試。2024 兩折是回溯演示，2025 兩折為樣本外。
- 候選、選參規則、每折決策皆在對應 OOS 前凍結；不以 2025 結果回頭調參。
- 全部 94 個有效唯一原生回測，逐一核對損益、Funding、風險、來源與時間邊界。
- 原生 timerange 包含結束邊界，因此研究流程轉為 epoch 結束時間前一秒，並核對交易日期。

原生下載器可能保留超出指定日期的批次資料；執行器會裁切超出範圍的資料，
再檢查時間網格、缺漏、重複、排序、OHLCV、跨週期價格與 Funding 完整性。
不補造 K 線，也不以零取代缺少的 Funding。

## 訊號與出場

- 4H 收盤突破「前 20 根」高點做多、跌破低點做空；視窗先 `shift(1)`。
- 透過 Freqtrade `@informative("1d")` 合併已完成日線；多單須日線收盤高於
  EMA200 且 EMA 斜率為正，空單相反。當天未收盤的日線不得參與判斷。
- 原生引擎將訊號移到下一根的開盤執行，不使用突破 K 線內的未來成交價。
- 初始絕對停損：成交價 ± 2.5 × 最後已收盤 4H 的 ATR14。
- 後續候選停損：最後已收盤 4H 收盤價 ± 2.5 × ATR14；只向有利方向移動。
- `custom_stoploss` 使用 `stoploss_from_absolute(..., leverage=trade.leverage)`，
  回傳槓桿後的風險比例，不直接回傳 ATR 價差。
- `custom_exit` 判斷已完成 4H 收盤是否穿越前 10 根反向通道。
- ATR 停損與通道出場由原生引擎依其 K 線內事件假設處理；沒有逐筆行情重播。

## 倉位與保護

預設資金 10,000 USDT，單筆預算風險 0.5%，最大 2x，isolated，一次一個部位。
策略採 one-way 假設；回測不存取私人帳戶或變更帳戶模式。
不使用 DCA、Martingale、Pyramiding 或 position stacking。

```text
distance = 2.5 × ATR
loss_per_unit = distance + (2 × entry_price + distance) × cost_per_side
                + entry_price × funding_reserve
quantity = equity × risk_fraction / loss_per_unit
notional <= min(equity × 2, available_margin × 0.98 × leverage)
stake_amount = notional / leverage
```

交易所最小金額、精度與最大金額由 Freqtrade 檢查；低於最小單的預算倉位會拒絕，
不會向上放大到最小單。進場確認會再次核對風險；資料過期或計算錯誤時拒絕進場。
槓桿只在 notional 與保證金的換算中計算一次。

保護包含：原生一根冷卻、30 日視窗的 10% 已實現回撤保護（冷卻 7 日）、
原生 StoplossGuard，以及任何原因連續三筆虧損後的 48 小時進場暫停。
這些是視窗／冷卻保護，不保證帳戶最大虧損一定受限於該比例。

## 成本與可重現範圍

基準使用市價單，假設 taker 5 bps、每次成交滑價 5 bps。原生引擎每邊的 `fee`
設為 10 bps，將兩者計入淨損益；摘要再拆開列出。Maker 假設 2 bps 記錄於
研究計畫，但本基準沒有 maker 成交。這些是研究假設，不是使用者帳戶費率。

**滑價是現金成本代理，並未改變成交價、觸發順序或訂單成交機率。**
不能據此宣稱已完成真實滑價或流動性模擬。原生回測也不計清算費用；
有清算價與 buffer，仍須後續更細週期及壓力測試。

使用實際 Funding；以原始 Funding × 同時點 Mark Price × 數量獨立核對引擎。
Funding 檔名雖為 `1h`，此 BTC 歷史區間實際事件間隔為 8 小時。
倉位的 Funding reserve 目前為零，持有期 Funding 與跳空可能使實際虧損超出預算。

資料 SHA256、計畫、映像 digest、依賴清單、程式與設定雜湊、交易規格快照及
原生結果 ZIP 一起保存。再次回測會拒絕交易規格無聲變更；仍需公開 API 可用，
且採用凍結時的交易規格，不是歷史逐時點交易規格。請保存本地原始資料與結果 ZIP。

原生匯出的 `initial_stop_loss_*` 是靜態緊急停損欄位；分析 CSV 的
`initial_atr_stop` 另外從進場前資料重建，並檢查有效停損已朝有利方向收緊。

原生 wallet 風險指標不包含持倉中浮動損益。研究報告另以每小時 Mark Price 開盤、
原生實際成交與 Funding 重建淨值、回撤與日報酬指標；每筆最終淨值均與引擎核對。
此重建只作風險報告，沒有產生交易，也沒有改變已凍結的 wallet-Sharpe 選參分數。

## 產物

- `user_data/strategies/Donchian4HStrategy.py`：策略與風控。
- `user_data/config.json`：無憑證、僅供研究的設定。
- `user_data/research/`：計畫、資料稽核、環境鎖定、lookahead 與精簡績效摘要。
- `user_data/backtest_results/`：原生 ZIP 與逐筆 CSV，不送入 Codex 上下文。
- `tests/`：收盤對齊、未來資料隔離、倉位、停損、資料異常及會計核對測試。

兩個專案 Skill 位於 `.agents/skills/`：`$backtest-analyzer` 與
`$quant-strategy-validator`，均設定為明確呼叫才載入，不會每次任務自動執行。
已增加原生 1H／15m 持倉事件、成本與漏檢情境，以及 2024 校準、先凍結部位再測
2025 的事前波動預算比較。這不是新的獨立 OOS；實現波動與尾部風險仍可能不同。
仍需連續 dry-run。原同 1x 買入持有比較只供描述，不作等風險績效排名。

## 報酬率改善研究（2026-10-28）

```bash
python3 scripts/research.py return-study
```

`DonchianReturnStrategy` 繼承原策略的倉位、停損與模擬交易限制，新增
`research.long_only`（只做多）及 `research.adx_filter`（4h ADX(14) ≥ 25）兩個開關。
兩者預設關閉。固定四組對照，維持每筆 0.5% 預算風險及最高 2 倍槓桿。

2023 年依扣成本後年化選擇，2024 年確認，再檢查 2023–2024 連續帳戶。
每個階段要求至少 10 筆、淨年化為正且高於同期間基準；每小時時價最大回撤
不得超過 10%，也不得比基準高逾 2 個百分點。確認失敗不改挑第二名。
兩年都是已知開發資料；2025／2026 不參與本次選擇或調參，仍需後續新資料驗證。

計畫、原始策略與分析程式雜湊、候選、選擇及結果保存在
[`user_data/research/return_revision_20261028/REPORT.md`](user_data/research/return_revision_20261028/REPORT.md)。
研究指令不會替換 PVE 服務或原本凍結策略。符合所有門檻且通過前視檢查後，
才將候選標為研究驗收通過。完整成交、時價淨值及原生記錄保存在本機結果目錄。

單檔測試可使用 `python3 scripts/research.py test --test-path tests/test_return_strategy.py`。

本次選中只做多（`long_only: true`、`adx_filter: false`），並通過兩階段及連續比較。
2023–2024 連續淨年化由 5.57% 提高至 7.30%，總報酬由 11.46% 提高至 15.14%，
每小時時價最大回撤由 5.52% 降至 3.33%。這是開發期結果，尚未達年化 10%，
也不代表未來或完整牛熊週期績效。PVE 目前仍執行原策略。

固定候選從上市歷史延伸至台北 2026-10-27 的評估：

```bash
python3 scripts/research.py candidate-inception-download --allow-final-holdout
python3 scripts/research.py candidate-inception --allow-final-holdout
```

這是使用者另行要求的完整期間描述性評估，會包含 2025／2026，但不改選參決策。
舊資料保持不變，只在新目錄追加；日線暖機規則使正式交易自 2022-06-21 開始。
資料截止為 UTC 2026-10-27 16:00（不含），最後原生 K 線時間戳為台北 10/27 20:00。
結果見 [固定候選完整歷史報告](user_data/research/candidate_inception_20261027/REPORT.md)。

## ETH 固定參數跨幣種評估（2026-10-28）

```bash
python3 scripts/research.py eth-history-download --allow-final-holdout
python3 scripts/research.py eth-history --allow-final-holdout
```

ETH/USDT:USDT 使用與 BTC 只做多版相同的原始碼及參數，只更換交易對與獨立資料目錄。
API 最早 4h 資料為 2019-11-27；2019-12-11 日線／4h 價格不一致，
保留原始資料並僅排除該日以前的初始暖機段。保留 1000 根日線暖機後，
正式績效自 2022-09-07 起，資料截止台北 2026-10-27 結束。

完整結果：淨總報酬 0.90%、年化 0.22%、每小時時價最大回撤 6.47%，72 筆交易。
含手續費、滑價現金成本代理及實際 Funding。早期 79 筆 Funding 缺少 Mark 對應，
全都在不可交易的暖機期；正式交易期間配對完整，未以零補缺。
這是固定參數描述性評估，沒有以 ETH 結果調參，也沒有改動 BTC VM。

原始下載計畫保留於 `user_data/research/eth_history_20261027/`；發現暖機資料品質
問題後，在任何績效回測前另凍結 v2 協議，正式結果見
[ETH 完整歷史報告](user_data/research/eth_history_v2_20261027/REPORT.md)。
凍結資料後下載指令會拒絕再次更新；可直接重跑評估指令核對固定結果。

## 框架依據

- [安裝與 ARM64](https://www.freqtrade.io/en/stable/installation/)
- [Strategy callbacks](https://www.freqtrade.io/en/stable/strategy-callbacks/)
- [Informative candles](https://www.freqtrade.io/en/stable/strategy-customization/)
- [回測假設](https://www.freqtrade.io/en/stable/backtesting/#assumptions-made-by-backtesting)
- [合約、Funding 與清算限制](https://www.freqtrade.io/en/stable/leverage/)

實作與測試使用鎖定的 2026.9 原始碼；上述 stable 文件將隨官方後續版本更新。

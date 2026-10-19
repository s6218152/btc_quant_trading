"""Render the frozen research summary; never select or rerun strategies."""

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "user_data/research/study"


def percent(value):
    return "—" if value is None else f"{value * 100:.2f}%"


def table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines.extend("| " + " | ".join(map(str, row)) + " |" for row in rows)
    return "\n".join(lines)


def main():
    summary = json.loads((STUDY / "summary.json").read_text())
    protocol = json.loads((STUDY / "protocol.json").read_text())
    folds = summary["folds"]
    pristine = [f for f in folds if f["pristine"]]
    fold_rows = []
    for fold in folds:
        r = fold["test_result"]
        selected = fold["selected"]
        params = (
            f"{selected['entry_period']}/{selected['exit_period']}/"
            f"{selected['atr_multiplier']}/{percent(selected['risk_per_trade'])}"
            if selected
            else "cash"
        )
        fold_rows.append(
            [
                fold["name"],
                fold["train"],
                fold["test"],
                params,
                percent(r["total_return"]),
                percent(r.get("max_drawdown")),
                percent(r.get("mark_to_market", {}).get("max_drawdown")),
                r.get("stats", {}).get("trades", 0),
            ]
        )
    cost_rows = []
    for r in summary["costs"]:
        p = r["params"]
        cost_rows.append(
            [
                p.get("taker_fee", 0.0005) * 10000,
                p.get("slippage_per_side", 0.0005) * 10000,
                percent(r["total_return"]),
                percent(r["max_drawdown"]),
                r["stats"]["trades"],
            ]
        )
    compare_rows = []
    for i, r in enumerate(summary["comparisons_1x"]):
        name = ["Donchian + EMA", "Donchian 無 EMA", "1x 永續買入持有（98%配置）"][i]
        compare_rows.append(
            [
                name,
                percent(r["total_return"]),
                percent(r["mark_to_market"]["max_drawdown"]),
                f"{r['mark_to_market']['sharpe_daily']:.3f}",
                r["stats"]["trades"],
                percent(r.get("average_entry_exposure")),
                f"{r['costs'].get('funding_cost_net', 0):.2f}",
            ]
        )
    development = summary["development"]
    baseline = next(
        r
        for r in development
        if r["params"]
        == {"entry_period": 20, "exit_period": 10, "atr_multiplier": 2.5, "risk_per_trade": 0.005}
    )
    positive = sum(r["total_return"] > 0 for r in development)
    refined = summary["refined_development"]
    minimum = min(r["total_return"] for r in development)
    maximum = max(r["total_return"] for r in development)
    regime = pd.read_csv(STUDY / "regimes.csv")
    coverage = regime.groupby("regime")["trades"].sum().to_dict()
    volatility = regime.groupby("volatility")["trades"].sum().to_dict()
    oos_trades = sum(f["test_result"].get("stats", {}).get("trades", 0) for f in pristine)
    all_integrity = all(r["test_result"].get("integrity", {}).get("passed", True) for r in folds)
    sections = [
        "# Donchian 4H 研究報告：Phase 7–10",
        "研究範圍截至 2025；2026 最終測試保留。這是單一商品、有限樣本的研究，尚未驗證可實盤部署。",
        f"**目前不支持穩定獲利的結論。** 預先凍結流程的 2025 樣本外串聯報酬為 "
        f"{percent(summary['pristine_2025_linked_return'])}；正的開發績效未在這次 WFA 重現。",
        "## 凍結方法與可重現性",
        f"原生框架 Freqtrade 2026.9，共 {summary['unique_backtests']} 個有效唯一回測。"
        f"候選共 {summary['candidate_count']} 組：12 組 OFAT 粗測，加上最多 6 組開發區鄰近測試。"
        "所有原生 ZIP、設定、逐筆 CSV、日誌與雜湊保存在本地；相同輸入可續跑，不重新計算已有結果。",
        "另有 2 個早期原生輸出因結束邊界包含問題未納入研究；保留日誌與原始產物供追溯。",
        "訓練與驗證均採 `[start,end)`。原生引擎接收 epoch 結束時間前一秒，"
        "並再檢查逐筆交易沒有越過邊界。每折的選參 JSON 在測試前寫入，"
        "2025 結果不參與候選產生或規則調整。所有 OOS 會計／風控核對通過："
        f"{all_integrity}。",
        "選出的 2025 候選與無 EMA 版本另外執行原生 lookahead-analysis，各抽驗 20 筆訊號，"
        "未偵測到 biased entry、exit 或 indicator。詳見 lookahead_validation.json。",
        "2024 已在前階段閱讀，因此兩個 2024 折僅為回溯演示，不能稱為全新樣本外驗證。"
        "2024 的細測候選僅允許用於 2025 折；不倒灌到較早的 2024 訓練選參。",
        "## 開發區參數敏感度（2024）",
        f"固定 V1：報酬 {percent(baseline['total_return'])}，"
        f"回撤 {percent(baseline['max_drawdown'])}。"
        f"12 組粗測中 {positive} 組正報酬，範圍 {percent(minimum)} 至 {percent(maximum)}。"
        f"另執行 {len(refined)} 組局部細測。這些是開發績效，不是 OOS 證據。",
        table(
            ["Entry", "Exit", "ATR倍數", "Risk", "Return", "DD", "Sharpe", "Trades"],
            [
                [
                    r["params"]["entry_period"],
                    r["params"]["exit_period"],
                    r["params"]["atr_multiplier"],
                    percent(r["params"]["risk_per_trade"]),
                    percent(r["total_return"]),
                    percent(r["max_drawdown"]),
                    f"{r['sharpe']:.3f}",
                    r["stats"]["trades"],
                ]
                for r in development + refined
            ],
        ),
        "## Walk-Forward",
        "每折採先前 15 個月訓練、後 6 個月測試。至少 10 筆訓練交易才可選；"
        "最大化訓練 daily-wallet Sharpe，平手依較低回撤與固定參數排序。"
        "沒有合格候選則持有現金。表中參數順序為 Entry / Exit / ATR / Risk。",
        table(
            [
                "Fold",
                "Training",
                "Test",
                "Parameters",
                "OOS Return",
                "Wallet DD",
                "Mark DD",
                "Trades",
            ],
            fold_rows,
        ),
        f"2025 兩折幾何串聯報酬：**{percent(summary['pristine_2025_linked_return'])}**。"
        "各折期初重設 10,000 USDT、在邊界平倉；串聯數字不是一個不中斷引擎回測，"
        "不據此合成單一 Sharpe 或最大回撤。第二折可依預定規則使用第一折已發生的資料訓練。",
        f"2025 OOS 共 {oos_trades} 筆交易；樣本數與期間有限，不能從最佳開發參數推論長期獲利。",
        "## 年份與市場狀態（固定 V1、2x 上限）",
        table(
            ["Period", "Return", "DD", "Long", "Short", "Fees", "Slip proxy", "Funding"],
            [
                [
                    r["period"],
                    percent(r["total_return"]),
                    percent(r["max_drawdown"]),
                    r["long"]["trades"],
                    r["short"]["trades"],
                    f"{r['costs'].get('trading_fees', 0):.2f}",
                    f"{r['costs'].get('slippage_proxy', 0):.2f}",
                    f"{r['costs'].get('funding_cost_net', 0):.2f}",
                ]
                for r in summary["historical"]
            ],
        ),
        "市場分類僅使用進場當時最後已完成日線的 90 日報酬（±15%）與 90 日年化波動（60%）。"
        f"按進場分類的交易筆數：{coverage}；波動分類：{volatility}。"
        "分類表為觀察性分組；部分分類交易稀少，不能視為各市場狀態都已獨立驗證。",
        "## 成本敏感度（2024、固定 V1）",
        table(["Taker bps/side", "Slip bps/side", "Return", "DD", "Trades"], cost_rows),
        "成本改變會同時影響風險倉位與實現損益。滑價仍是費用代理；"
        "沒有訂單簿、成交機率、跳空停損或真實市場衝擊重播。Taker 2 bps 情境不代表 maker 成交。",
        "## 同資金／同區間／同 1x 合約槓桿比較（2025）",
        table(
            [
                "Variant",
                "Return",
                "Mark DD",
                "Mark Sharpe",
                "Trades",
                "Mean entry exposure",
                "Funding USDT",
            ],
            compare_rows,
        ),
        protocol["comparison"]["interpretation"],
        "買入持有是 BTC 永續合約 1x 多單參考，計實際 Funding，不是現貨 BTC 指數。"
        "它沒有 ATR 停損或 0.5% 單筆預算風險；因此不能把不同曝險的報酬直接排名為策略優劣。",
        "Mark 指標以原生實際成交及每小時 Mark Price 開盤估值重建，含進出費與實際 Funding；"
        "日報酬以 UTC 日末估值，Sharpe 取 365 日年化、零無風險利率，"
        "Sortino 採全部日數的負報酬平方均值。"
        "這是報告層的風險核對，不產生交易，不變更原先 wallet-Sharpe 選參規則。"
        "Wallet 指標不包含持倉中浮動損益，長持倉參考尤其不能以 wallet DD 代表完整風險。",
        "## 限制與下一步",
        "- 2026 最終測試尚未開封；不再以 2025 結果調參。後續新想法須另開研究版本。\n"
        "- 目前為有成本、可追溯的回測研究；原生引擎不計清算費，交易規格並非歷史逐時點規格。\n"
        "- 原生 lookahead 測試及單元測試不保證消除所有模型或資料偏誤。\n"
        "- 未進行持續 dry-run、細週期停損壓力驗證或實盤。上述缺口完成前不部署。\n"
        "- 以凍結模型做額外連續時間 OOS、等波動曝險比較與交易成本壓力測試，"
        "不能重新挑選 2025 的贏家。",
        "來源：[凍結計畫](protocol.json)、[資料稽核](data_manifest.json)、"
        "[完整機器摘要](summary.json)、[回測索引](runs.csv)、[市場狀態](regimes.csv)。\n\n"
        "框架依據：[Freqtrade 回測假設](https://www.freqtrade.io/en/stable/backtesting/#assumptions-made-by-backtesting)、"
        "[合約與 Funding](https://www.freqtrade.io/en/stable/leverage/)。",
    ]
    (STUDY / "REPORT.md").write_text("\n\n".join(sections) + "\n")
    print("Saved user_data/research/study/REPORT.md")


if __name__ == "__main__":
    main()

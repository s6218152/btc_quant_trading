---
name: backtest-analyzer
description: Analyze this repository's Freqtrade JSON or ZIP results, costs, drawdowns and parameter comparisons when explicitly requested.
---

Use the pinned research container; do not install another analytics framework.
Run `python3 scripts/research.py analyze --result <repository-relative JSON-or-ZIP>`.
For the frozen study, read `user_data/research/study/summary.json` and the compact CSVs.

- Report return, CAGR, wallet-based Sharpe/Sortino/drawdown, PF, expectancy,
  average win/loss, trade counts, long/short, annual realized PnL and loss streaks.
- Use the study's hourly Mark-equity risk metrics alongside native wallet
  metrics; wallet balance omits open-position PnL. Frozen selection used wallets.
- Separate fees, the slippage cash-cost proxy and signed funding. Check PnL
  reconciliation and the result's code/config/data identity before comparisons.
- Compare only matching dates, capital and leverage; disclose exposure and
  benchmark risk differences. Never rank training fits as out-of-sample evidence.
- Read summary fields first. Save full trade tables locally; never print them
  or large OHLCV arrays into Codex context. Return a short finding with paths.

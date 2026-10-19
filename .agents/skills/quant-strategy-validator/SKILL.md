---
name: quant-strategy-validator
description: Validate this repository's quantitative research for leakage, data integrity, costs and chronological out-of-sample robustness when explicitly requested.
---

Use `user_data/research/study/protocol.json` as the frozen basis. Check saved
artifacts first; rerun only missing checks or checks invalidated by code/data changes.

- Run `python3 scripts/research.py test` and the native lookahead check for
  signal changes. Inspect UTC grids, duplicate/missing candles, funding events,
  completed-day alignment, source hashes and stop/risk accounting.
- Use `python3 scripts/research.py study` for the registered development,
  fee/slippage scenarios and chronological walk-forward workflow. It resumes
  matching cached runs; never rebuild a backtesting engine or silently zero funding.
- Verify that candidate selection precedes each OOS window, no future trade
  enters a training score, and selected configs are frozen before evaluation.
- Inspect `user_data/research/study/validation.json`; it verifies native ZIP
  hashes, decision chronology, exclusive boundaries and marked final equity.
- Distinguish retrospective 2024 slices from previously unobserved 2025 OOS.
  Keep 2026 sealed; do not tune from OOS results or automatically deploy winners.
- Assess parameter plateaus, yearly/regime and long/short coverage, trade
  sample size, cost sensitivity and benchmark exposure differences. Native
  lookahead success alone is not proof of profitability or full validity.
- Return passed/failed/unverified boundaries with artifact paths, not raw trades.

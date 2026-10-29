# BTCUSDT Donchian research

- Research BTC/USDT:USDT 4h / 1d with Freqtrade, Pandas, NumPy, Pytest and Ruff.
- Backtesting / dry-run only. Never enable live orders or load legacy API keys.
- The user authorized removing the unused legacy system on 2026-10-29.
  Keep only the selected BTC long-only strategy available to the paper service.
  Preserve its inherited base code, frozen research references, data and evidence;
  these dependencies are not unused alternative trading systems.
- Use the pinned Docker environment through `python3 scripts/research.py`.
  Use Freqtrade's exchange, downloader, engine and order management.
- Prefer RTK-supported shell output, targeted `rg` and small source ranges.
  If `.codegraph/` exists, use CodeGraph first for symbol/flow discovery.
  Do not repeat scans or print OHLCV / complete trade lists / successful logs.
- Run focused `pytest -q`, then relevant integration checks. Preserve failures.
- Validate UTC timestamps, closed candles, missing/duplicate data and funding.
  Never replace missing funding with zero for formal results.
- Shift Donchian windows; merge only completed daily candles. Test prefix
  invariance, stoploss leverage conversion and risk sizing before backtests.
- Save data, logs, trades and manifests locally. Report concise metrics and
  cost-model limitations. Keep OOS/final holdouts separate from development.
- Resume the frozen study with `research.py study`; never tune from 2025 OOS
  or open 2026. Mark-equity reporting must not change frozen selection scores.
- Use existing token-efficient-workflow / headroom as needed; debugging skill
  for failures. Quant analyzer/validator skills are opt-in, not every-task hooks.
- Use Context7 / official docs for framework contracts. REA and browser MCPs
  are unnecessary for ordinary strategy work. Do not add overlapping tools.
- Do not overwrite user hooks or silently change global MCP settings.

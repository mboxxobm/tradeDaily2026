# Axiory US100 / JP225 market-context data

A cumulative snapshot of completed MT5 bars used by the daily trading journal. The latest full retrieval was run on 2026-10-14 JST; weekday updates extend the master CSVs.

| Symbol | Files | First usable history (UTC bar-open time) |
|---|---|---|
| Axiory `JP225` | `JP225_M1.csv`, `JP225_M5.csv`, `JP225_H1.csv` | 2025-04-07 01:00 |
| Axiory `JP225` | `JP225_D1.csv` | 2023-01-03 00:00 |
| Axiory `JP225` | `JP225_W1.csv` | 2023-01-08 00:00 |
| Axiory `US100` | `US100_M1.csv`, `US100_M5.csv`, `US100_H1.csv` | 2025-04-07 01:00 |
| Axiory `US100` | `US100_D1.csv` | 2023-01-03 00:00 |
| Axiory `US100` | `US100_W1.csv` | 2023-01-08 00:00 |

The currently available row counts and latest completed-bar boundaries are listed in each daily report under `diaries/YYYY-MM-DD/market_context/market_context.md`.

## CSV format

Columns are `time_utc`, `open`, `high`, `low`, `close`, `tick_volume`, `spread`, and `real_volume`. `time_utc` is the bar-open timestamp in UTC (ISO 8601 with `Z`). Display times in journal charts are converted to JST. Forming bars are omitted.

`M1` means one-minute OHLC candles. `tick_volume` is the number of price updates counted inside a bar; these files do not contain a timestamped raw-tick stream. The current MA/W%R calculations need completed OHLC candles, not raw ticks. MT5 CFDs may report `real_volume` as zero.

Axiory symbols `JP225` and `US100` are separate CFD price series. Do not merge their prices with another broker's `JP225Cash` / `US100Cash` files.

## Update and journal recipe

The repository updater is [`scripts/update_market_context.py`](../../scripts/update_market_context.py). The indicator, chart, timeframe, and daily-update rules are in [`diaries/market_context_recipe.md`](../../diaries/market_context_recipe.md). It preserves cumulative master CSVs, retrieves a two-day overlap on later runs, and saves differences and runtime logs in local ignored paths. The script produces a JST Markdown report and one multi-timeframe chart per symbol in the daily diary folder. If that date's main diary already exists, it also inserts or refreshes the marked Axiory context section.

The daily updater changes the local repository working tree. GitHub is updated only when the reviewed changes are committed and pushed.
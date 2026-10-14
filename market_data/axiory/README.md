# Axiory US100 / JP225 intraday history

This folder contains the Axiory MT5 OHLC bar history used as source data for the daily market-context section.

| File | Instrument | Timeframe | Rows | Coverage (bar-open time, UTC) |
|---|---|---:|---:|---|
| `JP225_M5.csv` | Axiory `JP225` | 5 minutes | 107,921 | 2025-04-07 01:00 to 2026-10-14 04:50 |
| `JP225_H1.csv` | Axiory `JP225` | 1 hour | 9,004 | 2025-04-07 01:00 to 2026-10-14 03:00 |
| `US100_M5.csv` | Axiory `US100` | 5 minutes | 107,632 | 2025-04-07 01:00 to 2026-10-14 04:50 |
| `US100_H1.csv` | Axiory `US100` | 1 hour | 8,979 | 2025-04-07 01:00 to 2026-10-14 03:00 |

The snapshot was downloaded on 2026-10-14 (JST). It contains completed bars, not raw ticks. CSV timestamps use ISO 8601 UTC (`Z`); convert to JST for journal display. The first usable intraday history from this Axiory terminal is 2025-04-07. These are Axiory CFD price series (`JP225` / `US100`), not XM `JP225Cash` / `US100Cash` or exchange index-futures data.

Each CSV has these columns: `time_utc`, `open`, `high`, `low`, `close`, `tick_volume`, `spread`, `real_volume`.

The files support the journal context calculations: M5 60-period EMA, H1 20-period SMA, and H1 Williams %R (15). Indicator values should be calculated from completed bars only.

This is a snapshot. The weekday updater currently runs on the Windows MT5 workstation and updates its local `mt5History` output; it does not automatically push refreshed files to this repository. To keep this copy current, refresh the local master bars and commit the updated files here.

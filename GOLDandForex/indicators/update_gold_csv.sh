#!/bin/sh
# Mac/Linux: ./update_gold_csv.sh "/path/to/MQL5/Files"   (cron: 0 8,10 * * * /path/update_gold_csv.sh "...")
DEST="${1:-.}"
URL="https://raw.githubusercontent.com/mboxxobm/tradeDaily2026/main/GOLDandForex/GOLD_retail_daily.csv"
curl -fsSL "$URL" -o "$DEST/GOLD_retail_daily.csv.tmp" && mv "$DEST/GOLD_retail_daily.csv.tmp" "$DEST/GOLD_retail_daily.csv"

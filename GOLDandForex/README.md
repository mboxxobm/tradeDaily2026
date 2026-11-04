# GOLDandForex — GOLD（XAUUSD）リテール偏り 日次データ

[forexsentiment.live](https://forexsentiment.live/detail/XAUUSD) が公開している **リテール（個人投資家）のロング比率** を日次で記録したものです。
OANDA のオープンポジション比率などとは **集計元が違う** ため、数値は一致しません（傾向を見る用途向け）。

値はすべてサイトの実データ（詳細ページ埋め込みJSON `fxc-data` の `sentiment`、および `/api/chart-bars.php?symbol=XAUUSD&tf=H1` の3時間ごとの集計）をそのまま保存しています。加工・補間はしていません。

## ファイル

| ファイル | 内容 |
|---|---|
| `GOLD_retail_daily.csv` | 日次値（2025-05-12〜、キー: `date_utc`、重複なし） |
| `GOLD_retail_daily.md` | 上記CSVから毎回再生成する一覧（月次サマリー＋日次表・新しい順） |
| `GOLD_retail_raw.csv` | 3時間ごとの集計値（2026-10-02 以降のみサイトが公開、キー: `datetime_utc`） |
| `GOLD_retail_0000utc.csv` | 参考: 各日 00:00 UTC（09:00 JST）時点の集計値 |
| `collect_forexsentiment.py` | 収集スクリプト（Python標準ライブラリのみ） |

### `GOLD_retail_daily.csv` の列

| 列 | 意味 |
|---|---|
| `date_utc` | 日付（UTC） |
| `time_utc` | その日の値と一致した最後の3時間集計の時刻（UTC）。サイトが時刻を出していない日は空欄 |
| `datetime_jst` | 同じ時刻の日本時間 |
| `long_pct` | ロング比率（%） |
| `short_pct` | ショート比率（% = 100 − ロング） |
| `price_if_any` | その日の日足終値（NY 17:00 クローズ）。土日など日足がない日は空欄。当日分は形成中 |
| `source` | 取得元 |
| `status` | `final`（確定）/ `provisional`（暫定） |
| `notes` | 補足（価格の意味、3時間集計との照合結果など） |

## タイミングの注意

- **日次値はUTCその日の最後の集計**（≒ 21:00 UTC = 翌朝 6:00 JST）です。00:00 UTC の値ではありません。
- **当日の行は暫定**（`provisional`）で、日中の集計ごとに値が変わります。翌日以降の実行で確定値に上書きされます。
- **2026年4月中旬までは土日の行も含まれます**（サイト側の系列がそうなっているため）。それ以降は原則平日のみです（例外的に数日だけ土日あり）。
- 3時間ごとの集計（`GOLD_retail_raw.csv`）はサイトが 2026-10-02 以降しか公開していません。

## 手動で実行

```bash
python GOLDandForex/collect_forexsentiment.py
# 生レスポンスを raw_responses/ に保存したいとき（デバッグ用）
python GOLDandForex/collect_forexsentiment.py --debug
```

スクリプトは自分と同じフォルダのCSVを読み書きするので、どこから実行しても構いません。何度実行しても日付の重複は発生せず、暫定行だけが更新されます。

## 自動収集（GitHub Actions）

`.github/workflows/gold_retail_daily.yml` が毎日 **07:30 JST**（22:30 UTC）と **09:15 JST**（00:15 UTC）に実行し、`GOLDandForex/` に変化があったときだけ `github-actions[bot]` としてコミットします（メッセージ: `GOLD retail bias: update YYYY-MM-DD`）。
Actions タブから手動実行（workflow_dispatch）も可能です。

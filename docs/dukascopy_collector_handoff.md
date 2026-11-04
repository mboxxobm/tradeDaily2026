# Dukascopy 足コレクターの引き継ぎ

JP225 / US100 の足を、PC がオフでも GitHub Actions で貯めるためのクラウド側です。Axiory MT5 の `market_data/axiory/` とは別系統です。価格もファイルも混ぜません。

## どのファイルが本体か

| パス | 役割 |
|---|---|
| `scripts/collect_dukascopy_candles.py` | 取得・保存・M5/H1/D1/W1 の再集計 |
| `scripts/requirements-dukascopy.txt` | `requests` と `pandas` |
| `scripts/test_dukascopy_candles.py` | ネットワークなしのテスト |
| `market_data/dukascopy/` | 保存先。説明はそこの README |
| `.github/workflows/dukascopy_candles.yml` | 毎日 00:30 UTC と手動実行 |
| `scripts/update_market_context.py` | 日誌チャート。既定は Axiory。CSV が無いときだけ Dukascopy |

手動実行:

```bash
python -m pip install -r scripts/requirements-dukascopy.txt
python scripts/collect_dukascopy_candles.py
python scripts/collect_dukascopy_candles.py --symbols US100 --backfill-from 2026-10-01 --max-days 40
```

## CSV

列は Axiory と同じです。

`time_utc,open,high,low,close,tick_volume,spread,real_volume`

- `time_utc` は足の開始。末尾 `Z`。
- 価格は Dukascopy の整数を 1000 で割った BID。
- `tick_volume` は Dukascopy の足出来高。Axiory の価格更新回数とは別物。
- `spread` と `real_volume` はフィードに無いので `0`。
- 出来高 0 の穴埋め足と、まだ確定していない足は捨てます。
- M1 だけ月次です。`market_data/dukascopy/m1/{銘柄}_M1_YYYY-MM.csv`。GitHub の 100MB 制限を避けるためです。
- M5 / H1 / D1 / W1 は `market_data/dukascopy/{銘柄}_{足}.csv` の1ファイル。M1 から作ります。
- D1 は 0:00、W1 は日曜 0:00。区切りは Axiory の CSV に合わせています。
- `coverage.csv` は取得した UTC 日（`ok` / `empty`）です。日次の再開位置に使います。

## 時刻

Dukascopy の bi5 は UTC 日のファイルです。URL の月は 0 始まりです。

`https://datafeed.dukascopy.com/datafeed/{SYM}/{YYYY}/{MM0}/{DD}/BID_candles_min_1.bi5`

中身は LZMA。24 バイトのビッグエンディアン `秒, open, close, low, high, volume`。

保存する `time_utc` は、その UTC に次を足した時刻です。Axiory CSV の時計と揃えるためです。

- 米国東部の冬時間: +2 時間
- 米国東部の夏時間: +3 時間

実装は `axiory_clock_shift()` です。2026-01-20、07-15、10-14、10-28、11-02 の M1 で、足したあとの値動きが Axiory と一致することを確認しています。

## ワークフロー

`.github/workflows/dukascopy_candles.yml`

- 毎日 00:30 UTC（09:30 JST）。土日も動きます。
- 手動実行は Actions の **Dukascopy JP225 US100 candles**。
- `permissions: contents: write`。push が 403 なら、Settings → Actions → General → Workflow permissions を Read and write にします。
- 変化があったときだけ commit します。push の前に `git pull --rebase` を、失敗したら待ち時間を延ばして再試行します。
- 通常実行は、各銘柄の最後の取得日の直前から昨日までです。古い穴は埋めません。
- `backfill_from` を付けると、その日以降の未取得日を埋めます。1回の上限は `max_days`（既定 40。`0` は無制限）。ログに残り日数が出たら、同じ入力でもう一度実行します。取得済みは飛ばすので、途中失敗でも次が同じ穴の先頭から続きます。
- ジョブ上限は 6 時間です。40 日ずつなら、Dukascopy が 503 やタイムアウトを返してもその中に収まる想定です。

このリポジトリに入っている範囲は、JP225 が 2025-03-31 から 2026-11-03 までです。US100 は 2025-03-31 から 2025-09-09 までと、日次に必要な 2026-09-30 から 2026-11-03 までです。あいだの US100 は意図的に未取得です。深い過去は別ソースで足してかまいません。日次ワークフローの US100 更新（末尾への追加）は残しています。

足データだけの commit は、GitHub Pages の `paths-ignore` と `vercel.json` の `ignoreCommand` でサイト再ビルドを起こさないようにしてあります。

## ソースを足す・入れ替える

1. 銘柄とフィード名の対応は `SYMBOLS`（JP225=`JPNIDXJPY`、US100=`USATECHIDXUSD`）です。銘柄を足すならここに追加し、出力ファイル名は `{銘柄}_M5.csv` のように既存と同じにします。
2. 取得関数は `http_get()` と `fetch_day_bytes()` です。Dukascopy は SSL ハンドシェイクで固まることがあるので、1 リクエストを別プロセスにし、約 50 秒で打ち切ります。503 とタイムアウトは待って再試行します。
3. 別の配信元に替えるときは、`decode_m1()` の代わりにそのソースの足を同じ列の DataFrame にします。`time_utc` を Axiory の時計に揃えるかどうかは、日誌の日付区切りと照合して決めてください。揃えない場合は D1/W1 の区切りが Axiory とずれます。
4. 保存は `market_data/dukascopy/` だけです。`refuse_axiory_output()` が axiory への書き込みを拒否します。新しいソースの CSV も axiory には置かず、別フォルダにしてください。
5. 日次が古い穴まで遡らないのは `plan_dates()` です。末尾更新のままにするなら、この関数の「最後の取得日の直前から」を崩さないでください。範囲を指定して埋めるのは `--backfill-from` です。
6. 上位足は `build_higher_timeframes()` が M1 から作ります。D1 は対象日とその前日の UTC ファイルが `coverage.csv` にあるときだけ確定にします。
7. テストは `python scripts/test_dukascopy_candles.py` です。ライブの Dukascopy は叩きません。

日誌チャートをこの CSV から出すときは、Axiory のファイルが無い状態で次を実行します。既定の MT5 取得は変わりません。

```bash
python scripts/update_market_context.py --report-only --source dukascopy
```

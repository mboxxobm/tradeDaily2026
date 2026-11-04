# Dukascopy の JP225 / US100 足データ

PC がオフの日でも足が欠けないように、GitHub Actions が Dukascopy の公開データフィードから **JP225（日経225 CFD）** と **US100（ナスダック100 CFD）** の確定足を毎日蓄積します。認証は不要です。

このフォルダは Axiory MT5 の `market_data/axiory/` とは **別系統** です。価格を混ぜたり、Axiory の CSV を書き換えたりしません。

## Axiory との違い

Dukascopy の `JPNIDXJPY` / `USATECHIDXUSD` は、Axiory の `JP225` / `US100` と同じく株価指数の CFD で、現物指数の動きによく追従します。ただし配信元が違うため、**終値は近くても一致しません**。約定価格や損益の照合には使わないでください。XM などの `JP225Cash` / `US100Cash` とも別です。

保存しているのは BID のローソク足です。ASK ではありません。

## フォルダ

```text
market_data/dukascopy/
├─ README.md
├─ coverage.csv                 取得済みのUTC日（ok / empty）
├─ JP225_M5.csv / US100_M5.csv
├─ JP225_H1.csv / US100_H1.csv
├─ JP225_D1.csv / US100_D1.csv
├─ JP225_W1.csv / US100_W1.csv
└─ m1/
   ├─ JP225_M1_YYYY-MM.csv      1分足（月ごと）
   └─ US100_M1_YYYY-MM.csv
```

1分足だけ月次ファイルです。年単位の1本の M1 CSV は GitHub の 100MB 制限と 50MB の警告に近づくため、月で分けています。M5 以降は本数が少なく、Axiory と同じ `銘柄_時間足.csv` の1ファイルです。

## 列

Axiory の CSV と同じ列です。`time_utc` は足の開始時刻（UTC、末尾 `Z`）。

| 列 | 内容 |
|---|---|
| `time_utc` | 足の開始。M1 は分、M5 は5分、H1 は時、D1 は UTC の 0:00、W1 は日曜 0:00 UTC |
| `open`, `high`, `low`, `close` | BID。Dukascopy の整数価格を 1000 で割った値 |
| `tick_volume` | Dukascopy の足出来高。Axiory の「価格更新回数」とは意味が違います |
| `spread` | フィードに無いため `0` |
| `real_volume` | フィードに無いため `0` |

出来高 0 の穴埋め足（取引のない分）は保存しません。取得時点でまだ確定していない足も入れません。Dukascopy は UTC の日が終わったあとに、その日のファイルを出します。

## 時刻の合わせ方

Dukascopy のファイルに入っている秒は UTC です。一方、このリポジトリの Axiory CSV の `time_utc` は、同じ値動きと比べると UTC そのものではありません。2026年1月20日、7月15日、10月14日、10月28日、11月2日の1分足で確認した差は次のとおりです。

| アメリカ東部 | Dukascopy の UTC に足す時間 | 例 |
|---|---:|---|
| 冬時間（EST） | 2時間 | 2026-01-20、2026-11-02 |
| 夏時間（EDT） | 3時間 | 2026-07-15、2026-10-14、2026-10-28 |

夏冬の切り替え日はアメリカ東部（`America/New_York`）に合わせています。欧州の夏時間（10月最終日曜）では差は変わりませんでした。足したあとの1分足は Axiory とほぼ同じ時刻で動き、終値の差は時刻を揃えないときの数百ポイントではなく、おおむね数ポイントから数十ポイントです。

保存する `time_utc` はこの合わせた時刻です。日誌側が Axiory の CSV を JST（+9時間）として読んでいるので、代替チャートの日付の区切りもそちらに揃えます。Dukascopy の取得ファイル自体は UTC 日のままです（`coverage.csv` の日付は取得した UTC 日）。

## 上位足の区切り

M5 / H1 / D1 / W1 は、保存した M1 を束ねて作ります。Dukascopy 自身の日足・週足は使っていません。区切りを Axiory の CSV に合わせるためです。

- **D1**: UTC の日付（0:00 UTC = 09:00 JST）
- **W1**: 日曜 0:00 UTC から次の日曜の直前まで。週の7日がすべて取得済みで、週が終わっているときだけ確定足として書きます
- 途中の欠けた週は週足にしません。次の実行が穴を埋めたあとで作られます

## 差分更新

- 既存の最終日から **2日分を重ねて** 取り直し、`time_utc` が同じ行は新しい方を残します。何度実行しても同じ結果になります。
- 取得に失敗した日は `coverage.csv` に書かず、次回の穴として取り直します。
- 直近の「ファイルがまだ無い」（404）は、未公開と休場の区別が付かないため、2日以上経ってから空日として確定します。それまでは次回も再取得します。
- 有効なファイルで出来高のある足が無い日（休場など）は、その場で空日にします。
- 初期状態で CSV が無いときの開始日は **2026-10-01** です。それより前は `--backfill-from` で指定します。揃えた日足は UTC の前日ファイルにもまたがるので、実装はその前日も取ります。
- `--backfill-from` でも、`coverage.csv` に既にある日は直近2日以外を取り直しません。指定した日以降の未取得日だけを埋めます。最初から取り直すときは、その銘柄の行を `coverage.csv` から消して再実行します。
- 日次の通常実行は、各銘柄の**最後の取得日の直前から昨日まで**だけです。それより前に残した穴は埋めにいきません。
- 1回の実行は既定で **40日**までです（`--max-days`）。未取得日は古い方から取り、末尾だけ先に埋めて途中を穴にしません。

## いま入っている範囲

| 銘柄 | UTCファイルの範囲 | メモ |
|---|---|---|
| JP225 | 2025-03-31 ～ 2026-11-03 | 日次更新の対象 |
| US100 | 2025-03-31 ～ 2025-09-09 と、2026-09-30 ～ 2026-11-03 | あいだは未取得。深い過去はここで足さない。日次は末尾だけ更新する |

US100 の未取得期間は、あとから別のソースで埋めてもかまいません。混ぜる先は `market_data/axiory/` でも、この Dukascopy の CSV でもありません。引き継ぎは [docs/dukascopy_collector_handoff.md](../../docs/dukascopy_collector_handoff.md) です。

## 手動実行

```bash
python -m pip install -r scripts/requirements-dukascopy.txt
python scripts/collect_dukascopy_candles.py
python scripts/collect_dukascopy_candles.py --backfill-from 2026-10-01 --max-days 40
python scripts/collect_dukascopy_candles.py --compare-axiory
```

`--compare-axiory` は Axiory の M1 を読むだけで、書き換えません。重なった足の終値差（Dukascopy − Axiory）の中央値を表示します。

## 自動実行（GitHub Actions）

`.github/workflows/dukascopy_candles.yml` が毎日 **00:30 UTC（09:30 JST）** に動きます。土日も動きます。金曜の深夜から日曜夕方に付く足を、PC の平日タスクでは取りこぼすためです。休場日は空のまま成功します。

Actions の **Dukascopy JP225 US100 candles → Run workflow** から手動実行できます。`backfill_from` を空にすると、各銘柄の末尾だけを更新します（US100 の 2025-09-10～2026-09-29 のような古い穴は対象外です）。日付を入れると、その日以降の未取得日を **1回40日**（`max_days` で変更。`0` は無制限）ずつ埋めます。ログに「残り」が出たら、同じ入力でもう一度実行します。取得済みの日は飛ばすので、途中で落ちても次の実行が続き、未取得日を飛ばして先だけ埋めることはしません。1回のジョブ上限は6時間です。40日なら、サーバが遅くてもその中に収まる想定です。

変化があったときだけ `github-actions[bot]` が `Dukascopy candles: update YYYY-MM-DD` として commit します。push の前に `git pull --rebase` を、失敗時は待ち時間を延ばしながら再試行します。ほかの自動更新が `main` に先に入っても、足データだけを重ねられるようにするためです。

このコミットはサイトの再デプロイを起こさないようにしてあります。

- GitHub Pages（`.github/workflows/pages.yml`）は、変更が `market_data/dukascopy/` とこのワークフローファイルだけのときは起動しません。ページ本体の変更を含む commit では、これまでどおりデプロイします。
- Vercel（`vercel.json` の `ignoreCommand`）も、足データだけの commit ではビルドをスキップします。それ以外の変更ではビルドします。判断できないときはビルドする側です。

ワークフローの `GITHUB_TOKEN` に contents の書き込みが必要です。push が 403 になる場合は、GitHub の **Settings → Actions → General → Workflow permissions** を **Read and write permissions** にしてください。

## 日誌チャートの代替

`scripts/update_market_context.py` の初期動作は従来どおり Axiory MT5 です。Axiory の M5/H1/D1/W1 が揃っているときは Dukascopy を読みません。

Axiory の CSV が無い環境では、次で Dukascopy の足から同じ形式のレポートとチャートを作れます。MetaTrader5 パッケージは不要です（`pandas` と `matplotlib` は必要）。

```bash
python scripts/update_market_context.py --report-only --source dukascopy
```

`--source auto`（初期値）の `--report-only` は、Axiory が揃っていれば Axiory、無ければ Dukascopy です。レポートには代替であることと、価格が Axiory と同一ではないことを書きます。

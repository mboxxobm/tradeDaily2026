# GOLD リテール・ロング% インジケーター

データ: `GOLDandForex/GOLD_retail_daily.csv`（forexsentiment.live の XAUUSD リテール Long%、日次、GitHub Actions で毎朝更新）  
Raw URL: https://raw.githubusercontent.com/mboxxobm/tradeDaily2026/main/GOLDandForex/GOLD_retail_daily.csv

## MT5（GoldRetailBias.mq5）
1. MT5 で「ファイル → データフォルダを開く」→ `MQL5\Indicators\` に `GoldRetailBias.mq5` を置く。
2. MetaEditor で開いて「コンパイル」（F7）。
3. `GOLD_retail_daily.csv` を `MQL5\Files\` に置く（入力 `InpCommonFolder=true` の場合は `%APPDATA%\MetaQuotes\Terminal\Common\Files\`）。
4. GOLD/XAUUSD チャートにナビゲーターからドラッグ。下段に Long%（0–100）と 70/50/30 ラインが出ます。レベル・Short%表示・ヒストグラム（50基準）・アラートは入力で変更可。
5. 10分ごと（`InpReloadMinutes`）に CSV を再読込します。CSV の更新方法はどちらか:
   - **A. 更新スクリプト（おすすめ）**: `update_gold_csv.ps1` を使う  
     `powershell -ExecutionPolicy Bypass -File update_gold_csv.ps1 -Dest "C:\Users\<you>\AppData\Roaming\MetaQuotes\Terminal\<ID>\MQL5\Files"`  
     タスクスケジューラ →「基本タスクの作成」→ 毎日 08:00 と 10:00 → 操作「プログラムの開始」: `powershell.exe`、引数に上記 `-ExecutionPolicy ...` 以降。  
     Mac/Linux は `update_gold_csv.sh "<MQL5/Filesのパス>"` を cron で。
   - **B. インジ内ダウンロード**: 入力 `InpUseWebRequest=true`。「ツール → オプション → エキスパートアドバイザ → WebRequestを許可するURL」に `https://raw.githubusercontent.com` を追加。
     ※インジケーターから WebRequest が使えない環境もあるため、動かない場合は A を使ってください。

各バーには「バーの日付以下で最新の日付の値」を割り当てる（階段状）ので、どの時間足でも使えます。

## TradingView（GoldRetailBias.pine）
1. `GoldRetailBias.pine` の中身をコピー → Pineエディタに貼り付けて保存 →「チャートに追加」。
2. データは埋め込み式（Pine Seeds は不使用）なので自動更新されません。更新するには `python make_pine.py`（CSV またはGitHubから読み込んで .pine を再生成）を実行するか、GitHub の最新 `GoldRetailBias.pine` をコピーして貼り直してください。

## 注意
- 日付は UTC 日付。MT5 のバー時刻はブローカーのサーバー時間（多くは UTC+2/+3）なので、日足の切替が数時間ずれることがあります。
- Long% は1日1回の値なので、短い時間足では階段状の線になります。

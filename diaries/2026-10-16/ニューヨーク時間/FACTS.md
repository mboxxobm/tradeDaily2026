# 2026-10-16 NY 日誌作成記録

{
  "realized_jpy": -612.7126170396803,
  "by_symbol": {
    "MU": 757.6694480180743,
    "SNDK": -1370.3820650577545
  },
  "closed_count": 7,
  "position": {
    "シンボル": "NASDAQ:AMD",
    "方向": "ショート",
    "数量": "10",
    "平均約定価格": "548",
    "利益確定": "",
    "損切り": "",
    "現在値": "528.75",
    "含み損益 (値)": "30401.13708149084",
    "含み損益 (通貨)": "JPY",
    "含み損益%": "+3.51%",
    "取引額": "865,445.36 JPY",
    "評価額": "895,846.49 JPY",
    "レバレッジ": "4x",
    "証拠金": "223,961.62 JPY",
    "有効期限": ""
  },
  "market_coverage": {
    "MU": {
      "native_seconds": 30,
      "last_completed_end": 1792161390,
      "last_unconfirmed_start": 1792161390,
      "rows": 314,
      "duplicate_volume_equal": true,
      "path": "data/2026-10-16/ny/github/diaries/2026-10-16/source-data/BATS_MU, 30S_67556.csv"
    },
    "SNDK": {
      "native_seconds": 30,
      "last_completed_end": 1792180770,
      "last_unconfirmed_start": 1792180770,
      "rows": 1560,
      "duplicate_volume_equal": true,
      "path": "data/2026-10-16/ny/attached/BATS_SNDK, 30S.csv"
    },
    "AMD": {
      "native_seconds": 30,
      "last_completed_end": 1792180770,
      "last_unconfirmed_start": 1792180770,
      "rows": 1559,
      "duplicate_volume_equal": true,
      "path": "data/2026-10-16/ny/attached/BATS_AMD, 30S.csv"
    },
    "NAS100": "提供GitHub20分足はNY寄り前まで。NY地合いの確定には使わない。"
  },
  "source_commit": "cff196c6491f2c2dd64393cf75d04b7ffe77b483"
}

- CSV5点はユーザーの10/17添付。注文履歴100行・取引履歴116行・活動50行・残高50行・未決済建玉1行。
- 約定秒は注文履歴と、取得範囲内の活動ログで照合。7決済を残高履歴と照合。数量加重平均と取引番号の平均価格を一致させる。
- 合計は残高の未丸め値で−612.7126170396802円。履歴の損益はENTRY/EXIT各行に重複するため一度だけ計上。
- 元の注文履歴CSVは100行制限で、部分取消や過去の変更が完全に保存されているとは保証しない。
- MUの23:33:16と23:35:16の時刻は注文履歴の注文時刻。活動ログで23:35:20にLC・TPの変更があり、設定の履歴すべてを当初計画へ遡らない。
- MUのVolume重複列は同値を照合し1本のみ使用。末尾23:36:30開始足は形成確認がないため除外。DELTAは未取得。
- 10/16 NYのSNDK・AMDは追加BATS 30秒足を使用し、前営業日も保持。全銘柄で30秒/1分/2分/5分・寄りからの表示・再生に対応。SNDK/AMDの末尾04:59:30開始足は除外。
- ユーザーがCSV・HTMLのGitHub pushを明示依頼。FTPは今回の依頼に含まれない。

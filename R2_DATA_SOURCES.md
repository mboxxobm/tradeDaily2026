# R2データ取得

Cloudflare R2にある歩み値CSVを、日付・銘柄単位で取得するための設定です。GitHubにはR2の秘密鍵を保存しません。

## ローカル設定

リポジトリ直下の`.env`に次の値を設定します。

```text
CLOUDFLARE_ACCOUNT_ID=...
R2_BUCKET_NAME=...
R2_S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
R2_PUBLIC_BASE_URL=https://<public-bucket>.r2.dev
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
```

`.env`はGitの無視対象です。アクセスキーやシークレットをGitHub、Notion、HTML、ログへ貼り付けないでください。

## 歩み値CSVのキー規則

```text
{YYYYMMDD}_picked/qr-{銘柄コード}-{YYYYMMDD}.csv
```

例：

```text
20260828_picked/qr-7974-20260828.csv
20260828_picked/qr-6981-20260828.csv
```

## 取得例

公開読み取りURLを使う場合（クラウド作業向け）：

```bash
python3 scripts/r2_fetch_walk_csv.py \
  --date 20260828 \
  --code 7974 \
  --output /tmp/qr-7974-20260828.csv
```

非公開バケットを認証付きで読む場合：

```bash
python3 scripts/r2_fetch_walk_csv.py \
  --source private \
  --date 20260828 \
  --code 7974 \
  --output /tmp/qr-7974-20260828.csv
```

クラウド側では、読み取り専用の一時認証または有効期限付きURLを使い、書き込み用のR2キーを渡さない構成にします。

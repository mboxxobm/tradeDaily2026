# DomainKing サブドメインへの追加ページ公開設定

DomainKing 側が既存サイトのマスターです。現在のダッシュボードや `022tradeDaily/DairyHome.html` を置き換えず、GitHubで今日追加した日誌・グラフ・学習ページを専用サブフォルダに公開して、既存トップからリンクする形にします。

公開URLの想定は `https://matsuicsv.laggy.jp/022tradeDaily/codex2026/` です。FTP上の実際のフォルダ名・階層はサーバーの設定に合わせてください。

## 1. FTP/S の専用アカウントを作る

サブフォルダだけにアクセスできるFTPアカウントを作ります。可能ならFTPSを使います。通常のFTPは接続中に認証情報が暗号化されません。SFTPのみ対応の場合はワークフローの方式変更が必要です。

## 2. GitHub に接続情報を登録する

リポジトリの **Settings → Secrets and variables → Actions** で登録します。パスワードはチャットやリポジトリのファイルには入力しません。

### Repository secrets

- `FTP_SERVER`: サーバー指定の接続先ホスト名
- `FTP_USERNAME`: サブフォルダ用FTPアカウント
- `FTP_PASSWORD`: そのアカウントのパスワード

### Repository variables

- `FTP_SERVER_DIR`: `codex2026` の公開フォルダ。実際のFTPパスを確認し、末尾に `/` を付ける
- `FTP_PROTOCOL`: `ftps`（推奨）
- `FTP_PORT`: サーバー指定のポート。明示がなければFTPSは通常 `21`
- `FTP_DEPLOY_ENABLED`: 設定確認後に `true` にする

`FTP_DEPLOY_ENABLED` が `true` になるまで公開ワークフローは動きません。設定後は `Actions → Deploy to DomainKing subdomain → Run workflow` で初回転送できます。以後はGitHub `main` の更新時に専用サブフォルダだけを同期します。

## 3. 既存サイトとの統合

同期先は新しい `codex2026` フォルダだけにしてください。DomainKingのドメイン直下や既存の `022tradeDaily` フォルダを同期先にすると、マスター側のファイルを上書きするおそれがあります。

初回公開を確認した後、DomainKingの既存トップ／日誌トップに `/022tradeDaily/codex2026/` へのリンクを追加します。既存のトップページや日誌は保持します。GitHub Pagesは切替確認まで残します。

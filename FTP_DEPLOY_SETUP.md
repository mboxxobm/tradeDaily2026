# DomainKing への初回追加設定

DomainKing 側が既存サイトのマスターです。GitHubに追加した日誌・グラフ・学習ページを専用サブフォルダに初回取り込みし、既存トップへ3つのリンクを追加します。既存のダッシュボードや日誌は置き換えません。以後GitHubの更新でDomainKingを自動上書きしません。

公開URLの想定は `https://matsuicsv.laggy.jp/022tradeDaily/codex2026/` です。FTP上の実際のフォルダ名・階層はサーバーの設定に合わせてください。

## 1. FTPアカウント

可能ならFTPSを使います。通常のFTPは接続中に認証情報が暗号化されません。SFTPのみ対応の場合はワークフローの方式変更が必要です。

アカウントには、次の2か所への書き込み権限が必要です。

- `FTP_SERVER_DIR`: GitHub追加ページの専用フォルダ
- `FTP_ROOT_DIR`: DomainKingの既存トップがあるフォルダ。ワークフローはここにある `index.html` だけを更新します

アカウントをサブドメインのWeb領域に限定できる場合は限定してください。同期先は新しい `codex2026` フォルダだけにし、既存トップや既存の `022tradeDaily` 配下全体を同期先に指定しないでください。初回は念のため既存 `index.html` のバックアップを取ってください。

## 2. GitHub に接続情報を登録する

リポジトリの **Settings → Secrets and variables → Actions** で登録します。パスワードはチャットやリポジトリのファイルには入力しません。

### Repository secrets

- `FTP_SERVER`: サーバー指定の接続先ホスト名
- `FTP_USERNAME`: 専用FTPアカウント
- `FTP_PASSWORD`: そのアカウントのパスワード

### Repository variables

- `FTP_SERVER_DIR`: `codex2026` の公開フォルダのFTPパス。末尾に `/` を付ける
- `FTP_ROOT_DIR`: DomainKingトップのFTPフォルダ。ここに既存の `index.html` があることを確認
- `FTP_PROTOCOL`: `ftps`（推奨）
- `FTP_PORT`: サーバー指定のポート。明示がなければFTPSは通常 `21`
- `FTP_DEPLOY_ENABLED`: 接続情報と両方のFTPパスを確認してから `true` にする

## 3. 初回取り込み

`FTP_DEPLOY_ENABLED` が `true` になるまでワークフローは動きません。設定後、GitHubの **Actions → Import GitHub pages into DomainKing → Run workflow** を手動実行してください。

ワークフローはまずGitHubのページ一式を専用フォルダへ同期し、その後、DomainKingの現在の公開トップを読み直してから、依頼された日誌一覧・年間損益グラフ・最新日誌のリンクだけを既存トップに追加します。FTPで更新する既存ファイルはルートの `index.html` だけです。他のDomainKingファイルは削除・置換しません。

初回公開後は `https://matsuicsv.laggy.jp/022tradeDaily/codex2026/` とDomainKingトップの3リンクを確認します。DomainKingが正本で、GitHub Pagesは切替確認用として残します。

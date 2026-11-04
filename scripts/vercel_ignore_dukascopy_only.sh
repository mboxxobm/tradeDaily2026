#!/bin/bash
# Vercel の Ignored Build Step。
# 終了コード 0 = ビルドをスキップ、1 = ビルドする。
# 判断できないときはビルドする（本番の更新を止めない）。

if ! git rev-parse --verify HEAD^ >/dev/null 2>&1; then
  echo "親コミットがないため Vercel のビルドを続行します。"
  exit 1
fi

if ! changed=$(git diff --name-only HEAD^ HEAD); then
  echo "git diff に失敗したため Vercel のビルドを続行します。"
  exit 1
fi

if [ -z "$changed" ]; then
  echo "差分がないため Vercel のビルドをスキップします。"
  exit 0
fi

while IFS= read -r path; do
  [ -z "$path" ] && continue
  case "$path" in
    market_data/dukascopy/*|.github/workflows/dukascopy_candles.yml)
      ;;
    *)
      echo "Dukascopy の足データ以外の変更があるため Vercel のビルドを続行します: $path"
      exit 1
      ;;
  esac
done <<< "$changed"

echo "Dukascopy の足データだけの変更のため Vercel のビルドをスキップします。"
exit 0

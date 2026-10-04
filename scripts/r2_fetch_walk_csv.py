#!/usr/bin/env python3
"""Fetch one TradeDaily walk-value CSV from Cloudflare R2.

The script prefers the public R2 read URL so cloud jobs do not need write
credentials. Use --source private when a private bucket object is required.
Secrets are read from the local .env file and are never printed.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote


DATE_RE = re.compile(r"^\d{8}$")
CODE_RE = re.compile(r"^[0-9A-Za-z]+$")


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip("'\"")


def object_key(date: str, code: str) -> str:
    return f"{date}_picked/qr-{code}-{date}.csv"


def public_url(base_url: str, key: str) -> str:
    encoded_key = "/".join(quote(part, safe="") for part in key.split("/"))
    return f"{base_url.rstrip('/')}/{encoded_key}"


def fetch_public(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "TradeDaily-R2-fetch/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"公開R2 URLの取得に失敗しました: HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"公開R2 URLへ接続できません: {error.reason}") from error


def fetch_private(bucket: str, endpoint: str, key: str, destination: Path) -> None:
    try:
        import boto3
        from botocore.config import Config
    except ImportError as error:
        raise RuntimeError("private取得にはboto3が必要です") from error

    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
    if not access_key or not secret_key:
        raise RuntimeError("R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY が.envにありません")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    client.download_file(bucket, key, str(destination))


def main() -> int:
    parser = argparse.ArgumentParser(description="Cloudflare R2から歩み値CSVを1ファイル取得します")
    parser.add_argument("--date", required=True, help="YYYYMMDD")
    parser.add_argument("--code", required=True, help="銘柄コード")
    parser.add_argument("--output", type=Path, required=True, help="保存先CSV")
    parser.add_argument("--source", choices=("public", "private"), default="public")
    parser.add_argument("--env", type=Path, default=Path(".env"), help="環境変数ファイル")
    args = parser.parse_args()

    if not DATE_RE.fullmatch(args.date):
        parser.error("--dateはYYYYMMDD形式で指定してください")
    if not CODE_RE.fullmatch(args.code):
        parser.error("--codeに使用できない文字が含まれています")

    load_env(args.env)
    key = object_key(args.date, args.code)
    if args.source == "public":
        base_url = os.environ.get("R2_PUBLIC_BASE_URL")
        if not base_url:
            raise SystemExit("R2_PUBLIC_BASE_URLが.envにありません")
        url = public_url(base_url, key)
        fetch_public(url, args.output)
        source_text = "public R2"
    else:
        bucket = os.environ.get("R2_BUCKET_NAME")
        endpoint = os.environ.get("R2_S3_ENDPOINT")
        if not bucket or not endpoint:
            raise SystemExit("R2_BUCKET_NAME / R2_S3_ENDPOINTが.envにありません")
        fetch_private(bucket, endpoint, key, args.output)
        source_text = "private R2"

    print(f"取得完了: {args.output} ({args.date} {args.code}, {source_text})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"エラー: {error}", file=sys.stderr)
        raise SystemExit(1)

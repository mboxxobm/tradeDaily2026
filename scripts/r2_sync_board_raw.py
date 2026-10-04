#!/usr/bin/env python3
"""Synchronize raw BoardReadTools data to a public Cloudflare R2 prefix.

The source files are uploaded byte-for-byte. Credentials are read from the
repository-local .env file and are never printed. Application code, scripts,
and credential files are deliberately excluded from the public data prefix.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DATA_ROOT_DEFAULT = Path("/Volumes/1000GB20260826/2026BC_imac3/板読みTools")
DEFAULT_PREFIX = "board_raw"
DATA_SUFFIXES = (".jsonl.gz", ".log.gz", ".csv", ".json", ".txt", ".html")
EXCLUDED_NAMES = {".DS_Store", ".env", "R2_ENV.txt"}
EXCLUDED_DIRS = {"boardreadtools-app"}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def source_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError(f"source directory does not exist: {root}")
    candidates: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.name in EXCLUDED_NAMES:
            continue
        if path.name.endswith(DATA_SUFFIXES):
            candidates.append(path)
    return sorted(candidates, key=lambda item: item.as_posix())


def total_bytes(files: Iterable[Path]) -> int:
    return sum(path.stat().st_size for path in files)


def load_config(env_path: Path) -> tuple[str, str, str, str, str]:
    values = parse_env(env_path)
    required = (
        "R2_BUCKET_NAME",
        "R2_S3_ENDPOINT",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
    )
    missing = [name for name in required if not values.get(name) and not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"missing R2 configuration in {env_path} or environment: {', '.join(missing)}"
        )

    def get(name: str) -> str:
        return os.environ.get(name) or values[name]

    return (
        get("R2_BUCKET_NAME"),
        get("R2_S3_ENDPOINT"),
        get("R2_ACCESS_KEY_ID"),
        get("R2_SECRET_ACCESS_KEY"),
        values.get("R2_PUBLIC_BASE_URL", os.environ.get("R2_PUBLIC_BASE_URL", "")),
    )


def make_client(endpoint: str, access_key: str, secret_key: str):
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - environment diagnostic
        raise RuntimeError("boto3 is required for --sync") from exc

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "adaptive"}),
    )


def object_key(prefix: str, root: Path, path: Path) -> str:
    return f"{prefix.rstrip('/')}/{path.relative_to(root).as_posix()}"


def upload_one(client, bucket: str, root: Path, path: Path, prefix: str, multipart_threshold: int, multipart_chunksize: int, max_concurrency: int) -> str:
    from boto3.s3.transfer import TransferConfig

    key = object_key(prefix, root, path)
    size = path.stat().st_size
    mtime_ns = str(path.stat().st_mtime_ns)
    try:
        head = client.head_object(Bucket=bucket, Key=key)
        metadata = {str(k).lower(): str(v) for k, v in (head.get("Metadata") or {}).items()}
        if head.get("ContentLength") == size and metadata.get("source-mtime-ns") == mtime_ns:
            return "skipped"
    except Exception as exc:
        error_code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if str(error_code) not in {"404", "NoSuchKey", "NotFound"}:
            raise

    config = TransferConfig(
        multipart_threshold=multipart_threshold,
        multipart_chunksize=multipart_chunksize,
        max_concurrency=max_concurrency,
        use_threads=True,
    )
    client.upload_file(
        str(path),
        bucket,
        key,
        ExtraArgs={
            "ContentType": "application/octet-stream",
            "Metadata": {"source-mtime-ns": mtime_ns},
        },
        Config=config,
    )
    return "uploaded"


def write_manifest(client, bucket: str, root: Path, files: list[Path], prefix: str) -> None:
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_label": root.name,
        "prefix": prefix,
        "file_count": len(files),
        "total_bytes": total_bytes(files),
        "excluded_directories": sorted(EXCLUDED_DIRS),
        "excluded_names": sorted(EXCLUDED_NAMES),
        "files": [
            {
                "key": object_key(prefix, root, path),
                "bytes": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
            }
            for path in files
        ],
    }
    body = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    client.put_object(
        Bucket=bucket,
        Key=f"{prefix.rstrip('/')}/manifest.json",
        Body=body,
        ContentType="application/json; charset=utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="list the exact local files and total size")
    mode.add_argument("--sync", action="store_true", help="upload the selected files to R2")
    parser.add_argument("--source", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--max-concurrency", type=int, default=4)
    args = parser.parse_args()

    if args.max_concurrency < 1:
        parser.error("--max-concurrency must be at least 1")

    files = source_files(args.source)
    size = total_bytes(files)
    print(f"source={args.source}")
    print(f"prefix={args.prefix}")
    print(f"file_count={len(files)}")
    print(f"total_bytes={size}")
    print(f"total_gib={size / (1024 ** 3):.2f}")
    print("excluded_directories=" + ",".join(sorted(EXCLUDED_DIRS)))
    print("excluded_names=" + ",".join(sorted(EXCLUDED_NAMES)))
    if args.dry_run:
        print("sample_files:")
        for path in files[:12]:
            print(f"  {path.relative_to(args.source)}\t{path.stat().st_size}")
        if len(files) > 12:
            print(f"  ... ({len(files) - 12} more)")
        return 0

    bucket, endpoint, access_key, secret_key, public_base = load_config(args.env_file)
    client = make_client(endpoint, access_key, secret_key)
    threshold = 64 * 1024 * 1024
    chunk_size = 64 * 1024 * 1024
    uploaded = 0
    skipped = 0
    for index, path in enumerate(files, start=1):
        result = upload_one(
            client,
            bucket,
            args.source,
            path,
            args.prefix,
            threshold,
            chunk_size,
            args.max_concurrency,
        )
        if result == "uploaded":
            uploaded += 1
        else:
            skipped += 1
        if index == 1 or index % 10 == 0 or index == len(files):
            print(f"progress={index}/{len(files)} uploaded={uploaded} skipped={skipped}", flush=True)

    write_manifest(client, bucket, args.source, files, args.prefix)
    print(f"manifest={args.prefix.rstrip('/')}/manifest.json")
    print(f"uploaded={uploaded}")
    print(f"skipped={skipped}")
    if public_base:
        print(f"public_prefix={public_base.rstrip('/')}/{args.prefix.rstrip('/')}/")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130)

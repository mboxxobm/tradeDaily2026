#!/usr/bin/env python3
"""Dukascopy から JP225 / US100 の確定足を取得し、market_data/dukascopy/ へ蓄積する。

Axiory MT5 の market_data/axiory/ とは別系統。価格を混ぜたり、あちらを書き換えたりしない。

1分足は Dukascopy datafeed の BID bi5（UTC日、月は0始まり）を使う。
レコードは 24 バイトのビッグエンディアン ``sec, open, close, low, high, volume``
で、価格は 1000 分の 1。出来高 0 の穴埋め足は捨て、終了時刻が現在より前の足だけを残す。
保存する time_utc は、Axiory CSV と同じ時計になるよう米国東部の夏時間で +3 時間、
冬時間で +2 時間する。M5 / H1 / D1 / W1 はその時刻で再集計する。D1 は 0:00、
W1 は日曜 0:00（Axiory の CSV と同じ区切り）。
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import lzma
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests


REPO_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_DIR / "market_data" / "dukascopy"
AXIORY_DIR = REPO_DIR / "market_data" / "axiory"

SYMBOLS = (
    ("JP225", "JPNIDXJPY"),
    ("US100", "USATECHIDXUSD"),
)
PRICE_DIVISOR = 1000
OVERLAP_DAYS = 2
DEFAULT_START = date(2026, 10, 1)
BAR_COLUMNS = [
    "time_utc",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "spread",
    "real_volume",
]
HIGHER_TIMEFRAMES = ("M5", "H1", "D1", "W1")
RECORD = struct.Struct(">5if")
MAX_ATTEMPTS = 6
PACE_SECONDS = 0.5
DATAFEED = "https://datafeed.dukascopy.com/datafeed"
USER_AGENT = "Mozilla/5.0 (compatible; tradeDaily2026-dukascopy-collector/1.0)"
RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
NEW_YORK = ZoneInfo("America/New_York")

LOGGER = logging.getLogger("dukascopy_candles")


class DayFetchError(RuntimeError):
    """1日分をリトライ後も取れなかった。"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dukascopy の JP225/US100 BID 足を market_data/dukascopy/ へ差分保存します。"
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=[name for name, _feed in SYMBOLS],
        help="JP225 または US100（初期値: 両方）",
    )
    parser.add_argument(
        "--backfill-from",
        help="このUTC日付から取り直す（YYYY-MM-DD）。既存のそれより前の足は残します。",
    )
    parser.add_argument(
        "--end-date",
        help="このUTC日付まで（含む）。省略時は昨日。今日以降は指定できません。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="出力先（初期値: market_data/dukascopy）",
    )
    parser.add_argument(
        "--compare-axiory",
        action="store_true",
        help="保存済みM1と market_data/axiory のM1を読み取り専用で比べ、中央値差を表示します。",
    )
    parser.add_argument(
        "--pace-seconds",
        type=float,
        default=PACE_SECONDS,
        help="リクエスト間の待ち秒数（初期値: 0.5）",
    )
    parser.add_argument(
        "--max-days",
        type=int,
        default=40,
        help="1回で取りに行くUTC日の上限。0は無制限。未取得日は古い方から取る（初期値: 40）",
    )
    return parser.parse_args(argv)


def parse_iso_date(value: str, label: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{label}はYYYY-MM-DDで指定してください: {value}") from exc


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time_utc": pd.Series(dtype="datetime64[ns, UTC]"),
            "open": pd.Series(dtype="int64"),
            "high": pd.Series(dtype="int64"),
            "low": pd.Series(dtype="int64"),
            "close": pd.Series(dtype="int64"),
            "tick_volume": pd.Series(dtype="float64"),
        }
    )


def fmt_price(raw: int) -> str:
    negative = raw < 0
    number = abs(int(raw))
    whole, frac = divmod(number, PRICE_DIVISOR)
    text = f"{whole}.{frac:03d}".rstrip("0").rstrip(".")
    if not text:
        text = "0"
    return f"-{text}" if negative else text


def price_to_raw(value: object) -> int:
    decimal = Decimal(str(value).strip())
    return int((decimal * PRICE_DIVISOR).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fmt_volume(value: float) -> str:
    rounded = round(float(value), 6)
    if rounded == 0:
        return "0"
    return f"{rounded:.6f}".rstrip("0").rstrip(".")


def axiory_clock_shift(opened_utc: datetime) -> timedelta:
    """Dukascopy の UTC を、Axiory CSV の time_utc と同じ時計へずらす。

    2026-01-20・07-15・10-14・10-28・11-02 の M1 を突き合わせると、同じ値動きは
    米国東部の冬時間で 2 時間、夏時間で 3 時間だけ Axiory 側が先に付いている。
    米国東部の UTC オフセット（冬 -5 / 夏 -4）に 7 時間を足すと、その差になる。
    """
    if opened_utc.tzinfo is None:
        opened_utc = opened_utc.replace(tzinfo=timezone.utc)
    offset = opened_utc.astimezone(NEW_YORK).utcoffset()
    if offset is None:
        raise ValueError(f"米国東部の時差を取れません: {opened_utc.isoformat()}")
    return timedelta(hours=7) + offset


def fmt_time(value: object) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def render_csv(frame: pd.DataFrame) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(BAR_COLUMNS)
    if not frame.empty:
        ordered = frame.sort_values("time_utc")
        for time_utc, open_raw, high_raw, low_raw, close_raw, volume in zip(
            ordered["time_utc"],
            ordered["open"],
            ordered["high"],
            ordered["low"],
            ordered["close"],
            ordered["tick_volume"],
            strict=True,
        ):
            writer.writerow(
                [
                    fmt_time(time_utc),
                    fmt_price(int(open_raw)),
                    fmt_price(int(high_raw)),
                    fmt_price(int(low_raw)),
                    fmt_price(int(close_raw)),
                    fmt_volume(float(volume)),
                    "0",
                    "0",
                ]
            )
    return buffer.getvalue()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, path)
        os.chmod(path, 0o644)
    except Exception:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    atomic_write(path, text)
    return True


def refuse_axiory_output(path: Path) -> Path:
    resolved = path.resolve()
    axiory = AXIORY_DIR.resolve()
    if resolved == axiory or axiory in resolved.parents or resolved.name == "axiory":
        raise ValueError(f"market_data/axiory には書きません: {resolved}")
    return resolved


def frame_from_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return empty_frame()
    raw = pd.read_csv(
        path,
        dtype={column: "string" for column in BAR_COLUMNS},
    )
    if raw.empty:
        return empty_frame()
    missing = [column for column in BAR_COLUMNS if column not in raw.columns]
    if missing:
        raise ValueError(f"{path} の列が不足しています: {', '.join(missing)}")
    frame = pd.DataFrame(
        {
            "time_utc": pd.to_datetime(raw["time_utc"], utc=True),
            "open": raw["open"].map(price_to_raw).astype("int64"),
            "high": raw["high"].map(price_to_raw).astype("int64"),
            "low": raw["low"].map(price_to_raw).astype("int64"),
            "close": raw["close"].map(price_to_raw).astype("int64"),
            "tick_volume": pd.to_numeric(raw["tick_volume"]).round(6),
        }
    )
    return (
        frame.dropna(subset=["time_utc", "open", "high", "low", "close"])
        .sort_values("time_utc")
        .drop_duplicates("time_utc", keep="last")
        .reset_index(drop=True)
    )


def m1_path(root: Path, symbol: str, month: str) -> Path:
    return root / "m1" / f"{symbol}_M1_{month}.csv"


def timeframe_path(root: Path, symbol: str, timeframe: str) -> Path:
    return root / f"{symbol}_{timeframe}.csv"


def load_m1(root: Path, symbol: str) -> pd.DataFrame:
    folder = root / "m1"
    if not folder.exists():
        return empty_frame()
    frames = [frame_from_csv(path) for path in sorted(folder.glob(f"{symbol}_M1_????-??.csv"))]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return empty_frame()
    return (
        pd.concat(frames, ignore_index=True)
        .sort_values("time_utc")
        .drop_duplicates("time_utc", keep="last")
        .reset_index(drop=True)
    )


def load_coverage(path: Path) -> dict[tuple[str, date], tuple[str, int]]:
    if not path.exists():
        return {}
    coverage: dict[tuple[str, date], tuple[str, int]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            coverage[(row["symbol"], date.fromisoformat(row["date_utc"]))] = (
                row["status"],
                int(row["m1_bars"]),
            )
    return coverage


def render_coverage(coverage: dict[tuple[str, date], tuple[str, int]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["symbol", "date_utc", "status", "m1_bars"])
    for symbol, day in sorted(coverage):
        status, bars = coverage[(symbol, day)]
        writer.writerow([symbol, day.isoformat(), status, bars])
    return buffer.getvalue()


def merge_bars(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    frames = [frame for frame in (existing, incoming) if frame is not None and not frame.empty]
    if not frames:
        return empty_frame()
    combined = pd.concat(frames, ignore_index=True)
    return (
        combined.sort_values("time_utc")
        .drop_duplicates("time_utc", keep="last")
        .reset_index(drop=True)
    )


def decode_m1(content: bytes, day: date, now: datetime) -> pd.DataFrame:
    """bi5 を M1 に展開する。出来高0と未確定足は含めない。"""
    try:
        raw = lzma.decompress(content)
    except lzma.LZMAError as exc:
        raise DayFetchError(f"{day.isoformat()} の bi5 を展開できません: {exc}") from exc
    if len(raw) % RECORD.size != 0:
        raise DayFetchError(f"{day.isoformat()} のレコード長が24バイト単位ではありません: {len(raw)}")
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    times: list[datetime] = []
    opens: list[int] = []
    highs: list[int] = []
    lows: list[int] = []
    closes: list[int] = []
    volumes: list[float] = []
    violations = 0
    for offset in range(0, len(raw), RECORD.size):
        second, open_raw, close_raw, low_raw, high_raw, volume = RECORD.unpack_from(raw, offset)
        if volume <= 0:
            continue
        second = int(second)
        if second < 0 or second >= 86400:
            violations += 1
            continue
        if high_raw < low_raw or high_raw < max(open_raw, close_raw) or low_raw > min(open_raw, close_raw):
            violations += 1
            continue
        opened = base + timedelta(seconds=second - (second % 60))
        if opened + timedelta(minutes=1) > now:
            continue
        # 確定判定は Dukascopy の UTC のまま行い、保存時刻だけ Axiory の時計に合わせる。
        times.append(opened + axiory_clock_shift(opened))
        opens.append(int(open_raw))
        highs.append(int(high_raw))
        lows.append(int(low_raw))
        closes.append(int(close_raw))
        volumes.append(round(float(volume), 6))
    if violations and (not times or violations > max(3, int(len(times) * 0.01))):
        raise DayFetchError(f"{day.isoformat()} の OHLC が異常です（{violations}本）")
    if not times:
        return empty_frame()
    frame = pd.DataFrame(
        {
            "time_utc": pd.to_datetime(times, utc=True),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "tick_volume": volumes,
        }
    )
    return (
        frame.sort_values("time_utc")
        .drop_duplicates("time_utc", keep="last")
        .reset_index(drop=True)
    )


def week_start_sunday(times: pd.Series) -> pd.Series:
    """Axiory の W1 と同じ、日曜 00:00 UTC を週の開始にする。"""
    days_since_sunday = (times.dt.day_of_week + 1) % 7
    return times.dt.floor("D") - pd.to_timedelta(days_since_sunday, unit="D")


def aggregate(frame: pd.DataFrame, bucket: pd.Series) -> pd.DataFrame:
    if frame.empty:
        return empty_frame()
    work = frame.copy()
    # 位置で対応づける。足の開始時刻が同じバケットでは時刻順の先頭/末尾を使う。
    work["_bucket"] = pd.to_datetime(pd.Series(bucket).to_numpy(), utc=True)
    work = work.sort_values(["_bucket", "time_utc"])
    grouped = (
        work.groupby("_bucket", sort=True)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            tick_volume=("tick_volume", "sum"),
        )
        .reset_index()
        .rename(columns={"_bucket": "time_utc"})
    )
    grouped = grouped.dropna(subset=["open", "high", "low", "close"])
    for column in ("open", "high", "low", "close"):
        grouped[column] = grouped[column].round().astype("int64")
    grouped["tick_volume"] = grouped["tick_volume"].round(6)
    grouped = grouped.loc[grouped["tick_volume"] > 0]
    return grouped.sort_values("time_utc").reset_index(drop=True)


def drop_unconfirmed(frame: pd.DataFrame, interval: timedelta, now: datetime) -> pd.DataFrame:
    if frame.empty:
        return frame
    closed_by = frame["time_utc"] + interval
    return frame.loc[closed_by <= pd.Timestamp(now)].reset_index(drop=True)


def axiory_day_is_complete(day: date, covered_utc_days: set[date]) -> bool:
    """Axiory の1日は、時計を戻した前後で UTC の2日にまたがる。両方のファイルが必要。"""
    return day in covered_utc_days and (day - timedelta(days=1)) in covered_utc_days


def week_is_complete(week_start: object, covered_utc_days: set[date]) -> bool:
    start = pd.Timestamp(week_start)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    else:
        start = start.tz_convert("UTC")
    start_day = start.date()
    return all(axiory_day_is_complete(start_day + timedelta(days=offset), covered_utc_days) for offset in range(7))


def build_higher_timeframes(
    minute_bars: pd.DataFrame,
    covered: set[date],
    now: datetime,
) -> dict[str, pd.DataFrame]:
    if minute_bars.empty:
        return {timeframe: empty_frame() for timeframe in HIGHER_TIMEFRAMES}
    ordered = minute_bars.sort_values("time_utc").reset_index(drop=True)
    m5 = drop_unconfirmed(aggregate(ordered, ordered["time_utc"].dt.floor("5min")), timedelta(minutes=5), now)
    h1 = drop_unconfirmed(aggregate(ordered, ordered["time_utc"].dt.floor("h")), timedelta(hours=1), now)
    d1 = aggregate(ordered, ordered["time_utc"].dt.floor("D"))
    if not d1.empty:
        d1 = d1.loc[
            d1["time_utc"].map(lambda stamp: axiory_day_is_complete(pd.Timestamp(stamp).date(), covered))
        ].reset_index(drop=True)
    d1 = drop_unconfirmed(d1, timedelta(days=1), now)
    weeks = week_start_sunday(ordered["time_utc"])
    w1 = aggregate(ordered, weeks)
    if not w1.empty:
        w1 = w1.loc[w1["time_utc"].map(lambda stamp: week_is_complete(stamp, covered))].reset_index(drop=True)
    w1 = drop_unconfirmed(w1, timedelta(days=7), now)
    return {"M5": m5, "H1": h1, "D1": d1, "W1": w1}


def limit_fetch_days(days: list[date], end: date, max_days: int | None) -> list[date]:
    """未取得日は古い方から最大 max_days 日。末尾だけ先に埋めて途中を穴にしない。"""
    del end
    if not max_days or max_days < 0 or len(days) <= max_days:
        return days
    return days[:max_days]


def plan_dates(
    covered: set[date],
    *,
    backfill_from: date | None,
    default_start: date,
    end: date,
    max_days: int | None = None,
) -> list[date]:
    """取りに行くUTC日。

    通常実行は最終取得日の直前から昨日までだけ。保存済み範囲の途中にある古い穴は
    埋めにいかない（US100 の深い過去を日次で取り直さないため）。
    --backfill-from を付けたときだけ、その日から昨日までの未取得日を対象にする。
    """
    # 揃えた日足は UTC の前日にもまたがるので、開始日の前日ファイルも取る。
    if backfill_from is not None:
        start = backfill_from - timedelta(days=1)
    elif not covered:
        start = default_start - timedelta(days=1)
    else:
        start = max(covered) - timedelta(days=OVERLAP_DAYS)
    if start > end:
        return []
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    # 取得済みの日は直近の重ね取り以外は飛ばす。バックフィルでも穴と未取得日だけを埋める。
    overlap_start = end - timedelta(days=OVERLAP_DAYS - 1)
    selected = [day for day in days if day not in covered or day >= overlap_start]
    return limit_fetch_days(selected, end, max_days)


def missing_file_is_trusted_empty(day: date, end: date) -> bool:
    """直近の404は未公開のことがあるので、少し経ってから空日として確定する。"""
    return day <= end - timedelta(days=OVERLAP_DAYS)


def backoff_seconds(attempt: int) -> float:
    return float(min(60, 5 * (2**attempt)))


def http_get(url: str) -> tuple[int, bytes]:
    """別プロセスで取得する。SSL ハンドシェイクが固まっても timeout で殺せる。"""
    script = (
        "import sys,requests\n"
        "url, agent = sys.argv[1], sys.argv[2]\n"
        "response = requests.get(url, timeout=(15, 40), headers={'User-Agent': agent})\n"
        "sys.stdout.buffer.write(str(response.status_code).encode() + b'\\n' + (response.content or b''))\n"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, url, USER_AGENT],
            capture_output=True,
            timeout=50,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("Dukascopy request timed out") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", errors="replace")[-400:]
        raise TimeoutError(detail or f"exit {completed.returncode}")
    newline = completed.stdout.find(b"\n")
    if newline < 0:
        raise TimeoutError("empty response")
    return int(completed.stdout[:newline]), completed.stdout[newline + 1 :]


def fetch_day_bytes(
    session: requests.Session,
    feed_symbol: str,
    day: date,
    *,
    pace_seconds: float,
) -> bytes | None:
    """200の中身を返す。404など実体なしは None。失敗が続くと DayFetchError。"""
    del session  # 接続の固まりを避けるため、日ごとに別プロセスで取る。
    url = f"{DATAFEED}/{feed_symbol}/{day.year}/{day.month - 1:02d}/{day.day:02d}/BID_candles_min_1.bi5"
    last_error = "不明なエラー"
    for attempt in range(MAX_ATTEMPTS):
        try:
            status, body = http_get(url)
        except (OSError, TimeoutError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {str(exc).splitlines()[-1][:180]}"
            LOGGER.warning(
                "再試行 %s %s (%s/%s): %s",
                feed_symbol,
                day.isoformat(),
                attempt + 1,
                MAX_ATTEMPTS,
                last_error,
            )
            time.sleep(backoff_seconds(attempt))
            continue
        if status == 404 or (status == 200 and not body):
            time.sleep(pace_seconds)
            return None
        if status == 200 and body[:1] not in (b"<", b"{"):
            time.sleep(pace_seconds)
            return body
        last_error = f"HTTP {status} ({len(body)} bytes)"
        LOGGER.warning(
            "再試行 %s %s (%s/%s): %s",
            feed_symbol,
            day.isoformat(),
            attempt + 1,
            MAX_ATTEMPTS,
            last_error,
        )
        time.sleep(backoff_seconds(attempt))
    raise DayFetchError(f"{feed_symbol} {day.isoformat()} を取得できません: {last_error} {url}")


def flush_months(root: Path, symbol: str, merged: pd.DataFrame, months: set[str]) -> int:
    written = 0
    if merged.empty or not months:
        return 0
    month_key = merged["time_utc"].dt.strftime("%Y-%m")
    for month in sorted(months):
        subset = merged.loc[month_key == month]
        if subset.empty:
            continue
        if write_if_changed(m1_path(root, symbol, month), render_csv(subset)):
            written += 1
            LOGGER.info("更新 %s M1 %s %s本", symbol, month, len(subset))
    return written


def flush_higher(root: Path, symbol: str, frames: dict[str, pd.DataFrame]) -> None:
    for timeframe, frame in frames.items():
        path = timeframe_path(root, symbol, timeframe)
        if frame.empty and not path.exists():
            continue
        if write_if_changed(path, render_csv(frame)):
            last = fmt_time(frame["time_utc"].iloc[-1]) if not frame.empty else "-"
            LOGGER.info("更新 %s %s %s本 last=%s", symbol, timeframe, len(frame), last)


def covered_dates(coverage: dict[tuple[str, date], tuple[str, int]], symbol: str) -> set[date]:
    return {day for (name, day) in coverage if name == symbol}


def sync_symbol(
    *,
    root: Path,
    symbol: str,
    feed_symbol: str,
    coverage: dict[tuple[str, date], tuple[str, int]],
    backfill_from: date | None,
    end: date,
    now: datetime,
    session: requests.Session,
    pace_seconds: float,
    max_days: int | None = None,
) -> list[str]:
    failures: list[str] = []
    covered = covered_dates(coverage, symbol)
    days = plan_dates(
        covered,
        backfill_from=backfill_from,
        default_start=DEFAULT_START,
        end=end,
        max_days=max_days,
    )
    if max_days:
        unlimited = plan_dates(
            covered,
            backfill_from=backfill_from,
            default_start=DEFAULT_START,
            end=end,
            max_days=None,
        )
        if len(unlimited) > len(days):
            LOGGER.info(
                "%s は今回 %s 日まで。未取得の残り %s 日は次の実行で、同じ開始日から続けます。",
                symbol,
                len(days),
                len(unlimited) - len(days),
            )
    merged = load_m1(root, symbol)
    if not days:
        LOGGER.info("%s は新規の取得日がありません。上位足だけ確認します。", symbol)
        flush_higher(root, symbol, build_higher_timeframes(merged, covered, now))
        return failures
    LOGGER.info(
        "%s を %s 日分取得します（%s ～ %s）",
        symbol,
        len(days),
        days[0].isoformat(),
        days[-1].isoformat(),
    )
    dirty_months: set[str] = set()
    coverage_path = root / "coverage.csv"
    try:
        for index, day in enumerate(days, start=1):
            try:
                content = fetch_day_bytes(session, feed_symbol, day, pace_seconds=pace_seconds)
                bars = empty_frame() if content is None else decode_m1(content, day, now)
            except DayFetchError as exc:
                failures.append(str(exc))
                LOGGER.error("%s", exc)
                continue
            if content is None:
                if missing_file_is_trusted_empty(day, end):
                    coverage[(symbol, day)] = ("empty", 0)
                    LOGGER.info("空の日 %s %s (%s/%s)", symbol, day.isoformat(), index, len(days))
                else:
                    LOGGER.info(
                        "未公開の可能性があるため空確定を保留 %s %s (%s/%s)",
                        symbol,
                        day.isoformat(),
                        index,
                        len(days),
                    )
            else:
                coverage[(symbol, day)] = ("ok" if not bars.empty else "empty", int(len(bars)))
                LOGGER.info(
                    "取得 %s %s %s本 (%s/%s)",
                    symbol,
                    day.isoformat(),
                    len(bars),
                    index,
                    len(days),
                )
            if not bars.empty:
                merged = merge_bars(merged, bars)
                dirty_months.update(bars["time_utc"].dt.strftime("%Y-%m").unique())
            flush_months(root, symbol, merged, dirty_months)
            dirty_months.clear()
            write_if_changed(coverage_path, render_coverage(coverage))
            if index % 40 == 0:
                session.close()
                session = requests.Session()
                session.headers["User-Agent"] = USER_AGENT
                session.headers["Connection"] = "close"
    finally:
        flush_months(root, symbol, merged, dirty_months)
        write_if_changed(coverage_path, render_coverage(coverage))
        covered = covered_dates(coverage, symbol)
        flush_higher(root, symbol, build_higher_timeframes(merged, covered, now))
    return failures


def summarize(root: Path, symbols: list[str]) -> None:
    for symbol in symbols:
        minute = load_m1(root, symbol)
        if minute.empty:
            LOGGER.info("要約 %s M1 なし", symbol)
        else:
            LOGGER.info(
                "要約 %s M1 %s本 %s ～ %s",
                symbol,
                len(minute),
                fmt_time(minute["time_utc"].iloc[0]),
                fmt_time(minute["time_utc"].iloc[-1]),
            )
        for timeframe in HIGHER_TIMEFRAMES:
            frame = frame_from_csv(timeframe_path(root, symbol, timeframe))
            if frame.empty:
                LOGGER.info("要約 %s %s なし", symbol, timeframe)
                continue
            LOGGER.info(
                "要約 %s %s %s本 %s ～ %s",
                symbol,
                timeframe,
                len(frame),
                fmt_time(frame["time_utc"].iloc[0]),
                fmt_time(frame["time_utc"].iloc[-1]),
            )


def compare_with_axiory(root: Path, symbols: list[str]) -> None:
    """Axiory CSV は読むだけ。中央値は終値の差（Dukascopy − Axiory）。"""
    for symbol in symbols:
        axiory_path = AXIORY_DIR / f"{symbol}_M1.csv"
        if not axiory_path.exists():
            LOGGER.info("比較スキップ %s（Axiory M1 がありません）", symbol)
            continue
        left = load_m1(root, symbol)
        if left.empty:
            LOGGER.info("比較スキップ %s（Dukascopy M1 がありません）", symbol)
            continue
        right = pd.read_csv(axiory_path, usecols=["time_utc", "close"])
        right["time_utc"] = pd.to_datetime(right["time_utc"], utc=True)
        right["close"] = pd.to_numeric(right["close"])
        left = left.copy()
        left["duka_close"] = left["close"] / PRICE_DIVISOR
        merged = left[["time_utc", "duka_close"]].merge(right, on="time_utc", how="inner")
        if merged.empty:
            LOGGER.info("比較 %s 重なるM1がありません", symbol)
            continue
        delta = merged["duka_close"] - merged["close"]
        LOGGER.info(
            "比較 %s M1 重なり %s本 終値差 中央値 %.4f 絶対差中央値 %.4f 絶対差90%% %.4f Axiory中央値 %.2f",
            symbol,
            len(merged),
            float(delta.median()),
            float(delta.abs().median()),
            float(delta.abs().quantile(0.9)),
            float(merged["close"].median()),
        )


def run(args: argparse.Namespace) -> int:
    output = refuse_axiory_output(args.output_dir)
    known = {name: feed for name, feed in SYMBOLS}
    unknown = [symbol for symbol in args.symbols if symbol not in known]
    if unknown:
        raise ValueError(f"未対応の銘柄です: {', '.join(unknown)}")
    end = parse_iso_date(args.end_date, "終了日") if args.end_date else utc_today() - timedelta(days=1)
    if end >= utc_today():
        raise ValueError(f"終了日は確定したUTC日（今日より前）にしてください: {end.isoformat()}")
    backfill_from = parse_iso_date(args.backfill_from, "バックフィル開始日") if args.backfill_from else None
    if backfill_from is not None and backfill_from > end:
        raise ValueError(f"バックフィル開始日が終了日より後です: {backfill_from.isoformat()} > {end.isoformat()}")
    output.mkdir(parents=True, exist_ok=True)
    coverage = load_coverage(output / "coverage.csv")
    now = datetime.now(timezone.utc)
    failures: list[str] = []
    socket.setdefaulttimeout(40)
    for symbol in args.symbols:
        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT
        session.headers["Connection"] = "close"
        failures.extend(
            sync_symbol(
                root=output,
                symbol=symbol,
                feed_symbol=known[symbol],
                coverage=coverage,
                backfill_from=backfill_from,
                end=end,
                now=now,
                session=session,
                pace_seconds=args.pace_seconds,
                max_days=args.max_days,
            )
        )
    summarize(output, list(args.symbols))
    if args.compare_axiory:
        compare_with_axiory(output, list(args.symbols))
    if failures:
        LOGGER.error("取得できなかった日が %s 件あります。保存済みの日は次回の実行で穴として再取得します。", len(failures))
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        return run(parse_args(argv))
    except ValueError as exc:
        LOGGER.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

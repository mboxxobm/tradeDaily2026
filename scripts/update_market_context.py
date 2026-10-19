from __future__ import annotations

import argparse
import logging
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd


REPO_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_DIR / "market_data" / "axiory"
BARS_DIR = OUTPUT_DIR
DELTA_DIR = OUTPUT_DIR / "delta"
DAILY_DIR = REPO_DIR / "diaries"
LOG_FILE = OUTPUT_DIR / "update.log"
EXECUTION_LOG_FILE = OUTPUT_DIR / "execution_log.md"
TERMINAL_EXE = Path(
    os.environ.get(
        "MT5_TERMINAL_EXE",
        r"C:\Program Files\Axiory MetaTrader 5\terminal64.exe",
    )
)
EXPECTED_DATA_DIR = (
    Path(os.environ["MT5_EXPECTED_DATA_DIR"])
    if os.environ.get("MT5_EXPECTED_DATA_DIR")
    else None
)

DEFAULT_SYMBOLS = ("JP225", "US100")
DEFAULT_START_DATE = "2023-01-03"
OVERLAP_DAYS = 2
CHUNK_DAYS = 30
MIN_BARS_PER_UTC_DAY = {"M1": 12, "M5": 12, "H1": 2}
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
PRICE_COLUMNS = ["open", "high", "low", "close"]
TIMEFRAMES = {
    "M1": ("TIMEFRAME_M1", timedelta(minutes=1)),
    "M5": ("TIMEFRAME_M5", timedelta(minutes=5)),
    "H1": ("TIMEFRAME_H1", timedelta(hours=1)),
    "D1": ("TIMEFRAME_D1", timedelta(days=1)),
    "W1": ("TIMEFRAME_W1", timedelta(days=7)),
}
JST = ZoneInfo("Asia/Tokyo")


@dataclass
class DatasetResult:
    symbol: str
    timeframe: str
    path: Path
    rows: int
    delta_rows: int
    first_time: pd.Timestamp
    last_time: pd.Timestamp
    newest_fetch: pd.Timestamp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Axiory MT5からJP225/US100のM1・M5・H1・D1・W1を差分取得し、日誌用Markdownとチャートを作成します."
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=list(DEFAULT_SYMBOLS),
        help="MT5銘柄名（初期値: JP225 US100）",
    )
    parser.add_argument(
        "--start-date",
        default=DEFAULT_START_DATE,
        help="初回取得開始日（UTC、YYYY-MM-DD。初期値: 2023-01-03）",
    )
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="保存済み足データを使わず、開始日から取得し直します",
    )
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=CHUNK_DAYS,
        help="MT5への1回の要求期間（日数、初期値: 30）",
    )
    return parser.parse_args()


def configure_logging() -> logging.Logger:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("market_journal")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def parse_start_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"開始日はYYYY-MM-DDで指定してください: {value}") from exc


def normalized_path(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def data_path(symbol: str, timeframe: str) -> Path:
    return BARS_DIR / f"{symbol}_{timeframe}.csv"


def read_existing(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=BAR_COLUMNS)
    frame = pd.read_csv(path)
    missing = set(BAR_COLUMNS).difference(frame.columns)
    if missing:
        raise ValueError(f"{path}の列が不足しています: {', '.join(sorted(missing))}")
    frame = frame[BAR_COLUMNS].copy()
    frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True, errors="coerce")
    for column in [*PRICE_COLUMNS, "tick_volume", "spread", "real_volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["time_utc", *PRICE_COLUMNS])
    return frame.sort_values("time_utc").drop_duplicates("time_utc", keep="last").reset_index(drop=True)


def _serialize_times(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame[BAR_COLUMNS].copy()
    result["time_utc"] = pd.to_datetime(result["time_utc"], utc=True).map(
        lambda value: value.isoformat().replace("+00:00", "Z")
    )
    return result


def atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = _serialize_times(frame)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", suffix=".tmp", dir=path.parent, delete=False
        ) as handle:
            temp_name = handle.name
            serialized.to_csv(handle, index=False)
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def merge_history(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        combined = incoming.copy()
    elif incoming.empty:
        combined = existing.copy()
    else:
        combined = pd.concat([existing, incoming], ignore_index=True)
    if combined.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    return (
        combined[BAR_COLUMNS]
        .sort_values("time_utc")
        .drop_duplicates("time_utc", keep="last")
        .reset_index(drop=True)
    )


def values_differ(old: pd.Series, new: pd.Series) -> bool:
    for column in BAR_COLUMNS[1:]:
        old_value = old[column]
        new_value = new[column]
        if pd.isna(old_value) and pd.isna(new_value):
            continue
        if column in PRICE_COLUMNS:
            if not math.isclose(float(old_value), float(new_value), rel_tol=0.0, abs_tol=1e-10):
                return True
        elif float(old_value) != float(new_value):
            return True
    return False


def find_delta(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if incoming.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    if existing.empty:
        return incoming[BAR_COLUMNS].copy().reset_index(drop=True)
    indexed = existing.set_index("time_utc", drop=False)
    changed: list[pd.Series] = []
    for _, row in incoming.drop_duplicates("time_utc", keep="last").iterrows():
        old = indexed.loc[row["time_utc"]] if row["time_utc"] in indexed.index else None
        if old is None or isinstance(old, pd.DataFrame) or values_differ(old, row):
            changed.append(row)
    if not changed:
        return pd.DataFrame(columns=BAR_COLUMNS)
    return pd.DataFrame(changed)[BAR_COLUMNS].sort_values("time_utc").reset_index(drop=True)


def fetch_rates(
    mt5: Any,
    symbol: str,
    timeframe: str,
    timeframe_value: Any,
    interval: timedelta,
    start: datetime,
    end: datetime,
    chunk_days: int,
    logger: logging.Logger,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    cursor = start
    chunk = timedelta(days=chunk_days)
    while cursor < end:
        chunk_end = min(cursor + chunk, end)
        rates = mt5.copy_rates_range(symbol, timeframe_value, cursor, chunk_end)
        if rates is None:
            raise RuntimeError(
                f"{symbol} {timeframe}の取得に失敗: {mt5.last_error()} "
                f"({cursor.isoformat()} - {chunk_end.isoformat()})"
            )
        if len(rates):
            frame = pd.DataFrame(rates)
            frame["time_utc"] = pd.to_datetime(frame.pop("time"), unit="s", utc=True)
            frame = frame[BAR_COLUMNS]
            # MT5 returns the currently forming candle too; journal only closed candles.
            frame = frame.loc[frame["time_utc"] + interval <= pd.Timestamp(end)]
            if not frame.empty:
                frames.append(frame)
            logger.info(
                "取得 %s %s %s ～ %s: %s本（確定足）",
                symbol,
                timeframe,
                cursor.date(),
                chunk_end.date(),
                len(frame),
            )
        else:
            logger.info("データなし %s %s %s ～ %s", symbol, timeframe, cursor.date(), chunk_end.date())
        cursor = chunk_end
    if not frames:
        return pd.DataFrame(columns=BAR_COLUMNS)
    combined = (
        pd.concat(frames, ignore_index=True)
        .sort_values("time_utc")
        .drop_duplicates("time_utc", keep="last")
        .reset_index(drop=True)
    )
    # When Axiory has no intraday history for an old period, this terminal can
    # return one daily-shaped bar per date for an intraday request. Reject those
    # sparse dates so they cannot contaminate EMA/SMA/W%R calculations.
    minimum = MIN_BARS_PER_UTC_DAY.get(timeframe)
    if minimum is not None:
        day_key = combined["time_utc"].dt.floor("D")
        counts = combined.groupby(day_key).size()
        sparse_days = counts[counts < minimum].index
        if len(sparse_days):
            sparse_mask = day_key.isin(sparse_days)
            logger.warning(
                "イントラデイ履歴が不足する日を除外: %s %s %s本 / %s日（1日%s本未満）",
                symbol,
                timeframe,
                int(sparse_mask.sum()),
                len(sparse_days),
                minimum,
            )
            combined = combined.loc[~sparse_mask].reset_index(drop=True)
    return combined


def save_delta(symbol: str, timeframe: str, delta: pd.DataFrame, run_jst: datetime) -> Path | None:
    if delta.empty:
        return None
    DELTA_DIR.mkdir(parents=True, exist_ok=True)
    stamp = run_jst.strftime("%Y%m%d_%H%M%S_JST")
    path = DELTA_DIR / f"{symbol}_{timeframe}_delta_{stamp}.csv"
    suffix = 2
    while path.exists():
        path = DELTA_DIR / f"{symbol}_{timeframe}_delta_{stamp}_{suffix}.csv"
        suffix += 1
    atomic_write_csv(delta, path)
    return path


def update_dataset(
    mt5: Any,
    symbol: str,
    timeframe: str,
    timeframe_value: Any,
    interval: timedelta,
    start_date: datetime,
    now_utc: datetime,
    chunk_days: int,
    full_refresh: bool,
    logger: logging.Logger,
) -> DatasetResult:
    path = data_path(symbol, timeframe)
    existing = pd.DataFrame(columns=BAR_COLUMNS) if full_refresh else read_existing(path)
    initial_load = full_refresh or existing.empty
    if initial_load:
        fetch_start = start_date
    else:
        fetch_start = max(start_date, existing["time_utc"].max().to_pydatetime() - timedelta(days=OVERLAP_DAYS))

    logger.info(
        "開始 %s %s from=%s to=%s mode=%s",
        symbol,
        timeframe,
        fetch_start.isoformat(),
        now_utc.isoformat(),
        "全履歴" if full_refresh or existing.empty else "差分",
    )
    incoming = fetch_rates(
        mt5,
        symbol,
        timeframe,
        timeframe_value,
        interval,
        fetch_start,
        now_utc,
        chunk_days,
        logger,
    )
    delta = pd.DataFrame(columns=BAR_COLUMNS) if initial_load else find_delta(existing, incoming)
    combined = merge_history(existing, incoming)
    if combined.empty:
        raise RuntimeError(
            f"{symbol} {timeframe}に保存可能な確定足がありません。銘柄名とMT5履歴を確認してください。"
        )
    atomic_write_csv(combined, path)
    delta_path = save_delta(symbol, timeframe, delta, now_utc.astimezone(JST))
    if delta_path:
        logger.info("差分 %s %s: %s本 -> %s", symbol, timeframe, len(delta), delta_path)
    else:
        logger.info("差分なし %s %s", symbol, timeframe)
    logger.info(
        "保存完了 %s %s rows=%s first=%s last=%s",
        symbol,
        timeframe,
        len(combined),
        combined["time_utc"].min().isoformat(),
        combined["time_utc"].max().isoformat(),
    )
    return DatasetResult(
        symbol=symbol,
        timeframe=timeframe,
        path=path,
        rows=len(combined),
        delta_rows=len(delta),
        first_time=combined["time_utc"].min(),
        last_time=combined["time_utc"].max(),
        newest_fetch=now_utc,
    )


def calculate_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy().sort_values("time_utc").reset_index(drop=True)
    result["ema60"] = result["close"].ewm(span=60, adjust=False, min_periods=60).mean()
    for period in (5, 20, 60):
        result[f"sma{period}"] = result["close"].rolling(window=period, min_periods=period).mean()
    highest = result["high"].rolling(window=15, min_periods=15).max()
    lowest = result["low"].rolling(window=15, min_periods=15).min()
    spread = highest - lowest
    result["wpr15"] = ((highest - result["close"]) / spread) * -100.0
    result.loc[spread == 0, "wpr15"] = float("nan")
    return result


def format_price(value: Any, digits: int) -> str:
    if pd.isna(value):
        return "計算不可（履歴不足）"
    return f"{float(value):,.{digits}f}"


def format_number(value: Any, places: int = 2) -> str:
    if pd.isna(value):
        return "計算不可（履歴不足）"
    return f"{float(value):,.{places}f}"


def position_label(close: Any, indicator: Any) -> str:
    if pd.isna(close) or pd.isna(indicator):
        return "—"
    if float(close) > float(indicator):
        return "終値が上"
    if float(close) < float(indicator):
        return "終値が下"
    return "同値"


def wpr_signal(value: Any) -> str:
    if pd.isna(value):
        return "—"
    if float(value) <= -60.0:
        return "下側（≤ -60）"
    if float(value) >= -40.0:
        return "上側（≥ -40）"
    return "中立帯（-60～-40）"


def perfect_order_label(frame: pd.DataFrame) -> str:
    """SMA5/20/60 stacked and all rising/falling over five completed bars."""
    if len(frame) < 65:
        return "履歴不足"
    current = frame.iloc[-1]
    previous = frame.iloc[-6]
    periods = (5, 20, 60)
    current_values = [current[f"sma{period}"] for period in periods]
    previous_values = [previous[f"sma{period}"] for period in periods]
    if any(pd.isna(value) for value in [*current_values, *previous_values]):
        return "履歴不足"
    up_stack = current_values[0] > current_values[1] > current_values[2]
    down_stack = current_values[0] < current_values[1] < current_values[2]
    all_rising = all(now > before for now, before in zip(current_values, previous_values))
    all_falling = all(now < before for now, before in zip(current_values, previous_values))
    if up_stack and all_rising:
        return "上昇PO"
    if down_stack and all_falling:
        return "下降PO"
    return "なし"


def bar_close_jst(open_time: pd.Timestamp, interval: timedelta) -> str:
    close_time = (open_time + interval).tz_convert(JST)
    return close_time.strftime("%Y-%m-%d %H:%M")


def read_metrics(symbol: str, digits: int) -> dict[str, Any]:
    frames = {
        timeframe: calculate_indicators(read_existing(data_path(symbol, timeframe)))
        for timeframe in ("M5", "H1", "D1", "W1")
    }
    if any(frame.empty for frame in frames.values()):
        missing = ", ".join(timeframe for timeframe, frame in frames.items() if frame.empty)
        raise RuntimeError(f"{symbol}のデータがありません: {missing}")
    m5 = frames["M5"]
    h1 = frames["H1"]
    m5_row = m5.iloc[-1]
    h1_row = h1.iloc[-1]
    item: dict[str, Any] = {
        "symbol": symbol,
        "digits": digits,
        "m5_close_time": bar_close_jst(m5_row["time_utc"], TIMEFRAMES["M5"][1]),
        "m5_close": m5_row["close"],
        "ema60": m5_row["ema60"],
        "m5_position": position_label(m5_row["close"], m5_row["ema60"]),
        "m5_first": m5["time_utc"].min(),
        "m5_last": m5["time_utc"].max(),
        "m5_rows": len(m5),
        "h1_close_time": bar_close_jst(h1_row["time_utc"], TIMEFRAMES["H1"][1]),
        "h1_close": h1_row["close"],
        "h1_sma20": h1_row["sma20"],
        "h1_position": position_label(h1_row["close"], h1_row["sma20"]),
        "wpr15": h1_row["wpr15"],
        "wpr_signal": wpr_signal(h1_row["wpr15"]),
        "h1_first": h1["time_utc"].min(),
        "h1_last": h1["time_utc"].max(),
        "h1_rows": len(h1),
    }
    item["h1_po"] = perfect_order_label(h1)
    for timeframe in ("D1", "W1"):
        frame = frames[timeframe]
        row = frame.iloc[-1]
        key = timeframe.lower()
        item[f"{key}_close_time"] = bar_close_jst(row["time_utc"], TIMEFRAMES[timeframe][1])
        item[f"{key}_close"] = row["close"]
        item[f"{key}_sma20"] = row["sma20"]
        item[f"{key}_position"] = position_label(row["close"], row["sma20"])
        item[f"{key}_po"] = perfect_order_label(frame)
        item[f"{key}_first"] = frame["time_utc"].min()
        item[f"{key}_last"] = frame["time_utc"].max()
        item[f"{key}_rows"] = len(frame)
    return item


def plot_candles(
    axis: Any,
    frame: pd.DataFrame,
    timeframe: str,
    limit: int,
    average_column: str,
    average_label: str,
    average_color: str,
) -> None:
    view = frame.tail(limit).copy()
    x_values = mdates.date2num(view["time_utc"].dt.tz_convert(JST).tolist())
    bar_width = TIMEFRAMES[timeframe][1].total_seconds() / 86_400 * 0.68
    point = 0.01
    for x_value, row in zip(x_values, view.itertuples(index=False)):
        opening = float(row.open)
        closing = float(row.close)
        high = float(row.high)
        low = float(row.low)
        color = "#d64045" if closing >= opening else "#3787c8"
        axis.vlines(x_value, low, high, color=color, linewidth=0.65, zorder=2)
        body_height = max(abs(closing - opening), point)
        axis.bar(
            x_value,
            body_height,
            bottom=min(opening, closing),
            width=bar_width,
            color=color,
            edgecolor=color,
            linewidth=0.45,
            align="center",
            zorder=3,
        )
    average_values = view[average_column]
    axis.plot(
        x_values,
        average_values,
        color=average_color,
        linewidth=1.25,
        label=average_label,
        zorder=4,
    )
    axis.set_ylabel("Index points")
    axis.grid(axis="y", color="#d8dee8", linewidth=0.55, alpha=0.8)
    axis.legend(loc="upper left", frameon=False, fontsize=8)
    axis.xaxis_date()
    axis.margins(x=0.01)


def create_market_chart(symbol: str, digits: int, chart_dir: Path, generated_at: datetime) -> Path:
    frames = {
        timeframe: calculate_indicators(read_existing(data_path(symbol, timeframe)))
        for timeframe in ("M5", "H1", "D1", "W1")
    }
    for timeframe, frame in frames.items():
        if frame.empty:
            raise RuntimeError(f"{symbol} {timeframe}のチャート用データがありません")

    figure, axes = plt.subplots(
        5,
        1,
        figsize=(14, 12),
        gridspec_kw={"height_ratios": [1.3, 1.15, 0.55, 1.1, 1.1]},
    )
    figure.patch.set_facecolor("white")
    title_time = generated_at.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")
    figure.suptitle(f"Axiory {symbol} — Market context through {title_time}", fontsize=14, x=0.08, ha="left")

    plot_candles(axes[0], frames["M5"], "M5", 360, "ema60", "EMA60 M5", "#00a84f")
    axes[0].set_title("M5 · 60 EMA (green)", loc="left", fontsize=10)

    plot_candles(axes[1], frames["H1"], "H1", 240, "sma20", "SMA20 H1", "#d4a900")
    axes[1].set_title("H1 · 20 SMA (yellow)", loc="left", fontsize=10)
    h1_view = frames["H1"].tail(240)
    h1_x = mdates.date2num(h1_view["time_utc"].dt.tz_convert(JST).tolist())
    axes[2].plot(h1_x, h1_view["wpr15"], color="#00a6b2", linewidth=1.0)
    axes[2].axhline(-40, color="#6c7a89", linewidth=0.7, linestyle="--")
    axes[2].axhline(-60, color="#6c7a89", linewidth=0.7, linestyle="--")
    axes[2].axhline(-50, color="#aab2bd", linewidth=0.55, linestyle=":")
    axes[2].set_ylim(-100, 0)
    axes[2].set_ylabel("W%R15")
    axes[2].set_title("H1 · Williams %R (15) · bands −60 / −40", loc="left", fontsize=9)
    axes[2].grid(axis="y", color="#d8dee8", linewidth=0.5, alpha=0.8)
    axes[2].xaxis_date()

    plot_candles(axes[3], frames["D1"], "D1", 180, "sma20", "SMA20 D1", "#d64045")
    axes[3].set_title("D1 · 20 SMA (red)", loc="left", fontsize=10)

    plot_candles(axes[4], frames["W1"], "W1", 156, "sma20", "SMA20 W1", "#8b5cf6")
    axes[4].set_title("W1 · 20 SMA (purple)", loc="left", fontsize=10)

    for axis, timeframe in zip((axes[0], axes[1], axes[3], axes[4]), ("M5", "H1", "D1", "W1")):
        if timeframe in ("M5", "H1"):
            axis.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M", tz=JST))
        else:
            axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m", tz=JST))
        axis.tick_params(axis="x", labelsize=8)
        axis.tick_params(axis="y", labelsize=8)
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d", tz=JST))
    axes[2].tick_params(axis="x", labelsize=8)
    axes[2].tick_params(axis="y", labelsize=8)
    figure.tight_layout(rect=(0.02, 0.01, 0.99, 0.96), h_pad=0.8)
    chart_dir.mkdir(parents=True, exist_ok=True)
    chart_path = chart_dir / f"{symbol}_market_context.png"
    figure.savefig(chart_path, dpi=145, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    return chart_path


def build_daily_markdown(
    metrics: list[dict[str, Any]],
    generated_at: datetime,
    dataset_results: list[DatasetResult],
) -> str:
    day = generated_at.astimezone(JST).date()
    lines = [
        f"# US100・JP225 日次マーケット指標 — {day.isoformat()}（JST）",
        "",
        f"- 生成時刻: {generated_at.astimezone(JST).strftime('%Y-%m-%d %H:%M:%S JST')}",
        "- データ元: Axiory MetaTrader 5（銘柄名 JP225 / US100）。別口座のJP225Cash/US100Cashとは価格系列が異なります。",
        "- 時刻: CSVはUTC。日誌とチャートはJST。未確定足を除外しています。",
        "- 指標の読み方とチャート規約: [市場コンテキストのレシピ](../../market_context_recipe.md)",
        "",
        "| 銘柄 | M5終値 / EMA60（緑） | H1終値 / SMA20（黄） | W%R15 H1 | D1終値 / SMA20（赤） | W1終値 / SMA20（紫） | PO H1 / D1 / W1 |",
        "|---|---|---|---:|---|---|---|",
    ]
    for item in metrics:
        digits = item["digits"]
        lines.append(
            "| {symbol} | {m5_pair} | {h1_pair} | {wpr15} {wpr_signal} | "
            "{d1_pair} | {w1_pair} | {h1_po} / {d1_po} / {w1_po} |".format(
                symbol=item["symbol"],
                m5_pair=(
                    f"{format_price(item['m5_close'], digits)} / "
                    f"{format_price(item['ema60'], digits)}（{item['m5_position']}）"
                ),
                h1_pair=(
                    f"{format_price(item['h1_close'], digits)} / "
                    f"{format_price(item['h1_sma20'], digits)}（{item['h1_position']}）"
                ),
                wpr15=format_number(item["wpr15"]),
                wpr_signal=item["wpr_signal"],
                d1_pair=(
                    f"{format_price(item['d1_close'], digits)} / "
                    f"{format_price(item['d1_sma20'], digits)}（{item['d1_position']}）"
                ),
                w1_pair=(
                    f"{format_price(item['w1_close'], digits)} / "
                    f"{format_price(item['w1_sma20'], digits)}（{item['w1_position']}）"
                ),
                h1_po=item["h1_po"],
                d1_po=item["d1_po"],
                w1_po=item["w1_po"],
            )
        )
    lines.extend(["", "## ソースCSV", ""])
    for item in metrics:
        symbol = item["symbol"]
        links = " / ".join(
            f"[{timeframe}](../../../market_data/axiory/{symbol}_{timeframe}.csv)"
            for timeframe in TIMEFRAMES
        )
        lines.append(f"- {symbol}: {links}")
    lines.extend(["", "## チャート", ""])
    for item in metrics:
        symbol = item["symbol"]
        lines.extend([f"### {symbol}", "", f"![Axiory {symbol} multi-timeframe chart]({symbol}_market_context.png)", ""])
    lines.extend(
        [
            "## データ範囲",
            "",
            "| 銘柄 | 足 | 保存本数 | 全履歴の最古足（JST） | 最終足の終了時刻（JST） | 今回の差分 |",
            "|---|---|---:|---|---|---:|",
        ]
    )
    for result in dataset_results:
        first_jst = result.first_time.tz_convert(JST).strftime("%Y-%m-%d %H:%M")
        interval = TIMEFRAMES[result.timeframe][1]
        last_close_jst = bar_close_jst(result.last_time, interval)
        lines.append(
            f"| {result.symbol} | {result.timeframe} | {result.rows:,} | {first_jst} | {last_close_jst} | {result.delta_rows:,}本 |"
        )
    lines.extend(
        [
            "",
            "## 計算ルール",
            "",
            "- EMA60(M5): M5終値に対する60期間指数移動平均。",
            "- SMA20(H1/D1/W1): それぞれの足の終値、直近20本の単純平均。",
            "- W%R(15,H1): H1の高値・安値・終値から計算。-60以下=下側、-40以上=上側、その間=中立帯。",
            "- パーフェクトオーダー(PO): 各足でSMA5>SMA20>SMA60かつ各SMAが5本前より上なら上昇PO。逆順かつ各SMAが5本前より下なら下降PO。それ以外は「なし」。",
            "- 同日に再実行した場合は、その日付のレポートとチャートを置き換えます。",
            "",
            "## 保存ファイル",
            "",
            "- M1/M5/H1/D1/W1履歴: `market_data/axiory/`",
            "- 差分CSV・詳細ログ・実行履歴: ローカルの `market_data/axiory/` 配下（Gitでは追跡しません）",
            "",
        ]
    )
    return "\n".join(lines)


def atomic_write_text(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", suffix=".tmp", dir=path.parent, delete=False
        ) as handle:
            temp_name = handle.name
            handle.write(text)
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def build_diary_context_block(metrics: list[dict[str, Any]], generated_at: datetime) -> str:
    generated_jst = generated_at.astimezone(JST)
    lines = [
        "<!-- AUTO MARKET CONTEXT START -->",
        "## 指数の定量マーケットコンテキスト（Axiory）",
        "",
        f"> データ取得: {generated_jst.strftime('%Y-%m-%d %H:%M JST')}。Axiory `JP225` / `US100` の参考値です。",
        "> `JP225Cash` / `US100Cash` を扱う別口座とは価格系列が異なるため、約定価格や損益の照合には使いません。",
        "",
        "| 銘柄 | M5終値 / EMA60 | H1終値 / SMA20 | H1 W%R15 | D1終値 / SMA20 | W1終値 / SMA20 | PO H1 / D1 / W1 |",
        "|---|---|---|---:|---|---|---|",
    ]
    for item in metrics:
        digits = item["digits"]
        lines.append(
            "| {symbol} | {m5_close} / {m5_ema} | {h1_close} / {h1_sma} | {wpr} {zone} | "
            "{d1_close} / {d1_sma} | {w1_close} / {w1_sma} | {h1_po} / {d1_po} / {w1_po} |".format(
                symbol=item["symbol"],
                m5_close=format_price(item["m5_close"], digits),
                m5_ema=format_price(item["ema60"], digits),
                h1_close=format_price(item["h1_close"], digits),
                h1_sma=format_price(item["h1_sma20"], digits),
                wpr=format_number(item["wpr15"]),
                zone=item["wpr_signal"],
                d1_close=format_price(item["d1_close"], digits),
                d1_sma=format_price(item["d1_sma20"], digits),
                w1_close=format_price(item["w1_close"], digits),
                w1_sma=format_price(item["w1_sma20"], digits),
                h1_po=item["h1_po"],
                d1_po=item["d1_po"],
                w1_po=item["w1_po"],
            )
        )
    lines.extend(
        [
            "",
            "- [指標レポート・対象期間・元CSV](market_context/market_context.md)",
            "- [計算・チャート作成レシピ](../market_context_recipe.md)",
        ]
    )
    for item in metrics:
        symbol = item["symbol"]
        lines.extend(
            [
                "",
                f"### {symbol}（Axiory）",
                "",
                f"![Axiory {symbol} M5/H1/D1/W1 chart](market_context/{symbol}_market_context.png)",
            ]
        )
    lines.extend(["", "<!-- AUTO MARKET CONTEXT END -->"])
    return "\n".join(lines)


def update_daily_diary(metrics: list[dict[str, Any]], generated_at: datetime, logger: logging.Logger) -> Path | None:
    day = generated_at.astimezone(JST).date()
    diary_path = DAILY_DIR / day.isoformat() / f"diary_{day:%Y%m%d}.md"
    if not diary_path.exists():
        logger.info("日誌本文はまだありません。独立レポートのみ作成: %s", diary_path)
        return None
    existing = diary_path.read_text(encoding="utf-8-sig")
    start_marker = "<!-- AUTO MARKET CONTEXT START -->"
    end_marker = "<!-- AUTO MARKET CONTEXT END -->"
    block = build_diary_context_block(metrics, generated_at)
    has_start = start_marker in existing
    has_end = end_marker in existing
    if has_start != has_end:
        raise RuntimeError(f"日誌内のマーケットコンテキストマーカーが不完全です: {diary_path}")
    if has_start:
        start = existing.index(start_marker)
        end = existing.index(end_marker, start) + len(end_marker)
        updated = existing[:start] + block + existing[end:]
    else:
        updated = existing.rstrip() + "\n\n" + block + "\n"
    atomic_write_text(updated, diary_path)
    logger.info("日誌へマーケットコンテキストを反映: %s", diary_path)
    return diary_path


def append_execution_log(
    status: str,
    started_at: datetime,
    finished_at: datetime,
    report_path: Path | None,
    results: list[DatasetResult],
    error: str | None = None,
) -> None:
    lines = [
        f"## {started_at.astimezone(JST).strftime('%Y-%m-%d %H:%M:%S JST')} — {status}",
        "",
        f"- 終了時刻: {finished_at.astimezone(JST).strftime('%Y-%m-%d %H:%M:%S JST')}",
    ]
    if report_path:
        lines.append(f"- 日次レポート: `{report_path}`")
    if results:
        lines.extend(["", "| 銘柄 | 足 | 保存本数 | 差分本数 | 最終足（JST） |", "|---|---|---:|---:|---|"])
        for result in results:
            last_close_jst = bar_close_jst(result.last_time, TIMEFRAMES[result.timeframe][1])
            lines.append(
                f"| {result.symbol} | {result.timeframe} | {result.rows:,} | {result.delta_rows:,} | {last_close_jst} |"
            )
    if error:
        lines.extend(["", "```text", error, "```"])
    lines.append("")
    EXECUTION_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with EXECUTION_LOG_FILE.open("a", encoding="utf-8", newline="\n") as handle:
        if EXECUTION_LOG_FILE.exists() and EXECUTION_LOG_FILE.stat().st_size:
            handle.write("\n")
        handle.write("\n".join(lines))


def run_update(args: argparse.Namespace, logger: logging.Logger) -> tuple[list[DatasetResult], Path]:
    import MetaTrader5 as mt5

    if not TERMINAL_EXE.is_file():
        raise FileNotFoundError(f"MT5実行ファイルがありません: {TERMINAL_EXE}")
    start_date = parse_start_date(args.start_date)
    now_utc = datetime.now(timezone.utc)
    if not mt5.initialize(str(TERMINAL_EXE), timeout=60_000):
        raise RuntimeError(f"MT5に接続できません: {mt5.last_error()}")
    results: list[DatasetResult] = []
    try:
        terminal_info = mt5.terminal_info()
        if terminal_info is None:
            raise RuntimeError(f"MT5端末情報を取得できません: {mt5.last_error()}")
        actual_data_dir = getattr(terminal_info, "data_path", "")
        if (
            EXPECTED_DATA_DIR is not None
            and actual_data_dir
            and normalized_path(actual_data_dir) != normalized_path(EXPECTED_DATA_DIR)
        ):
            raise RuntimeError(
                "接続先MT5のデータフォルダーが想定と異なります。"
                f" expected={EXPECTED_DATA_DIR}, actual={actual_data_dir}"
            )
        if not getattr(terminal_info, "connected", False):
            raise RuntimeError("Axiory MT5がサーバーへ接続されていません")
        logger.info(
            "MT5接続 data_dir=%s maxbars=%s symbols=%s start=%s",
            actual_data_dir,
            getattr(terminal_info, "maxbars", "unknown"),
            ",".join(args.symbols),
            start_date.isoformat(),
        )

        for symbol in args.symbols:
            if not mt5.symbol_select(symbol, True):
                raise RuntimeError(
                    f"銘柄 {symbol} を選択できません。Axiory MT5の気配値表示で正確な銘柄名を確認してください。"
                )
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                raise RuntimeError(f"銘柄情報を取得できません: {symbol} ({mt5.last_error()})")
            for timeframe, (constant_name, interval) in TIMEFRAMES.items():
                timeframe_value = getattr(mt5, constant_name)
                result = update_dataset(
                    mt5=mt5,
                    symbol=symbol,
                    timeframe=timeframe,
                    timeframe_value=timeframe_value,
                    interval=interval,
                    start_date=start_date,
                    now_utc=now_utc,
                    chunk_days=args.chunk_days,
                    full_refresh=args.full_refresh,
                    logger=logger,
                )
                results.append(result)
        metrics = [
            read_metrics(symbol, int(getattr(mt5.symbol_info(symbol), "digits", 2)))
            for symbol in args.symbols
        ]
        journal_day = now_utc.astimezone(JST).date().isoformat()
        chart_dir = DAILY_DIR / journal_day / "market_context"
        for item in metrics:
            create_market_chart(item["symbol"], item["digits"], chart_dir, now_utc)
        report_text = build_daily_markdown(metrics, now_utc, results)
        report_path = chart_dir / "market_context.md"
        atomic_write_text(report_text, report_path)
        logger.info("日次Markdownを作成: %s", report_path)
        update_daily_diary(metrics, now_utc, logger)
        return results, report_path
    finally:
        mt5.shutdown()


def main() -> int:
    args = parse_args()
    logger = configure_logging()
    started_at = datetime.now(timezone.utc)
    results: list[DatasetResult] = []
    report_path: Path | None = None
    status = "成功"
    error: str | None = None
    exit_code = 0
    if started_at.astimezone(JST).weekday() >= 5:
        status = "スキップ"
        logger.info("JSTの土曜日・日曜日のため、データ取得・日誌・チャート生成をスキップします")
    elif args.chunk_days < 1:
        status = "失敗"
        error = "--chunk-daysは1以上で指定してください。"
        logger.error(error)
        exit_code = 2
    elif not args.symbols:
        status = "失敗"
        error = "--symbolsに銘柄を1つ以上指定してください。"
        logger.error(error)
        exit_code = 2
    else:
        try:
            results, report_path = run_update(args, logger)
        except Exception as exc:
            status = "失敗"
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("日次マーケットデータ更新に失敗しました")
            exit_code = 1
    finished_at = datetime.now(timezone.utc)
    try:
        append_execution_log(status, started_at, finished_at, report_path, results, error)
        logger.info("実行履歴: %s", EXECUTION_LOG_FILE)
    except Exception:
        logger.exception("Markdown実行履歴を記録できませんでした")
        if exit_code == 0:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

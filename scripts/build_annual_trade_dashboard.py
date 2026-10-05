#!/usr/bin/env python3
"""Build the portable one-year trade visualization from archived Matsui CSVs.

The dashboard is deliberately self-contained: the generated HTML embeds the
small normalized dataset, so it also works when opened directly from GitHub or
as a static Vercel page without a server-side CSV reader.
"""

from __future__ import annotations

import csv
import json
import math
import re
import urllib.error
import urllib.request
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSV_DIR = ROOT / "2026 Daily" / "trade_data_2025_2026"
LIGHT_OUTPUT = ROOT / "2026 Daily" / "annual_trade_dashboard.html"
DARK_OUTPUT = ROOT / "2026 Daily" / "annual_trade_dashboard_dark.html"
R2_MANIFEST_RE = re.compile(
    r"^board_raw/([^/]+)/([0-9A-Za-z]+)_(\d{8})_(\d{6})\.jsonl\.gz$"
)


def to_number(value: str) -> float:
    text = str(value or "").strip().replace(",", "").replace("，", "")
    if not text or text in {"-", "—", "－"}:
        return 0.0
    text = re.sub(r"[^0-9.\-]", "", text)
    try:
        return float(text)
    except ValueError:
        return 0.0


def time_to_minutes(value: str) -> int | None:
    match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(value or ""))
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def time_label(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def clean_text(value: str) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def read_fills(path: Path) -> list[dict]:
    """Read only executed rows from one Matsui order CSV."""
    fills: list[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return fills

        positions: dict[str, list[int]] = defaultdict(list)
        for index, name in enumerate(header):
            positions[clean_text(name)].append(index)

        def field(row: list[str], name: str, occurrence: int = 0) -> str:
            indices = positions.get(name, [])
            if occurrence >= len(indices):
                return ""
            index = indices[occurrence]
            return row[index] if index < len(row) else ""

        date = path.stem.split("_")[-1]
        for source_order, row in enumerate(reader):
            status = clean_text(field(row, "状態", 0))
            quantity = int(round(to_number(field(row, "約定数"))))
            price = to_number(field(row, "約定単価"))
            execution_time = clean_text(field(row, "約定時間"))
            minute = time_to_minutes(execution_time)
            code = clean_text(field(row, "銘柄コード"))
            name = clean_text(field(row, "銘柄名"))
            trade_type = clean_text(field(row, "取引区分"))
            side = clean_text(field(row, "売買"))

            if status and "約定" not in status:
                continue
            if quantity <= 0 or price <= 0 or minute is None or not code:
                continue
            if "新規" not in trade_type and "返済" not in trade_type:
                continue
            if "買" not in side and "売" not in side:
                continue

            fills.append(
                {
                    "date": date,
                    "code": code,
                    "name": name or code,
                    "trade_type": trade_type,
                    "side": side,
                    "quantity": quantity,
                    "price": price,
                    "time": execution_time,
                    "minute": minute,
                    "source_order": source_order,
                }
            )

    # Matsui exports are often newest-first. Chronological order is needed for
    # FIFO pairing, while source_order preserves the CSV order at equal times.
    fills.sort(key=lambda item: (item["minute"], item["source_order"]))
    return fills


def pair_day(fills: list[dict]) -> tuple[list[dict], dict]:
    """Match new/repayment fills FIFO and return realized trade pairs."""
    books: dict[str, dict[str, deque]] = defaultdict(
        lambda: {"long": deque(), "short": deque()}
    )
    pairs: list[dict] = []
    open_lots = 0
    unmatched_exit_shares = 0

    for fill in fills:
        is_entry = "新規" in fill["trade_type"]
        is_buy = "買" in fill["side"]
        if is_entry:
            direction = "long" if is_buy else "short"
            books[fill["code"]][direction].append(
                {
                    "quantity": fill["quantity"],
                    "price": fill["price"],
                    "time": fill["time"],
                    "minute": fill["minute"],
                    "code": fill["code"],
                    "name": fill["name"],
                }
            )
            continue

        # A sell repayment closes a long; a buy repayment closes a short.
        direction = "long" if not is_buy else "short"
        remaining = fill["quantity"]
        queue = books[fill["code"]][direction]
        while remaining > 0 and queue:
            lot = queue.popleft()
            matched = min(remaining, lot["quantity"])
            if direction == "long":
                pnl = (fill["price"] - lot["price"]) * matched
            else:
                pnl = (lot["price"] - fill["price"]) * matched
            hold_minutes = fill["minute"] - lot["minute"]
            if hold_minutes < 0:
                hold_minutes += 24 * 60
            pairs.append(
                {
                    "date": fill["date"],
                    "code": fill["code"],
                    "name": fill["name"] or lot["name"],
                    "direction": direction,
                    "entryTime": lot["time"],
                    "exitTime": fill["time"],
                    "entryMinute": lot["minute"],
                    "exitMinute": fill["minute"],
                    "entryPrice": lot["price"],
                    "exitPrice": fill["price"],
                    "quantity": matched,
                    "pnl": round(pnl, 2),
                    "holdMinutes": hold_minutes,
                }
            )
            remaining -= matched
            if lot["quantity"] > matched:
                lot["quantity"] -= matched
                queue.appendleft(lot)
        if remaining > 0:
            unmatched_exit_shares += remaining

    for code_books in books.values():
        open_lots += sum(lot["quantity"] for queue in code_books.values() for lot in queue)
    return pairs, {"open_shares": open_lots, "unmatched_exit_shares": unmatched_exit_shares}


def round_number(value: float) -> int | float:
    rounded = round(value, 2)
    return int(rounded) if rounded == int(rounded) else rounded


def read_public_r2_base() -> str:
    """Read only the public R2 base URL for the generated browser page."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return ""
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "R2_PUBLIC_BASE_URL":
            return value.strip().strip("'\"").rstrip("/")
    return ""


def load_board_index(public_base: str) -> dict:
    """Load the small public manifest and keep only board snapshot metadata.

    The raw board files stay in R2; embedding this index lets a static HTML
    page locate the right morning/afternoon file without exposing credentials
    or copying several gigabytes of source data into the repository.
    """
    if not public_base:
        return {}
    url = f"{public_base}/board_raw/manifest.json"
    request = urllib.request.Request(url, headers={"User-Agent": "TradeDaily-dashboard-builder/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            manifest = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return {}

    index: dict[str, dict[str, list[dict]]] = {}
    for item in manifest.get("files", []):
        key = str(item.get("key") or "")
        match = R2_MANIFEST_RE.match(key)
        if not match:
            continue
        directory, code, date, capture_time = match.groups()
        index.setdefault(date, {}).setdefault(code, []).append(
            {
                "key": key,
                "directory": directory,
                "captureTime": capture_time,
                "bytes": int(item.get("bytes") or 0),
            }
        )
    for by_code in index.values():
        for files in by_code.values():
            files.sort(key=lambda item: item["captureTime"])
    return index


def build_data() -> dict:
    paths = sorted(CSV_DIR.glob("stockorder_*.csv"))
    all_pairs: list[dict] = []
    daily: dict[str, dict] = {}
    symbol_names: dict[str, Counter] = defaultdict(Counter)
    total_fills = 0
    total_entries = 0
    total_exits = 0
    open_shares = 0
    unmatched_exit_shares = 0

    for path in paths:
        date = path.stem.split("_")[-1]
        fills = read_fills(path)
        pairs, leftovers = pair_day(fills)
        entries = sum(1 for fill in fills if "新規" in fill["trade_type"])
        exits = sum(1 for fill in fills if "返済" in fill["trade_type"])
        for fill in fills:
            symbol_names[fill["code"]][fill["name"]] += fill["quantity"]
        all_pairs.extend(pairs)
        daily[date] = {
            "date": date,
            "fills": len(fills),
            "entries": entries,
            "exits": exits,
            "pairs": len(pairs),
            "pnl": round_number(sum(pair["pnl"] for pair in pairs)),
        }
        total_fills += len(fills)
        total_entries += entries
        total_exits += exits
        open_shares += leftovers["open_shares"]
        unmatched_exit_shares += leftovers["unmatched_exit_shares"]

    all_pairs.sort(key=lambda pair: (pair["date"], pair["entryMinute"], pair["exitMinute"]))
    dates = sorted(daily)
    for date in dates:
        daily[date]["pnl"] = round_number(daily[date]["pnl"])

    month_map: dict[str, dict] = {}
    for row in daily.values():
        month = row["date"][:6]
        target = month_map.setdefault(
            month, {"month": month, "days": 0, "pairs": 0, "pnl": 0}
        )
        target["days"] += 1
        target["pairs"] += row["pairs"]
        target["pnl"] += row["pnl"]
    for month in month_map.values():
        month["pnl"] = round_number(month["pnl"])

    symbol_map: dict[str, dict] = {}
    for pair in all_pairs:
        code = pair["code"]
        target = symbol_map.setdefault(
            code,
            {
                "code": code,
                "name": pair["name"],
                "pnl": 0,
                "pairs": 0,
                "wins": 0,
                "losses": 0,
                "flat": 0,
                "shares": 0,
                "longPairs": 0,
                "shortPairs": 0,
            },
        )
        target["name"] = symbol_names[code].most_common(1)[0][0]
        target["pnl"] += pair["pnl"]
        target["pairs"] += 1
        target["shares"] += pair["quantity"]
        target["longPairs" if pair["direction"] == "long" else "shortPairs"] += 1
        if pair["pnl"] > 0:
            target["wins"] += 1
        elif pair["pnl"] < 0:
            target["losses"] += 1
        else:
            target["flat"] += 1
    for symbol in symbol_map.values():
        symbol["pnl"] = round_number(symbol["pnl"])
        symbol["winRate"] = round_number(
            symbol["wins"] / symbol["pairs"] * 100 if symbol["pairs"] else 0
        )

    time_map: dict[int, dict] = {}
    for pair in all_pairs:
        bucket = (pair["entryMinute"] // 30) * 30
        target = time_map.setdefault(bucket, {"minute": bucket, "label": time_label(bucket), "pairs": 0, "pnl": 0})
        target["pairs"] += 1
        target["pnl"] += pair["pnl"]
    for bucket in time_map.values():
        bucket["pnl"] = round_number(bucket["pnl"])

    public_r2_base = read_public_r2_base()
    sources = {
        "publicR2BaseUrl": public_r2_base,
        "walkPathPattern": "{date}_picked/qr-{code}-{date}.csv",
        "boardManifestPath": "board_raw/manifest.json",
        "boardIndex": load_board_index(public_r2_base),
    }

    meta = {
        "startDate": dates[0] if dates else "",
        "endDate": dates[-1] if dates else "",
        "fileCount": len(paths),
        "executionFills": total_fills,
        "entryFills": total_entries,
        "exitFills": total_exits,
        "closedPairs": len(all_pairs),
        "openShares": open_shares,
        "unmatchedExitShares": unmatched_exit_shares,
        "symbols": len(symbol_map),
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    return {
        "meta": meta,
        "daily": [daily[date] for date in dates],
        "monthly": [month_map[month] for month in sorted(month_map)],
        "symbols": sorted(symbol_map.values(), key=lambda row: (-row["pnl"], row["code"])),
        "timeBins": [time_map[key] for key in sorted(time_map)],
        "trades": all_pairs,
        "sources": sources,
    }


HTML = r'''<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>年間トレードビジュアルレビュー</title>
<style>
:root{--ink:#18324a;--muted:#6b7b8d;--line:#dce6ef;--bg:#f3f7fa;--card:#fff;--teal:#117d76;--blue:#2a79b8;--orange:#ec7b1b;--red:#c84a45;--green:#1a9662;--soft:#eef5f7;--shadow:0 7px 22px rgba(26,57,84,.08);--donut-positive:#117d76;--donut-positive-2:#2a79b8;--donut-negative:#c84a45;--donut-negative-2:#ec7b1b;--donut-neutral:#9ab2c0}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif}main{max-width:1500px;margin:22px auto;padding:0 18px}.hero,.panel,.metric{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow)}.hero{padding:25px 27px;margin-bottom:15px}.eyebrow{color:var(--teal);font-weight:800;letter-spacing:.12em;font-size:12px}.hero h1{font-size:30px;line-height:1.25;margin:8px 0 7px;letter-spacing:.01em}.hero p{margin:4px 0;color:var(--muted);max-width:920px}.toolbar{display:flex;gap:10px;align-items:end;flex-wrap:wrap;margin-top:20px}.control{display:flex;flex-direction:column;gap:5px;color:var(--muted);font-size:12px;font-weight:700;min-width:190px}.control.wide{min-width:300px;flex:1}.control input,.control select{height:42px;border:1px solid #cbd8e3;border-radius:10px;background:#fff;color:var(--ink);padding:0 12px;font:inherit;font-size:14px}.button{height:42px;border:1px solid #c5d5e3;border-radius:10px;background:#fff;color:var(--ink);padding:0 16px;font-weight:700;cursor:pointer}.button:hover{border-color:var(--teal);color:var(--teal)}.filter-state{margin-top:12px;color:var(--muted);min-height:22px}.filter-state strong{color:var(--ink)}.cards{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:11px;margin-bottom:15px}.metric{padding:15px 17px}.metric span{display:block;color:var(--muted);font-size:12px}.metric strong{display:block;font-size:25px;margin-top:3px;letter-spacing:.01em}.metric small{display:block;color:var(--muted);margin-top:3px}.positive{color:var(--green)!important}.negative{color:var(--red)!important}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px;margin-bottom:15px}.panel{padding:19px 20px;min-width:0}.panel h2{font-size:18px;line-height:1.3;margin:0 0 3px}.panel h2:before{content:"";display:inline-block;width:5px;height:20px;background:var(--teal);border-radius:5px;vertical-align:-4px;margin-right:9px}.panel .sub{margin:0 0 10px;color:var(--muted);font-size:12px}.chart-wrap{overflow:hidden;min-height:290px}.chart{width:100%;height:auto;display:block}.axis{font-size:11px;fill:#6e7d8c}.gridline{stroke:#e4ebf0;stroke-width:1}.zero{stroke:#9eb0be;stroke-width:1.4}.tip{font-size:11px;fill:#496073}.legend{font-size:12px;color:var(--muted);margin-top:7px}.swatch{display:inline-block;width:19px;height:10px;border-radius:3px;vertical-align:-1px;margin-right:4px}.table-wrap{overflow:auto;max-height:530px;border:1px solid #e5ecf1;border-radius:10px}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;white-space:nowrap}th,td{padding:8px 10px;border-bottom:1px solid #edf1f4;text-align:right}th:first-child,td:first-child{text-align:left}thead th{position:sticky;top:0;background:#f4f8fa;color:#52687c;font-size:12px}tbody tr:hover{background:#f6fafb}.dir-long{color:var(--blue);font-weight:700}.dir-short{color:#9b5c22;font-weight:700}.right{text-align:right}.empty{padding:28px;text-align:center;color:var(--muted);background:#f7fafb;border-radius:10px}.note{color:var(--muted);font-size:12px;margin:10px 0 0}.tag{display:inline-flex;align-items:center;border:1px solid #cce1e5;background:#f0f8f8;color:#176b68;border-radius:999px;padding:3px 9px;font-size:12px;font-weight:700;margin:0 5px 5px 0}.footer{color:var(--muted);font-size:12px;padding:5px 4px 35px}.summary-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}.mini{border:1px solid var(--line);border-radius:10px;background:#f9fbfc;padding:9px 12px}.mini b{display:block;font-size:16px}.rank-label{font-size:12px;fill:#29455c}.rank-value{font-size:11px;fill:#5d7081}.bar-pos{fill:#1a9662}.bar-neg{fill:#d0635b}.bar-neutral{fill:#9ab2c0}.bar-time{fill:#3789a9}.bar-time-neg{fill:#d0635b}@media(max-width:1080px){.cards{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:720px){main{padding:0 10px;margin:10px auto}.hero,.panel{padding:15px}.hero h1{font-size:24px}.cards{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.metric{padding:12px}.metric strong{font-size:20px}.grid{grid-template-columns:1fr;gap:10px}.control,.control.wide{min-width:100%;flex:1}.toolbar .button{width:100%}.chart-wrap{min-height:240px}.panel h2{font-size:17px}}
.theme-nav{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.theme-nav a{display:inline-flex;align-items:center;min-height:34px;padding:6px 10px;border:1px solid var(--line);border-radius:9px;color:var(--blue);background:var(--card);text-decoration:none;font-size:12px;font-weight:700}.theme-nav a:hover{border-color:var(--teal);color:var(--teal)}.swatch-teal{background:#117d76}.swatch-zero{background:#9eb0be}.swatch-green{background:#1a9662}.swatch-red{background:#d0635b}.chart-options{display:flex;gap:8px;align-items:end;flex-wrap:wrap;margin:8px 0 10px}.chart-options .control{min-width:135px;flex:1}.chart-options .control select{height:36px;font-size:12px}.donut-legend{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px 12px;margin-top:8px}.donut-item{display:flex;align-items:center;gap:6px;min-width:0;color:var(--muted);font-size:12px}.donut-item span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.donut-swatch{width:11px;height:11px;border-radius:3px;flex:0 0 auto;background:var(--donut-neutral)}.donut-swatch.positive,.donut-slice.positive{background:var(--donut-positive)!important;fill:var(--donut-positive)!important}.donut-swatch.positive.alt,.donut-slice.positive.alt{background:var(--donut-positive-2)!important;fill:var(--donut-positive-2)!important}.donut-swatch.negative,.donut-slice.negative{background:var(--donut-negative)!important;fill:var(--donut-negative)!important}.donut-swatch.negative.alt,.donut-slice.negative.alt{background:var(--donut-negative-2)!important;fill:var(--donut-negative-2)!important}.donut-swatch.neutral,.donut-slice.neutral{background:var(--donut-neutral)!important;fill:var(--donut-neutral)!important}
.trade-row{cursor:pointer}.trade-row.selected{background:var(--soft)}.trade-row:focus-within{outline:2px solid var(--teal);outline-offset:-2px}.inspect-button{height:30px;border:1px solid #b9d5dc;border-radius:8px;background:transparent;color:var(--teal);padding:0 8px;font:inherit;font-size:12px;font-weight:700;cursor:pointer}.inspect-button:hover,.inspect-button:focus{background:var(--soft);border-color:var(--teal)}.inspect-panel[hidden]{display:none}.inspect-controls{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0}.inspect-status{color:var(--muted);font-size:12px}.inspect-selected{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:4px 0 12px}.inspect-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.inspect-card{min-width:0;border:1px solid var(--line);border-radius:12px;background:var(--card);padding:12px}.inspect-card h3{font-size:15px;margin:0 0 3px}.inspect-card .sub{font-size:11px;margin:0 0 8px}.inspect-chart{min-height:205px;overflow:hidden}.inspect-chart .chart{width:100%;height:auto}.inspect-chart .axis{font-size:10px}.inspect-chart .gridline{stroke:#e4ebf0;stroke-width:1}.inspect-chart .zero{stroke:#9eb0be;stroke-width:1.2}.inspect-chart .trade-line{stroke:#ec7b1b;stroke-width:1.5;stroke-dasharray:4 4}.inspect-chart .entry-mark{fill:#2a79b8;stroke:#fff;stroke-width:1.2}.inspect-chart .exit-mark{fill:#c84a45;stroke:#fff;stroke-width:1.2}.inspect-chart .wick-up{stroke:#1a9662;fill:#1a9662}.inspect-chart .wick-down{stroke:#d0635b;fill:#d0635b}.inspect-chart .bar-volume{fill:#80a9b8;opacity:.55}.inspect-source{margin:0 0 12px;padding:9px 11px;border:1px solid var(--line);border-radius:10px;color:var(--muted);font-size:12px}.inspect-source a{color:var(--blue)}.board-card{margin-top:12px}.board-reading{display:grid;grid-template-columns:1fr 1fr;gap:10px}.board-reading .mini{min-width:0}.book-levels{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}.book-side{font-size:11px;color:var(--muted)}.book-side strong{display:block;color:var(--ink);font-size:12px;margin-bottom:4px}.book-level{display:flex;justify-content:space-between;gap:8px;border-bottom:1px solid var(--line);padding:3px 0;font-variant-numeric:tabular-nums}.book-level.ask{color:#c84a45}.book-level.bid{color:#2a79b8}.imbalance-bar{height:8px;border-radius:999px;background:linear-gradient(90deg,#c84a45 0 50%,#2a79b8 50% 100%);position:relative;margin-top:8px;overflow:hidden}.imbalance-bar i{position:absolute;top:0;bottom:0;width:3px;background:var(--ink);transform:translateX(-50%)}@media(max-width:980px){.inspect-grid{grid-template-columns:1fr}.inspect-card{padding:10px}.inspect-chart{min-height:220px}}@media(max-width:720px){.board-reading,.book-levels{grid-template-columns:1fr}.inspect-controls .button{width:100%}}
@media(max-width:720px){.chart-options .control{min-width:100%}.donut-legend{grid-template-columns:1fr}}
</style>
</head>
<body>
<main>
  <header class="hero">
    <div class="eyebrow">TRADE DAILY / VISUAL REVIEW</div>
    <h1>1年分のトレードを、銘柄別に見る</h1>
    <p>日誌の振り返りを、日別損益・銘柄別損益・エントリー／エグジットの組み合わせで整理しています。銘柄コードまたは銘柄名を検索すると、下のすべての表示がその銘柄に絞り込まれます。</p>
    <div class="toolbar">
      <label class="control wide">銘柄を検索（コード／銘柄名）<input id="symbolSearch" type="search" placeholder="例：9984 / ＳＢＧ / ソフトバンク"></label>
      <label class="control">期間<select id="periodSelect"><option value="all">保存済み全期間</option></select></label>
      <label class="control">方向<select id="directionSelect"><option value="all">ロング＋ショート</option><option value="long">ロングのみ</option><option value="short">ショートのみ</option></select></label>
      <button class="button" id="clearButton" type="button">フィルターをクリア</button>
    </div>
    <div class="filter-state" id="filterState" aria-live="polite"></div>
    <nav class="theme-nav" aria-label="表示切り替え"><a href="./annual_trade_dashboard.html">☀ ライト版</a><a href="./annual_trade_dashboard_dark.html">☾ ダーク版</a><a href="../site_structure.html">サイト構成・導線図</a></nav>
  </header>

  <section class="cards" id="kpis" aria-label="集計サマリー"></section>

  <section class="grid">
    <article class="panel"><h2>累積損益の推移</h2><p class="sub">絞り込み後の決済損益を日付順に積み上げています。</p><div class="chart-wrap" id="cumulativeChart"></div><div class="legend"><span class="swatch" style="background:#117d76"></span>累積損益　<span class="swatch" style="background:#9eb0be"></span>ゼロライン</div></article>
    <article class="panel"><h2>日別損益</h2><p class="sub">プラスの日とマイナスの日を色分けしています。</p><div class="chart-wrap" id="dailyChart"></div><div class="legend"><span class="swatch" style="background:#1a9662"></span>プラス　<span class="swatch" style="background:#d0635b"></span>マイナス</div></article>
    <article class="panel"><h2 id="symbolChartTitle">銘柄別の損益ランキング</h2><p class="sub" id="symbolChartSub">検索時は該当銘柄だけを表示します。未検索時は上位／下位を表示。</p><div class="chart-options" aria-label="銘柄グラフ設定"><label class="control">表示形式<select id="symbolChartType"><option value="bar">横棒グラフ</option><option value="donut">円グラフ（損益構成）</option></select></label><label class="control">並び順<select id="symbolSort"><option value="pnl">損益順</option><option value="pairs">件数順</option><option value="winRate">勝率順</option><option value="code">コード順</option></select></label><label class="control">表示数<select id="symbolLimit"><option value="10">上位10銘柄</option><option value="20" selected>上位20銘柄</option><option value="all">全銘柄</option></select></label></div><div class="chart-wrap" id="symbolChart"></div></article>
    <article class="panel"><h2>エントリー時刻別の傾向</h2><p class="sub">エントリー時刻を30分ごとにまとめ、損益と件数を表示しています。</p><div class="chart-wrap" id="timeChart"></div></article>
  </section>

  <section class="panel" style="margin-bottom:15px"><h2>銘柄別サマリー</h2><p class="sub">銘柄検索後は、その銘柄のロング／ショート別の実績を確認できます。</p><div id="symbolSummary"></div></section>

  <section class="panel" style="margin-bottom:15px"><h2>エントリー・エグジット一覧</h2><p class="sub">CSVの新規約定と返済約定をFIFOで対応させた実現損益です。分割決済は複数行になります。</p><div id="tradeSummary"></div><div class="table-wrap" id="tradeTable"></div></section>

  <section class="panel inspect-panel" id="tradeInspector" style="margin-bottom:15px" hidden>
    <h2>1件ずつ検証：歩み値・板読みチャート</h2>
    <p class="sub">一覧の「検証」から1件を選び、歩み値を15秒足・2分足・当日日足へ集約します。エントリー／エグジット、出来高、取得できた板読みスナップショットを同じ画面で確認できます。</p>
    <div class="inspect-selected" id="inspectorSelected"></div>
    <div class="inspect-controls"><button class="button" id="generateInspectorButton" type="button">選択したトレードのチャートを生成</button><span class="inspect-status" id="inspectorStatus" aria-live="polite">検証する行を選択してください。</span></div>
    <div class="inspect-source" id="inspectorSource">歩み値と板読みの公開データは、生成ボタンを押したときに取得します。</div>
    <div class="inspect-grid">
      <article class="inspect-card"><h3>15秒足</h3><p class="sub" id="chart15sMeta">—</p><div class="inspect-chart" id="chart15s"></div></article>
      <article class="inspect-card"><h3>2分足</h3><p class="sub" id="chart2mMeta">—</p><div class="inspect-chart" id="chart2m"></div></article>
      <article class="inspect-card"><h3>日足（当日）</h3><p class="sub" id="chartDailyMeta">歩み値から当日のOHLCを集計</p><div class="inspect-chart" id="chartDaily"></div></article>
    </div>
    <article class="inspect-card board-card"><h3>板読みスナップショット</h3><p class="sub">エントリー時刻に近い公開板データの最終スナップショット。板が保存されていない日付は理由を表示します。</p><div id="boardView" class="empty">チャート生成後に表示します。</div></article>
  </section>

  <p class="footer" id="sourceNote"></p>
</main>

<script id="tradeData" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('tradeData').textContent);
const $ = id => document.getElementById(id);
const fmtInt = value => Number(value || 0).toLocaleString('ja-JP');
const fmtYen = value => `${Number(value || 0).toLocaleString('ja-JP',{maximumFractionDigits:0})}円`;
const fmtDate = value => value ? `${value.slice(0,4)}/${value.slice(4,6)}/${value.slice(6,8)}` : '—';
const fmtPct = value => `${Number(value || 0).toLocaleString('ja-JP',{maximumFractionDigits:1})}%`;
const signClass = value => Number(value) > 0 ? 'positive' : Number(value) < 0 ? 'negative' : '';
const escapeHtml = text => String(text ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const shortName = (name, code) => `${name || code}（${code}）`;

function parseDate(value){ return new Date(Number(value.slice(0,4)), Number(value.slice(4,6))-1, Number(value.slice(6,8))); }
function matchesPeriod(date, period){ return period === 'all' || date.startsWith(period); }
function yen(value){ return Number(value || 0).toLocaleString('ja-JP',{maximumFractionDigits:0}); }
function svgShell(w,h,body){ return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img">${body}</svg>`; }
function scale(value, min, max, outMin, outMax){ return max === min ? (outMin + outMax) / 2 : outMin + (value-min)/(max-min)*(outMax-outMin); }
function niceMax(values){ const max = Math.max(...values.map(v => Math.abs(Number(v)||0)), 1); return max * 1.12; }
function axisLines(w, top, bottom, left, right, maxAbs, ticks=4){
  let out = '';
  for(let i=0;i<=ticks;i++){
    const value = maxAbs - (2*maxAbs*i/ticks);
    const y = scale(value,-maxAbs,maxAbs,top,bottom);
    out += `<line class="${Math.abs(value)<1e-9?'zero':'gridline'}" x1="${left}" y1="${y}" x2="${w-right}" y2="${y}"/><text class="axis" x="${left-8}" y="${y+4}" text-anchor="end">${yen(value)}</text>`;
  }
  return out;
}
function noData(message){ return `<div class="empty">${escapeHtml(message)}</div>`; }

function renderCumulative(rows){
  if(!rows.length) return noData('該当する決済データがありません。');
  const W=760,H=285,L=58,R=15,T=18,B=35;
  let sum=0; const points=rows.map(row=>{sum+=Number(row.pnl||0);return {...row,cum:sum};});
  const maxAbs=niceMax(points.map(p=>p.cum));
  let body=axisLines(W,T,H-B,L,R,maxAbs);
  const coords=points.map((p,i)=>[scale(i,0,Math.max(points.length-1,1),L,W-R),scale(p.cum,-maxAbs,maxAbs,T,H-B)]);
  body += `<polyline fill="none" stroke="#117d76" stroke-width="3" stroke-linejoin="round" points="${coords.map(p=>p.join(',')).join(' ')}"/>`;
  coords.forEach((p,i)=>{ if(points.length<80 || i%Math.ceil(points.length/70)===0 || i===points.length-1) body+=`<circle cx="${p[0]}" cy="${p[1]}" r="3.2" fill="#117d76"><title>${fmtDate(points[i].date)} ${fmtYen(points[i].cum)}</title></circle>`; });
  const labels=[0,Math.floor((points.length-1)/2),points.length-1];
  labels.forEach(i=>body+=`<text class="axis" x="${coords[i][0]}" y="${H-10}" text-anchor="${i===0?'start':i===points.length-1?'end':'middle'}">${fmtDate(points[i].date)}</text>`);
  return svgShell(W,H,body);
}
function renderDaily(rows){
  if(!rows.length) return noData('該当する日別データがありません。');
  const W=760,H=285,L=58,R=15,T=18,B=35, maxAbs=niceMax(rows.map(r=>r.pnl)), base=scale(0,-maxAbs,maxAbs,T,H-B);
  let body=axisLines(W,T,H-B,L,R,maxAbs);
  const slot=(W-L-R)/Math.max(rows.length,1), bar=Math.max(1.5,slot*.72);
  rows.forEach((row,i)=>{const x=L+i*slot+(slot-bar)/2,y=scale(Math.max(0,row.pnl),-maxAbs,maxAbs,T,H-B),y2=scale(Math.min(0,row.pnl),-maxAbs,maxAbs,T,H-B);body+=`<rect x="${x}" y="${Math.min(y,y2)}" width="${bar}" height="${Math.max(1,Math.abs(y2-y))}" rx="2" class="${row.pnl>0?'bar-pos':row.pnl<0?'bar-neg':'bar-neutral'}"><title>${fmtDate(row.date)} ${fmtYen(row.pnl)} / ${fmtInt(row.pairs)}件</title></rect>`;});
  const labels=[0,Math.floor((rows.length-1)/2),rows.length-1];
  labels.forEach(i=>body+=`<text class="axis" x="${L+i*slot+slot/2}" y="${H-10}" text-anchor="middle">${fmtDate(rows[i].date)}</text>`);
  return svgShell(W,H,body);
}
function symbolRowsForChart(rows){
  const sort=$('symbolSort').value;
  const sorted=[...rows].sort((a,b)=>{
    if(sort==='pairs') return Number(b.pairs)-Number(a.pairs)||Number(b.pnl)-Number(a.pnl);
    if(sort==='winRate') return Number(b.winRate)-Number(a.winRate)||Number(b.pnl)-Number(a.pnl);
    if(sort==='code') return String(a.code).localeCompare(String(b.code),'ja');
    return Number(b.pnl)-Number(a.pnl)||String(a.code).localeCompare(String(b.code),'ja');
  });
  const limit=$('symbolLimit').value;
  return limit==='all'?sorted:sorted.slice(0,Number(limit));
}
function symbolSortLabel(){
  const labels={pnl:'損益順',pairs:'件数順',winRate:'勝率順',code:'コード順'};
  return labels[$('symbolSort').value]||'損益順';
}
function renderSymbolBars(rows){
  if(!rows.length) return noData('該当する銘柄がありません。');
  const chosen=rows;
  const W=760,H=Math.max(285,chosen.length*25+35),L=185,R=75,T=15,B=15;
  const maxAbs=niceMax(chosen.map(r=>r.pnl)); let body='';
  const zero=scale(0,-maxAbs,maxAbs,L,W-R);
  body += `<line class="zero" x1="${zero}" y1="${T}" x2="${zero}" y2="${H-B}"/>`;
  chosen.forEach((row,i)=>{const y=T+i*25+4;const x=scale(row.pnl,-maxAbs,maxAbs,L,W-R);const left=Math.min(zero,x);const width=Math.max(1,Math.abs(x-zero));const label=escapeHtml(`${row.name}（${row.code}）`);body+=`<text class="rank-label" x="${L-10}" y="${y+13}" text-anchor="end">${label}</text><rect x="${left}" y="${y}" width="${width}" height="16" rx="4" class="${row.pnl>0?'bar-pos':row.pnl<0?'bar-neg':'bar-neutral'}"><title>${label} ${fmtYen(row.pnl)} / ${fmtInt(row.pairs)}件 / 勝率 ${fmtPct(row.winRate)}</title></rect><text class="rank-value" x="${row.pnl>=0?left+width+6:left-6}" y="${y+13}" text-anchor="${row.pnl>=0?'start':'end'}">${fmtYen(row.pnl)}</text>`;});
  return svgShell(W,H,body);
}
function donutPath(cx,cy,r,start,end){
  const x1=cx+r*Math.cos(start),y1=cy+r*Math.sin(start),x2=cx+r*Math.cos(end),y2=cy+r*Math.sin(end),large=end-start>Math.PI?1:0;
  return `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`;
}
function renderSymbolDonut(rows){
  if(!rows.length) return noData('該当する銘柄がありません。');
  const weighted=rows.map(row=>({...row,weight:Math.abs(Number(row.pnl)||0)}));
  const total=weighted.reduce((sum,row)=>sum+row.weight,0);
  if(total<=0) return noData('損益が0円のため、円グラフを表示できません。横棒グラフで件数を確認してください。');
  const W=760,H=Math.max(300,Math.min(680,170+weighted.length*25)),cx=190,cy=H/2,r=Math.min(132,H/2-24),legendX=365;
  let body=`<title>銘柄別の損益構成</title><desc>各銘柄の損益の絶対値を面積で比較しています。損益額は凡例に表示します。</desc>`;
  let angle=-Math.PI/2;
  weighted.forEach((row,i)=>{
    const start=angle,end=angle+(row.weight/total)*Math.PI*2;angle=end;
    const klass=row.pnl>0?'positive':row.pnl<0?'negative':'neutral';
    const alt=i%2?' alt':'';
    const label=escapeHtml(shortName(row.name,row.code));
    body+=`<path class="donut-slice ${klass}${alt}" d="${donutPath(cx,cy,r,start,end)}" stroke="var(--card)" stroke-width="2"><title>${label} ${fmtYen(row.pnl)} / 構成 ${fmtPct(row.weight/total*100)}</title></path>`;
  });
  body+=`<circle cx="${cx}" cy="${cy}" r="${Math.max(52,r*.43)}" fill="var(--card)"/><text class="rank-label" x="${cx}" y="${cy-5}" text-anchor="middle">損益構成</text><text class="rank-value" x="${cx}" y="${cy+14}" text-anchor="middle">${fmtYen(weighted.reduce((sum,row)=>sum+Number(row.pnl||0),0))}</text>`;
  const legend=weighted.map((row,i)=>{const klass=row.pnl>0?'positive':row.pnl<0?'negative':'neutral';const alt=i%2?' alt':'';const label=escapeHtml(shortName(row.name,row.code));const share=fmtPct(row.weight/total*100);return `<div class="donut-item"><i class="donut-swatch ${klass}${alt}"></i><span title="${label}">${label}　<b class="${signClass(row.pnl)}">${fmtYen(row.pnl)}</b> <small>(${share})</small></span></div>`}).join('');
  body+=`<foreignObject x="${legendX}" y="20" width="365" height="${H-40}"><div xmlns="http://www.w3.org/1999/xhtml" class="donut-legend">${legend}</div></foreignObject>`;
  return svgShell(W,H,body);
}
function renderSymbolChart(rows){
  const chosen=symbolRowsForChart(rows);const type=$('symbolChartType').value;
  const limitText=$('symbolLimit').value==='all'?'全銘柄':`上位${$('symbolLimit').value}銘柄`;
  if(type==='donut'){
    $('symbolChartTitle').textContent='銘柄別の損益構成';
    $('symbolChartSub').textContent=`円グラフ：損益の絶対値を面積で比較（${symbolSortLabel()}・${limitText}）。中央の数値は表示銘柄の合計損益です。`;
    return renderSymbolDonut(chosen);
  }
  $('symbolChartTitle').textContent='銘柄別の損益ランキング';
  $('symbolChartSub').textContent=`横棒グラフ：${symbolSortLabel()}で並べた${limitText}。プラスとマイナスを色分けしています。`;
  return renderSymbolBars(chosen);
}
function renderTimeBars(rows){
  if(!rows.length) return noData('該当するエントリー時刻データがありません。');
  const sorted=[...rows].sort((a,b)=>a.minute-b.minute); const W=760,H=285,L=45,R=15,T=18,B=34,maxAbs=niceMax(sorted.map(r=>r.pnl)),base=scale(0,-maxAbs,maxAbs,T,H-B);let body=axisLines(W,T,H-B,L,R,maxAbs);
  const slot=(W-L-R)/Math.max(sorted.length,1),bar=Math.max(7,slot*.68);
  sorted.forEach((row,i)=>{const x=L+i*slot+(slot-bar)/2,y=scale(Math.max(0,row.pnl),-maxAbs,maxAbs,T,H-B),y2=scale(Math.min(0,row.pnl),-maxAbs,maxAbs,T,H-B);body+=`<rect x="${x}" y="${Math.min(y,y2)}" width="${bar}" height="${Math.max(1,Math.abs(y2-y))}" rx="2" class="${row.pnl>=0?'bar-time':'bar-time-neg'}"><title>${escapeHtml(row.label)} ${fmtYen(row.pnl)} / ${fmtInt(row.pairs)}件</title></rect><text class="axis" x="${x+bar/2}" y="${H-10}" text-anchor="middle">${escapeHtml(row.label)}</text>`;});
  return svgShell(W,H,body);
}

function aggregate(trades){
  const symbolMap=new Map(), dayMap=new Map(), timeMap=new Map();
  trades.forEach(trade=>{
    const day=dayMap.get(trade.date)||{date:trade.date,pairs:0,pnl:0}; day.pairs++;day.pnl+=Number(trade.pnl||0);dayMap.set(trade.date,day);
    const code=trade.code; const symbol=symbolMap.get(code)||{code,name:trade.name,pnl:0,pairs:0,wins:0,losses:0,flat:0,shares:0,longPairs:0,shortPairs:0};symbol.name=trade.name||symbol.name;symbol.pnl+=Number(trade.pnl||0);symbol.pairs++;symbol.shares+=Number(trade.quantity||0);symbol[trade.direction==='long'?'longPairs':'shortPairs']++;if(trade.pnl>0)symbol.wins++;else if(trade.pnl<0)symbol.losses++;else symbol.flat++;symbolMap.set(code,symbol);
    const minute=Math.floor(Number(trade.entryMinute||0)/30)*30; const time=timeMap.get(minute)||{minute,label:`${String(Math.floor(minute/60)).padStart(2,'0')}:${String(minute%60).padStart(2,'0')}`,pairs:0,pnl:0};time.pairs++;time.pnl+=Number(trade.pnl||0);timeMap.set(minute,time);
  });
  const daily=[...dayMap.values()].sort((a,b)=>a.date.localeCompare(b.date));let cum=0;daily.forEach(row=>{row.pnl=Math.round(row.pnl*100)/100;cum+=row.pnl;row.cum=cum});
  const symbols=[...symbolMap.values()].map(row=>({...row,winRate:row.pairs?row.wins/row.pairs*100:0,pnl:Math.round(row.pnl*100)/100}));
  const times=[...timeMap.values()].sort((a,b)=>a.minute-b.minute).map(row=>({...row,pnl:Math.round(row.pnl*100)/100}));
  const pnl=trades.reduce((sum,row)=>sum+Number(row.pnl||0),0);const wins=trades.filter(row=>row.pnl>0).length;const losses=trades.filter(row=>row.pnl<0).length;
  return {daily,symbols,times,pnl,wins,losses,flat:trades.length-wins-losses};
}
function populatePeriods(){
  const periods=new Set(); DATA.daily.forEach(row=>{periods.add(row.date.slice(0,4));periods.add(row.date.slice(0,6));});
  [...periods].sort().forEach(period=>{const option=document.createElement('option');option.value=period;option.textContent=period.length===4?`${period}年`:`${period.slice(0,4)}年${Number(period.slice(4))}月`;$('periodSelect').appendChild(option)});
}
function renderKpis(summary,trades){
  const winRate=trades.length?summary.wins/trades.length*100:0;
  $('kpis').innerHTML=`<div class="metric"><span>実現損益</span><strong class="${signClass(summary.pnl)}">${fmtYen(summary.pnl)}</strong><small>${trades.length?'決済ペアベース':'データなし'}</small></div><div class="metric"><span>決済トレード</span><strong>${fmtInt(trades.length)}件</strong><small>FIFO対応後</small></div><div class="metric"><span>勝率</span><strong>${fmtPct(winRate)}</strong><small>${fmtInt(summary.wins)}勝 / ${fmtInt(summary.losses)}敗</small></div><div class="metric"><span>対象銘柄</span><strong>${fmtInt(summary.symbols.length)}銘柄</strong><small>検索後の表示</small></div><div class="metric"><span>平均損益</span><strong class="${signClass(trades.length?summary.pnl/trades.length:0)}">${fmtYen(trades.length?summary.pnl/trades.length:0)}</strong><small>${fmtInt(summary.flat)}引き分け</small></div>`;
}
function renderSymbolSummary(symbols){
  if(!symbols.length){$('symbolSummary').innerHTML=noData('該当する銘柄がありません。');return;}
  const rows=[...symbols].sort((a,b)=>b.pnl-a.pnl);$('symbolSummary').innerHTML=`<div class="summary-row">${rows.slice(0,12).map(row=>`<span class="tag">${escapeHtml(shortName(row.name,row.code))} <b class="${signClass(row.pnl)}" style="margin-left:5px">${fmtYen(row.pnl)}</b></span>`).join('')}</div><div class="table-wrap"><table><thead><tr><th>銘柄</th><th>損益</th><th>件数</th><th>勝率</th><th>ロング</th><th>ショート</th><th>株数</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${escapeHtml(shortName(row.name,row.code))}</td><td class="${signClass(row.pnl)}">${fmtYen(row.pnl)}</td><td>${fmtInt(row.pairs)}</td><td>${fmtPct(row.winRate)}</td><td>${fmtInt(row.longPairs)}</td><td>${fmtInt(row.shortPairs)}</td><td>${fmtInt(row.shares)}</td></tr>`).join('')}</tbody></table></div>`;
}
let selectedTrade=null;
let visibleTrades=[];
let inspectorRequestId=0;

function tradeKey(row){
  return [row.date,row.code,row.direction,row.entryTime,row.exitTime,row.entryPrice,row.exitPrice,row.quantity].join('|');
}
function resetInspector(){
  selectedTrade=null;
  $('tradeInspector').hidden=true;
  $('inspectorSelected').innerHTML='';
  $('inspectorStatus').textContent='検証する行を選択してください。';
  $('inspectorSource').textContent='歩み値と板読みの公開データは、生成ボタンを押したときに取得します。';
  ['chart15s','chart2m','chartDaily'].forEach(id=>$(id).innerHTML='');
  ['chart15sMeta','chart2mMeta','chartDailyMeta'].forEach(id=>$(id).textContent='—');
  $('boardView').innerHTML='チャート生成後に表示します。';
}
function selectInspectorTrade(trade){
  selectedTrade=trade;
  $('tradeInspector').hidden=false;
  $('inspectorSelected').innerHTML=`<span class="tag">${escapeHtml(fmtDate(trade.date))}</span><span class="tag">${escapeHtml(shortName(trade.name,trade.code))}</span><span class="tag ${trade.direction==='long'?'dir-long':'dir-short'}">${trade.direction==='long'?'ロング':'ショート'}</span><span class="tag">${escapeHtml(trade.entryTime)} → ${escapeHtml(trade.exitTime)}</span><span class="tag ${signClass(trade.pnl)}">${fmtYen(trade.pnl)}</span>`;
  $('inspectorStatus').textContent='対象を選択しました。生成ボタンで歩み値・板読みを取得します。';
  $('inspectorSource').textContent='歩み値と板読みの公開データは、生成ボタンを押したときに取得します。';
  ['chart15s','chart2m','chartDaily'].forEach(id=>$(id).innerHTML='<div class="empty">生成待ち</div>');
  ['chart15sMeta','chart2mMeta','chartDailyMeta'].forEach(id=>$(id).textContent='生成待ち');
  $('boardView').innerHTML='<div class="empty">生成ボタンを押すと、エントリー時刻に近い板読みスナップショットを探します。</div>';
  document.querySelectorAll('.trade-row').forEach(row=>row.classList.toggle('selected',row.dataset.tradeKey===tradeKey(trade)));
  $('tradeInspector').scrollIntoView({behavior:'smooth',block:'start'});
}
function timeToSeconds(value){
  const compact=String(value||'').match(/^(\d{2})(\d{2})(\d{2})$/);if(compact)return Number(compact[1])*3600+Number(compact[2])*60+Number(compact[3]);
  const match=String(value||'').match(/(\d{1,2}):(\d{2})(?::(\d{2}))?/);if(!match)return null;
  return Number(match[1])*3600+Number(match[2])*60+Number(match[3]||0);
}
function formatClock(seconds){
  const value=Math.max(0,Math.round(Number(seconds)||0));return `${String(Math.floor(value/3600)).padStart(2,'0')}:${String(Math.floor(value%3600/60)).padStart(2,'0')}:${String(value%60).padStart(2,'0')}`;
}
function sourceUrl(path){
  const base=DATA.sources&&DATA.sources.publicR2BaseUrl;if(!base)return '';
  return `${base}/${String(path).split('/').map(part=>encodeURIComponent(part)).join('/')}`;
}
function walkUrlFor(trade){
  const pattern=DATA.sources&&DATA.sources.walkPathPattern;if(!pattern)return '';
  return sourceUrl(pattern.replaceAll('{date}',trade.date).replaceAll('{code}',trade.code));
}
function parseWalkCsv(text){
  const rows=[];const lines=String(text||'').split(/\r?\n/);
  for(const line of lines){
    if(!line.trim())continue;
    const cells=line.split(',');const price=Number(String(cells[0]||'').replace(/[,_]/g,''));const quantity=Number(String(cells[1]||'').replace(/[,_]/g,''));const seconds=timeToSeconds(cells[3]);
    if(!Number.isFinite(price)||price<=0||!Number.isFinite(quantity)||quantity<0||seconds===null)continue;
    rows.push({price,quantity,seconds});
  }
  return rows.sort((a,b)=>a.seconds-b.seconds);
}
function aggregateWalk(rows, interval, start=0, end=86400){
  const map=new Map();
  rows.forEach(row=>{if(row.seconds<start||row.seconds>end)return;const bucket=Math.floor(row.seconds/interval)*interval;let bar=map.get(bucket);if(!bar){bar={time:bucket,open:row.price,high:row.price,low:row.price,close:row.price,volume:0,trades:0};map.set(bucket,bar)}bar.high=Math.max(bar.high,row.price);bar.low=Math.min(bar.low,row.price);bar.close=row.price;bar.volume+=row.quantity;bar.trades++});
  return [...map.values()].sort((a,b)=>a.time-b.time);
}
function windowBars(rows,trade,interval,marginSeconds){
  const entry=timeToSeconds(trade.entryTime),exit=timeToSeconds(trade.exitTime);if(entry===null)return aggregateWalk(rows,interval);
  const low=Math.max(0,Math.min(entry,exit===null?entry:exit)-marginSeconds);const high=Math.min(86400,Math.max(entry,exit===null?entry:exit)+marginSeconds);let bars=aggregateWalk(rows,interval,low,high);
  if(!bars.length)bars=aggregateWalk(rows,interval);return bars;
}
function renderTradeChart(bars,trade,intervalLabel){
  if(!bars.length)return noData('この公開歩み値から表示できる足がありません。');
  const W=860,H=315,L=62,R=14,T=16,priceBottom=230,volumeTop=244,volumeBottom=278;
  const values=bars.flatMap(row=>[row.high,row.low]).concat([Number(trade.entryPrice),Number(trade.exitPrice)]).filter(Number.isFinite);let low=Math.min(...values),high=Math.max(...values);const pad=(high-low||Math.max(Math.abs(high)*.002,1))*.10;low-=pad;high+=pad;
  const xFor=(time)=>{const first=bars[0].time,last=bars[bars.length-1].time;return L+(last===first?(W-L-R)/2:(time-first)/(last-first)*(W-L-R))};
  let body='';for(let i=0;i<=4;i++){const value=high-(high-low)*i/4;const y=scale(value,low,high,T,priceBottom);body+=`<line class="gridline" x1="${L}" y1="${y}" x2="${W-R}" y2="${y}"/><text class="axis" x="${L-7}" y="${y+4}" text-anchor="end">${yen(value)}</text>`}
  const maxVolume=Math.max(...bars.map(row=>row.volume),1);const slot=(W-L-R)/Math.max(bars.length,1);const candleWidth=Math.max(1,Math.min(9,slot*.68));
  bars.forEach(row=>{const x=xFor(row.time);const color=row.close>=row.open?'wick-up':'wick-down';const yHigh=scale(row.high,low,high,T,priceBottom),yLow=scale(row.low,low,high,T,priceBottom),yOpen=scale(row.open,low,high,T,priceBottom),yClose=scale(row.close,low,high,T,priceBottom);const bodyY=Math.min(yOpen,yClose),bodyH=Math.max(1,Math.abs(yClose-yOpen));const vh=row.volume/maxVolume*(volumeBottom-volumeTop);body+=`<line class="${color}" x1="${x}" y1="${yHigh}" x2="${x}" y2="${yLow}" stroke-width="1"/><rect class="${color}" x="${x-candleWidth/2}" y="${bodyY}" width="${candleWidth}" height="${bodyH}" opacity=".86"><title>${formatClock(row.time)} O${yen(row.open)} H${yen(row.high)} L${yen(row.low)} C${yen(row.close)} / 出来高 ${fmtInt(row.volume)}</title></rect><rect class="bar-volume" x="${x-candleWidth/2}" y="${volumeBottom-vh}" width="${candleWidth}" height="${Math.max(1,vh)}"/>`});
  const marker=(time,price,label,klass)=>{if(time===null||!bars.length)return '';const x=xFor(Math.max(bars[0].time,Math.min(bars[bars.length-1].time,time)));const y=scale(price,low,high,T,priceBottom);return `<line class="trade-line" x1="${x}" y1="${T}" x2="${x}" y2="${volumeBottom}"/><circle class="${klass}" cx="${x}" cy="${y}" r="5"><title>${label} ${formatClock(time)} ${yen(price)}</title></circle><text class="axis" x="${x}" y="${T+10}" text-anchor="middle">${label}</text>`};
  body+=marker(timeToSeconds(trade.entryTime),Number(trade.entryPrice),'E','entry-mark');body+=marker(timeToSeconds(trade.exitTime),Number(trade.exitPrice),'X','exit-mark');
  const labels=[bars[0],bars[Math.floor((bars.length-1)/2)],bars[bars.length-1]];labels.forEach((row,index)=>{const x=xFor(row.time);body+=`<text class="axis" x="${x}" y="${H-10}" text-anchor="${index===0?'start':index===2?'end':'middle'}">${formatClock(row.time).slice(0,5)}</text>`});body+=`<text class="axis" x="${L}" y="${volumeTop-6}">出来高</text><text class="axis" x="${W-R}" y="${H-10}" text-anchor="end">${escapeHtml(intervalLabel)}</text>`;return svgShell(W,H,body);
}
function renderInspectionHeader(trade,rows){
  const entry=timeToSeconds(trade.entryTime),exit=timeToSeconds(trade.exitTime);const prices=rows.length?`${yen(Math.min(...rows.map(row=>row.price)))}〜${yen(Math.max(...rows.map(row=>row.price)))}`:'—';
  $('inspectorSource').innerHTML=`歩み値 <a href="${walkUrlFor(trade)}" target="_blank" rel="noopener">${escapeHtml(trade.date)} / ${escapeHtml(trade.code)} を開く</a>　｜　価格範囲 ${prices}　｜　E ${escapeHtml(trade.entryTime)} / X ${escapeHtml(trade.exitTime)}　｜　※公開歩み値を画面内で集約した検証用チャートです。`;
  $('chart15sMeta').textContent=`${formatClock(entry||0).slice(0,5)}〜${formatClock(exit||entry||0).slice(0,5)}付近・15秒ごと・出来高付き`;
  $('chart2mMeta').textContent=`${formatClock(entry||0).slice(0,5)}〜${formatClock(exit||entry||0).slice(0,5)}付近・2分ごと・出来高付き`;
  $('chartDailyMeta').textContent=`${fmtDate(trade.date)}の歩み値から集計した当日OHLC・出来高`;
}
async function readBoardSnapshot(url,code,targetSeconds){
  const response=await fetch(url,{cache:'no-store'});if(!response.ok)throw new Error(`板読みデータ HTTP ${response.status}`);if(!response.body||typeof DecompressionStream==='undefined')throw new Error('このブラウザはgzipの板読みデータを展開できません');
  const reader=response.body.pipeThrough(new DecompressionStream('gzip')).getReader();const decoder=new TextDecoder();let buffer='',best=null,readLines=0;const target=targetSeconds??0;
  const consider=(line)=>{if(!line.trim())return;readLines++;let obj;try{obj=JSON.parse(line)}catch{return}if(String(obj.symbol||'')!==String(code))return;const source=String(obj.source||'');if(!['PUSH','REST_BOARD','ORDERS'].includes(source))return;const time=Date.parse(obj.observed_at||'');if(!Number.isFinite(time))return;const seconds=timeToSeconds(new Date(time).toLocaleTimeString('en-GB',{timeZone:'Asia/Tokyo',hour12:false}));if(seconds===null)return;const distance=Math.abs(seconds-target);if(!best||distance<best.distance)best={obj,distance,seconds};};
  let done=false;while(!done){const chunk=await reader.read();done=chunk.done;if(chunk.value)buffer+=decoder.decode(chunk.value,{stream:!done});let newline;while((newline=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,newline).replace(/\r$/,'');buffer=buffer.slice(newline+1);consider(line);if(best&&best.seconds>target+600){done=true;break}}if(done)break}if(buffer.trim())consider(buffer);try{await reader.cancel()}catch{}return {snapshot:best,readLines};
}
function levelList(raw,prefix){const values=[];for(let i=1;i<=10;i++){const value=raw&&raw[`${prefix}${i}`];if(!value)continue;const price=Number(value.Price),qty=Number(value.Qty);if(Number.isFinite(price)&&Number.isFinite(qty))values.push({price,qty})}return values}
function renderBoardSnapshot(result,trade,file){
  if(!result||!result.snapshot){$('boardView').innerHTML='<div class="empty">対象銘柄の板読みスナップショットを見つけられませんでした。歩み値チャートは表示済みです。</div>';return}
  const obj=result.snapshot.obj,raw=obj.raw||{},compact=obj.compact||{},asks=levelList(raw,'Sell'),bids=levelList(raw,'Buy'),askQty=asks.reduce((sum,row)=>sum+row.qty,0),bidQty=bids.reduce((sum,row)=>sum+row.qty,0),total=askQty+bidQty,imbalance=total?(bidQty-askQty)/total:0,marker=Math.max(0,Math.min(100,(imbalance+1)*50));
  const levels=(rows,klass)=>rows.slice(0,5).map(row=>`<div class="book-level ${klass}"><span>${yen(row.price)}</span><b>${fmtInt(row.qty)}</b></div>`).join('')||'<div class="book-level">—</div>';
  const observed=obj.observed_at||'—';const link=sourceUrl(file.key);$('boardView').innerHTML=`<div class="board-reading"><div class="mini"><span>取得時刻</span><b>${escapeHtml(observed.replace('T',' ').slice(0,19))}</b><small>エントリーとの差 ${fmtInt(result.snapshot.distance)}秒</small></div><div class="mini"><span>最良気配</span><b>${yen(compact.bid_price||bids[0]?.price||0)} / ${yen(compact.ask_price||asks[0]?.price||0)}</b><small>買い ${fmtInt(compact.bid_qty||bids[0]?.qty||0)}　売り ${fmtInt(compact.ask_qty||asks[0]?.qty||0)}</small></div><div class="mini"><span>板上位5本合計</span><b class="${imbalance>=0?'positive':'negative'}">${imbalance>=0?'買い優勢':'売り優勢'} ${Math.abs(imbalance*100).toLocaleString('ja-JP',{maximumFractionDigits:1})}%</b><div class="imbalance-bar"><i style="left:${marker}%"></i></div></div><div class="mini"><span>データ源</span><b>${escapeHtml(obj.source||'—')}</b><small>${fmtInt(result.readLines)}行を走査</small></div></div><div class="book-levels"><div class="book-side"><strong>売り板</strong>${levels(asks,'ask')}</div><div class="book-side"><strong>買い板</strong>${levels(bids,'bid')}</div></div><p class="note"><a href="${link}" target="_blank" rel="noopener">板読み元ファイルを開く</a>　${escapeHtml(file.key)}　｜ ${escapeHtml(trade.date)} ${escapeHtml(trade.code)} のエントリー近傍。</p>`;
}
function boardFileFor(trade){
  const byDate=DATA.sources&&DATA.sources.boardIndex&&DATA.sources.boardIndex[trade.date];const files=byDate&&byDate[trade.code];if(!files||!files.length)return null;const target=timeToSeconds(trade.entryTime)||0;return [...files].sort((a,b)=>Math.abs((timeToSeconds(a.captureTime)||0)-target)-Math.abs((timeToSeconds(b.captureTime)||0)-target))[0]}
async function generateInspector(){
  if(!selectedTrade){$('inspectorStatus').textContent='先に一覧の「検証」でトレードを選択してください。';return}
  const trade=selectedTrade;const requestId=++inspectorRequestId;const walkUrl=walkUrlFor(trade);$('inspectorStatus').textContent='歩み値を取得して、3種類の足へ集約しています…';$('boardView').innerHTML='<div class="empty">板読みデータを確認中…</div>';
  if(!walkUrl){$('inspectorStatus').textContent='公開R2の設定がないため、歩み値を取得できません。';return}
  try{
    const response=await fetch(walkUrl,{cache:'no-store'});if(!response.ok)throw new Error(`歩み値CSV HTTP ${response.status}`);const rows=parseWalkCsv(await response.text());if(!rows.length)throw new Error('歩み値CSVに有効な約定行がありません');if(requestId!==inspectorRequestId)return;
    const bars15=windowBars(rows,trade,15,30*60),bars2=windowBars(rows,trade,120,90*60),barsDaily=aggregateWalk(rows,86400);renderInspectionHeader(trade,rows);$('chart15s').innerHTML=renderTradeChart(bars15,trade,'表示範囲');$('chart2m').innerHTML=renderTradeChart(bars2,trade,'表示範囲');$('chartDaily').innerHTML=renderTradeChart(barsDaily,trade,'当日');$('inspectorStatus').textContent=`生成完了：歩み値 ${fmtInt(rows.length)}行 / 15秒足 ${fmtInt(bars15.length)}本 / 2分足 ${fmtInt(bars2.length)}本`;
    const file=boardFileFor(trade);if(!file){$('boardView').innerHTML='<div class="empty">この日付・銘柄の板読み元ファイルは公開データにありません。歩み値チャートは表示済みです。</div>';return}
    const boardResult=await readBoardSnapshot(sourceUrl(file.key),trade.code,timeToSeconds(trade.entryTime)||0);if(requestId!==inspectorRequestId)return;renderBoardSnapshot(boardResult,trade,file);
  }catch(error){if(requestId!==inspectorRequestId)return;const message=error&&error.message?error.message:String(error);$('inspectorStatus').textContent=`取得できませんでした：${message}`;$('inspectorSource').innerHTML=`${escapeHtml(message)}　｜　公開R2への接続または対象データの有無を確認してください。`;$('chart15s').innerHTML=noData('歩み値を取得できませんでした。');$('chart2m').innerHTML=noData('歩み値を取得できませんでした。');$('chartDaily').innerHTML=noData('歩み値を取得できませんでした。');$('boardView').innerHTML='<div class="empty">板読みも未表示です。</div>'}
}
function renderTrades(trades){
  visibleTrades=trades;
  if(!trades.length){$('tradeSummary').innerHTML='';$('tradeTable').innerHTML=noData('該当するエントリー／エグジットがありません。');resetInspector();return;}
  const ordered=[...trades].sort((a,b)=>`${b.date}${b.exitMinute}`.localeCompare(`${a.date}${a.exitMinute}`));const limit=500;const shown=ordered.slice(0,limit);$('tradeSummary').innerHTML=`<div class="summary-row"><div class="mini"><span>表示</span><b>${fmtInt(shown.length)} / ${fmtInt(ordered.length)}件</b></div><div class="mini"><span>対象期間</span><b>${fmtDate(ordered[ordered.length-1].date)}〜${fmtDate(ordered[0].date)}</b></div><div class="mini"><span>1件検証</span><b>行を選んでチャート生成</b></div></div>`;$('tradeTable').innerHTML=`<table><thead><tr><th>日付</th><th>銘柄</th><th>方向</th><th>エントリー</th><th>エグジット</th><th>数量</th><th>エントリー値</th><th>エグジット値</th><th>損益</th><th>保有</th><th>検証</th></tr></thead><tbody>${shown.map(row=>`<tr class="trade-row" data-trade-key="${escapeHtml(tradeKey(row))}"><td>${fmtDate(row.date)}</td><td>${escapeHtml(shortName(row.name,row.code))}</td><td class="${row.direction==='long'?'dir-long':'dir-short'}">${row.direction==='long'?'ロング':'ショート'}</td><td>${escapeHtml(row.entryTime)}</td><td>${escapeHtml(row.exitTime)}</td><td>${fmtInt(row.quantity)}</td><td>${yen(row.entryPrice)}</td><td>${yen(row.exitPrice)}</td><td class="${signClass(row.pnl)}">${fmtYen(row.pnl)}</td><td>${fmtInt(row.holdMinutes)}分</td><td><button class="inspect-button" type="button" data-inspect-key="${escapeHtml(tradeKey(row))}" aria-label="${escapeHtml(shortName(row.name,row.code))} ${escapeHtml(row.entryTime)}のトレードを検証">検証</button></td></tr>`).join('')}</tbody></table>`;
  if(selectedTrade&&!trades.some(row=>tradeKey(row)===tradeKey(selectedTrade)))resetInspector();
  if(selectedTrade)document.querySelectorAll('.trade-row').forEach(row=>row.classList.toggle('selected',row.dataset.tradeKey===tradeKey(selectedTrade)));
}
function update(){
  const query=$('symbolSearch').value.trim().toLocaleLowerCase('ja-JP');const period=$('periodSelect').value;const direction=$('directionSelect').value;
  const trades=DATA.trades.filter(row=>matchesPeriod(row.date,period)&&(!query||`${row.code} ${row.name}`.toLocaleLowerCase('ja-JP').includes(query))&&(direction==='all'||row.direction===direction));
  const summary=aggregate(trades);renderKpis(summary,trades);$('cumulativeChart').innerHTML=renderCumulative(summary.daily);$('dailyChart').innerHTML=renderDaily(summary.daily);$('symbolChart').innerHTML=renderSymbolChart(summary.symbols);$('timeChart').innerHTML=renderTimeBars(summary.times);renderSymbolSummary(summary.symbols);renderTrades(trades);
  const queryText=query?`銘柄検索「${escapeHtml($('symbolSearch').value.trim())}」`:'全銘柄';const periodText=period==='all'?'保存済み全期間':period.length===4?`${period}年`:`${period.slice(0,4)}年${Number(period.slice(4))}月`;const directionText=direction==='all'?'全方向':direction==='long'?'ロング':'ショート';$('filterState').innerHTML=`現在の表示：<strong>${queryText}</strong>　<span class="tag">${periodText}</span><span class="tag">${directionText}</span>　${fmtInt(trades.length)}件 / ${fmtInt(DATA.trades.length)}件`;
}
populatePeriods();
['symbolSearch','periodSelect','directionSelect','symbolChartType','symbolSort','symbolLimit'].forEach(id=>$(id).addEventListener('input',update));
$('clearButton').addEventListener('click',()=>{$('symbolSearch').value='';$('periodSelect').value='all';$('directionSelect').value='all';update()});
document.addEventListener('click',event=>{
  const button=event.target.closest('[data-inspect-key]');
  if(!button)return;
  const key=button.dataset.inspectKey;const trade=visibleTrades.find(row=>tradeKey(row)===key);
  if(trade)selectInspectorTrade(trade);
});
$('generateInspectorButton').addEventListener('click',generateInspector);
update();
$('sourceNote').textContent=`出典：${DATA.meta.startDate ? fmtDate(DATA.meta.startDate)+'〜'+fmtDate(DATA.meta.endDate) : '—'} に保存された松井証券CSV ${fmtInt(DATA.meta.fileCount)}日分。実行約定 ${fmtInt(DATA.meta.executionFills)}行（新規 ${fmtInt(DATA.meta.entryFills)}行／返済 ${fmtInt(DATA.meta.exitFills)}行）を読み込み、新規と返済を銘柄別・方向別にFIFO対応しました。未対応の建玉残は ${fmtInt(DATA.meta.openShares)}株、返済先を対応できなかった数量は ${fmtInt(DATA.meta.unmatchedExitShares)}株です。数値はCSVの約定単価と約定数から計算した実現損益で、手数料・税金は含みません。`;
</script>
</body>
</html>
'''


DARK_CSS = r'''
:root{color-scheme:dark;--ink:#e7f0f8;--muted:#9fb2c4;--line:#294156;--bg:#07131f;--card:#0d1e2d;--teal:#46d2c5;--blue:#76baff;--orange:#ffad5b;--red:#ff807a;--green:#52d69c;--soft:#102b3e;--shadow:0 10px 30px rgba(0,0,0,.28);--donut-positive:#46d2c5;--donut-positive-2:#76baff;--donut-negative:#ff807a;--donut-negative-2:#ffad5b;--donut-neutral:#7290a5}
body{background:var(--bg);color:var(--ink)}.hero,.panel,.metric{background:var(--card);border-color:var(--line);box-shadow:var(--shadow)}.hero p,.panel .sub,.filter-state,.metric span,.metric small,.legend,.footer,.note{color:var(--muted)}.filter-state strong{color:var(--ink)}.control{color:var(--muted)}.control input,.control select,.button{border-color:#3b566d;background:#0a1826;color:var(--ink)}.control input::placeholder{color:#7e96aa}.button:hover{border-color:var(--teal);color:var(--teal)}.theme-nav a{border-color:#3b566d;background:#0a1826;color:var(--blue)}.theme-nav a:hover{border-color:var(--teal);color:var(--teal)}.chart .axis{fill:#a9bdcf!important}.chart .gridline{stroke:#294052!important}.chart .zero{stroke:#7893a8!important}.chart .tip{fill:#a9bdcf!important}.chart .rank-label{fill:#d4e4f0!important}.chart .rank-value{fill:#a9bdcf!important}.chart polyline{stroke:var(--teal)!important}.chart circle{fill:var(--teal)!important}.bar-pos{fill:var(--green)!important}.bar-neg,.bar-time-neg{fill:var(--red)!important}.bar-neutral{fill:#7290a5!important}.bar-time{fill:#56b2d4!important}.legend .swatch{opacity:.95}.legend .swatch:first-of-type{background:var(--teal)!important}.legend .swatch:last-of-type{background:#7893a8!important}.table-wrap{border-color:#294156}th,td{border-bottom-color:#203548}thead th{background:#122b3d;color:#c6d9e9}tbody tr:hover{background:#142e42}.dir-long{color:var(--blue)}.dir-short{color:var(--orange)}.empty{background:#102638;color:var(--muted)}.tag{border-color:#31576a;background:#10333d;color:#8ce5dd}.mini{border-color:var(--line);background:#102638}.positive{color:var(--green)!important}.negative{color:var(--red)!important}
'''


def render_html(embedded: str, dark: bool = False) -> str:
    html = HTML.replace("__DATA__", embedded)
    if dark:
        html = html.replace("<title>年間トレードビジュアルレビュー</title>", "<title>年間トレードビジュアルレビュー｜ダークモード</title>")
        html = html.replace("<div class=\"eyebrow\">TRADE DAILY / VISUAL REVIEW</div>", "<div class=\"eyebrow\">TRADE DAILY / VISUAL REVIEW / DARK</div>")
        html = html.replace("</style>", DARK_CSS + "</style>", 1)
    return html


def main() -> None:
    data = build_data()
    embedded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    LIGHT_OUTPUT.write_text(render_html(embedded), encoding="utf-8")
    DARK_OUTPUT.write_text(render_html(embedded, dark=True), encoding="utf-8")
    meta = data["meta"]
    print(
        f"generated {LIGHT_OUTPUT} and {DARK_OUTPUT} | {meta['startDate']}–{meta['endDate']} | "
        f"{meta['fileCount']} files / {meta['closedPairs']} pairs / "
        f"{sum(row['pnl'] for row in data['daily']):,.0f} JPY"
    )


if __name__ == "__main__":
    main()

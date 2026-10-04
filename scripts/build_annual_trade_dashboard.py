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
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSV_DIR = ROOT / "2026 Daily" / "trade_data_2025_2026"
OUTPUT = ROOT / "2026 Daily" / "annual_trade_dashboard.html"


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
    }


HTML = r'''<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>年間トレードビジュアルレビュー</title>
<style>
:root{--ink:#18324a;--muted:#6b7b8d;--line:#dce6ef;--bg:#f3f7fa;--card:#fff;--teal:#117d76;--blue:#2a79b8;--orange:#ec7b1b;--red:#c84a45;--green:#1a9662;--soft:#eef5f7;--shadow:0 7px 22px rgba(26,57,84,.08)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif}main{max-width:1500px;margin:22px auto;padding:0 18px}.hero,.panel,.metric{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow)}.hero{padding:25px 27px;margin-bottom:15px}.eyebrow{color:var(--teal);font-weight:800;letter-spacing:.12em;font-size:12px}.hero h1{font-size:30px;line-height:1.25;margin:8px 0 7px;letter-spacing:.01em}.hero p{margin:4px 0;color:var(--muted);max-width:920px}.toolbar{display:flex;gap:10px;align-items:end;flex-wrap:wrap;margin-top:20px}.control{display:flex;flex-direction:column;gap:5px;color:var(--muted);font-size:12px;font-weight:700;min-width:190px}.control.wide{min-width:300px;flex:1}.control input,.control select{height:42px;border:1px solid #cbd8e3;border-radius:10px;background:#fff;color:var(--ink);padding:0 12px;font:inherit;font-size:14px}.button{height:42px;border:1px solid #c5d5e3;border-radius:10px;background:#fff;color:var(--ink);padding:0 16px;font-weight:700;cursor:pointer}.button:hover{border-color:var(--teal);color:var(--teal)}.filter-state{margin-top:12px;color:var(--muted);min-height:22px}.filter-state strong{color:var(--ink)}.cards{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:11px;margin-bottom:15px}.metric{padding:15px 17px}.metric span{display:block;color:var(--muted);font-size:12px}.metric strong{display:block;font-size:25px;margin-top:3px;letter-spacing:.01em}.metric small{display:block;color:var(--muted);margin-top:3px}.positive{color:var(--green)!important}.negative{color:var(--red)!important}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px;margin-bottom:15px}.panel{padding:19px 20px;min-width:0}.panel h2{font-size:18px;line-height:1.3;margin:0 0 3px}.panel h2:before{content:"";display:inline-block;width:5px;height:20px;background:var(--teal);border-radius:5px;vertical-align:-4px;margin-right:9px}.panel .sub{margin:0 0 10px;color:var(--muted);font-size:12px}.chart-wrap{overflow:hidden;min-height:290px}.chart{width:100%;height:auto;display:block}.axis{font-size:11px;fill:#6e7d8c}.gridline{stroke:#e4ebf0;stroke-width:1}.zero{stroke:#9eb0be;stroke-width:1.4}.tip{font-size:11px;fill:#496073}.legend{font-size:12px;color:var(--muted);margin-top:7px}.swatch{display:inline-block;width:19px;height:10px;border-radius:3px;vertical-align:-1px;margin-right:4px}.table-wrap{overflow:auto;max-height:530px;border:1px solid #e5ecf1;border-radius:10px}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;white-space:nowrap}th,td{padding:8px 10px;border-bottom:1px solid #edf1f4;text-align:right}th:first-child,td:first-child{text-align:left}thead th{position:sticky;top:0;background:#f4f8fa;color:#52687c;font-size:12px}tbody tr:hover{background:#f6fafb}.dir-long{color:var(--blue);font-weight:700}.dir-short{color:#9b5c22;font-weight:700}.right{text-align:right}.empty{padding:28px;text-align:center;color:var(--muted);background:#f7fafb;border-radius:10px}.note{color:var(--muted);font-size:12px;margin:10px 0 0}.tag{display:inline-flex;align-items:center;border:1px solid #cce1e5;background:#f0f8f8;color:#176b68;border-radius:999px;padding:3px 9px;font-size:12px;font-weight:700;margin:0 5px 5px 0}.footer{color:var(--muted);font-size:12px;padding:5px 4px 35px}.summary-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}.mini{border:1px solid var(--line);border-radius:10px;background:#f9fbfc;padding:9px 12px}.mini b{display:block;font-size:16px}.rank-label{font-size:12px;fill:#29455c}.rank-value{font-size:11px;fill:#5d7081}.bar-pos{fill:#1a9662}.bar-neg{fill:#d0635b}.bar-neutral{fill:#9ab2c0}.bar-time{fill:#3789a9}.bar-time-neg{fill:#d0635b}@media(max-width:1080px){.cards{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:720px){main{padding:0 10px;margin:10px auto}.hero,.panel{padding:15px}.hero h1{font-size:24px}.cards{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.metric{padding:12px}.metric strong{font-size:20px}.grid{grid-template-columns:1fr;gap:10px}.control,.control.wide{min-width:100%;flex:1}.toolbar .button{width:100%}.chart-wrap{min-height:240px}.panel h2{font-size:17px}}
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
  </header>

  <section class="cards" id="kpis" aria-label="集計サマリー"></section>

  <section class="grid">
    <article class="panel"><h2>累積損益の推移</h2><p class="sub">絞り込み後の決済損益を日付順に積み上げています。</p><div class="chart-wrap" id="cumulativeChart"></div><div class="legend"><span class="swatch" style="background:#117d76"></span>累積損益　<span class="swatch" style="background:#9eb0be"></span>ゼロライン</div></article>
    <article class="panel"><h2>日別損益</h2><p class="sub">プラスの日とマイナスの日を色分けしています。</p><div class="chart-wrap" id="dailyChart"></div><div class="legend"><span class="swatch" style="background:#1a9662"></span>プラス　<span class="swatch" style="background:#d0635b"></span>マイナス</div></article>
    <article class="panel"><h2>銘柄別の損益ランキング</h2><p class="sub">検索時は該当銘柄だけを表示します。未検索時は上位／下位を表示。</p><div class="chart-wrap" id="symbolChart"></div></article>
    <article class="panel"><h2>エントリー時刻別の傾向</h2><p class="sub">エントリー時刻を30分ごとにまとめ、損益と件数を表示しています。</p><div class="chart-wrap" id="timeChart"></div></article>
  </section>

  <section class="panel" style="margin-bottom:15px"><h2>銘柄別サマリー</h2><p class="sub">銘柄検索後は、その銘柄のロング／ショート別の実績を確認できます。</p><div id="symbolSummary"></div></section>

  <section class="panel" style="margin-bottom:15px"><h2>エントリー・エグジット一覧</h2><p class="sub">CSVの新規約定と返済約定をFIFOで対応させた実現損益です。分割決済は複数行になります。</p><div id="tradeSummary"></div><div class="table-wrap" id="tradeTable"></div></section>

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
function renderSymbolBars(rows){
  if(!rows.length) return noData('該当する銘柄がありません。');
  const sorted=[...rows].sort((a,b)=>Number(b.pnl)-Number(a.pnl));
  const chosen=sorted.length<=14?sorted:[...sorted.slice(0,7),...sorted.slice(-7)];
  const W=760,H=Math.max(285,chosen.length*25+35),L=185,R=75,T=15,B=15;
  const maxAbs=niceMax(chosen.map(r=>r.pnl)); let body='';
  const zero=scale(0,-maxAbs,maxAbs,L,W-R);
  body += `<line class="zero" x1="${zero}" y1="${T}" x2="${zero}" y2="${H-B}"/>`;
  chosen.forEach((row,i)=>{const y=T+i*25+4;const x=scale(row.pnl,-maxAbs,maxAbs,L,W-R);const left=Math.min(zero,x);const width=Math.max(1,Math.abs(x-zero));const label=escapeHtml(`${row.name}（${row.code}）`);body+=`<text class="rank-label" x="${L-10}" y="${y+13}" text-anchor="end">${label}</text><rect x="${left}" y="${y}" width="${width}" height="16" rx="4" class="${row.pnl>0?'bar-pos':row.pnl<0?'bar-neg':'bar-neutral'}"><title>${label} ${fmtYen(row.pnl)}</title></rect><text class="rank-value" x="${row.pnl>=0?left+width+6:left-6}" y="${y+13}" text-anchor="${row.pnl>=0?'start':'end'}">${fmtYen(row.pnl)}</text>`;});
  return svgShell(W,H,body);
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
function renderTrades(trades){
  if(!trades.length){$('tradeSummary').innerHTML='';$('tradeTable').innerHTML=noData('該当するエントリー／エグジットがありません。');return;}
  const ordered=[...trades].sort((a,b)=>`${b.date}${b.exitMinute}`.localeCompare(`${a.date}${a.exitMinute}`));const limit=500;const shown=ordered.slice(0,limit);$('tradeSummary').innerHTML=`<div class="summary-row"><div class="mini"><span>表示</span><b>${fmtInt(shown.length)} / ${fmtInt(ordered.length)}件</b></div><div class="mini"><span>対象期間</span><b>${fmtDate(ordered[ordered.length-1].date)}〜${fmtDate(ordered[0].date)}</b></div></div>`;$('tradeTable').innerHTML=`<table><thead><tr><th>日付</th><th>銘柄</th><th>方向</th><th>エントリー</th><th>エグジット</th><th>数量</th><th>エントリー値</th><th>エグジット値</th><th>損益</th><th>保有</th></tr></thead><tbody>${shown.map(row=>`<tr><td>${fmtDate(row.date)}</td><td>${escapeHtml(shortName(row.name,row.code))}</td><td class="${row.direction==='long'?'dir-long':'dir-short'}">${row.direction==='long'?'ロング':'ショート'}</td><td>${escapeHtml(row.entryTime)}</td><td>${escapeHtml(row.exitTime)}</td><td>${fmtInt(row.quantity)}</td><td>${yen(row.entryPrice)}</td><td>${yen(row.exitPrice)}</td><td class="${signClass(row.pnl)}">${fmtYen(row.pnl)}</td><td>${fmtInt(row.holdMinutes)}分</td></tr>`).join('')}</tbody></table>`;
}
function update(){
  const query=$('symbolSearch').value.trim().toLocaleLowerCase('ja-JP');const period=$('periodSelect').value;const direction=$('directionSelect').value;
  const trades=DATA.trades.filter(row=>matchesPeriod(row.date,period)&&(!query||`${row.code} ${row.name}`.toLocaleLowerCase('ja-JP').includes(query))&&(direction==='all'||row.direction===direction));
  const summary=aggregate(trades);renderKpis(summary,trades);$('cumulativeChart').innerHTML=renderCumulative(summary.daily);$('dailyChart').innerHTML=renderDaily(summary.daily);$('symbolChart').innerHTML=renderSymbolBars(summary.symbols);$('timeChart').innerHTML=renderTimeBars(summary.times);renderSymbolSummary(summary.symbols);renderTrades(trades);
  const queryText=query?`銘柄検索「${escapeHtml($('symbolSearch').value.trim())}」`:'全銘柄';const periodText=period==='all'?'保存済み全期間':period.length===4?`${period}年`:`${period.slice(0,4)}年${Number(period.slice(4))}月`;const directionText=direction==='all'?'全方向':direction==='long'?'ロング':'ショート';$('filterState').innerHTML=`現在の表示：<strong>${queryText}</strong>　<span class="tag">${periodText}</span><span class="tag">${directionText}</span>　${fmtInt(trades.length)}件 / ${fmtInt(DATA.trades.length)}件`;
}
populatePeriods();
['symbolSearch','periodSelect','directionSelect'].forEach(id=>$(id).addEventListener('input',update));
$('clearButton').addEventListener('click',()=>{$('symbolSearch').value='';$('periodSelect').value='all';$('directionSelect').value='all';update()});
update();
$('sourceNote').textContent=`出典：${DATA.meta.startDate ? fmtDate(DATA.meta.startDate)+'〜'+fmtDate(DATA.meta.endDate) : '—'} に保存された松井証券CSV ${fmtInt(DATA.meta.fileCount)}日分。実行約定 ${fmtInt(DATA.meta.executionFills)}行（新規 ${fmtInt(DATA.meta.entryFills)}行／返済 ${fmtInt(DATA.meta.exitFills)}行）を読み込み、新規と返済を銘柄別・方向別にFIFO対応しました。未対応の建玉残は ${fmtInt(DATA.meta.openShares)}株、返済先を対応できなかった数量は ${fmtInt(DATA.meta.unmatchedExitShares)}株です。数値はCSVの約定単価と約定数から計算した実現損益で、手数料・税金は含みません。`;
</script>
</body>
</html>
'''


def main() -> None:
    data = build_data()
    embedded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    OUTPUT.write_text(HTML.replace("__DATA__", embedded), encoding="utf-8")
    meta = data["meta"]
    print(
        f"generated {OUTPUT} | {meta['startDate']}–{meta['endDate']} | "
        f"{meta['fileCount']} files / {meta['closedPairs']} pairs / "
        f"{sum(row['pnl'] for row in data['daily']):,.0f} JPY"
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Collect REAL retail positioning (long %/short %) from forexsentiment.live.

Sources (both public, no key):
  1. https://forexsentiment.live/detail/<SYMBOL>
     -> <script class="fxc-data"> JSON embedded in the HTML:
        "sentiment": {"YYYY-MM-DD": long_pct, ...}  (site's daily series, since 2025-05-12)
        "daily": [{t,o,h,l,c,(f)}]                   (daily candles, close 17:00 New York)
  2. https://forexsentiment.live/api/chart-bars.php?symbol=<SYMBOL>&tf=H1[&before=<unix>]
     -> H1 bars; bars with rk=="i" carry r (long %) and ra (unix time of the 3-hourly read).
        3-hourly reads only exist from 2026-10-02 03:00 UTC onward.

Outputs (upsert, no duplicates; re-running refreshes provisional rows):
  <SYM>_retail_raw.csv        every 3-hourly read (datetime_utc key)
  <SYM>_retail_daily.csv      site daily series (date_utc key)
  <SYM>_retail_0000utc.csv    the 00:00 UTC 3-hourly read per day (from raw)
  <SYM>_retail_daily.md       Markdown table + monthly summary
  raw_responses/              raw JSON dumps (only with --debug)

Files are read/written next to this script (Path(__file__).parent) regardless of cwd,
unless --outdir is given. The Markdown is regenerated from the CSV on every run.

Usage: python3 collect_forexsentiment.py [--symbol XAUUSD] [--outdir DIR] [--prefix GOLD] [--debug]
"""
import argparse, csv, json, os, re, sys, time, urllib.request
from datetime import datetime, timezone, timedelta, date
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
BASE = "https://forexsentiment.live"
JST = timezone(timedelta(hours=9))
DAILY_COLS = ["date_utc", "time_utc", "datetime_jst", "long_pct", "short_pct",
              "price_if_any", "source", "status", "notes"]
RAW_COLS = ["datetime_utc", "datetime_jst", "long_pct", "short_pct",
            "price_if_any", "source"]


def get(url, referer=None):
    h = {"User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}
    if referer:
        h["Referer"] = referer
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=40) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status} for {url}")
        return r.read().decode("utf-8")


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc)


def read_csv(path, key):
    rows = OrderedDict()
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r[key]] = r
    return rows


def write_csv(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for k in sorted(rows):
            w.writerow(rows[k])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--prefix", default=None, help="file prefix (default GOLD for XAUUSD)")
    ap.add_argument("--outdir", default=str(HERE))
    ap.add_argument("--debug", action="store_true", help="save raw JSON responses to raw_responses/")
    ap.add_argument("--max-pages", type=int, default=6)
    a = ap.parse_args()
    sym = a.symbol.upper()
    pre = a.prefix or ("GOLD" if sym == "XAUUSD" else sym)
    if a.debug:
        os.makedirs(os.path.join(a.outdir, "raw_responses"), exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    P = lambda n: os.path.join(a.outdir, n)

    # ---- 1. detail page with embedded JSON ----
    page_url = f"{BASE}/detail/{sym}"
    html = get(page_url)
    m = re.search(r'<script[^>]*class="fxc-data"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        sys.exit(f"fxc-data JSON not found in {page_url} (page layout changed?)")
    d = json.loads(m.group(1))
    if a.debug:
        with open(P(f"raw_responses/{sym}_detail_fxc_{stamp}.json"), "w") as f:
            json.dump(d, f)
    sentiment = d["sentiment"]
    candles = {c["t"]: c for c in d["daily"]}
    print(f"detail page: {len(sentiment)} daily values {d['sentiment_first']}..{d['sentiment_last']}"
          f" (site count {d.get('sentiment_count')}), latest {d.get('sentiment_now')}% "
          f"updated {utc(d['sentiment_updated']).isoformat()}")

    # ---- 2. H1 bars with 3-hourly reads (paginate back until no reads or already stored) ----
    raw = read_csv(P(f"{pre}_retail_raw.csv"), "datetime_utc")
    newest_stored = max(raw) if raw else None
    bars, before = [], None
    api = f"{BASE}/api/chart-bars.php?symbol={sym}&tf=H1"
    for page in range(a.max_pages):
        u = api + (f"&before={before}" if before else "")
        j = json.loads(get(u, referer=page_url))
        if a.debug:
            with open(P(f"raw_responses/{sym}_H1_{stamp}_p{page}.json"), "w") as f:
                json.dump(j, f)
        b = j.get("bars") or []
        bars = b + bars
        has_reads = any(x.get("rk") == "i" for x in b)
        oldest = utc(b[0]["t"]).strftime("%Y-%m-%d %H:%M:%S") if b else None
        print(f"H1 page {page}: {len(b)} bars from {oldest}, reads in page: "
              f"{sum(1 for x in b if x.get('rk') == 'i')}")
        if not b or not j.get("has_more") or not has_reads:
            break
        if newest_stored and oldest and oldest < newest_stored:
            break
        before = b[0]["t"]
        time.sleep(2)

    reads = {}
    for x in bars:  # bar whose hour contains the read -> its open ~ price at read time
        if x.get("rk") == "i" and "ra" in x:
            ra = int(x["ra"])
            if x["t"] <= ra < x["t"] + 3600 or ra not in reads:
                reads[ra] = (int(x["r"]), x["o"] if x["t"] <= ra < x["t"] + 3600 else "")
    for ra, (r, px) in reads.items():
        dt = utc(ra)
        k = dt.strftime("%Y-%m-%d %H:%M:%S")
        raw[k] = {"datetime_utc": k, "datetime_jst": dt.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S"),
                  "long_pct": r, "short_pct": 100 - r, "price_if_any": px,
                  "source": "forexsentiment.live /api/chart-bars.php tf=H1 (r, ra); price=open of H1 bar containing read"}
    write_csv(P(f"{pre}_retail_raw.csv"), RAW_COLS, raw)

    # ---- 3. daily series ----
    daily = read_csv(P(f"{pre}_retail_daily.csv"), "date_utc")
    raw_by_day = {}
    for k, r in raw.items():
        raw_by_day.setdefault(k[:10], []).append(r)
    last_day = d["sentiment_last"]
    for day, v in sentiment.items():
        v = int(v)
        c = candles.get(day)
        notes = []
        price = ""
        if c:
            price = c["c"]
            notes.append("price=daily close (17:00 NY) of candle " + day + (" [forming]" if c.get("f") else ""))
        else:
            notes.append("no daily candle for this date (weekend/holiday)")
        time_utc, dtj = "", ""
        rs = sorted(raw_by_day.get(day, []), key=lambda r: r["datetime_utc"])
        if rs and int(rs[-1]["long_pct"]) == v:
            time_utc = rs[-1]["datetime_utc"][11:]
            dtj = rs[-1]["datetime_jst"]
            notes.append("matches last 3-hourly read of the UTC date")
        elif rs:
            notes.append(f"differs from last captured read ({rs[-1]['datetime_utc'][11:16]} UTC={rs[-1]['long_pct']}); "
                         "likely a later read not exposed in H1 bars")
        else:
            notes.append("snapshot time not published by site")
        status = "provisional" if day == last_day else "final"
        daily[day] = {"date_utc": day, "time_utc": time_utc, "datetime_jst": dtj, "long_pct": v,
                      "short_pct": 100 - v, "price_if_any": price,
                      "source": "forexsentiment.live/detail/" + sym + " embedded fxc-data.sentiment",
                      "status": status, "notes": "; ".join(notes)}
    write_csv(P(f"{pre}_retail_daily.csv"), DAILY_COLS, daily)

    # ---- 4. 00:00 UTC read per day ----
    z = OrderedDict()
    for k in sorted(raw):
        if k[11:13] == "00":
            r = raw[k]
            z[k[:10]] = {"date_utc": k[:10], "time_utc": k[11:], "datetime_jst": r["datetime_jst"],
                         "long_pct": r["long_pct"], "short_pct": r["short_pct"],
                         "price_if_any": r["price_if_any"], "source": r["source"]}
    write_csv(P(f"{pre}_retail_0000utc.csv"), DAILY_COLS[:7], z)

    # ---- 5. Markdown ----
    months = OrderedDict()
    for day in sorted(daily):
        months.setdefault(day[:7], []).append(int(daily[day]["long_pct"]))
    L = [f"# {pre} ({sym}) 個人投資家ポジション（日次）— forexsentiment.live", "",
         f"取得: {datetime.now(JST).strftime('%Y-%m-%d %H:%M')} JST / 日数 {len(daily)} / "
         f"期間 {min(daily)} 〜 {max(daily)} (UTC日付)", "",
         "値はサイト埋め込みJSON (fxc-data.sentiment) の実データ。最新日は暫定値（当日中に更新される）。", "",
         "## 月次サマリー", "", "| 月 | 日数 | 平均ロング% | 最小 | 最大 |", "|---|---|---|---|---|"]
    for mo, vs in months.items():
        L.append(f"| {mo} | {len(vs)} | {sum(vs)/len(vs):.1f} | {min(vs)} | {max(vs)} |")
    L += ["", "## 日次データ（新しい順）", "",
          "| 日付(UTC) | 時刻(UTC) | 日時(JST) | ロング% | ショート% | 終値(17:00 NY) | 状態 |",
          "|---|---|---|---|---|---|---|"]
    for day in sorted(daily, reverse=True):
        r = daily[day]
        L.append(f"| {day} | {r['time_utc'] or '—'} | {r['datetime_jst'] or '—'} | {r['long_pct']} | "
                 f"{r['short_pct']} | {r['price_if_any'] or '—'} | {'暫定' if r['status']=='provisional' else '確定'} |")
    with open(P(f"{pre}_retail_daily.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"daily rows: {len(daily)}, raw reads: {len(raw)}, 00:00 rows: {len(z)}")


if __name__ == "__main__":
    main()

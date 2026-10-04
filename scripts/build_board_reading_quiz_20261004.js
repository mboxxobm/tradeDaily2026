const fs = require('fs');
const path = require('path');
const zlib = require('zlib');
const readline = require('readline');

const REPO_DIR = path.resolve(__dirname, '..');
const SOURCE_ROOT = process.env.BOARD_SOURCE_ROOT || '/Volumes/1000GB20260826/2026BC_imac3/板読みTools';
const DIARY_MANIFEST = process.env.TRADE_DIARY_MANIFEST || '/Users/th/Documents/TradeDayReport20260821/outputs/full_day_312_trade_charts_202605_202608/manifest.json';
const OUT_DIR = path.join(REPO_DIR, '2026 Daily', 'board_reading_quiz_20261004');
const ASSET_DIR = path.join(OUT_DIR, 'assets');
const DATA_DIR = path.join(OUT_DIR, 'data');

const CASE_SPECS = [
  {
    id: 'B001', date: '20260828', code: '7974', name: '任天堂', direction: 'LONG',
    entryTime: '09:15:00', sourceDirs: ['20260828fri'],
    sourceFiles: [
      'watchlist_1321-285A-7203-6857-9984-6976-6981-5803-7974-5801-4062_20260828_084121.jsonl.gz',
      'watchlist_1321-285A-7203-6857-9984-6976-6981-5803-7974-5801-4062_20260828_122502.jsonl.gz',
    ],
  },
  {
    id: 'B002', date: '20260819', code: '6981', name: '村田製作所', direction: 'SHORT',
    entryTime: '09:14:00', sourceDirs: ['20260819'],
    sourceFiles: [
      'watchlist_1321-285A-7203-6857-9984-6976-6981-5803-7974-5801-4062_20260819_085459.jsonl.gz',
      'watchlist_1321-285A-7203-6857-9984-6976-6981-5803-7974-5801-4062_20260819_122503.jsonl.gz',
    ],
  },
  {
    id: 'B003', date: '20260824', code: '6981', name: '村田製作所', direction: 'SHORT',
    entryTime: '09:43:00', sourceDirs: ['20260824mon'],
    sourceFiles: [
      'watchlist_1321-285A-7203-6857-9984-6976-6981-5803-7974-5801-4062_20260824_085228.jsonl.gz',
      'watchlist_1321-285A-7203-6857-9984-6976-6981-5803-7974-5801-4062_20260824_122502.jsonl.gz',
    ],
  },
];

const diary = JSON.parse(fs.readFileSync(DIARY_MANIFEST, 'utf8'));
const number = (value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};
const pad = (value) => String(value).padStart(2, '0');
const dateDisplay = (date) => `${date.slice(0, 4)}/${date.slice(4, 6)}/${date.slice(6, 8)}`;
const timeDisplay = (value) => String(value || '').slice(0, 8);
const dateTime = (date, time) => `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}T${timeDisplay(time)}+09:00`;
const epoch = (value) => {
  if (!value) return NaN;
  const text = String(value);
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(text) ? text : `${text}+09:00`;
  const result = Date.parse(normalized);
  return Number.isFinite(result) ? result : NaN;
};
const fmtPrice = (value) => {
  if (!Number.isFinite(value)) return '—';
  return Number(value).toLocaleString('ja-JP', { maximumFractionDigits: 3 });
};
const fmtQty = (value) => Number(value || 0).toLocaleString('ja-JP', { maximumFractionDigits: 0 });
const fmtYen = (value) => `${Number(value) >= 0 ? '+' : '−'}${Math.abs(Number(value) || 0).toLocaleString('ja-JP')}円`;
const escXml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' }[char]));
const escHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));

function findDiaryTrade(spec) {
  const group = diary.groups.find((item) => String(item.date).replaceAll('-', '') === spec.date && String(item.code) === spec.code);
  if (!group) throw new Error(`日誌manifestに ${spec.date} ${spec.code} がありません`);
  const trade = (group.trades || []).find((item) => item.direction === spec.direction && Number(item.actual_pnl) > 0 && (!spec.entryTime || timeDisplay(item.entry_time) === spec.entryTime));
  if (!trade) throw new Error(`日誌manifestにプラス約定 ${spec.date} ${spec.code} ${spec.direction} がありません`);
  return { group, trade };
}

function getLevel(raw, side, level) {
  const row = raw?.[`${side}${level}`];
  if (!row || number(row.Price) === null) return null;
  return {
    price: number(row.Price),
    qty: number(row.Qty) || 0,
    level,
    sign: row.Sign || '',
    time: row.Time || '',
  };
}

function normalizeRecord(record) {
  const compact = record.compact || {};
  const raw = record.raw || {};
  const time = compact.current_price_time || raw.CurrentPriceTime || record.observed_at;
  const t = epoch(time);
  const price = number(compact.current_price ?? raw.CurrentPrice ?? raw.CurrentPriceValue);
  const sell = Array.from({ length: 10 }, (_, index) => getLevel(raw, 'Sell', index + 1)).filter(Boolean);
  const buy = Array.from({ length: 10 }, (_, index) => getLevel(raw, 'Buy', index + 1)).filter(Boolean);
  return {
    t,
    observedT: epoch(record.observed_at),
    observedAt: record.observed_at || '',
    currentPriceTime: time || '',
    price,
    volume: number(compact.trading_volume ?? raw.TradingVolume),
    vwap: number(raw.VWAP),
    sell,
    buy,
  };
}

async function readBoardFile(filePath, symbol) {
  const points = [];
  const snapshots = [];
  const input = fs.createReadStream(filePath).pipe(zlib.createGunzip());
  const lines = readline.createInterface({ input, crlfDelay: Infinity });
  for await (const line of lines) {
    if (!line || !line.includes(`"symbol":"${symbol}"`)) continue;
    let record;
    try {
      record = JSON.parse(line);
    } catch (_error) {
      continue;
    }
    if (String(record.symbol || record.compact?.symbol || record.raw?.Symbol || '') !== symbol) continue;
    const normalized = normalizeRecord(record);
    if (!Number.isFinite(normalized.t)) continue;
    if (normalized.price !== null) points.push(normalized);
    if (normalized.sell.length || normalized.buy.length) snapshots.push(normalized);
  }
  return { points, snapshots };
}

function mergeRecords(chunks) {
  const points = chunks.flatMap((chunk) => chunk.points).sort((a, b) => a.t - b.t);
  const snapshots = chunks.flatMap((chunk) => chunk.snapshots).sort((a, b) => a.t - b.t);
  return { points, snapshots };
}

function pickSnapshot(snapshots, targetEpoch) {
  if (!snapshots.length) return null;
  let best = snapshots[0];
  const snapshotTime = (snapshot) => Number.isFinite(snapshot.observedT) ? snapshot.observedT : snapshot.t;
  let bestDistance = Math.abs(snapshotTime(best) - targetEpoch);
  for (const snapshot of snapshots) {
    const time = snapshotTime(snapshot);
    const bestTime = snapshotTime(best);
    const distance = Math.abs(time - targetEpoch);
    if (distance < bestDistance || (distance === bestDistance && time <= targetEpoch && bestTime > targetEpoch)) {
      best = snapshot;
      bestDistance = distance;
    }
  }
  return best;
}

function aggregateBars(points, startEpoch, endEpoch) {
  const bars = new Map();
  const barMs = 15 * 1000;
  const sorted = [...points].filter((point) => Number.isFinite(point.t) && Number.isFinite(point.price)).sort((a, b) => a.t - b.t);
  let previous = null;
  for (const point of sorted) {
    let volumeDelta = Number.isFinite(point.volume) && !previous ? point.volume : 0;
    if (previous && Number.isFinite(point.volume) && Number.isFinite(previous.volume)) {
      volumeDelta = point.volume >= previous.volume ? point.volume - previous.volume : point.volume;
    }
    const priceDelta = previous ? Math.sign(point.price - previous.price) : 0;
    previous = point;
    if (point.t < startEpoch || point.t > endEpoch) continue;
    const bucket = startEpoch + Math.floor((point.t - startEpoch) / barMs) * barMs;
    const existing = bars.get(bucket);
    if (!existing) {
      bars.set(bucket, { t: bucket, open: point.price, high: point.price, low: point.price, close: point.price, vwap: point.vwap, volume: volumeDelta, delta: volumeDelta * priceDelta, ticks: 1 });
      continue;
    }
    existing.high = Math.max(existing.high, point.price);
    existing.low = Math.min(existing.low, point.price);
    existing.close = point.price;
    if (Number.isFinite(point.vwap)) existing.vwap = point.vwap;
    existing.volume += volumeDelta;
    existing.delta += volumeDelta * priceDelta;
    existing.ticks += 1;
  }
  const result = [...bars.values()].sort((a, b) => a.t - b.t);
  for (let index = 0; index < result.length; index += 1) {
    const prior = result.slice(Math.max(0, index - 30), index).map((bar) => bar.volume);
    result[index].rvol = prior.length === 30 ? result[index].volume / (prior.reduce((sum, value) => sum + value, 0) / prior.length || 1) : null;
    const lsmaWindow = result.slice(Math.max(0, index - 49), index + 1).map((bar) => bar.volume);
    const stdevWindow = result.slice(Math.max(0, index - 20), index + 1).map((bar) => bar.volume);
    const n = lsmaWindow.length;
    const sumY = lsmaWindow.reduce((sum, value) => sum + value, 0);
    const sumXY = lsmaWindow.reduce((sum, value, at) => sum + value * at, 0);
    const sumX = n * (n - 1) / 2;
    const sumX2 = n * (n - 1) * (2 * n - 1) / 6;
    const slope = n > 1 ? (n * sumXY - sumX * sumY) / (n * sumX2 - sumX * sumX || 1) : 0;
    const intercept = n ? (sumY - slope * sumX) / n : 0;
    const lsma50 = intercept + slope * (n - 1);
    const mean = stdevWindow.reduce((sum, value) => sum + value, 0) / Math.max(1, stdevWindow.length);
    const stdev21 = Math.sqrt(stdevWindow.reduce((sum, value) => sum + ((value - mean) ** 2), 0) / Math.max(1, stdevWindow.length));
    result[index].largeVolume = n === 50 && stdevWindow.length === 21 && (result[index].volume - lsma50) > stdev21;
  }
  return result;
}

function roundInfo(price) {
  const step = price >= 10000 ? 500 : price >= 3000 ? 100 : 50;
  const level = Math.round(price / step) * step;
  return { step, level, distance: price - level };
}

function boardMetrics(snapshot) {
  const sell = snapshot?.sell || [];
  const buy = snapshot?.buy || [];
  const sell5 = sell.slice(0, 5).reduce((sum, row) => sum + row.qty, 0);
  const buy5 = buy.slice(0, 5).reduce((sum, row) => sum + row.qty, 0);
  const bestAsk = sell[0]?.price ?? null;
  const bestBid = buy[0]?.price ?? null;
  const spread = bestAsk !== null && bestBid !== null ? bestAsk - bestBid : null;
  const total = buy5 + sell5;
  const imbalance = total ? (buy5 - sell5) / total : null;
  return { bestAsk, bestBid, spread, buy5, sell5, imbalance };
}

function snapshotForOutput(snapshot) {
  if (!snapshot) return null;
  return {
    observedAt: snapshot.observedAt,
    currentPriceTime: snapshot.currentPriceTime,
    price: snapshot.price,
    vwap: snapshot.vwap,
    volume: snapshot.volume,
    sell: snapshot.sell,
    buy: snapshot.buy,
    metrics: boardMetrics(snapshot),
  };
}

function buildBeforeAnalysis(bars, snapshot, entryPrice) {
  const valid = bars.filter((bar) => Number.isFinite(bar.close));
  if (!valid.length) return [];
  const first = valid[0];
  const last = valid.at(-1);
  const high = Math.max(...valid.map((bar) => bar.high));
  const low = Math.min(...valid.map((bar) => bar.low));
  const priceSpan = high - low;
  const openToEntry = last.close - first.open;
  const openToEntryPct = first.open ? openToEntry / first.open * 100 : 0;
  const vwapGap = Number.isFinite(snapshot.vwap) ? entryPrice - snapshot.vwap : null;
  const vwapGapPct = Number.isFinite(snapshot.vwap) && snapshot.vwap ? vwapGap / snapshot.vwap * 100 : null;
  const rangePosition = priceSpan ? (entryPrice - low) / priceSpan * 100 : 50;
  const jstDate = new Date(first.t + 9 * 60 * 60 * 1000);
  const dayStart = Date.UTC(jstDate.getUTCFullYear(), jstDate.getUTCMonth(), jstDate.getUTCDate()) - 9 * 60 * 60 * 1000;
  const marketOpen = dayStart + 9 * 60 * 60 * 1000;
  const fiveMinuteBars = valid.filter((bar) => bar.t < marketOpen + 5 * 60 * 1000);
  const openingHigh = fiveMinuteBars.length ? Math.max(...fiveMinuteBars.map((bar) => bar.high)) : null;
  const openingLow = fiveMinuteBars.length ? Math.min(...fiveMinuteBars.map((bar) => bar.low)) : null;
  const recent = valid.slice(-5);
  const recentUp = recent.filter((bar) => bar.close > bar.open).length;
  const recentDown = recent.filter((bar) => bar.close < bar.open).length;
  const recentVolume = recent.reduce((sum, bar) => sum + bar.volume, 0);
  const recentDelta = recent.reduce((sum, bar) => sum + bar.delta, 0);
  const recentRvol = last.rvol === null ? '—（30本分の比較データなし）' : `${last.rvol.toFixed(1)}x`;
  const m = snapshot.metrics;
  const valuePosition = priceSpan ? `${rangePosition.toFixed(0)}%` : '—';
  const signedYen = (value) => `${value >= 0 ? '+' : '−'}${fmtPrice(Math.abs(value))}円`;
  const signedPct = (value) => `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(2)}%`;
  return [
    { label: '寄り付きからENTRY', value: `${signedYen(openToEntry)}（${signedPct(openToEntryPct)}）`, detail: `09:00最初の価格 ${fmtPrice(first.open)}円 → ENTRY直前 ${fmtPrice(last.close)}円` },
    { label: 'ENTRYとVWAP', value: vwapGap === null ? '—' : `${vwapGap >= 0 ? 'VWAP上' : 'VWAP下'} ${signedYen(vwapGap)}`, detail: `ENTRY ${fmtPrice(entryPrice)}円 ／ VWAP ${fmtPrice(snapshot.vwap)}円${vwapGapPct === null ? '' : `（${signedPct(vwapGapPct)}）`}` },
    { label: 'ENTRY前の高値・安値', value: `${fmtPrice(low)}〜${fmtPrice(high)}円`, detail: `ENTRY価格の位置 ${valuePosition}（100%超はレンジ高値より上）` },
    { label: '寄り後5分の値幅', value: openingHigh === null ? '—' : `${fmtPrice(openingLow)}〜${fmtPrice(openingHigh)}円`, detail: '09:00〜09:05に記録された高値・安値' },
    { label: '直近5本のローソク', value: `${recentUp}本上昇 ／ ${recentDown}本下落`, detail: `${recent.length}本の15秒足。色の偏りだけで方向は断定しない` },
    { label: '直近5本の出来高', value: `${fmtQty(recentVolume)}株`, detail: `推定Δ ${recentDelta >= 0 ? '+' : '−'}${fmtQty(Math.abs(recentDelta))}株 ／ 最終足RVOL ${recentRvol}` },
    { label: 'ENTRY時点の板需給', value: `需給差 ${m.imbalance === null ? '—' : `${m.imbalance >= 0 ? '+' : ''}${(m.imbalance * 100).toFixed(1)}%`}`, detail: `買い上位5段 ${fmtQty(m.buy5)}株 ／ 売り上位5段 ${fmtQty(m.sell5)}株` },
    { label: '最良気配とスプレッド', value: `${fmtPrice(m.bestBid)}円 ／ ${fmtPrice(m.bestAsk)}円`, detail: `買い最良値 ／ 売り最良値　スプレッド ${fmtPrice(m.spread)}円` },
  ];
}

function chartSvg(item, bars, mode) {
  const W = 1200;
  const H = 730;
  const margins = { left: 82, right: 28, top: 78, bottom: 42 };
  const priceBottom = 405;
  const volTop = 442;
  const volHeight = 62;
  const deltaTop = 523;
  const deltaHeight = 55;
  const rvolTop = 600;
  const rvolHeight = 48;
  const start = epoch(dateTime(item.date, '09:00:00'));
  const entryEpoch = epoch(dateTime(item.date, item.entryTime));
  const exitEpoch = epoch(dateTime(item.date, item.exitTime));
  const lastEpoch = bars.length ? bars.at(-1).t + 15000 : exitEpoch + 60000;
  const sessionEnd = epoch(dateTime(item.date, '15:30:00'));
  const end = mode === 'before' ? sessionEnd : Math.max(sessionEnd, lastEpoch, exitEpoch + 60000);
  const visible = bars.filter((bar) => bar.t >= start && bar.t <= end);
  const fallbackPrice = item.entryPrice;
  const lows = visible.map((bar) => bar.low).filter(Number.isFinite);
  const highs = visible.map((bar) => bar.high).filter(Number.isFinite);
  const low = (lows.length ? Math.min(...lows) : fallbackPrice) - Math.max(1, Math.abs((highs.length ? Math.max(...highs) : fallbackPrice) - (lows.length ? Math.min(...lows) : fallbackPrice)) * 0.08);
  const high = (highs.length ? Math.max(...highs) : fallbackPrice) + Math.max(1, Math.abs((highs.length ? Math.max(...highs) : fallbackPrice) - (lows.length ? Math.min(...lows) : fallbackPrice)) * 0.08);
  const plotWidth = W - margins.left - margins.right;
  const plotHeight = priceBottom - margins.top;
  const x = (time) => margins.left + ((time - start) / Math.max(1, end - start)) * plotWidth;
  const y = (price) => margins.top + ((high - price) / Math.max(0.0001, high - low)) * plotHeight;
  const barWidth = Math.max(1, Math.min(9, plotWidth / Math.max(1, visible.length) * 0.72));
  const maxVolume = Math.max(1, ...visible.map((bar) => bar.volume || 0));
  const maxDelta = Math.max(1, ...visible.map((bar) => Math.abs(bar.delta || 0)));
  const maxRvol = Math.max(2, ...visible.map((bar) => bar.rvol || 0));
  const largeBar = visible.filter((bar) => (bar.rvol || 0) >= 2 || bar.largeVolume).sort((a, b) => b.volume - a.volume)[0];
  const largeSummary = largeBar
    ? `大口候補 ${new Date(largeBar.t + 9 * 60 * 60 * 1000).toISOString().slice(11, 16)} 出来高 ${fmtQty(largeBar.volume)} / 推定Δ ${largeBar.delta >= 0 ? '+' : '−'}${fmtQty(Math.abs(largeBar.delta))} / RVOL ${largeBar.rvol.toFixed(1)}x`
    : '大口出来高の条件該当なし';
  const title = `${item.dateDisplay} ${item.code} ${item.name}｜${mode === 'before' ? 'BEFORE 9:00→引け（ENTRY以降を非表示）' : 'AFTER 板データ連動'}`;
  const lines = [];
  lines.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${escXml(title)}">`);
  lines.push('<style>text{font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Yu Gothic",Meiryo,sans-serif}.title{fill:#edf6fb;font-size:20px;font-weight:800}.sub{fill:#a7bac7;font-size:12px}.grid{stroke:#29465a;stroke-width:1}.axis{fill:#a7bac7;font-size:11px}.up{fill:#5ce09f;stroke:#5ce09f}.down{fill:#ff8b98;stroke:#ff8b98}.wick{stroke-width:1}.vwap{fill:none;stroke:#f4c95d;stroke-width:1.6;stroke-dasharray:5 4}.entry{stroke:#f59e0b;stroke-width:2;stroke-dasharray:7 5}.exit{stroke:#77e3a9;stroke-width:2;stroke-dasharray:5 4}.label{fill:#071019;font-size:11px;font-weight:800}.box{fill:#f59e0b}.exitbox{fill:#77e3a9}.panel{fill:#0d1b27;stroke:#29465a}.axisline{stroke:#607887;stroke-width:1}.delta-pos{fill:#5ce09f}.delta-neg{fill:#ff8b98}.rvol{fill:none;stroke:#71d8ef;stroke-width:2}.large{stroke:#f4c95d;stroke-width:2}.mask{fill:#071019;fill-opacity:.94}.masktext{fill:#edf6fb;font-size:17px;font-weight:800}</style>');
  lines.push('<rect width="100%" height="100%" fill="#0b1219"/>');
  lines.push(`<text x="${margins.left}" y="27" class="title">${escXml(title)}</text>`);
  lines.push(`<text x="${margins.left}" y="48" class="sub">15秒足｜黄色=VWAP｜出来高バー・推定デルタ・RVOL（直前30本平均比）　${visible.length}本　｜　${escXml(largeSummary)}</text>`);
  for (let i = 0; i <= 5; i += 1) {
    const price = low + (high - low) * (i / 5);
    const yy = y(price);
    lines.push(`<line x1="${margins.left}" y1="${yy.toFixed(2)}" x2="${W - margins.right}" y2="${yy.toFixed(2)}" class="grid"/>`);
    lines.push(`<text x="${margins.left - 8}" y="${(yy + 4).toFixed(2)}" text-anchor="end" class="axis">${escXml(fmtPrice(price))}</text>`);
  }
  for (const [top, height, name] of [[volTop, volHeight, '出来高'], [deltaTop, deltaHeight, '推定Δ'], [rvolTop, rvolHeight, 'RVOL']]) {
    lines.push(`<rect x="${margins.left}" y="${top}" width="${plotWidth}" height="${height}" class="panel"/>`);
    lines.push(`<text x="${margins.left - 8}" y="${top + 13}" text-anchor="end" class="axis">${name}</text>`);
  }
  const tickCount = 8;
  for (let i = 0; i <= tickCount; i += 1) {
    const time = start + (end - start) * (i / tickCount);
    const xx = x(time);
    const d = new Date(time);
    const label = `${pad(d.getUTCHours() + 9 > 23 ? d.getUTCHours() - 15 : d.getUTCHours() + 9)}:${pad(d.getUTCMinutes())}`;
    lines.push(`<line x1="${xx.toFixed(2)}" y1="${margins.top}" x2="${xx.toFixed(2)}" y2="${rvolTop + rvolHeight}" class="grid" opacity=".45"/>`);
    lines.push(`<text x="${xx.toFixed(2)}" y="${H - 22}" text-anchor="middle" class="axis">${label}</text>`);
  }
  for (const bar of visible) {
    const xx = x(bar.t + 7500);
    const yyOpen = y(bar.open);
    const yyClose = y(bar.close);
    const yyHigh = y(bar.high);
    const yyLow = y(bar.low);
    const color = bar.close >= bar.open ? 'up' : 'down';
    const top = Math.min(yyOpen, yyClose);
    const height = Math.max(1.4, Math.abs(yyClose - yyOpen));
    lines.push(`<line x1="${xx.toFixed(2)}" y1="${yyHigh.toFixed(2)}" x2="${xx.toFixed(2)}" y2="${yyLow.toFixed(2)}" class="${color} wick"/>`);
    lines.push(`<rect x="${(xx - barWidth / 2).toFixed(2)}" y="${top.toFixed(2)}" width="${barWidth.toFixed(2)}" height="${height.toFixed(2)}" class="${color}"/>`);
    const vh = (bar.volume || 0) / maxVolume * (volHeight - 8);
    if (vh > 0) lines.push(`<rect x="${(xx - barWidth / 2).toFixed(2)}" y="${(volTop + volHeight - vh).toFixed(2)}" width="${barWidth.toFixed(2)}" height="${vh.toFixed(2)}" class="${color}" opacity=".8"/>`);
    const dh = Math.abs(bar.delta || 0) / maxDelta * (deltaHeight / 2 - 4);
    if (dh > 0) lines.push(`<rect x="${(xx - barWidth / 2).toFixed(2)}" y="${bar.delta >= 0 ? deltaTop + deltaHeight / 2 - dh : deltaTop + deltaHeight / 2}" width="${barWidth.toFixed(2)}" height="${dh.toFixed(2)}" class="${bar.delta >= 0 ? 'delta-pos' : 'delta-neg'}"/>`);
    const rh = bar.rvol ? Math.min(rvolHeight - 5, bar.rvol / maxRvol * (rvolHeight - 5)) : 0;
    if (rh > 0) lines.push(`<rect x="${(xx - barWidth / 2).toFixed(2)}" y="${(rvolTop + rvolHeight - rh).toFixed(2)}" width="${barWidth.toFixed(2)}" height="${rh.toFixed(2)}" fill="#71d8ef" opacity=".55"/>`);
    if ((bar.rvol || 0) >= 2 || bar.largeVolume) lines.push(`<circle cx="${xx.toFixed(2)}" cy="${(volTop + 5).toFixed(2)}" r="3.2" fill="#f4c95d" class="large"/>`);
  }
  const vwapPoints = visible.filter((bar) => Number.isFinite(bar.vwap)).map((bar) => `${x(bar.t + 7500).toFixed(2)},${y(bar.vwap).toFixed(2)}`);
  if (vwapPoints.length > 1) lines.push(`<polyline points="${vwapPoints.join(' ')}" class="vwap"/>`);
  const entryX = x(entryEpoch);
  if (mode === 'before') {
    lines.push(`<rect x="${entryX.toFixed(2)}" y="${margins.top}" width="${Math.max(0, W - margins.right - entryX).toFixed(2)}" height="${rvolTop + rvolHeight - margins.top}" class="mask"/>`);
    lines.push(`<text x="${Math.min(W - 210, entryX + 16).toFixed(2)}" y="${margins.top + 52}" class="masktext">ENTRY以降はAFTERで公開</text>`);
  }
  lines.push(`<line x1="${entryX.toFixed(2)}" y1="${margins.top}" x2="${entryX.toFixed(2)}" y2="${priceBottom}" class="entry"/>`);
  lines.push(`<rect x="${Math.min(W - 138, entryX + 6).toFixed(2)}" y="${margins.top + 8}" width="111" height="21" rx="5" class="box"/><text x="${Math.min(W - 132, entryX + 12).toFixed(2)}" y="${margins.top + 23}" class="label">ENTRY ${escXml(item.entryTime)}</text>`);
  if (mode !== 'before') {
    const exitX = x(exitEpoch);
    lines.push(`<line x1="${exitX.toFixed(2)}" y1="${margins.top}" x2="${exitX.toFixed(2)}" y2="${priceBottom}" class="exit"/>`);
    lines.push(`<rect x="${Math.min(W - 138, exitX + 6).toFixed(2)}" y="${margins.top + 36}" width="111" height="21" rx="5" class="exitbox"/><text x="${Math.min(W - 132, exitX + 12).toFixed(2)}" y="${margins.top + 51}" class="label">EXIT ${escXml(item.exitTime)}</text>`);
  }
  lines.push(`<line x1="${margins.left}" y1="${deltaTop + deltaHeight / 2}" x2="${W - margins.right}" y2="${deltaTop + deltaHeight / 2}" class="axisline"/>`);
  lines.push(`<text x="${margins.left}" y="${H - 4}" class="sub">推定Δ=価格上昇時＋出来高／下落時−出来高（同値は0）｜RVOL=直前30本平均比｜点=RVOL 2倍以上または出来高異常</text>`);
  lines.push('</svg>');
  return lines.join('');
}

function boardSvg(item, snapshot, afterSnapshot) {
  const W = 1200;
  const H = 690;
  const metrics = snapshot.metrics;
  const maxQty = Math.max(1, ...snapshot.sell.map((row) => row.qty), ...snapshot.buy.map((row) => row.qty));
  const lines = [];
  lines.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${escXml(item.dateDisplay + ' ' + item.code + ' ENTRY板')}">`);
  lines.push('<style>text{font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Yu Gothic",Meiryo,sans-serif}.title{fill:#edf6fb;font-size:21px;font-weight:800}.sub{fill:#a7bac7;font-size:12px}.head{fill:#ff8b98;font-size:14px;font-weight:800}.bidhead{fill:#77e3a9;font-size:14px;font-weight:800}.label{fill:#edf6fb;font-size:13px}.muted{fill:#a7bac7;font-size:11px}.sellbar{fill:#b84f62;opacity:.72}.buybar{fill:#2f9a72;opacity:.8}.row{stroke:#29465a;stroke-width:1}.center{fill:#12283a;stroke:#71d8ef;stroke-width:1.5}.badge{fill:#f4c95d}.badgetext{fill:#071019;font-size:11px;font-weight:800}</style>');
  lines.push('<rect width="100%" height="100%" fill="#0b1219"/>');
  lines.push(`<text x="50" y="34" class="title">${escXml(item.dateDisplay)} ${escXml(item.code)} ${escXml(item.name)}｜ENTRY時点の板</text>`);
  lines.push(`<text x="50" y="55" class="sub">板読みTools　${escXml(snapshot.currentPriceTime || snapshot.observedAt)}　現在値 ${escXml(fmtPrice(snapshot.price))}円　VWAP ${escXml(fmtPrice(snapshot.vwap))}円</text>`);
  lines.push(`<text x="50" y="91" class="head">売り注文（上）</text><text x="50" y="111" class="muted">数量・板の厚み</text><text x="600" y="111" class="muted">価格</text>`);
  lines.push(`<text x="740" y="91" class="bidhead">買い注文（下）</text><text x="740" y="111" class="muted">価格</text><text x="900" y="111" class="muted">数量・板の厚み</text>`);
  for (let index = 0; index < 10; index += 1) {
    const sellY = 133 + index * 21;
    const buyY = 356 + index * 21;
    const sell = snapshot.sell[9 - index];
    const buy = snapshot.buy[index];
    if (sell) {
      const width = 380 * sell.qty / maxQty;
      lines.push(`<line x1="50" y1="${sellY + 10}" x2="1150" y2="${sellY + 10}" class="row" opacity=".5"/><rect x="${(580 - width).toFixed(2)}" y="${sellY - 9}" width="${width.toFixed(2)}" height="16" rx="4" class="sellbar"/>`);
      lines.push(`<text x="${(570 - width).toFixed(2)}" y="${sellY + 4}" text-anchor="end" class="label">${escXml(fmtQty(sell.qty))}</text><text x="610" y="${sellY + 4}" class="label">${escXml(fmtPrice(sell.price))}</text>`);
      if (sell.level <= 3) lines.push(`<text x="${Math.max(65, 562 - width).toFixed(2)}" y="${sellY + 4}" text-anchor="end" class="muted">L${sell.level}</text>`);
    }
    if (buy) {
      const width = 380 * buy.qty / maxQty;
      lines.push(`<line x1="50" y1="${buyY + 10}" x2="1150" y2="${buyY + 10}" class="row" opacity=".5"/><rect x="900" y="${buyY - 9}" width="${width.toFixed(2)}" height="16" rx="4" class="buybar"/>`);
      lines.push(`<text x="750" y="${buyY + 4}" class="label">${escXml(fmtPrice(buy.price))}</text><text x="${Math.min(1140, 912 + width).toFixed(2)}" y="${buyY + 4}" class="label">${escXml(fmtQty(buy.qty))}</text>`);
      if (index < 3) lines.push(`<text x="${Math.min(1170, 915 + width).toFixed(2)}" y="${buyY + 4}" class="muted">L${index + 1}</text>`);
    }
  }
  const boxY = 575;
  const imbalanceText = metrics.imbalance === null ? '—' : `${metrics.imbalance >= 0 ? '+' : ''}${(metrics.imbalance * 100).toFixed(1)}%`;
  lines.push(`<rect x="50" y="${boxY}" width="1100" height="96" rx="10" class="center"/>`);
  lines.push(`<text x="72" y="${boxY + 28}" class="label">最良買い ${escXml(fmtPrice(metrics.bestBid))}　最良売り ${escXml(fmtPrice(metrics.bestAsk))}　スプレッド ${escXml(fmtPrice(metrics.spread))}</text>`);
  lines.push(`<text x="72" y="${boxY + 52}" class="label">買い上位5段 ${escXml(fmtQty(metrics.buy5))}　／　売り上位5段 ${escXml(fmtQty(metrics.sell5))}　／　需給差 ${escXml(imbalanceText)}</text>`);
  lines.push(`<text x="72" y="${boxY + 76}" class="muted">数量だけでは方向を決めず、歩み値で板を吸収したかを確認。</text>`);
  lines.push('</svg>');
  return lines.join('');
}

function boardChangeText(entry, after) {
  if (!after) return 'EXIT時点の板スナップショットなし';
  const entryMetrics = entry.metrics;
  const afterMetrics = after.metrics;
  const entryImbalance = entryMetrics.imbalance === null ? null : entryMetrics.imbalance * 100;
  const afterImbalance = afterMetrics.imbalance === null ? null : afterMetrics.imbalance * 100;
  if (entryImbalance === null || afterImbalance === null) return '上位5段の比較値なし';
  return `上位5段の需給差 ${entryImbalance.toFixed(1)}% → ${afterImbalance.toFixed(1)}%（${after.currentPriceTime || after.observedAt}）`;
}

function boardHint(item) {
  const m = item.entrySnapshot.metrics;
  const imbalance = m.imbalance === null ? '需給差を算出できない' : `上位5段の需給差は${(m.imbalance * 100).toFixed(1)}%`;
  if (item.direction === 'LONG' && m.imbalance !== null && m.imbalance < 0) {
    return `LONGの確認点：${imbalance}で売り板優位に見えるため、売り1〜3段を買いが約定で吸収し、ENTRY上を維持できるかを確認する。`;
  }
  if (item.direction === 'SHORT' && m.imbalance !== null && m.imbalance > 0) {
    return `SHORTの確認点：${imbalance}で買い板優位に見えるため、買い1〜3段を売りが約定で消化し、ENTRY下へ定着できるかを確認する。`;
  }
  if (item.direction === 'LONG') return `LONGの確認点：${imbalance}。ENTRY下の買い板が残り、売り1〜3段を約定で吸収してENTRY上を維持できるかを確認する。`;
  return `SHORTの確認点：${imbalance}。ENTRY上の売り板が残り、買い1〜3段を消化してENTRY下へ定着できるかを確認する。`;
}

function quizChoices(item) {
  const imbalance = item.metrics?.imbalance;
  const gap = item.entryPrice - item.vwap;
  const gapPct = item.vwap ? gap / item.vwap * 100 : 0;
  const side = gap >= 0 ? '上' : '下';
  const q1Correct = Number.isFinite(item.vwap)
    ? `ENTRY価格はVWAPより${fmtPrice(Math.abs(gap))}円（${Math.abs(gapPct).toFixed(2)}%）${side}。これは平均約定価格に対する位置で、方向の確定には価格がその位置を保つか確認が必要。`
    : 'この時点ではVWAPを取得できないため、VWAPとの上下を材料にした判断は保留する。';
  const q1WrongSide = side === '上' ? '下' : '上';
  const q1Wrong = `ENTRY価格はVWAPより${fmtPrice(Math.abs(gap))}円${q1WrongSide}にある。`;
  const imbalanceText = imbalance === null ? '算出なし' : `${imbalance >= 0 ? '+' : ''}${(imbalance * 100).toFixed(1)}%`;
  const boardBias = imbalance === null ? '偏りなし' : imbalance < 0 ? '売り板優位' : '買い板優位';
  const q2Correct = item.direction === 'LONG'
    ? '板の数量は未約定の注文。売り1〜3段が買いの約定で減り、価格がENTRY上を保てるかを歩み値と値動きで確認する。'
    : '板の数量は未約定の注文。買い1〜3段が売りの約定で減り、価格がENTRY下を保てるかを歩み値と値動きで確認する。';
  const makeChoices = (correct, wrong, correctIndex) => {
    const keys = ['A', 'B', 'C', 'D'];
    const choices = {};
    let wrongIndex = 0;
    keys.forEach((key, index) => { choices[key] = index === correctIndex ? correct : wrong[wrongIndex++]; });
    return { answer: keys[correctIndex], choices };
  };
  const caseIndex = Math.max(0, Number(String(item.id || '').slice(-1)) - 1) % 4;
  const q1 = makeChoices(q1Correct, [
    q1Wrong,
    `${side === '上' ? 'ENTRY価格がVWAPより上なら' : 'ENTRY価格がVWAPより下なら'}、その時点で同方向への値動きが確定している。`,
    'VWAPは表示されていても売買判断には使えず、板の数量だけでENTRYを決める。',
  ], caseIndex);
  const q2 = makeChoices(q2Correct, [
    `${boardBias}なので、板の偏りが示す方向へ価格が動くと決めてENTRYする。`,
    '表示数量はすべて約定する前提で、厚い板は必ず支持または抵抗として残る。',
    '板は変化するので需給差は読まず、価格や歩み値も確認しない。',
  ], (caseIndex + 1) % 4);
  return {
    q1: {
      question: `ENTRY価格 ${fmtPrice(item.entryPrice)}円とVWAP ${fmtPrice(item.vwap)}円の関係を正しく説明しているのは？`,
      ...q1,
      purpose: 'VWAPに対する価格の位置を正しく読む練習。VWAPの上か下かは相場の位置情報であり、それだけで上昇・下落を断定しないことを確認する。',
      explanation: q1Correct,
    },
    q2: {
      question: `上位5段の需給差は${imbalanceText}（${boardBias}）。${item.direction}で確認すべきことは？`,
      ...q2,
      purpose: '板の数量はその瞬間の未約定注文で、取消や追加もある。数字の偏りを予言と誤解せず、歩み値の約定と価格の反応で確かめる練習。',
      explanation: q2Correct,
    },
  };
}

function renderPage(cases) {
  const card = (item, index) => {
    const choices = item.quiz;
    const radio = (name, data) => Object.entries(data.choices).map(([key, text]) => `<label class="option"><input type="radio" name="${item.id}-${name}" value="${key}"><span><strong>${key}.</strong> ${escHtml(text)}</span></label>`).join('');
    const analysisCards = (item.beforeAnalysis || []).map((metric) => `<article class="analysis-card"><h4>${escHtml(metric.label)}</h4><strong>${escHtml(metric.value)}</strong><p>${escHtml(metric.detail)}</p></article>`).join('');
    return `<details class="case" data-id="${item.id}" ${index === 0 ? 'open' : ''}><summary class="case-head"><div><span class="num">${escHtml(item.id)}</span><h2>${escHtml(item.code)} ${escHtml(item.name)}</h2><p>${escHtml(item.dateDisplay)} ${escHtml(item.entryTime)} / ENTRY ${escHtml(fmtPrice(item.entryPrice))}円 / ${escHtml(item.direction)}</p></div><span class="tag">板読みTools</span></summary><div class="body"><div class="facts"><span>ENTRY ${escHtml(item.entryTime)} / EXIT ${escHtml(item.exitTime)}</span><span>VWAP ${escHtml(fmtPrice(item.entrySnapshot.vwap))}円</span><span>上位5段差 ${escHtml(item.imbalanceText)}</span></div><div class="image-grid"><figure><img class="zoomable" src="${escHtml(item.assets.before)}" alt="${escHtml(item.dateDisplay)} ${escHtml(item.code)} BEFORE"><figcaption>① BEFORE：9時から引けまでのチャート。ENTRYより右を隠しています。</figcaption></figure><figure><img class="zoomable" src="${escHtml(item.assets.board)}" alt="${escHtml(item.dateDisplay)} ${escHtml(item.code)} ENTRY板"><figcaption>② ENTRY時点：売り注文を上、買い注文を下に表示</figcaption></figure></div><section class="before-analysis"><div class="analysis-heading"><h3>BEFOREで確認できる材料</h3><p>見る順：ENTRYとVWAPの位置 → 値幅のどの辺か → 出来高と推定Δの向き → 板の偏りが価格に出ているか。自分で方向を考えてから回答してください。</p></div><div class="analysis-grid">${analysisCards}</div></section><div class="hint"><strong>板の観察：</strong>${escHtml(item.observation)}</div><form class="quiz"><div class="questions"><fieldset><legend>1. ${escHtml(choices.q1.question)}</legend>${radio('q1', choices.q1)}</fieldset><fieldset><legend>2. ${escHtml(choices.q2.question)}</legend>${radio('q2', choices.q2)}</fieldset></div><button class="answer" type="button">回答して正解・理由を見る</button><span class="message">2問選択してください。</span><div class="quiz-feedback" aria-live="polite"></div></form><section class="after"><div class="after-title">③ AFTER：答え合わせ　実績 ${escHtml(fmtYen(item.pnl))}</div><img class="zoomable after-image" src="${escHtml(item.assets.after)}" alt="${escHtml(item.dateDisplay)} ${escHtml(item.code)} AFTER"><p><strong>板読みの再現ポイント：</strong>${escHtml(item.boardHint)}</p><p><strong>キリ番：</strong>${escHtml(item.roundText)}　<strong>ダウ理論：</strong>${escHtml(item.dowHint)}</p><p class="source">出典：${escHtml(item.sourceFiles.join(' / '))} ／ 日誌CSV：${escHtml(item.diaryCsv)}</p></section></div></details>`;
  };
  return `<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>板読み連動トレード復習クイズ｜2026</title><style>
:root{--bg:#071019;--panel:#102131;--line:#2b4a60;--text:#edf6fb;--muted:#a7bac7;--cyan:#71d8ef;--green:#77e3a9;--red:#ff8b98;--yellow:#f4c95d}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0,#17344a 0,#071019 42rem);color:var(--text);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Hiragino Sans","Yu Gothic",Meiryo,sans-serif}main{width:min(1220px,calc(100% - 24px));margin:auto;padding:25px 0 65px}header,.notice,.case{border:1px solid var(--line);border-radius:15px;background:rgba(16,33,49,.94);box-shadow:0 15px 38px rgba(0,0,0,.2)}header{padding:25px}h1{margin:0 0 8px;font-size:clamp(27px,4vw,42px)}h1 small{color:var(--cyan);font-size:12px;letter-spacing:.15em;display:block;margin-bottom:9px}.lead{margin:0;color:#c8d9e4}.notice{margin:13px 0;padding:13px 16px;color:#c8d9e4}.notice strong{color:var(--yellow)}.case-list{display:grid;gap:13px}.case{overflow:hidden}.case-head{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:15px 18px;cursor:pointer;list-style:none;background:rgba(20,49,70,.82)}.case-head::-webkit-details-marker{display:none}.case-head:after{content:"＋";font-size:20px;color:var(--cyan)}.case[open]>.case-head:after{content:"−"}.case-head>div{display:grid;grid-template-columns:auto 1fr;column-gap:11px;align-items:center}.case-head h2{margin:0;font-size:19px}.case-head p{grid-column:2;margin:2px 0 0;color:var(--muted);font-size:12px}.num{grid-row:span 2;color:var(--cyan);border:1px solid rgba(113,216,239,.4);border-radius:10px;padding:9px 8px;font-weight:800}.tag{border:1px solid rgba(113,216,239,.35);border-radius:999px;padding:4px 9px;color:var(--cyan);font-size:11px}.body{padding:18px}.facts{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:11px}.facts span{border:1px solid rgba(255,255,255,.14);border-radius:999px;padding:4px 8px;color:var(--muted);font-size:12px}.before-analysis{margin:14px 0;padding:14px;border:1px solid rgba(113,216,239,.24);border-radius:12px;background:rgba(7,17,26,.65)}.analysis-heading h3{margin:0;color:var(--cyan);font-size:17px}.analysis-heading p{margin:2px 0 11px;color:var(--muted);font-size:12px}.analysis-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.analysis-card{min-width:0;padding:10px;border:1px solid rgba(255,255,255,.12);border-radius:9px;background:rgba(255,255,255,.025)}.analysis-card h4{margin:0 0 5px;color:var(--muted);font-size:11px;font-weight:650}.analysis-card strong{display:block;color:var(--text);font-size:14px;line-height:1.4;overflow-wrap:anywhere}.analysis-card p{margin:5px 0 0;color:#b7c8d3;font-size:11px;line-height:1.45}.image-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.image-grid figure{margin:0;border:1px solid var(--line);border-radius:11px;overflow:hidden;background:#0b1219}.image-grid img{display:block;width:100%;height:auto;cursor:zoom-in}.image-grid figcaption{padding:8px 10px;color:#c8d9e4;background:#07111a;font-size:12px}.hint{margin-top:11px;padding:11px;border-left:3px solid var(--yellow);background:rgba(244,201,93,.07);color:#dbe7ed}.hint strong{color:var(--yellow)}.questions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px;margin-top:15px}fieldset{margin:0;padding:12px;border:1px solid rgba(255,255,255,.14);border-radius:10px;background:rgba(255,255,255,.025)}legend{font-weight:800}.option{display:flex;gap:7px;margin-top:7px;padding:7px;border:1px solid rgba(255,255,255,.12);border-radius:8px;cursor:pointer;color:#c8d9e4}.option input{accent-color:var(--cyan);margin-top:4px}.option strong{color:var(--cyan)}.answer{margin-top:13px;border:0;border-radius:8px;background:var(--cyan);color:#06141c;padding:9px 13px;font-weight:800;cursor:pointer} .message{margin-left:8px;color:var(--muted);font-size:12px}.quiz-feedback{display:none;margin-top:12px;padding:12px;border:1px solid rgba(113,216,239,.24);border-radius:10px;background:rgba(7,17,26,.6)}.quiz-feedback.show{display:grid;gap:8px}.quiz-purpose{padding:9px;border-left:3px solid var(--yellow);background:rgba(244,201,93,.07);color:#e4edf2}.feedback-item{padding:10px;border:1px solid rgba(255,255,255,.12);border-radius:8px}.feedback-item.right{border-left:3px solid var(--green)}.feedback-item.wrong{border-left:3px solid var(--red)}.feedback-item h4{margin:0 0 5px}.feedback-item p{margin:4px 0;color:#c8d9e4}.feedback-item strong{color:var(--cyan)}.after{display:none;margin-top:18px;padding-top:17px;border-top:1px solid rgba(255,255,255,.14)}.after.show{display:block}.after-title{color:var(--green);font-weight:850;margin-bottom:8px}.after-image{display:block;width:100%;cursor:zoom-in;border:1px solid var(--line);border-radius:10px}.after p{margin:8px 0;color:#c8d9e4}.source{font-size:11px;color:var(--muted)!important}.lightbox{width:min(96vw,1400px);max-height:94vh;padding:42px 14px 14px;background:rgba(3,10,17,.97);border:1px solid rgba(113,216,239,.5);border-radius:13px}.lightbox::backdrop{background:rgba(0,0,0,.8)}.lightbox img{display:block;width:100%;max-height:82vh;object-fit:contain}.close{position:absolute;right:9px;top:7px;border:1px solid rgba(255,255,255,.2);border-radius:99px;background:rgba(255,255,255,.1);color:var(--text);font-size:22px;width:34px;height:34px}.caption{text-align:center;color:var(--muted);font-size:12px;margin:7px 0 0}@media(max-width:800px){main{width:min(100% - 14px,650px)}.image-grid,.questions{grid-template-columns:1fr}.analysis-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.case-head{display:block}.case-head>div{display:block}.case-head p{margin-left:0}.tag{display:inline-block;margin-top:8px}.message{display:block;margin:7px 0 0}}</style></head><body><main><header><h1><small>BOARD READING × TRADE REVIEW</small>板読み連動トレード復習クイズ</h1><p class="lead">板読みToolsの実データと日誌の3ケースです。チャートには出来高バー・推定デルタ・RVOLを表示し、板は売りを上、買いを下に配置しています。</p></header><div class="notice"><strong>解き方：</strong>①09:00から引けまでの時間軸でENTRYまでの値動き、②ENTRY時点の板10段を見て2問に答えてください。回答すると、各問の正解・不正解と理由、③AFTERを表示します。出来高バーは15秒ごとの数量、RVOLは直前30本平均との比率です。推定デルタは価格上昇時の出来高差分を＋、下落時を−としており、売買主導別の実約定デルタではありません。</div><section class="case-list">${cases.map(card).join('')}</section><footer class="notice">元データ：板読みToolsのjsonl.gzから選択ケースの銘柄・価格・板10段だけを抽出。4.9GBの生データ全量はGitHubへコピーせず、クイズ再現に必要な抜粋JSONと生成SVGを保存しています。</footer></main><dialog id="lightbox" class="lightbox"><button id="close" class="close" type="button">×</button><img id="lightbox-image" alt=""><p id="caption" class="caption"></p></dialog><script>const cases=${JSON.stringify(cases)};const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));document.querySelectorAll('.case').forEach(card=>{const id=card.dataset.id;const item=cases.find(v=>v.id===id);const button=card.querySelector('.answer');button.addEventListener('click',()=>{const a=card.querySelector('input[name="'+id+'-q1"]:checked');const b=card.querySelector('input[name="'+id+'-q2"]:checked');const message=card.querySelector('.message');if(!a||!b){message.textContent='2問とも選択してください。';return}const score=Number(a.value===item.quiz.q1.answer)+Number(b.value===item.quiz.q2.answer);message.textContent=score+' / 2 問正解';const explain=(number,q,selected)=>{const correct=selected===q.answer;return '<article class="feedback-item '+(correct?'right':'wrong')+'"><h4>問'+number+'：'+(correct?'正解 ✓':'不正解 ✗')+'</h4><p><strong>あなたの回答：</strong>'+selected+'. '+esc(q.choices[selected])+'</p><p><strong>正解：</strong>'+q.answer+'. '+esc(q.choices[q.answer])+'</p><p><strong>なぜこの質問をしたか：</strong>'+esc(q.purpose)+'</p><p><strong>答えの根拠：</strong>'+esc(q.explanation)+'</p></article>'};const feedback=card.querySelector('.quiz-feedback');feedback.innerHTML='<div class="quiz-purpose"><strong>このクイズの目的：</strong>ENTRY前の数値と板を根拠を持って読み、値動きの予想と確認条件を用意してから、AFTERで検証する練習です。</div>'+explain(1,item.quiz.q1,a.value)+explain(2,item.quiz.q2,b.value);feedback.classList.add('show');card.querySelector('.after').classList.add('show');card.querySelectorAll('input').forEach(input=>input.disabled=true)})});const lightbox=document.getElementById('lightbox'),image=document.getElementById('lightbox-image'),caption=document.getElementById('caption');document.addEventListener('click',event=>{const target=event.target.closest('.zoomable');if(!target)return;image.src=target.src;image.alt=target.alt;caption.textContent=target.alt+'｜クリックまたはEscで閉じる';lightbox.showModal()});document.getElementById('close').addEventListener('click',()=>lightbox.close());lightbox.addEventListener('click',event=>{if(event.target===lightbox)lightbox.close()});</script></body></html>`;
}

async function main() {
  fs.mkdirSync(ASSET_DIR, { recursive: true });
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const outputCases = [];
  for (const spec of CASE_SPECS) {
    const { group, trade } = findDiaryTrade(spec);
    const sourcePaths = spec.sourceFiles.map((file) => path.join(SOURCE_ROOT, spec.sourceDirs[0], file));
    for (const sourcePath of sourcePaths) if (!fs.existsSync(sourcePath)) throw new Error(`板データがありません: ${sourcePath}`);
    const chunks = [];
    for (const sourcePath of sourcePaths) {
      console.log(`Reading ${path.basename(sourcePath)} / ${spec.code}`);
      chunks.push(await readBoardFile(sourcePath, spec.code));
    }
    const records = mergeRecords(chunks);
    const entryEpoch = epoch(dateTime(spec.date, timeDisplay(trade.entry_time)));
    const exitEpoch = epoch(dateTime(spec.date, timeDisplay(trade.actual_exit_time)));
    const entrySnapshot = pickSnapshot(records.snapshots, entryEpoch);
    const afterSnapshot = pickSnapshot(records.snapshots, exitEpoch);
    if (!entrySnapshot) throw new Error(`ENTRY板スナップショットがありません: ${spec.date} ${spec.code}`);
    const allBars = aggregateBars(records.points, epoch(dateTime(spec.date, '09:00:00')), records.points.at(-1)?.t || exitEpoch + 60000);
    const beforeBars = aggregateBars(records.points, epoch(dateTime(spec.date, '09:00:00')), entryEpoch);
    const outputBase = `${spec.date}_${spec.code}`;
    const beforeName = `${outputBase}_before.svg`;
    const boardName = `${outputBase}_entry_board.svg`;
    const afterName = `${outputBase}_after.svg`;
    const entryOutput = snapshotForOutput(entrySnapshot);
    const afterOutput = snapshotForOutput(afterSnapshot);
    const metrics = entryOutput.metrics;
    const round = roundInfo(number(trade.entry_price));
    const direction = spec.direction;
    const observation = `現在値 ${fmtPrice(entryOutput.price)}円、VWAP ${fmtPrice(entryOutput.vwap)}円、最良買い ${fmtPrice(metrics.bestBid)}円、最良売り ${fmtPrice(metrics.bestAsk)}円、スプレッド ${fmtPrice(metrics.spread)}円。`;
    const item = {
      id: spec.id,
      date: spec.date,
      dateDisplay: dateDisplay(spec.date),
      code: spec.code,
      name: spec.name,
      direction,
      entryTime: timeDisplay(trade.entry_time),
      exitTime: timeDisplay(trade.actual_exit_time),
      entryPrice: number(trade.entry_price),
      exitPrice: number(trade.actual_exit_price),
      pnl: number(trade.actual_pnl),
      groupPnl: number(group.pnl),
      diaryCsv: trade.execution_source_file || '',
      sourceFiles: spec.sourceFiles,
      entrySnapshot: entryOutput,
      afterSnapshot: afterOutput,
      beforeAnalysis: buildBeforeAnalysis(beforeBars, entryOutput, number(trade.entry_price)),
      imbalanceText: metrics.imbalance === null ? '—' : `${metrics.imbalance >= 0 ? '+' : ''}${(metrics.imbalance * 100).toFixed(1)}%`,
      observation,
      roundText: `${fmtPrice(round.level)}円（ENTRYとの差 ${round.distance >= 0 ? '+' : ''}${fmtPrice(round.distance)}円）`,
      dowHint: direction === 'LONG' ? '安値切り上げ→高値更新の順番。高値更新に失敗したら追い買いしない。' : '高値切り下げ→安値更新の順番。安値更新に失敗したら追い売りしない。',
      boardHint: '',
      assets: { before: `assets/${beforeName}`, board: `assets/${boardName}`, after: `assets/${afterName}` },
      quiz: quizChoices({ id: spec.id, direction, metrics, entryPrice: number(trade.entry_price), vwap: entryOutput.vwap }),
    };
    item.boardHint = boardHint(item);
    fs.writeFileSync(path.join(ASSET_DIR, beforeName), chartSvg(item, beforeBars, 'before'), 'utf8');
    fs.writeFileSync(path.join(ASSET_DIR, boardName), boardSvg(item, entryOutput, afterOutput), 'utf8');
    fs.writeFileSync(path.join(ASSET_DIR, afterName), chartSvg(item, allBars, 'after'), 'utf8');
    outputCases.push(item);
    fs.writeFileSync(path.join(DATA_DIR, `${spec.id}_board_snapshots.json`), JSON.stringify({ id: spec.id, date: spec.date, code: spec.code, sourceFiles: spec.sourceFiles, entry: entryOutput, after: afterOutput }, null, 2), 'utf8');
  }
  fs.writeFileSync(path.join(DATA_DIR, 'cases.json'), JSON.stringify(outputCases, null, 2), 'utf8');
  fs.writeFileSync(path.join(OUT_DIR, 'source_manifest.json'), JSON.stringify({ generatedAt: new Date().toISOString(), caseCount: outputCases.length, imageCount: outputCases.length * 3, source: '板読みTools jsonl.gz + full_day trade diary manifest', rawSourceSize: '約4.9GB（全量はGitHubへコピーせず）', cases: outputCases.map((item) => ({ id: item.id, date: item.date, code: item.code, entryTime: item.entryTime, exitTime: item.exitTime, pnl: item.pnl, sourceFiles: item.sourceFiles, diaryCsv: item.diaryCsv })) }, null, 2), 'utf8');
  fs.writeFileSync(path.join(OUT_DIR, 'index.html'), renderPage(outputCases), 'utf8');
  console.log(JSON.stringify({ output: path.join(OUT_DIR, 'index.html'), cases: outputCases.length, images: outputCases.length * 3, size: 'curated snapshots only' }, null, 2));
}

main().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });

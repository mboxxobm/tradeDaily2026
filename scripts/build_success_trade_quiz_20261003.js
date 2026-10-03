#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

const REPO = path.resolve(__dirname, '..');
const SOURCE = '/Users/th/Documents/TradeDayReport20260821';
const OUT_DIR = path.join(REPO, 'success_trade_quiz_20261003');
const ASSET_DIR = path.join(OUT_DIR, 'assets');
const CHART_DIR = path.join(SOURCE, 'outputs/full_day_312_trade_charts_202605_202608/charts');
const FULL_DAY_MANIFEST = path.join(SOURCE, 'outputs/full_day_312_trade_charts_202605_202608/manifest.json');
const PATTERN_CSV = path.join(SOURCE, 'entry_pattern_database_20260803_20260901.csv');
const BOARD_ROOT = '/Volumes/1000GB20260826/2026BC_imac3/板読みTools';
const BRISX_ROOT = '/Volumes/1000GB20260826/BrisX';

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = '';
  let quoted = false;
  const source = text.replace(/^\uFEFF/, '');
  for (let i = 0; i < source.length; i += 1) {
    const char = source[i];
    if (quoted) {
      if (char === '"' && source[i + 1] === '"') {
        cell += '"';
        i += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        cell += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ',') {
      row.push(cell);
      cell = '';
    } else if (char === '\n' || char === '\r') {
      if (char === '\r' && source[i + 1] === '\n') i += 1;
      row.push(cell);
      if (row.some((value) => value !== '')) rows.push(row);
      row = [];
      cell = '';
    } else {
      cell += char;
    }
  }
  row.push(cell);
  if (row.some((value) => value !== '')) rows.push(row);
  if (!rows.length) return [];
  const headers = rows.shift().map((value) => value.trim());
  return rows.map((values) => Object.fromEntries(headers.map((header, i) => [header, values[i] ?? ''])));
}

function number(value) {
  const parsed = Number(String(value ?? '').replaceAll(',', '').replace(/[円株]/g, ''));
  return Number.isFinite(parsed) ? parsed : 0;
}

function yen(value) {
  const amount = Math.round(number(value));
  return `${amount >= 0 ? '+' : '−'}${Math.abs(amount).toLocaleString('ja-JP')}円`;
}

function price(value) {
  const amount = number(value);
  return Number.isInteger(amount) ? amount.toLocaleString('ja-JP') : amount.toLocaleString('ja-JP', { maximumFractionDigits: 2 });
}

function dateDisplay(value) {
  const text = String(value || '');
  return text.length === 10 ? text.replaceAll('-', '/') : `${text.slice(0, 4)}/${text.slice(4, 6)}/${text.slice(6, 8)}`;
}

function timeDisplay(value) {
  return String(value || '').slice(0, 5);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
}

function dateKey(value) {
  return String(value || '').replaceAll('-', '').slice(0, 8);
}

function dateDirectories(root) {
  const result = new Map();
  if (!fs.existsSync(root)) return result;
  for (const name of fs.readdirSync(root)) {
    if (!/^\d{8}/.test(name)) continue;
    const full = path.join(root, name);
    if (fs.statSync(full).isDirectory()) result.set(name.slice(0, 8), full);
  }
  return result;
}

function availabilityFor(date, code, boardDirs, brisxDirs) {
  const sources = [];
  const boardDir = boardDirs.get(date);
  const brisxDir = brisxDirs.get(date);
  let replay = false;
  let events = false;
  if (boardDir) {
    const names = fs.readdirSync(boardDir);
    replay = names.some((name) => name.endsWith('.jsonl.gz'));
    events = names.some((name) => name.endsWith('board_events.csv'));
    if (replay || events) sources.push('板読みTools');
  }
  if (brisxDir && (fs.existsSync(path.join(brisxDir, 'All_ItaRows.json')) || fs.existsSync(path.join(brisxDir, 'All_Ticks.json')))) {
    sources.push('BrisX');
  }
  return {
    available: sources.length > 0,
    sources,
    replay,
    events,
    code,
    date,
  };
}

function roundInfo(entryPrice) {
  const value = number(entryPrice);
  const step = value >= 10000 ? 500 : value >= 3000 ? 100 : value >= 1000 ? 50 : value >= 200 ? 10 : 1;
  const level = Math.round(value / step) * step;
  const distance = Math.round((value - level) * 100) / 100;
  const near = Math.abs(distance) <= step * 0.2;
  return {
    step,
    level,
    distance,
    label: near
      ? `ENTRYはキリ番 ${price(level)}円付近（差 ${distance >= 0 ? '+' : ''}${distance}円）`
      : `近いキリ番は ${price(level)}円（ENTRYとの差 ${distance >= 0 ? '+' : ''}${distance}円）`,
  };
}

const PATTERN_HINTS = {
  P1: {
    label: 'P1 下げ止まり→反転LONG',
    correct: '安値切り上げ・支持帯反応・買い板の支えがそろってからLONGする',
    review: '下げ止まりを値ごろ感で決めず、安値切り上げと反発の継続を確認できた点を再現する。',
  },
  P2: {
    label: 'P2 225同期＋戻り失敗SELL',
    correct: '戻り高値が抑えられ、安値更新・225同期・売り板優位がそろってからSHORTする',
    review: '個別銘柄だけで先回りせず、225との向きと戻り失敗を重ねてから入る。',
  },
  P3: {
    label: 'P3 戻り売り／下方向継続SELL',
    correct: '戻り高値の切り下げと安値更新を確認し、売り板の残り方を見てSHORTする',
    review: '下方向が続くときも、戻りを待ってから入る順番を守る。',
  },
  P4: {
    label: 'P4 板読み短期スキャル',
    correct: '買い板の強さ・売り板の吸収・価格維持を確認し、短く利確する',
    review: '板の厚さだけでなく、約定が板を消化して価格を維持するかを確認する。',
  },
  P10: {
    label: 'P10 225シンクロLONG／短期戻し',
    correct: '225と個別銘柄が同方向になり、ENTRY上への定着を確認してからLONGする',
    review: '個別の反発だけでなく、225との同期が続くかを見て短期で判断する。',
  },
};

function genericPattern(direction, hasMarket) {
  if (direction === 'LONG') {
    return {
      label: hasMarket ? '価格構造＋225同期LONG' : '価格構造LONG',
      correct: '安値切り上げ・高値更新・買い板の支えがそろうまで待ってLONGする',
      review: '上がったという結果だけでなく、ENTRY前に安値切り上げと価格の定着を確認したかを再現する。',
    };
  }
  return {
    label: hasMarket ? '価格構造＋225同期SHORT' : '価格構造SHORT',
    correct: '高値切り下げ・安値更新・売り板の残り方がそろうまで待ってSHORTする',
    review: '下がったという結果だけでなく、戻りの抑制とENTRY下への定着を確認したかを再現する。',
  };
}

function chooseText(correct, distractors, correctIndex) {
  const values = [...distractors];
  values.splice(correctIndex, 0, correct);
  const letters = ['A', 'B', 'C', 'D'];
  return {
    answer: letters[correctIndex],
    choices: Object.fromEntries(letters.map((letter, index) => [letter, values[index]])),
  };
}

function copyAsset(source, target) {
  if (!source || !fs.existsSync(source)) return false;
  fs.mkdirSync(path.dirname(target), { recursive: true });
  if (!fs.existsSync(target)) fs.copyFileSync(source, target);
  return true;
}

const fullDay = JSON.parse(fs.readFileSync(FULL_DAY_MANIFEST, 'utf8'));
const patternRows = parseCsv(fs.readFileSync(PATTERN_CSV, 'utf8')).filter((row) => row.record_kind === 'EXECUTION_SESSION' && number(row.pnl_jpy) > 0);
const boardDirs = dateDirectories(BOARD_ROOT);
const brisxDirs = dateDirectories(BRISX_ROOT);
const groupsByDateCode = new Map();
for (const group of fullDay.groups || []) groupsByDateCode.set(`${dateKey(group.date)}|${group.code}`, group);

// This output directory is generated by this script and contains no user-authored files.
fs.rmSync(OUT_DIR, { recursive: true, force: true });
fs.mkdirSync(path.join(ASSET_DIR, 'charts'), { recursive: true });
fs.mkdirSync(path.join(ASSET_DIR, 'context'), { recursive: true });
fs.mkdirSync(path.join(ASSET_DIR, 'market'), { recursive: true });

const cases = [];
const copied = { charts: new Set(), context: new Set(), market: new Set() };
const marketAssets = new Map();
let skipped = 0;

for (const group of fullDay.groups || []) {
  const date = dateKey(group.date);
  const mainSource = path.join(CHART_DIR, group.chart_15s || '');
  if (!fs.existsSync(mainSource)) continue;
  const mainName = path.basename(mainSource);
  const mainTarget = path.join(ASSET_DIR, 'charts', mainName);
  copyAsset(mainSource, mainTarget);
  copied.charts.add(mainName);

  const contextSource = path.join(CHART_DIR, group.chart_3m || '');
  const contextName = path.basename(contextSource);
  const contextTarget = path.join(ASSET_DIR, 'context', contextName);
  if (copyAsset(contextSource, contextTarget)) copied.context.add(contextName);

  const marketGroup = groupsByDateCode.get(`${date}|1321`);
  let market = null;
  if (marketGroup && marketGroup.chart_15s) {
    const marketSource = path.join(CHART_DIR, marketGroup.chart_15s);
    const marketName = path.basename(marketSource);
    const marketTarget = path.join(ASSET_DIR, 'market', marketName);
    if (copyAsset(marketSource, marketTarget)) {
      copied.market.add(marketName);
      marketAssets.set(date, `assets/market/${marketName}`);
      market = `assets/market/${marketName}`;
    }
  }

  for (const trade of group.trades || []) {
    if (number(trade.actual_pnl) <= 0) continue;
    const entryMinute = timeDisplay(trade.entry_time);
    const pattern = patternRows.find((row) => (
      dateKey(row.date) === date &&
      String(row.code) === String(trade.code) &&
      row.direction === trade.direction &&
      timeDisplay(row.entry_time) === entryMinute
    ));
    const board = availabilityFor(date, trade.code, boardDirs, brisxDirs);
    const round = roundInfo(trade.entry_price);
    const hasMarket = Boolean(market);
    const patternGuide = PATTERN_HINTS[pattern?.pattern_id] || genericPattern(trade.direction, hasMarket);
    const q1Correct = trade.direction === 'LONG'
      ? 'ENTRY後に安値を切り上げ、高値更新方向へ進んで利益を伸ばす'
      : 'ENTRY後に戻り高値を抑え、安値更新方向へ進んで利益を伸ばす';
    const q1 = chooseText(q1Correct, trade.direction === 'LONG'
      ? ['ENTRYを割って逆行し、損切り方向へ進む', 'ENTRY付近で往復し、方向が出ない', '一度上がってもすぐに高値を失い、見送る流れになる']
      : ['ENTRYを上抜いて逆行し、損切り方向へ進む', 'ENTRY付近で往復し、方向が出ない', '一度下がってもすぐに安値を失い、見送る流れになる'], cases.length % 4);
    const q2 = chooseText(patternGuide.correct, [
      'キリ番に近いという理由だけで、板と価格の反応を見ずに入る',
      '逆行したらロットを追加し、損切り位置を後ろへ動かす',
      '225や上位足と逆向きでも、最初の一瞬の値動きだけを根拠に入る',
    ], (cases.length + 1) % 4);
    const id = `W${String(cases.length + 1).padStart(3, '0')}`;
    cases.push({
      id,
      number: String(cases.length + 1).padStart(3, '0'),
      date,
      dateDisplay: dateDisplay(group.date),
      code: String(trade.code),
      name: trade.name || group.name || '',
      direction: trade.direction,
      qty: number(trade.qty),
      entryTime: timeDisplay(trade.entry_time),
      exitTime: timeDisplay(trade.actual_exit_time),
      entryPrice: number(trade.entry_price),
      exitPrice: number(trade.actual_exit_price),
      pnl: number(trade.actual_pnl),
      groupPnl: number(group.pnl),
      cut: 100 * (76 + Math.max(0, Math.min(23400, (number(trade.entry_time.split(':')[0]) * 3600 + number(trade.entry_time.split(':')[1]) * 60 + number(trade.entry_time.split(':')[2] || 0)) - 32400)) / 23400 * (1432 - 76)) / 1450,
      main: `assets/charts/${mainName}`,
      chart3m: copied.context.has(contextName) ? `assets/context/${contextName}` : null,
      market,
      q1,
      q2,
      pattern: pattern ? {
        id: pattern.pattern_id,
        name: pattern.pattern_name,
        ruleStatus: pattern.rule_status,
        reason: pattern.classification_reason,
        invalidation: pattern.invalidation_or_no_trade,
      } : null,
      patternLabel: patternGuide.label,
      patternReview: patternGuide.review,
      round,
      board,
      boardHint: board.available
        ? (trade.direction === 'LONG'
          ? '買い板がENTRY下で残るか、売り1〜3枚を約定で吸収してENTRY上に定着するかを見る。'
          : '売り板がENTRY上で残るか、買い1〜3枚を叩いてENTRY下に定着するかを見る。')
        : (trade.direction === 'LONG'
          ? '当日板ログがないため、次回は買い板の残り方と売り板の吸収を記録する。'
          : '当日板ログがないため、次回は売り板の残り方と買い板の消化を記録する.'),
      dowHint: trade.direction === 'LONG'
        ? 'ダウ理論：安値切り上げ→高値更新の順番を確認。高値更新に失敗したら追い買いしない。'
        : 'ダウ理論：高値切り下げ→安値更新の順番を確認。安値更新に失敗したら追い売りしない。',
      source: {
        csv: trade.execution_source_file || '',
        tick: group.tick_available ? group.tick_source || 'R2同期CSV' : '未取得／CSV中心',
        diary: pattern ? 'entry_pattern_database（日誌レビュー反映）' : 'full_day_312_trade_charts manifest（取引履歴CSV集計）',
      },
    });
  }
}

// Keep only assets referenced by successful cases. The source directory also
// contains charts for losing and flat groups, but they are not part of this quiz.
const referenced = {
  charts: new Set(cases.map((item) => path.basename(item.main))),
  context: new Set(cases.map((item) => item.chart3m ? path.basename(item.chart3m) : null).filter(Boolean)),
  market: new Set(cases.map((item) => item.market ? path.basename(item.market) : null).filter(Boolean)),
};
for (const [kind, names] of Object.entries(referenced)) {
  const directory = path.join(ASSET_DIR, kind);
  for (const name of fs.readdirSync(directory)) {
    if (!names.has(name)) fs.rmSync(path.join(directory, name), { force: true });
  }
  copied[kind] = names;
}

if (!cases.length) throw new Error('成功トレードが見つかりませんでした。');

const dates = [...new Set(cases.map((item) => item.date))];
const codes = [...new Set(cases.map((item) => item.code))].sort();
  const dateButtons = dates.map((date) => `<button data-filter="date:${date}" type="button">${escapeHtml(`${date.slice(4, 6)}/${date.slice(6, 8)}`)}</button>`).join('');
const codeButtons = codes.map((code) => `<button data-filter="code:${escapeHtml(code)}" type="button">${escapeHtml(code)}</button>`).join('');
const boardCaseCount = cases.filter((item) => item.board.available).length;
const diaryPatternCount = cases.filter((item) => item.pattern).length;
const marketCaseCount = cases.filter((item) => item.market).length;

const html = String.raw`<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>勝ちトレード復習クイズ｜2026</title>
<style>
:root{--bg:#071019;--panel:#0e1c28;--panel2:#12283a;--line:#29465a;--text:#edf6fb;--muted:#a7bac7;--cyan:#71d8ef;--green:#77e3a9;--yellow:#f4c95d;--red:#ff8a97}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,#17344a 0,#071019 38rem);color:var(--text);font:14px/1.62 -apple-system,BlinkMacSystemFont,'Hiragino Sans','Yu Gothic',Meiryo,sans-serif}main{width:min(1180px,calc(100% - 28px));margin:0 auto;padding:28px 0 70px}.hero{display:grid;grid-template-columns:minmax(0,1fr) 270px;gap:16px;align-items:stretch}.hero-copy,.score,.notice,.toolbar,.case{border:1px solid var(--line);border-radius:15px;background:rgba(14,28,40,.92);box-shadow:0 18px 45px rgba(0,0,0,.2)}.hero-copy{padding:27px}.eyebrow{color:var(--cyan);font-size:12px;letter-spacing:.16em;font-weight:800}.hero h1{margin:11px 0 8px;font-size:clamp(27px,4vw,44px);line-height:1.15}.lead{margin:0;color:#c8d9e4;font-size:15px}.score{padding:20px}.score h2{margin:0;color:var(--yellow);font-size:14px}.score-number{font-size:42px;font-weight:900;margin:9px 0 5px}.score-number small{font-size:16px;color:var(--muted)}.score-meta{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.score-meta div{padding:8px 5px;border:1px solid var(--line);border-radius:9px;text-align:center}.score-meta strong{display:block;font-size:19px}.score-meta span{display:block;color:var(--muted);font-size:10px}.notice{margin-top:15px;padding:14px 17px;color:#c8d9e4}.notice strong{color:var(--yellow)}.toolbar{position:sticky;top:8px;z-index:10;display:grid;gap:9px;padding:12px;margin:15px 0;background:rgba(7,16,24,.94);backdrop-filter:blur(10px)}.toolbar-row{display:flex;gap:8px;align-items:center}.toolbar input,.toolbar select{min-width:0;background:#091520;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:8px 10px}.toolbar input{flex:1}.toolbar button,.secondary{background:var(--panel2);color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:5px 10px;font-size:12px;cursor:pointer}.toolbar button.active,.toolbar button:hover,.secondary:hover{border-color:var(--cyan);color:var(--text)}.result-count{margin-left:auto;color:var(--muted);font-size:12px;white-space:nowrap}.filter-row{display:flex;flex-wrap:wrap;gap:6px}.toolbar-note{margin:0;color:var(--muted);font-size:12px}.case-list{display:grid;gap:13px}.case.is-hidden{display:none}.case{overflow:hidden}.case-head{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:15px 18px;cursor:pointer;list-style:none;background:rgba(16,37,54,.8)}.case-head::-webkit-details-marker{display:none}.case-head:after{content:'＋';color:var(--cyan);font-size:20px}.case[open]>.case-head:after{content:'−'}.case-title{display:flex;gap:11px;align-items:center;min-width:0}.num{display:grid;place-items:center;width:40px;height:40px;border:1px solid rgba(110,217,239,.4);border-radius:11px;color:var(--cyan);font-weight:850}.case h2{font-size:18px;margin:0}.sub{color:var(--muted);font-size:12px;margin:2px 0 0}.tags{display:flex;flex-wrap:wrap;gap:5px;justify-content:flex-end}.tag{border:1px solid rgba(255,255,255,.15);border-radius:999px;padding:2px 7px;color:var(--muted);font-size:11px}.long{color:#8ef0ba;border-color:rgba(109,229,161,.4)}.short{color:#ffabb2;border-color:rgba(255,135,147,.4)}.win{color:#8ef0ba;border-color:rgba(109,229,161,.4)}.body{padding:18px}.line{display:flex;justify-content:space-between;gap:9px;align-items:baseline;margin-bottom:8px}.label{font-size:13px;font-weight:850;letter-spacing:.05em;color:var(--cyan)}.after .label{color:var(--green)}.hint{color:var(--muted);font-size:12px}.frame{margin:0;overflow:hidden;border:1px solid var(--line);border-radius:11px;background:#0b1219}.frame figcaption,.context figcaption,.after-card figcaption{padding:8px 10px;background:#07111a;color:#c8d9e4;font-size:12px}.shell{position:relative;line-height:0;overflow:hidden;background:#0b1219}.shell img{display:block;width:100%;height:auto}.head-mask{position:absolute;z-index:2;inset:0 0 auto;height:10%;background:#0b1219}.future-mask{position:absolute;z-index:2;top:10%;right:0;bottom:0;left:var(--cut);background:#0b1219}.cut{position:absolute;z-index:3;top:10%;bottom:0;left:var(--cut);border-left:2px dashed #f59e0b}.cut span{position:absolute;top:7px;left:6px;padding:2px 5px;background:#f59e0b;color:#241604;border-radius:4px;font-size:11px;line-height:1.2;white-space:nowrap}.context-title{margin:15px 0 7px;color:var(--yellow);font-size:14px;font-weight:800}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}.context,.after-card{min-width:0;margin:0;overflow:hidden;border:1px solid var(--line);border-radius:11px;background:#07111a}.context img,.after-card img{display:block;width:100%;height:auto}.zoomable{cursor:zoom-in}.questions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px;margin-top:16px}.question{margin:0;padding:12px;border:1px solid rgba(255,255,255,.13);border-radius:10px;background:rgba(255,255,255,.03)}legend{font-size:14px;font-weight:750}.options{display:grid;gap:6px;margin-top:8px}.option{display:flex;gap:7px;padding:7px;border:1px solid rgba(255,255,255,.12);border-radius:8px;background:rgba(255,255,255,.035);font-size:13px;cursor:pointer}.option input{margin-top:4px;accent-color:var(--cyan)}.option strong{color:var(--cyan)}.option.correct{border-color:rgba(109,229,161,.7);background:rgba(109,229,161,.12)}.option.wrong{border-color:rgba(255,135,147,.7);background:rgba(255,135,147,.12)}.answer{display:flex;gap:10px;align-items:center;margin-top:13px}.primary{border:0;border-radius:8px;background:var(--cyan);color:#06141c;padding:9px 12px;font-weight:800;cursor:pointer}.message{color:var(--muted);font-size:12px}.result{display:none;margin-top:12px;padding:11px;border:1px solid rgba(255,255,255,.13);border-radius:9px}.result.show{display:block}.after{display:none;margin-top:19px;padding-top:18px;border-top:1px solid rgba(255,255,255,.12)}.after.show{display:block}.review{display:grid;grid-template-columns:minmax(0,1fr) minmax(220px,.62fr);gap:11px;margin-top:11px}.review-box{padding:12px;border:1px solid rgba(255,255,255,.11);border-radius:9px;background:rgba(5,16,25,.42);font-size:13px;color:#c8d9e4}.review-box h3{font-size:14px;color:var(--yellow);margin:3px 0 5px}.review-box p{margin:4px 0 10px}.actual{background:rgba(44,117,76,.12);border-color:rgba(109,229,161,.3)}.actual h3{color:var(--green)}.actual ul{padding-left:18px;margin:6px 0 0}.next{margin-top:11px}.lightbox{width:min(96vw,1450px);max-height:94vh;padding:44px 15px 15px;background:rgba(3,10,17,.97);border:1px solid rgba(110,217,239,.45);border-radius:14px}.lightbox::backdrop{background:rgba(0,0,0,.78)}.lightbox img{display:block;width:100%;max-height:82vh;object-fit:contain;background:#0b1219}.close{position:absolute;right:9px;top:8px;border:1px solid rgba(255,255,255,.2);border-radius:99px;background:rgba(255,255,255,.08);color:var(--text);font-size:23px;width:34px;height:34px;cursor:pointer}.caption{color:var(--muted);font-size:12px;text-align:center;margin:8px 0 0}footer{margin-top:24px;color:var(--muted);font-size:12px}
@media(max-width:820px){main{width:min(100% - 18px,700px);padding-top:18px}.hero,.questions,.grid,.review{grid-template-columns:1fr}.hero-copy{padding:21px}.toolbar-row{align-items:stretch;flex-direction:column}.result-count{margin-left:0;white-space:normal}.case-head{display:block}.case-head:after{float:right}.tags{justify-content:flex-start;margin-top:8px}.answer{align-items:flex-start;flex-direction:column}}
</style>
</head>
<body>
<main>
<header class="hero"><div class="hero-copy"><div class="eyebrow">WINNING TRADE REVIEW / 09:00 HISTORY → ENTRY → AFTER</div><h1>勝ちトレード復習クイズ</h1><p class="lead">取引履歴CSVと日誌レビューで損益がプラスだった約定から、__CASE_COUNT__ケース・__QUESTION_COUNT__問を収録。勝った理由を「結果」ではなく、チャート・板・キリ番・ダウ理論・225同期の順番で再現してください。</p></div><aside class="score"><h2>今回のスコア</h2><div class="score-number"><span id="score">0</span><small> / __QUESTION_COUNT__</small></div><div class="score-meta"><div><strong id="answered">0</strong><span>回答済み</span></div><div><strong id="perfect">0</strong><span>満点ケース</span></div><div><strong id="visible">__CASE_COUNT__</strong><span>表示中</span></div></div></aside></header>
<div class="notice"><strong>見方：</strong>BEFOREは9:00からENTRYまでを残し、ENTRY以後だけをマスク。AFTER・3分足・同日225はクリックで拡大できます。板読みTools／BrisXの当日データがあるケースは、その有無もヒント欄に表示しています。日誌側で型が分類済みのケースはP1/P2/P3/P4/P10を付けています。</div>
<section class="toolbar"><div class="toolbar-row"><input id="search" type="search" placeholder="日付・銘柄コード・銘柄名・LONG/SHORT・P1で検索"><select id="loss"><option value="all">利益額：すべて</option><option value="large">大きな利益：+5,000円以上</option><option value="small">+5,000円未満</option></select><button class="secondary" id="open-all" type="button">全件開く</button><button class="secondary" id="close-all" type="button">全件閉じる</button><span id="count" class="result-count">__CASE_COUNT__ / __CASE_COUNT__ ケース</span></div><div class="filter-row"><button class="active" data-filter="all" type="button">すべて</button><button data-filter="LONG" type="button">LONG</button><button data-filter="SHORT" type="button">SHORT</button><button data-filter="large" type="button">大きな利益</button>__DATE_BUTTONS____CODE_BUTTONS__</div><p class="toolbar-note">BEFORE画像はマスク漏れを防ぐためクリック拡大しません。AFTER・3分足・同日225はクリックで拡大できます。</p></section>
<section id="list" class="case-list"></section>
<footer>元データ：2026年5月〜8月のfull_day_312_trade_charts manifestと、日誌・取引履歴CSVを照合。成功条件は対象約定の実績損益がプラスであることです。P1/P2/P3/P4/P10は日誌レビュー側の暫定分類を表示しています。板読みTools／BrisXの生データ自体はGitHubへアップロードせず、当日データの有無と再現時の観察ポイントだけをクイズに反映しています。</footer>
</main>
<dialog id="lightbox" class="lightbox"><button id="close" class="close" type="button">×</button><img id="lightbox-image" alt=""><p id="caption" class="caption"></p></dialog>
<script>
const cases=__CASES__,state=new Map();
const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const yen=value=>(Number(value)>=0?'+':'−')+Math.abs(Number(value)||0).toLocaleString('ja-JP')+'円';
const prettyDate=value=>String(value||'').replaceAll('-','/');
const options=(id,q,data)=>Object.entries(data).map(([key,text])=>'<label class="option"><input type="radio" name="'+id+'-'+q+'" value="'+key+'"><span><strong>'+key+'.</strong> '+esc(text)+'</span></label>').join('');
const masked=(src,alt,cut)=>'<div class="shell" style="--cut:'+cut+'%"><img src="'+src+'" alt="'+esc(alt)+'" loading="lazy"><div class="head-mask"></div><div class="future-mask"></div><div class="cut"><span>ENTRY時点</span></div></div>';
function contexts(item){let out='';if(item.chart3m)out+='<figure class="context">'+masked(item.chart3m,item.date+' '+item.code+' 3分足補助',item.cut)+'<figcaption>3分足の方向・押し戻り。ENTRY以後はマスク。</figcaption></figure>';if(item.market)out+='<figure class="context">'+masked(item.market,item.date+' 同日225（1321代理）',item.cut)+'<figcaption>同日225（1321代理）の方向。ENTRY以後はマスク。</figcaption></figure>';return out?'<div class="context-title">BEFORE補助資料：3分足と同日225</div><div class="grid">'+out+'</div>':''}
function afterImages(item){let out='<figure class="after-card"><img class="zoomable" src="'+item.main+'" alt="'+esc(item.date+' '+item.code+' '+item.name+' AFTER全日足')+'" loading="lazy"><figcaption>AFTER：9:00から引けまで。クリックで拡大。</figcaption></figure>';if(item.chart3m)out+='<figure class="after-card"><img class="zoomable" src="'+item.chart3m+'" alt="'+esc(item.date+' '+item.code+' 3分足')+'" loading="lazy"><figcaption>3分足補助。クリックで拡大。</figcaption></figure>';if(item.market)out+='<figure class="after-card"><img class="zoomable" src="'+item.market+'" alt="'+esc(item.date+' 同日225（1321代理）')+'" loading="lazy"><figcaption>同日225（1321代理）。クリックで拡大。</figcaption></figure>';return out}
function hintTags(item){return '<div class="tags"><span class="tag '+item.direction.toLowerCase()+'">'+item.direction+'</span><span class="tag win">'+yen(item.pnl)+'</span><span class="tag">'+esc(item.patternLabel)+'</span><span class="tag">'+(item.board.available?'板データあり':'板データ未添付')+'</span></div>'}
function card(item){const search=[item.date,item.dateDisplay,item.code,item.name,item.direction,item.patternLabel,item.pattern?.id||'',item.pattern?.name||'',item.board.sources.join(' ')].join(' ');return '<details class="case" data-id="'+item.id+'" data-date="'+item.date+'" data-code="'+item.code+'" data-direction="'+item.direction+'" data-pnl="'+item.pnl+'" data-search="'+esc(search)+'"'+(item.number==='001'?' open':'')+'><summary class="case-head"><div class="case-title"><span class="num">'+item.number+'</span><div><h2>'+esc(item.code)+' '+esc(item.name)+'</h2><p class="sub">'+esc(item.dateDisplay)+' '+esc(item.entryTime)+' / ENTRY '+esc(item.entryPrice)+'円 / 実績 '+yen(item.pnl)+'</p></div></div>'+hintTags(item)+'</summary><div class="body"><div class="line"><span class="label">BEFORE / 9:00からENTRYまで</span><span class="hint">右側だけをマスク</span></div><figure class="frame">'+masked(item.main,item.date+' '+item.code+' '+item.name+' 9:00からENTRY',item.cut)+'<figcaption>左側の寄り付き履歴は残しています。縦線より右側がENTRY後です。</figcaption></figure>'+contexts(item)+'<div class="review-box"><h3>見るポイント</h3><p>キリ番：'+esc(item.round.label)+' ／ '+esc(item.dowHint)+'</p><p>板読み：'+esc(item.boardHint)+'</p></div><form class="quiz"><div class="questions"><fieldset class="question"><legend>1. ENTRY後の実績の流れはどれ？</legend><div class="options">'+options(item.id,'q1',item.q1.choices)+'</div></fieldset><fieldset class="question"><legend>2. この勝ちを再現するENTRY前の確認はどれ？</legend><div class="options">'+options(item.id,'q2',item.q2.choices)+'</div></fieldset></div><div class="answer"><button class="primary answer-button" type="button">回答を確定してAFTERを見る</button><span class="message">2問とも選んでから確定してください。</span></div><div class="result"></div></form><section class="after"><div class="line"><span class="label">AFTER / 答え合わせ</span><span class="hint">全日足・3分足・補助資料</span></div><div class="grid">'+afterImages(item)+'</div><div class="review"><div class="review-box"><h3>今回の勝ちの核心</h3><p>'+esc(item.patternReview)+'</p><h3>キリ番・ダウ理論</h3><p>'+esc(item.round.label)+'。'+esc(item.dowHint)+'</p><h3>板読みヒント</h3><p>'+esc(item.boardHint)+'（資料：'+esc(item.board.sources.length?item.board.sources.join('・'):'当日ログなし')+'）</p></div><div class="review-box actual"><h3>実績・出典</h3><ul><li>'+item.direction+' / '+esc(item.qty)+'株</li><li>ENTRY：'+esc(item.entryTime)+' / '+esc(item.entryPrice)+'円</li><li>EXIT：'+esc(item.exitTime)+' / '+esc(item.exitPrice)+'円</li><li>損益：'+yen(item.pnl)+'</li><li>同日グループ損益：'+yen(item.groupPnl)+'</li><li>型：'+esc(item.pattern?.name||'日誌分類なし')+'</li><li>CSV：'+esc(item.source.csv||'manifest集計')+'</li></ul></div></div><button class="secondary next" type="button">次のケースへ</button></section></div></details>'}
function selected(form,name){const el=form.querySelector('input[name="'+name+'"]:checked');return el?el.value:''}
function stats(){let score=0,answered=0,perfect=0;for(const result of state.values()){answered++;score+=result.score;if(result.score===2)perfect++}document.getElementById('score').textContent=score;document.getElementById('answered').textContent=answered;document.getElementById('perfect').textContent=perfect}
function answer(item,cardEl,form){const a1=selected(form,item.id+'-q1'),a2=selected(form,item.id+'-q2'),box=cardEl.querySelector('.result'),message=form.querySelector('.message');if(!a1||!a2){message.textContent='2問とも選んでください。';return}const score=Number(a1===item.q1.answer)+Number(a2===item.q2.answer);state.set(item.id,{score});stats();form.querySelectorAll('.option').forEach(option=>{const input=option.querySelector('input'),question=input.name.endsWith('-q1')?item.q1:item.q2;if(input.value===question.answer)option.classList.add('correct');if(input.checked&&input.value!==question.answer)option.classList.add('wrong');input.disabled=true});message.textContent='回答を記録しました。AFTERで答え合わせしてください。';box.innerHTML='<strong>'+score+' / 2 問正解</strong><p>正解：1問目 '+item.q1.answer+' / 2問目 '+item.q2.answer+'。勝った理由は結果ではなく、ENTRY前の条件がそろったかで振り返ります。</p>';box.classList.add('show');cardEl.querySelector('.after').classList.add('show')}
document.getElementById('list').innerHTML=cases.map(card).join('');
const cards=[...document.querySelectorAll('.case')],search=document.getElementById('search'),loss=document.getElementById('loss'),count=document.getElementById('count'),visible=document.getElementById('visible');let active='all';
function apply(){const query=search.value.trim().toLowerCase(),lossMode=loss.value;let n=0;cards.forEach(cardEl=>{const data=cardEl.dataset,hitQuery=!query||data.search.toLowerCase().includes(query),hitFilter=active==='all'||data.direction===active||active==='large'&&Number(data.pnl)>=5000||active.startsWith('date:')&&data.date===active.slice(5)||active.startsWith('code:')&&data.code===active.slice(5),hitLoss=lossMode==='all'||lossMode==='large'&&Number(data.pnl)>=5000||lossMode==='small'&&Number(data.pnl)<5000,show=hitQuery&&hitFilter&&hitLoss;cardEl.classList.toggle('is-hidden',!show);if(show)n++});count.textContent=n+' / '+cards.length+' ケース';visible.textContent=n}
search.addEventListener('input',apply);loss.addEventListener('change',apply);document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{active=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(item=>item.classList.toggle('active',item===button));apply()}));document.getElementById('open-all').addEventListener('click',()=>cards.filter(item=>!item.classList.contains('is-hidden')).forEach(item=>{item.open=true}));document.getElementById('close-all').addEventListener('click',()=>cards.forEach(item=>{item.open=false}));cards.forEach(cardEl=>{const item=cases.find(value=>value.id===cardEl.dataset.id);const form=cardEl.querySelector('form');cardEl.querySelector('.answer-button').addEventListener('click',()=>answer(item,cardEl,form));cardEl.querySelector('.next').addEventListener('click',()=>{const current=cards.indexOf(cardEl);const next=cards.slice(current+1).find(value=>!value.classList.contains('is-hidden'))||cards.find(value=>!value.classList.contains('is-hidden'));if(next){cardEl.open=false;next.open=true;next.scrollIntoView({behavior:'smooth',block:'start'})}})});
const lightbox=document.getElementById('lightbox'),lightboxImage=document.getElementById('lightbox-image'),caption=document.getElementById('caption');document.addEventListener('click',event=>{const image=event.target.closest('.zoomable');if(!image)return;lightboxImage.src=image.src;lightboxImage.alt=image.alt;caption.textContent=image.alt+'｜クリックまたはEscで閉じる';lightbox.showModal()});document.getElementById('close').addEventListener('click',()=>lightbox.close());lightbox.addEventListener('click',event=>{if(event.target===lightbox)lightbox.close()});
apply();
</script>
</body>
</html>`
  .replace(/__CASE_COUNT__/g, String(cases.length))
  .replace(/__QUESTION_COUNT__/g, String(cases.length * 2))
  .replace(/__DATE_BUTTONS__/g, dateButtons)
  .replace(/__CODE_BUTTONS__/g, codeButtons)
  .replace(/__CASES__/g, JSON.stringify(cases));

fs.writeFileSync(path.join(OUT_DIR, 'index.html'), html, 'utf8');
const manifest = {
  generatedAt: new Date().toISOString(),
  period: fullDay.period,
  caseCount: cases.length,
  questionCount: cases.length * 2,
  positiveTradePnl: cases.reduce((sum, item) => sum + item.pnl, 0),
  diaryPatternCaseCount: diaryPatternCount,
  boardDataCaseCount: boardCaseCount,
  sameDay225CaseCount: marketCaseCount,
  assets: {
    mainCharts: copied.charts.size,
    contextCharts: copied.context.size,
    marketCharts: copied.market.size,
  },
  cases: cases.map((item) => ({
    id: item.id,
    date: item.date,
    code: item.code,
    name: item.name,
    direction: item.direction,
    entryTime: item.entryTime,
    exitTime: item.exitTime,
    entryPrice: item.entryPrice,
    exitPrice: item.exitPrice,
    pnl: item.pnl,
    pattern: item.pattern,
    board: item.board,
    source: item.source,
  })),
};
fs.writeFileSync(path.join(OUT_DIR, 'source_manifest.json'), JSON.stringify(manifest, null, 2), 'utf8');
console.log(JSON.stringify({output: path.join(OUT_DIR, 'index.html'), caseCount: cases.length, questionCount: cases.length * 2, positiveTradePnl: manifest.positiveTradePnl, diaryPatternCaseCount: diaryPatternCount, boardDataCaseCount: boardCaseCount, sameDay225CaseCount: marketCaseCount, assets: manifest.assets}, null, 2));

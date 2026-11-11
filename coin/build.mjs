// 코인 탭 데이터 생성 + ntfy 알림 (GitHub Actions에서 4시간마다 실행)
//   node coin/build.mjs [이전 coin.json 경로]     → docs/coinhist.json, docs/coin.json 생성, 새 신호만 알림
//   node coin/build.mjs --fail <실행기록 URL>      → 실패 알림
//   NTFY_DRY=1 이면 알림을 보내지 않고 화면에만 출력
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CE = createRequire(import.meta.url)(path.join(ROOT, "docs/coin-engine.js"));
const CFG = JSON.parse(fs.readFileSync(path.join(ROOT, "coin/config.json"), "utf8"));
const ROOTCFG = JSON.parse(fs.readFileSync(path.join(ROOT, "config.json"), "utf8"));
const TOPIC = process.env.NTFY_TOPIC_COIN || ROOTCFG.ntfy_topic_coin || ROOTCFG.ntfy_topic;   // 코인 알림 전용 주제(주식과 분리)
const REG = TOPIC + "-reg";             // 앱의 '알림 받기'가 내 전략 목록을 보내는 주제(구독할 필요 없음)
const DRY = !!process.env.NTFY_DRY;
const START = "2012-01-01";                 // 데이터 시작(365일선·2년 백분위 준비 기간 포함)
const BT4H_FROM = Date.UTC(2018, 0, 1);
const SYM = { BTC: "BTCUSDT", ETH: "ETHUSDT", SOL: "SOLUSDT", XRP: "XRPUSDT" };
const NAME = { BTC: "비트코인", ETH: "이더리움", SOL: "솔라나", XRP: "리플" };
const BIN = "https://data-api.binance.vision/api/v3/klines";   // 시세 전용 주소(미국 서버에서도 열림)
const CM = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics";
const H4 = 4 * 3600e3;
const UT4 = CFG.utbot4h || CFG.utbot;   // 4시간봉 UT 설정(없으면 일봉 설정)
const ut4Of = s => ({ key: (UT4.coins?.[s] || UT4).key, atr: (UT4.coins?.[s] || UT4).atr });   // 코인별 설정이 있으면 그것

const sleep = ms => new Promise(r => setTimeout(r, ms));
const isn = CE.isn;
const kst = ms => new Date(ms + 9 * 3600e3).toISOString().slice(0, 16).replace("T", " ");
const usd = v => "$" + (v >= 100 ? Math.round(v).toLocaleString("en-US") : v >= 1 ? v.toFixed(2) : v.toFixed(4));
const sig = (x, d = 6) => (isn(x) ? Number(Number(x).toPrecision(d)) : null);

async function getJSON(url, tries = 4) {
  for (let k = 1; ; k++) {
    try {
      const r = await fetch(url, { headers: { "User-Agent": "hwandong-quant" }, signal: AbortSignal.timeout(40000) });
      if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);
      return await r.json();
    } catch (e) {
      if (k >= tries) throw new Error(`${url.split("?")[0]} 실패: ${e.message}`);
      await sleep(2000 * k);
    }
  }
}
// 바이낸스 캔들 전체(완성된 봉만): [{t:시작ms, T:마감ms, o,h,l,c}]
async function klines(sym, interval, from) {
  const out = [];
  let t = from;
  for (;;) {
    const a = await getJSON(`${BIN}?symbol=${sym}&interval=${interval}&startTime=${t}&limit=1000`);
    for (const k of a) out.push({ t: k[0], T: k[6], o: +k[1], h: +k[2], l: +k[3], c: +k[4] });
    if (a.length < 1000) break;
    t = a[a.length - 1][0] + 1;
  }
  const now = Date.now();
  return out.filter(k => k.T < now);
}
async function coinmetrics(asset, metrics) {
  const rows = [];
  let url = `${CM}?assets=${asset}&metrics=${metrics}&frequency=1d&start_time=2011-01-01&page_size=10000`;
  while (url) {
    const d = await getJSON(url);
    rows.push(...d.data);
    url = d.next_page_url;
  }
  return rows;
}
async function send(title, message, { click, tags, priority = 3 } = {}) {
  if (DRY || !TOPIC) { console.log(`\n[알림${DRY ? " (DRY)" : ""}] ${title}\n${message}\n`); return; }
  const body = { topic: TOPIC, title, message: message.slice(0, 3800), priority, tags: tags || [] };
  if (click) body.click = click;
  try {
    const r = await fetch("https://ntfy.sh/", { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });
    if (!r.ok) console.log("ntfy 실패", r.status, await r.text());
  } catch (e) { console.log("ntfy 실패", e.message); }
}

// ---------------------------------------------------------------- 실패 알림 모드
if (process.argv[2] === "--fail") {
  await send("환동퀀트 코인 갱신 실패", "코인 데이터 갱신이 실패했습니다. 눌러서 실행 기록을 확인하세요.", { click: process.argv[3], tags: ["warning"], priority: 4 });
  process.exit(0);
}

// ---------------------------------------------------------------- 1) 일봉 + 온체인 → coinhist.json
const prevPath = process.argv[2];
let prev = null;
try { if (prevPath && fs.existsSync(prevPath)) prev = JSON.parse(fs.readFileSync(prevPath, "utf8")); } catch { prev = null; }
if (prev && !prev.state) prev = null;

console.log("바이낸스 일봉…");
const D = {};
for (const s of CE.COINS) { D[s] = await klines(SYM[s], "1d", Date.UTC(2017, 0, 1)); console.log(`  ${s} ${D[s].length}일`); }
const lastDay = new Date(D.BTC[D.BTC.length - 1].t).toISOString().slice(0, 10);
const n = CE.idxOf(START, lastDay) + 1;
const nul = () => new Array(n).fill(null);
const at = ms => CE.idxOf(START, new Date(ms).toISOString().slice(0, 10));

console.log("Coin Metrics 온체인…");
const cm = {
  btc: await coinmetrics("btc", "PriceUSD,CapMVRVCur,HashRate,IssTotUSD,FlowInExNtv,FlowOutExNtv,SplyExNtv"),
  eth: await coinmetrics("eth", "PriceUSD,CapMVRVCur,FlowInExNtv,FlowOutExNtv,SplyExNtv"),
  xrp: await coinmetrics("xrp", "PriceUSD,CapMVRVCur"),
};
const cmLast = cm.btc.filter(r => isn(+r.CapMVRVCur) && r.CapMVRVCur !== undefined).slice(-1)[0]?.time.slice(0, 10);
function series(rows, f) {
  const a = nul();
  for (const r of rows) { const i = CE.idxOf(START, r.time.slice(0, 10)); if (i < 0 || i >= n) continue; const v = f(r); if (isn(v)) a[i] = v; }
  return a;
}
const num = k => r => (r[k] === undefined || r[k] === null || r[k] === "" ? null : +r[k]);

const coins = {};
for (const s of CE.COINS) {
  const c = s === "SOL" ? nul() : series(cm[s.toLowerCase()], num("PriceUSD")).map(v => sig(v));   // 바이낸스 상장 전은 Coin Metrics 종가
  const h = nul(), l = nul();
  for (const k of D[s]) { const i = at(k.t); if (i < 0 || i >= n) continue; c[i] = k.c; h[i] = k.h; l[i] = k.l; }
  coins[s] = { c, h, l, bin: new Date(D[s][0].t).toISOString().slice(0, 10) };
}
const net = r => (isn(+r.FlowInExNtv) && isn(+r.FlowOutExNtv) && r.FlowInExNtv != null && r.FlowOutExNtv != null ? Math.round(r.FlowInExNtv - r.FlowOutExNtv) : null);
const oc = {
  btc: {
    mvrv: series(cm.btc, num("CapMVRVCur")).map(v => sig(v, 5)),
    hash: series(cm.btc, r => (r.HashRate == null ? null : r.HashRate / 1e6)).map(v => sig(v, 4)),       // EH/s
    iss: series(cm.btc, r => (r.IssTotUSD == null ? null : r.IssTotUSD / 1e6)).map(v => sig(v, 4)),      // 백만 달러
    net: series(cm.btc, net),
    exs: series(cm.btc, num("SplyExNtv")).map(v => (isn(v) ? Math.round(v) : null)),
  },
  eth: {
    mvrv: series(cm.eth, num("CapMVRVCur")).map(v => sig(v, 5)),
    net: series(cm.eth, net),
    exs: series(cm.eth, num("SplyExNtv")).map(v => (isn(v) ? Math.round(v) : null)),
  },
  xrp: { mvrv: series(cm.xrp, num("CapMVRVCur")).map(v => sig(v, 5)) },
};
const H = { v: 1, start: START, n, last: lastDay, cmLast, generated: kst(Date.now()), cfg: { utKey: CFG.utbot.key, utAtr: CFG.utbot.atr, ut4Key: UT4.key, ut4Atr: UT4.atr, ut4: Object.fromEntries(CE.COINS.map(s => [s, ut4Of(s)])), start: CFG.start, btOnly: CFG.backtest_only }, coins, oc };


// ---------------------------------------------------------------- 2) 4시간봉 UT Bot (현재 상태 + 2018년부터 백테스트)
console.log("바이낸스 4시간봉…");
const ut4h = {}, bt4h = {}, B4 = {};
function bt(k, pos, lev) {
  let i0 = k.findIndex(b => b.t >= BT4H_FROM); if (i0 < 0) i0 = 0;
  let eq = 1, pk = 1, mdd = 0, inn = false, entry = 0, trades = 0, wins = 0, bars = 0, inBars = 0;
  for (let i = i0; i < k.length - 1; i++) {
    const want = pos[i] === 1;
    if (want && !inn) { eq *= 1 - CE.COST; inn = true; entry = eq; trades++; }
    else if (!want && inn) { eq *= 1 - CE.COST; inn = false; if (eq > entry) wins++; }
    if (inn) { const r = k[i + 1].c / k[i].c - 1; eq *= lev ? Math.max(0, 1 + 2 * r - CE.LEV_COST / 6) : 1 + r; inBars++; }
    bars++; pk = Math.max(pk, eq); mdd = Math.min(mdd, eq / pk - 1);
  }
  const yrs = bars / (6 * 365);
  return { cagr: (Math.pow(eq, 1 / yrs) - 1) * 100, mdd: mdd * 100, tot: (eq - 1) * 100, trades, tpy: trades / yrs, win: trades ? (wins / Math.max(1, trades - (inn ? 1 : 0))) * 100 : 0, expo: (inBars / bars) * 100 };
}
function holdStats(k, lev) {
  let i0 = k.findIndex(b => b.t >= BT4H_FROM); if (i0 < 0) i0 = 0;
  let eq = 1 - CE.COST, pk = eq, mdd = 0, bars = 0;
  for (let i = i0; i < k.length - 1; i++) { const r = k[i + 1].c / k[i].c - 1; eq *= lev ? Math.max(0, 1 + 2 * r - CE.LEV_COST / 6) : 1 + r; pk = Math.max(pk, eq); mdd = Math.min(mdd, eq / pk - 1); bars++; }
  return { cagr: (Math.pow(eq, 1 / (bars / 2190)) - 1) * 100, mdd: mdd * 100 };
}
for (const s of CE.COINS) {
  const k = await klines(SYM[s], "4h", Date.UTC(2017, 11, 1));
  B4[s] = k;
  const u4c = ut4Of(s), u = CE.utbot(k.map(b => b.c), k.map(b => b.h), k.map(b => b.l), u4c.key, u4c.atr);
  const L = k.length - 1;
  let since = L; while (since > 0 && u.pos[since - 1] === u.pos[L]) since--;
  ut4h[s] = { pos: u.pos[L], stop: sig(u.stop[L]), px: k[L].c, bar: kst(k[L].T + 1), since: kst(k[since].T + 1), sincePx: k[since].c, pos_: u.pos };
  bt4h[s] = { from: new Date(Math.max(BT4H_FROM, k[0].t)).toISOString().slice(0, 10), x1: bt(k, u.pos, false), x2: bt(k, u.pos, true), hold: holdStats(k, false), hold2: holdStats(k, true) };
  // 일봉 마감 시점(한국시간 09시)의 4시간봉 UT 상태 → 조합 전략용 (그날 마지막 4시간봉 값)
  const u4 = nul();
  for (let j = 0; j < k.length; j++) { const i = at(k[j].t); if (i >= 0 && i < n && isn(u.pos[j])) u4[i] = u.pos[j]; }
  coins[s].u4 = u4;
  console.log(`  ${s} ${k.length}봉 · UT ${u.pos[L] === 1 ? "매수" : "매도"} 중 (${ut4h[s].since}~)`);
}

// ---------------------------------------------------------------- 저장 (일봉·온체인·4시간봉 UT 상태)
function pack(a) {
  if (!a) return null;
  let o = a.findIndex(isn); if (o < 0) return { o: 0, v: [] };
  let e = a.length - 1; while (e >= 0 && !isn(a[e])) e--;
  return { o, v: a.slice(o, e + 1) };
}
const packed = { ...H, coins: {}, oc: {} };
for (const s of CE.COINS) packed.coins[s] = { c: pack(coins[s].c), h: pack(coins[s].h), l: pack(coins[s].l), u4: pack(coins[s].u4), bin: coins[s].bin };
for (const a in oc) { packed.oc[a] = {}; for (const k in oc[a]) packed.oc[a][k] = pack(oc[a][k]); }
fs.writeFileSync(path.join(ROOT, "docs/coinhist.json"), JSON.stringify(packed));
console.log(`coinhist.json ${(fs.statSync(path.join(ROOT, "docs/coinhist.json")).size / 1024).toFixed(0)}KB · 마지막 일봉 ${lastDay} · 온체인 ${cmLast}`);

const X = CE.prep(H);

// ---------------------------------------------------------------- 3) 새 신호 찾기
const events = [];
const lastI = X.n - 1;
const prevLast = prev?.state?.lastDay ? CE.idxOf(START, prev.state.lastDay) : null;
const presets = CFG.alert_presets.map(id => CE.PRESETS.find(p => p.id === id)).filter(Boolean);
const status = {};
for (const s of CFG.alert_coins) {
  status[s] = {};
  for (const p of presets) {
    const res = CE.simulate(X, { ...p, coins: { [s]: 1 }, start: CFG.start });
    const st = CE.status(X, res)[s];
    status[s][p.id] = st;
    if (prev && prevLast !== null) {
      for (const t of res.trades) if (t.i > prevLast) {
        events.push({ pri: 4, line: `${s} · ${p.name}: ${t.a === "buy" ? "매수" : "매도"} ${t.k} · ${usd(t.px)} (${t.why}) [${CE.dateAt(START, t.i).slice(5)} 종가]` });
      }
    }
  }
}
// 내 조합 전략 알림: 앱에서 '알림 받기'를 켜면 전략 목록 전체를 REG 주제로 보낸다 → 가장 최근 목록을 받아 매일 계산
let mine = prev?.state?.mine || { t: 0, h: null, list: [] };
try {
  const r = await fetch(`https://ntfy.sh/${REG}/json?poll=1&since=all`, { signal: AbortSignal.timeout(20000) });
  for (const ln of (await r.text()).split("\n")) {
    let m, d; try { m = JSON.parse(ln); d = JSON.parse(m.message); } catch { continue; }
    if (m.event !== "message" || !(m.time > mine.t) || d?.v !== 1 || typeof d.h !== "string" || !Array.isArray(d.list)) continue;
    mine = { t: m.time, h: d.h, list: d.list.slice(0, 12) };
  }
} catch (e) { console.log("내 전략 등록 확인 실패:", e.message); }
const mineChanged = !!prev && mine.h !== (prev.state.mine?.h ?? null);
const mineNow = [], mineEv = [];   // mineEv: 전략별 새 매매 → 전략마다 따로 알림(제목 = 전략 이름)
for (const m of mine.list) {
  try {
    const res = m.type === "mix" ? CE.mix(X, (m.items || []).map(it => ({ w: +it.w || 0, P: it.P })), +m.start || CFG.start) : CE.simulate(X, m.P);
    if (!res) continue;
    const tr = prev && prevLast !== null ? res.trades.filter(t => t.i > prevLast) : [];
    if (tr.length) mineEv.push({ name: m.name, tr });
    if (m.type === "mix") { mineNow.push(`${m.name}: 묶음 ${(m.items || []).length}개 전략`); continue; }
    const st = CE.status(X, res);
    mineNow.push(`${m.name}: ` + Object.keys(st).map(s => { const x = st[s]; return `${s} ${x.hold > 0.001 ? "보유 " + Math.round(x.hold * 100) + "%" : "현금"}${x.sell ? " · 매도 조건 충족" : x.buy ? " · 매수 조건 충족" : ""}`; }).join(" / "));
  } catch (e) { console.log(`내 전략 ${m.name} 계산 실패:`, e.message); }
}
console.log(`내 전략 알림 ${mine.list.length}개${mineChanged ? " (새로 바뀜)" : ""}`);

const pc = CE.lastVal(CE.pctOf(X, "btc", CE.REGIME_W)), mv = CE.lastVal(X.oc.mvrv.btc);
const regime = CE.regimeOf(pc.v);
const regimeLine = `BTC MVRV ${mv.v?.toFixed(2)} · 최근 4년 중 하위 ${Math.round(pc.v)}%`;
if (prev && prev.state.regime && prev.state.regime !== regime) events.push({ pri: 4, line: `온체인 국면: ${prev.state.regime} → ${regime} (${regimeLine})` });
const utState = {};
for (const s of CE.COINS) {
  const k = B4[s], u = ut4h[s];
  utState[s] = { t: k[k.length - 1].T, pos: u.pos };
  if (!CFG.alert_ut4h.includes(s) || !prev?.state?.ut4h?.[s]) continue;
  const pt = prev.state.ut4h[s].t;
  let flip = null;
  for (let i = 1; i < k.length; i++) if (k[i].T > pt && u.pos_[i] !== u.pos_[i - 1] && (u.pos_[i] === 1 || u.pos_[i] === -1)) flip = i;
  if (flip !== null) events.push({ pri: 3, line: `${s} · UT Bot 4시간봉 ${u.pos_[flip] === 1 ? "매수" : "매도"} 전환 · ${usd(k[flip].c)} (${kst(k[flip].T + 1).slice(5)} 마감 봉)` });
}

// ---------------------------------------------------------------- 4) 알림
const click = CFG.site + "#coin";
const alerts = (prev?.alerts || []).slice();
const hello = !prev || prev.cfg?.topic !== TOPIC;   // 첫 실행 또는 알림 주제가 바뀐 뒤 첫 실행
if (hello) {
  const lines = [`온체인 국면: ${regime} (${regimeLine})`];
  for (const s of CFG.alert_coins) {
    const u = ut4h[s];
    const ps = presets.map(p => { const st = status[s][p.id]; return `${p.name} ${st.hold > 0 ? "보유 " + Math.round(st.hold * 100) + "%" : st.buy ? "매수 조건 충족" : "대기"}`; }).join(" · ");
    lines.push(`${s} ${usd(X.coins[s].c[lastI])}${CFG.alert_ut4h.includes(s) ? ` · UT 4h ${u.pos === 1 ? "매수" : "매도"} 중` : ""} · ${ps}`);
  }
  lines.push("앞으로 새 신호가 생길 때만 알림이 옵니다.");
  if (mine.list.length) lines.push("내 전략 알림: " + mine.list.map(m => m.name).join(", "));
  await send("환동퀀트 코인 알림 연결됨", lines.join("\n"), { click, tags: ["coin"] });
  alerts.unshift({ t: kst(Date.now()), title: "알림 연결됨", lines });
}
if (mineChanged) {
  const lines = mine.list.length ? ["매일 09시 일봉 마감 후 매수·매도가 생기면 알려드립니다.", ...mineNow] : ["내 전략 알림이 모두 꺼졌습니다."];
  const title = mine.list.length ? `내 전략 알림 설정 · ${mine.list.length}개` : "내 전략 알림 꺼짐";
  await send(title, lines.join("\n"), { click, tags: ["bell"] });
  alerts.unshift({ t: kst(Date.now()), title, lines });
}
for (const e of mineEv.slice(0, 8)) {
  const t0 = e.tr[e.tr.length - 1], one = e.tr.length === 1, buy = e.tr.every(t => t.a === "buy"), sell = e.tr.every(t => t.a === "sell");
  const title = `${e.name} · ` + (one ? `${t0.s} ${t0.a === "buy" ? "매수" : "매도"} ${t0.k}` : `${buy ? "매수" : sell ? "매도" : "매매"} ${e.tr.length}건`);
  const lines = e.tr.map(t => `${t.s} ${t.a === "buy" ? "매수" : "매도"} ${t.k} · ${usd(t.px)}${t.src ? ` · ${t.src}` : ""} (${t.why}) [${CE.dateAt(START, t.i).slice(5)} 종가]`);
  await send(title, lines.join("\n"), { click, tags: [buy ? "chart_with_upwards_trend" : sell ? "chart_with_downwards_trend" : "bell"], priority: 4 });
  alerts.unshift({ t: kst(Date.now()), title: `[내 전략] ${title}`, lines });
}
if (prev && events.length) {
  const lines = events.map(e => e.line);
  await send(`환동퀀트 코인 · 새 신호 ${events.length}건`, lines.join("\n"), { click, tags: ["coin"], priority: Math.max(...events.map(e => e.pri)) });
  alerts.unshift({ t: kst(Date.now()), title: `새 신호 ${events.length}건`, lines });
} else console.log("새 신호 없음");

// ---------------------------------------------------------------- 5) coin.json
for (const s in ut4h) delete ut4h[s].pos_;
const out = {
  v: 1, generated: kst(Date.now()), lastDay, cmLast, ut4h, bt4h, alerts: alerts.slice(0, 30),
  mine: { h: mine.h, t: mine.t, names: mine.list.map(m => m.name) },
  cfg: { topic: TOPIC, reg: REG, alert_ut4h: CFG.alert_ut4h, alert_presets: CFG.alert_presets, alert_coins: CFG.alert_coins, backtest_only: CFG.backtest_only, start: CFG.start, utbot: CFG.utbot, utbot4h: UT4 },
  state: { lastDay, regime, ut4h: utState, mine },
};
fs.writeFileSync(path.join(ROOT, "docs/coin.json"), JSON.stringify(out));
console.log(`coin.json 저장 · 국면 ${regime} (${regimeLine}) · 이벤트 ${events.length}건`);

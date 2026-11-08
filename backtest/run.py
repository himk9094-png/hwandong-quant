"""월 1회 백테스트 → docs/backtest.json (GitHub Actions에서 실행, Claude 토큰 사용 없음).

  python -m backtest.run            # DART_API_KEY 필요

데이터: 네이버 일봉(코스피 보통주, 2019년 7월~), DART 연간 재무(FY2019~, 공시 다음 해 4월부터 사용).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from pipeline import config, dart, naver, ntfy

ROOT = config.ROOT
CACHE = ROOT / "cache"
BT = CACHE / "bt"
W1 = ("20201101", "20231031")
COST, CASH_R = 0.0015, 0.025 / 250


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- 데이터
def load_data(today: dt.date):
    BT.mkdir(parents=True, exist_ok=True)
    (BT / "px").mkdir(exist_ok=True)
    src = naver.NaverSource(CACHE / "naver")
    lst = src.listing()
    k = lst[(lst.mkt == "KOSPI") & (lst.index.str[-1] == "0") & ~lst.name.str.contains("스팩|리츠|인프라")]
    n = int((today - dt.date(2019, 7, 1)).days * 5 / 7) + 30
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"

    def px(code):
        for i in range(3):
            try:
                r = s.get(naver.CHART_URL, params={"symbol": code, "timeframe": "day", "count": n, "requestType": 0}, timeout=20)
                df = pd.DataFrame(naver.ITEM.findall(r.text), columns=naver.HIST_COLS)
                df.to_csv(BT / "px" / f"{code}.csv", index=False)
                return
            except Exception:
                time.sleep(1 + i)

    t = time.time()
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(px, list(k.index) + ["KOSPI"]))
    log(f"일봉 {len(k)}종목 ({time.time() - t:.0f}초)")

    # DART 연간 재무 (한 번 받은 해는 캐시에 영구 보관)
    dc = dart.DartClient(os.environ.get("DART_API_KEY"))
    cc_p = CACHE / "dart" / "corp_codes.json"
    if cc_p.exists():
        cc = json.loads(cc_p.read_text(encoding="utf-8"))
    else:
        cc = dc.corp_codes()
        cc_p.parent.mkdir(parents=True, exist_ok=True)
        cc_p.write_text(json.dumps(cc), encoding="utf-8")
    pairs = [(c, cc[c]) for c in k.index if c in cc]
    c2s = {v: c for c, v in pairs}
    batches = [[v for _, v in pairs[i:i + 100]] for i in range(0, len(pairs), 100)]
    last_fy = today.year - 1 if today >= dt.date(today.year, 4, 15) else today.year - 2
    fin = {}
    for fy in range(2019, last_fy + 1):
        p = BT / f"dart_{fy}.json"
        if not p.exists() or (fy == last_fy and time.time() - p.stat().st_mtime > 25 * 86400):
            t = time.time()
            with ThreadPoolExecutor(5) as ex:
                parts = list(ex.map(lambda b: dc.multi(b, fy, "11011"), batches))
            parsed = dart.parse_period([x for pt in parts for x in pt], "11011", c2s)
            p.write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
            log(f"DART FY{fy}: {len(parsed)}개사 ({time.time() - t:.0f}초)")
        d = json.loads(p.read_text(encoding="utf-8"))
        fin[fy] = pd.DataFrame({c: {f: (v or {}).get(f) for f in ("ni", "op", "rev", "liab", "assets", "equity")}
                                for c, v in d.items()}).T.astype(float)
    return k, fin, src


class Market:
    def __init__(self, k, fin):
        fr = []
        for code in k.index:
            p = BT / "px" / f"{code}.csv"
            if not p.exists():
                continue
            d = pd.read_csv(p, dtype={"date": str})
            if len(d) < 60:
                continue
            d["code"] = code
            fr.append(d)
        D = pd.concat(fr)
        D = D[D.close > 0]
        P = {c: D.pivot(index="date", columns="code", values=c).sort_index().astype(float)
             for c in ("open", "high", "low", "close", "vol")}
        self.C = P["close"].ffill(limit=5)
        self.O = P["open"].where(P["open"] > 0)
        self.H, self.L, self.V = P["high"].ffill(limit=5), P["low"].ffill(limit=5), P["vol"]
        self.dates = list(self.C.index)
        self.T = len(self.dates)
        kx = pd.read_csv(BT / "px" / "KOSPI.csv", dtype={"date": str}).set_index("date")
        self.kospi = kx["close"].reindex(self.dates)
        sh = k["shares"].reindex(self.C.columns)
        self.mcap = self.C * sh
        self.val = P["close"] * self.V
        self.val5 = self.val.rolling(5, min_periods=3).mean()
        self.tv20 = self.val.rolling(20, min_periods=10).mean()
        self.fin = fin

    def first_on_or_after(self, ymd):
        for j, d in enumerate(self.dates):
            if d >= ymd:
                return j
        return None

    def last_on_or_before(self, ymd):
        return max(j for j, d in enumerate(self.dates) if d <= ymd)

    def fin_at(self, d):
        fy = int(d[:4]) - 1 if d[4:] >= "0401" else int(d[:4]) - 2
        return self.fin.get(fy, pd.DataFrame(columns=["ni", "op", "rev", "liab", "assets", "equity"])).reindex(self.C.columns)

    def fin_panel(self, field):
        out = pd.DataFrame(np.nan, index=self.dates, columns=self.C.columns)
        for fy, f in sorted(self.fin.items()):
            s = f[field].reindex(self.C.columns) if field in f else pd.Series(np.nan, index=self.C.columns)
            m = out.index >= f"{fy + 1}0401"
            out.loc[m] = np.where(s.notna().values, s.values, out.loc[m].values)
        return out


def pct(s, asc=True):
    return s.rank(pct=True, ascending=asc)


def fmt_eq(eq):
    yrs = len(eq) / 250
    return f"{((eq.iloc[-1] / 1e7) ** (1 / yrs) - 1) * 100:+.0f}%/년|{eq.iloc[-1] / 1e4:,.0f}만 · 낙폭 {(eq / eq.cummax() - 1).min() * 100:.0f}%"


# ---------------------------------------------------------------- 계절성 전략 (A안·대형주·지수)
def season_strats(M: Market, windows):
    C, O, V = M.C, M.O, M.V

    def a_select(i, exclude=()):
        d = M.dates[i]
        f = M.fin_at(d)
        m, c = M.mcap.iloc[i], C.iloc[i]
        ok = (m >= 750e8) & (c >= 1500) & (M.val5.iloc[i] >= 3e7) & (V.iloc[i] > 0) & (f["ni"] > 0)
        ev = m + f["liab"]
        g1ok = ok & (f["op"] > 0) & (ev > 0)
        s1 = pct((ev / f["op"])[g1ok], True) + pct((f["ni"] / f["assets"])[g1ok], False)
        g1 = list(s1.sort_values(ascending=False).index[:8])
        r1y = C.iloc[i] / C.iloc[i - 252] - 1 if i >= 252 else pd.Series(np.nan, index=C.columns)
        g2ok = ok & r1y.notna() & ~C.columns.isin(list(exclude) + g1)
        s2 = pct(m[g2ok], False) + pct(r1y[g2ok], True)
        return g1, s2.sort_values(ascending=False)

    def px(i, c):
        p = O.iloc[i][c]
        return p if not np.isnan(p) else C.iloc[i - 1][c]

    def run_aplan(start, end):
        cash, hold, vals = 1e7, {}, []
        i0, i1 = M.first_on_or_after(start), M.last_on_or_before(end)
        ev = {}
        for y in range(int(start[:4]), int(end[:4]) + 1):
            for tag, ymd in (("buy", f"{y}1101"), ("jan", f"{y + 1}0201"), ("sell", f"{y + 1}0501")):
                j = M.first_on_or_after(ymd)
                if j is not None:
                    ev[j] = tag
        g1 = []
        for i in range(i0, i1 + 1):
            e = ev.get(i)
            if e == "sell" and hold:
                for c, n in hold.items():
                    p = px(i, c)
                    cash += 0 if np.isnan(p) else n * p * (1 - COST)
                hold = {}
            if e == "buy" and not hold:
                g1, r2 = a_select(i - 1)
                picks = g1 + list(r2.index[:7])
                w = cash / len(picks)
                for c in picks:
                    p = px(i, c)
                    if np.isnan(p) or p <= 0:
                        continue
                    hold[c] = w * (1 - COST) / p
                    cash -= w
            if e == "jan" and hold:
                _, r2 = a_select(i - 1, exclude=g1)
                top7 = list(r2.index[:7])
                out = [c for c in hold if c not in g1 and c not in top7]
                proceeds = 0
                for c in out:
                    p = px(i, c)
                    proceeds += 0 if np.isnan(p) else hold.pop(c) * p * (1 - COST)
                new = [c for c in top7 if c not in hold][:len(out)]
                for c in new:
                    p = px(i, c)
                    if not np.isnan(p) and p > 0:
                        hold[c] = proceeds / len(new) * (1 - COST) / p
                if not new:
                    cash += proceeds
            if not hold:
                cash *= 1 + CASH_R
            vals.append(cash + sum(n * (C.iloc[i][c] if not np.isnan(C.iloc[i][c]) else 0) for c, n in hold.items()))
        return pd.Series(vals, index=M.dates[i0:i1 + 1])

    def top100(i):
        m = M.mcap.iloc[i].where((V.iloc[i] > 0) & C.iloc[i].notna())
        return m.dropna().sort_values(ascending=False).index[:100]

    def mom(i, a, b):
        return C.iloc[i - a] / C.iloc[i - b] - 1 if i >= b else pd.Series(np.nan, index=C.columns)

    def sel_size(i):
        return list(top100(i)[:20])

    def sel_mom(i):
        u = top100(i)
        return list(mom(i, 21, 252)[u].dropna().sort_values(ascending=False).index[:10])

    def sel_mf(i):
        u = top100(i)
        f, m = M.fin_at(M.dates[i]).reindex(u), M.mcap.iloc[i][u]
        ok = (f["op"] > 0) & (f["ni"] > 0)
        s = pct((f["op"] / (m + f["liab"]))[ok]) + pct((f["ni"] / f["assets"])[ok])
        return list(s.sort_values(ascending=False).index[:10])

    def sel_combo(i):
        u = top100(i)
        f, m = M.fin_at(M.dates[i]).reindex(u), M.mcap.iloc[i][u]
        eq = f["assets"] - f["liab"]
        parts = pd.concat([pct(f["ni"] / m), pct(f["op"] / (m + f["liab"])), pct(f["ni"] / f["assets"]),
                           pct((f["ni"] / eq).where(eq > 0)), pct(mom(i, 21, 252)[u]), pct(mom(i, 21, 126)[u])], axis=1)
        s = parts.mean(axis=1)[parts.notna().sum(axis=1) >= 4]
        return list(s.sort_values(ascending=False).index[:10])

    def sim(start, end, selector, seasonal):
        i0, i1 = M.first_on_or_after(start), M.last_on_or_before(end)
        ev = {}
        for y in range(int(start[:4]) - 1, int(end[:4]) + 1):
            for mo in (["11", "02"] if seasonal else ["11", "02", "05", "08"]):
                j = M.first_on_or_after(f"{y if mo == '11' else y + 1}{mo}01")
                if j is not None and i0 <= j <= i1:
                    ev[j] = "reb"
            if seasonal:
                j = M.first_on_or_after(f"{y + 1}0501")
                if j is not None and i0 <= j <= i1:
                    ev[j] = "sell"
        if i0 not in ev and (not seasonal or M.dates[i0][4:6] in ("11", "12", "01", "02", "03", "04")):
            ev[i0] = "reb"
        cash, hold, vals = 1e7, {}, []
        for i in range(i0, i1 + 1):
            e = ev.get(i)
            if e:
                value = cash + sum(n * px(i, c) for c, n in hold.items() if not np.isnan(px(i, c)))
                target = [] if e == "sell" else [c for c in selector(i - 1) if not np.isnan(px(i, c))]
                tw = value / len(target) if target else 0
                traded = sum(abs(n * px(i, c) - (tw if c in target else 0)) for c, n in hold.items() if not np.isnan(px(i, c))) \
                    + sum(tw for c in target if c not in hold)
                value -= traded * COST
                tw = value / len(target) if target else 0
                hold = {c: tw / px(i, c) for c in target}
                cash = value - tw * len(target)
            if not hold:
                cash *= 1 + CASH_R
            vals.append(cash + sum(n * (C.iloc[i][c] if not np.isnan(C.iloc[i][c]) else 0) for c, n in hold.items()))
        return pd.Series(vals, index=M.dates[i0:i1 + 1])

    def bench(start, end, seasonal=False):
        i0, i1 = M.first_on_or_after(start), M.last_on_or_before(end)
        r = M.kospi.iloc[i0 - 1:i1 + 1].pct_change().iloc[1:].fillna(0)
        if seasonal:
            mo = pd.Series([int(d[4:6]) for d in r.index], index=r.index)
            r = r.where(mo.isin([11, 12, 1, 2, 3, 4]), CASH_R)
        return 1e7 * (1 + r).cumprod()

    def ew(start, end):
        i0, i1 = M.first_on_or_after(start), M.last_on_or_before(end)
        uni = (M.mcap >= 300e8) & (V > 0)
        r = C.pct_change(fill_method=None).where(uni.shift(1)).clip(-0.3, 0.3).mean(axis=1).iloc[i0:i1 + 1].fillna(0)
        return 1e7 * (1 + r).cumprod()

    res = {}
    for a, b in windows:
        log(f"  계절성 전략 {a}~{b}")
        res[(a, b)] = {
            "A": run_aplan(a, b), "K": bench(a, b, True), "Kall": bench(a, b), "EW": ew(a, b),
            **{f"{k}{'s' if s else 'y'}": sim(a, b, fn, s) for k, fn in (("size", sel_size), ("mom", sel_mom), ("mf", sel_mf), ("combo", sel_combo)) for s in (False, True)},
        }
    return res


# ---------------------------------------------------------------- 종합 점수 (연중) + 매수신호
def total_strats(M: Market, windows):
    C, O, V, H, Lo = M.C, M.O, M.V, M.H, M.L
    uni = (M.mcap >= 300e8) & (M.tv20 >= 2e8) & (V > 0)
    rev, op, ni, liab, assets, eqf = (M.fin_panel(f) for f in ("rev", "op", "ni", "liab", "assets", "equity"))
    mcap = M.mcap

    def pr(x, asc=True):
        return x.where(uni).rank(axis=1, pct=True, ascending=asc) * 100

    def mean_min(parts, k):
        s = sum(p.fillna(0) for p in parts)
        n = sum(p.notna().astype(int) for p in parts)
        return (s / n).where(n >= k)

    eqp = eqf.where(eqf > 0)
    v = mean_min([pr(ni / mcap), pr(eqf / mcap), pr(rev / mcap), pr(op / (mcap + liab))], 3)
    q = mean_min([pr(ni / eqp), pr(ni / assets.where(assets > 0)), pr(op / rev.where(rev > 0)), pr((liab / eqp), False)], 3)
    m = mean_min([pr(C.shift(21) / C.shift(252) - 1), pr(C.shift(21) / C.shift(126) - 1), pr(C / C.shift(63) - 1)], 2)
    total = ((v + q + m) / 3).where(uni)

    def xup(a, b):
        return (a > b) & (a.shift(1) <= b.shift(1))

    ma20, ma60 = C.rolling(20, min_periods=20).mean(), C.rolling(60, min_periods=60).mean()
    d = C.diff()
    au = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    ad = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100 / (1 + au / ad.replace(0, np.nan))
    lower = ma20 - 2 * C.rolling(20, min_periods=20).std()
    ll, hh = Lo.rolling(14, min_periods=14).min(), H.rolling(14, min_periods=14).max()
    sk = ((C - ll) / (hh - ll).replace(0, np.nan) * 100).rolling(3).mean()
    sdl = sk.rolling(3).mean()
    vavg = V.shift(1).rolling(20, min_periods=15).mean()
    r1 = C.pct_change(fill_method=None)
    sig = (xup(ma20, ma60) | ((rsi >= 30) & (rsi.shift(1) < 30)) | ((C > lower) & (C.shift(1) < lower.shift(1)))
           | (xup(sk, sdl) & (sk.shift(1) < 20) & (sdl.shift(1) < 20)) | (C > C.shift(1).rolling(250, min_periods=200).max())
           | ((V / vavg >= 3) & (r1 >= 0.03))) & uni
    ret_cc = (C / C.shift(1) - 1).replace([np.inf, -np.inf], np.nan)
    ret_oc = (C / O - 1).replace([np.inf, -np.inf], np.nan)
    rank = total.rank(axis=1, ascending=False)
    top20pct = total.rank(axis=1, pct=True) >= 0.8

    def staggered(mask, H_, si, T):
        E = mask.copy()
        E.iloc[:si - 1] = False
        Ein = E.shift(1, fill_value=False)
        A = sum(Ein.shift(k, fill_value=False).astype(int) for k in range(H_))
        newp = Ein.astype(int)
        old = A - newp
        vn, vo = newp * ret_oc.notna(), old * ret_cc.notna()
        num = (vn * (ret_oc - COST * 2)).where(vn > 0, 0).fillna(0).sum(axis=1) + (vo * ret_cc).where(vo > 0, 0).fillna(0).sum(axis=1)
        cnt = vn.sum(axis=1) + vo.sum(axis=1)
        r = (num / cnt.replace(0, np.nan)).fillna(0).iloc[si:T]
        eq = (1 + r).cumprod()
        return f"{(eq.iloc[-1] ** (250 / len(r)) - 1) * 100:+.0f}%"

    def rebal(R, si, T):
        cash, hold, vals, i = 1e7, {}, [], si
        rb = set(range(si, T - 1, R))
        for i in range(si, T):
            if (i - 1) in rb:
                target = list(total.iloc[i - 1].dropna().sort_values(ascending=False).index[:20])
                if target:
                    def p(c):
                        x = O.iloc[i][c]
                        return x if not np.isnan(x) else C.iloc[i - 1][c]
                    value = cash + sum(n * p(c) for c, n in hold.items() if not np.isnan(p(c)))
                    tw = value / len(target)
                    traded = sum(abs(n * p(c) - (tw if c in target else 0)) for c, n in hold.items() if not np.isnan(p(c))) \
                        + sum(tw for c in target if c not in hold)
                    value -= traded * COST
                    tw = value / len(target)
                    hold = {c: tw / p(c) for c in target if not np.isnan(p(c)) and p(c) > 0}
                    cash = value - tw * len(hold)
            vals.append(cash + sum(n * (C.iloc[i][c] if not np.isnan(C.iloc[i][c]) else 0) for c, n in hold.items()))
        return pd.Series(vals, index=M.dates[si:T])

    out = {}
    for a, b in windows:
        log(f"  종합 전략 {a}~{b}")
        T = M.last_on_or_before(b) + 1
        si = M.first_on_or_after(a)
        reb = pd.DataFrame(False, index=M.dates, columns=C.columns)
        for i in range(si - 1, T, 5):
            reb.iloc[i] = (rank.iloc[i] <= 20).values
        out[(a, b)] = {
            "monthly": rebal(20, si, T), "quarterly": rebal(60, si, T),
            "sig": {name: [staggered(mk, h, si, T) for h in (20, 60)] for name, mk in
                    (("종합 상위20", reb), ("종합 상위20% + 신호", sig & top20pct), ("종합 50위 + 신호", sig & (rank <= 50)), ("매수신호만", sig))},
        }
    return out


# ---------------------------------------------------------------- 급등 이후 흐름 (코스피+코스닥, 최근 약 1년)
def surge_study(src: naver.NaverSource):
    lst = src.listing()
    lst = lst[(lst.index.str[-1] == "0") & ~lst["name"].str.contains("스팩|리츠")]
    src.done, src.errors, src.t0, src.skipped = 0, [], time.time(), 0
    with ThreadPoolExecutor(8) as ex:
        hs = list(ex.map(src.history_safe, list(lst.index)))
    fr = []
    for code, h in zip(lst.index, hs):
        if h is not None and len(h) >= 60:
            h = h.copy()
            h["code"] = code
            fr.append(h)
    df = pd.concat(fr)
    df = df[df.close > 0]
    c = df.pivot(index="date", columns="code", values="close").sort_index()
    v = df.pivot(index="date", columns="code", values="vol").sort_index()
    r1 = c.pct_change(fill_method=None)
    volx = v / v.shift(1).rolling(20, min_periods=15).mean()
    liq = (c * v).rolling(20, min_periods=10).mean() >= 5e8
    f5, f20 = c.shift(-5) / c - 1, c.shift(-20) / c - 1
    peak5 = c[::-1].rolling(5, min_periods=1).max()[::-1].shift(-1) / c - 1
    rows = []
    for lab, lo, hi in (("+5~15%", .05, .15), ("+15~25%", .15, .25), ("+25% 이상", .25, 9)):
        mk = ((r1 >= lo) & (r1 < hi) & (volx >= 3) & liq).stack()
        idx = mk[mk].index
        a5, a20, p5 = (x.stack().reindex(idx).dropna() for x in (f5, f20, peak5))
        rows.append([lab, f"{len(idx):,}", f"{a5.median() * 100:+.1f}%", f"{(a5 > 0).mean() * 100:.0f}%",
                     f"{a20.median() * 100:+.1f}%", f"{(p5 >= .1).mean() * 100:.0f}%"])
    return rows, f"{c.index[0][2:4]}.{c.index[0][4:6]}~{c.index[-1][2:4]}.{c.index[-1][4:6]}"


# ---------------------------------------------------------------- 결과 조립
def build(today):
    k, fin, src = load_data(today)
    M = Market(k, fin)
    end = M.dates[-1]
    W2 = ("20231101", end)
    wins = (W1, W2)
    lab = [f"{a[2:4]}.{a[4:6]}~{b[2:4]}.{b[4:6]}" for a, b in wins]
    S = season_strats(M, wins)
    Tt = total_strats(M, wins)
    surge, srange = surge_study(src)

    def combo(w, parts):
        return sum(S[w][k] * x for k, x in parts.items())

    combos = [("A안 100%", {"A": 1}), ("대형 모멘텀10 100%", {"moms": 1}), ("코스피 지수 100%", {"K": 1}),
              ("A안 50 + 대형 모멘텀10 50", {"A": .5, "moms": .5}), ("A안 50 + 코스피 지수 50", {"A": .5, "K": .5}),
              ("A안 50 + 대형 종합10 50", {"A": .5, "combos": .5})]
    rows1 = []
    for name, parts in combos:
        eqs = [combo(w, parts) for w in wins]
        mult = np.prod([e.iloc[-1] / 1e7 for e in eqs])
        yrs = sum(len(e) for e in eqs) / 250
        rows1.append([name] + [fmt_eq(e) for e in eqs] + [f"{(mult ** (1 / yrs) - 1) * 100:+.0f}%/년|{mult * 1e3:,.0f}만"])

    seasons = []
    for y in range(2020, int(end[:4]) + 1):
        a, b = f"{y}1101", f"{y + 1}1031"
        w = next((w for w in wins if w[0] <= a <= w[1]), None)
        if w is None:
            continue
        row = [f"{str(y)[2:]}~{str(y + 1)[2:]}" + (" (진행 중)" if b > end else "")]
        for key in ("A", "Kall", "EW"):
            eq = S[w][key]
            s = eq[(eq.index >= a) & (eq.index <= b)]
            prev = eq[eq.index < a]
            base = prev.iloc[-1] if len(prev) else 1e7
            row.append(f"{(s.iloc[-1] / base - 1) * 100:+.0f}%" if len(s) >= 5 else "-")
        seasons.append(row)

    big = []
    names = {"size": "시총 상위20", "mom": "대형 모멘텀10", "mf": "대형 마법공식10", "combo": "대형 종합10"}
    for kk, nm in names.items():
        for s in ("y", "s"):
            big.append([f"{nm} · {'연중' if s == 'y' else '11~4월'}"] + [fmt_eq(S[w][kk + s]) for w in wins])
    big += [["코스피 지수 · 연중"] + [fmt_eq(S[w]["Kall"]) for w in wins], ["코스피 지수 · 11~4월"] + [fmt_eq(S[w]["K"]) for w in wins]]

    reb = [["매달 교체"] + [fmt_eq(Tt[w]["monthly"]) for w in wins], ["분기 교체"] + [fmt_eq(Tt[w]["quarterly"]) for w in wins],
           ["코스피 지수"] + [fmt_eq(S[w]["Kall"]) for w in wins], ["코스피 평균 종목"] + [fmt_eq(S[w]["EW"]) for w in wins]]
    sigrows = [[nm] + [x for w in wins for x in Tt[w]["sig"][nm]] for nm in Tt[wins[0]]["sig"]]

    B = {
        "note": f"{today.isoformat()} 자동 갱신(매월 2일). 모두 1,000만 원 시작, 매매비용 왕복 0.3%, 신호·순위 다음 날 시가 매매. 데이터 끝: {end[:4]}-{end[4:6]}-{end[6:]}.",
        "sections": [
            {"title": "A안과 조합 (전부 11~4월 보유)", "note": "11월 초 매수 → 2월 초 교체 → 5월 초 매도, 5~10월은 현금(연 2.5% 가정).",
             "cols": ["조합", *lab, "전체"], "rows": rows1},
            {"title": "A안 시즌별 (11월~다음 해 10월)", "cols": ["시즌", "A안", "코스피 지수", "평균 종목"], "rows": seasons},
            {"title": "대형주 퀀트 (코스피 시총 상위 100 중)", "note": "연중 = 분기마다 교체, 11~4월 = 계절성 보유.", "cols": ["전략", *lab], "rows": big},
            {"title": "종합 상위20 리밸런싱 (연중 보유)", "cols": ["방식", *lab], "rows": reb},
            {"title": "종합 점수 + 매수신호 (보유 기간별 연 수익)", "note": "신호 다음 날 시가 매수 후 정해진 기간 보유.",
             "cols": ["전략", f"20일 {lab[0]}", f"60일 {lab[0]}", f"20일 {lab[1]}", f"60일 {lab[1]}"], "rows": sigrows},
            {"title": f"급등 다음 흐름 ({srange}, 거래량 3배↑)", "note": "급등한 날 종가 기준 이후 수익률.",
             "cols": ["당일 상승", "건수", "5일 뒤 중앙값", "5일 뒤 오른 비율", "20일 뒤 중앙값", "5일 안 +10% 더"], "rows": surge},
        ],
        "caveats": [
            "현재 상장 종목만 사용(상장폐지 종목 제외) → 개별 종목 전략은 실제보다 좋게 나왔을 수 있음. 지수는 해당 없음.",
            "재무는 DART 연간 실적만, 공시 다음 해 4월부터 사용. EV/EBITDA는 EV/영업이익으로 근사. A안의 DART 공시 제외 규칙은 미반영.",
            "여러 변형을 시험해 좋은 것을 고른 결과라 운이 섞여 있음. 기간이 짧고 특정 장세 영향이 큼.",
            "주식 수는 현재 기준으로 과거 시총을 계산함(유상증자 등은 반영 안 됨).",
        ],
    }
    return B, rows1


def main():
    t = time.time()
    today = config.today_kst()
    B, rows1 = build(today)
    (ROOT / "docs" / "backtest.json").write_text(json.dumps(B, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"저장 docs/backtest.json ({time.time() - t:.0f}초)")
    msg = "\n".join(f"{r[0]}: {r[-1].split('|')[0]}" for r in rows1)
    url = None
    if os.environ.get("GITHUB_REPOSITORY"):
        o, rp = os.environ["GITHUB_REPOSITORY"].split("/")
        url = f"https://{o.lower()}.github.io/{rp}/"
    ntfy.send("환동퀀트 백테스트 갱신", "전체 기간 연 수익률\n" + msg, click=url, tags=["bar_chart"])


if __name__ == "__main__":
    main()

"""앱 '소형주' 탭 데이터(docs/smallcap.json) + 분기 리밸런싱 알림.

  python -m backtest.smallcap_app             # 전략 그리드 백테스트 + 지금 종목 목록 → docs/smallcap.json
  python -m backtest.smallcap_app --no-grid   # 그리드는 지난 결과 그대로, 종목 목록만 갱신
  python -m backtest.smallcap_app --no-alert  # 알림 안 보냄

store/ (data 브랜치)의 KRX 일별 시세·DART 재무·공시가 있어야 한다(collect 작업이 매일 밤 채움).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import time

import numpy as np
import pandas as pd

from backtest import collect as C
from backtest import smallcap as S
from backtest import listing_risk

OUT = C.ROOT / "docs" / "smallcap.json"
ORDER = [3, 2, 5, 4, 6, 7, 1, 12, 0]          # 화면 순서(이름 있는 7개 → 비교용 2개)
COMBO = [1, 2, 3, 4, 5, 6, 7]                 # 7전략 동일비중
FREQS, NS, RISKS = ["Q", "M", "W"], [10, 20, 30], [0, 1]
TOPN = 30
DESC = {
    0: "시가총액 가장 작은 순(재무 안 봄) — 비교 기준",
    12: "시가총액 작은 순, 최근 4분기 순이익 흑자·자본잠식 아님만",
    1: "PBR(시총÷자본) 낮은 순",
    2: "PER·PBR·PCR 순위 합(셋 다 흑자일 때만)",
    3: "PER·PBR·PSR·PCR 순위 합(넷 다 계산될 때만)",
    4: "PBR 낮은 순위 + GP/A(매출총이익÷자산) 높은 순위",
    5: "F스코어 3점(순이익>0·영업현금흐름>0·신주발행 없음) 중 PBR 낮은 순",
    6: "F스코어 3점 중 GP/A 높은 순",
    7: "밸류(PER·PBR·PSR·PCR)·퀄리티(GP/A)·모멘텀(12개월) 순위 1/3씩",
}
PERIOD = {1: "1분기", 2: "반기", 3: "3분기", 4: "사업"}
QMONTH = {4: "사업보고서", 6: "1분기보고서", 9: "반기보고서", 12: "3분기보고서"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def ymd(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def r1(v, k=1):
    return None if v is None or not np.isfinite(v) else round(float(v), k)


def risk_events() -> pd.DataFrame:
    fs = sorted(glob.glob(str(S.STORE / "disc" / "B_*.parquet")))
    if not fs:
        return pd.DataFrame(columns=["stock", "date", "report"])
    b = pd.concat([pd.read_parquet(f, columns=["stock", "date", "report"]) for f in fs], ignore_index=True)
    b = b[b.report.str.contains(S.RISK_Y, regex=True)]
    b["date"] = b.date.astype(str)
    return b.sort_values("date")


BANDS = [("S", "소형주", 0.0, 0.2), ("MS", "중소형주", 0.2, 0.5), ("ML", "중대형주", 0.5, 0.8), ("L", "대형주", 0.8, 1.0), ("A", "전체", 0.0, 1.0)]


def band_cfg(b: str):
    _, _, lo, hi = next(x for x in BANDS if x[0] == b)
    S.CAP_LO, S.SMALL_Q = lo, hi
    strats = ORDER if b == "S" else COMBO          # 초소형 비교군(0·12)은 소형주에서만 의미 있음
    freqs = FREQS if b == "S" else ["Q", "M"]      # 매주는 소형주만(나머지 구간은 시간 대비 쓸모 적음)
    return strats, freqs


def run_grid(m, F, b: str, red=False):
    strats, freqs = band_cfg(b)
    rows = []
    ns, risks = NS, RISKS
    # 각 신호일의 순위는 종목 수와 익절률에 공통이다.
    rankings = {}
    for f in freqs:
        for i in S.reb_days(m["dates"], f):
            for rk in risks:
                if (i, rk) in rankings:
                    continue
                X = S.factors(m, F, i - 1, trade_ok=None if red else m["alive"].iloc[i], risk=bool(rk))
                if red and X is not None:
                    X = X.loc[~pd.Series(m["listing_state"][i].astype(bool), index=m["codes"]).reindex(X.index)]
                rankings[i, rk] = {s: list(S.score(X, s).dropna().sort_values().index[:max(ns)])
                                   if X is not None else [] for s in strats}
    for tp in (0, 10, 20, 30):
        rows.extend(run_threshold(m, F, b, strats, freqs, ns, risks, rankings, tp, red))
    band_cfg("S")
    return rows


def run_threshold(m, F, b, strats, freqs, ns, risks, rankings, tp, red):
    rows, curves = [], {}
    for s in strats:
        for f in freqs:
            for n in ns:
                for rk in risks:
                    targets = {i: ranks[s] for (i, risk), ranks in rankings.items() if risk == rk}
                    r = S.simulate(m, F, s, f, n, close_tp=tp / 100, targets=targets, red=red)
                    e = r.pop("curve")
                    curves[(s, f, n, rk)] = e
                    rows.append({"b": b, "red": int(red), "tp": tp, "s": s, "f": f, "n": n, "r": rk, "cagr": r1(r["cagr"] * 100), "mdd": r1(r["mdd"] * 100, 0),
                                 "to": r1(r["turnover"]), "cost": r1(r["cost"] * 100), "hold": r1(r["hold"]), "y": r["yearly"], "_e": e})
                    log(f"[{b}] {S.NAMES[s]:10s} {f} {n:2d} {'🟡제외' if rk else '      '}: 연 {r['cagr']*100:5.1f}% 낙폭 {r['mdd']*100:4.0f}% · 26년 {r['yearly'].get('2026', 0):+.0f}%")
    S.RISK_FILTER = False
    # 7전략 동일비중: 처음에 7등분해서 각자 굴림(전략 사이 재조정 없음) → 곡선 평균
    for f in freqs:
        for n in ns:
            for rk in risks:
                e = pd.concat([curves[(s, f, n, rk)] for s in COMBO], axis=1).ffill().mean(axis=1)
                yrs = (e.index[-1] - e.index[0]).days / 365.25
                ye = e.resample("YE").last()
                yearly = ye.pct_change()
                yearly.iloc[0] = ye.iloc[0] - 1
                sub = [x for x in rows if x["f"] == f and x["n"] == n and x["r"] == rk and x["s"] in COMBO]
                rows.append({"b": b, "red": int(red), "tp": tp, "s": 99, "f": f, "n": n, "r": rk, "cagr": r1((e.iloc[-1] ** (1 / yrs) - 1) * 100),
                             "mdd": r1((e / e.cummax() - 1).min() * 100, 0), "to": r1(np.mean([x["to"] for x in sub])),
                             "cost": r1(np.mean([x["cost"] for x in sub])), "hold": None,
                             "y": {str(k.year): round(float(v) * 100, 1) for k, v in yearly.items()}, "_e": e})
    return rows


def finish(rows):
    months = sorted({k.strftime("%Y-%m") for r in rows for k in r["_e"].resample("ME").last().index})
    for row in rows:
        mon = row.pop("_e").resample("ME").last()
        mon.index = mon.index.strftime("%Y-%m")
        row["c"] = [None if pd.isna(v) else round(float(v), 3) for v in mon.reindex(months).values]
    return months


def snapshot(m, F, j: int, trade_i: int | None, ev: pd.DataFrame, last: int, strats=ORDER):
    """j일 종가 기준 전략별 상위 종목(🟡 포함/제외 둘 다)과 종목 정보."""
    lists, info = {}, {}
    dates = m["dates"]
    X0 = S.factors(m, F, j, trade_ok=None if trade_i is None else m["alive"].iloc[trade_i], risk=False)
    X1 = S.factors(m, F, j, trade_ok=None if trade_i is None else m["alive"].iloc[trade_i], risk=True)
    bases = [S.factors(m, F, j, trade_ok=None, risk=bool(rk)) for rk in RISKS] if trade_i is not None else [X0, X1]
    state_day = j + 1
    red = pd.Series(m["listing_state"][state_day], index=m["codes"])
    for strat in strats:
        lists[str(strat)] = {}
        for rk, X in ((0, X0), (1, X1)):
            ranks = S.score(X, strat).dropna().sort_values() if X is not None else pd.Series(dtype=float)
            lists[str(strat)][str(rk)] = list(ranks.index[:TOPN])
            base = bases[rk]
            if base is not None:
                clean = base.loc[red.reindex(base.index).eq(0)]
                clean_ranks = S.score(clean, strat).dropna().sort_values()
                lists[str(strat)][str(rk + 2)] = list(clean_ranks.index[:TOPN])
            else:
                lists[str(strat)][str(rk + 2)] = []
    codes = sorted({c for v in lists.values() for L in v.values() for c in L})
    d0 = dates[j]
    lo = dates[max(0, j - 249)]
    evj = ev[(ev.date >= lo) & (ev.date <= d0)]
    lastev = evj.drop_duplicates("stock", keep="last").set_index("stock")
    per = F["period"].iloc[j]
    cum = m["cumidx"]
    frames = [frame for frame in [X0, X1, *bases] if frame is not None]
    full = pd.concat(frames) if frames else pd.DataFrame()
    full = full.loc[~full.index.duplicated()]
    for c in codes:
        x = full.loc[c]
        rk = lastev.loc[c] if c in lastev.index else None
        p = per.get(c)
        o = {"red": int(red[c]), "n": str(m["names"].get(c, c)), "cap": r1(x["cap"] / 1e8, 0), "pbr": r1(x["pbr"], 2), "per": r1(x["per"]), "psr": r1(x["psr"], 2),
             "pcr": r1(x["pcr"]), "gpa": r1(x["gpa"] * 100 if pd.notna(x["gpa"]) else None), "f": int(x["fscore"]),
             "mom": r1(x["mom"] * 100 if pd.notna(x["mom"]) else None),
             "fin": f"{int(p) // 10} {PERIOD[int(p) % 10]}" if pd.notna(p) else None,
             "risk": f"{ymd(rk.date)} {rk.report.strip()}" if rk is not None else None,
             "px": r1(m["close"][c].iloc[last], 0)}
        if trade_i is not None:      # 리밸런싱 날 시가 → 마지막 종가 수익률
            intra = m["intra"][c].iloc[trade_i]
            if pd.notna(intra) and m["alive"][c].iloc[last]:
                o["since"] = r1(((1 + intra) * cum[c].iloc[last] / cum[c].iloc[trade_i] - 1) * 100)
            elif not m["alive"][c].iloc[last]:
                o["since"] = None
                o["halt"] = True
        info[c] = o
    return {"sig": ymd(d0), "lists": lists, "stocks": info}


def next_reb(after: str) -> str:
    """다음 분기 리밸런싱(4·6·9·12월) 첫 평일. 공휴일은 모름 → 대략."""
    d = dt.date(int(after[:4]), int(after[4:6]), int(after[6:]))
    y, mo = d.year, d.month
    for _ in range(14):
        if mo in (4, 6, 9, 12):
            f = dt.date(y, mo, 1)
            while f.weekday() >= 5:
                f += dt.timedelta(days=1)
            if f > d:
                return f.isoformat()
        mo += 1
        if mo > 12:
            y, mo = y + 1, 1
    return ""


def next_fin(d: str) -> str | None:
    """그 리밸런싱에 반영될 보고서(앱의 '재무' 표기와 같은 꼴)."""
    if not d:
        return None
    y, mo = int(d[:4]), int(d[5:7])
    return {4: f"{y - 1} 사업", 6: f"{y} 1분기", 9: f"{y} 반기", 12: f"{y} 3분기"}.get(mo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-grid", action="store_true")
    ap.add_argument("--no-alert", action="store_true")
    ap.add_argument("--slip", type=float, default=0.005)
    a = ap.parse_args()
    t = time.time()
    S.SLIP = a.slip
    m = S.load()
    m["cumidx"] = (1 + m["ret"]).cumprod()
    F = S.fundamentals(m)
    m["risk"] = S.risk_matrix(m)
    m["listing_state"] = listing_risk.build(S.STORE, m["dates"], m["codes"], m["dates"][-1])
    m["tradable"] = (m["intra"].notna() & m["gap"].notna() & (m["val"] > 0)).to_numpy()
    ev = risk_events()
    dates = m["dates"]
    last = len(dates) - 1
    log(f"시세 {len(dates)}일 · 마지막 {dates[-1]} ({time.time() - t:.0f}초)")
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    if a.no_grid and any(r.get("red") for r in old.get("grid", [])):
        rows, months = old["grid"], old["months"]
    else:
        rows = []
        for b, *_ in BANDS:
            for red in (False, True):
                rows += run_grid(m, F, b, red)
        months = finish(rows)
    qi = S.reb_days(dates, "Q")[-1]
    snap_q, snap_now = {"lists": {}, "stocks": {}}, {"lists": {}, "stocks": {}}
    for b, *_ in BANDS:                       # 시총 구간마다 전략별 목록, 종목 정보는 합침
        strats, _f = band_cfg(b)
        for snap, args in ((snap_q, (qi - 1, qi)), (snap_now, (last, None))):
            x = snapshot(m, F, args[0], args[1], ev, last, strats)
            snap["lists"][b] = x["lists"]
            snap["stocks"].update(x["stocks"])
            snap["sig"] = x["sig"]
    band_cfg("S")
    snap_q["date"] = ymd(dates[qi])
    snap_q["basis"] = QMONTH[int(dates[qi][4:6])]
    nxt = next_reb(dates[-1])
    today = C.today_kst()
    out = {
        "made": (dt.datetime.utcnow() + dt.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M"),
        "asof": ymd(dates[-1]), "start": ymd(S.START), "slip": a.slip * 100, "min_val": S.MIN_VAL / 1e8,
        "names": {str(s): S.NAMES[s] for s in ORDER} | {"99": "7전략 동일비중"},
        "desc": {str(s): DESC[s] for s in ORDER} | {"99": "1~7번 전략에 돈을 7등분해 각자 굴림(서로 재조정 없음)"},
        "order": ORDER[:7] + [99] + ORDER[7:],
        "bands": [{"k": b, "l": l, "d": f"시가총액 하위 {lo*100:.0f}~{hi*100:.0f}%" if b != "A" else "거래 가능한 보통주 전체"} for b, l, lo, hi in BANDS],
        "grid": rows, "months": months,
        "q": snap_q, "now": snap_now, "next": nxt, "next_basis": QMONTH.get(int(nxt[5:7])) if nxt else None, "next_fin": next_fin(nxt),
        "alerted": old.get("alerted"),
    }
    # 알림: 리밸런싱 달이 됐는데 아직 그 달 시세가 없으면 = 오늘이 첫 거래일(밤 00:07 실행 기준)
    key = today.strftime("%Y-%m")
    if (today.month in (4, 6, 9, 12) and dates[-1][:6] != today.strftime("%Y%m") and old.get("alerted") != key
            and not a.no_alert):
        from pipeline import ntfy
        url = os.environ.get("PAGES_URL")
        if not url and os.environ.get("GITHUB_REPOSITORY"):
            owner, repo = os.environ["GITHUB_REPOSITORY"].split("/")
            url = f"https://{owner.lower()}.github.io/{repo}/#small"
        L = snap_now["lists"]["S"]["3"]["1"][:20]
        names = ", ".join(snap_now["stocks"][c]["n"] for c in L[:10])
        ntfy.send(f"소형주 분기 리밸런싱 {today:%m/%d}",
                  f"오늘 시가에 분기 교체하는 날입니다(신호: {ymd(dates[-1])} 종가, {QMONTH[today.month]} 반영).\n"
                  f"예) 슈퍼가치 20종목·🟡제외 상위: {names} …\n앱 '소형주' 탭에서 전략별 목록을 확인하세요.",
                  click=url, tags=["calendar"], priority=4)
        out["alerted"] = key
        log("리밸런싱 알림 보냄")
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    log(f"저장 {OUT} ({OUT.stat().st_size // 1024} KB) · {time.time() - t:.0f}초")


if __name__ == "__main__":
    main()

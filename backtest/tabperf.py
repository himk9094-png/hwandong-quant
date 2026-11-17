"""앱 목록 탭(종합·밸류·퀄리티·모멘텀·마법공식·대형 탭)의 백테스트 → docs/tabperf.json.

같은 엔진(smallcap.simulate: 상장폐지 포함·공시 다음 날 재무·다음 날 시가 체결·비용 포함)으로
앱 탭과 같은 정의(pipeline/factors.py)를 2016-04부터 돌린다.
  python -m backtest.tabperf
"""
from __future__ import annotations

import datetime as dt
import json
import time

import pandas as pd

from backtest import collect as C
from backtest import sigbt as G
from backtest import smallcap as S
from backtest.smallcap_app import r1

OUT = C.ROOT / "docs" / "tabperf.json"
ALL = dict(MIN_CAP=300e8, MIN_VAL=2e8, TOPK=0, KOSPI_ONLY=False, SMALL_Q=1.0, CAP_LO=0.0)       # 앱 기본 유니버스
BIG = dict(MIN_CAP=0.0, MIN_VAL=1e8, TOPK=100, KOSPI_ONLY=True, SMALL_Q=1.0, CAP_LO=0.0)       # 코스피 시총 상위 100
TABS = {
    "total": (21, 20, ALL), "value": (22, 20, ALL), "quality": (23, 20, ALL), "mom": (24, 20, ALL), "mf": (25, 20, ALL),
    "lmom": (26, 10, BIG), "lcombo": (27, 10, BIG), "lmf": (28, 10, BIG), "size": (29, 20, BIG),
}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def setu(u: dict, season=False):
    for k, v in u.items():
        setattr(S, k, v)
    S.SEASON = season
    S.RISK_FILTER = False


def line(m, F, name, strat, n, u, freq, season=False):
    setu(u, season)
    r = S.simulate(m, F, strat, freq, n)
    e = r.pop("curve")
    log(f"{name:24s}: 연 {r['cagr']*100:5.1f}% 낙폭 {r['mdd']*100:4.0f}% 비용 {r['cost']*100:4.1f}% 26년 {r['yearly'].get('2026', 0):+.0f}%")
    return {"name": name, "cagr": r1(r["cagr"] * 100), "mdd": r1(r["mdd"] * 100, 0), "cost": r1(r["cost"] * 100),
            "y": r["yearly"], "_e": e}


def main():
    t = time.time()
    S.SLIP = 0.005
    m = S.load()
    m["cumidx"] = (1 + m["ret"]).cumprod()
    F = S.fundamentals(m)
    log(f"준비 {time.time() - t:.0f}초 · 시세 끝 {m['dates'][-1]}")
    bench_all = line(m, F, "전체 동일비중(비교)", 30, 99999, ALL, "Q")
    bench_big = line(m, F, "코스피 상위100 동일비중(비교)", 30, 100, BIG, "Q")
    bench_all["b"] = bench_big["b"] = 1
    U = G.universe(m)
    nsig, surge = G.indicators(m)
    log(f"신호 계산 {time.time() - t:.0f}초")
    mem = {}
    out = {}
    for k, (s, n, u) in TABS.items():
        nm = S.NAMES[s]
        if u is BIG:
            L = [line(m, F, f"{nm} {n} · 11~4월만", s, n, u, "S", season=True),
                 line(m, F, f"{nm} {n} · 분기 교체 연중", s, n, u, "Q")]
            cond = f"코스피 시총 상위 100 중 상위 {n}종목 동일비중. 11~4월만 = 11월 초 매수 → 2월 초 교체 → 5월 초 매도(5~10월 현금, 이자 0)."
        else:
            L = [line(m, F, f"{nm} 상위{n} · 분기 교체", s, n, u, "Q"),
                 line(m, F, f"{nm} 상위{n} · 매월 교체", s, n, u, "M")]
            cond = f"앱 목록과 같은 조건(보통주·시총 300억↑·20일 거래대금 2억↑)에서 점수 상위 {n}종목 동일비중."
        setu(u)
        mem[k] = G.membership(m, F, s, 0.2)
        L.append(G.event_line(m, G.newly((nsig >= 1) & mem[k]) & U, 20, "점수 상위20%+매수신호 · 20일 보유"))
        L[-1]["sig"] = 1
        log(L[-1]["name"], L[-1]["cagr"])
        L.append(dict(bench_big if u is BIG else bench_all))
        cond += " '매수신호만'을 켜면 카드가 그 경우로 바뀜: 점수 상위 20% 종목에 신호가 새로 뜨면 다음 날 시가 매수 → 20거래일 보유."
        out[k] = {"cond": cond, "lines": L}
    # 매수신호 탭
    L = [G.event_line(m, G.newly(nsig >= 1) & U, 20, "신호 1개↑ 전부 · 20일 보유"),
         G.event_line(m, G.newly(nsig >= 2) & U, 20, "신호 2개↑ · 20일 보유"),
         G.event_line(m, G.newly((nsig >= 1) & mem["total"]) & U, 20, "종합 상위20%+신호 · 20일 보유"),
         G.event_line(m, G.newly(nsig >= 2) & U, 5, "신호 2개↑ · 5일 보유"),
         dict(bench_all)]
    for x in L[:-1]:
        log(x["name"], x["cagr"])
    out["signal"] = {"cond": "목록에 처음 올라온 날 종가 → 다음 날 시가 매수 → 정해진 거래일 보유 후 종가 매도. 보유 중 종목 동일비중. "
                             "시세에 고가·저가가 없어 스토캐스틱은 종가로 근사, 거래량은 거래대금으로 대신함.", "lines": L}
    # 급등후보 탭: 촘촘하게(매일 교체)
    L = [G.event_line(m, (surge >= 60) & U, 1, "급등점수 60↑ · 1일 보유(매일 교체)"),
         G.event_line(m, (surge >= 60) & U, 3, "급등점수 60↑ · 3일 보유"),
         G.event_line(m, (surge >= 60) & U, 5, "급등점수 60↑ · 5일 보유"),
         G.event_line(m, (surge >= 80) & U, 1, "급등점수 80↑ · 1일 보유(매일 교체)"),
         dict(bench_all)]
    for x in L[:-1]:
        log(x["name"], x["cagr"])
    out["surge"] = {"cond": "그날 종가 기준 급등점수가 기준 이상인 종목을 다음 날 시가 매수 → 1·3·5거래일 보유 후 종가 매도. 매일 새로 사고판다. "
                            "시세에 고가·저가가 없어 거래량은 거래대금으로 대신함.", "lines": L}
    setu(ALL)
    months = sorted({x.strftime("%Y-%m") for v in out.values() for l in v["lines"] for x in l["_e"].resample("ME").last().index})
    for v in out.values():
        for l in v["lines"]:
            mon = l["_e"].resample("ME").last()
            mon.index = mon.index.strftime("%Y-%m")
            l["c"] = [None if pd.isna(x) else round(float(x), 3) for x in mon.reindex(months).values]
        for l in v["lines"]:
            l.pop("_e", None)
    res = {"made": (dt.datetime.utcnow() + dt.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M"),
           "start": f"{S.START[:4]}-{S.START[4:6]}", "asof": f"{m['dates'][-1][:4]}-{m['dates'][-1][4:6]}-{m['dates'][-1][6:]}",
           "slip": S.SLIP * 100, "months": months, "tabs": out}
    OUT.write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")))
    log(f"저장 {OUT} ({OUT.stat().st_size // 1024} KB) · {time.time() - t:.0f}초")


if __name__ == "__main__":
    main()

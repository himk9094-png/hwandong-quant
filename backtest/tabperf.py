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
    out = {}
    for k, (s, n, u) in TABS.items():
        nm = S.NAMES[s]
        if u is BIG:
            L = [line(m, F, f"{nm} {n} · 11~4월만", s, n, u, "S", season=True),
                 line(m, F, f"{nm} {n} · 분기 교체 연중", s, n, u, "Q"),
                 dict(bench_big)]
            cond = f"코스피 시총 상위 100 중 상위 {n}종목 동일비중. 11~4월만 = 11월 초 매수 → 2월 초 교체 → 5월 초 매도(5~10월 현금, 이자 0)."
        else:
            L = [line(m, F, f"{nm} 상위{n} · 분기 교체", s, n, u, "Q"),
                 line(m, F, f"{nm} 상위{n} · 매월 교체", s, n, u, "M"),
                 dict(bench_all)]
            cond = f"앱 목록과 같은 조건(보통주·시총 300억↑·20일 거래대금 2억↑)에서 점수 상위 {n}종목 동일비중."
        out[k] = {"cond": cond, "lines": L}
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

"""강환국식 소형주 전략 7종 백테스트 (생존 편향 없음 · 공시일 이후 재무만 · 다음 날 시가 체결 · 현실 비용).

  python -m backtest.smallcap                 # store/ 데이터로 전부 → docs/smallcap.json
  python -m backtest.smallcap --quick         # 분기 리밸런싱만

전략 (소형주 = 거래 가능한 보통주 중 시가총액 하위 20%)
  1 저PBR            PBR 낮은 순
  2 파마+LSV 콤보    PER·PBR·PCR 순위 합
  3 슈퍼가치         PER·PBR·PSR·PCR 순위 합
  4 신마법공식 2.0   PBR(낮을수록) + GP/A(높을수록) 순위 합
  5 F-스코어         F스코어 3점(순이익>0·영업현금흐름>0·신주발행 없음) 중 PBR 낮은 순
  6 슈퍼퀄리티       F스코어 3점 중 GP/A 높은 순
  7 짬뽕             밸류콤보(PER·PBR·PSR·PCR)·퀄리티(GP/A)·모멘텀(12개월) 순위를 1/3씩
체결: 신호는 전 거래일 종가 기준, 리밸런싱 날 시가에 사고팖. 비용: 수수료 0.015%×2 + 매도세(연도별) + 슬리피지(편도).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import collect as C

STORE = C.STORE
OUT = C.ROOT / "docs" / "smallcap.json"
COMM = 0.00015
SLIP = 0.0025                     # 편도 슬리피지(소형주 호가 차이·충격)
MIN_VAL = 1e8                     # 20일 평균 거래대금 1억 원 이상만
SMALL_Q = 0.20
START = "20160401"
EXCL_NAME = r"스팩|리츠|REIT|인프라|선박|기업인수목적|ETN|ETF|유전|\d+호|맥쿼리|투자회사"   # 펀드·투자회사형 종목


def sell_tax(ymd: str) -> float:
    """매도 증권거래세+농특세(코스피·코스닥 공통으로 근사)."""
    if ymd < "20190603":
        return 0.0030
    if ymd < "20210101":
        return 0.0025
    if ymd < "20230101":
        return 0.0023
    if ymd < "20240101":
        return 0.0020
    if ymd < "20250101":
        return 0.0018
    if ymd < "20260101":
        return 0.0015
    return 0.0020


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- 데이터 → 행렬
def load():
    px = C.krx_all()
    px = px[px.code.str.len() == 6]
    dates = sorted(px.date.unique())
    codes = sorted(px.code.unique())
    piv = lambda col: px.pivot(index="date", columns="code", values=col).reindex(index=dates, columns=codes)
    close, opn, chg = piv("close").astype("float64"), piv("open").astype("float64"), piv("chg").astype("float64")
    mcap, val, shares = piv("mcap").astype("float64"), piv("val").astype("float64"), piv("shares").astype("float64")
    last = px.drop_duplicates("code", keep="last").set_index("code")
    sect = piv("sect") if "sect" in px else None
    # 일간 수익률(권리락·액면분할 반영: 등락률은 조정 기준가 대비)
    ret = (chg / 100.0)
    base = close / (1 + ret)
    gap = (opn / base - 1).where(opn > 0)          # 전일 기준가 → 오늘 시가
    intra = (close / opn - 1).where(opn > 0)        # 오늘 시가 → 종가
    alive = close.notna() & (close > 0)
    flags = pd.DataFrame(False, index=dates, columns=codes)
    if sect is not None:
        flags = sect.apply(lambda c: c.astype(str).str.contains("관리|환기|SPAC", regex=True, na=False))
    common = (pd.Index(codes).str[-1] == "0") & (pd.Index(codes).str[0] != "9")   # 보통주만, 해외기업(9로 시작) 제외
    okname = ~last.reindex(codes)["name"].astype(str).str.contains(EXCL_NAME, regex=True).values
    m = dict(dates=dates, codes=codes, close=close, open=opn, ret=ret.fillna(0.0), gap=gap, intra=intra, mcap=mcap,
             val20=val.rolling(20, min_periods=10).mean(), shares=shares, alive=alive, flags=flags,
             static_ok=pd.Series(common & okname, index=codes), names=last.reindex(codes)["name"])
    return m


def fundamentals(m):
    """보고서별 TTM → 공시 다음 거래일부터 유효한 일별 행렬(종목 × 날짜)."""
    if not (STORE / "fin" / "multi.parquet").exists() and not (STORE / "fin" / "fin.parquet").exists():
        log("재무 데이터 없음 → 주가만 쓰는 전략(0)만 가능")
        empty = pd.DataFrame(np.nan, index=m["dates"], columns=m["codes"])
        return {k: empty for k in ["assets", "equity", "rev", "op", "ni", "gp", "cfo"]}
    cc = C.corp_map()
    corp2code = {}
    for code, corp in cc.items():
        corp2code.setdefault(corp, code)
    frames = []
    mp = STORE / "fin" / "multi.parquet"
    fp = STORE / "fin" / "fin.parquet"
    mm = pd.read_parquet(mp) if mp.exists() else pd.DataFrame()
    ff = pd.read_parquet(fp) if fp.exists() else pd.DataFrame()
    recs = []
    for src, df in (("m", mm), ("f", ff)):
        if df.empty:
            continue
        df = df.copy()
        df["pref"] = (df["fs"] == "CFS").astype(int)
        df = df.sort_values("pref").drop_duplicates(["corp", "year", "rpt"], keep="last")
        df["src"] = src
        recs.append(df)
    if not recs:
        log("재무 데이터 없음 → 주가만 쓰는 전략(0)만 가능")
        empty = pd.DataFrame(np.nan, index=m["dates"], columns=m["codes"])
        return {k: empty for k in ["assets", "equity", "rev", "op", "ni", "gp", "cfo"]}
    R = pd.concat(recs, ignore_index=True)
    # 같은 (회사, 연도, 보고서)에 요약(m)과 전체(f)가 있으면 합침: 전체 재무가 있으면 그 값 우선, 없는 항목은 요약에서
    cols = ["rev", "rev_p", "op", "op_p", "ni", "ni_p", "assets", "equity", "gp", "gp_p", "cfo", "cfo_p"]
    for c in cols:
        if c not in R:
            R[c] = np.nan
    R = R.sort_values("src")   # f < m → f가 먼저
    G = R.groupby(["corp", "year", "rpt"], as_index=False).agg({**{c: "first" for c in cols}, "rcept_no": "min"})
    G["filed"] = G["rcept_no"].astype(str).str[:8]
    fy = G[G.rpt == "11011"].set_index(["corp", "year"])
    out = []
    flows = ["rev", "op", "ni", "gp", "cfo"]
    for r in G.itertuples(index=False):
        if not isinstance(r.filed, str) or len(r.filed) != 8 or not r.filed.isdigit():
            continue
        rec = {"corp": r.corp, "filed": r.filed, "assets": r.assets, "equity": r.equity, "period": int(r.year) * 10 + {"11013": 1, "11012": 2, "11014": 3, "11011": 4}[r.rpt]}
        if r.rpt == "11011":
            for k in flows:
                rec[k] = getattr(r, k)
        else:
            key = (r.corp, r.year - 1)
            if key not in fy.index:
                continue
            f0 = fy.loc[key]
            for k in flows:
                cur, prev, base = getattr(r, k), getattr(r, k + "_p"), f0[k]
                rec[k] = base + cur - prev if pd.notna(cur) and pd.notna(prev) and pd.notna(base) else np.nan
        out.append(rec)
    T = pd.DataFrame(out)
    T["code"] = T.corp.map(corp2code)
    T = T.dropna(subset=["code"])
    T = T[T.code.isin(m["codes"])]
    # 같은 날 여러 보고서(정정 등)면 더 최근 기간을 씀
    T = T.sort_values(["code", "filed", "period"]).drop_duplicates(["code", "filed"], keep="last")
    dates = pd.Index(m["dates"])
    eff_idx = dates.searchsorted(T["filed"].values, side="right")      # 공시 다음 거래일부터
    T["eff"] = [dates[i] if i < len(dates) else None for i in eff_idx]
    T = T.dropna(subset=["eff"])
    # 더 이른 기간 보고서가 늦게 정정 공시되면 최신 기간을 덮어쓰지 않게: 기간이 뒤로 가는 행은 버림
    T = T.sort_values(["code", "eff"])
    T["maxp"] = T.groupby("code").period.cummax()
    T = T[T.period >= T.maxp]
    F = {}
    for k in ["assets", "equity"] + flows:
        p = T.pivot_table(index="eff", columns="code", values=k, aggfunc="last")
        F[k] = p.reindex(index=dates, columns=m["codes"]).ffill(limit=400)
    log(f"재무: 보고서 {len(T)}건 · 종목 {T.code.nunique()}개 · GP 있음 {T.gp.notna().mean():.0%} · CFO 있음 {T.cfo.notna().mean():.0%}")
    return F


# ---------------------------------------------------------------- 신호
def rank(s: pd.Series, asc=True):
    return s.rank(ascending=asc, pct=True)


def picks(m, F, i: int, strat: int, n: int):
    """i번째 날의 '전 거래일 종가' 기준 종목 선택."""
    j = i - 1
    d = m["dates"][j]
    mc = m["mcap"].iloc[j]
    ok = m["alive"].iloc[j] & m["static_ok"] & ~m["flags"].iloc[j] & (m["val20"].iloc[j] >= MIN_VAL) & (mc > 0)
    ok &= m["alive"].iloc[i]                       # 리밸런싱 날 거래되는 종목만(정지 제외)
    mc = mc[ok]
    if len(mc) < 50:
        return []
    small = mc[mc <= mc.quantile(SMALL_Q)].index
    g = lambda k: F[k].iloc[j].reindex(small)
    cap = mc.reindex(small)
    eq, ni, rev, cfo, gp, assets = g("equity"), g("ni"), g("rev"), g("cfo"), g("gp"), g("assets")
    # 재무 단위 오류 방지: 자본·자산이 시총의 50배를 넘거나 자본 > 자산이면 버림
    bad = (eq > cap * 50) | (assets > cap * 200) | (eq > assets * 1.01)
    eq, ni, rev, cfo, gp, assets = (x.where(~bad) for x in (eq, ni, rev, cfo, gp, assets))
    pbr = (cap / eq).where(eq > 0)
    per = (cap / ni).where(ni > 0)
    psr = (cap / rev).where(rev > 0)
    pcr = (cap / cfo).where(cfo > 0)
    gpa = (gp / assets).where(assets > 0)
    sh, sh1 = m["shares"].iloc[j].reindex(small), m["shares"].iloc[max(0, j - 250)].reindex(small)
    fscore = (ni > 0).astype(int) + (cfo > 0).astype(int) + (sh <= sh1 * 1.001).astype(int)
    if strat == 12:                                          # 초소형주 + 흑자 + 자본잠식 아님
        score = rank(cap).where((ni > 0) & (eq > 0))
    elif strat == 11:                                          # 저PBR + 흑자(최근 4분기 순이익 > 0)
        score = rank(pbr).where(ni > 0)
    elif strat == 0:
        score = rank(cap)                                     # 시가총액 가장 작은 순
    elif strat == 1:
        score = rank(pbr)
    elif strat == 2:
        score = rank(per) + rank(pbr) + rank(pcr)
        score = score.where(per.notna() & pbr.notna() & pcr.notna())
    elif strat == 3:
        score = rank(per) + rank(pbr) + rank(psr) + rank(pcr)
        score = score.where(per.notna() & pbr.notna() & psr.notna() & pcr.notna())
    elif strat == 4:
        score = rank(pbr) + rank(gpa, asc=False)
        score = score.where(pbr.notna() & gpa.notna())
    elif strat == 5:
        score = rank(pbr).where((fscore == 3) & cfo.notna())
    elif strat == 6:
        score = rank(gpa, asc=False).where((fscore == 3) & cfo.notna())
    elif strat == 7:
        # 12개월 수익률(조정): 누적 수익률 지수로 계산
        cum = m["cumidx"]
        mom = cum.iloc[j].reindex(small) / cum.iloc[max(0, j - 250)].reindex(small) - 1
        v = rank(per) + rank(pbr) + rank(psr) + rank(pcr)
        score = rank(v) + rank(gpa, asc=False) + rank(mom, asc=False)
        score = score.where(v.notna() & gpa.notna() & mom.notna())
    else:
        raise ValueError(strat)
    score = score.dropna().sort_values()
    return list(score.index[:n])


NAMES = {0: "초소형주(시총 하위)", 12: "초소형주+흑자", 11: "저PBR+흑자", 1: "소형주 저PBR", 2: "파마+LSV 콤보", 3: "슈퍼가치", 4: "신마법공식 2.0", 5: "F-스코어", 6: "슈퍼퀄리티", 7: "짬뽕"}
NEEDS_FULL = {2, 3, 5, 6, 7}           # 영업현금흐름 필요
NEEDS_GP = {4, 6, 7}


def reb_days(dates, freq):
    idx = []
    prev = None
    for i, d in enumerate(dates):
        if d < START:
            continue
        y, mo = d[:4], d[4:6]
        if freq == "D":
            key = d
        elif freq == "W":
            key = pd.Timestamp(d).isocalendar()[:2]
        elif freq == "M":
            key = (y, mo)
        elif freq == "Q":
            key = (y, mo) if mo in ("04", "06", "09", "12") else prev
        elif freq == "Y":
            key = (y, mo) if mo == "04" else prev
        if key != prev and key is not None:
            idx.append(i)
        prev = key
    return idx


def simulate(m, F, strat, freq, n, cost=True):
    dates = m["dates"]
    rd = reb_days(dates, freq)
    if not rd:
        return None
    start = rd[0]
    rset = set(rd)
    gap, intra, ret = m["gap"].values, m["intra"].values, m["ret"].values
    alive = m["alive"].values
    col = {c: k for k, c in enumerate(m["codes"])}
    w = {}                     # 종목 → 금액(계좌 1 기준)
    cash = 1.0
    eq = []
    traded = 0.0
    costpaid = 0.0
    nhold = []
    for i in range(start, len(dates)):
        d = dates[i]
        # 오늘 시가까지: 보유 종목에 갭 적용(상장폐지·정지면 그대로)
        for c in list(w):
            k = col[c]
            if alive[i, k] and not np.isnan(gap[i, k]):
                w[c] *= 1 + gap[i, k]
        if i in rset:
            tgt = picks(m, F, i, strat, n)
            total = cash + sum(w.values())
            tw = {c: total / len(tgt) for c in tgt} if tgt else {}
            tax = sell_tax(d)
            tr0, cp0 = 0.0, 0.0
            for c in set(w) | set(tw):
                cur, new = w.get(c, 0.0), tw.get(c, 0.0)
                delta = new - cur
                if abs(delta) < 1e-12:
                    continue
                tr0 += abs(delta)
                if cost:
                    fee = abs(delta) * (COMM + SLIP) + (abs(delta) * tax if delta < 0 else 0.0)
                    cp0 += fee
                    cash -= fee
                cash -= delta
                if new > 0:
                    w[c] = new
                else:
                    w.pop(c, None)
            traded += tr0 / total / 2          # 회전율: 한쪽(매수 또는 매도) 기준, 계좌 대비
            costpaid += cp0 / total
            # 비용 차감으로 생긴 음수 현금은 비중을 살짝 줄여 맞춤
            if cash < 0 and w:
                s = sum(w.values())
                for c in w:
                    w[c] *= (s + cash) / s
                cash = 0.0
        # 시가 → 종가
        for c in list(w):
            k = col[c]
            if alive[i, k]:
                x = intra[i, k] if not np.isnan(intra[i, k]) else 0.0
                w[c] *= 1 + x
            elif _gone(alive, i, k):
                cash += w.pop(c)              # 상장폐지: 마지막 거래 가격(정리매매 포함)으로 현금화
        eq.append(cash + sum(w.values()))
        nhold.append(len(w))
    e = pd.Series(eq, index=pd.to_datetime(dates[start:]))
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    cagr = e.iloc[-1] ** (1 / yrs) - 1
    mdd = (e / e.cummax() - 1).min()
    yearly = e.resample("YE").last().pct_change()
    yearly.iloc[0] = e.resample("YE").last().iloc[0] / 1.0 - 1
    return {"cagr": cagr, "mdd": mdd, "turnover": traded / yrs, "cost": costpaid / yrs, "final": e.iloc[-1],
            "yearly": {str(k.year): round(float(v) * 100, 1) for k, v in yearly.items()},
            "hold": float(np.mean(nhold)), "curve": e}


def _gone(alive, i, k):
    return not alive[i:, k].any()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--strats", default="1,2,3,4,5,6,7")
    ap.add_argument("--freqs", default="Y,Q,M,W,D")
    ap.add_argument("--ns", default="10,20,30")
    a = ap.parse_args()
    t = time.time()
    m = load()
    m["cumidx"] = (1 + m["ret"]).cumprod()
    log(f"시세 {len(m['dates'])}일 × {len(m['codes'])}종목 ({time.time() - t:.0f}초)")
    F = fundamentals(m)
    has_cfo = F["cfo"].notna().any().any()
    has_gp = F["gp"].notna().any().any()
    rows = []
    freqs = ["Q"] if a.quick else a.freqs.split(",")
    for s in map(int, a.strats.split(",")):
        if (s in NEEDS_FULL and not has_cfo) or (s in NEEDS_GP and not has_gp):
            log(f"{NAMES[s]}: 영업현금흐름·매출총이익 데이터가 아직 없어 건너뜀")
            continue
        for f in freqs:
            for n in map(int, a.ns.split(",")):
                r = simulate(m, F, s, f, n)
                if r is None:
                    continue
                rows.append({"strat": s, "name": NAMES[s], "freq": f, "n": n, **{k: v for k, v in r.items() if k != "curve"}})
                log(f"{NAMES[s]:10s} {f} {n:2d}종목: 연 {r['cagr']*100:5.1f}% · 낙폭 {r['mdd']*100:5.1f}% · 회전 {r['turnover']:.1f}배/년 · 비용 {r['cost']*100:.1f}%/년")
    OUT.write_text(json.dumps({"made": dt.date.today().isoformat(), "rows": rows}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

"""과거 데이터 묶음 → docs/hist.json (월 1회, GitHub Actions).

웹앱 '내 포트폴리오' 탭이 이 파일만 받아서 폰에서 바로 백테스트한다(서버·토큰 불필요).

  python -m backtest.pack            # DART_API_KEY 필요

내용(코스피·코스닥 보통주, 현재 상장 종목만):
  - 월말 수정종가, 월말 20일 평균 거래대금(억원)
  - 연간 재무(FY2019~): 매출·영업이익·지배주주 순이익·지배주주 자본·부채·비지배지분·자산·투하자본(억원)
    · FY2023~ 순이익은 네이버 '지배주주순이익', 그 전은 DART 연결 순이익 × 지배 비율(최근 연도 평균)로 근사
  - 코스피·코스닥 지수 월말 값
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

from pipeline import config, dart, naver

from .run import BT, CACHE, log

OUT = config.ROOT / "docs" / "hist.json"
START = dt.date(2019, 7, 1)
FIELDS = ["rev", "op", "ni", "eq", "liab", "mi", "assets", "ic"]
EOK = 1e8


def universe():
    lst = naver.NaverSource(CACHE / "naver").listing()
    ok = (lst.index.str[-1] == "0") & ~lst.name.str.contains("스팩|리츠|인프라|REIT|제[0-9]+호")
    return lst[ok]


def fetch_px(codes: list[str], today: dt.date):
    """네이버 수정주가 일봉(2019-07~). 3일 안에 받은 파일은 재사용."""
    (BT / "px").mkdir(parents=True, exist_ok=True)
    n = int((today - START).days * 5 / 7) + 30
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    todo = [c for c in codes if not ((BT / "px" / f"{c}.csv").exists()
                                     and time.time() - (BT / "px" / f"{c}.csv").stat().st_mtime < 3 * 86400)]

    def px(code):
        for i in range(3):
            try:
                r = s.get(naver.CHART_URL, params={"symbol": code, "timeframe": "day", "count": n, "requestType": 0}, timeout=20)
                rows = naver.ITEM.findall(r.text)
                if rows:
                    pd.DataFrame(rows, columns=naver.HIST_COLS).to_csv(BT / "px" / f"{code}.csv", index=False)
                return
            except Exception:
                time.sleep(1 + i)

    t = time.time()
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(px, todo))
    log(f"일봉 {len(todo)}/{len(codes)}종목 새로 받음 ({time.time() - t:.0f}초)")


def monthly(codes, months_end: list[str]):
    """종목별 월말 종가·20일 평균 거래대금."""
    idx = {d: i for i, d in enumerate(months_end)}
    P = np.full((len(codes), len(months_end)), np.nan)
    V = np.full_like(P, np.nan)
    for j, c in enumerate(codes):
        p = BT / "px" / f"{c}.csv"
        if not p.exists():
            continue
        d = pd.read_csv(p, dtype={"date": str})
        d = d[d.close > 0]
        if d.empty:
            continue
        val = (d.close * d.vol).rolling(20, min_periods=5).mean().values
        dates, close = d.date.values, d.close.values
        # 각 월말 날짜 이전 마지막 거래일 값 (상장 전이면 비움)
        pos = np.searchsorted(dates, np.array(months_end), side="right") - 1
        for i, k in enumerate(pos):
            if k >= 0:                       # 거래정지 기간은 직전 가격 유지(수익률 0)
                P[j, i] = close[k]
                V[j, i] = val[k]
    return P, V


def dart_annual(codes, last_fy):
    dc = dart.DartClient(os.environ.get("DART_API_KEY"))
    cc_p = CACHE / "dart" / "corp_codes.json"
    if cc_p.exists():
        cc = json.loads(cc_p.read_text(encoding="utf-8"))
    else:
        cc = dc.corp_codes()
        cc_p.parent.mkdir(parents=True, exist_ok=True)
        cc_p.write_text(json.dumps(cc), encoding="utf-8")
    out = {}
    for fy in range(2019, last_fy + 1):
        # backtest.run의 dart_{fy}.json(코스피만)과 섞이지 않게 별도 파일. 처음엔 그걸 씨앗으로 쓴다
        p, tp, seed = BT / f"pdart_{fy}.json", BT / f"pdart_{fy}_tried.json", BT / f"dart_{fy}.json"
        src = p if p.exists() else seed
        have = json.loads(src.read_text(encoding="utf-8")) if src.exists() else {}
        tried = set(json.loads(tp.read_text(encoding="utf-8"))) if tp.exists() else set(have)
        if fy == last_fy and p.exists() and time.time() - p.stat().st_mtime > 25 * 86400:
            tried = set(have)                        # 최근 연도는 늦게 낸 회사 다시 확인
        pairs = [(c, cc[c]) for c in codes if c in cc and c not in tried]
        if pairs:
            c2s = {v: c for c, v in pairs}
            batches = [[v for _, v in pairs[i:i + 100]] for i in range(0, len(pairs), 100)]
            t = time.time()
            with ThreadPoolExecutor(5) as ex:
                parts = list(ex.map(lambda b: dc.multi(b, fy, "11011"), batches))
            have.update(dart.parse_period([x for pt in parts for x in pt], "11011", c2s))
            tried |= {c for c, _ in pairs}
            p.write_text(json.dumps(have, ensure_ascii=False), encoding="utf-8")
            tp.write_text(json.dumps(sorted(tried)), encoding="utf-8")
            log(f"DART FY{fy}: +{len(pairs)}종목 조회, 보유 {len(have)}개사 ({time.time() - t:.0f}초)")
        out[fy] = have
    return out


def naver_annual(codes):
    """네이버 연간 재무(최근 3개 연도): 지배주주 순이익·BPS·부채비율."""
    d = BT / "nann"
    d.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"

    def one(code):
        p = d / f"{code}.json"
        if not (p.exists() and time.time() - p.stat().st_mtime < 25 * 86400):
            try:
                r = s.get(f"https://m.stock.naver.com/api/stock/{code}/finance/annual", timeout=10)
                if r.status_code == 200:
                    p.write_text(r.text, encoding="utf-8")
            except requests.RequestException:
                pass
        if not p.exists():
            return code, {}
        try:
            fi = json.loads(p.read_text(encoding="utf-8")).get("financeInfo") or {}
        except ValueError:
            return code, {}
        keys = [x["key"] for x in fi.get("trTitleList", []) if x.get("isConsensus") == "N"]
        rows = {r.get("title"): r.get("columns") or {} for r in fi.get("rowList", [])}
        res = {}
        for k in keys:
            g = lambda t: naver._num((rows.get(t, {}).get(k) or {}).get("value"))
            res[int(k[:4])] = {"rev": g("매출액"), "op": g("영업이익"), "ni_all": g("당기순이익"),
                               "ni_ctrl": g("지배주주순이익"), "bps": g("BPS"), "debt": g("부채비율")}
        return code, res

    t = time.time()
    with ThreadPoolExecutor(6) as ex:
        res = dict(ex.map(one, codes))
    log(f"네이버 연간 재무 {sum(1 for v in res.values() if v)}/{len(codes)}종목 ({time.time() - t:.0f}초)")
    return res


def fundamentals(codes, shares, dfin, nfin, years):
    """FY별 [종목 × FIELDS] 행렬(억원)."""
    out = {}
    # 지배 비율: 네이버 연도들의 지배/연결 순이익 비(둘 다 흑자일 때) 중앙값
    ratio = {}
    for c in codes:
        rs = [v["ni_ctrl"] / v["ni_all"] for v in nfin.get(c, {}).values()
              if v.get("ni_ctrl") and v.get("ni_all") and v["ni_all"] > 0 and v["ni_ctrl"] > 0]
        ratio[c] = float(np.clip(np.median(rs), 0.05, 1.0)) if rs else 1.0
    for fy in years:
        M = np.full((len(codes), len(FIELDS)), np.nan)
        for j, c in enumerate(codes):
            d = (dfin.get(fy) or {}).get(c) or {}
            n = nfin.get(c, {}).get(fy) or {}
            r = ratio[c]
            eq_all = d.get("equity")
            rev = d.get("rev") if d.get("rev") is not None else (n["rev"] * EOK if n.get("rev") is not None else None)
            op = d.get("op") if d.get("op") is not None else (n["op"] * EOK if n.get("op") is not None else None)
            if n.get("ni_ctrl") is not None:
                ni = n["ni_ctrl"] * EOK
            elif d.get("ni") is not None:
                ni = d["ni"] * r
            else:
                ni = None
            if eq_all is not None:
                eq = eq_all * r
                liab = d.get("liab")
                assets = d.get("assets")
            elif n.get("bps") is not None and shares.get(c):
                eq = n["bps"] * shares[c]
                liab = n["debt"] / 100 * eq if n.get("debt") is not None else None
                assets = eq + liab if liab is not None else None
                eq_all = eq
            else:
                eq = liab = assets = None
            mi = (eq_all - eq) if (eq_all is not None and eq is not None) else None
            ic = None
            if all(d.get(x) is not None for x in ("ca", "cl", "nca")):
                ic = d["ca"] - d["cl"] + d["nca"]
            elif assets is not None:
                ic = assets
            M[j] = [np.nan if v is None else v / EOK for v in (rev, op, ni, eq, liab, mi, assets, ic)]
        out[fy] = M
        log(f"  FY{fy}: 재무 {int(np.isfinite(M[:, 2]).sum())}종목")
    return out


def rnd(a, nd=0):
    """NaN → None, 반올림 리스트."""
    return [None if not np.isfinite(x) else (round(float(x), nd) if nd else int(round(float(x)))) for x in a]


def build(today: dt.date | None = None):
    today = today or dt.datetime.now(config.KST).date()
    t0 = time.time()
    k = universe()
    codes = list(k.index)
    log(f"종목 {len(codes)}개 (코스피 {int((k.mkt == 'KOSPI').sum())}, 코스닥 {int((k.mkt == 'KOSDAQ').sum())})")
    fetch_px(codes + ["KOSPI", "KOSDAQ"], today)

    kx = pd.read_csv(BT / "px" / "KOSPI.csv", dtype={"date": str})
    days = kx.date.tolist()
    ends = []
    for i, d in enumerate(days):
        if d >= START.strftime("%Y%m%d") and (i == len(days) - 1 or days[i + 1][:6] != d[:6]):
            ends.append(d)
    P, V = monthly(codes, ends)
    IX, _ = monthly(["KOSPI", "KOSDAQ"], ends)

    last_fy = today.year - 1 if today >= dt.date(today.year, 4, 15) else today.year - 2
    years = list(range(2019, last_fy + 1))
    dfin = dart_annual(codes, last_fy)
    nfin = naver_annual(codes)
    shares = {c: float(s) for c, s in k["shares"].items() if pd.notna(s)}
    fin = fundamentals(codes, shares, dfin, nfin, years)

    data = {
        "generated": dt.datetime.now(config.KST).strftime("%Y-%m-%d %H:%M"),
        "dates": ends,                                         # 월말 거래일 (마지막은 최신 거래일)
        "codes": codes,
        "names": k["name"].tolist(),
        "mkt": ["K" if m == "KOSPI" else "Q" for m in k["mkt"]],
        "shares": [shares.get(c) for c in codes],
        "px": [rnd(row) for row in P],                         # 수정종가(원)
        "tv": [rnd(row, 1) for row in V / EOK],                # 20일 평균 거래대금(억원)
        "idx": {"KOSPI": rnd(IX[0], 2), "KOSDAQ": rnd(IX[1], 2)},
        "fields": FIELDS,
        "fin": {str(fy): [rnd(row, 1) for row in M] for fy, M in fin.items()},   # 억원
        "fin_from": "04",                                      # FY 재무는 다음 해 4월 말부터 사용
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"저장 {OUT.name} {OUT.stat().st_size / 1e6:.1f}MB, 월 {len(ends)}개 ({time.time() - t0:.0f}초)")


if __name__ == "__main__":
    build()

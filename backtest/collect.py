"""소형주 전략 백테스트용 과거 데이터 모으기 → store/ (data 브랜치에 커밋).

생존 편향을 없애려고 KRX '날짜별 전 종목' 자료를 쓴다(그날 상장돼 있던 종목 전부 = 나중에 상장폐지된 종목 포함).
DART는 하루 호출 한도(2만 건)가 있어 여러 날에 걸쳐 이어받는다.

  python -m backtest.collect krx        # 1) KRX 전 종목 일별 (2015-01~)
  python -m backtest.collect disc       # 2) DART 위험 공시 목록 (주요사항보고서·거래소공시)
  python -m backtest.collect multi      # 3) DART 요약 재무 (100개 회사씩 → 매출·이익·자본, 연결/별도 여부)
  python -m backtest.collect fin        # 4) DART 전체 재무 (매출총이익·영업현금흐름, 한 회사씩 · 한도 안에서)
  python -m backtest.collect status     # 진행 상황
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import sys
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

from pipeline import config

ROOT = config.ROOT
STORE = ROOT / "store"
KRX_DIR = STORE / "krx"
FIN_DIR = STORE / "fin"
DISC_DIR = STORE / "disc"
START = dt.date(2015, 1, 2)
DART = "https://opendart.fss.or.kr/api/"
KRX = "https://data-dbg.krx.co.kr/svc/apis/sto/"
DART_BUDGET = int(os.environ.get("DART_BUDGET", "17000"))   # 하루 2만 건 중 매일 돌리는 작업 몫을 남김


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def today_kst() -> dt.date:
    return (dt.datetime.utcnow() + dt.timedelta(hours=9)).date()


# ---------------------------------------------------------------- KRX
KCOLS = {"BAS_DD": "date", "ISU_CD": "code", "ISU_NM": "name", "SECT_TP_NM": "sect", "TDD_CLSPRC": "close",
         "TDD_OPNPRC": "open", "FLUC_RT": "chg", "ACC_TRDVAL": "val", "MKTCAP": "mcap", "LIST_SHRS": "shares"}


def _krx_day(s, ymd, mkt):
    ep = "stk_bydd_trd" if mkt == "KOSPI" else "ksq_bydd_trd"
    for i in range(6):
        try:
            r = s.get(KRX + ep, params={"basDd": ymd}, timeout=40)
            if r.status_code == 200:
                return r.json().get("OutBlock_1") or []
            if r.status_code in (401, 403):
                raise RuntimeError(f"KRX 인증 실패 {r.status_code}: {r.text[:120]}")
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"KRX 실패 {mkt} {ymd}")


def krx():
    KRX_DIR.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update({"AUTH_KEY": os.environ["KRX_API_KEY"].strip(), "User-Agent": "quant-dashboard/1.0"})
    empty_p = KRX_DIR / "holidays.json"
    holidays = set(json.loads(empty_p.read_text())) if empty_p.exists() else set()
    end = today_kst() - dt.timedelta(days=1)
    for year in range(START.year, end.year + 1):
        p = KRX_DIR / f"{year}.parquet"
        old = pd.read_parquet(p) if p.exists() else None
        have = set(old["date"].unique()) if old is not None else set()
        days = [d for d in pd.bdate_range(max(START, dt.date(year, 1, 1)), min(end, dt.date(year, 12, 31)))
                if d.strftime("%Y%m%d") not in have and d.strftime("%Y%m%d") not in holidays]
        if not days:
            continue
        t = time.time()

        def one(d):
            ymd = d.strftime("%Y%m%d")
            rows = _krx_day(s, ymd, "KOSPI") + _krx_day(s, ymd, "KOSDAQ")
            return ymd, rows

        frames, names = [], {}
        with ThreadPoolExecutor(4) as ex:
            for ymd, rows in ex.map(one, days):
                if not rows:
                    holidays.add(ymd)
                    continue
                df = pd.DataFrame(rows)
                df["mkt"] = df["MKT_NM"].map({"KOSPI": "P", "KOSDAQ": "Q"}).fillna("Q") if "MKT_NM" in df else "?"
                df = df.rename(columns=KCOLS)[list(KCOLS.values()) + ["mkt"]]
                for c in ("close", "open", "chg", "val", "mcap", "shares"):
                    df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce")
                frames.append(df)
        if frames:
            new = pd.concat(frames, ignore_index=True)
            new["close"] = new["close"].astype("float32")
            new["open"] = new["open"].astype("float32")
            new["chg"] = new["chg"].astype("float32")
            new["shares"] = new["shares"].astype("float64")
            allf = pd.concat([old, new], ignore_index=True) if old is not None else new
            allf = allf.drop_duplicates(["date", "code"], keep="last").sort_values(["date", "code"])
            for c in ("code", "name", "sect", "mkt", "date"):
                allf[c] = allf[c].astype("category")
            allf.to_parquet(p, index=False, compression="zstd")
        empty_p.write_text(json.dumps(sorted(holidays)))
        log(f"KRX {year}: +{len(days)}일 ({time.time() - t:.0f}초)")


def krx_all() -> pd.DataFrame:
    fs = sorted(KRX_DIR.glob("*.parquet"))
    df = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    for c in ("code", "name", "sect", "mkt", "date"):
        df[c] = df[c].astype(str)
    return df


# ---------------------------------------------------------------- DART 공통
class Dart:
    def __init__(self):
        self.key = os.environ["DART_API_KEY"].strip()
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "quant-dashboard/1.0"})
        self.used = 0
        self.lock = threading.Lock()
        self.stop = False
        self.next_t = 0.0                      # 요청 간격 제한 (분당 약 600건)
        self.gap = float(os.environ.get("DART_GAP", "0.1"))
        self.codes = {}

    def get(self, path, **p):
        with self.lock:
            if self.used >= DART_BUDGET or self.stop:
                return None
            self.used += 1
            wait = self.next_t - time.time()
            self.next_t = max(time.time(), self.next_t) + self.gap
        if wait > 0:
            time.sleep(wait)
        p["crtfc_key"] = self.key
        for i in range(4):
            try:
                r = self.s.get(DART + path, params=p, timeout=25)
                if r.status_code != 200:
                    raise ValueError(f"HTTP {r.status_code}")
                j = r.json()
                st = j.get("status")
                with self.lock:
                    self.codes[st] = self.codes.get(st, 0) + 1
                if st == "020":                      # 하루 한도 초과
                    self.stop = True
                    log("DART 하루 한도 도달 — 내일 이어받음")
                    return None
                return j
            except (requests.RequestException, ValueError) as e:
                log(f"  DART 재시도 {i + 1}: {path} {type(e).__name__} {str(e)[:60]}")
                time.sleep(5 * (i + 1))
        return {"status": "ERR"}

    def corp_codes(self) -> pd.DataFrame:
        r = self.s.get(DART + "corpCode.xml", params={"crtfc_key": self.key}, timeout=90)
        root = ET.fromstring(zipfile.ZipFile(io.BytesIO(r.content)).read("CORPCODE.xml"))
        rows = [((el.findtext("stock_code") or "").strip(), el.findtext("corp_code").strip(), el.findtext("corp_name").strip())
                for el in root.iter("list")]
        return pd.DataFrame(rows, columns=["stock", "corp", "name"])


# ---------------------------------------------------------------- 위험 공시 목록
def disc():
    DISC_DIR.mkdir(parents=True, exist_ok=True)
    d = Dart()
    end = today_kst()
    for ty in ("B", "I"):
        q0 = dt.date(2015, 4, 1)
        while q0 <= end:
            q1 = min((pd.Timestamp(q0) + pd.DateOffset(months=3) - pd.Timedelta(days=1)).date(), end)
            p = DISC_DIR / f"{ty}_{q0:%Y%m}.parquet"
            if p.exists() and q1 < end - dt.timedelta(days=3):
                q0 = (pd.Timestamp(q0) + pd.DateOffset(months=3)).date()
                continue
            rows, page, total = [], 1, 1
            while page <= total:
                j = d.get("list.json", bgn_de=q0.strftime("%Y%m%d"), end_de=q1.strftime("%Y%m%d"), pblntf_ty=ty,
                          page_no=page, page_count=100)
                if j is None:
                    return log(f"공시 목록: 한도로 중단 ({d.used}건)")
                if j.get("status") not in ("000", "013"):
                    log("공시 오류", ty, q0, j.get("status"), j.get("message"))
                    time.sleep(3)
                    continue
                total = int(j.get("total_page") or 0)
                for x in j.get("list") or []:
                    if x.get("corp_cls") in ("Y", "K") and x.get("stock_code"):
                        rows.append((x["rcept_dt"], x["stock_code"], x["corp_name"], x["report_nm"], x["rcept_no"]))
                page += 1
            df = pd.DataFrame(rows, columns=["date", "stock", "name", "report", "rcept_no"])
            if ty == "I":                                   # 거래소공시는 위험 관련만 남김
                df = df[df.report.str.contains(RISK_I)]
            df.to_parquet(p, index=False)
            log(f"공시 {ty} {q0:%Y-%m}: {len(df)}건 (누적 호출 {d.used})")
            q0 = (pd.Timestamp(q0) + pd.DateOffset(months=3)).date()


RISK_I = "횡령|배임|불성실공시|감사의견|관리종목|상장적격성|상장폐지|매매거래정지|회생|파산|부도|최대주주변경|투자주의환기"


# ---------------------------------------------------------------- 분기 재무
RPT = {"11013": "Q1", "11012": "H1", "11014": "Q3", "11011": "FY"}
ID = {
    "rev": ("ifrs-full_Revenue", "ifrs_Revenue"),
    "gp": ("ifrs-full_GrossProfit", "ifrs_GrossProfit"),
    "op": ("dart_OperatingIncomeLoss",),
    "ni": ("ifrs-full_ProfitLoss", "ifrs_ProfitLoss"),
    "cfo": ("ifrs-full_CashFlowsFromUsedInOperatingActivities", "ifrs_CashFlowsFromUsedInOperatingActivities"),
    "assets": ("ifrs-full_Assets", "ifrs_Assets"),
    "equity": ("ifrs-full_Equity", "ifrs_Equity"),
    "eq_parent": ("ifrs-full_EquityAttributableToOwnersOfParent", "ifrs_EquityAttributableToOwnersOfParent"),
}
NAME = {
    "rev": ("매출액", "수익(매출액)", "영업수익", "매출", "수익", "영업수익(매출액)"),
    "gp": ("매출총이익", "매출총이익(손실)", "매출총손익"),
    "op": ("영업이익", "영업이익(손실)", "영업손익"),
    "ni": ("당기순이익", "당기순이익(손실)", "분기순이익", "분기순이익(손실)", "반기순이익", "반기순이익(손실)", "당기순손익", "분기순손익", "반기순손익"),
    "cfo": ("영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동으로부터의현금흐름", "영업활동순현금흐름", "영업활동에서창출된현금흐름"),
    "assets": ("자산총계",),
    "equity": ("자본총계",),
    "eq_parent": ("지배기업의소유주에게귀속되는자본", "지배기업소유주지분", "지배기업의소유지분"),
}
NAME_N = {k: {re.sub(r"[\s·ㆍ]", "", n) for n in v} for k, v in NAME.items()}


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_fin(lst: list[dict], reprt: str) -> dict:
    """한 보고서 → 누적값(올해·작년 같은 기간)과 기말 잔액."""
    out = {}
    q = reprt != "11011"
    for x in lst:
        sj = x.get("sj_div")
        nm = re.sub(r"[\s·ㆍ]", "", x.get("account_nm") or "")
        aid = x.get("account_id") or ""
        for k in ID:
            if k in out and out[k + "_by"] == "id":
                continue
            hit = "id" if aid in ID[k] else ("nm" if nm in NAME_N[k] else None)
            if not hit or (k in out and hit == "nm"):
                continue
            if k in ("assets", "equity", "eq_parent"):
                if sj != "BS":
                    continue
                cur, prev = _num(x.get("thstrm_amount")), None
            elif k == "cfo":
                if sj != "CF":
                    continue
                cur, prev = _num(x.get("thstrm_amount")), _num(x.get("frmtrm_amount"))
            else:
                if sj not in ("IS", "CIS"):
                    continue
                if q:
                    cur = _num(x.get("thstrm_add_amount")) if x.get("thstrm_add_amount") not in (None, "", "-") else _num(x.get("thstrm_amount"))
                    prev = _num(x.get("frmtrm_add_amount")) if x.get("frmtrm_add_amount") not in (None, "", "-") else _num(x.get("frmtrm_q_amount"))
                else:
                    cur, prev = _num(x.get("thstrm_amount")), _num(x.get("frmtrm_amount"))
            if cur is None:
                continue
            out[k], out[k + "_p"], out[k + "_by"] = cur, prev, hit
    return {k: v for k, v in out.items() if not k.endswith("_by")}


def fin_tasks(px: pd.DataFrame, cc: dict) -> list[tuple]:
    """리밸런싱(4·6·9·12월 첫 거래일)마다 시총 하위 30% 후보 → 필요한 (회사, 연도, 보고서) 목록."""
    px = px[(px.code.str[-1] == "0") & ~px.sect.str.contains("SPAC|관리|환기", na=False)
            & ~px.name.str.contains("스팩|리츠|REIT|인프라|선박|기업인수목적", na=False)]
    days = sorted(px.date.unique())
    need = []
    for y in range(2016, today_kst().year + 1):
        for m, (ry, rp) in ((4, (y - 1, "11011")), (6, (y, "11013")), (9, (y, "11012")), (12, (y, "11014"))):
            first = next((d for d in days if d >= f"{y}{m:02d}01"), None)
            if not first or first[:6] != f"{y}{m:02d}":
                continue
            snap = px[px.date == first]
            cut = snap.mcap.quantile(0.30)
            for code in snap[snap.mcap <= cut].code:
                corp = cc.get(code)
                if not corp:
                    continue
                need.append((corp, ry, rp))
                need.append((corp, (y - 1) if rp != "11011" else ry, "11011"))
    return sorted(set(need), key=lambda t: (t[1], t[2], t[0]))


def corp_map(px: pd.DataFrame | None = None) -> dict:
    """KRX 종목코드 → DART 회사코드. 상장폐지돼 corpCode에 종목코드가 없는 회사는 이름으로(같은 이름이 하나뿐일 때)."""
    ccdf = pd.read_parquet(STORE / "corp_codes.parquet")
    cc = dict(zip(ccdf[ccdf.stock != ""].stock, ccdf[ccdf.stock != ""].corp))
    if px is None:
        px = krx_all()[["date", "code", "name"]]
    by_name = ccdf.groupby("name").corp.agg(list)
    for code, name in px.drop_duplicates("code", keep="last")[["code", "name"]].itertuples(index=False):
        if code not in cc and name in by_name.index and len(by_name[name]) == 1:
            cc[code] = by_name[name][0]
    return cc


def _cands(d):
    FIN_DIR.mkdir(parents=True, exist_ok=True)
    cc_p = STORE / "corp_codes.parquet"
    if not cc_p.exists() or time.time() - cc_p.stat().st_mtime > 20 * 86400:
        d.corp_codes().to_parquet(cc_p, index=False)
    px = krx_all()[["date", "code", "name", "sect", "mcap"]]
    cc = corp_map(px)
    tasks = fin_tasks(px, cc)
    log(f"후보 (회사·보고서) {len(tasks)}건 · 종목코드↔DART 연결 {sum(1 for c in px.code.unique() if c in cc)}/{px.code.nunique()}")
    return tasks


MACCT = {"매출액": "rev", "영업수익": "rev", "수익(매출액)": "rev", "매출": "rev", "영업이익": "op",
         "당기순이익": "ni", "분기순이익": "ni", "반기순이익": "ni", "자산총계": "assets", "자본총계": "equity"}


def multi():
    """요약 재무(fnlttMultiAcnt, 100개 회사씩): 매출·영업이익·순이익(올해·작년 같은 기간 누적), 자산·자본, 연결/별도 여부."""
    d = Dart()
    tasks = _cands(d)
    out_p = FIN_DIR / "multi.parquet"
    old = pd.read_parquet(out_p) if out_p.exists() else None
    done_p = FIN_DIR / "multi_done.json"
    done = set(json.loads(done_p.read_text())) if done_p.exists() else set()
    per = {}
    for corp, y, rp in tasks:
        if f"{corp}_{y}_{rp}" not in done:
            per.setdefault((y, rp), []).append(corp)
    log(f"요약 재무: {len(per)}개 기간, {sum(len(v) for v in per.values())}개 회사·기간 남음")
    rows = []
    t0 = time.time()
    for pi, ((y, rp), corps) in enumerate(sorted(per.items())):
        cum = rp in ("11012", "11014")
        tp = time.time()
        for i in range(0, len(corps), 100):
            b = corps[i:i + 100]
            tc = time.time()
            j = d.get("fnlttMultiAcnt.json", corp_code=",".join(b), bsns_year=y, reprt_code=rp)
            if time.time() - tc > 20:
                log(f"  느린 응답 {time.time() - tc:.0f}초 ({y} {rp} {i})")
            if j is None:
                break
            if j.get("status") not in ("000", "013"):
                log("요약 재무 오류", y, rp, j.get("status"), j.get("message"))
                continue
            rec = {}
            for r in j.get("list") or []:
                k = MACCT.get(re.sub(r"\s+", "", str(r.get("account_nm") or "")).replace("(손실)", ""))
                if not k:
                    continue
                fs = r.get("fs_div") or "OFS"
                c = rec.setdefault((r["corp_code"], fs), {"rcept_no": r.get("rcept_no")})
                if k in ("rev", "op", "ni"):
                    cur = _num(r.get("thstrm_add_amount")) if cum else None
                    cur = cur if cur is not None else _num(r.get("thstrm_amount"))
                    prev = _num(r.get("frmtrm_add_amount")) if cum else None
                    prev = prev if prev is not None else _num(r.get("frmtrm_amount"))
                    c.setdefault(k, cur); c.setdefault(k + "_p", prev)
                else:
                    c.setdefault(k, _num(r.get("thstrm_amount")))
            for (corp, fs), v in rec.items():
                rows.append({"corp": corp, "year": y, "rpt": rp, "fs": fs, **v})
            done.update(f"{c}_{y}_{rp}" for c in b)
        # 기간마다 저장(중간에 끊겨도 이어받기)
        if rows:
            new = pd.DataFrame(rows)
            old = pd.concat([old, new], ignore_index=True) if old is not None else new
            old = old.drop_duplicates(["corp", "year", "rpt", "fs"], keep="last")
            old.to_parquet(out_p, index=False)
            rows = []
        done_p.write_text(json.dumps(sorted(done)))
        log(f"요약 재무 {pi + 1}/{len(per)} {y} {rp}: {len(corps)}개 회사 · {time.time() - tp:.0f}초 · 호출 누적 {d.used} · 전체 {time.time() - t0:.0f}초")
        if d.stop:
            break
    log(f"요약 재무 끝 · 호출 {d.used}")


def fin(limit: int | None = None):
    d = Dart()
    tasks = _cands(d)
    mp = FIN_DIR / "multi.parquet"
    fsmap = {}
    if mp.exists():
        m = pd.read_parquet(mp, columns=["corp", "year", "rpt", "fs"])
        for (corp, y, rp), g in m.groupby(["corp", "year", "rpt"]).fs:
            fsmap[(corp, int(y), rp)] = "CFS" if "CFS" in set(g) else "OFS"
    # 연간 보고서 먼저(모든 해) → 그다음 분기 보고서: 하루치만 모여도 연 1회 리밸런싱 시험이 가능하게
    tasks = sorted(tasks, key=lambda t: (t[2] != "11011", t[1], t[2], t[0]))
    done_p = FIN_DIR / "done.json"
    done = set(json.loads(done_p.read_text())) if done_p.exists() else set()
    todo = [t for t in tasks if f"{t[0]}_{t[1]}_{t[2]}" not in done]
    if fsmap:      # 요약 재무에 아예 없는 회사·기간은 건너뜀(제출 안 함)
        todo = [t for t in todo if (t[0], t[1], t[2]) in fsmap]
    if limit:
        todo = todo[:limit]
    log(f"전체 재무: 남은 {len(todo)}건")
    rows, lock = [], threading.Lock()

    def one(t):
        corp, y, rp = t
        rec = None
        for fs in ((fsmap[(corp, y, rp)],) if (corp, y, rp) in fsmap else ("CFS", "OFS")):
            j = d.get("fnlttSinglAcntAll.json", corp_code=corp, bsns_year=y, reprt_code=rp, fs_div=fs)
            if j is None:
                return
            if j.get("status") == "000" and j.get("list"):
                v = parse_fin(j["list"], rp)
                rec = {"corp": corp, "year": y, "rpt": rp, "fs": fs, "rcept_no": j["list"][0].get("rcept_no"), **v}
                break
            if j.get("status") not in ("013",):
                return                                   # 일시 오류 → 다음에 다시
        with lock:
            done.add(f"{corp}_{y}_{rp}")
            if rec:
                rows.append(rec)

    t0 = time.time()
    for i in range(0, len(todo), 2000):
        nth = int(os.environ.get("DART_THREADS", "1"))
        tc = time.time()
        if nth == 1:
            for k, t in enumerate(todo[i:i + 2000]):
                one(t)
                if k % 200 == 199:
                    log(f"  {i + k + 1}건 · {(k + 1) / (time.time() - tc):.1f}건/초 · 응답 {d.codes}")
                if d.stop:
                    break
        else:
            with ThreadPoolExecutor(nth) as ex:
                list(ex.map(one, todo[i:i + 2000]))
        _save_fin(rows, done, done_p)
        rows.clear()
        log(f"재무 {min(i + 2000, len(todo))}/{len(todo)} · 호출 {d.used} · {time.time() - t0:.0f}초 · 응답 {d.codes}")
        if d.stop or d.used >= DART_BUDGET:
            break


def _save_fin(rows, done, done_p):
    if rows:
        new = pd.DataFrame(rows)
        p = FIN_DIR / "fin.parquet"
        old = pd.read_parquet(p) if p.exists() else None
        allf = pd.concat([old, new], ignore_index=True) if old is not None else new
        allf.drop_duplicates(["corp", "year", "rpt"], keep="last").to_parquet(p, index=False)
    done_p.write_text(json.dumps(sorted(done)))


def probe():
    """DART 요청 속도 점검: 묶음 크기별 요약 재무, 단일 전체 재무."""
    d = Dart()
    ccdf = d.corp_codes()
    last = pd.read_parquet(sorted(KRX_DIR.glob("*.parquet"))[-1], columns=["code", "mcap"])
    last = last.drop_duplicates("code", keep="last").sort_values("mcap")
    cc = dict(zip(ccdf.stock, ccdf.corp))
    corps = [cc[c] for c in last.code.astype(str) if c in cc][:100]          # 지금 상장된 소형주 100개
    for y, rp in ((2024, "11011"), (2017, "11013")):
        for n in (5, 20, 50, 100):
            t = time.time()
            try:
                r = d.s.get(DART + "fnlttMultiAcnt.json", params={"crtfc_key": d.key, "corp_code": ",".join(corps[:n]), "bsns_year": y, "reprt_code": rp}, timeout=60)
                j = r.json()
                log(f"요약 {y} {rp} {n}개: {time.time() - t:.1f}초 status {j.get('status')} 행 {len(j.get('list') or [])}")
            except Exception as e:
                log(f"요약 {y} {rp} {n}개: 실패 {type(e).__name__} {time.time() - t:.1f}초")
    mm = pd.read_parquet(FIN_DIR / "multi.parquet", columns=["corp", "year", "rpt", "fs"])
    cfs = mm[(mm.fs == "CFS") & (mm.year == 2016) & (mm.rpt == "11011")].corp.unique()[:6]
    for fs in ("CFS", "OFS", "CFS"):
        ts = []
        for c in cfs:
            t = time.time()
            r = d.s.get(DART + "fnlttSinglAcntAll.json", params={"crtfc_key": d.key, "corp_code": c, "bsns_year": 2016, "reprt_code": "11011", "fs_div": fs}, timeout=60)
            ts.append((round(time.time() - t, 1), r.json().get("status"), len(r.content) // 1024))
        log(f"2016 사업보고서 {fs} (초, 상태, KB): {ts}")
    t = time.time(); r = d.s.get(DART + "fnlttSinglAcntAll.json", params={"crtfc_key": d.key, "corp_code": cfs[0], "bsns_year": "2016", "reprt_code": "11011", "fs_div": "CFS"}, timeout=60)
    log(f"문자열 연도: {time.time() - t:.1f}초")
    for y, rp in ((2016, "11011"),):
        ts = []
        for c in corps[:8]:
            t = time.time()
            try:
                r = d.s.get(DART + "fnlttSinglAcntAll.json", params={"crtfc_key": d.key, "corp_code": c, "bsns_year": y, "reprt_code": rp, "fs_div": "OFS"}, timeout=60)
                st = r.json().get("status")
            except Exception as e:
                st = type(e).__name__
            ts.append((round(time.time() - t, 1), st))
        log(f"전체 {y} {rp}: {ts}")


def status():
    fs = sorted(KRX_DIR.glob("*.parquet"))
    print("KRX:", ", ".join(f"{f.stem}({pd.read_parquet(f, columns=['date']).date.nunique()}일)" for f in fs) or "없음")
    print("공시:", len(list(DISC_DIR.glob("*.parquet"))), "분기 파일")
    p = FIN_DIR / "fin.parquet"
    if p.exists():
        f = pd.read_parquet(p)
        print("재무:", len(f), "건 ·", f.groupby("year").size().to_dict())
    dp = FIN_DIR / "done.json"
    print("재무 처리:", len(json.loads(dp.read_text())) if dp.exists() else 0)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    fn = {"krx": krx, "disc": disc, "multi": multi, "fin": fin, "status": status, "probe": probe}[cmd]
    fn(int(sys.argv[2])) if cmd == "fin" and len(sys.argv) > 2 else fn()

"""DART Open API — 다중회사 주요계정(fnlttMultiAcnt)으로 최근 재무 + TTM(최근 4분기 합) 계산."""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

BASE = "https://opendart.fss.or.kr/api/"
QEND = {"11013": (3, 31), "11012": (6, 30), "11014": (9, 30), "11011": (12, 31)}
NQ = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
ACCT = {
    "유동자산": "ca", "비유동자산": "nca", "자산총계": "assets",
    "유동부채": "cl", "비유동부채": "ncl", "부채총계": "liab", "자본총계": "equity",
    "매출액": "rev", "영업수익": "rev", "수익(매출액)": "rev", "매출": "rev",
    "영업이익": "op",
    "당기순이익": "ni", "분기순이익": "ni", "반기순이익": "ni",
}
FLOW = ("rev", "op", "ni")
BAL = ("ca", "nca", "assets", "cl", "ncl", "liab", "equity")


class DartError(RuntimeError):
    pass


class DartClient:
    def __init__(self, key: str | None):
        if not key:
            raise DartError("DART_API_KEY가 없습니다.")
        self.key = key.strip()
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "quant-dashboard/1.0"})

    def corp_codes(self) -> dict[str, str]:
        r = self.s.get(BASE + "corpCode.xml", params={"crtfc_key": self.key}, timeout=60)
        r.raise_for_status()
        if not r.content[:2] == b"PK":
            raise DartError(f"DART corpCode 오류: {r.text[:200]}")
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        root = ET.fromstring(zf.read(zf.namelist()[0]))
        out = {}
        for el in root.iter("list"):
            sc = (el.findtext("stock_code") or "").strip()
            if sc:
                out[sc] = (el.findtext("corp_code") or "").strip()
        return out

    def multi(self, corp_codes: list[str], year: int, reprt: str) -> list[dict]:
        params = {"crtfc_key": self.key, "corp_code": ",".join(corp_codes),
                  "bsns_year": str(year), "reprt_code": reprt}
        last = None
        for i in range(2):
            try:
                r = self.s.get(BASE + "fnlttMultiAcnt.json", params=params, timeout=20)
                js = r.json()
            except (requests.RequestException, ValueError) as e:
                last = e
                time.sleep(2 * (i + 1))
                continue
            st = js.get("status")
            if st == "000":
                return js.get("list") or []
            if st == "013":
                return []
            if st in ("010", "011", "012", "901"):
                raise DartError(f"DART 인증키 오류({st}): {js.get('message')}")
            if st == "020":
                raise DartError("DART 일일 요청 한도 초과")
            if st == "021" and len(corp_codes) > 1:   # 회사 수 초과 → 반으로 나눠 재요청
                h = len(corp_codes) // 2
                return self.multi(corp_codes[:h], year, reprt) + self.multi(corp_codes[h:], year, reprt)
            if st in ("800", "900"):
                last = js.get("message")
                time.sleep(5)
                continue
            raise DartError(f"DART 오류({st}): {js.get('message')}")
        raise DartError(f"DART 요청 실패: {last}")


def _amt(x):
    if x is None:
        return None
    s = str(x).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _norm(name) -> str:
    return re.sub(r"\s+", "", str(name or "")).replace("(손실)", "")


def parse_period(rows: list[dict], reprt: str, corp2stock: dict | None = None) -> dict[str, dict]:
    """한 보고서 기간의 응답 → {종목코드: {계정: 값, 'rev_prev': 전년 동기 누적, ...}} (연결 우선)"""
    cum = reprt in ("11012", "11014")
    recs: dict[str, dict] = {}
    for r in rows:
        sc = (r.get("stock_code") or "").strip() or (corp2stock or {}).get((r.get("corp_code") or "").strip(), "")
        if not sc:
            continue
        f = ACCT.get(_norm(r.get("account_nm")))
        if not f:
            continue
        fs = r.get("fs_div") or ("CFS" if "연결" in str(r.get("fs_nm") or "") else "OFS")
        d = recs.setdefault(sc, {}).setdefault(fs, {})
        if f in FLOW:
            cur = _amt(r.get("thstrm_add_amount")) if cum else None
            if cur is None:
                cur = _amt(r.get("thstrm_amount"))
            prev = _amt(r.get("frmtrm_add_amount")) if cum else None
            if prev is None:
                prev = _amt(r.get("frmtrm_amount"))
            if cur is not None:
                d.setdefault(f, cur)
            if prev is not None:
                d.setdefault(f + "_prev", prev)
        else:
            v = _amt(r.get("thstrm_amount"))
            if v is not None:
                d.setdefault(f, v)
    out = {}
    for sc, v in recs.items():
        c, o = v.get("CFS") or {}, v.get("OFS") or {}
        out[sc] = c if ("equity" in c or "ni" in c) else o
    return out


def candidate_periods(asof: dt.date, n: int = 5) -> list[tuple[int, str]]:
    out, y = [], asof.year
    while len(out) < n:
        for r in ("11011", "11014", "11012", "11013"):
            m, d = QEND[r]
            if dt.date(y, m, d) + dt.timedelta(days=20) <= asof:
                out.append((y, r))
                if len(out) >= n:
                    break
        y -= 1
    return out


def _label(y: int, r: str) -> str:
    return f"{y % 100:02d}.{NQ[r]}Q"


def load_fundamentals(client, stock_codes: list[str], asof: dt.date, cache_dir: Path,
                      workers: int = 3, budget_sec: int = 600, log=print) -> pd.DataFrame:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    cc_path = cache_dir / "corp_codes.json"
    cc = None
    if cc_path.exists() and (time.time() - cc_path.stat().st_mtime) < 7 * 86400:
        cc = json.loads(cc_path.read_text(encoding="utf-8"))
    if cc is None:
        t0 = time.time()
        cc = client.corp_codes()
        log(f"  DART 회사코드 {len(cc)}개 ({time.time() - t0:.0f}초)")
        cc_path.write_text(json.dumps(cc), encoding="utf-8")

    pairs = [(s, cc[s]) for s in stock_codes if s in cc]
    s2c, c2s = dict(pairs), {c: s for s, c in pairs}
    t0 = time.time()
    client.multi(["00126380"], asof.year - 1, "11011")   # 접속 확인(삼성전자 1개사). 실패하면 여기서 예외
    log(f"  DART 접속 확인 ({time.time() - t0:.1f}초)")
    deadline = time.time() + budget_sec

    # 회사별로 언제 받았는지 기록해 두고, 오래된 것만 다시 받는다(재무는 분기마다만 바뀜).
    # 시간 안에 못 받은 회사는 다음 실행 때 이어서 받는다.
    periods = candidate_periods(asof)
    data: dict[tuple[int, str], dict] = {}
    now = time.time()
    for idx, (y, r) in enumerate(periods):
        p = cache_dir / f"{y}_{r}.json"
        st = {"t": {}, "d": {}}
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if "t" in raw and "d" in raw:
                st = raw
            else:                                           # 예전 형식 변환
                st = {"t": {k: p.stat().st_mtime for k in raw}, "d": raw}
        ttl = (7 if idx < 2 else 90) * 86400
        need = [sc for sc, _ in pairs if now - st["t"].get(sc, 0) > ttl]
        batches = [need[i:i + 100] for i in range(0, len(need), 100)]
        t1, done, failed = time.time(), 0, 0
        for i in range(0, len(batches), workers):
            if time.time() > deadline:
                break
            chunk = batches[i:i + workers]

            def fetch(b):
                try:
                    return client.multi([s2c[x] for x in b], y, r)
                except DartError as e:
                    if "인증키" in str(e) or "한도" in str(e):
                        raise
                    return None

            with ThreadPoolExecutor(workers) as ex:
                results = list(ex.map(fetch, chunk))
            for b, rows in zip(chunk, results):
                if rows is None:
                    failed += 1
                    continue
                parsed = parse_period(rows, r, c2s)
                t = time.time()
                for x in b:
                    st["d"][x] = parsed.get(x)
                    st["t"][x] = t
                done += len(b)
            p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        left = len(need) - done
        if need:
            log(f"  DART {y} {r}: {done}개사 갱신, 남은 {left}개사, 실패 묶음 {failed} ({time.time() - t1:.0f}초)")
        data[(y, r)] = {k: v for k, v in st["d"].items() if v}

    out = []
    for sc in stock_codes:
        for (y, r) in periods:
            d = data[(y, r)].get(sc)
            if not d or d.get("equity") is None or (d.get("ni") is None and d.get("op") is None):
                continue
            rec = {"code": sc, "fin": _label(y, r)}
            for f in BAL:
                rec[f] = d.get(f)
            approx = False
            for f in FLOW:
                cur = d.get(f)
                if cur is None:
                    rec[f] = None
                    continue
                if r == "11011":
                    rec[f] = cur
                    continue
                ann = (data.get((y - 1, "11011")) or {}).get(sc) or {}
                prev = d.get(f + "_prev")
                if ann.get(f) is not None and prev is not None:
                    rec[f] = cur + ann[f] - prev          # TTM = 올해 누적 + 작년 연간 - 작년 동기 누적
                else:
                    rec[f] = cur * 4 / NQ[r]              # 연환산(근사)
                    approx = True
            if approx:
                rec["fin"] += "*"
            out.append(rec)
            break
    if not out:
        return pd.DataFrame(columns=["code", "fin", *BAL, *FLOW]).set_index("code")
    return pd.DataFrame(out).set_index("code")

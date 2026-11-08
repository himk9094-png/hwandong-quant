"""네이버 증권 시세 (KRX 키가 없을 때 쓰는 대체 소스, 비공식).

- 종목 목록·시총: m.stock.naver.com 시가총액 순위 API (코스피/코스닥, 보통주·우선주·스팩 포함, ETF/ETN 제외)
- 일봉: fchart.stock.naver.com (수정주가)
결과는 krx.COLUMNS와 같은 긴 형태로 돌려주므로 이후 계산은 KRX와 동일하다.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

from . import config
from .krx import COLUMNS

LIST_URL = "https://m.stock.naver.com/api/stocks/marketValue/{mkt}"
CHART_URL = "https://fchart.stock.naver.com/sise.nhn"
FIN_URL = "https://m.stock.naver.com/api/stock/{code}/finance/quarter"
ITEM = re.compile(r'data="(\d{8})\|([\d.]+)\|([\d.]+)\|([\d.]+)\|([\d.]+)\|(\d+)"')
HIST_COLS = ["date", "open", "high", "low", "close", "vol"]


class NaverError(RuntimeError):
    pass


class NaverSource:
    def __init__(self, cache_dir: Path, workers: int = 4, count: int = 320, budget_sec: int = 720):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.workers, self.count, self.budget = workers, count, budget_sec
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "Mozilla/5.0 (quant-dashboard)"})
        self.fetched = 0
        self.done, self.errors, self.t0 = 0, [], time.time()

    def _get(self, url, **params):
        last = None
        for i in range(2):
            try:
                r = self.s.get(url, params=params, timeout=8)
                if r.status_code == 200:
                    return r
                last = f"HTTP {r.status_code}"
            except requests.RequestException as e:
                last = e
            time.sleep(1 + i)
        raise NaverError(f"네이버 요청 실패 {url} {params}: {last}")

    def listing(self) -> pd.DataFrame:
        rows = []
        for mkt in ("KOSPI", "KOSDAQ"):
            for page in range(1, 80):
                st = self._get(LIST_URL.format(mkt=mkt), page=page, pageSize=100).json().get("stocks") or []
                if not st:
                    break
                for x in st:
                    if x.get("stockEndType") != "stock":
                        continue
                    close = float(x["closePriceRaw"])
                    rows.append({
                        "code": x["itemCode"], "name": x["stockName"], "mkt": mkt,
                        "shares": float(x["marketValueRaw"]) / close if close else None,
                        "halted": (x.get("tradeStopType") or {}).get("name") == "HALTED",
                    })
        if not rows:
            raise NaverError("네이버 종목 목록이 비어 있습니다.")
        return pd.DataFrame(rows).drop_duplicates("code").set_index("code")

    def _chart(self, code: str, count: int) -> pd.DataFrame:
        r = self._get(CHART_URL, symbol=code, timeframe="day", count=count, requestType=0)
        self.fetched += 1
        rows = ITEM.findall(r.text)
        df = pd.DataFrame(rows, columns=HIST_COLS)
        for c in HIST_COLS[1:]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df

    def history_safe(self, code: str):
        if time.time() - self.t0 > self.budget:       # 시간 초과: 남은 종목은 캐시만 사용
            p = self.dir / f"{code}.csv"
            self.skipped += 1
            return pd.read_csv(p, dtype={"date": str}) if p.exists() else None
        try:
            df = self.history(code)
        except Exception as e:  # 한 종목 실패로 전체를 멈추지 않는다
            df = None
            self.errors.append(f"{code}: {e}")
        self.done += 1
        if self.done % 300 == 0:
            print(f"  일봉 {self.done}종목 완료 ({time.time() - self.t0:.0f}초, 실패 {len(self.errors)})", flush=True)
        return df

    def history(self, code: str) -> pd.DataFrame:
        """캐시가 있으면 최근 30일만 받아 이어 붙인다. 겹치는 날 가격이 다르면(액면분할 등으로 수정주가가 바뀜) 전부 다시 받는다."""
        p = self.dir / f"{code}.csv"
        if p.exists():
            old = pd.read_csv(p, dtype={"date": str})
            new = self._chart(code, 30)
            ov = old.merge(new, on="date", suffixes=("_o", "_n"))
            same = len(ov) > 0 and ((ov["close_o"] - ov["close_n"]).abs() <= ov["close_o"].abs() * 0.001 + 0.5).all()
            if same:
                df = pd.concat([old[~old["date"].isin(new["date"])], new]).sort_values("date").tail(self.count)
            else:
                df = self._chart(code, self.count)
        else:
            df = self._chart(code, self.count)
        if len(df):
            df.to_csv(p, index=False)
        return df

    def load(self, today: dt.date, log=print) -> tuple[pd.DataFrame, dt.date]:
        t = time.time()
        try:
            self._chart("005930", 5)
        except NaverError as e:
            raise NaverError(f"네이버 시세 서버에 접속할 수 없습니다(이 서버에서 차단됐을 수 있음): {e}")
        log(f"  네이버 접속 확인 ({time.time() - t:.1f}초)")
        lst = self.listing()
        codes = list(lst.index)
        log(f"  종목 목록 {len(codes)}개 ({time.time() - t:.0f}초)")
        self.done, self.errors, self.t0, self.skipped = 0, [], time.time(), 0
        with ThreadPoolExecutor(self.workers) as ex:
            hists = list(ex.map(self.history_safe, codes))
        if self.skipped:
            log(f"  시간 제한으로 {self.skipped}종목은 이번에 못 받음(다음 실행에서 이어 받음)")
        if len(self.errors) > len(codes) * 0.3:
            raise NaverError(f"일봉 실패가 너무 많습니다({len(self.errors)}/{len(codes)}). 예: {self.errors[:3]}")
        frames = []
        for code, h in zip(codes, hists):
            if h is None or h.empty:
                continue
            h = h.copy()
            h["code"] = code
            frames.append(h)
        df = pd.concat(frames, ignore_index=True)

        # 장중 실행이면 오늘 미완성 봉은 버린다
        now = dt.datetime.now(config.KST)
        today_s = today.strftime("%Y%m%d")
        if now.date() == today and now.weekday() < 5 and now.time() < dt.time(15, 40):
            df = df[df["date"] != today_s]
        asof_s = df["date"].value_counts().loc[lambda s: s >= len(codes) * 0.5].index.max()
        df = df[df["date"] <= asof_s].sort_values(["code", "date"])

        prev = df.groupby("code")["close"].shift(1)
        df["diff"] = df["close"] - prev.fillna(df["close"])
        df["chg"] = (df["diff"] / prev * 100).round(2).fillna(0)
        df["val"] = df["close"] * df["vol"]                      # 거래대금 근사(종가×거래량)
        df = df.join(lst, on="code")
        df["mcap"] = df["shares"] * df["close"]
        df["sect"] = ""
        # 거래정지 종목은 마지막 날 거래량 0으로 처리 → 유니버스에서 빠짐
        df.loc[df["halted"].fillna(False).astype(bool) & (df["date"] == asof_s), "vol"] = 0
        log(f"  네이버 일봉 요청 {self.fetched}건, 종목 {len(codes)}개, 실패 {len(self.errors)}개, {time.time() - t:.0f}초")
        return df[COLUMNS], dt.datetime.strptime(asof_s, "%Y%m%d").date()


def _num(v):
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_fin(js: dict) -> dict | None:
    """분기 재무(억원) → 최근 4분기 합(TTM) 매출·영업이익·순이익, 최근 분기 BPS·부채비율."""
    fi = js.get("financeInfo") or {}
    periods = [x["key"] for x in fi.get("trTitleList", []) if x.get("isConsensus") == "N"]
    if not periods:
        return None
    rows = {r.get("title"): r.get("columns") or {} for r in fi.get("rowList", [])}

    def val(title, key):
        return _num((rows.get(title, {}).get(key) or {}).get("value"))

    last4 = periods[-4:]
    rec = {}
    approx = False
    for f, titles in (("rev", ("매출액",)), ("op", ("영업이익",)), ("ni", ("지배주주순이익", "당기순이익"))):
        vals = None
        for t in titles:
            v = [val(t, k) for k in last4]
            if sum(x is not None for x in v) >= 2:
                vals = v
                break
        if vals is None:
            rec[f] = None
            continue
        got = [x for x in vals if x is not None]
        total = sum(got)
        if len(got) < 4:
            total = total * 4 / len(got)
            approx = True
        rec[f] = total * 1e8
    latest = periods[-1]
    rec["bps"] = val("BPS", latest)
    rec["debt_ratio"] = val("부채비율", latest)
    rec["fin"] = f"{latest[2:4]}.{(int(latest[4:6]) + 2) // 3}Q" + ("*" if approx else "")
    return rec


def _fin_one(src: "NaverSource", code: str, fin_dir: Path):
    p = fin_dir / f"{code}.json"
    try:
        if p.exists() and time.time() - p.stat().st_mtime < 3 * 86400:
            js = json.loads(p.read_text(encoding="utf-8"))
        else:
            js = src._get(FIN_URL.format(code=code)).json()
            p.write_text(json.dumps(js, ensure_ascii=False), encoding="utf-8")
        rec = parse_fin(js)
    except Exception:
        return None
    if rec:
        rec["code"] = code
    return rec


def fundamentals(src: "NaverSource", codes: list[str], shares: pd.Series, budget_sec: int = 480, log=print) -> pd.DataFrame:
    fin_dir = src.dir.parent / "naver_fin"
    fin_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    def one(code):
        if time.time() - t0 > budget_sec:
            p = fin_dir / f"{code}.json"
            if not p.exists():
                return None
            try:
                rec = parse_fin(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                return None
            if rec:
                rec["code"] = code
            return rec
        return _fin_one(src, code, fin_dir)

    with ThreadPoolExecutor(src.workers) as ex:
        recs = [r for r in ex.map(one, codes) if r]
    log(f"  네이버 재무 {len(recs)}/{len(codes)}종목 ({time.time() - t0:.0f}초)")
    if not recs:
        return pd.DataFrame()
    df = pd.DataFrame(recs).set_index("code")
    sh = pd.to_numeric(shares.reindex(df.index), errors="coerce")
    df["equity"] = df["bps"] * sh                          # 자본총계(근사) = BPS × 상장주식수
    df["liab"] = df["debt_ratio"] / 100 * df["equity"]      # 부채총계 = 부채비율 × 자본
    df["assets"] = df["equity"] + df["liab"]
    return df[["fin", "rev", "op", "ni", "equity", "liab", "assets"]]

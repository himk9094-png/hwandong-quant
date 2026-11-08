"""네이버 증권 시세 (KRX 키가 없을 때 쓰는 대체 소스, 비공식).

- 종목 목록·시총: m.stock.naver.com 시가총액 순위 API (코스피/코스닥, 보통주·우선주·스팩 포함, ETF/ETN 제외)
- 일봉: fchart.stock.naver.com (수정주가)
결과는 krx.COLUMNS와 같은 긴 형태로 돌려주므로 이후 계산은 KRX와 동일하다.
"""
from __future__ import annotations

import datetime as dt
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
ITEM = re.compile(r'data="(\d{8})\|([\d.]+)\|([\d.]+)\|([\d.]+)\|([\d.]+)\|(\d+)"')
HIST_COLS = ["date", "open", "high", "low", "close", "vol"]


class NaverError(RuntimeError):
    pass


class NaverSource:
    def __init__(self, cache_dir: Path, workers: int = 8, count: int = 320):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.workers, self.count = workers, count
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "Mozilla/5.0 (quant-dashboard)"})
        self.fetched = 0

    def _get(self, url, **params):
        last = None
        for i in range(4):
            try:
                r = self.s.get(url, params=params, timeout=20)
                if r.status_code == 200:
                    return r
                last = f"HTTP {r.status_code}"
            except requests.RequestException as e:
                last = e
            time.sleep(1.5 * (i + 1))
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
        lst = self.listing()
        codes = list(lst.index)
        with ThreadPoolExecutor(self.workers) as ex:
            hists = list(ex.map(self.history, codes))
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
        log(f"  네이버 일봉 요청 {self.fetched}건, 종목 {len(codes)}개")
        return df[COLUMNS], dt.datetime.strptime(asof_s, "%Y%m%d").date()

"""KRX Open API (data-dbg.krx.co.kr) — 코스피/코스닥 전종목 일별매매정보 수집 + 캐시.

KRX Open API는 전 거래일(T-1) 데이터를 매일 오전 8시(KST)에 갱신한다.
"""
from __future__ import annotations

import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

BASE = "https://data-dbg.krx.co.kr/svc/apis/sto/"
ENDPOINT = {"KOSPI": "stk_bydd_trd", "KOSDAQ": "ksq_bydd_trd"}
FIELDS = {
    "BAS_DD": "date", "ISU_CD": "code", "ISU_NM": "name", "SECT_TP_NM": "sect",
    "TDD_CLSPRC": "close", "CMPPREVDD_PRC": "diff", "FLUC_RT": "chg",
    "TDD_OPNPRC": "open", "TDD_HGPRC": "high", "TDD_LWPRC": "low",
    "ACC_TRDVOL": "vol", "ACC_TRDVAL": "val", "MKTCAP": "mcap", "LIST_SHRS": "shares",
}
NUMERIC = ["close", "diff", "chg", "open", "high", "low", "vol", "val", "mcap", "shares"]
COLUMNS = ["date", "code", "name", "mkt", "sect"] + NUMERIC


class KrxError(RuntimeError):
    pass


class KrxClient:
    def __init__(self, key: str | None):
        if not key:
            raise KrxError("KRX_API_KEY가 없습니다. GitHub Secrets에 KRX_API_KEY를 등록하세요.")
        self.s = requests.Session()
        self.s.headers.update({"AUTH_KEY": key.strip(), "User-Agent": "quant-dashboard/1.0"})

    def day(self, ymd: str, market: str) -> list[dict]:
        url = BASE + ENDPOINT[market]
        last = None
        for i in range(5):
            try:
                r = self.s.get(url, params={"basDd": ymd}, timeout=30)
            except requests.RequestException as e:  # 네트워크 일시 오류
                last = e
                time.sleep(2 * (i + 1))
                continue
            if r.status_code in (401, 403):
                raise KrxError(
                    f"KRX 인증 실패(HTTP {r.status_code}): {r.text[:200]} — 인증키가 맞는지, "
                    f"'{'유가증권' if market == 'KOSPI' else '코스닥'} 일별매매정보' API 이용신청이 승인됐는지 확인하세요."
                )
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                time.sleep(3 * (i + 1))
                continue
            r.raise_for_status()
            try:
                js = r.json()
            except ValueError:
                last = "JSON이 아닌 응답"
                time.sleep(2)
                continue
            if "OutBlock_1" not in js:
                raise KrxError(f"KRX 응답 형식 오류({market} {ymd}): {str(js)[:200]} — API 이용신청 승인 여부를 확인하세요.")
            return js["OutBlock_1"] or []
        raise KrxError(f"KRX 요청 실패 {market} {ymd}: {last}")


def _to_num(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.replace(",", "", regex=False).str.strip()
    s = s.where(~s.isin(["", "-", "None", "nan", "NaN"]))
    return pd.to_numeric(s, errors="coerce")


def parse_rows(rows: list[dict], market: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows).rename(columns=FIELDS)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = None
    df["mkt"] = market
    code = df["code"].astype(str).str.strip()
    # 표준코드(KR7005930003)로 오는 경우 단축코드(005930)로 변환
    code = code.where(code.str.len() != 12, code.str[3:9])
    df["code"] = code
    df["date"] = df["date"].astype(str).str.replace("-", "", regex=False).str.replace("/", "", regex=False).str[:8]
    for c in NUMERIC:
        df[c] = _to_num(df[c])
    return df[COLUMNS]


def ymd(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


class KrxStore:
    """날짜·시장별 응답을 csv.gz로 캐시. 휴장일(빈 응답)은 최근 7일이 아니면 빈 파일로 기록."""

    def __init__(self, client, cache_dir: Path, today: dt.date, workers: int = 4):
        self.client = client
        self.dir = Path(cache_dir)
        self.today = today
        self.workers = workers
        self.fetched = 0

    def _path(self, d: str, mkt: str) -> Path:
        return self.dir / mkt / f"{d}.csv.gz"

    def get(self, d: str, mkt: str) -> pd.DataFrame:
        p = self._path(d, mkt)
        if p.exists():
            return pd.read_csv(p, dtype={"code": str, "date": str, "name": str, "sect": str})
        df = parse_rows(self.client.day(d, mkt), mkt)
        self.fetched += 1
        age = (self.today - dt.datetime.strptime(d, "%Y%m%d").date()).days
        if len(df) or age > 7:
            p.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(p, index=False, compression="gzip")
        return df

    def find_asof(self, max_back: int = 14) -> dt.date:
        d = self.today
        for _ in range(max_back):
            if d.weekday() < 5 and len(self.get(ymd(d), "KOSPI")):
                return d
            d -= dt.timedelta(days=1)
        raise KrxError(f"최근 {max_back}일 안에 KRX 데이터가 없습니다.")

    def history(self, asof: dt.date, calendar_days: int, markets: list[str]) -> pd.DataFrame:
        start = asof - dt.timedelta(days=calendar_days)
        days = []
        d = start
        while d <= asof:
            if d.weekday() < 5:
                days.append(ymd(d))
            d += dt.timedelta(days=1)
        jobs = [(x, m) for x in days for m in markets]
        with ThreadPoolExecutor(self.workers) as ex:
            frames = list(ex.map(lambda j: self.get(*j), jobs))
        frames = [f for f in frames if len(f)]
        if not frames:
            raise KrxError("KRX 데이터가 비어 있습니다.")
        df = pd.concat(frames, ignore_index=True)
        return df.drop_duplicates(["date", "code"], keep="last")

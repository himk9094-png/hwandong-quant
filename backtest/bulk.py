"""DART 재무정보 일괄다운로드 파일(BS/PL/CF zip) → store/fin/bulk.parquet (fin.parquet 과 같은 꼴).

서울 서버(hq dartbulk)가 GitHub 릴리스 'dart-bulk'에 올린 zip을 읽는다. 상장사 전체·전체 계정.
  python -m backtest.bulk --zips DIR        # DIR 안의 zip 전부 → store/fin/bulk.parquet
공시일이 파일에 없으므로 rcept_no 자리에 '법정 제출기한(주말이면 다음 평일)'을 넣는다(보수적: 미리 쓰지 않음).
12월 결산 회사만 쓴다.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import re
import zipfile
from pathlib import Path

import pandas as pd

from backtest import collect as C

RPT = {"1Q": "11013", "2Q": "11012", "3Q": "11014", "4Q": "11011"}
KEYS = list(C.ID)
AID = {a: k for k, v in C.ID.items() for a in v}


def deadline(year: int, rpt: str) -> str:
    d = {"11013": dt.date(year, 5, 15), "11012": dt.date(year, 8, 14), "11014": dt.date(year, 11, 14),
         "11011": dt.date(year + 1, 3, 31)}[rpt]
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d.strftime("%Y%m%d")


def _col(head: list[str], who: str) -> int | None:
    """'당기'/'전기' 열(재무상태표는 '당기말'/'전기말'): 누적 열이 있으면 누적, 없으면 첫 번째."""
    c = [i for i, h in enumerate(head) if h.strip().startswith(who)]      # '당기3분기'처럼 붙여 쓴 해도 있음
    acc = [i for i in c if "누적" in head[i]]
    return (acc or c or [None])[0]


def parse_zip(path: Path, out: dict):
    m = re.match(r"(\d{4})_(\dQ)_(BS|PL|CF)_", path.name)
    if not m:
        return
    year, rpt, kind = int(m.group(1)), RPT[m.group(2)], m.group(3)
    z = zipfile.ZipFile(path)
    # 손익계산서(02) 먼저, 포괄손익(03) 나중 → 같은 값이면 앞의 것 유지
    for info in sorted(z.infolist(), key=lambda i: i.filename):
        name = info.filename.encode("cp437").decode("cp949", "replace") if not info.flag_bits & 0x800 else info.filename
        fs = "CFS" if "연결" in name else "OFS"
        rd = csv.reader(io.TextIOWrapper(z.open(info), encoding="cp949", errors="replace"), delimiter="\t")
        head = next(rd, None)
        if not head:
            continue
        ix = {h.strip(): i for i, h in enumerate(head)}
        cur, prev = _col(head, "당기"), _col(head, "전기")
        icode, iacc, inm, imon = ix.get("종목코드"), ix.get("항목코드"), ix.get("항목명"), ix.get("결산월")
        if None in (cur, icode, iacc):
            continue
        for r in rd:
            if len(r) <= cur:
                continue
            k = AID.get(r[iacc].strip())
            if k is None:
                nm = re.sub(r"[\s·ㆍ]", "", r[inm]) if inm is not None else ""
                k = next((kk for kk, v in C.NAME_N.items() if nm in v), None)
                if k is None:
                    continue
            if (kind == "BS") != (k in ("assets", "equity", "eq_parent")) or (kind == "CF") != (k == "cfo"):
                continue
            if imon is not None and r[imon].strip() not in ("12", ""):
                continue
            code = r[icode].strip().strip("[]")
            key = (code, year, rpt, fs)
            rec = out.setdefault(key, {})
            if k in rec:
                continue
            rec[k] = C._num(r[cur])
            if prev is not None and len(r) > prev:
                rec[k + "_p"] = C._num(r[prev])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zips", required=True)
    a = ap.parse_args()
    zs = sorted(Path(a.zips).glob("*.zip"))
    # 같은 기간·종류가 여러 번 올라왔으면 최신(파일명 끝 시각) 하나만
    best = {}
    for p in zs:
        m = re.match(r"(\d{4}_\dQ_(?:BS|PL|CF))_(\d+)\.zip", p.name)
        if m and (m.group(1) not in best or best[m.group(1)].name < p.name):
            best[m.group(1)] = p
    out = {}
    for i, p in enumerate(sorted(best.values())):
        parse_zip(p, out)
        print(f"{i + 1}/{len(best)} {p.name} → 누적 {len(out)}", flush=True)
    rows = [{"code": k[0], "year": k[1], "rpt": k[2], "fs": k[3], **v} for k, v in out.items()]
    df = pd.DataFrame(rows)
    ccdf = pd.read_parquet(C.STORE / "corp_codes.parquet")
    st = dict(zip(ccdf[ccdf.stock != ""].stock, ccdf[ccdf.stock != ""].corp))
    df["corp"] = df.code.map(st).fillna("S" + df.code)      # 상장폐지로 회사코드가 없으면 S+종목코드
    df["rcept_no"] = [deadline(y, r) + "999999" for y, r in zip(df.year, df.rpt)]
    for c in [k for k in KEYS] + [k + "_p" for k in KEYS]:
        if c not in df:
            df[c] = None
    df = df.drop(columns=["code"])
    dst = C.STORE / "fin" / "bulk.parquet"
    dst.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(dst, index=False)
    print(f"저장 {dst}: {len(df)}행 · 회사 {df.corp.nunique()} · 기간 {df.groupby(['year', 'rpt']).ngroups}", flush=True)


if __name__ == "__main__":
    main()

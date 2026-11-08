"""매일 실행: 데이터 수집 → 팩터·신호 계산 → 기록·성과 → docs/data/latest.json → 텔레그램.

  python -m pipeline.main            # 실데이터 (KRX_API_KEY, DART_API_KEY 필요)
  python -m pipeline.main --demo     # 가짜 데이터로 전체 동작 확인
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, dart, factors, krx, naver, ntfy, prices, signals, telegram, tracker

# (필드, 배율, 소수자리)  배율: 원→억은 1e-8, 비율→%는 100
FIELDS = [
    ("code", None, None), ("name", None, None), ("mkt", None, None),
    ("close", 1, 0), ("chg", 1, 2), ("mcap", 1e-8, 0), ("tv20", 1e-8, 1),
    ("per", 1, 1), ("pbr", 1, 2), ("psr", 1, 2), ("ey", 100, 1), ("roc", 100, 1),
    ("roe", 100, 1), ("roa", 100, 1), ("opm", 100, 1), ("debt", 100, 0), ("fin", None, None),
    ("mom12", 100, 1), ("mom6", 100, 1), ("mom3", 100, 1), ("hi52", 100, 1),
    ("rsi", 1, 0), ("disp", 1, 1), ("v", 1, 0), ("q", 1, 0), ("m", 1, 0), ("total", 1, 0),
    ("mf", 1, 0), ("sig", None, None), ("st", None, None), ("nsig", 1, 0), ("vr", 1, 1),
    ("surge", 1, 0), ("r1y", 100, 1), ("evop", 1, 1), ("amf", 1, 0), ("asm", 1, 0),
    ("lm", 1, 0), ("lc", 1, 0), ("lf", 1, 0), ("sz", 1, 0), ("hold", 1, 0),
]


def _col(s: pd.Series, mult, nd):
    if mult is None:
        return [None if (v is None or (isinstance(v, float) and np.isnan(v)) or v == "") else str(v) for v in s]
    x = pd.to_numeric(s, errors="coerce") * mult
    x = x.where(np.isfinite(x))
    out = []
    for v in x.round(nd):
        if pd.isna(v):
            out.append(None)
        else:
            out.append(int(v) if nd == 0 else float(v))
    return out


def build_payload(df, panel, meta, perf, recent):
    df = df.reset_index().rename(columns={"index": "code"})
    if "code" not in df:
        df = df.rename(columns={df.columns[0]: "code"})
    cols = [f for f, _, _ in FIELDS]
    data = {f: _col(df[f] if f in df else pd.Series([None] * len(df)), m, nd) for f, m, nd in FIELDS}
    rows = [list(t) for t in zip(*[data[c] for c in cols])]

    d = df.set_index("code")
    spark_codes = set(d.index[d["amf"].notna() | d["asm"].notna()]) | set(d.index[d["nsig"] >= 1])
    for col in ("lm", "lc", "lf", "sz"):
        spark_codes |= set(d.index[d[col] <= 30])
    for col, asc in (("total", False), ("mf", True), ("v", False), ("q", False), ("m", False), ("surge", False)):
        spark_codes |= set(d[col].dropna().sort_values(ascending=asc).index[:60])
    c = panel["close"].iloc[-120:].ffill(limit=5)
    spark = {}
    for code in spark_codes:
        if code in c.columns:
            s = c[code].dropna()
            spark[code] = [round(float(v)) if v >= 1000 else round(float(v), 1) for v in s]
    return {"meta": meta, "cols": cols, "rows": rows, "spark": spark, "perf": perf, "recent": recent}


def alert_text(df: pd.DataFrame, asof: str, n: int, url: str | None) -> str:
    L = [f"📊 환동퀀트 · {asof} 종가 기준"]

    def nm(c):
        return f"{df.at[c, 'name']}"

    top = df["total"].dropna().sort_values(ascending=False).index[:n]
    L.append("\n[종합 상위]")
    L += [f"{i + 1}. {nm(c)} ({df.at[c, 'total']:.0f}점)" for i, c in enumerate(top)]
    mf = df["mf"].dropna().sort_values().index[:n]
    L.append("\n[마법공식 상위]")
    L += [f"{i + 1}. {nm(c)}" for i, c in enumerate(mf)]
    if df["amf"].notna().any():
        L.append("\n[A안 후보]")
        L.append("마법공식: " + ", ".join(nm(c) for c in df[df["amf"] <= 8].sort_values("amf").index))
        L.append("소형모멘텀: " + ", ".join(nm(c) for c in df[df["asm"] <= 7].sort_values("asm").index))
    if df["lm"].notna().any():
        L.append("\n[대형 모멘텀 10]")
        L.append(", ".join(nm(c) for c in df[df["lm"] <= 10].sort_values("lm").index))
    s2 = df[df["nsig"] >= 2].sort_values(["nsig", "total"], ascending=[False, False], na_position="last")
    L.append(f"\n[매수신호 2개 이상] {len(s2)}종목")
    L += [f"· {nm(c)}: {df.at[c, 'sig'].replace('|', ', ')}" for c in s2.index[:8]]
    su = df["surge"].dropna().sort_values(ascending=False).index[:3]
    if len(su):
        L.append("\n[급등후보 · 참고용]")
        L += [f"· {nm(c)} ({df.at[c, 'surge']:.0f}점, 거래량 {df.at[c, 'vr']:.1f}배)" for c in su]
    if url:
        L.append(f"\n대시보드: {url}")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--today")
    ap.add_argument("--out", default=str(config.ROOT / "docs"))
    ap.add_argument("--cache", default=str(config.ROOT / "cache"))
    ap.add_argument("--history", default=str(config.ROOT / "history" / "picks.csv"))
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args(argv)

    t0 = time.time()
    cfg = config.load()
    today = dt.date.fromisoformat(a.today) if a.today else config.today_kst()
    warnings: list[str] = []
    cache = Path(a.cache)

    source = "KRX Open API"
    if a.demo:
        from . import demo
        mk = demo.DemoMarket(today)
        kc, dc = mk, demo.DemoDart(mk)
        source = "샘플"
    else:
        kc = krx.KrxClient(os.environ["KRX_API_KEY"]) if os.environ.get("KRX_API_KEY") else None
        try:
            dc = dart.DartClient(os.environ.get("DART_API_KEY"))
        except dart.DartError as e:
            dc = None
            warnings.append(str(e) + " 재무 팩터 없이 계산했습니다.")

    if kc is None:
        source = "네이버 증권(비공식)"
        warnings.append("주가는 네이버 증권 시세로 계산했습니다(관리종목 필터 없음). KRX_API_KEY를 등록하면 자동으로 KRX로 바뀝니다.")
        print("KRX 키 없음 → 네이버 증권 시세 사용. 주가 이력 수집 중…")
        long, asof_d = naver.NaverSource(cache / "naver").load(today)
        asof = krx.ymd(asof_d)
    else:
        store = krx.KrxStore(kc, cache / "krx", today)
        asof_d = store.find_asof()
        asof = krx.ymd(asof_d)
        print(f"기준일 {asof_d} — 주가 이력 수집 중…")
        long = store.history(asof_d, cfg["history_calendar_days"], cfg["universe"]["markets"])
        print(f"  KRX 신규 요청 {store.fetched}건")
    panel, last, events = prices.build(long, asof)
    print(f"  기준일 {asof_d}, 거래일 {len(panel['close'])}일, 종목 {len(last)}개, 권리변동 보정 {events}건")

    fund = pd.DataFrame()
    fin_src = {}
    if dc is not None and cfg.get("fundamentals", "dart") == "dart":
        try:
            print("재무 데이터(DART) 수집 중…")
            fund = dart.load_fundamentals(dc, list(last.index), asof_d, cache / "dart")
            fin_src["DART"] = int(len(fund))
        except Exception as e:  # 재무 실패해도 가격 기반 탭은 살린다
            warnings.append(f"DART 수집 실패: {e}")
            print(f"  경고: {e}")
    if not a.demo:
        missing = [c for c in last.index if c not in fund.index]
        if missing:
            print(f"DART에 없는 {len(missing)}종목은 네이버 재무로 채움…")
            try:
                nf = naver.fundamentals(naver.NaverSource(cache / "naver"), missing, last["shares"])
                fin_src["네이버"] = int(len(nf))
                fund = pd.concat([fund, nf]) if len(fund) else nf
            except Exception as e:
                warnings.append(f"네이버 재무 수집 실패: {e}")

    df = factors.compute(panel, last, fund, cfg)
    df = df.join(signals.compute(panel), how="left")
    df["nsig"] = df["nsig"].fillna(0)
    df["sig"] = df["sig"].fillna("")
    print(f"유니버스 {len(df)}종목, 재무 매칭 {df['fin'].notna().sum()}종목")

    hist = Path(a.history)
    tracker.record(df, asof, hist, cfg["track_top_n"])
    perf, recent = tracker.evaluate(hist, panel["close"])

    fin_dist = df["fin"].dropna().str.rstrip("*").value_counts().to_dict()
    meta = {
        "asof": asof_d.isoformat(),
        "generated": dt.datetime.now(config.KST).strftime("%Y-%m-%d %H:%M"),
        "n": int(len(df)), "sample": bool(a.demo), "fin_dist": fin_dist,
        "approx_ttm": int(df["fin"].fillna("").str.endswith("*").sum()),
        "warnings": warnings, "strats": tracker.STRATS, "horizons": list(tracker.HORIZONS),
        "aplan": cfg["aplan"], "universe": cfg["universe"], "source": source, "fin_source": " · ".join(f"{k} {v}종목" for k, v in fin_src.items()) or "DART",
    }
    payload = build_payload(df, panel, meta, perf, recent)
    out = Path(a.out) / "data"
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"저장: {out / 'latest.json'} ({(out / 'latest.json').stat().st_size // 1024} KB)")

    url = os.environ.get("PAGES_URL")
    if not url and os.environ.get("GITHUB_REPOSITORY"):
        owner, repo = os.environ["GITHUB_REPOSITORY"].split("/")
        url = f"https://{owner.lower()}.github.io/{repo}/"
    text = alert_text(df, asof_d.isoformat(), cfg["alert_top_n"], url)
    if not a.demo and not a.no_telegram:
        ntfy.send(f"환동퀀트 {asof_d.isoformat()}", text.split("\n", 1)[1].strip(), click=url, tags=["chart_with_upwards_trend"])
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat and not a.no_telegram and not a.demo:
        telegram.send(text, tok, chat)
        print("텔레그램 전송 완료")
    else:
        print("----- 알림 미리보기 -----\n" + text)
    for w in warnings:
        print("경고:", w)
    print(f"완료 ({time.time() - t0:.0f}초)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

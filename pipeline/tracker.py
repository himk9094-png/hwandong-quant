"""매일 각 전략의 추천 종목을 기록하고, 이후 수익률(5·20·60거래일)을 시장 평균과 비교한다.

과최적화 여부를 실제 결과로 확인하기 위한 장치. history/picks.csv는 저장소에 커밋된다.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

STRATS = {
    "total": "종합 상위", "aplan": "A안 후보", "mf": "마법공식", "value": "밸류",
    "quality": "퀄리티", "mom": "모멘텀", "signal": "매수신호", "surge": "급등후보",
}
HORIZONS = (5, 20, 60)
COLS = ["date", "strat", "code", "name", "close"]


def picks(df: pd.DataFrame, n: int) -> dict[str, list[str]]:
    top = lambda col, asc=False: list(df[col].dropna().sort_values(ascending=asc).index[:n])
    sig = df[df["nsig"] >= 1].sort_values(["nsig", "total"], ascending=[False, False], na_position="last")
    return {
        "total": top("total"), "mf": top("mf", True), "value": top("v"), "quality": top("q"),
        "mom": top("m"), "aplan": list(df.index[df["ag"] != ""]),
        "signal": list(sig.index[:50]), "surge": top("surge"),
    }


def record(df: pd.DataFrame, asof: str, path: Path, n: int) -> None:
    path = Path(path)
    old = pd.read_csv(path, dtype={"code": str, "date": str}) if path.exists() else pd.DataFrame(columns=COLS)
    old = old[old["date"] != asof]
    new = [
        {"date": asof, "strat": s, "code": c, "name": df.at[c, "name"], "close": df.at[c, "close"]}
        for s, codes in picks(df, n).items() for c in codes
    ]
    out = pd.concat([old, pd.DataFrame(new, columns=COLS)], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)


def evaluate(path: Path, close: pd.DataFrame) -> tuple[dict, list]:
    path = Path(path)
    if not path.exists():
        return {}, []
    p = pd.read_csv(path, dtype={"code": str, "date": str})
    if p.empty:
        return {}, []
    c = close.ffill(limit=5)
    arr = c.to_numpy(dtype=float)
    T = len(c) - 1
    row = {d: i for i, d in enumerate(c.index)}
    col = {k: j for j, k in enumerate(c.columns)}
    p["i"] = p["date"].map(row)
    p["j"] = p["code"].map(col)
    p = p.dropna(subset=["i", "j"]).astype({"i": int, "j": int})

    perf: dict = {}
    for h in HORIZONS:
        ok = p[p["i"] + h <= T]
        if ok.empty:
            continue
        r = arr[ok["i"] + h, ok["j"]] / arr[ok["i"], ok["j"]] - 1
        bench = {}
        for i in ok["i"].unique():
            b = arr[i + h] / arr[i] - 1
            bench[i] = float(np.nanmean(np.clip(b, -0.9, 3)))
        ok = ok.assign(r=r, b=ok["i"].map(bench)).dropna(subset=["r"])
        for s, g in ok.groupby("strat"):
            perf.setdefault(s, {})[str(h)] = {
                "n": int(len(g)), "days": int(g["date"].nunique()),
                "avg": round(float(g["r"].mean()) * 100, 2),
                "hit": round(float((g["r"] > 0).mean()) * 100, 1),
                "ex": round(float((g["r"] - g["b"]).mean()) * 100, 2),
            }

    recent_dates = sorted(p["date"].unique())[-10:]
    rc = p[p["date"].isin(recent_dates) & p["strat"].isin(["surge", "signal", "aplan"])].copy()
    rc["ret"] = arr[T, rc["j"]] / arr[rc["i"], rc["j"]] - 1
    rc = rc.sort_values(["date", "strat"], ascending=[False, True]).head(300)
    recent = [
        [d, s, c, n, None if not np.isfinite(r) else round(float(r) * 100, 1)]
        for d, s, c, n, r in rc[["date", "strat", "code", "name", "ret"]].itertuples(index=False)
    ]
    return perf, recent

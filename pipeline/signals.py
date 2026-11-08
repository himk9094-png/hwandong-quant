"""기술적 지표·매수신호·급등후보 점수 (모두 종가 기준, 수정주가 사용)."""
from __future__ import annotations

import numpy as np
import pandas as pd

K = 3  # '최근 3거래일 안에 발생' 기준


def _crossed_up(a: pd.DataFrame, b: pd.DataFrame) -> pd.Series:
    x = (a > b) & (a.shift(1) <= b.shift(1))
    return x.iloc[-K:].any()


def compute(panel: dict, rows: int = 300) -> pd.DataFrame:
    c = panel["close"].iloc[-rows:].ffill(limit=5)
    h = panel["high"].iloc[-rows:].ffill(limit=5)
    lo = panel["low"].iloc[-rows:].ffill(limit=5)
    v = panel["vol"].iloc[-rows:].fillna(0)

    ma = {n: c.rolling(n, min_periods=n).mean() for n in (5, 20, 60, 120)}
    last = c.iloc[-1]

    # RSI(14, Wilder)
    d = c.diff()
    au = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    ad = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100 / (1 + au / ad.replace(0, np.nan))
    rsi = rsi.where(ad != 0, 100.0).where(au.notna())

    # 볼린저 밴드(20, 2)
    sd = c.rolling(20, min_periods=20).std()
    lower, upper = ma[20] - 2 * sd, ma[20] + 2 * sd
    bbw = (upper - lower) / ma[20]

    # 스토캐스틱 슬로우(14, 3, 3)
    ll = lo.rolling(14, min_periods=14).min()
    hh = h.rolling(14, min_periods=14).max()
    fk = (c - ll) / (hh - ll).replace(0, np.nan) * 100
    sk = fk.rolling(3).mean()
    sdl = sk.rolling(3).mean()

    vavg = v.shift(1).rolling(20, min_periods=15).mean()
    vr = v.iloc[-1] / vavg.iloc[-1].replace(0, np.nan)
    ret1 = c.iloc[-1] / c.iloc[-2] - 1

    sig = {
        "골든크로스": _crossed_up(ma[20], ma[60]),
        "RSI반등": ((rsi >= 30) & (rsi.shift(1) < 30)).iloc[-K:].any(),
        "BB반등": ((c > lower) & (c.shift(1) < lower.shift(1))).iloc[-K:].any(),
        "스토캐스틱": ((sk > sdl) & (sk.shift(1) <= sdl.shift(1)) & (sk.shift(1) < 25)).iloc[-K:].any(),
        "신고가": last > c.shift(1).rolling(250, min_periods=200).max().iloc[-1],
        "거래량급증": (vr >= 3) & (ret1 >= 0.03),
    }
    disp = last / ma[20].iloc[-1] * 100
    state = {
        "정배열": (ma[5].iloc[-1] > ma[20].iloc[-1]) & (ma[20].iloc[-1] > ma[60].iloc[-1]) & (ma[60].iloc[-1] > ma[120].iloc[-1]),
        "RSI과매도": rsi.iloc[-1] < 30,
        "이격과매도": disp < 92,
    }

    # 급등후보 점수(0~100, 참고용)
    brk20 = last > c.shift(1).rolling(20, min_periods=20).max().iloc[-1]
    bw_prev = bbw.iloc[-2]
    bw_pct = (bbw.iloc[-121:-1] <= bw_prev).sum() / bbw.iloc[-121:-1].notna().sum().replace(0, np.nan)
    squeeze = (bw_pct <= 0.2) & (bbw.iloc[-1] > bw_prev)
    r_now = rsi.iloc[-1]
    score = (
        (vr.clip(1, 10).fillna(1) - 1) / 9 * 30
        + brk20.astype(float) * 20
        + squeeze.astype(float) * 20
        + ((r_now >= 55) & (r_now < 75)).astype(float) * 15 + ((r_now >= 75) & (r_now < 85)).astype(float) * 5
        + ((ret1 >= 0.02) & (ret1 <= 0.15)).astype(float) * 15
    )
    score = score.where((ret1 > 0) & (vr >= 1.5))

    out = pd.DataFrame({
        "rsi": rsi.iloc[-1], "disp": disp, "vr": vr, "surge": score.round(0),
    })
    out["sig"] = pd.DataFrame(sig).apply(lambda r: "|".join(k for k, x in r.items() if bool(x)), axis=1)
    out["st"] = pd.DataFrame(state).apply(lambda r: "|".join(k for k, x in r.items() if bool(x)), axis=1)
    out["nsig"] = pd.DataFrame(sig).sum(axis=1)
    return out

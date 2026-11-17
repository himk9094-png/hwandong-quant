"""매수신호·급등후보 이벤트 백테스트 — pipeline/signals.py 와 같은 정의(종가 기준, 조정주가).

KRX 일별 자료에 고가·저가가 없어 스토캐스틱은 종가로 근사, 거래량 대신 거래대금 비율을 쓴다.
신호가 처음 뜬 날 종가 → 다음 날 시가 매수 → H거래일 보유 후 종가 매도. 그날그날 보유 중인 종목을 동일비중으로 본다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import smallcap as S


def universe(m, min_cap=300e8, min_val=2e8):
    U = m["alive"] & ~m["flags"] & (m["mcap"] >= min_cap) & (m["val20"] >= min_val)
    return U & m["static_ok"]


def indicators(m):
    c = m["cumidx"].astype("float32").where(m["alive"])          # 조정 종가 지수
    f32 = lambda x: x.astype("float32")
    ma20, ma60 = f32(c.rolling(20, min_periods=20).mean()), f32(c.rolling(60, min_periods=60).mean())
    d = c.diff()
    au = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    ad = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = f32((100 - 100 / (1 + au / ad.replace(0, np.nan))).where(ad != 0, 100.0).where(au.notna()))
    del au, ad, d
    sd = f32(c.rolling(20, min_periods=20).std())
    lower, bbw = ma20 - 2 * sd, 4 * sd / ma20
    ll, hh = c.rolling(14, min_periods=14).min(), c.rolling(14, min_periods=14).max()
    sk = f32(((c - ll) / (hh - ll).replace(0, np.nan) * 100).rolling(3).mean())
    sdl = f32(sk.rolling(3).mean())
    del ll, hh
    v = m["val"]
    vr = f32(v / v.shift(1).rolling(20, min_periods=15).mean().replace(0, np.nan))
    r1 = m["ret"]
    win = lambda x, k: x.astype("float32").rolling(k, min_periods=1).max() > 0
    sig = [
        win((ma20 > ma60) & (ma20.shift(1) <= ma60.shift(1)), 3),                                  # 골든크로스 20/60
        win((rsi >= 30) & (rsi.shift(1) < 30), 3),                                                 # RSI 30 상향
        win((c > lower) & (c.shift(1) < lower.shift(1)), 3),                                       # 볼린저 하단 복귀
        win((sk > sdl) & (sk.shift(1) <= sdl.shift(1)) & (sk.shift(1) < 20) & (sdl.shift(1) < 20), 2),  # 스토캐스틱(근사)
        c > c.shift(1).rolling(250, min_periods=200).max(),                                        # 52주 신고가
        (vr >= 3) & (r1 >= 0.03),                                                                  # 거래량 급증
    ]
    nsig = sum(x.astype("int8") for x in sig)
    brk20 = c > c.shift(1).rolling(20, min_periods=20).max()
    bwp = bbw.shift(1)
    try:
        pct = bwp.rolling(120, min_periods=60).rank(pct=True)
        squeeze = (pct <= 0.2) & (bbw > bwp)
    except AttributeError:                       # 오래된 pandas
        squeeze = pd.DataFrame(False, index=c.index, columns=c.columns)
    score = ((vr.clip(1, 10).fillna(1) - 1) / 9 * 30 + brk20 * 20.0 + squeeze * 20.0
             + ((rsi >= 55) & (rsi < 75)) * 15.0 + ((rsi >= 75) & (rsi < 85)) * 5.0 + ((r1 >= 0.02) & (r1 <= 0.15)) * 15.0)
    score = f32(score.where((r1 > 0) & (vr >= 1.5)))
    return nsig, score


def newly(cond: pd.DataFrame) -> pd.DataFrame:
    """조건이 새로 참이 된 날(목록에 처음 올라온 날)."""
    return cond & ~cond.shift(1, fill_value=False)


def membership(m, F, strat, frac=0.2):
    """매달 첫 거래일마다 탭 점수 상위 frac(현재 S 유니버스 설정) → 그달 동안 True."""
    d = m["dates"]
    M = np.zeros((len(d), len(m["codes"])), dtype=bool)
    col = {c: k for k, c in enumerate(m["codes"])}
    rd = S.reb_days(d, "M") + [len(d)]
    for a, b in zip(rd[:-1], rd[1:]):
        X = S.factors(m, F, a - 1)
        if X is None:
            continue
        sc = S.score(X, strat).dropna()
        top = sc.nsmallest(max(1, int(len(sc) * frac))).index
        M[a - 1:b - 1, [col[c] for c in top]] = True          # 신호일(전날 종가) 기준이라 한 칸 앞
    return pd.DataFrame(M, index=d, columns=m["codes"])


def event_line(m, E: pd.DataFrame, H: int, name: str):
    d = m["dates"]
    Ef = E.astype("float32")
    cum = Ef.cumsum()
    act = (cum.shift(1, fill_value=0) - cum.shift(H + 1, fill_value=0))
    new = Ef.shift(1, fill_value=0)
    ret, intra = m["ret"].astype("float32"), m["intra"].astype("float32").fillna(0)
    gross = ((act - new) * ret + new * intra).sum(axis=1)
    n, nn = act.sum(axis=1), new.sum(axis=1)
    tax = pd.Series([S.sell_tax(x) for x in d], index=d)
    rt = 2 * (S.COMM + S.SLIP) + tax
    pr = ((gross - nn * rt) / n).where(n > 0, 0.0)
    pr = pr[[x >= S.START for x in d]]
    e = (1 + pr).cumprod()
    e.index = pd.to_datetime(e.index)
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    ye = e.resample("YE").last()
    yearly = ye.pct_change()
    yearly.iloc[0] = ye.iloc[0] - 1
    # 건당 수익(다음 날 시가 → H일째 종가, 비용 빼고)
    cidx = m["cumidx"]
    fwd = (1 + m["intra"].shift(-1)) * cidx.shift(-H) / cidx.shift(-1) - 1
    ev = E.loc[[x >= S.START for x in d]]
    vals = fwd.loc[ev.index].values[ev.values]
    vals = vals[~np.isnan(vals)] - float(rt.mean())
    k = len(vals)
    return {"name": f"{name} (건당 {np.mean(vals) * 100:+.1f}%, 오른 비율 {np.mean(vals > 0) * 100:.0f}%, {k:,}건)" if k else name,
            "cagr": round(float(e.iloc[-1] ** (1 / yrs) - 1) * 100, 1), "mdd": round(float((e / e.cummax() - 1).min()) * 100, 0),
            "cost": None, "y": {str(x.year): round(float(v) * 100, 1) for x, v in yearly.items()}, "_e": e}

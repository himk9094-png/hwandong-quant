"""긴 형태 일별 데이터 → 날짜×종목 행렬, 수정주가 계산.

수정주가: KRX 전일대비(diff)는 '기준가' 대비 값이다. 기준가 = 종가 - 전일대비.
액면분할·무상증자·감자 등으로 기준가가 전일 종가와 달라지면 그 비율(k)만큼 과거 가격을 조정한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def build(long: pd.DataFrame, asof: str):
    long = long.copy()
    long.loc[long["close"] <= 0, "close"] = np.nan

    def piv(col):
        return long.pivot(index="date", columns="code", values=col).sort_index().astype(float)

    close, diff, chg = piv("close"), piv("diff"), piv("chg")
    open_, high, low = piv("open"), piv("high"), piv("low")
    vol, val, mcap = piv("vol"), piv("val"), piv("mcap")

    sdiff = diff.abs() * np.sign(chg)          # 부호는 등락률에서 가져옴(부호 없이 와도 안전)
    base = close - sdiff                        # 기준가
    prev = close.ffill().shift(1)
    k = base / prev
    k = k.where((k - 1).abs() > 0.002, 1.0).fillna(1.0)
    # 과거 가격 조정 계수: t 이후(t 제외) 모든 k의 곱
    factor = k.iloc[::-1].cumprod().iloc[::-1].shift(-1).fillna(1.0)

    panel = {
        "close": close * factor,
        "open": open_ * factor,
        "high": high * factor,
        "low": low * factor,
        "vol": vol / factor,
        "val": val,
        "mcap": mcap,
        "raw_close": close,
    }
    last = long[long["date"] == asof].set_index("code")
    events = int(((k - 1).abs() > 0).sum().sum())
    return panel, last, events

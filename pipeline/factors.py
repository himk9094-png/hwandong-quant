"""팩터 계산: 유니버스 필터, 밸류·퀄리티·모멘텀·마법공식·종합점수·A안."""
from __future__ import annotations

import numpy as np
import pandas as pd

EOK = 1e8


def _pr(s: pd.Series, higher_better: bool = True) -> pd.Series:
    return s.rank(pct=True, ascending=higher_better) * 100


def _pos(x: pd.Series) -> pd.Series:
    return x.where(x > 0)


def _mf_rank(d: pd.DataFrame) -> pd.Series:
    ok = (d["op"] > 0) & d["ey"].notna() & (d["roc"] > 0)
    e = d[ok]
    s = e["ey"].rank(ascending=False) + e["roc"].rank(ascending=False)
    return s.rank(method="first").reindex(d.index)


def compute(panel: dict, last: pd.DataFrame, fund: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    u = cfg["universe"]
    df = last[["name", "mkt", "sect", "close", "chg", "vol", "mcap"]].copy()
    df["tv20"] = panel["val"].iloc[-20:].mean().reindex(df.index)

    name = df["name"].astype(str)
    kw = "|".join(u["exclude_name_keywords"]) or "^$"
    common = df.index.str[-1] == "0"                        # 우선주 제외(코드 끝자리 0 = 보통주)
    bad = name.str.contains(kw, regex=True) | df["sect"].astype(str).str.contains("SPAC|관리|환기", regex=True)
    uni = common & ~bad & (df["vol"] > 0) & (df["mcap"] >= u["min_mcap_eok"] * EOK) \
        & (df["tv20"] >= u["min_trdval20_eok"] * EOK) & df["mkt"].isin(u["markets"])
    df = df[uni].copy()

    df = df.join(fund, how="left")
    for c in ("ca", "nca", "assets", "cl", "ncl", "liab", "equity", "rev", "op", "ni"):
        if c not in df:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if "fin" not in df:
        df["fin"] = None

    m = df["mcap"]
    bad_eq = (df["equity"] / m) > 20                          # 감자·재상장 등으로 BPS가 깨진 경우
    df.loc[bad_eq, ["equity", "liab", "assets"]] = np.nan
    eq = _pos(df["equity"])
    df["per"] = m / _pos(df["ni"])
    df["pbr"] = m / eq
    df["psr"] = m / _pos(df["rev"])
    df["ey"] = df["op"] / (m + df["liab"])                  # 이익수익률(근사): 영업이익 / (시총 + 부채총계)
    ic = df["ca"] - df["cl"] + df["nca"]                     # 투하자본(근사): 순운전자본 + 비유동자산
    ic = ic.where(ic.notna(), df["assets"])                   # 상세 계정이 없으면 총자산
    df["roc"] = df["op"] / ic.where(ic > 0)
    df["roe"] = df["ni"] / eq
    df["roa"] = df["ni"] / _pos(df["assets"])
    df["opm"] = df["op"] / _pos(df["rev"])
    df["debt"] = df["liab"] / eq

    # 밸류: 이익·순자산·매출 수익률 + 이익수익률 (높을수록 싸다)
    val_parts = pd.concat([_pr(df["ni"] / m), _pr(df["equity"] / m), _pr(df["rev"] / m), _pr(df["ey"])], axis=1)
    df["v"] = val_parts.mean(axis=1).where(val_parts.notna().sum(axis=1) >= 3)
    q_parts = pd.concat([_pr(df["roe"]), _pr(df["roa"]), _pr(df["opm"]), _pr(df["debt"], False)], axis=1)
    df["q"] = q_parts.mean(axis=1).where(q_parts.notna().sum(axis=1) >= 3)

    # 모멘텀
    c = panel["close"].ffill(limit=5)
    t = len(c) - 1

    def ret(a, b):
        if b > t:
            return pd.Series(np.nan, index=c.columns)
        return c.iloc[t - a] / c.iloc[t - b] - 1

    mom = pd.DataFrame({
        "mom12": ret(21, 252), "mom6": ret(0, 126), "mom3": ret(0, 63), "mom6_1": ret(21, 126),
    })
    hist = c.iloc[-252:]
    mom["hi52"] = (c.iloc[-1] / hist.max() - 1).where(hist.notna().sum() >= 120)
    df = df.join(mom, how="left")
    m_parts = pd.concat([_pr(df["mom12"]), _pr(df["mom6_1"]), _pr(df["mom3"])], axis=1)
    df["m"] = m_parts.mean(axis=1).where(m_parts.notna().sum(axis=1) >= 2)

    parts = df[["v", "q", "m"]]
    df["total"] = parts.mean(axis=1).where(parts.notna().all(axis=1))

    df["mf"] = _mf_rank(df)

    # A안 근사: (코스피) 마법공식 상위 N + 소형주(시총 하위 분위) 12-1개월 모멘텀 상위 N
    a = cfg["aplan"]
    k = df[(df["mkt"] == a["market"]) & (df["equity"] > 0)]
    mf_k = _mf_rank(k).dropna().sort_values()
    mf_pick = list(mf_k.index[: a["mf_n"]])
    cut = k["mcap"].quantile(a["smallcap_quantile"])
    sm = k[(k["mcap"] <= cut) & k["mom12"].notna() & ~k.index.isin(mf_pick)]
    sm_pick = list(sm["mom12"].sort_values(ascending=False).index[: a["mom_n"]])
    df["ag"] = ""
    df["ar"] = np.nan
    for i, code in enumerate(mf_pick):
        df.loc[code, ["ag", "ar"]] = ["MF", i + 1]
    for i, code in enumerate(sm_pick):
        df.loc[code, ["ag", "ar"]] = ["SM", i + 1]
    return df

"""가짜 데이터 생성기 — KRX/DART API와 같은 형식(문자열·쉼표 포함)으로 응답해 전체 파이프라인을 검증한다.
종목명은 모두 '샘플…'이며 실제 기업과 무관하다."""
from __future__ import annotations

import datetime as dt

import numpy as np

SYL = "가나다라마바사아자차카타파하"


def _fmt(x, comma=True):
    return f"{x:,.0f}" if comma else f"{x:.0f}"


class DemoMarket:
    def __init__(self, today: dt.date, n: int = 260, seed: int = 7):
        rng = np.random.default_rng(seed)
        days, d = [], today - dt.timedelta(days=450)
        k = 0
        while d < today:
            if d.weekday() < 5:
                k += 1
                if k % 41 != 0:          # 가끔 휴장일
                    days.append(d.strftime("%Y%m%d"))
            d += dt.timedelta(days=1)
        self.days = days
        self.di = {x: i for i, x in enumerate(days)}
        T = len(days)
        codes = [f"9{i:04d}0" for i in range(n)]
        codes[5] = "900055"                       # 우선주
        names = [f"샘플{SYL[i % 14]}{i:03d}" for i in range(n)]
        names[6] = "샘플스팩1호"
        names[7] = "샘플리츠"
        self.codes, self.names = codes, names
        self.mkt = np.where(rng.random(n) < 0.45, "KOSPI", "KOSDAQ")
        shares = rng.uniform(3e6, 2e8, n)
        p0 = np.exp(rng.uniform(np.log(1500), np.log(300000), n))
        mu = rng.normal(0.0002, 0.0008, n)
        sig = rng.uniform(0.012, 0.04, n)
        rets = rng.normal(mu, sig, (T, n))
        jumps = rng.random((T, n)) < 0.004
        rets[jumps] += rng.uniform(0.08, 0.25, jumps.sum())
        rets = np.clip(rets, -0.29, 0.29)
        px = p0 * np.cumprod(1 + rets, axis=0)
        # 3번 종목: 200일째 2:1 액면분할
        self.split_day, self.split_col = 200, 3
        px[self.split_day:, 3] /= 2
        shares_t = np.tile(shares, (T, 1))
        shares_t[self.split_day:, 3] *= 2
        self.close = np.maximum(np.round(px), 1)
        self.open = np.round(self.close * (1 + rng.normal(0, 0.006, (T, n))))
        self.high = np.maximum(self.close, self.open) * (1 + np.abs(rng.normal(0, 0.01, (T, n))))
        self.low = np.minimum(self.close, self.open) * (1 - np.abs(rng.normal(0, 0.01, (T, n))))
        basev = shares * rng.uniform(0.002, 0.02, n)
        self.vol = np.round(basev * np.exp(rng.normal(0, 0.4, (T, n))) * np.where(jumps, 5, 1))
        self.shares = shares_t
        self.n = n

    def day(self, ymd: str, market: str) -> list[dict]:
        i = self.di.get(ymd)
        if i is None:
            return []
        rows = []
        for j in range(self.n):
            if self.mkt[j] != market:
                continue
            c = self.close[i, j]
            base = self.close[i - 1, j] if i > 0 else c
            if i == self.split_day and j == self.split_col:
                base = self.close[i - 1, j] / 2
            diff = c - base
            rows.append({
                "BAS_DD": ymd, "ISU_CD": self.codes[j], "ISU_NM": self.names[j], "MKT_NM": market,
                "SECT_TP_NM": "SPAC(소속부없음)" if j == 6 else ("" if market == "KOSPI" else "벤처기업부"),
                "TDD_CLSPRC": _fmt(c), "CMPPREVDD_PRC": _fmt(diff, False), "FLUC_RT": f"{diff / base * 100:.2f}",
                "TDD_OPNPRC": _fmt(self.open[i, j]), "TDD_HGPRC": _fmt(self.high[i, j]), "TDD_LWPRC": _fmt(self.low[i, j]),
                "ACC_TRDVOL": _fmt(self.vol[i, j]), "ACC_TRDVAL": _fmt(self.vol[i, j] * c),
                "MKTCAP": _fmt(c * self.shares[i, j]), "LIST_SHRS": _fmt(self.shares[i, j]),
            })
        return rows


class DemoDart:
    def __init__(self, mk: DemoMarket, seed: int = 11):
        rng = np.random.default_rng(seed)
        n = mk.n
        self.mk = mk
        mcap = mk.close[-1] * mk.shares[-1]
        self.rev = mcap * rng.uniform(0.2, 3.0, n)
        self.opm = rng.normal(0.06, 0.08, n)
        self.assets = self.rev * rng.uniform(0.6, 2.0, n)
        self.eqr = rng.uniform(0.25, 0.75, n)
        self.car = rng.uniform(0.3, 0.6, n)
        self.clr = rng.uniform(0.4, 0.8, n)
        self.growth = rng.normal(0.05, 0.1, n)
        self.cfs = rng.random(n) < 0.7
        self.idx = {c: j for j, c in enumerate(mk.codes)}
        self.today = dt.date.fromisoformat(f"{mk.days[-1][:4]}-{mk.days[-1][4:6]}-{mk.days[-1][6:]}") + dt.timedelta(days=1)

    def corp_codes(self):
        return {c: "C" + c for c in self.mk.codes}

    def _q(self, j, year, q):  # 분기 매출·영업이익·순이익
        g = (1 + self.growth[j]) ** (year - 2024 + q / 4)
        rev = self.rev[j] / 4 * g
        op = rev * self.opm[j]
        return rev, op, op * 0.75

    def multi(self, corp_codes, year, reprt):
        nq = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}[reprt]
        end = dt.date(year, 3 * nq, 31 if nq in (1, 4) else 30)
        out = []
        for cc in corp_codes:
            sc = cc[1:]
            j = self.idx[sc]
            lag = 100 if nq == 4 else 46
            if j % 17 == 0:
                lag += 30                                   # 늦게 공시하는 회사
            if end + dt.timedelta(days=lag) > self.today:
                continue
            fs = "CFS" if self.cfs[j] else "OFS"

            def cum(y, k):
                return [sum(x) for x in zip(*[self._q(j, y, i) for i in range(1, k + 1)])]

            cur, prv = cum(year, nq), cum(year - 1, nq)
            qcur, qprv = self._q(j, year, nq), self._q(j, year - 1, nq)
            assets = self.assets[j] * (1 + self.growth[j]) ** (year - 2024)
            eq = assets * self.eqr[j]
            liab = assets - eq
            ca = assets * self.car[j]
            cl = liab * self.clr[j]
            bal = {"유동자산": ca, "비유동자산": assets - ca, "자산총계": assets, "유동부채": cl,
                   "비유동부채": liab - cl, "부채총계": liab, "자본총계": eq}
            if j == 9:                                         # 금융업 흉내: 유동/비유동 구분 없음
                bal = {k: v for k, v in bal.items() if k in ("자산총계", "부채총계", "자본총계")}
            for k, v in bal.items():
                out.append({"stock_code": sc, "fs_div": fs, "account_nm": k, "thstrm_amount": f"{v:,.0f}"})
            names = ["매출액", "영업이익(손실)" if j % 3 else "영업이익", "당기순이익"]
            for t, nm in enumerate(names):
                row = {"stock_code": sc, "fs_div": fs, "account_nm": nm}
                if nq == 4:
                    row["thstrm_amount"] = f"{cur[t]:,.0f}"
                    row["frmtrm_amount"] = f"{prv[t]:,.0f}"
                elif nq == 1:
                    row["thstrm_amount"] = f"{cur[t]:,.0f}"
                    row["frmtrm_amount"] = f"{prv[t]:,.0f}"
                else:
                    row["thstrm_amount"] = f"{qcur[t]:,.0f}"
                    row["thstrm_add_amount"] = f"{cur[t]:,.0f}"
                    row["frmtrm_amount"] = f"{qprv[t]:,.0f}"
                    row["frmtrm_add_amount"] = f"{prv[t]:,.0f}"
                out.append(row)
        return out

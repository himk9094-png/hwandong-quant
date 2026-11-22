"""Replay the weekly microcap strategy in a virtual ledger with optional ntfy alerts."""

import argparse
from contextlib import redirect_stdout
from datetime import date
import fcntl
import json
import math
import os
from pathlib import Path
from decimal import Decimal
import re
import sys
import tempfile
from urllib.parse import urlsplit

import requests

from backtest import smallcap as S
from pipeline import config

BAND_PERIOD = 30
BAND_WIDTH = 2.5
NUMBER_OF_HOLDINGS = 20
INITIAL = 10_000_000
FEE = .005 + S.COMM


def exit_signal(prices, reference_close, average_price):
    gain = Decimal(str(reference_close)) / Decimal(str(average_price)) - 1
    if gain >= Decimal("0.15"):
        return "FIXED_15"
    if gain < Decimal("0.10") or len(prices) != BAND_PERIOD + 1 or prices.isna().any():
        return None
    bands = prices.rolling(BAND_PERIOD)
    top = bands.mean() + BAND_WIDTH * bands.std(ddof=0)
    if prices.iloc[-2] <= top.iloc[-2] and prices.iloc[-1] > top.iloc[-1]:
        return "BOLLINGER_30_2_5"
    return None


def load_market():
    with redirect_stdout(sys.stderr):
        market = S.load()
        finance = S.fundamentals(market)
    market["cumidx"] = (1 + market["ret"]).cumprod()
    S.CAP_LO, S.SMALL_Q = 0., .2
    S.RISK_FILTER, S.SEASON = False, False
    return market, finance


def process_session(state, market, finance, i, weekly, adjusted):
    d = market["dates"][i]
    opn = adjusted.iloc[i] / (1 + market["intra"].iloc[i])
    valid = (market["open"].iloc[i] > 0) & (market["val"].iloc[i] > 0)
    positions = state["positions"]
    pending = state["pending"]
    fills = []
    existing = set(positions)
    targets = None

    def record(code, side, value, reason):
        fills.append({"code": code, "side": side, "value_krw": round(value), "reason": reason})

    def sell(code, qty, reason):
        p = positions[code]
        value = qty * opn[code]
        state["cash"] += value * (1 - FEE - S.sell_tax(d))
        if qty >= p["units"] * (1 - 1e-10):
            del positions[code]
        else:
            p["units"] -= qty
        record(code, "SELL", value, reason)

    def buy(code, budget, reason):
        budget = min(budget, state["cash"])
        if budget < 1:
            return
        value = budget / (1 + FEE)
        qty = value / opn[code]
        if code in positions:
            p = positions[code]
            p["average"] = (p["average"] * p["units"] + value) / (p["units"] + qty)
            p["units"] += qty
        else:
            positions[code] = {"units": qty, "average": float(opn[code]), "mark": float(opn[code])}
        state["cash"] -= budget
        record(code, "BUY", value, reason)

    account_at_open = state["cash"] + sum(
        p["units"] * (opn[code] if valid.get(code, False) and math.isfinite(opn[code]) else p["mark"])
        for code, p in positions.items()
    )
    if weekly:
        factors = S.factors(market, finance, i-1, trade_ok=None)
        if factors is None:
            raise ValueError(f"{d}: 주간 순위를 계산할 데이터가 없습니다.")
        targets = S.score(factors, 0).dropna().sort_values().head(NUMBER_OF_HOLDINGS).index.tolist()
        if len(targets) != NUMBER_OF_HOLDINGS:
            raise ValueError(f"{d}: 주간 후보가 20개보다 적습니다.")
        for code in positions.keys() - set(targets):
            pending[code] = "WEEKLY_REMOVAL"

    def tradable(code):
        return valid.get(code, False) and math.isfinite(opn[code]) and opn[code] > 0

    for code, reason in list(pending.items()):
        if tradable(code):
            sell(code, positions[code]["units"], reason)
            del pending[code]

    if targets is not None:
        budget = account_at_open / NUMBER_OF_HOLDINGS
        desired = budget / (1 + FEE)
        for code in targets:
            if code in positions and tradable(code):
                excess = positions[code]["units"] - desired / opn[code]
                if excess > 0 and excess * opn[code] >= 1:
                    sell(code, excess, "WEEKLY_TRIM")
        for code in targets:
            if not tradable(code):
                continue
            if code in positions:
                needed = desired - positions[code]["units"] * opn[code]
                if needed > 0:
                    buy(code, needed * (1 + FEE), "WEEKLY_TOPUP")
            elif code not in existing and len(positions) < NUMBER_OF_HOLDINGS and state["cash"] >= budget / 2:
                buy(code, budget, "WEEKLY_ENTRY")

    for code, p in positions.items():
        price = adjusted[code].iloc[i]
        if not math.isfinite(price):
            continue
        p["mark"] = float(price)
        if code in pending or not valid.get(code, False):
            continue
        candles = adjusted[code].iloc[max(0, i-BAND_PERIOD):i+1]
        reason = exit_signal(candles, price, p["average"])
        if reason:
            pending[code] = reason

    assert state["cash"] >= 0 and len(positions) <= NUMBER_OF_HOLDINGS
    state["last"] = d
    state["fills"].extend({"date": d, **fill} for fill in fills)
    equity = state["cash"] + sum(p["units"] * p["mark"] for p in positions.values())
    return {"date": d, "fills": fills, "pending": dict(pending),
            "positions": len(positions), "equity_krw": round(equity)}


def notification(result):
    received = {"BUY": "가상 매수", "SELL": "가상 매도"}
    lines = [f"[모의투자] {result['date']} KRX 확정 일봉",
             f"평가액 {result['equity_krw']:,}원 | 누적 {(result['equity_krw']/INITIAL-1)*100:+.2f}% | 보유 {result['positions']}종목"]
    lines.extend(f"{received[f['side']]} {f['code']} {f['value_krw']:,}원 ({f['reason']})"
                 for f in result["fills"])
    lines.extend(f"다음 거래 가능 시가 매도 대기 {k} ({reason})"
                 for k, reason in result["pending"].items())
    return "\n".join(lines)


def publish(message):
    if os.environ.get("PAPER_USE_EXISTING_TOPIC") == "1":
        topic = config.load()["ntfy_topic"]
        body = {"topic": topic, "title": "환동퀀트 모의투자", "message": message}
        response = requests.post("https://ntfy.sh/", json=body, timeout=20, allow_redirects=False)
        if not 200 <= response.status_code < 300:
            raise ValueError(f"ntfy 발송 실패 (HTTP {response.status_code})")
        receipt = response.json()
        if receipt.get("topic") != topic or not receipt.get("id"):
            raise ValueError("ntfy 발송 영수증을 확인하지 못했습니다.")
        return

    endpoint = os.environ.get("NTFY_TOPIC_URL", "")
    token = os.environ.get("NTFY_TOKEN", "")
    target = urlsplit(endpoint)
    if (target.scheme != "https" or not target.hostname or target.username or target.password
            or target.query or target.fragment or not re.fullmatch(r"/[-_A-Za-z0-9]+", target.path) or not token):
        raise ValueError("HTTPS ntfy 토픽 주소와 쓰기 토큰을 서버 환경변수에 설정하세요.")
    response = requests.post(endpoint, data=message.encode("utf-8"), timeout=15, allow_redirects=False,
                             headers={"Authorization": f"Bearer {token}", "Title": "Hwandong Paper Trading"})
    if not 200 <= response.status_code < 300:
        raise ValueError(f"ntfy 발송 실패 (HTTP {response.status_code})")


def save(path, state):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        json.dump(state, f, ensure_ascii=False, allow_nan=False)
        temporary = Path(f.name)
    temporary.replace(path)


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--state", required=True, type=Path)
    args.add_argument("--from-date", required=True, type=date.fromisoformat)
    args.add_argument("--as-of", required=True, type=date.fromisoformat)
    args.add_argument("--publish", action="store_true")
    opts = args.parse_args()
    start = opts.from_date.strftime("%Y%m%d")
    end = opts.as_of.strftime("%Y%m%d")
    opts.state.parent.mkdir(parents=True, exist_ok=True)
    with opts.state.with_suffix(opts.state.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = (json.loads(opts.state.read_text(encoding="utf-8")) if opts.state.exists() else
                 {"start": start, "last": "", "cash": INITIAL, "positions": {}, "pending": {},
                  "fills": [], "last_alert": "", "latest": None})
        if state["start"] != start or start > end or state["last"] > end:
            args.error("장부의 시작일 또는 마지막 처리일과 요청 범위가 맞지 않습니다.")
        if state["last"] != end:
            market, finance = load_market()
            dates = market["dates"]
            if start not in dates or end not in dates:
                args.error("시작일과 종료일의 확정 KRX 일봉이 필요합니다.")
            weeks = set(S.reb_days(dates, "W"))
            if dates.index(start) not in weeks:
                args.error("시작일은 주간 첫 거래일이어야 합니다.")
            adjusted = market["cumidx"].where(market["alive"])
            for i in range(dates.index(start), dates.index(end)+1):
                if dates[i] > state["last"]:
                    state["latest"] = process_session(state, market, finance, i, i in weeks, adjusted)
            save(opts.state, state)
        message = notification(state["latest"])
        if opts.publish and state["last_alert"] != end:
            publish(message)
            state["last_alert"] = end
            save(opts.state, state)
        print(message)
        print(f"장부 누적 가상 체결 {len(state['fills'])}건")


if __name__ == "__main__":
    main()

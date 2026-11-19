"""토스 봇과 같은 규칙의 모의투자(가상 돈) — 매일 아침 전 거래일 시세로 체결을 따져 ntfy로 알림.

규칙: 슈퍼퀄리티 소형주 20종목(🟡 제외) 동일 금액 · 4·6·9·12월 첫 거래일 교체 · 산 가격 × 1.2 지정가 익절 → 현금 대기.
- 아침 실행 때 낸 주문은 그날 장에서 체결된 것으로 보고, 다음 날 아침에 그날 시가·고가로 따진다.
  (매수·교체 = 그날 시가 + 슬리피지 0.5%, 익절 = 시가가 목표 이상이면 시가, 아니면 고가 ≥ 목표 × 1.003 일 때 목표가)
- 종목 목록 = docs/smallcap.json 의 '다음 교체 후보'(전날 밤 계산). 상태 = docs/paper.json.
  python -m backtest.paper            # 처리 + ntfy
  python -m backtest.paper --dry      # 알림 없이
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os

import requests

from backtest import collect as C
from backtest import smallcap as S

STATE = C.ROOT / "docs" / "paper.json"
PICKS = C.ROOT / "docs" / "smallcap.json"
CAPITAL = 600_000
N, TP, STRAT = 20, 0.20, "6"
COMM, SLIP = 0.00015, 0.005
QM = (4, 6, 9, 12)


def kst_today() -> dt.date:
    return (dt.datetime.utcnow() + dt.timedelta(hours=9)).date()


def tick(p: float) -> int:
    for lim, t in ((2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500)):
        if p < lim:
            return t
    return 1000


def up_tick(p: float) -> int:
    t = tick(p)
    return int(math.ceil(p / t) * t)


def num(x) -> float:
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return 0.0


def picks() -> list[dict]:
    J = json.loads(PICKS.read_text())
    now = J["now"]
    L = (now["lists"].get("S") or now["lists"])[STRAT]["1"][:N]
    return [{"code": c, "name": now["stocks"][c]["n"]} for c in L]


def day_prices(s, ymd: str) -> dict:
    rows = C._krx_day(s, ymd, "KOSPI") + C._krx_day(s, ymd, "KOSDAQ")
    return {r["ISU_CD"]: {"o": num(r.get("TDD_OPNPRC")), "h": num(r.get("TDD_HGPRC")), "c": num(r.get("TDD_CLSPRC")),
                          "n": r.get("ISU_NM")} for r in rows}


def value(st) -> float:
    return st["cash"] + sum(h["qty"] * h["last"] for h in st["hold"].values())


def process(st, ymd: str, px: dict, ev: list):
    tax = S.sell_tax(ymd)
    # ① 아침에 낸 매도(교체) → 시가
    for code in st.get("pend_sell", []):
        h = st["hold"].pop(code, None)
        p = px.get(code)
        if not h:
            continue
        if not p or p["o"] <= 0:                       # 거래정지 등: 못 팖 → 다시 보유로
            st["hold"][code] = h
            continue
        amt = h["qty"] * p["o"] * (1 - SLIP)
        st["cash"] += amt - amt * (COMM + tax)
        ev.append(f"교체 매도 {h['name']} {h['qty']}주 @{p['o']:,.0f} ({p['o'] / h['entry'] - 1:+.1%})")
    st["pend_sell"] = []
    # ② 아침에 낸 매수 → 시가(+슬리피지)
    for o in st.get("pend_buy", []):
        p = px.get(o["code"])
        if not p or p["o"] <= 0:
            ev.append(f"매수 못 함 {o['name']} (거래 없음)")
            continue
        fill = p["o"] * (1 + SLIP)
        qty = int(min(o["budget"], st["cash"]) // (fill * (1 + COMM)))
        if qty <= 0:
            ev.append(f"매수 못 함 {o['name']} (1주 {fill:,.0f}원 > 배정 {o['budget']:,.0f}원)")
            continue
        st["cash"] -= qty * fill * (1 + COMM)
        st["hold"][o["code"]] = {"name": o["name"], "qty": qty, "entry": fill, "last": p["c"], "since": ymd}
        ev.append(f"매수 {o['name']} {qty}주 @{fill:,.0f}")
    st["pend_buy"] = []
    # ③ +20% 익절(그날 시가·고가)
    for code, h in list(st["hold"].items()):
        p = px.get(code)
        if not p or p["o"] <= 0:
            continue
        tgt = up_tick(h["entry"] * (1 + TP))
        if p["o"] >= tgt:
            sell = p["o"]
        elif p["h"] >= tgt * 1.003:
            sell = tgt
        else:
            h["last"] = p["c"] or h["last"]
            continue
        amt = h["qty"] * sell
        st["cash"] += amt - amt * (COMM + tax)
        st["hold"].pop(code)
        st["tp"] = st.get("tp", 0) + 1
        ev.append(f"🎯 익절 {h['name']} {h['qty']}주 @{sell:,.0f} (+{sell / h['entry'] - 1:.1%}) → 현금 대기")
    st["last_day"] = ymd


def order_rebalance(st, ev, why: str):
    P = picks()
    tgt = {p["code"] for p in P}
    st["pend_sell"] = [c for c in st["hold"] if c not in tgt]
    keep = {c: h for c, h in st["hold"].items() if c in tgt}
    total = value(st)
    per = total / N
    buys = [p for p in P if p["code"] not in keep]
    # 남겨둘 종목은 그대로, 새 종목에 동일 금액(현금 = 팔 종목 + 현금)
    st["pend_buy"] = [{"code": p["code"], "name": p["name"], "budget": per} for p in buys]
    ev.append(f"{why}: 매도 {len(st['pend_sell'])} · 매수 {len(buys)} · 유지 {len(keep)} (종목당 약 {per:,.0f}원)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    today = kst_today()
    ev: list[str] = []
    if STATE.exists():
        st = json.loads(STATE.read_text())
    else:
        st = {"start": today.isoformat(), "capital": CAPITAL, "cash": CAPITAL, "hold": {}, "pend_buy": [], "pend_sell": [],
              "last_day": (today - dt.timedelta(days=1)).strftime("%Y%m%d"), "hist": [], "tp": 0}
        order_rebalance(st, ev, "모의투자 시작 — 오늘 시가에")
    # 지난 거래일들 처리(전 거래일까지, KRX가 아직 안 올린 날은 다음에)
    s = requests.Session()
    s.headers.update({"AUTH_KEY": os.environ["KRX_API_KEY"].strip(), "User-Agent": "quant-dashboard/1.0"})
    d = dt.datetime.strptime(st["last_day"], "%Y%m%d").date() + dt.timedelta(days=1)
    while d < today:
        ymd = d.strftime("%Y%m%d")
        if d.weekday() < 5:
            px = day_prices(s, ymd)
            if px:
                process(st, ymd, px, ev)
                st["hist"].append([ymd, round(value(st))])
            elif (today - d).days <= 1:
                break                                   # 어제 시세가 아직 안 올라옴 → 다음 실행에
            else:
                st["last_day"] = ymd                    # 휴장일
        else:
            st["last_day"] = ymd
        d += dt.timedelta(days=1)
    # 오늘이 분기 교체일(4·6·9·12월 첫 평일)이면 오늘 시가 교체 주문
    first = dt.date(today.year, today.month, 1)
    while first.weekday() >= 5:
        first += dt.timedelta(days=1)
    if today.month in QM and today == first and st.get("rebal") != today.isoformat() and st["start"] != today.isoformat():
        order_rebalance(st, ev, "분기 교체 — 오늘 시가에")
        st["rebal"] = today.isoformat()
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1))
    v = value(st)
    hold = sorted(st["hold"].values(), key=lambda h: h["last"] / h["entry"], reverse=True)
    lines = [f"평가 {v:,.0f}원 ({v / st['capital'] - 1:+.2%}, {st['start']} 시작) · 보유 {len(st['hold'])} · 현금 {st['cash']:,.0f}원 · 익절 누적 {st.get('tp', 0)}회"]
    lines += ev
    if hold:
        lines.append("수익 상위: " + ", ".join(f"{h['name']} {h['last'] / h['entry'] - 1:+.0%}" for h in hold[:3]))
        lines.append("익절까지: " + ", ".join(f"{h['name']} {up_tick(h['entry'] * 1.2) / h['last'] - 1:+.0%}" for h in hold[:3]))
    msg = "\n".join(lines)
    print(msg)
    if not a.dry and (ev or today.weekday() < 5):
        from pipeline import ntfy
        ntfy.send("📒 모의투자 · 슈퍼퀄리티20 +20% 익절", msg, tags=["chart_with_upwards_trend"])


if __name__ == "__main__":
    main()

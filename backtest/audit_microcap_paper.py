"""Compare the deployed paper rules with the assumptions of the November 22 study.

Run with python -m backtest.audit_microcap_paper using store/ at data commit
a7f1e2108d551687779ae7374216617cfdc58bfe (KRX ends 2026-11-16).
The two disappearance models are retrospective sensitivity scenarios. Neither
is an execution rule for current trading. The deployed engine is exercised
directly as a third comparison. The late period starts on 2021-04-05.
"""
import argparse
import numpy as np
import pandas as pd

from backtest import smallcap as S
from backtest import ntfy_microcap_paper as paper

INITIAL = 10_000_000.
COUNT = 20
FEE = .005 + S.COMM


def prepare():
    market = S.load()
    market["cumidx"] = (1 + market["ret"]).cumprod()
    finance = S.fundamentals(market)
    S.MIN_CAP, S.MIN_VAL, S.TOPK, S.KOSPI_ONLY = 0., 1e8, 0, False
    S.CAP_LO, S.SMALL_Q, S.RISK_FILTER, S.SEASON = 0., .2, False, False
    dates = market["dates"]
    columns = dict(zip(market["codes"], range(len(market["codes"]))))
    weeks = S.reb_days(dates, "W")
    targets = {}
    for i in weeks:
        f = S.factors(market, finance, i-1, trade_ok=None)
        scores = S.score(f, 0).dropna().sort_values()
        targets[i] = tuple(columns[code] for code in scores.head(COUNT).index)

    close = market["cumidx"]
    raw_open = market["open"]
    adjusted = close.where(market["alive"])
    bands = adjusted.rolling(30)
    top = bands.mean() + 2.5 * bands.std(ddof=0)
    trigger = ((adjusted > top) & (adjusted.shift() <= top.shift())).to_numpy()
    # Retrospective classification only. It knows whether a stock ever reappears.
    alive = market["alive"].to_numpy()
    gone = ~np.logical_or.accumulate(alive[::-1], axis=0)[::-1]
    valid = ((raw_open > 0) & (market["val"] > 0)).to_numpy()
    opn = (close / (1 + market["intra"])).to_numpy()
    valid &= np.isfinite(opn) & (opn > 0)
    return (dates, targets, close.to_numpy(), opn, valid, trigger, gone), market, finance


def simulate(data, first, last, approach, exit_enabled):
    dates, targets, close, opn, valid, trigger, gone = data
    cash = INITIAL
    held = {}
    pending = {}
    attempted = set()
    names = ()
    budget = 0.
    values = [INITIAL]
    removals = 0

    def sell(i, k, units, price):
        nonlocal cash
        current, basis = held[k]
        cash += units * price * (1 - FEE - S.sell_tax(dates[i]))
        remaining = current - units
        if remaining <= current * 1e-10:
            del held[k]
        else:
            held[k] = remaining, basis * remaining / current

    def buy(i, k, spend):
        nonlocal cash
        spend = min(cash, spend)
        if spend < 1e-7:
            return
        value = spend / (1 + FEE)
        current, basis = held.get(k, (0., 0.))
        held[k] = current + value / opn[i,k], basis + value
        cash -= spend

    for i in range(first, last):
        weekly = i in targets
        if weekly:
            names = targets[i]
            attempted = set(held)&set(names)
            pending.update({k:"weekly" for k in held.keys()-set(names)})
            nav = cash + sum(q * (opn[i,k] if valid[i,k] else close[i-1,k]) for k,(q,_) in held.items())
            budget = nav / COUNT
        for k in list(pending):
            if valid[i,k]:
                sell(i,k,held[k][0],opn[i,k])
                del pending[k]
        if weekly:
            goal = budget / (1 + FEE)
            for k in names:
                if k in held and valid[i,k]:
                    excess = held[k][0]*opn[i,k]-goal
                    if excess > 1e-7:
                        sell(i,k,excess/opn[i,k],opn[i,k])
            for k in names:
                if not valid[i,k]:
                    continue
                if k in held:
                    buy(i,k,max(0.,goal-held[k][0]*opn[i,k])*(1+FEE))
        for k in names:
            if k not in attempted and k not in held and valid[i,k] and len(held)<COUNT and cash>=budget/2:
                buy(i,k,budget)
                attempted.add(k)
        for k in list(held):
            if gone[i,k]:
                if approach=="last_price":
                    sell(i,k,held[k][0],close[i,k])
                else:
                    del held[k]
                pending.pop(k,None)
                removals+=1
        gross = sum(q*close[i,k] for k,(q,_) in held.items())
        nav = cash + gross*(1-FEE-S.sell_tax(dates[i]))
        values.append(nav)
        if exit_enabled:
            for k,(q,basis) in held.items():
                if k not in pending and valid[i,k]:
                    ratio=close[i,k]*q/basis
                    if ratio>=1.15 or (ratio>=1.10 and trigger[i,k]):
                        pending[k]="profit"
    a=np.array(values)
    years=(pd.Timestamp(dates[last-1])-pd.Timestamp(dates[first-1])).days/365.25
    return dict(cagr=round(100*((a[-1]/INITIAL)**(1/years)-1),4),
                mdd=round(100*(a/np.maximum.accumulate(a)-1).min(),4),
                removals=removals,pending=len(pending))


def live_replay(market, finance, first, last, weeks, exits):
    dates = market["dates"]
    adjusted = market["cumidx"].where(market["alive"])
    state = {"cash": INITIAL, "positions": {}, "pending": {}, "fills": [], "last": ""}
    saved = paper.exit_signal
    equity = [INITIAL]
    try:
        if not exits:
            paper.exit_signal = lambda *args: None
        for i in range(first, last):
            snapshot = paper.process_session(state, market, finance, i, i in weeks, adjusted)
            equity.append(snapshot["equity_krw"])
    finally:
        paper.exit_signal = saved
    years = (pd.Timestamp(dates[last-1])-pd.Timestamp(dates[first-1])).days/365.25
    values = np.array(equity)
    return dict(cagr=round(100*((values[-1]/INITIAL)**(1/years)-1),4),
                mdd=round(100*(values/np.maximum.accumulate(values)-1).min(),4),
                pending=len(state["pending"]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end",default="20261116")
    opts=parser.parse_args()
    data, market, finance=prepare()
    dates, targets, *_ = data
    first=next(iter(targets))
    late=next(i for i in targets if dates[i]>="20210401")
    last=dates.index(opts.end)+1
    print("dates",dates[first],dates[late],dates[last-1],flush=True)
    for method in ("last_price","writeoff"):
        for label, start in (("full",first),("late",late)):
            for exits in (False,True):
                print(method,label,exits,simulate(data,start,last,method,exits),flush=True)
    for label, start in (("full",first),("late",late)):
        for exits in (False,True):
            print("deployed",label,exits,live_replay(market,finance,start,last,targets,exits),flush=True)


if __name__ == "__main__":
    main()

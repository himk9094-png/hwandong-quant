#!/usr/bin/env python3
"""Toss Securities Open API helper for hwandong-quant (stdlib only).

What it does now: for the stocks you register as "quant" stocks, keep ONE sell
conditional order (조건주문) at  average purchase price x 1.20  (rounded up to tick).
Toss watches the price during the KRX regular session and sells for you; this script
only creates / fixes / cancels those orders once a day. Other stocks are never touched.

  hq toss key      paste client_id / client_secret (hidden)
  hq toss check    token, account, holdings, open conditional orders
  hq toss pick     choose which holdings are quant stocks (by number)
  hq toss list     registered quant stocks
  hq toss tp       sync +20% take-profit orders (paper mode only prints)
  hq toss live     really send orders     hq toss paper   print only (default)
  hq toss buy      morning rotation: money from SOLD quant stocks -> buy Super Quality stocks
                   (Mon-Fri 08:35 by timer; opening auction LIMIT order at prev close +3%)
  hq toss plan     show budget, slot size and the next Super Quality stocks to buy
  hq toss autobuy on|off   turn the morning rotation on/off (default on)
  hq toss refill on|off    Super Quality take-profit money: buy next stock now (on) / wait (off, default)

Rotation rules: only money from selling REGISTERED quant stocks is used (US ETFs, other
stocks and your other cash are never touched). Money from old quant stocks buys the next
Super Quality stocks you don't hold (up to 20, equal size). Money from a Super Quality
stock's take-profit waits for the next quarterly rebalance (refill=false).

Terminal messages are English because the Lightsail browser terminal shows Korean as boxes.
"""
from __future__ import annotations

import datetime as dt
import getpass
import json
import math
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

D = os.environ.get("HQ_HOME", "/opt/hq")
ENV, CFG, TOK, LOG = f"{D}/toss.env", f"{D}/toss.json", f"{D}/toss_token.json", f"{D}/toss.log"
API = os.environ.get("TOSS_API", "https://openapi.tossinvest.com")
REPO = "himk9094-png/hwandong-quant"
TAG = "hqtp"                       # clientOrderId prefix: orders made by this script

_gai = socket.getaddrinfo          # IPv4 first: the allowed IP is the server's IPv4


def _v4(*a, **k):
    r = _gai(*a, **k)
    return sorted(r, key=lambda x: x[0] != socket.AF_INET)


socket.getaddrinfo = _v4


# ------------------------------------------------------------------ config / log
def cfg() -> dict:
    c = {"mode": "paper", "tp": 0.20, "symbols": [], "expire_days": 180, "account": None,
         # rotation (hq toss buy)
         "buy": True, "n": 20, "list": "S/6/1", "buy_up": 0.03, "refill": False,
         "budget": 0.0, "sq": [], "sold": {}, "pending": {}, "snap": {}, "snap_at": None, "seen": []}
    try:
        c.update(json.load(open(CFG)))
    except FileNotFoundError:
        pass
    return c


def save(c: dict):
    os.makedirs(D, exist_ok=True)
    json.dump(c, open(CFG, "w"), indent=2)
    os.chmod(CFG, 0o600)


def log(msg: str):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def notify(title: str, body: str):
    if os.environ.get("HQ_NO_NOTIFY"):
        print(f"(notify skipped) {title}\n{body}")
        return
    try:
        raw = urllib.request.urlopen(f"https://raw.githubusercontent.com/{REPO}/main/config.json", timeout=15).read()
        t = json.loads(raw).get("ntfy_topic")
        if t:
            req = urllib.request.Request(f"https://ntfy.sh/{t}", data=f"{title}\n{body}".encode(), method="POST",
                                         headers={"Tags": "moneybag"})
            urllib.request.urlopen(req, timeout=15)
    except Exception as e:  # noqa: BLE001
        log(f"(ntfy failed: {e})")


# ------------------------------------------------------------------ HTTP
def creds():
    v = {}
    try:
        for ln in open(ENV):
            if "=" in ln:
                k, x = ln.strip().split("=", 1)
                v[k] = x
    except FileNotFoundError:
        pass
    return v.get("TOSS_CLIENT_ID"), v.get("TOSS_CLIENT_SECRET")


def call(method: str, path: str, params=None, body=None, form=None, auth=True, acct=None, retry=3):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    h = {"User-Agent": "hq-toss/1.0", "Accept": "application/json"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if auth:
        h["Authorization"] = "Bearer " + token()
    if acct:
        h["X-Tossinvest-Account"] = str(acct)
    for i in range(retry):
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                txt = r.read().decode() or "{}"
                return r.status, json.loads(txt)
        except urllib.error.HTTPError as e:
            txt = e.read().decode(errors="replace")
            if e.code == 429 and i < retry - 1:
                time.sleep(float(e.headers.get("Retry-After") or 1.5))
                continue
            try:
                return e.code, json.loads(txt)
            except ValueError:
                return e.code, {"raw": txt[:300]}
        except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
            if i < retry - 1:
                time.sleep(3)
                continue
            return 0, {"raw": str(e)}
    return 0, {}


HINT = {401: "token/keys wrong -> hq toss key", 403: "IP not allowed -> add 13.124.17.147 in Toss WTS Open API settings",
        429: "too many requests, try later"}


def die(code, j, what):
    print(f"FAIL {what}: HTTP {code} {json.dumps(j)[:300]}")
    if code in HINT:
        print("hint:", HINT[code])
    sys.exit(2)


def token() -> str:
    try:
        t = json.load(open(TOK))
        if t["exp"] > time.time() + 60:
            return t["access_token"]
    except (FileNotFoundError, ValueError, KeyError):
        pass
    cid, sec = creds()
    if not cid:
        print("No Toss keys yet. Run: hq toss key")
        sys.exit(2)
    code, j = call("POST", "/oauth2/token", form={"grant_type": "client_credentials", "client_id": cid,
                                                   "client_secret": sec}, auth=False)
    if code != 200 or "access_token" not in j:
        die(code, j, "token")
    j["exp"] = time.time() + int(j.get("expires_in", 3600))
    with open(TOK, "w") as f:
        json.dump(j, f)
    os.chmod(TOK, 0o600)
    return j["access_token"]


def account(c) -> int:
    if c.get("account"):
        return c["account"]
    code, j = call("GET", "/api/v1/accounts")
    if code != 200:
        die(code, j, "accounts")
    acc = [a for a in j.get("result", []) if a.get("accountType") == "BROKERAGE"] or j.get("result", [])
    if not acc:
        print("No account found.")
        sys.exit(2)
    c["account"] = acc[0]["accountSeq"]
    save(c)
    return c["account"]


def holdings(c) -> list:
    code, j = call("GET", "/api/v1/holdings", acct=account(c))
    if code != 200:
        die(code, j, "holdings")
    return [x for x in j.get("result", {}).get("items", []) if x.get("marketCountry", "KR") == "KR"]


def open_conds(c) -> list:
    out, cur = [], None
    for _ in range(20):
        p = {"status": "OPEN", "limit": 100}
        if cur:
            p["cursor"] = cur
        code, j = call("GET", "/api/v1/conditional-orders", params=p, acct=account(c))
        if code != 200:
            die(code, j, "conditional orders")
        r = j.get("result", {})
        out += r.get("conditionalOrders", [])
        cur = r.get("nextCursor")
        if not cur:
            break
    return out


# ------------------------------------------------------------------ price helpers
def tick(p: float) -> int:
    """KRX tick size (KOSPI/KOSDAQ, 2023~)."""
    for lim, t in ((2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500)):
        if p < lim:
            return t
    return 1000


def up_tick(p: float) -> int:
    t = tick(p)
    return int(math.ceil(p / t) * t)


# ------------------------------------------------------------------ commands
def cmd_key():
    print("\n=== STEP 1/2 ===  Paste CLIENT ID now (not shown), then press Enter")
    cid = getpass.getpass("client_id: ").strip()
    print("\n=== STEP 2/2 ===  Paste CLIENT SECRET now (not shown), then press Enter")
    sec = getpass.getpass("client_secret: ").strip()
    if not cid or not sec:
        print("Empty. Nothing saved. Run again: hq toss key")
        return
    os.makedirs(D, exist_ok=True)
    with open(ENV, "w") as f:
        f.write(f"TOSS_CLIENT_ID={cid}\nTOSS_CLIENT_SECRET={sec}\n")
    os.chmod(ENV, 0o600)
    try:
        os.remove(TOK)
    except FileNotFoundError:
        pass
    c = cfg()
    c["account"] = None
    save(c)
    token()
    print("OK - keys work. Account:", account(c), "\nNext: hq toss check   then   hq toss pick")


def cmd_check():
    c = cfg()
    token()
    print("OK token   account", account(c), "  mode:", c["mode"].upper(), "  take-profit: +%d%%" % round(c["tp"] * 100))
    hs = holdings(c)
    print(f"\nHoldings (KR): {len(hs)}")
    for i, h in enumerate(hs, 1):
        mark = "Q" if h["symbol"] in c["symbols"] else " "
        print(f" {i:2d} [{mark}] {h['symbol']}  qty {h['quantity']:>6}  avg {float(h['averagePurchasePrice']):>10,.0f}"
              f"  now {float(h['lastPrice']):>10,.0f}  {float(h['profitLoss']['rate']) * 100:+6.1f}%")
    co = open_conds(c)
    print(f"\nOpen conditional orders: {len(co)}")
    for o in co:
        f = o.get("first") or {}
        print(f"  {o['symbol']} {f.get('orderSide')} qty {o['quantity']} trigger {f.get('triggerPrice')} "
              f"limit {f.get('orderPrice')} until {o.get('expireDate')} [{o.get('status')}]")
    print("\n[Q] = registered quant stock. Change with: hq toss pick")


def cmd_pick():
    c = cfg()
    hs = holdings(c)
    print("Match with your Toss app by QUANTITY and AVERAGE PRICE (names can't show in this terminal).\n")
    for i, h in enumerate(hs, 1):
        mark = "Q" if h["symbol"] in c["symbols"] else " "
        print(f" {i:2d} [{mark}] {h['symbol']}  qty {h['quantity']:>6}  avg {float(h['averagePurchasePrice']):>10,.0f}"
              f"  {float(h['profitLoss']['rate']) * 100:+6.1f}%")
    print("\nType the NUMBERS of your quant stocks separated by spaces (e.g. 1 3 4 7), then Enter.")
    print("Only these will ever get automatic orders. Empty = keep current list.")
    s = input("> ").strip()
    if not s:
        return
    try:
        idx = [int(x) for x in s.replace(",", " ").split()]
        c["symbols"] = sorted({hs[i - 1]["symbol"] for i in idx if 1 <= i <= len(hs)})
    except (ValueError, IndexError):
        print("Could not read that. Nothing changed.")
        return
    save(c)
    print("Saved quant stocks:", " ".join(c["symbols"]) or "(none)")


def cmd_tp(quiet=False):
    c = cfg()
    tp, live = float(c["tp"]), c["mode"] == "live"
    acct = account(c)
    hs = {h["symbol"]: h for h in holdings(c)}
    co = open_conds(c)
    sells = {}
    for o in co:
        f = o.get("first") or {}
        if o.get("type") == "SINGLE" and f.get("orderSide") == "SELL":
            sells.setdefault(o["symbol"], []).append(o)
    exp = (dt.date.today() + dt.timedelta(days=int(c["expire_days"]))).isoformat()
    done, notes = [], []
    if live and c.get("buy", True):            # 오늘 체결된 매수 정산 + 팔린 퀀트 종목 대금을 매수 예산에 반영
        notes += settle_buys(c) + collect(c, hs)
        save(c)
    ours = {sym for sym, os_ in sells.items() if any(str(o.get("clientOrderId") or "").startswith(TAG) for o in os_)}
    for sym in sorted(set(c["symbols"]) | ours):
        h = hs.get(sym)
        mine = [o for o in sells.get(sym, []) if str(o.get("clientOrderId") or "").startswith(TAG)]
        other = [o for o in sells.get(sym, []) if o not in mine]
        if sym not in c["symbols"] or not h or float(h["quantity"]) <= 0:
            for o in mine:                         # no longer held -> remove our leftover order
                act(live, "DELETE", f"/api/v1/conditional-orders/{o['conditionalOrderId']}", None, acct,
                    f"cancel {sym} (not held or not a quant stock any more)", done)
            continue
        if other:
            notes.append(f"{sym}: you already have your own sell conditional order -> left as is")
            continue
        qty = int(float(h["quantity"]))
        trig = up_tick(float(h["averagePurchasePrice"]) * (1 + tp))
        ok = [o for o in mine if int(float(o["quantity"])) == qty and int(float((o.get("first") or {}).get("triggerPrice") or 0)) == trig]
        if ok and len(mine) == 1:
            continue
        for o in mine:
            act(live, "DELETE", f"/api/v1/conditional-orders/{o['conditionalOrderId']}", None, acct,
                f"cancel old {sym}", done)
        body = {"symbol": sym, "type": "SINGLE", "quantity": str(qty), "orderType": "LIMIT", "expireDate": exp,
                "clientOrderId": f"{TAG}{sym}{dt.date.today():%y%m%d}{qty}"[:36],
                "first": {"orderSide": "SELL", "triggerPrice": str(trig), "orderPrice": str(trig)}}
        act(live, "POST", "/api/v1/conditional-orders", body, acct,
            f"SELL {sym} {h.get('name', '')} qty {qty} at {trig:,} (avg {float(h['averagePurchasePrice']):,.0f} +{round(tp * 100)}%)", done)
    head = "LIVE" if live else "PAPER (nothing sent)"
    if done or notes:
        msg = "\n".join(done + notes)
        log(f"[{head}]\n{msg}")
        notify(f"토스 익절 주문 {'실행' if live else '모의'}", msg)
    elif not quiet:
        print(f"[{head}] all quant stocks already have correct +{round(tp * 100)}% orders. Nothing to do.")


def act(live, method, path, body, acct, label, done):
    if not live:
        done.append("would " + label)
        return
    code, j = call(method, path, body=body, acct=acct)
    if code in (200, 201, 204):
        done.append("done " + label)
    else:
        done.append(f"FAILED {label}: HTTP {code} {json.dumps(j)[:200]}")


# ------------------------------------------------------------------ rotation: sold quant stocks -> Super Quality buys
SITE = os.environ.get("HQ_SITE", "https://himk9094-png.github.io/hwandong-quant")
BTAG = "hqb"                       # clientOrderId prefix: buys made by this script
KST = dt.timezone(dt.timedelta(hours=9))
MIN_ORDER = 30000                  # 원. 이보다 작은 돈으로는 사지 않음
OPEN_ST = ("PENDING", "PENDING_CANCEL", "PENDING_REPLACE", "PARTIAL_FILLED")


def now_kst() -> dt.datetime:
    return dt.datetime.now(KST)


def num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def when(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=KST)
    except ValueError:
        return None


def market_open_today() -> bool:
    today = f"{now_kst():%Y-%m-%d}"
    code, j = call("GET", "/api/v1/market-calendar/KR", params={"date": today})
    if code != 200:
        log(f"(market calendar failed HTTP {code}) -> no buying today")
        return False
    t = (j.get("result") or {}).get("today") or {}
    return t.get("date") == today and t.get("integrated") is not None


def sq_list(c):
    """환동퀀트 슈퍼퀄리티 목록(순위순)과 종목 이름."""
    band, strat, risk = str(c.get("list") or "S/6/1").split("/")
    raw = urllib.request.urlopen(f"{SITE}/smallcap.json?t={int(time.time())}", timeout=30).read()
    now = json.loads(raw)["now"]
    names = {k: v.get("n", k) for k, v in (now.get("stocks") or {}).items()}
    return list(now["lists"][band][strat][risk]), names, now.get("sig", "")


def orders(c, status, symbol=None, since=None) -> list | None:
    out, cur = [], None
    for _ in range(10):
        p = {"status": status, "limit": 100}
        if symbol:
            p["symbol"] = symbol
        if since:
            p["from"] = since
        if cur:
            p["cursor"] = cur
        code, j = call("GET", "/api/v1/orders", params=p, acct=account(c))
        if code != 200:
            log(f"(orders {status} {symbol or ''} failed HTTP {code})")
            return None
        r = j.get("result") or {}
        out += r.get("orders") or []
        cur = r.get("nextCursor")
        if not r.get("hasNext") or not cur:
            break
    return out


def collect(c, hs) -> list:
    """지난번 이후 팔린 퀀트 종목의 매도 대금(수수료·세금 뺀 돈)을 매수 예산에 더한다."""
    notes = []
    held = {s: int(num(h["quantity"])) for s, h in hs.items()}
    snap, seen = c.get("snap") or {}, set(c.get("seen") or [])
    t0 = when(c.get("snap_at")) if c.get("snap_at") else None
    since = f"{now_kst().date() - dt.timedelta(days=14):%Y-%m-%d}"
    for sym in sorted(set(c["symbols"]) | set(snap)):
        before, now = int(snap.get(sym, 0)), held.get(sym, 0)
        if t0 is None or now >= before:
            continue
        os_ = orders(c, "CLOSED", sym, since)
        if os_ is None:
            notes.append(f"{sym}: sold but order history failed -> will retry next run")
            continue
        got = 0.0
        for o in os_:
            e = o.get("execution") or {}
            t = when(o.get("orderedAt"))
            if o.get("side") != "SELL" or o["orderId"] in seen or num(e.get("filledQuantity")) <= 0 or (t and t < t0):
                continue
            seen.add(o["orderId"])
            got += num(e.get("filledAmount")) - num(e.get("commission")) - num(e.get("tax"))
        if got <= 0:
            notes.append(f"{sym}: {before - now} shares gone but no sell fill found -> nothing added")
        elif sym in c["sq"] and not c.get("refill"):
            c["sold"][sym] = f"{now_kst():%Y-%m-%d}"
            notes.append(f"SOLD {sym} (Super Quality take-profit) {got:,.0f} KRW -> waits for next quarterly rebalance")
        else:
            c["budget"] = round(num(c["budget"]) + got)
            notes.append(f"SOLD {sym} -> +{got:,.0f} KRW to buy budget (now {c['budget']:,.0f})")
        if now == 0:
            c["symbols"] = [s for s in c["symbols"] if s != sym]
            c["sq"] = [s for s in c["sq"] if s != sym]
    c["snap"] = {s: held[s] for s in c["symbols"] if held.get(s, 0) > 0}
    c["snap_at"] = now_kst().isoformat(timespec="seconds")
    c["seen"] = sorted(seen)[-500:]
    return notes


def settle_buys(c) -> list:
    """어제·오늘 낸 매수 주문 결과: 쓴 돈만큼 예산에서 확정하고 안 쓴 돈은 돌려놓는다."""
    notes = []
    for oid, p in list((c.get("pending") or {}).items()):
        code, j = call("GET", f"/api/v1/orders/{oid}", acct=account(c))
        if code != 200:
            notes.append(f"{p['sym']}: buy order check failed HTTP {code} -> retry next run")
            continue
        o = j.get("result") or {}
        if o.get("status") in OPEN_ST:
            continue
        e = o.get("execution") or {}
        q = int(num(e.get("filledQuantity")))
        spent = num(e.get("filledAmount")) + num(e.get("commission"))
        c["budget"] = round(num(c["budget"]) + p["reserved"] - spent)
        if q > 0:
            if p["sym"] not in c["sq"]:
                c["sq"].append(p["sym"])
            notes.append(f"BOUGHT {p['sym']} {p.get('name', '')} {q} sh at {num(e.get('averageFilledPrice')):,.0f} ({spent:,.0f} KRW)")
        else:
            c["symbols"] = [s for s in c["symbols"] if s != p["sym"]]
            notes.append(f"{p['sym']} {p.get('name', '')} not filled at the open ({o.get('status')}) -> money back, try again tomorrow")
        del c["pending"][oid]
    return notes


def plan(c, hs, budget, cash):
    """살 종목과 금액. (종목, 이름, 수량, 지정가, 금액) 목록과 설명 줄."""
    lst, names, sig = sq_list(c)
    pend = {p["sym"] for p in c["pending"].values()}
    open_buys = {o["symbol"] for o in (orders(c, "OPEN") or []) if o.get("side") == "BUY"}
    cutoff = f"{now_kst().date() - dt.timedelta(days=90):%Y-%m-%d}"
    cands = [s for s in lst if s not in hs and s not in pend and s not in open_buys and c["sold"].get(s, "") < cutoff]
    group = sum(num(hs[s]["lastPrice"]) * num(hs[s]["quantity"]) for s in c["symbols"] if s in hs) + budget
    slot = group / max(1, int(c["n"]))
    free = int(c["n"]) - len([s for s in c["sq"] if s in hs]) - len(pend)
    spend = min(budget, cash)
    px = {}
    if cands:
        code, j = call("GET", "/api/v1/prices", params={"symbols": ",".join(cands[:30])})
        if code == 200:
            px = {x["symbol"]: num(x.get("lastPrice")) for x in j.get("result") or []}
    buys = []
    for s in cands:
        if free <= 0 or spend < max(MIN_ORDER, slot * 0.5):
            break
        if px.get(s, 0) <= 0:
            continue
        lim = up_tick(px[s] * (1 + float(c["buy_up"])))
        q = int(min(slot, spend) // lim)
        if q < 1:
            continue
        buys.append((s, names.get(s, s), q, lim, q * lim))
        spend -= q * lim
        free -= 1
    head = (f"Super Quality list {c['list']} (signal {sig}) | budget {budget:,.0f} | cash {cash:,.0f} | "
            f"quant group {group:,.0f} | slot {slot:,.0f} | free slots {int(c['n']) - len([s for s in c['sq'] if s in hs]) - len(pend)}")
    return buys, head, cands[:10], names


def cmd_buy(quiet=False, force=False, show=False):
    c = cfg()
    live = c["mode"] == "live"
    if not c.get("buy", True) and not show:
        if not quiet:
            print("autobuy is OFF (turn on: hq toss autobuy on)")
        return
    if not show and not force and not market_open_today():
        if not quiet:
            print("market closed today -> nothing to do")
        return
    acct = account(c)
    hs = {h["symbol"]: h for h in holdings(c)}
    notes = []
    if live and not show:
        notes += settle_buys(c) + collect(c, hs)
        save(c)
    code, j = call("GET", "/api/v1/buying-power", params={"currency": "KRW"}, acct=acct)
    cash = num((j.get("result") or {}).get("cashBuyingPower")) if code == 200 else 0.0
    if code != 200:
        notes.append(f"buying power check failed HTTP {code} -> no buying today")
    budget = num(c["budget"])
    buys, head, cands, names = plan(c, hs, budget, cash)
    if show:
        print(head)
        print("next to buy when money comes in (about one slot each):", ", ".join(f"{s} {names.get(s, s)}" for s in cands) or "(none)")
        for s, n, q, lim, amt in buys:
            print(f"  would BUY {s} {n} {q} sh, limit {lim:,} (opening auction) = {amt:,.0f} KRW")
        if not buys:
            print("  nothing to buy now (buy budget comes only from SOLD quant stocks)")
        return
    for s, n, q, lim, amt in buys:
        label = f"BUY {s} {n} {q} sh, limit {lim:,} at the open = {amt:,.0f} KRW"
        if not live:
            notes.append("would " + label)
            continue
        body = {"clientOrderId": f"{BTAG}{s}{now_kst():%y%m%d}"[:36], "symbol": s, "side": "BUY",
                "orderType": "LIMIT", "timeInForce": "OPG", "quantity": str(q), "price": str(lim)}
        code, j = call("POST", "/api/v1/orders", body=body, acct=acct)
        oid = (j.get("result") or {}).get("orderId") if code in (200, 201) else None
        if not oid:
            notes.append(f"FAILED {label}: HTTP {code} {json.dumps(j)[:200]}")
            continue
        c["pending"][oid] = {"sym": s, "name": n, "reserved": amt, "at": now_kst().isoformat(timespec="seconds")}
        c["budget"] = round(num(c["budget"]) - amt)
        if s not in c["symbols"]:
            c["symbols"] = sorted(set(c["symbols"]) | {s})   # 체결되면 15:45에 +20% 익절 주문이 붙음
        notes.append("sent " + label)
        save(c)
        time.sleep(0.3)
    if live:
        save(c)
    head2 = "LIVE" if live else "PAPER (nothing sent)"
    if notes:
        msg = "\n".join(notes) + f"\nbudget left {num(c['budget']):,.0f} KRW"
        log(f"[{head2} buy]\n{msg}")
        notify(f"토스 자동 매수 {'실행' if live else '모의'}", msg)
    elif not quiet:
        print(f"[{head2}] {head}\nnothing to buy")


def main():
    a = sys.argv[1:] or ["check"]
    c = cfg()
    if a[0] == "key":
        cmd_key()
    elif a[0] == "check":
        cmd_check()
    elif a[0] == "pick":
        cmd_pick()
    elif a[0] == "list":
        print("quant stocks:", " ".join(c["symbols"]) or "(none)", "| mode:", c["mode"], "| tp:", c["tp"])
    elif a[0] == "tp":
        cmd_tp(quiet="--quiet" in a)
    elif a[0] in ("live", "paper"):
        c["mode"] = a[0]
        save(c)
        print("mode ->", a[0].upper(), "(live = real orders)" if a[0] == "live" else "(prints only)")
    elif a[0] == "buy":
        cmd_buy(quiet="--quiet" in a, force="--force" in a)
    elif a[0] == "plan":
        cmd_buy(show=True)
    elif a[0] == "autobuy" and len(a) > 1 and a[1] in ("on", "off"):
        c["buy"] = a[1] == "on"
        save(c)
        print("autobuy ->", a[1].upper())
    elif a[0] == "refill" and len(a) > 1 and a[1] in ("on", "off"):
        c["refill"] = a[1] == "on"
        save(c)
        print("refill ->", a[1].upper(), "(Super Quality take-profit money", "buys the next stock right away)" if a[1] == "on" else "waits for the next quarterly rebalance)")
    elif a[0] == "log":
        os.system(f"tail -n 40 {LOG}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

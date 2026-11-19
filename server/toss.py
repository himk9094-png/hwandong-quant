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
    c = {"mode": "paper", "tp": 0.20, "symbols": [], "expire_days": 180, "account": None}
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
    for i, h in enumerate(hs, 1):
        mark = "Q" if h["symbol"] in c["symbols"] else " "
        print(f" {i:2d} [{mark}] {h['symbol']}  qty {h['quantity']}")
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
    for sym in c["symbols"]:
        h = hs.get(sym)
        mine = [o for o in sells.get(sym, []) if str(o.get("clientOrderId") or "").startswith(TAG)]
        other = [o for o in sells.get(sym, []) if o not in mine]
        if not h or float(h["quantity"]) <= 0:
            for o in mine:                         # no longer held -> remove our leftover order
                act(live, "DELETE", f"/api/v1/conditional-orders/{o['conditionalOrderId']}", None, acct,
                    f"cancel {sym} (not held)", done)
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
    elif a[0] == "log":
        os.system(f"tail -n 40 {LOG}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

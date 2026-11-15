#!/usr/bin/env bash
# UT봇 설치·업데이트 — Ubuntu (오라클 무료 서버)
#   bash install.sh
# 다시 실행하면 봇 코드만 새로 깔고 설정·API 키·상태는 그대로 둡니다.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then exec sudo bash "$0" "$@"; fi

D=/opt/utbot
U=utbot
say() { printf '\n\033[1;36m%s\033[0m\n' "$*"; }

say "1/5 서버 준비 (파이썬, 시간대, 전용 계정)"
command -v python3 >/dev/null 2>&1 || { apt-get update -y && apt-get install -y python3; }
python3 -c 'import sys; assert sys.version_info >= (3, 8)' || { echo "python3 3.8 이상이 필요합니다"; exit 1; }
timedatectl set-timezone Asia/Seoul 2>/dev/null || true
timedatectl set-ntp true 2>/dev/null || true
# 메모리가 적은 서버(512MB 등)는 1GB 스왑을 만들어 둔다
if [ "$(awk '/MemTotal/{print $2}' /proc/meminfo)" -lt 1500000 ] && [ -z "$(swapon --noheadings 2>/dev/null)" ] && [ ! -f /swapfile ]; then
  if { fallocate -l 1G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none; } \
     && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile; then
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw,nofail 0 0' >> /etc/fstab
    echo "스왑 1GB 만듦"
  else
    rm -f /swapfile; echo "스왑 만들기 실패(무시하고 계속)"
  fi
fi
id -u "$U" >/dev/null 2>&1 || useradd --system --home-dir "$D" --shell /usr/sbin/nologin "$U"
mkdir -p "$D"

say "2/5 봇 설치 → $D"
cat > "$D/bot.py" <<'__UTBOT_PY__'
#!/usr/bin/env python3
"""UT Bot 현물 포트폴리오 봇 (바이낸스 현물, 롱만, 레버리지 없음)

- 4시간봉이 마감될 때마다 실행(systemd 타이머). 코인마다 UT Bot 상태(보유/관망)를 계산해 계좌를 맞춘다.
  보유 신호인데 안 갖고 있으면 → 시장가 매수(계좌 평가액 ÷ 코인 수).
  관망 신호인데 갖고 있으면 → 비상 손절 주문 취소 후 시장가 매도.
- 보유 중에는 바이낸스에 비상 손절(STOP_LOSS) 주문을 걸어 둔다: UT선보다 disaster_stop_pct% 아래.
  서버가 꺼져도 폭락은 막고, 평소 청산은 4시간봉 종가 신호로만 한다(백테스트와 같은 방식).
  비상 손절이 나가면 새 매수 신호가 나올 때까지 다시 사지 않는다.
- mode "paper"면 실제 주문 없이 모의 계좌로 똑같이 돌린다.
표준 라이브러리만 사용.

  python3 bot.py run        # 4시간마다 (타이머)
  python3 bot.py heartbeat  # 하루 한 번 상태 알림
  python3 bot.py check      # API 키·권한·IP 점검
  python3 bot.py status     # 현재 상태 출력
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import socket
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_DOWN, Decimal

# 서버에 IPv6가 있어도 항상 IPv4로 접속: 바이낸스 키의 IP 제한(고정 IPv4)과 맞추기 위해
_getaddrinfo = socket.getaddrinfo


def _v4_first(host, *a, **k):
    res = _getaddrinfo(host, *a, **k)
    v4 = [r for r in res if r[0] == socket.AF_INET]
    return v4 or res


socket.getaddrinfo = _v4_first

HOME = os.environ.get("UTBOT_HOME", os.path.dirname(os.path.abspath(__file__)))
CFG_PATH = os.path.join(HOME, "config.json")
STATE_PATH = os.path.join(HOME, "state.json")
ENV_PATH = os.path.join(HOME, ".env")
LOG_PATH = os.path.join(HOME, "bot.log")
API = "https://api.binance.com"
DATA = "https://data-api.binance.vision"
TAG = "utb"                     # 이 봇이 낸 주문 표시 (clientOrderId 앞부분)
FEE = 0.001                     # 모의 계좌용 현물 수수료
DUST = 1.0                      # 이 금액(USDT) 미만 잔고는 '없음'으로 봄


# ---------------------------------------------------------------- 공통
def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if os.path.getsize(LOG_PATH) > 2_000_000:
            os.replace(LOG_PATH, LOG_PATH + ".1")
    except OSError:
        pass


def load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_env() -> dict:
    env = {}
    try:
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def notify(cfg: dict, title: str, msg: str, prio: int = 3, tags=None) -> None:
    topic = cfg.get("ntfy_topic")
    if not topic:
        return
    body = json.dumps({"topic": topic, "title": title, "message": msg, "priority": prio, "tags": tags or []}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request("https://ntfy.sh/", data=body, headers={"Content-Type": "application/json"}), timeout=15)
    except Exception as e:  # 알림 실패로 매매가 멈추면 안 됨
        log(f"ntfy 실패: {e}")


def http_json(url: str, method: str = "GET", headers=None, timeout: int = 20):
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {url.split('?')[0]}: {e.read().decode(errors='replace')[:300]}") from None


HINTS = {
    "-2015": "API 키·IP 제한·권한 문제 — 키의 IP 제한에 서버 IP(utbot check)가 있는지, '현물 거래' 권한이 켜졌는지 보세요",
    "-2014": "API 키 형식이 틀림 — utbot key 로 다시 넣으세요",
    "-1022": "서명 오류 — API SECRET을 다시 넣으세요(utbot key)",
    "-1021": "서버 시계 차이 — 'sudo timedatectl set-ntp true' 후 다시",
    "-2010": "잔고 부족 또는 주문 거절 — 현물 지갑 USDT, 직접 걸어 둔 주문이 있는지 보세요",
    "HTTP 451": "바이낸스가 이 서버 지역을 막음 — 서버 지역을 한국·일본·싱가포르로",
    "HTTP 418": "요청이 너무 많아 IP 일시 차단 — 잠시 뒤 저절로 풀림",
    "HTTP 429": "요청이 너무 많음 — 잠시 뒤 저절로 풀림",
}


def hint(e) -> str:
    t = str(e)
    for k, v in HINTS.items():
        if k in t:
            return f"{t[:200]}\n→ {v}"
    return t[:300]


def fmt_dec(x: float, step: str) -> str:
    """step 단위로 내림한 문자열 (바이낸스 수량·가격 형식)"""
    st = Decimal(step).normalize()
    d = (Decimal(repr(float(x))) / st).to_integral_value(rounding=ROUND_DOWN) * st
    return format(d.normalize(), "f")


# ---------------------------------------------------------------- UT Bot
def ut_bot(o, h, l, c, key: float, per: int, ha: bool):
    """TradingView 'UT Bot Alerts'와 같은 계산. 반환: 신호(+1 매수, -1 매도, 0) 목록, UT선 목록"""
    n = len(c)
    src = [(o[i] + h[i] + l[i] + c[i]) / 4 for i in range(n)] if ha else list(c)
    atr = [math.nan] * n
    s = 0.0
    for i in range(n):
        tr = h[i] - l[i] if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        if i < per:
            s += tr
            if i == per - 1:
                atr[i] = s / per
        else:
            atr[i] = (atr[i - 1] * (per - 1) + tr) / per
    stop = [0.0] * n
    sig = [0] * n
    for i in range(n):
        p = stop[i - 1] if i else 0.0
        if math.isnan(atr[i]):
            continue
        nl = key * atr[i]
        s0 = src[i]
        s1 = src[i - 1] if i else s0
        if s0 > p and s1 > p:
            t = max(p, s0 - nl)
        elif s0 < p and s1 < p:
            t = min(p, s0 + nl)
        elif s0 > p:
            t = s0 - nl
        else:
            t = s0 + nl
        stop[i] = t
        if i and not math.isnan(atr[i - 1]):
            if s0 > t and s1 <= p:
                sig[i] = 1
            elif s0 < t and s1 >= p:
                sig[i] = -1
    return sig, stop


def klines(sym: str, interval: str, limit: int = 1000):
    rows = http_json(f"{DATA}/api/v3/klines?symbol={sym}&interval={interval}&limit={limit}")
    now = time.time() * 1000
    return [r for r in rows if r[6] < now]          # 마감된 봉만


def coin_signal(sym: str, p: dict, interval: str) -> dict:
    rows = klines(sym, interval)
    if len(rows) < 100:
        raise RuntimeError(f"{sym} 봉 데이터 부족({len(rows)})")
    o, h, l, c = ([float(r[k]) for r in rows] for k in (1, 2, 3, 4))
    sig, line = ut_bot(o, h, l, c, float(p["key"]), int(p["atr"]), bool(p.get("ha", False)))
    want, last_buy = False, 0
    for i, s in enumerate(sig):
        if s == 1:
            want, last_buy = True, int(rows[i][6]) + 1      # 신호 봉 마감 시각(ms)
        elif s == -1:
            want = False
    return {"want": want, "last_buy": last_buy, "line": line[-1], "close": c[-1], "bar_close": int(rows[-1][6]) + 1,
            "lows": [(int(r[6]), float(r[3])) for r in rows[-60:]]}   # (봉 마감 ms, 저가)


# ---------------------------------------------------------------- 거래소
class Live:
    """바이낸스 현물 실계좌"""

    def __init__(self, key: str, secret: str):
        self.key, self.secret = key, secret.encode()
        self.offset = 0
        self._filters = {}

    def sign(self, q: str) -> str:
        return hmac.new(self.secret, q.encode(), hashlib.sha256).hexdigest()

    def _req(self, method: str, path: str, params: dict | None = None, signed: bool = False):
        params = dict(params or {})
        if signed:
            params["recvWindow"] = 10000
            params["timestamp"] = int(time.time() * 1000) + self.offset
            q = urllib.parse.urlencode(params)
            q += "&signature=" + self.sign(q)
        else:
            q = urllib.parse.urlencode(params)
        url = f"{API}{path}" + (f"?{q}" if q else "")
        return http_json(url, method, {"X-MBX-APIKEY": self.key})

    def sync_time(self):
        t = self._req("GET", "/api/v3/time")["serverTime"]
        self.offset = int(t - time.time() * 1000)

    def filters(self, sym: str) -> dict:
        if sym not in self._filters:
            s = self._req("GET", "/api/v3/exchangeInfo", {"symbol": sym})["symbols"][0]
            f = {x["filterType"]: x for x in s["filters"]}
            mn = f.get("NOTIONAL", f.get("MIN_NOTIONAL", {}))
            self._filters[sym] = {"step": f["LOT_SIZE"]["stepSize"], "minqty": float(f["LOT_SIZE"]["minQty"]),
                                  "tick": f["PRICE_FILTER"]["tickSize"], "minnot": float(mn.get("minNotional", 5))}
        return self._filters[sym]

    def account(self):
        a = self._req("GET", "/api/v3/account", {"omitZeroBalances": "true"}, signed=True)
        return a, {b["asset"]: (float(b["free"]), float(b["locked"])) for b in a["balances"]}

    def balances(self):
        return self.account()[1]

    def price(self, sym: str) -> float:
        return float(self._req("GET", "/api/v3/ticker/price", {"symbol": sym})["price"])

    def my_stops(self, sym: str):
        return [o for o in self._req("GET", "/api/v3/openOrders", {"symbol": sym}, signed=True)
                if o.get("clientOrderId", "").startswith(TAG) and o["type"] in ("STOP_LOSS", "STOP_LOSS_LIMIT")]

    def cancel(self, sym: str, oid: int):
        self._req("DELETE", "/api/v3/order", {"symbol": sym, "orderId": oid}, signed=True)

    def market_buy(self, sym: str, usdt: float):
        return self._req("POST", "/api/v3/order", {"symbol": sym, "side": "BUY", "type": "MARKET", "quoteOrderQty": fmt_dec(usdt, "0.01"),
                                                   "newClientOrderId": f"{TAG}_b_{sym}_{int(time.time() * 1000)}"}, signed=True)

    def market_sell(self, sym: str, qty: float):
        return self._req("POST", "/api/v3/order", {"symbol": sym, "side": "SELL", "type": "MARKET", "quantity": fmt_dec(qty, self.filters(sym)["step"]),
                                                   "newClientOrderId": f"{TAG}_s_{sym}_{int(time.time() * 1000)}"}, signed=True)

    def place_stop(self, sym: str, qty: float, price: float):
        f = self.filters(sym)
        return self._req("POST", "/api/v3/order", {"symbol": sym, "side": "SELL", "type": "STOP_LOSS", "quantity": fmt_dec(qty, f["step"]),
                                                   "stopPrice": fmt_dec(price, f["tick"]), "newClientOrderId": f"{TAG}_x_{sym}_{int(time.time() * 1000)}"}, signed=True)


class Paper:
    """모의 계좌: 실제 시세로 같은 결정을 내리고 체결을 흉내 낸다 (수수료 0.1%, 비상 손절은 4시간봉 저가로 판정)"""

    def __init__(self, st: dict, start_usdt: float):
        self.p = st.setdefault("paper", {"USDT": float(start_usdt), "qty": {}, "stops": {}})
        self.offset = 0

    def sync_time(self):
        pass

    def filters(self, sym):
        return {"step": "0.00001", "minqty": 0.0, "tick": "0.01", "minnot": 5.0}

    def balances(self):
        b = {"USDT": (self.p["USDT"], 0.0)}
        for sym, q in self.p["qty"].items():
            locked = q if sym in self.p["stops"] else 0.0
            b[sym[:-4]] = (q - locked, locked)
        return b

    def price(self, sym):
        return float(http_json(f"{DATA}/api/v3/ticker/price?symbol={sym}")["price"])

    def check_stops(self, sym: str, lows):
        s = self.p["stops"].get(sym)
        if not s:
            return
        hit = [lo for t, lo in lows if t > s["since"] and lo <= s["price"]]     # 손절을 건 뒤에 끝난 봉
        if hit:
            q = self.p["qty"].pop(sym, 0.0)
            self.p["USDT"] += q * s["price"] * (1 - FEE)
            del self.p["stops"][sym]

    def my_stops(self, sym):
        s = self.p["stops"].get(sym)
        return [{"orderId": 1, "stopPrice": str(s["price"]), "origQty": str(s["qty"])}] if s else []

    def cancel(self, sym, oid):
        self.p["stops"].pop(sym, None)

    def market_buy(self, sym, usdt):
        px = self.price(sym)
        self.p["USDT"] -= usdt
        self.p["qty"][sym] = self.p["qty"].get(sym, 0.0) + usdt / px * (1 - FEE)
        return {"price": px}

    def market_sell(self, sym, qty):
        px = self.price(sym)
        self.p["qty"][sym] = max(0.0, self.p["qty"].get(sym, 0.0) - qty)
        if self.p["qty"][sym] < 1e-12:
            self.p["qty"].pop(sym)
        self.p["USDT"] += qty * px * (1 - FEE)
        return {"price": px}

    def place_stop(self, sym, qty, price):
        self.p["stops"][sym] = {"price": price, "qty": qty, "since": int(time.time() * 1000)}


# ---------------------------------------------------------------- 본체
def make_ex(cfg, st):
    if cfg.get("mode") == "live":
        env = load_env()
        if not env.get("BINANCE_API_KEY") or not env.get("BINANCE_API_SECRET"):
            raise RuntimeError(".env에 BINANCE_API_KEY / BINANCE_API_SECRET이 없습니다")
        return Live(env["BINANCE_API_KEY"], env["BINANCE_API_SECRET"])
    return Paper(st, cfg.get("paper_start_usdt", 100))


def _coin(c, p, s, cs, ex, cfg, quote, n, pct, min_order, px, held, equity, started, notes, slots):
    sym = c + quote

    def warn(key, text):                                  # 같은 경고는 한 번만 알림
        if me.get("warned") != key:
            notes.append(text)
            me["warned"] = key
    me = cs.setdefault(c, {"holding": False, "stopped_at": 0})
    holding = held[c] * px[c] >= DUST
    if me.get("holding") and not holding and s["want"]:
        me["stopped_at"] = int(time.time() * 1000)          # 비상 손절(또는 직접 매도) — 새 매수 신호까지 대기
        notes.append(f"🛑 {c} 비상 손절 체결(또는 직접 매도) — 새 매수 신호까지 대기")
    if holding and not s["want"]:
        for o in ex.my_stops(sym):
            ex.cancel(sym, o["orderId"])
        free = ex.balances().get(c, (0.0, 0.0))[0]
        minnot = ex.filters(sym)["minnot"]
        if free * px[c] < minnot:
            warn("tiny", f"⚠️ {c} 매도 신호지만 남은 양이 {minnot} {quote} 미만이라 거래소가 시장가 매도를 받지 않음 — 그대로 둠")
        else:
            ex.market_sell(sym, free)
            slots["free"] += 1
            me.pop("warned", None)
            notes.append(f"🔴 {c} 매도 {free:.6g}개 (≈{free * px[c]:,.2f} {quote}) — UT 매도 신호")
        holding = False
    elif not holding and s["want"]:
        if me.get("stopped_at") and s["last_buy"] <= me["stopped_at"]:
            pass                                              # 손절 뒤 새 매수 신호 대기
        elif not cfg.get("enter_on_start", True) and s["last_buy"] < started:
            pass                                              # 봇 시작 전 신호는 건너뜀(설정)
        else:
            usdt_free = ex.balances().get(quote, (0.0, 0.0))[0]
            amt = min(equity / n, usdt_free / max(1, slots["free"]), usdt_free * 0.995)   # 현금은 안 산 코인끼리 똑같이 나눔
            if amt < min_order:
                warn(f"cash{s['last_buy']}", f"⚠️ {c} 매수 신호지만 금액 부족({amt:.2f} {quote} < {min_order}) — 건너뜀")
            else:
                ex.market_buy(sym, amt)
                holding = True
                slots["free"] -= 1
                me["stopped_at"] = 0
                me.pop("warned", None)
                notes.append(f"🟢 {c} 매수 ≈{amt:,.2f} {quote} @ {px[c]:,.6g} — UT 매수 신호")
    if holding:
        want_px = s["line"] * (1 - pct)
        b = ex.balances().get(c, (0.0, 0.0))
        qty = b[0] + b[1]
        f = ex.filters(sym)
        stops = ex.my_stops(sym)
        qmax = float(fmt_dec(qty, f["step"]))                 # 주문 단위로 내림한 전체 수량
        ok = len(stops) == 1 and abs(float(stops[0]["stopPrice"]) / want_px - 1) < 0.002 and float(stops[0]["origQty"]) >= qmax * 0.999
        if not ok:
            for o in stops:
                ex.cancel(sym, o["orderId"])
            q = ex.balances().get(c, (0.0, 0.0))[0]
            if want_px >= px[c]:
                warn(f"above{s['last_buy']}", f"⚠️ {c} 비상 손절가({want_px:,.6g})가 현재가 이상 — 주문 안 함")
            elif q * want_px < f["minnot"] or q < f["minqty"]:
                warn(f"small{s['last_buy']}", f"⚠️ {c} 보유가 작아 비상 손절 주문 불가(최소 {f['minnot']} {quote})")
            else:
                ex.place_stop(sym, q, want_px)
        me["stop_px"] = want_px
    me["holding"] = holding
    me["want"] = s["want"]
    me["line"] = s["line"]


def run(cfg: dict, st: dict, ex):
    """한 번 점검·매매. 알림 문구 목록을 돌려준다."""
    quote = cfg.get("quote", "USDT")
    coins = cfg["coins"]
    n = len(coins)
    pct = float(cfg.get("disaster_stop_pct", 10)) / 100
    min_order = float(cfg.get("min_order_usdt", 5.5))
    ex.sync_time()
    sigs, errs = {}, []
    for c, p in coins.items():
        try:
            sigs[c] = coin_signal(c + quote, p, cfg.get("interval", "4h"))
        except Exception as e:
            errs.append(f"{c} 신호 계산: {e}")
    if isinstance(ex, Paper):
        for c in sigs:
            ex.check_stops(c + quote, sigs[c]["lows"])
    bal = ex.balances()
    px = {c: ex.price(c + quote) for c in coins}
    held = {c: sum(bal.get(c, (0.0, 0.0))) for c in coins}
    equity = sum(bal.get(quote, (0.0, 0.0))) + sum(held[c] * px[c] for c in coins)
    cs = st.setdefault("coins", {})
    started = st.setdefault("started_at", int(time.time() * 1000))
    notes = []
    slots = {"free": sum(1 for c in coins if held[c] * px[c] < DUST)}     # 지금 안 들고 있는 코인 수
    for c, p in coins.items():
        if c not in sigs:
            continue
        try:
            _coin(c, p, sigs[c], cs, ex, cfg, quote, n, pct, min_order, px, held, equity, started, notes, slots)
        except Exception as e:                                   # 한 코인 오류가 다른 코인 매매를 막지 않게
            log(f"{c} 오류: " + "".join(traceback.format_exception(type(e), e, e.__traceback__)).strip())
            errs.append(f"{c}: {e}")
    st["last_run"] = int(time.time())
    st["equity"] = equity
    return notes, errs


def summary(cfg, st, ex) -> str:
    quote = cfg.get("quote", "USDT")
    bal = ex.balances()
    lines, eq = [], sum(bal.get(quote, (0.0, 0.0)))
    for c in cfg["coins"]:
        q = sum(bal.get(c, (0.0, 0.0)))
        v = q * ex.price(c + quote)
        eq += v
        me = st.get("coins", {}).get(c, {})
        state = "보유" if v >= DUST else ("대기(손절 후)" if me.get("want") and me.get("stopped_at") else "관망")
        extra = f" · 비상손절 {me['stop_px']:,.6g}" if v >= DUST and me.get("stop_px") else ""
        lines.append(f"{c} {state} {v:,.2f}{extra}")
    last = time.strftime("%m/%d %H:%M", time.localtime(st.get("last_run", 0))) if st.get("last_run") else "없음"
    mode = "모의" if cfg.get("mode") != "live" else "실거래"
    return f"[{mode}] 평가액 {eq:,.2f} {quote} · 마지막 점검 {last}\n" + "\n".join(lines)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    cfg = load_json(CFG_PATH, None)
    if not cfg:
        print(f"설정 파일이 없습니다: {CFG_PATH}")
        sys.exit(1)
    if cmd in ("run", "heartbeat"):                    # 동시 실행 방지
        import fcntl
        lockf = open(os.path.join(HOME, ".lock"), "w")
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("이미 실행 중입니다.")
            sys.exit(0)
    st = load_json(STATE_PATH, {})
    mode = "모의" if cfg.get("mode") != "live" else "실거래"
    try:
        ex = make_ex(cfg, st)
        if cmd == "run":
            notes, errs = run(cfg, st, ex)
            save_json(STATE_PATH, st)
            for nline in notes:
                log(nline)
            if errs:
                st["errors"] = st.get("errors", 0) + 1
                save_json(STATE_PATH, st)
                if st["errors"] in (1, 3) or st["errors"] % 6 == 0:      # 연속 오류는 가끔만 알림
                    notes = notes + [f"❌ {hint(x)}" for x in errs] + [f"(연속 {st['errors']}회 · 나머지 코인은 정상 처리)"]
            else:
                st.pop("errors", None)
                save_json(STATE_PATH, st)
            if notes:
                urgent = any(x.startswith(("🛑", "❌")) for x in notes)
                notify(cfg, f"UT봇 [{mode}]", "\n".join(notes) + "\n\n" + summary(cfg, st, ex), 4 if urgent else 3)
            else:
                log("변화 없음")
            if errs:
                sys.exit(1)
        elif cmd == "heartbeat":
            behind = time.time() - st.get("last_run", 0) > 5 * 3600
            msg = summary(cfg, st, ex) + ("\n⚠️ 5시간 넘게 점검이 안 돌았습니다 — 서버를 확인하세요" if behind else "")
            notify(cfg, f"UT봇 하루 점검 [{mode}]", msg, 4 if behind else 2, ["white_check_mark"] if not behind else ["warning"])
            log("하루 점검 알림 보냄")
        elif cmd == "status":
            print(summary(cfg, st, ex))
        elif cmd == "check":
            check(cfg)
        else:
            print(__doc__)
    except Exception as e:
        log("오류: " + "".join(traceback.format_exception_only(type(e), e)).strip())
        st["errors"] = st.get("errors", 0) + 1
        save_json(STATE_PATH, st)
        if st["errors"] in (1, 3) or st["errors"] % 6 == 0:          # 연속 오류는 가끔만 알림
            notify(cfg, f"UT봇 오류 [{mode}]", f"{hint(e)}\n(연속 {st['errors']}회)", 4, ["warning"])
        sys.exit(1)


def check(cfg):
    env = load_env()
    try:
        ip = urllib.request.urlopen("https://api.ipify.org", timeout=10).read().decode()
    except Exception:
        ip = "확인 실패"
    print(f"이 서버의 공인 IP: {ip}  ← 바이낸스 API 키의 '접속 IP 제한'에 이 주소를 넣으세요")
    try:
        http_json(f"{API}/api/v3/time")
        print("  바이낸스 API 접속: OK (이 지역에서 거래 가능)")
    except Exception as e:
        print(f"  ⚠️ 바이낸스 API 접속 실패: {hint(e)}")
    for c, p in cfg["coins"].items():
        s = coin_signal(c + cfg.get("quote", "USDT"), p, cfg.get("interval", "4h"))
        print(f"  {c}: 지금 {'보유' if s['want'] else '관망'} 신호 · UT선 {s['line']:,.6g} · 종가 {s['close']:,.6g}")
    if not env.get("BINANCE_API_KEY"):
        print("API 키가 아직 없습니다(.env). 모의 모드는 키 없이 돌아갑니다.")
    else:
        ex = Live(env["BINANCE_API_KEY"], env.get("BINANCE_API_SECRET", ""))
        ex.sync_time()
        a, bal = ex.account()
        perms = a.get("permissions", [])
        print(f"  키 확인 완료 · 현물 거래 가능: {'예' if a.get('canTrade') else '아니오'} · 권한 {perms}")
        print(f"  USDT {sum(bal.get('USDT', (0, 0))):,.2f} · 시계 차이 {ex.offset} ms")
        if not a.get("canTrade"):
            print("  ⚠️ 거래 권한이 없습니다. 키 설정에서 'Enable Spot & Margin Trading'을 켜고 IP 제한을 거세요.")
            notify(cfg, "UT봇 연결 시험", "⚠️ 키는 맞지만 현물 거래 권한이 없습니다", 4)
            sys.exit(2)
    notify(cfg, "UT봇 연결 시험", "알림이 잘 옵니다 ✅", 3)
    print("ntfy 시험 알림을 보냈습니다." if cfg.get("ntfy_topic") else "ntfy_topic이 비어 있어 알림은 꺼져 있습니다.")


if __name__ == "__main__":
    main()
__UTBOT_PY__
if [ ! -f "$D/config.json" ]; then
cat > "$D/config.json" <<'__UTBOT_CFG__'
{
  "mode": "paper",
  "paper_start_usdt": 100,
  "quote": "USDT",
  "interval": "4h",
  "coins": {
    "BTC": {"key": 7, "atr": 20, "ha": false},
    "ETH": {"key": 7, "atr": 20, "ha": false},
    "BNB": {"key": 7, "atr": 20, "ha": false}
  },
  "disaster_stop_pct": 10,
  "min_order_usdt": 5.5,
  "enter_on_start": true,
  "ntfy_topic": ""
}
__UTBOT_CFG__
  echo "기본 설정 만듦 (모의 모드, BTC·ETH·BNB, 4시간봉, Key 7 / ATR 20)"
else
  echo "기존 설정 유지: $D/config.json"
fi

cat > /usr/local/bin/utbot <<'__UTBOT_CLI__'
#!/usr/bin/env bash
# utbot — UT봇 관리 명령
D=/opt/utbot
if [ "$(id -u)" -ne 0 ]; then exec sudo --preserve-env=UTBOT_NOCHECK "$0" "$@"; fi
py() { sudo -u utbot env UTBOT_HOME="$D" /usr/bin/python3 "$D/bot.py" "$@"; }
cfgset() {  # cfgset 키 값(JSON)
  python3 - "$D/config.json" "$1" "$2" <<'__PY__'
import json, sys
p, k, v = sys.argv[1:]
c = json.load(open(p, encoding="utf-8")); c[k] = json.loads(v)
json.dump(c, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
__PY__
  chown utbot:utbot "$D/config.json"
}
mode() { python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('mode'))" "$D/config.json"; }
stash() {  # 모드가 바뀌면 상태 파일을 따로 보관하고 새로 시작
  [ -f "$D/state.json" ] && mv "$D/state.json" "$D/state.$1-$(date +%Y%m%d-%H%M).json"
  return 0
}

case "${1:-help}" in
  status) py status ;;
  run)    systemctl start utbot.service; tail -n "${2:-8}" "$D/bot.log" 2>/dev/null ;;
  log)    tail -n "${2:-50}" "$D/bot.log" 2>/dev/null || echo "아직 기록 없음" ;;
  check)  py check ;;
  next)   systemctl list-timers 'utbot*' --no-pager ;;
  key)
    echo "※ 민감한 정보입니다. 입력이 화면에 안 보입니다. 채팅·메신저에 붙여넣지 마세요."
    echo "  바이낸스 API 관리 → 키 만들기(System generated) → 'Enable Reading' + 'Enable Spot & Margin Trading'만 켜고"
    echo "  출금(Withdrawals)·선물은 끄고, 'Restrict access to trusted IPs only'에 이 서버 IP를 넣으세요."
    echo "  (지금 없으면 그냥 엔터 — 모의 모드는 키 없이 돌아갑니다)"
    read -rsp "API Key: " K </dev/tty; echo
    if [ -z "$K" ]; then echo "건너뜀"; exit 0; fi
    read -rsp "Secret Key: " S </dev/tty; echo
    [ -n "$S" ] || { echo "Secret이 비었습니다. 다시: utbot key"; exit 1; }
    ( umask 077; printf 'BINANCE_API_KEY=%s\nBINANCE_API_SECRET=%s\n' "$K" "$S" > "$D/.env" )
    unset K S
    chown utbot:utbot "$D/.env"; chmod 600 "$D/.env"
    echo "저장함"
    [ -n "${UTBOT_NOCHECK:-}" ] || py check ;;
  live)
    [ -s "$D/.env" ] || { echo "API 키가 없습니다. 먼저: utbot key"; exit 1; }
    [ "$(mode)" = "live" ] && { echo "이미 실거래 모드입니다."; exit 0; }
    echo "실거래로 바꿉니다. 바이낸스 '현물 지갑'의 USDT로 실제 주문이 나갑니다."
    echo " - 현물 지갑에 BTC·ETH·BNB를 따로 들고 있으면 봇이 자기 것으로 보고 사고팔 수 있습니다 (봇 전용으로 쓰세요)."
    echo " - 선물 지갑 USDT는 쓰지 않습니다. 필요하면 먼저 현물 지갑으로 옮기세요."
    read -rp "계속하려면 '실거래' 라고 입력: " A </dev/tty
    [ "$A" = "실거래" ] || { echo "취소"; exit 1; }
    py check || { echo "키 점검 실패 — 바꾸지 않았습니다."; exit 1; }
    stash paper; cfgset mode '"live"'
    echo "실거래 모드로 바꿨습니다. 지금 한 번 점검합니다."
    systemctl start utbot.service; tail -n 8 "$D/bot.log"; py status ;;
  paper)
    [ "$(mode)" = "live" ] || { echo "이미 모의 모드입니다."; exit 0; }
    stash live; cfgset mode '"paper"'
    echo "모의 모드로 바꿨습니다. 바이낸스에 남은 코인과 비상 손절 주문은 그대로 있습니다 — 필요하면 직접 정리하세요." ;;
  stop)
    systemctl disable --now utbot.timer utbot-heartbeat.timer
    echo "자동 실행을 멈췄습니다. 바이낸스에 걸린 비상 손절 주문은 남아 있습니다. 다시 켜기: utbot start" ;;
  start)
    systemctl enable --now utbot.timer utbot-heartbeat.timer; echo "자동 실행 켬"; systemctl list-timers 'utbot*' --no-pager | head -3 ;;
  config)
    ${EDITOR:-nano} "$D/config.json"
    python3 -m json.tool "$D/config.json" >/dev/null && echo "설정 OK" || echo "⚠️ 설정 파일 형식(JSON) 오류 — 다시 고치세요" ;;
  *)
    cat <<'__H__'
utbot status   지금 상태 (평가액·코인별 보유/관망·비상손절가)
utbot log [N]  최근 기록 N줄
utbot run      지금 한 번 점검 (평소엔 4시간마다 저절로)
utbot check    서버 IP·지금 신호·API 키·알림 점검
utbot key      API 키 넣기/바꾸기
utbot live     실거래로 전환      utbot paper   모의로 전환
utbot stop     자동 실행 멈춤     utbot start   다시 켬
utbot next     다음 실행 시각     utbot config  설정 파일 고치기
__H__
    ;;
esac
__UTBOT_CLI__
chmod 755 /usr/local/bin/utbot

say "3/5 자동 실행 등록 (4시간봉 마감 1분 뒤 + 매일 아침 8:10 상태 알림)"
cat > /etc/systemd/system/utbot.service <<__UNIT__
[Unit]
Description=UT bot 4h check
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=$U
Environment=UTBOT_HOME=$D
ExecStart=/usr/bin/python3 $D/bot.py run
TimeoutStartSec=600
__UNIT__
cat > /etc/systemd/system/utbot.timer <<'__UNIT__'
[Unit]
Description=UT bot every 4h candle close (UTC 00,04,08,12,16,20 +1min)

[Timer]
OnCalendar=*-*-* 00/4:01:00 UTC
Persistent=true
AccuracySec=10s

[Install]
WantedBy=timers.target
__UNIT__
cat > /etc/systemd/system/utbot-heartbeat.service <<__UNIT__
[Unit]
Description=UT bot daily status
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=$U
Environment=UTBOT_HOME=$D
ExecStart=/usr/bin/python3 $D/bot.py heartbeat
TimeoutStartSec=300
__UNIT__
cat > /etc/systemd/system/utbot-heartbeat.timer <<'__UNIT__'
[Unit]
Description=UT bot daily status 08:10 KST

[Timer]
OnCalendar=*-*-* 23:10:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
__UNIT__

say "4/5 알림(ntfy)과 API 키"
CUR=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('ntfy_topic',''))" "$D/config.json")
if [ -z "$CUR" ]; then
  GEN="utbot-$(python3 -c 'import secrets;print(secrets.token_hex(6))')"
  echo "휴대폰 ntfy 앱에서 구독할 토픽 이름입니다. 남이 알면 알림을 볼 수 있으니 길고 무작위가 좋습니다."
  read -rp "ntfy 토픽 (그냥 엔터 = $GEN): " T </dev/tty || T=""
  T=${T:-$GEN}
  python3 - "$D/config.json" "$T" <<'__PY__'
import json, sys
p, t = sys.argv[1:]
c = json.load(open(p, encoding="utf-8")); c["ntfy_topic"] = t
json.dump(c, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
__PY__
  echo "→ ntfy 앱에서 '$T' 구독하세요"
fi
chown -R "$U:$U" "$D"; chmod 700 "$D"
if [ ! -s "$D/.env" ]; then
  UTBOT_NOCHECK=1 utbot key || true
else
  echo "기존 API 키 유지 (바꾸려면: utbot key)"
fi

systemctl daemon-reload
systemctl enable --now utbot.timer utbot-heartbeat.timer >/dev/null

say "5/5 점검"
utbot check || true
utbot run || true
echo
systemctl list-timers 'utbot*' --no-pager | head -4
MODE=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('mode'))" "$D/config.json")
say "설치 끝 — 지금 모드: $MODE"
cat <<'__MSG__'
  utbot status   지금 상태          utbot log      최근 기록
  utbot run      지금 한 번 점검    utbot check    키·IP·알림 점검
  utbot key      API 키 넣기/바꾸기 utbot live     실거래로 전환
  utbot paper    모의로 전환        utbot stop     자동 실행 멈춤
모의 모드로 1~2주 알림을 지켜본 뒤 'utbot live' 로 바꾸세요.
__MSG__

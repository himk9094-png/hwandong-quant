"""앱 '보유' 탭에서 보낸 종목 목록(ntfy)을 config.json holdings에 반영 → 주간 공시·뉴스 점검(news)·'보유' 표시 대상.

앱은 종목코드·이름만 보낸다(수량·평단은 폰에만 저장). ntfy.sh는 메시지를 12시간만 보관하므로 hold-reg 작업이 1시간마다 확인한다.
마지막으로 반영한 메시지 시각(holdings_t)보다 새 메시지만 반영 → config.json을 손으로 고친 뒤 옛 메시지로 되돌아가지 않음.

  python -m pipeline.holdsync          # 바뀌었으면 config.json 수정 + ntfy 확인 알림
"""
from __future__ import annotations

import json
import re

import requests

from . import config, ntfy

CODE = re.compile(r"^[0-9A-Z]{6}$")
MAX = 100


def topic(cfg: dict) -> str | None:
    t = cfg.get("ntfy_topic")
    return f"{t}-hold" if t else None


def parse(lines: str) -> tuple[int, dict] | None:
    """ntfy 폴링 결과(JSON 줄들)에서 형식이 맞는 가장 최근 목록 → (시각, {코드: 이름})"""
    best = None
    for line in lines.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("event") != "message":
            continue
        try:
            body = json.loads(m.get("message") or "")
        except ValueError:
            continue
        hold = body.get("hold") if isinstance(body, dict) else None
        if not isinstance(hold, dict) or len(hold) > MAX or not all(isinstance(k, str) and CODE.match(k) for k in hold):
            continue
        t = int(m.get("time") or 0)
        if best is None or t >= best[0]:
            best = (t, {k: (str(v).strip()[:40] or k) for k, v in hold.items()})
    return best


def main() -> int:
    cfg = config.load()
    t = topic(cfg)
    if not t:
        print("config.json에 ntfy_topic이 없습니다.")
        return 0
    r = requests.get(f"https://ntfy.sh/{t}/json", params={"poll": 1, "since": "all"}, timeout=30)
    r.raise_for_status()
    got = parse(r.text)
    last = int(cfg.get("holdings_t") or 0)
    if not got or got[0] <= last:
        print("새 목록 없음")
        return 0
    when, new = got
    old = cfg.get("holdings") or {}
    added = [new[c] for c in new if c not in old]
    removed = [old[c] for c in old if c not in new]
    cfg["holdings"] = new
    cfg["holdings_t"] = when
    (config.ROOT / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"반영: {len(new)}종목 (추가 {added or '-'} / 제외 {removed or '-'})")
    if added or removed:
        msg = [f"주간 공시·뉴스 점검 대상 {len(new)}종목: " + ", ".join(new.values())]
        if added:
            msg.append("추가: " + ", ".join(added))
        if removed:
            msg.append("제외: " + ", ".join(removed))
        ntfy.send("환동퀀트: 보유 종목 반영", "\n".join(msg), tags=["white_check_mark"], priority=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

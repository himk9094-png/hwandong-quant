"""ntfy 푸시 알림 (앱에서 주제를 구독하면 폰으로 옴, 가입 불필요)."""
import sys

import requests

from . import config


def topic() -> str | None:
    import os
    return os.environ.get("NTFY_TOPIC") or config.load().get("ntfy_topic")


def send(title: str, message: str, click: str | None = None, tags=None, priority: int = 3) -> None:
    t = topic()
    if not t:
        return
    body = message.encode("utf-8")
    if len(body) > 3900:                                   # ntfy 본문 한도(4KB) 안으로 자르기
        message = body[:3880].decode("utf-8", "ignore") + "\n…"
    payload = {"topic": t, "title": title, "message": message, "priority": priority, "tags": tags or []}
    if click:
        payload["click"] = click
    try:
        r = requests.post("https://ntfy.sh/", json=payload, timeout=20)
        if r.status_code != 200:
            print(f"ntfy 전송 실패: {r.status_code} {r.text[:200]}")
    except requests.RequestException as e:
        print(f"ntfy 전송 실패: {e}")


if __name__ == "__main__":  # 워크플로 실패 시: python -m pipeline.ntfy <실행기록URL>
    url = sys.argv[1] if len(sys.argv) > 1 else None
    send("환동퀀트 실행 실패", "오늘 자동 계산이 실패했습니다. 눌러서 실행 기록을 확인하세요.", click=url, tags=["warning"], priority=4)

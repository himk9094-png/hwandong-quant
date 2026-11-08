import requests


def send(text: str, token: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    for ch in chunks:
        r = requests.post(url, json={"chat_id": chat_id, "text": ch, "disable_web_page_preview": True}, timeout=30)
        if r.status_code != 200:
            print(f"텔레그램 전송 실패: {r.status_code} {r.text[:200]}")

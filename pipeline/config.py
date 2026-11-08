import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
KST = ZoneInfo("Asia/Seoul")


def load() -> dict:
    with open(ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()

"""주 1회 보유 종목 점검: DART 공시(최근 7일) + 네이버 종목 뉴스(최근 7일) → ntfy.

  python -m pipeline.news       # DART_API_KEY 있으면 공시도 확인
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time

import requests

from . import config, ntfy

RISK = ("유상증자", "전환사채", "신주인수권", "교환사채", "불성실공시", "감사의견", "관리종목", "횡령", "배임",
        "거래정지", "상장폐지", "회생", "파산", "부도", "최대주주변경", "소송", "자본감소", "감자")
GOOD = ("수주", "공급계약", "실적", "영업이익", "흑자", "자기주식", "배당", "인수", "합병", "투자")
NEWS_KEYS = RISK + GOOD + ("목표가", "어닝", "적자", "급등", "급락", "계약")


def _get(s, url, **params):
    for i in range(3):
        try:
            r = s.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r
        except requests.RequestException:
            pass
        time.sleep(2 + i)
    return None


def main():
    cfg = config.load()
    hold = cfg.get("holdings") or {}
    if not hold:
        print("config.json에 holdings가 없습니다.")
        return
    today = config.today_kst()
    since = today - dt.timedelta(days=7)
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    key = os.environ.get("DART_API_KEY")
    cc = {}
    p = config.ROOT / "cache" / "dart" / "corp_codes.json"
    if p.exists():
        cc = json.loads(p.read_text(encoding="utf-8"))

    risk_lines, stock_lines = [], []
    for code, name in hold.items():
        items = []
        # DART 공시
        if key and code in cc:
            r = _get(s, "https://opendart.fss.or.kr/api/list.json", crtfc_key=key, corp_code=cc[code],
                     bgn_de=since.strftime("%Y%m%d"), end_de=today.strftime("%Y%m%d"), page_count=100)
            for x in ((r.json().get("list") or []) if r else []):
                title = x.get("report_nm", "").strip()
                tag = "⚠️" if any(k in title for k in RISK) else "📄"
                if tag == "⚠️":
                    risk_lines.append(f"⚠️ {name}: {title} ({x.get('rcept_dt', '')[4:6]}/{x.get('rcept_dt', '')[6:]})")
                items.append(f"  {tag} 공시 {x.get('rcept_dt', '')[4:6]}/{x.get('rcept_dt', '')[6:]} {title}")
        # 네이버 종목 뉴스
        r = _get(s, f"https://m.stock.naver.com/api/news/stock/{code}", pageSize=20, page=1)
        news = []
        for grp in ((r.json() if r else []) or []):
            for x in grp.get("items", []):
                d = x.get("datetime", "")[:8]
                if d < since.strftime("%Y%m%d"):
                    continue
                t = (x.get("title") or "").replace("&quot;", '"').replace("&amp;", "&")
                if any(k in t for k in NEWS_KEYS):
                    news.append(f"  📰 {d[4:6]}/{d[6:]} {t}")
        items += news[:3]
        stock_lines.append(f"■ {name}" + ("" if items else " — 특이사항 없음"))
        stock_lines += items
        time.sleep(0.5)

    head = f"보유 {len(hold)}종목 · 최근 7일({since.month}/{since.day}~{today.month}/{today.day})"
    body = "\n".join(([f"위험 공시 {len(risk_lines)}건"] + risk_lines + [""]) if risk_lines else ["위험 공시 없음", ""])
    body += "\n".join(stock_lines)
    print(head + "\n" + body)
    ntfy.send(f"환동퀀트 주간 점검 · {head}", body, tags=["warning" if risk_lines else "newspaper"],
              priority=4 if risk_lines else 3, click="https://dart.fss.or.kr/")


if __name__ == "__main__":
    main()

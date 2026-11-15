"""데이터 가능성 점검 (임시): KRX 과거 일자·상장폐지 종목, DART 분기 전체재무(매출총이익·영업현금흐름), 주요사항보고서 목록."""
import os, time, json, requests
K = os.environ.get("KRX_API_KEY", "").strip(); D = os.environ.get("DART_API_KEY", "").strip()
s = requests.Session(); s.headers["User-Agent"] = "quant-dashboard/1.0"
def krx(ep, d):
    r = s.get("https://data-dbg.krx.co.kr/svc/apis/sto/" + ep, params={"basDd": d}, headers={"AUTH_KEY": K}, timeout=30)
    try: return r.status_code, r.json().get("OutBlock_1") or []
    except Exception: return r.status_code, r.text[:200]
for d in ("20100104", "20150402", "20160401", "20180402"):
    for ep in ("stk_bydd_trd", "ksq_bydd_trd"):
        c, rows = krx(ep, d)
        names = {x.get("ISU_NM") for x in rows} if isinstance(rows, list) else set()
        print(f"KRX {ep} {d}: HTTP {c} rows {len(rows) if isinstance(rows, list) else rows}", "| 예: " + ", ".join(sorted(n for n in names if n)[:3]) if names else "")
# 상장폐지 확인: 2016년에 있었고 지금은 없는 종목 수
c, a = krx("ksq_bydd_trd", "20160401"); c2, b = krx("ksq_bydd_trd", "20261113")
if isinstance(a, list) and isinstance(b, list):
    gone = {x["ISU_CD"] for x in a} - {x["ISU_CD"] for x in b}
    print("2016-04 코스닥에 있고 지금 없는 종목:", len(gone))
    print("필드:", sorted(a[0].keys()) if a else None)
def dart(path, **p):
    p["crtfc_key"] = D
    r = s.get("https://opendart.fss.or.kr/api/" + path, params=p, timeout=30)
    try: return r.status_code, r.json()
    except Exception: return r.status_code, r.text[:200]
# 삼성전자 00126380 / 소형주 예시 아이컴포넌트?  분기 전체재무
for corp, y, rp in (("00126380", 2016, "11013"), ("00126380", 2023, "11014")):
    for fs in ("CFS", "OFS"):
        t = time.time(); c, j = dart("fnlttSinglAcntAll.json", corp_code=corp, bsns_year=y, reprt_code=rp, fs_div=fs)
        lst = j.get("list", []) if isinstance(j, dict) else []
        nm = {x.get("account_nm") for x in lst}; ids = {x.get("account_id") for x in lst}
        want = ["매출총이익", "영업활동현금흐름", "영업활동으로인한현금흐름"]
        print(f"DART 전체재무 {corp} {y} {rp} {fs}: HTTP {c} status {j.get('status') if isinstance(j, dict) else j} 항목 {len(lst)} ({time.time()-t:.1f}s)",
              {w: any(w in (n or '') for n in nm) for w in want}, "GP id" , "ifrs-full_GrossProfit" in ids, "CFO id", "ifrs-full_CashFlowsFromUsedInOperatingActivities" in ids)
# 주요사항보고서 목록 (전체 회사, 3개월)
c, j = dart("list.json", bgn_de="20170101", end_de="20170331", pblntf_ty="B", page_count=100)
print("주요사항보고서 2017Q1:", c, j.get("status"), "총", j.get("total_count"), "페이지", j.get("total_page"))
if isinstance(j, dict) and j.get("list"):
    from collections import Counter
    print(Counter(x["report_nm"].split("(")[0].strip()[:20] for x in j["list"]).most_common(12))
# 거래소 공시(I) 중 횡령·배임, 불성실공시
c, j = dart("list.json", bgn_de="20170101", end_de="20170331", pblntf_ty="I", page_count=100)
print("거래소공시 2017Q1:", c, j.get("status"), "총", j.get("total_count"))
# 일괄다운로드 페이지 접근
try:
    r = s.get("https://opendart.fss.or.kr/disclosureinfo/fnltt/dwld/main.do", timeout=20); print("일괄다운로드 페이지:", r.status_code, len(r.text))
except Exception as e: print("일괄다운로드 페이지 실패:", e)

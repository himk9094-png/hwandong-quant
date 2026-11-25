"""공시 제목으로 식별한 보통주 상장폐지 위험·거래정지 상태. 일봉은 접수 다음 거래일부터 반영."""
import re

import numpy as np
import pandas as pd


def classify(title):
    s = re.sub(r'\s+', '', title)
    if re.search(r'우선주|\d우B?|이전상장|합병에따른상장폐지', s):
        return {}
    changes = {}
    if re.search('상장폐지|상장적격성|상장유지결정|정리매매', s):
        cleared = re.search('상장유지결정|상장적격성실질심사대상(?:에서)?제외|상장폐지(?:기준|사유)?미해당|상장폐지사유해소', s)
        changes['listing'] = not cleared or '정리매매' in s
    if '매매거래정지' in s:
        changes['halt'] = '정지해제' not in s or '정지지속' in s
    return changes


def build(store, dates, codes, through):
    files = sorted((store / 'disc').glob('I_*.parquet'))
    reports = pd.concat(pd.read_parquet(p) for p in files).drop_duplicates('rcept_no')
    lookup = {c:i for i,c in enumerate(codes)}
    sessions = np.array(dates)
    # Extra row gives next-session candidates using disclosures available by 'through'.
    bits = np.zeros(len(codes), dtype=np.uint8)
    matrix = np.zeros((len(dates)+1, len(codes)), dtype=np.uint8)
    keys = {'listing':1, 'halt':2}
    cursor = 0
    for r in reports[reports.date <= through].sort_values(['date','rcept_no']).itertuples():
        changes = classify(r.report)
        if not changes or r.stock not in lookup:
            continue
        effective = sessions.searchsorted(r.date, side='right')
        if effective > cursor:
            matrix[cursor:effective] = bits
            cursor = effective
        k = lookup[r.stock]
        for kind, active in changes.items():
            bit = keys[kind]
            bits[k] = int(bits[k]) | bit if active else int(bits[k]) & ~bit
    matrix[cursor:] = bits
    return matrix

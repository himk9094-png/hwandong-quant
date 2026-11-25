"""Monthly microcap disclosure study. Run from repository root with a data branch checkout in store/."""
import glob
import re
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

from backtest import smallcap as S


def event_changes(title):
    title = re.sub(r'\s+', '', title)
    if re.search('우선주|[0-9]우B?|이전상장|합병에따른상장폐지', title):
        return {}
    changes = {}
    for key, token in [('listing', '상장폐지|상장적격성'), ('management', '관리종목'), ('watch', '투자주의환기')]:
        if re.search(token, title):
            clear = (r'상장유지결정|상장적격성실질심사대상(?:에서)?제외|상장폐지(?:기준|사유)?미해당|상장폐지사유해소'
                     if key == 'listing' else token + r'(?:지정사유)?(?:지정)?해제')
            changes[key] = not bool(re.search(clear, title))
    if '상장유지결정' in title:
        changes['listing'] = False
    if '정리매매' in title:
        changes['listing'] = True
    if '매매거래정지' in title:
        changes['halt'] = '해제' not in title or '정지지속' in title
    return changes


def disclosures(dates, codes):
    result = defaultdict(list)
    lookup = {code: i for i, code in enumerate(codes)}
    files = glob.glob(str(S.STORE / 'disc' / 'I_*.parquet'))
    reports = pd.concat(pd.read_parquet(f) for f in files).drop_duplicates('rcept_no')
    for r in reports.sort_values(['date', 'rcept_no']).itertuples():
        if r.stock not in lookup:
            continue
        effective = np.searchsorted(dates, r.date, side='right')
        changes = event_changes(r.report)
        if changes:
            result[effective].append((lookup[r.stock], changes))
    return result


def run(m, events, ranked, threshold, filtered):
    dates = m['dates']
    n = len(m['codes'])
    states = [set() for _ in range(n)]
    flags = m['flags'].to_numpy()
    gap, intra, value = [m[k].to_numpy() for k in ('gap','intra','val')]
    held, basis, pending = {}, {}, {}
    cash = 1.
    daily, liquid = [], []
    count = Counter()
    for i, date in enumerate(dates):
        for k, changes in events.get(i, []):
            for key, active in changes.items():
                if active:
                    states[k].add(key)
                else:
                    states[k].discard(key)
        if date < S.START:
            continue
        valid = np.isfinite(gap[i]) & np.isfinite(intra[i]) & (value[i] > 0)
        def blocked(k):
            return bool(states[k]) or flags[i-1, k]
        for k in held:
            if valid[k]:
                held[k] *= 1 + gap[i,k]
                basis[k] *= 1 + gap[i,k]
            if filtered and blocked(k):
                pending.setdefault(k, 'risk')
        fee = S.COMM + .005
        sell_fee = fee + S.sell_tax(date)
        exited = set()
        for k in list(pending):
            if valid[k]:
                cash += held.pop(k) * (1-sell_fee)
                basis.pop(k)
                count[pending.pop(k)] += 1
                exited.add(k)
        if i in ranked:
            picks = [k for k in ranked[i] if not filtered or not blocked(k)][:20]
            count['excluded_slot_dates'] += len(set(ranked[i][:20]) - set(picks)) if filtered else 0
            # Frozen assets supply no cash. Do not replace a pick using today's ex-post volume.
            cap = (cash + sum(v for k,v in held.items() if valid[k])) / 20
            target = {k:cap for k in picks if valid[k] and k not in exited}
            for k in list(held):
                if k not in picks and not valid[k]:
                    pending.setdefault(k, 'rebalance')
                delta = held[k]-target.get(k, 0)
                if delta > 0 and valid[k]:
                    cash += delta*(1-sell_fee)
                    if k in target:
                        held[k] -= delta
                    else:
                        del held[k], basis[k]
            buys = {k:v-held.get(k,0) for k,v in target.items() if v>held.get(k,0)}
            scale = min(1, cash / (sum(buys.values())*(1+fee))) if buys else 0
            for k,amt in buys.items():
                amt *= scale
                if amt < 1e-14:
                    continue
                old = held.get(k,0)
                held[k] = old+amt
                basis[k] = (old+amt)/(old/basis.get(k,1)+amt)
                cash -= amt*(1+fee)
        for k in held:
            if valid[k]:
                held[k] *= 1 + intra[i,k]
                basis[k] *= 1 + intra[i,k]
                if threshold and basis[k] >= 1+threshold-1e-12:
                    pending.setdefault(k,'tp')
        assert cash > -1e-10
        daily.append(cash+sum(held.values()))
        liquid.append(cash+sum(v for k,v in held.items() if valid[k]))
    years = (pd.Timestamp(dates[-1])-pd.Timestamp(S.START)).days/365.25
    a=np.array(daily)
    return [round(100*x,2) for x in (a[-1]**(1/years)-1, min(a/np.maximum.accumulate(a)-1), liquid[-1]**(1/years)-1,1-liquid[-1]/a[-1])], dict(count)


if __name__ == '__main__':
    m=S.load()
    lookup={c:i for i,c in enumerate(m['codes'])}
    ranks={}
    for i in S.reb_days(m['dates'],'M'):
        j=i-1
        ok=m['alive'].iloc[j] & m['static_ok'] & ~m['flags'].iloc[j] & (m['val20'].iloc[j]>=S.MIN_VAL)
        cap=m['mcap'].iloc[j]
        cap=cap[ok & (cap>0)]
        ranks[i]=[lookup[c] for c in cap[cap<=cap.quantile(.2)].sort_values(kind='stable').index]
    events=disclosures(np.array(m['dates']),m['codes'])
    print('dates',S.START,m['dates'][-1], 'months',len(ranks))
    for filtered in (False,True):
        for tp in (0,.10,.20,.30):
            print(filtered,tp,*run(m,events,ranks,tp,filtered),flush=True)

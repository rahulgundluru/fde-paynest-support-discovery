import json, re
import pandas as pd
from norm import load, cat, ts, D
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 30); pd.set_option('display.max_rows', 200)

df, ev = load()
print('raw rows', len(df), 'unique ids', df.id.nunique())
df = df.drop_duplicates('id')
ev = ev.drop_duplicates(['id', 'i'])
cust = pd.read_csv(D + 'customers.csv')
df = df.merge(cust[['customer_id', 'name', 'tier', 'region', 'monthly_fee_usd']], left_on='cust', right_on='customer_id', how='left')
print('tickets w/o customer match', df.tier.isna().sum())

# created-event category vs header category
evc = ev[ev.action == 'created'].copy()
evc['cat_created'] = evc.data.apply(lambda d: cat(d.get('category')))
df = df.merge(evc[['id', 'cat_created']], on='id', how='left')
print('\n== header cat vs created-event cat differ:', (df.cat != df.cat_created).sum())

print('\n== VOLUME by month / payroll week')
df['month'] = df.start.dt.strftime('%Y-%m')
day = df.start.dt.tz_convert('Asia/Kolkata').dt.day
df['pweek'] = (day >= 25) | (day <= 3)
print(df.groupby('month').size())
print('share in payroll week:', round(df.pweek.mean(), 3), '(days fraction ~', round(10 / 30.5, 3), ')')

print('\n== By tier/customer')
print(df.groupby('tier').size())
print(df.groupby(['cust', 'name', 'tier']).size().sort_values(ascending=False).head(10))

print('\n== Category mix')
print(df.cat.value_counts(normalize=True).round(3))
print(pd.crosstab(df.cat, df.prio))

# --- SLA recompute
sla = {('Enterprise', 'P1'): 8, ('Enterprise', 'P2'): 24, ('Enterprise', 'P3'): 72,
       ('Business', 'P1'): 12, ('Business', 'P2'): 48, ('Business', 'P3'): 96}
pct = {('Enterprise', 'P1'): 10, ('Enterprise', 'P2'): 5, ('Enterprise', 'P3'): 2,
       ('Business', 'P1'): 5, ('Business', 'P2'): 3, ('Business', 'P3'): 1}
frt = {('Enterprise', 'P1'): 1, ('Enterprise', 'P2'): 4, ('Enterprise', 'P3'): 8,
       ('Business', 'P1'): 2, ('Business', 'P2'): 8, ('Business', 'P3'): 24}
df['target_h'] = [sla.get((a, b)) for a, b in zip(df.tier, df.prio)]
df['breach'] = df.res_h > df.target_h
df['calc_credit_pct'] = [pct.get((a, b), 0) * (1.5 if r > 2 * t else 1) if t and r > t else 0
                         for a, b, r, t in zip(df.tier, df.prio, df.res_h, df.target_h)]
df['calc_credit'] = df.calc_credit_pct / 100 * df.monthly_fee_usd

br = pd.read_csv(D + 'sla_breaches_q3.csv')
print('\n== BREACH FILE', len(br), 'rows; credits by month'); print(br.groupby('invoice_month').credit_usd.sum())
print('dup ticket ids in breach file', br.ticket_id.duplicated().sum())
print('reasons'); print(br.agent_noted_reason.value_counts(dropna=False))
b = br.merge(df[['id', 'cat', 'channel', 'name', 'res_h', 'n_reassign', 'n_reassign_nonhandover', 'n_reopen', 'kb_cited', 'pweek', 'title', 'calc_credit']], left_on='ticket_id', right_on='id', how='left')
print('breach rows not in ticket export', b.id.isna().sum())
print('\ncredits by customer'); print(b.groupby(['customer_id', 'name']).credit_usd.agg(['count', 'sum']).sort_values('sum', ascending=False).head(10))
print('\ncredits by category'); print(b.groupby('cat').credit_usd.agg(['count', 'sum']).sort_values('sum', ascending=False))
print('\ncredits by month x customer'); print(b.pivot_table(index='customer_id', columns='invoice_month', values='credit_usd', aggfunc='sum').fillna(0).sort_values('2026-08', ascending=False).head(8))
print('\ncredits by tier'); print(b.groupby('tier').credit_usd.agg(['count', 'sum']))

# breaches we compute that are not in Finance file
calc = df[df.breach & df.target_h.notna()]
missing = calc[~calc.id.isin(br.ticket_id)]
print('\n== computed breaches (contract tiers):', len(calc), 'in finance file:', calc.id.isin(br.ticket_id).sum(),
      'missing from file:', len(missing), 'missing credit USD', round(missing.calc_credit.sum(), 1))
print(missing.groupby(['tier', 'prio']).size())
fil_not_breach = b[b.id.notna() & (b.res_h <= b.sla_resolution_target_h)]
print('in file but resolution within target per export:', len(fil_not_breach))
print('credit arithmetic mismatches:', (abs(b.credit_usd - b.credit_pct / 100 * b.monthly_fee_usd) > 0.5).sum())
mult_err = b[(b.actual_resolution_h > 2 * b.sla_resolution_target_h)]
print('severity-multiplier cases:', len(mult_err))
print('file actual_res vs export res_h diff>1h:', (abs(b.actual_resolution_h - b.res_h) > 1).sum())

print('\n== REASSIGNMENT')
print('tickets with >=1 reassign', (df.n_reassign >= 1).sum(), '>=2', (df.n_reassign >= 2).sum(), '>=2 non-handover', (df.n_reassign_nonhandover >= 2).sum())
ra = ev[ev.action == 'reassigned'].copy()
ra['reason'] = ra.data.apply(lambda d: d.get('reason'))
ra['frm'] = ra.data.apply(lambda d: d.get('from')); ra['to'] = ra.data.apply(lambda d: d.get('to'))
print(ra.reason.value_counts())
print(ra.groupby(['frm', 'to']).size().sort_values(ascending=False).head(15))
print('breach rate by bounce count (non-handover)')
print(df[df.target_h.notna()].groupby(df.n_reassign_nonhandover.clip(upper=3)).breach.agg(['count', 'mean']))
print('lead approvals in history?', ev.data.apply(lambda d: 'approv' in json.dumps(d).lower()).sum())

print('\n== REOPENS & KB')
df['kb0142'] = df.kb_cited.apply(lambda l: any(k in ('KB-0142', 'KB-0143') for k in l))
df['kb0201'] = df.kb_cited.apply(lambda l: any(k in ('KB-0201', 'KB-0202') for k in l))
df['pf'] = df.title.str.contains(r'\bPF\b|provident|wage ceiling|EPF', case=False) | df.body.str.contains(r'\bPF\b|wage ceiling|EPF', case=False)
df['wps'] = df.title.str.contains(r'WPS|SIF', case=False) | df.body.str.contains(r'WPS|SIF', case=False)
for k in ['kb0142', 'kb0201', 'pf', 'wps']:
    s = df[df[k]]
    print(k, 'n', len(s), 'reopen rate', round((s.n_reopen > 0).mean(), 2), 'breach', round(s.breach.mean(), 2), 'median res_h', round(s.res_h.median(), 1))
print('overall reopen rate', round((df.n_reopen > 0).mean(), 3))
print('reopens by topic (pf / wps / other):', df[df.pf].n_reopen.sum(), df[df.wps].n_reopen.sum(), df[~df.pf & ~df.wps].n_reopen.sum())
# PF before/after 1 Aug, WPS before/after 15 Jul
for k, d0 in [('pf', '2026-08-01'), ('wps', '2026-07-15')]:
    s = df[df[k]]
    after = s.start >= pd.Timestamp(d0, tz='UTC')
    print(k, 'before', len(s[~after]), 'reopen', round((s[~after].n_reopen > 0).mean(), 2), '| after', len(s[after]), 'reopen', round((s[after].n_reopen > 0).mean(), 2))
kbuse = pd.Series([k for l in df.kb_cited for k in l]).value_counts()
print('KB citations'); print(kbuse.head(12))
print('replies with KB id share', round(ev[ev.action == 'replied'].data.apply(lambda d: bool(d.get('kb_article_id'))).mean(), 2))
print('stat tickets breached by pf/wps', df[(df.pf | df.wps) & df.breach].shape[0], 'credits', round(b[b.id.isin(df[df.pf | df.wps].id)].credit_usd.sum(), 1))

print('\n== FIRST RESPONSE')
print('fr blank by channel'); print(df.assign(blank=df.fr.isna()).groupby('channel').blank.sum())
print('blank fr but has replied event', (df.fr.isna() & df.fr_ev.notna()).sum())
blank = df[df.fr.isna()]
print('blank-fr tickets: prio', blank.prio.value_counts().to_dict(), 'tier', blank.tier.value_counts().to_dict(), 'breach rate', round(blank.breach.mean(), 2))
df['frt_target'] = [frt.get((a, b)) for a, b in zip(df.tier, df.prio)]
m = df.frt_target.notna()
print('FRT compliance on recorded-only:', round((df[m & df.fr.notna()].fr_h <= df[m & df.fr.notna()].frt_target).mean(), 3),
      '| using event-derived fr all:', round((df[m].fr_h <= df[m].frt_target).mean(), 3))
print('median FR hrs by month'); print(df.groupby('month').fr_h.median().round(2))
print('first reply is holding reply share', round(ev[ev.action == 'replied'].groupby('id').head(1).data.apply(lambda d: d.get('note') == 'holding reply').mean(), 2))

print('\n== CSAT')
cs = pd.read_csv(D + 'csat_survey.csv')
cs['m'] = cs.submitted_at.str[:7]
print(cs.groupby('m').score.agg(['count', 'mean']).round(2))
print('ex-Kestrel'); print(cs[cs.customer_id != 'C-1001'].groupby('m').score.agg(['count', 'mean']).round(2))
print('Kestrel'); print(cs[cs.customer_id == 'C-1001'].groupby('m').score.agg(['count', 'mean']).round(2))
cs2 = cs.merge(df[['id', 'cat', 'n_reopen', 'n_reassign_nonhandover', 'pf', 'wps', 'fr_h', 'res_h']], left_on='ticket_id', right_on='id', how='left')
print('csat by category'); print(cs2.groupby('cat').score.agg(['count', 'mean']).round(2))
print('csat by reopened'); print(cs2.groupby(cs2.n_reopen > 0).score.agg(['count', 'mean']).round(2))
print('csat by bounces'); print(cs2.groupby(cs2.n_reassign_nonhandover.clip(upper=2)).score.agg(['count', 'mean']).round(2))
print('corr csat vs fr_h', round(cs2.score.corr(cs2.fr_h), 3), 'vs res_h', round(cs2.score.corr(cs2.res_h), 3))
print('csat survey ticket ids not in export', (~cs.ticket_id.isin(df.id)).sum())
print(cs[cs.comment.notna()].comment.value_counts().head(20))

print('\n== KESTREL')
k = df[df.cust == 'C-1001']
print('tickets', len(k), 'by cat', k.cat.value_counts().to_dict())
print('median fr_h', round(k.fr_h.median(), 2), 'vs others', round(df[df.cust != 'C-1001'].fr_h.median(), 2))
jira = json.load(open(D + 'jira_escalations.json'))
j = {x['key']: x for x in jira}
print('PNJ-4412', j['PNJ-4412']['status'], len(j['PNJ-4412']['linked_ticket_ids']), 'linked tickets')
lk = set(j['PNJ-4412']['linked_ticket_ids'])
print('breach credits on 4412-linked tickets', round(b[b.ticket_id.isin(lk)].credit_usd.sum(), 1), 'n', b.ticket_id.isin(lk).sum())
print('jira status counts', pd.Series([x['status'] for x in jira]).value_counts().to_dict())
print('jira by component', pd.Series([x['component'] for x in jira]).value_counts().to_dict())
for x in jira:
    if x['key'] in ('PNJ-4412', 'PNJ-5107') or len(x['linked_ticket_ids']) > 3:
        print(x['key'], x['status'], x['created'], x['customer_ids'], len(x['linked_ticket_ids']), x['summary'][:90])

print('\n== MERIDIAN 25 Sep incident')
mer = df[(df.cust == 'C-1002') & (df.start.dt.tz_convert('Asia/Kolkata').dt.strftime('%m-%d') == '09-25')]
mer = mer[mer.title.str.contains('blank|payslip', case=False) | mer.body.str.contains('blank', case=False)].sort_values('start')
print('blank payslip tickets', len(mer))
st = mer.start.dt.tz_convert('Asia/Kolkata')
print('1st', st.iloc[0], '5th', st.iloc[4] if len(mer) > 4 else None, 'last', st.iloc[-1])
print('ticket count by 15:50 IST', (st < pd.Timestamp('2026-09-25 15:50', tz='Asia/Kolkata')).sum())

print('\n== FALCON / ORCHID / Gulf Horizon')
print(cust[cust.name.str.contains('Falcon|Orchid|Gulf', case=False)])
for c in cust[cust.name.str.contains('Falcon|Orchid', case=False)].customer_id:
    s = df[df.cust == c]
    print(c, len(s), 'median res_h', round(s.res_h.median(), 1), 'max', round(s.res_h.max(), 1), 'wps', s.wps.sum(), 'reassign', s.n_reassign_nonhandover.sum())
    print(s.sort_values('res_h', ascending=False)[['id', 'title', 'prio', 'fr_h', 'res_h', 'n_reassign_nonhandover', 'n_reopen']].head(4))
gh = df[df.id == 'PN-100402']
print(gh[['id', 'cust', 'title', 'prio', 'res_h', 'n_reassign', 'n_reassign_nonhandover', 'n_reopen', 'kb_cited']])

print('\n== HOW-TO really something else?')
ht = df[df.cat == 'How-To']
print('How-To mentioning PF/WPS/error words', (ht.pf | ht.wps | ht.title.str.contains('error|wrong|fail|not working|blank|duplicate|reject', case=False)).sum(), 'of', len(ht))
print('How-To escalated to L2/Jira', (ht.n_escalated > 0).sum(), 'breach credits', round(b[b.cat == 'How-To'].credit_usd.sum(), 1))
print('title sample of how-to with stat words:'); print(ht[ht.pf | ht.wps].title.value_counts().head(10))
print('How-To queue vs first queue', ht.first_queue.value_counts().to_dict())

print('\n== AGENTS')
ag = pd.read_csv(D + 'agent_roster.csv')
print(len(ag), ag.status.value_counts().to_dict())
print(ag[ag.status != 'active'])
print(ag.groupby(['tier', 'desk', 'status']).size())


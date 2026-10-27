"""Normalise the three ticket export formats into one event model (all times UTC)."""
import json, re, sys, datetime as dt
import pandas as pd

import os
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data-room', 'data') + '/'
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
GST = dt.timezone(dt.timedelta(hours=4))
UTC = dt.timezone.utc

def ts(v, naive_tz=IST):
    if v is None or v == '':
        return None
    if isinstance(v, (int, float)):
        return dt.datetime.fromtimestamp(v / 1000 if v > 1e11 else v, UTC)
    s = str(v).strip()
    m = re.match(r'(\d\d)/(\d\d)/(\d{4}) (\d\d):(\d\d) (IST|GST)', s)
    if m:
        d, mo, y, h, mi, z = m.groups()
        return dt.datetime(int(y), int(mo), int(d), int(h), int(mi), tzinfo=IST if z == 'IST' else GST).astimezone(UTC)
    x = dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
    if x.tzinfo is None:
        x = x.replace(tzinfo=naive_tz)
    return x.astimezone(UTC)

CAT = [
    (r'how', 'How-To'), (r'statut|compliance', 'Statutory'), (r'payroll', 'Payroll'),
    (r'leave|attend', 'Leave & Attendance'), (r'integr', 'Integration'), (r'report', 'Reports'),
    (r'bill', 'Billing'), (r'access|login', 'Access / Login'), (r'onboard|setup', 'Onboarding'),
]
def cat(c):
    if not c:
        return None
    c = c.lower()
    for p, n in CAT:
        if re.search(p, c):
            return n
    return 'OTHER:' + c

PRIO = {'urgent': 'P1', 'high': 'P2', 'normal': 'P3', 'S1': 'P1', 'S2': 'P2', 'S3': 'P3'}

def load():
    raw = json.load(open(D + 'tickets_export.json'))['tickets']
    rows, events = [], []
    for t in raw:
        if t.get('source') == 'inapp_widget':
            r = dict(id=t['tkt'], channel='widget', cust=t['org_id'], title=t['title'], body=t['message'],
                     cat_raw=t['cat'], prio=t['prio'], created=ts(t['created']), fr=ts(t['first_reply_ts']),
                     closed=ts(t['closed_ts']))
            ev = [(h['action'], ts(h['time']), h['who'], h['data']) for h in t['history']]
        elif t.get('channel') == 'email':
            r = dict(id=t['ticket_id'], channel='email', cust=t['customer_id'], title=t['subject'], body=t['body'],
                     cat_raw=t['category'], prio=t['priority'], created=ts(t['received_at']),
                     fr=ts(t['first_response_at']), closed=ts(t['resolved_at']))
            ev = [(h['event'], ts(h['ts']), h['actor'], h['meta']) for h in t['events']]
        else:
            r = dict(id=t['reference'], channel='partner', cust=t['tenant'], title=t['summary'], body=t['details'],
                     cat_raw=t['topic'], prio=t['severity'], created=ts(t['opened_on']), fr=ts(t['fr_at']),
                     closed=ts(t['closed_at']), partner=t['partner_id'])
            ev = [(h['evt'].lower(), ts(h['when']), h['user'], h['info']) for h in t['activity_log']]
        r['prio'] = PRIO.get(r['prio'], r['prio'])
        r['cat'] = cat(r['cat_raw'])
        ev.sort(key=lambda e: e[1])
        r['created_ev'] = next((e[1] for e in ev if e[0] == 'created'), None)
        res = [e for e in ev if e[0] == 'resolved']
        r['final_resolved'] = res[-1][1] if res else None
        r['resolved_cat'] = cat(res[-1][3].get('category')) if res else None
        r['n_reassign'] = sum(e[0] == 'reassigned' for e in ev)
        r['n_reassign_nonhandover'] = sum(e[0] == 'reassigned' and 'handover' not in str(e[3].get('reason', '')).lower() for e in ev)
        r['n_reopen'] = sum(e[0] == 'reopened' for e in ev)
        r['n_escalated'] = sum(e[0] == 'escalated' for e in ev)
        r['kb_cited'] = [e[3].get('kb_article_id') for e in ev if e[0] == 'replied' and e[3].get('kb_article_id')]
        r['fr_ev'] = next((e[1] for e in ev if e[0] == 'replied' and not e[3].get('internal')), None)
        r['first_queue'] = next((e[3].get('queue') for e in ev if e[0] == 'assigned'), None)
        rows.append(r)
        for i, e in enumerate(ev):
            events.append(dict(id=r['id'], i=i, action=e[0], t=e[1], who=e[2], data=e[3]))
    df = pd.DataFrame(rows)
    ev = pd.DataFrame(events)
    start = df['created_ev'].fillna(df['created'])
    df['start'] = start
    df['res_h'] = (df['final_resolved'] - start).dt.total_seconds() / 3600
    df['fr_h'] = (df['fr_ev'] - start).dt.total_seconds() / 3600
    return df, ev

if __name__ == '__main__':
    df, ev = load()
    print(df.shape, ev.shape)

#!/usr/bin/env python3
"""Fetch tennis fixtures + results from TennisExplorer's daily matches pages
(type=all: ATP/WTA main tour, Challengers, ITF — singles only) and write
data.json. Runs server-side (GitHub Actions), where no browser CORS rule
applies. Stdlib only.

Times are TennisExplorer's displayed times, passed through as text.
Set scores are normalised: a tiebreak set is stored as games [7, 6] with the
loser's tiebreak points in the parallel 'tb' list.
"""
import json, re, sys, urllib.request
from datetime import datetime, timedelta, timezone

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'}

def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8', errors='ignore')

def clean(s):
    s = re.sub(r'<[^>]+>', '', s or '')
    s = s.replace('&nbsp;', ' ').replace('&amp;', '&')
    return re.sub(r'\s+', ' ', s).strip()

def classify(name, href, inner_html):
    n = (name or '').lower()
    if 'type=double' in href or 'type-men4' in inner_html or 'type-women4' in inner_html:
        return None  # doubles — the predictor is singles-only
    if 'utr' in n:
        return 'UTR'
    if 'challenger' in n:
        return 'Challenger'
    if re.search(r'\bitf\b', n):
        return 'ITF'
    return 'Main tour'

ROW_RE = re.compile(
    r'<tr class="head flags">\s*<td class="t-name"[^>]*><a href="([^"]+)">(.*?)</a></td>'
    r'|<tr id="r\d+"[^>]*>(.*?)</tr>\s*<tr id="r\d+b"[^>]*>(.*?)</tr>',
    re.S)

def parse_side(row_html):
    m = re.search(r'<td class="t-name"><a href="/player/[^"]+">(.*?)</a>(.*?)</td>', row_html, re.S)
    name = clean(m.group(1)) if m else ''
    seed = clean(m.group(2)) if m else ''
    if seed:
        name = f'{name} {seed}'
    res = re.search(r'<td class="result">(.*?)</td>', row_html, re.S)
    sets_won = clean(res.group(1)) if res else ''
    scores = [clean(x) for x in re.findall(r'<td class="score(?: nbr)?">(.*?)</td>', row_html, re.S)]
    return name, sets_won, scores

def norm_set(a, b):
    """Return ([g1, g2], tb_points_or_None). TE glues tiebreak points onto the
    loser's games: '64' means 6 games, 4 tiebreak points."""
    ai, bi = int(a), int(b)
    tb = None
    def split(v):
        s = str(v)
        if v >= 100:
            return int(s[:-2]) if len(s) > 2 else v, int(s[-2:]) if len(s) > 2 else None
        if v > 12:
            return int(s[0]), int(s[1:])
        return v, None
    if ai > 12 or bi > 12:
        ai, ta = split(ai)
        bi, tb2 = split(bi)
        tb = ta if ta is not None else tb2
    return [ai, bi], tb

def parse_day(html, date_str):
    out, tour, level, sex, skipped = [], None, None, None, 0
    for m in ROW_RE.finditer(html):
        if m.group(1) is not None:
            href, inner = m.group(1), m.group(2)
            tour = clean(inner)
            level = classify(tour, href, inner)
            sex = 'W' if '/wta-women/' in href else ('M' if '/atp-men/' in href else None)
            continue
        if tour is None:
            continue
        if level is None:
            skipped += 1
            continue
        row1, row2 = m.group(3), m.group(4)
        tm = re.search(r'<td class="first time"[^>]*>(.*?)</td>', row1, re.S)
        time_txt = clean(tm.group(1)) if tm else ''
        p1, r1, s1 = parse_side(row1)
        p2, r2, s2 = parse_side(row2)
        if not p1 or not p2:
            continue
        sets, tbs = [], []
        for a, b in zip(s1, s2):
            if a.isdigit() and b.isdigit():
                g, tb = norm_set(a, b)
                sets.append(g)
                tbs.append(tb)
        finished = r1.isdigit() and r2.isdigit() and len(sets) > 0
        winner = None
        if finished:
            winner = p1 if int(r1) > int(r2) else p2
        out.append({
            'date': date_str, 'tour': tour, 'level': level, 'sex': sex,
            'time': time_txt, 'p1': p1, 'p2': p2,
            'sets': sets, 'tb': tbs,
            'status': 'finished' if finished else 'scheduled',
            'winner': winner,
        })
    return out, skipped

def main():
    now = datetime.now(timezone.utc)
    matches, per_day, seen = [], {}, set()
    total_skipped = 0
    for delta in (-1, 0, 1):
        d = (now + timedelta(days=delta)).date()
        url = (f'https://www.tennisexplorer.com/matches/?type=all'
               f'&year={d.year}&month={d.month}&day={d.day}')
        try:
            html = fetch(url)
        except Exception as e:
            print(f'WARN: fetch failed for {d}: {e}', file=sys.stderr)
            continue
        rows, skipped = parse_day(html, d.isoformat())
        total_skipped += skipped
        kept = 0
        for r in rows:
            key = (r['date'], r['tour'], r['p1'], r['p2'], r['time'])
            if key in seen:
                continue
            seen.add(key)
            matches.append(r)
            kept += 1
        per_day[d.isoformat()] = kept
        print(f'{d}: {kept} matches ({skipped} doubles rows skipped)')
    payload = {
        'kind': 'predictor-live-feed', 'version': 1,
        'generatedAt': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'source': 'TennisExplorer daily matches pages, type=all, singles only',
        'timeNote': 'Times are TennisExplorer display times, passed through unchanged.',
        'counts': per_day, 'matches': matches,
    }
    with open('data.json', 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print('wrote data.json with', len(matches), 'matches')

if __name__ == '__main__':
    main()

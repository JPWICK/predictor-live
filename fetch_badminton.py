#!/usr/bin/env python3
"""Fetch badminton draws + results from Wikipedia tournament pages and write
badminton.json. Runs server-side (GitHub Actions). Stdlib only.

Why Wikipedia: the official BWF pages and TournamentSoftware are closed to
scripts (login wall / bot protection), while Wikipedia's tournament pages are
openly readable and are updated by editors from the official results during
each event. Limits: no start times, results can lag the official feed, names
use Wikipedia's short form (e.g. "P Tan / Thinaah M"), and only events with a
Wikipedia page are covered (World Tour Super 300 and above, mostly).

To cover a new tournament, add it to PAGES below: (display name, page title).
"""
import json, re, sys, urllib.request, urllib.parse
from datetime import datetime, timezone

PAGES = [
    ("Hong Kong Open", "2026 Hong Kong Open (badminton)"),
]

API = 'https://en.wikipedia.org/w/api.php'
UA = {'User-Agent': 'PredictorLiveFeed/1.1 (https://github.com/JPWICK/predictor-live; personal project)'}
CATS = {"men's singles": 'MS', "women's singles": 'WS', "men's doubles": 'MD',
        "women's doubles": 'WD', 'mixed doubles': 'XD'}

def api(params):
    url = API + '?' + urllib.parse.urlencode(params)
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            if isinstance(data, dict) and 'error' in data:
                raise RuntimeError('API error: ' + str(data['error'])[:200])
            return data
        except Exception as e:
            last = e
            print(f'  api attempt {attempt + 1} failed: {e}', file=sys.stderr)
            import time
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f'Wikipedia API unreachable after retries: {last}')

def clean_team(raw):
    raw = (raw or '').replace("'''", '').replace("''", '')
    names = re.findall(r'\[\[[^|\]]*\|([^\]]+)\]\]', raw)
    if not names:
        names = re.findall(r'\[\[([^\]|]+)\]\]', raw)
    names = [re.sub(r'\s+', ' ', n).strip() for n in names]
    names = [n for n in names if n]
    if names:
        return ' / '.join(names)
    txt = re.sub(r'\{\{[^}]*\}\}', '', raw)
    txt = re.sub(r'<[^>]+>', ' ', txt)
    return re.sub(r'\s+', ' ', txt).strip()

def extract_templates(text, name_pat):
    """Yield full {{...Bracket...}} template bodies using brace matching —
    the templates contain nested {{flagicon}} templates, so a plain regex
    over the body does not work."""
    out, pos = [], 0
    for m in re.finditer(r'\{\{' + name_pat, text):
        depth, j = 0, m.start()
        while j < len(text):
            if text.startswith('{{', j):
                depth += 1; j += 2; continue
            if text.startswith('}}', j):
                depth -= 1; j += 2
                if depth == 0:
                    break
                continue
            j += 1
        out.append(text[m.start():j])
    return out

def parse_brackets(wikitext, tournament, category):
    out = []
    for body in extract_templates(wikitext, r'\d+TeamBracket'):
        labels = dict(re.findall(r'\|\s*RD(\d+)\s*=\s*([^\n|]+)', body))
        teams, scores = {}, {}
        for m in re.finditer(r'\|\s*RD(\d+)-team(\d+)\s*=\s*([^\n]*)', body):
            teams[(int(m.group(1)), int(m.group(2)))] = clean_team(m.group(3))
        for m in re.finditer(r'\|\s*RD(\d+)-score(\d+)-(\d+)\s*=\s*([^\n|]*)', body):
            v = m.group(4).strip().strip("'").strip()
            if v.isdigit():
                scores.setdefault((int(m.group(1)), int(m.group(2))), {})[int(m.group(3))] = int(v)
        rounds = sorted({r for r, _ in teams})
        for rd in rounds:
            idxs = sorted(i for r, i in teams if r == rd)
            for k in range(0, len(idxs) - 1, 2):
                i1, i2 = idxs[k], idxs[k + 1]
                p1, p2 = teams[(rd, i1)], teams[(rd, i2)]
                if not p1 or not p2:
                    continue
                s1 = scores.get((rd, i1), {})
                s2 = scores.get((rd, i2), {})
                games = [[s1[g], s2[g]] for g in sorted(set(s1) & set(s2))]
                finished = len(games) > 0
                winner = None
                if finished:
                    w1 = sum(1 for a, b in games if a > b)
                    w2 = sum(1 for a, b in games if b > a)
                    if w1 > w2:
                        winner = p1
                    elif w2 > w1:
                        winner = p2
                out.append({
                    'tournament': tournament, 'category': category,
                    'round': (labels.get(str(rd)) or f'Round {rd}').strip(),
                    'p1': p1, 'p2': p2, 'games': games,
                    'status': 'finished' if finished else 'scheduled',
                    'winner': winner,
                })
    return out

def fetch_page(title):
    secs = api({'action': 'parse', 'page': title, 'prop': 'sections', 'format': 'json'})
    sections = secs.get('parse', {}).get('sections', [])
    matches, current_cat = [], None
    for s in sections:
        line = (s.get('line') or '').strip().lower()
        if s.get('level') == '2':
            current_cat = CATS.get(line)
            continue
        if current_cat is None:
            continue
        wt = api({'action': 'parse', 'page': title, 'prop': 'wikitext',
                  'format': 'json', 'section': s['index']})
        text = wt.get('parse', {}).get('wikitext', {}).get('*', '')
        matches.extend(parse_brackets(text, title, current_cat))
    return matches

def main():
    now = datetime.now(timezone.utc)
    matches, seen = [], set()
    for display, title in PAGES:
        try:
            rows = fetch_page(title)
        except Exception as e:
            print(f'ERROR: {title}: {e}', file=sys.stderr)
            print('Aborting without writing, so the last good badminton.json stays in place.', file=sys.stderr)
            sys.exit(1)
        kept = 0
        for r in rows:
            r['tournament'] = display
            key = (r['tournament'], r['category'], r['round'], r['p1'], r['p2'])
            if key in seen:
                continue
            seen.add(key)
            matches.append(r)
            kept += 1
        print(f'{display}: {kept} matches')
    if not matches:
        print('ERROR: 0 matches parsed — refusing to overwrite badminton.json with an empty feed.', file=sys.stderr)
        sys.exit(1)
    payload = {
        'kind': 'predictor-badminton-feed', 'version': 1,
        'generatedAt': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'source': 'Wikipedia tournament pages (community-updated from official BWF results)',
        'notes': 'No start times; names in Wikipedia short form; results may lag the official feed.',
        'matches': matches,
    }
    with open('badminton.json', 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print('wrote badminton.json with', len(matches), 'matches')

if __name__ == '__main__':
    main()

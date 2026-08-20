#!/usr/bin/env python3
"""
add_training.py — append one training session (or rest-day BP log) to
training.html and regenerate training.pdf, with no LLM involved.

Ground truth is always `raw`/`rows` inside training.html. Every derived
section (hero metrics, weekly rollups, table footer, cumulative-kcal chart,
axis ranges, Plan-200W chart, daily legend, subtitle) is fully RE-DERIVED
from raw/rows on every run rather than hand-patched — this is what avoids
the class of staleness bugs (stale tfoot total, kumData length mismatch)
found in earlier manual edits.

Usage
-----
Kinomap paste (paste the session summary screen text as-is):
    pbpaste | python3 add_training.py --paste
    python3 add_training.py --paste < paste.txt

Manual session:
    python3 add_training.py --date 17.8 --duration 31 --kcal 384 --watt 218 \
        --hf 117 --rr 131/79/90 --note "Freitext"

Rest-day BP-only log:
    python3 add_training.py --date 17.8 --rest --rr-ruhe "143/86/71 (12:46)"

Common flags:
    --html PATH     path to training.html (default: repo root, auto-detected)
    --no-pdf        skip PDF regeneration
    --dry-run       compute everything, print the summary, write nothing
    --rec           mark the session as a highlighted "sehr gute Leistung" row
"""
import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

WATT_PER_KCALMIN = 17.4375  # (1000*4.185)/60*0.25 — see CLAUDE.md
WEEKDAYS_DE = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
START_DATE = datetime.date(2026, 5, 25)  # week 1 start — fixed anchor
WEEK_COLOR_PALETTE = [
    '#DB2777', '#0891B2', '#65A30D', '#EA580C', '#7C3AED', '#0D9488',
    '#CA8A04', '#0369A1', '#B91C1C', '#4D7C0F', '#9333EA', '#0F766E',
]
JS_VAR_COLORS = {1: 'C1', 2: 'C2', 3: 'C3', 4: 'C4', 5: 'C5', 6: 'C6', 7: 'C7'}
CSS_VAR_COLORS = {1: 'var(--C1)', 2: 'var(--C2)', 3: 'var(--C3)', 4: 'var(--C4)',
                  5: 'var(--C5)', 6: 'var(--C6)', 7: 'var(--C7)'}
# Actual hex values behind C1..C7 (needed for wkColors/wkColArr, which use the
# bare var names for 1-7 but literal hex from week 8 on — see training.html).
C_HEX = {1: '#185FA5', 2: '#1D9E75', 3: '#BA7517', 4: '#533AB7', 5: '#A32D2D',
         6: '#6B21A8', 7: '#0F766E'}


def repo_root() -> Path:
    """.claude/skills/add-training/scripts/add_training.py -> repo root."""
    return Path(__file__).resolve().parents[4]


def parse_date_str(s: str) -> datetime.date:
    """'D.M' (no year, no leading zeros) -> date in 2026."""
    d, m = s.split('.')
    return datetime.date(2026, int(m), int(d))


def fmt_date(d: datetime.date) -> str:
    return f"{d.day}.{d.month}"


def week_of(d: datetime.date) -> int:
    return (d - START_DATE).days // 7 + 1


def week_range(w: int) -> tuple:
    start = START_DATE + datetime.timedelta(days=7 * (w - 1))
    end = start + datetime.timedelta(days=6)
    return start, end


def week_label_range(w: int) -> str:
    s, e = week_range(w)
    return f"{fmt_date(s)}–{fmt_date(e)}"


# --------------------------------------------------------------------------
# Kinomap paste parsing
# --------------------------------------------------------------------------

def parse_kinomap(text: str) -> dict:
    lines = [l.strip() for l in text.splitlines()]
    lines = [l for l in lines if l != '']

    if not lines:
        raise ValueError("Leerer Kinomap-Text.")

    # first line: 'DD.MM.YYYY HH:MM'
    m = re.match(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', lines[0])
    if not m:
        raise ValueError(f"Kann Datum nicht aus erster Zeile lesen: {lines[0]!r}")
    day, mon = int(m.group(1)), int(m.group(2))
    date = datetime.date(2026, mon, day)

    # route name = the line immediately before the first purely-numeric line
    route = None
    for i in range(1, len(lines)):
        if re.match(r'^\d+([.,]\d+)?$', lines[i]):
            route = lines[i - 1]
            break

    def find_num_after(label: str, unit: str = None) -> float:
        for i, l in enumerate(lines):
            if l == label:
                for j in range(i + 1, min(i + 3, len(lines))):
                    mm = re.match(r'^([\d.,]+)', lines[j])
                    if mm:
                        return float(mm.group(1).replace(',', '.'))
        return None

    def find_after_avr_max(label: str):
        """For blocks like 'Leistung\\nAvr.\\n218 Watts\\nMax.\\n364 Watts'."""
        for i, l in enumerate(lines):
            if l == label:
                avr = mx = None
                for j in range(i + 1, min(i + 6, len(lines))):
                    if lines[j] == 'Avr.' and j + 1 < len(lines):
                        mm = re.match(r'^([\d.,]+)', lines[j + 1])
                        if mm:
                            avr = float(mm.group(1).replace(',', '.'))
                    if lines[j] == 'Max.' and j + 1 < len(lines):
                        mm = re.match(r'^([\d.,]+)', lines[j + 1])
                        if mm:
                            mx = float(mm.group(1).replace(',', '.'))
                    if lines[j] in ('Entfernung', 'Erhebungen', 'Dauer', 'Kalorien',
                                     'Ausstattung', 'Kadenz', 'Geschwindigkeit', 'Leistung'):
                        if j > i + 1:
                            break
                return avr, mx
        return None, None

    watt_avg, watt_max = find_after_avr_max('Leistung')
    speed_avg, speed_max = find_after_avr_max('Geschwindigkeit')
    cad_avg, cad_max = find_after_avr_max('Kadenz')
    distance_km = find_num_after('Entfernung')
    elevation_m = find_num_after('Erhebungen')
    kcal = find_num_after('Kalorien')

    dauer_str = None
    for i, l in enumerate(lines):
        if l == 'Dauer' and i + 1 < len(lines):
            dauer_str = lines[i + 1]
            break

    ausstattung = None
    for i, l in enumerate(lines):
        if l == 'Ausstattung' and i + 1 < len(lines):
            ausstattung = lines[i + 1]
            break

    if watt_avg is None or kcal is None or dauer_str is None:
        missing = [n for n, v in [('Leistung/Avr.', watt_avg), ('Kalorien', kcal),
                                    ('Dauer', dauer_str)] if v is None]
        raise ValueError(f"Kinomap-Text unvollständig, fehlt: {', '.join(missing)}")

    # duration hh:mm:ss -> minutes (float, precise)
    parts = [int(p) for p in dauer_str.split(':')]
    if len(parts) == 3:
        h, mi, se = parts
    elif len(parts) == 2:
        h, mi, se = 0, parts[0], parts[1]
    else:
        raise ValueError(f"Kann Dauer nicht parsen: {dauer_str!r}")
    dur_min_precise = h * 60 + mi + se / 60

    pulse = temp = bldr = None
    extra_note = None
    if ausstattung:
        pm = re.search(r'puls[e]? (\d+)', ausstattung)
        tm = re.search(r'temp (\d+)', ausstattung)
        bm = re.search(r'(?:bldr|blrd) ([\d/]+)', ausstattung)
        pulse = int(pm.group(1)) if pm else None
        temp = int(tm.group(1)) if tm else None
        bldr = bm.group(1) if bm else None
        # anything after the last of pulse/temp/bldr is free-text commentary
        last_end = max((mm.end() for mm in (pm, tm, bm) if mm), default=None)
        if last_end is not None:
            extra_note = ausstattung[last_end:].strip()
        extra_note = extra_note or None

    return dict(
        date=date, route=route, distance_km=distance_km, elevation_m=elevation_m,
        kcal=round(kcal), dur_min_precise=dur_min_precise, watt_avg=watt_avg,
        watt_max=watt_max, cad_avg=cad_avg, speed_max=speed_max, temp=temp,
        pulse=pulse, bldr=bldr, extra_note=extra_note,
    )


def format_duration_exact(dur_min_precise: float) -> str:
    """Exact 'M min' or 'M min Ss' from precise minutes, no rounding to whole minutes."""
    total_seconds = round(dur_min_precise * 60)
    mins, secs = divmod(total_seconds, 60)
    return f"{mins} min" if secs == 0 else f"{mins} min {secs}s"


def build_kinomap_row(k: dict, rec: bool) -> dict:
    watt = round(k['watt_avg'])
    dur_min = round(k['dur_min_precise'])
    dur_str = format_duration_exact(k['dur_min_precise'])
    kpm = round(k['kcal'] / k['dur_min_precise'], 1) if k['dur_min_precise'] else None

    note_parts = ['Kinomap']
    if k['route']:
        note_parts.append(k['route'])
    if k['distance_km'] is not None:
        note_parts.append(f"{k['distance_km']:.2f}km")
    if k['cad_avg'] is not None:
        note_parts.append(f"Ø{round(k['cad_avg'])}rpm")
    if k['watt_max'] is not None:
        note_parts.append(f"Max {round(k['watt_max'])}W")
    if k['elevation_m'] is not None:
        note_parts.append(f"{round(k['elevation_m'])}Hm")
    if k['speed_max'] is not None:
        note_parts.append(f"Max {k['speed_max']:.1f}km/h")
    if k['temp'] is not None:
        note_parts.append(f"{k['temp']}°C")
    note = ' · '.join(note_parts)
    if k['extra_note']:
        note += f" · {k['extra_note']}"

    return dict(
        date=k['date'], dur_min=dur_min, dur_str=dur_str, kcal=k['kcal'], kpm=kpm, watt=watt,
        hf=k['pulse'], rr=k['bldr'], rr_ruhe=None, rr_abend=None, note=note,
        rec=rec, rest=False,
    )


# --------------------------------------------------------------------------
# JS array parsing (brace-balanced, respects escaped quotes in JS strings)
# --------------------------------------------------------------------------

def split_js_objects(body: str) -> list:
    """Split 'rows=[ {...}, {...} ]' body into a list of '{...}' strings."""
    out = []
    depth = 0
    start = None
    in_str = False
    quote = None
    i = 0
    while i < len(body):
        c = body[i]
        if in_str:
            if c == '\\':
                i += 2
                continue
            if c == quote:
                in_str = False
        else:
            if c in ("'", '"'):
                in_str = True
                quote = c
            elif c == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0 and start is not None:
                    out.append(body[start:i + 1])
                    start = None
        i += 1
    return out


def js_str(v) -> str:
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def parse_row_obj(obj_text: str) -> dict:
    def g(key, quoted=True):
        if quoted:
            m = re.search(key + r":'((?:[^'\\]|\\.)*)'", obj_text)
            return m.group(1).replace("\\'", "'") if m else None
        else:
            m = re.search(key + r':(-?\d+(?:\.\d+)?|null)', obj_text)
            if not m:
                return None
            return None if m.group(1) == 'null' else float(m.group(1))

    def g_int(key):
        v = g(key, quoted=False)
        return None if v is None else int(v)

    return dict(
        n=g('n'), w=g_int('w'), tag=g('tag'), dat=g('dat'),
        dur=g('dur'), k=g_int('k'), kpm=g('kpm', quoted=False),
        watt=g('watt'), hf=g_int('hf'), kum=g_int('kum'),
        rec=('rec:true' in obj_text), rr=g('rr'), rr_ruhe=g('rr_ruhe'),
        rr_abend=g('rr_abend'), note=g('note'),
    )


def serialize_row(r: dict) -> str:
    parts = [f"n:{js_str(r['n'])}", f"w:{r['w']}", f"tag:{js_str(r['tag'])}",
              f"dat:{js_str(r['dat'])}", f"dur:{js_str(r['dur'])}"]
    parts.append(f"k:{'null' if r['k'] is None else int(r['k'])}")
    parts.append(f"kpm:{'null' if r['kpm'] is None else r['kpm']}")
    parts.append(f"watt:{js_str(r['watt'])}")
    parts.append(f"hf:{'null' if r['hf'] is None else int(r['hf'])}")
    parts.append(f"kum:{int(r['kum'])}")
    if r.get('rec'):
        parts.append('rec:true')
    if r.get('rr'):
        parts.append(f"rr:{js_str(r['rr'])}")
    if r.get('rr_ruhe'):
        parts.append(f"rr_ruhe:{js_str(r['rr_ruhe'])}")
    if r.get('rr_abend'):
        parts.append(f"rr_abend:{js_str(r['rr_abend'])}")
    if r.get('note'):
        parts.append(f"note:{js_str(r['note'])}")
    return '{' + ', '.join(parts) + '}'


# --------------------------------------------------------------------------
# training.html read / extract / rebuild
# --------------------------------------------------------------------------

class Html:
    def __init__(self, path: Path):
        self.path = path
        self.text = path.read_text()

    def section(self, start_marker: str, end_marker: str = '\n];'):
        i = self.text.index(start_marker) + len(start_marker)
        j = self.text.index(end_marker, i)
        return i, j, self.text[i:j]

    def replace_section(self, start_marker: str, new_body: str, end_marker: str = '\n];'):
        i, j, _old = self.section(start_marker, end_marker)
        self.text = self.text[:i] + new_body + self.text[j:]


def extract_raw(html: Html) -> list:
    _, _, body = html.section('const raw=[')
    out = []
    for m in re.finditer(r"\['([\d.]+)',(-?\d+),(-?\d+),(-?\d+|null),(\d+)\]", body):
        d, w, k, p, wk = m.groups()
        out.append(dict(dat=d, watt=int(w), kcal=int(k),
                         hf=None if p == 'null' else int(p), w_num=int(wk)))
    return out


def extract_rows(html: Html) -> list:
    _, _, body = html.section('const rows=[')
    return [parse_row_obj(o) for o in split_js_objects(body)]


# --------------------------------------------------------------------------
# Building the new row/raw entries
# --------------------------------------------------------------------------

def next_n(rows: list) -> str:
    best = 0
    for r in rows:
        if r['n'] and re.match(r'^\d+$', r['n']):
            best = max(best, int(r['n']))
    return str(best + 1)


def build_new_row(session: dict, rows: list, running_kum: int) -> dict:
    d = session['date']
    tag = WEEKDAYS_DE[d.weekday()]
    w = week_of(d)
    dat = fmt_date(d)

    if session['rest']:
        return dict(n='–', w=w, tag=tag, dat=dat, dur='–', k=None, kpm=None,
                    watt='–', hf=None, kum=running_kum, rec=False,
                    rr=session.get('rr'), rr_ruhe=session.get('rr_ruhe'),
                    rr_abend=session.get('rr_abend'), note=session.get('note'))

    kcal = session['kcal']
    kum = running_kum + kcal
    return dict(n=next_n(rows), w=w, tag=tag, dat=dat,
                dur=session.get('dur_str') or f"{session['dur_min']} min", k=kcal, kpm=session['kpm'],
                watt=f"{session['watt']}W", hf=session.get('hf'), kum=kum,
                rec=session.get('rec', False), rr=session.get('rr'),
                rr_ruhe=session.get('rr_ruhe'), rr_abend=session.get('rr_abend'),
                note=session.get('note'))


# --------------------------------------------------------------------------
# Full re-derivation of every dependent section from raw/rows
# --------------------------------------------------------------------------

def week_color_hex(w: int) -> str:
    if w in C_HEX:
        return C_HEX[w]
    idx = (w - 8) % len(WEEK_COLOR_PALETTE)
    return WEEK_COLOR_PALETTE[idx]


def week_color_js(w: int) -> str:
    """For JS array/object literals: bare identifier C1..C7, quoted hex from 8+."""
    if w in JS_VAR_COLORS:
        return JS_VAR_COLORS[w]
    return js_str(week_color_hex(w))


def week_color_css(w: int) -> str:
    """For HTML style="background:..." attributes: var(--Cn) for 1-7, hex from 8+."""
    return CSS_VAR_COLORS.get(w, week_color_hex(w))


def rebuild(html: Html, raw: list, rows: list, dry_run: bool) -> dict:
    real_rows = [r for r in rows if r['n'] != '–']
    max_week = max(r['w_num'] for r in raw) if raw else 1
    end_date = parse_date_str(raw[-1]['dat']) if raw else START_DATE

    # ---- hero metrics ----
    total_kcal = sum(r['k'] for r in real_rows if r['k'] is not None)
    total_min = sum(int(re.match(r'-?\d+', r['dur']).group()) for r in real_rows if r['dur'] != '–')
    watts = []
    for r in real_rows:
        mm = re.match(r'~?(\d+)', r['watt'] or '')
        if mm:
            watts.append(int(mm.group(1)))
    avg_watt = round(sum(watts) / len(watts)) if watts else 0
    avg_kpm = round(total_kcal / total_min, 1) if total_min else 0
    einheiten = len(real_rows)
    letzte = fmt_date(end_date)

    html.text = re.sub(
        r'(<div class="metric"><div class="metric-lbl">Einheiten</div><div class="metric-val">)\d+',
        r'\g<1>' + str(einheiten), html.text)
    html.text = re.sub(
        r'(<div class="metric"><div class="metric-lbl">Kalorien total</div><div class="metric-val">)\d+',
        r'\g<1>' + str(total_kcal), html.text)
    html.text = re.sub(
        r'(<div class="metric"><div class="metric-lbl">Dauer total</div><div class="metric-val">)\d+',
        r'\g<1>' + str(total_min), html.text)
    html.text = re.sub(
        r'(<div class="metric"><div class="metric-lbl">Ø kcal/min</div><div class="metric-val">)[\d.]+',
        r'\g<1>' + str(avg_kpm), html.text)
    html.text = re.sub(
        r'(Ø Watt</div><div class="metric-val">)~?\d+',
        r'\g<1>~' + str(avg_watt), html.text)
    html.text = re.sub(
        r'(Letzte Einheit</div><div class="metric-val">)[\d.]+',
        r'\g<1>' + letzte, html.text)

    # ---- weekly rollups ----
    wk_kcal, wk_min, wk_watt, wk_kpm = {}, {}, {}, {}
    for w in range(1, max_week + 1):
        sess = [x for x in raw if x['w_num'] == w]
        k = sum(x['kcal'] for x in sess)
        durs = [r for r in real_rows if r['w'] == w]
        mn = sum(int(re.match(r'-?\d+', r['dur']).group()) for r in durs if r['dur'] != '–')
        wk_kcal[w] = k
        wk_min[w] = mn
        kpm = round(k / mn, 1) if mn else 0.0
        wk_kpm[w] = kpm
        wk_watt[w] = round(kpm * WATT_PER_KCALMIN)

    def num_list(d):
        return '[' + ','.join(str(d[w]) for w in range(1, max_week + 1)) + ']'

    html.replace_section('const wkKcal=[', num_list(wk_kcal)[1:-1], end_marker='];')
    html.replace_section('const wkMin=[', num_list(wk_min)[1:-1], end_marker='];')
    html.replace_section('const wkWatt=[', num_list(wk_watt)[1:-1], end_marker='];')
    html.replace_section('const wkKpm=[', num_list(wk_kpm)[1:-1], end_marker='];')

    labels = []
    for w in range(1, max_week + 1):
        if w == max_week:
            s, _e = week_range(w)
            labels.append(f"W{w}  ab {fmt_date(s)}")
        else:
            labels.append(f"W{w}  {week_label_range(w)}")
    html.replace_section('const wkLabels=[',
                          ','.join(js_str(l) for l in labels), end_marker='];')

    wn_entries = []
    for w in range(1, max_week + 1):
        if w == max_week:
            s, _e = week_range(w)
            wn_entries.append(f"{w}:'Woche {w} · ab {fmt_date(s)}'")
        else:
            wn_entries.append(f"{w}:'Woche {w} · {week_label_range(w)}'")
    html.replace_section('const wn={', ','.join(wn_entries), end_marker='};')

    colors = [week_color_js(w) for w in range(1, max_week + 1)]
    html.replace_section('const wkColors={',
                          ','.join(f"{w}:{c}" for w, c in zip(range(1, max_week + 1), colors)),
                          end_marker='};')
    html.replace_section('const wkColArr=[', ','.join(colors), end_marker='];')

    # ---- daily-tab week legend (Täglich tab) ----
    def legend_line(w):
        return (f'      <div class="li"><div class="dot" style="background:'
                f'{week_color_css(w)}"></div>W{w}</div>')
    legend_body = '\n'.join(legend_line(w) for w in range(1, max_week + 1))
    m = re.search(r'(<div class="legend">\n)(.*?)(\n\s*<div class="li"><div class="dot" '
                  r'style="background:rgba\(136,135,128,0\.2\)"></div>Ruhetag</div>)',
                  html.text, re.S)
    if m:
        html.text = html.text[:m.start(2)] + legend_body + html.text[m.end(2):]

    # ---- subtitle ----
    month_de = ['', 'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli',
                'August', 'September', 'Oktober', 'November', 'Dezember']
    sub_end = f"{end_date.day}. {month_de[end_date.month]} 2026"
    html.text = re.sub(
        r'(<p class="sub">25\. Mai – )[^·]+( · Woche 1–)\d+',
        lambda m2: m2.group(1) + sub_end + m2.group(2) + str(max_week),
        html.text)

    # ---- endDate ----
    html.text = re.sub(
        r"(const startDate=parseDate\('25\.5'\),endDate=parseDate\(')[\d.]+('\))",
        r'\g<1>' + fmt_date(end_date) + r'\g<2>', html.text)

    # ---- getWeek(): ensure formula form (one-time refactor, idempotent) ----
    formula_fn = (
        "function getWeek(s){\n"
        "  const d=parseDate(s);\n"
        "  return Math.floor((d-parseDate('25.5'))/86400000/7)+1;\n"
        "}"
    )
    html.text = re.sub(r"function getWeek\(s\)\{.*?\n\}", formula_fn, html.text, count=1, flags=re.S)

    # ---- table tfoot Total row ----
    html.text = re.sub(
        r'(<td colspan="3">Total</td>\s*<td class="r">)\d+( min</td><td class="r">)\d+'
        r'(</td>\s*<td class="r">)[\d.]+(</td><td class="r">)~?\d+( W</td>\s*'
        r'<td class="r">–</td><td class="r">–</td><td class="r">–</td><td class="r">–</td>'
        r'<td class="r">)\d+(</td>)',
        lambda m2: (m2.group(1) + str(total_min) + m2.group(2) + str(total_kcal) +
                    m2.group(3) + str(avg_kpm) + m2.group(4) + '~' + str(avg_watt) +
                    m2.group(5) + str(total_kcal) + m2.group(6)),
        html.text)

    # ---- kumData: full day-by-day rebuild from raw ----
    kcal_by_day = {}
    for x in raw:
        kcal_by_day[x['dat']] = kcal_by_day.get(x['dat'], 0) + x['kcal']
    days = []
    cur = START_DATE
    while cur <= end_date:
        days.append(fmt_date(cur))
        cur += datetime.timedelta(days=1)
    running = 0
    kum_data = []
    for d in days:
        running += kcal_by_day.get(d, 0)
        kum_data.append(running)
    assert len(kum_data) == len(days), "kumData/allDays length mismatch"
    html.replace_section('const kumData=[', ','.join(str(x) for x in kum_data), end_marker='];')

    # ---- axis range headroom ----
    def bump(pattern, current_max, actual_max, step=10, headroom=1.08):
        if actual_max <= current_max:
            return current_max
        import math
        return int(math.ceil(actual_max * headroom / step) * step)

    max_watt_val = max(watts) if watts else 0
    max_kcal_val = max((r['k'] for r in real_rows if r['k'] is not None), default=0)
    max_min_val = max((int(re.match(r'-?\d+', r['dur']).group()) for r in real_rows
                        if r['dur'] != '–'), default=0)
    max_hf_val = max((r['hf'] for r in real_rows if r['hf']), default=0)

    def bump_by(label, actual_max):
        mo = re.search(re.escape(f"by('{label}',") + r'(-?\d+),(-?\d+)\)', html.text)
        if not mo:
            return
        mn, mx = int(mo.group(1)), int(mo.group(2))
        new_mx = bump(label, mx, actual_max)
        if new_mx != mx:
            html.text = html.text[:mo.start()] + f"by('{label}',{mn},{new_mx})" + html.text[mo.end():]

    bump_by('Ø Watt', max_watt_val)
    bump_by('kcal', max_kcal_val)
    bump_by('Minuten', max_min_val)
    bump_by('Max bpm', max_hf_val)

    def bump_inline_max(marker, actual_max):
        mo = re.search(re.escape(marker) + r'max:(\d+)', html.text)
        if not mo:
            return
        mx = int(mo.group(1))
        new_mx = bump(marker, mx, actual_max)
        if new_mx != mx:
            html.text = (html.text[:mo.start()] + marker + f"max:{new_mx}" +
                         html.text[mo.end():])

    max_wk_watt_val = max(wk_watt.values()) if wk_watt else 0
    bump_inline_max("y:{min:130,", max_wk_watt_val)
    bump_inline_max("y:{min:140,", max_wk_watt_val)

    # ---- cWeekPerf: labels + current-week marker ----
    wp_labels = ','.join(f"'W{w}'" for w in range(1, max_week + 1))
    html.text = re.sub(
        r"(new Chart\(document\.getElementById\('cWeekPerf'\),\{type:'bar',data:\{\n  labels:)\[[^\]]*\]",
        lambda m2: m2.group(1) + '[' + wp_labels + ']', html.text)

    # ---- cPlan: Erreicht series + labels + x-axis current-week highlight ----
    plan_len = max(16, max_week + 3)
    erreicht = [wk_watt.get(w) for w in range(1, plan_len + 1)]
    erreicht_js = ','.join('null' if v is None else str(v) for v in erreicht)
    html.text = re.sub(
        r"(\{label:'Erreicht',data:)\[[^\]]*\]",
        lambda m2: m2.group(1) + '[' + erreicht_js + ']', html.text)

    plan_labels = []
    for w in range(1, plan_len + 1):
        plan_labels.append(f"W{w} (jetzt)" if w == max_week else f"W{w}")
    plan_labels_js = ','.join(js_str(l) for l in plan_labels)
    html.text = re.sub(
        r"(new Chart\(document\.getElementById\('cPlan'\),\{type:'line',data:\{\n  labels:)\[[^\]]*\]",
        lambda m2: m2.group(1) + '[' + plan_labels_js + ']', html.text)

    html.text = re.sub(
        r'ctx\.index===\d+\?C3:tc', f'ctx.index==={max_week - 1}?C3:tc', html.text)
    html.text = re.sub(
        r"ctx\.index===\d+\?\{size:10,weight:'bold'\}:\{size:10\}",
        f"ctx.index==={max_week - 1}?{{size:10,weight:'bold'}}:{{size:10}}", html.text)

    # Ziel/200W datasets must stay exactly as long as labels/Erreicht, or
    # Chart.js silently stops plotting them past their own array length.
    zm = re.search(r"\{label:'Ziel',data:\[([^\]]*)\]", html.text)
    if zm:
        ziel_vals = [None if v.strip() == 'null' else float(v) for v in zm.group(1).split(',')]
        last = next((v for v in reversed(ziel_vals) if v is not None), 200)
        while len(ziel_vals) < plan_len:
            ziel_vals.append(last)
        ziel_js = ','.join('null' if v is None else str(int(v) if v == int(v) else v)
                            for v in ziel_vals[:plan_len])
        html.text = (html.text[:zm.start()] + "{label:'Ziel',data:[" + ziel_js + ']' +
                     html.text[zm.end():])
    html.text = re.sub(r"\{label:'200W',data:Array\(\d+\)\.fill\(200\)",
                        f"{{label:'200W',data:Array({plan_len}).fill(200)", html.text)

    return dict(einheiten=einheiten, total_kcal=total_kcal, total_min=total_min,
                avg_kpm=avg_kpm, avg_watt=avg_watt, letzte=letzte, max_week=max_week,
                wk_kcal=wk_kcal, wk_min=wk_min, wk_watt=wk_watt)


# --------------------------------------------------------------------------
# PDF regeneration (documented recipe from CLAUDE.md)
# --------------------------------------------------------------------------

def regenerate_pdf(html_path: Path, pdf_path: Path, scratch: Path) -> dict:
    src = html_path.read_text()
    src = src.replace(".sec{display:none}", ".sec{display:block}")
    src = src.replace(
        "const isDark=matchMedia('(prefers-color-scheme: dark)').matches;",
        "const isDark=false;")
    print_html = scratch / 'training_print.html'
    print_html.write_text(src)

    out_pdf = scratch / 'final.pdf'
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    subprocess.run([
        chrome, '--headless', '--disable-gpu', '--no-pdf-header-footer',
        '--virtual-time-budget=8000', f'--print-to-pdf={out_pdf}',
        f'file://{print_html}',
    ], capture_output=True, timeout=60)

    if not out_pdf.exists():
        raise RuntimeError("Chrome hat kein PDF erzeugt.")

    pdf_path.write_bytes(out_pdf.read_bytes())

    pages = None
    try:
        info = subprocess.run(['pdfinfo', str(pdf_path)], capture_output=True, text=True)
        mo = re.search(r'Pages:\s+(\d+)', info.stdout)
        pages = int(mo.group(1)) if mo else None
    except FileNotFoundError:
        pass
    return dict(pages=pages)


def node_syntax_check_text(text: str) -> bool:
    """Syntax-check the page's inline <script> block by feeding it to node."""
    m = re.search(r'<script>([\s\S]*?)</script>', text)
    if not m:
        return False
    r = subprocess.run(
        ['node', '-e', "const s=require('fs').readFileSync(0,'utf8');new Function(s);console.log('OK')"],
        input=m.group(1), capture_output=True, text=True)
    return r.returncode == 0 and 'OK' in r.stdout


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_session_from_args(args) -> dict:
    if args.paste is not None:
        k = parse_kinomap(args.paste)
        row = build_kinomap_row(k, rec=args.rec)
        row['rest'] = False
        return row

    if not args.date:
        raise ValueError("--date ist erforderlich (oder --paste verwenden).")
    date = parse_date_str(args.date)

    if args.rest:
        if not (args.rr_ruhe or args.rr or args.rr_abend):
            raise ValueError("Ruhetag-Log braucht mindestens --rr-ruhe/--rr/--rr-abend.")
        return dict(date=date, rest=True, rr=args.rr, rr_ruhe=args.rr_ruhe,
                    rr_abend=args.rr_abend, note=args.note)

    if args.kcal is None or args.duration is None:
        raise ValueError("Manuelle Session braucht --kcal und --duration (Minuten).")
    kcal = args.kcal
    dur_min = args.duration
    if args.watt is not None:
        watt = args.watt
        kpm = round(kcal / dur_min, 1)
    else:
        kpm = round(kcal / dur_min, 1)
        watt = round(kpm * WATT_PER_KCALMIN)

    return dict(date=date, rest=False, kcal=kcal, dur_min=dur_min, kpm=kpm,
                watt=watt, hf=args.hf, rr=args.rr, rr_ruhe=args.rr_ruhe,
                rr_abend=args.rr_abend, note=args.note, rec=args.rec)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--paste', nargs='?', const='__STDIN__',
                     help="Kinomap-Session-Text (ohne Wert: von stdin lesen)")
    ap.add_argument('--date', help="D.M, z.B. 17.8")
    ap.add_argument('--rest', action='store_true', help="Nur Ruhetag/BP-Log, keine Session")
    ap.add_argument('--duration', type=int, help="Minuten")
    ap.add_argument('--kcal', type=int)
    ap.add_argument('--watt', type=int)
    ap.add_argument('--hf', type=int)
    ap.add_argument('--rr', help="RR nach Training, z.B. 131/79/90")
    ap.add_argument('--rr-ruhe', dest='rr_ruhe', help="RR Ruhe, z.B. '143/86/71 (12:46)'")
    ap.add_argument('--rr-abend', dest='rr_abend')
    ap.add_argument('--note', help="Freitext-Notiz (für manuelle Sessions)")
    ap.add_argument('--rec', action='store_true', help="Als 'sehr gute Leistung' markieren")
    ap.add_argument('--html', help="Pfad zu training.html (default: Repo-Root)")
    ap.add_argument('--no-pdf', action='store_true', help="PDF-Regenerierung überspringen")
    ap.add_argument('--dry-run', action='store_true', help="Nichts schreiben, nur anzeigen")
    args = ap.parse_args()

    if args.paste == '__STDIN__':
        args.paste = sys.stdin.read()

    html_path = Path(args.html) if args.html else repo_root() / 'training.html'
    pdf_path = html_path.with_suffix('.pdf')
    if not html_path.exists():
        print(json.dumps({"error": f"training.html nicht gefunden: {html_path}"}))
        sys.exit(1)

    try:
        session = build_session_from_args(args)
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    html = Html(html_path)
    raw = extract_raw(html)
    rows = extract_rows(html)

    last_date = parse_date_str(raw[-1]['dat']) if raw else START_DATE
    if session['date'] < last_date:
        print(json.dumps({"error": (
            f"Datum {fmt_date(session['date'])} liegt vor dem letzten "
            f"geloggten Datum {fmt_date(last_date)} — out-of-order-Einfügen "
            "wird von diesem Skript nicht unterstützt.")}))
        sys.exit(1)

    running_kum = sum(x['kcal'] for x in raw)
    new_row = build_new_row(session, rows, running_kum)

    # --- append to rows array ---
    # rstrip first so the result doesn't depend on how much trailing
    # whitespace `end_marker` happened to leave in the body — always end in
    # exactly ",\n" right before the marker, matching the file's own style.
    END = '];'
    _i, _j, rows_body = html.section('const rows=[', end_marker=END)
    rows_body = rows_body.rstrip()
    sep = '\n  ' if rows_body else '  '
    new_rows_body = rows_body + sep + serialize_row(new_row) + ',\n'
    html.replace_section('const rows=[', new_rows_body, end_marker=END)

    # --- append to raw array (skip rest days) ---
    if not session['rest']:
        _i, _j, raw_body = html.section('const raw=[', end_marker=END)
        raw_body = raw_body.rstrip()
        w = week_of(session['date'])
        entry = f"['{fmt_date(session['date'])}',{new_row['watt'].rstrip('W')},{new_row['k']},{new_row['hf'] if new_row['hf'] is not None else 'null'},{w}]"
        sep = '\n  ' if raw_body else '  '
        new_raw_body = raw_body + sep + entry + ',\n'
        html.replace_section('const raw=[', new_raw_body, end_marker=END)

    # re-parse from the now-updated source of truth
    raw2 = extract_raw(html)
    rows2 = extract_rows(html)
    summary = rebuild(html, raw2, rows2, args.dry_run)

    if not node_syntax_check_text(html.text):
        print(json.dumps({"error": "Syntax-Check nach dem Edit fehlgeschlagen — nichts gespeichert."}))
        sys.exit(1)

    if args.dry_run:
        print(json.dumps({"dry_run": True, "session": {
            k: (str(v) if isinstance(v, datetime.date) else v) for k, v in session.items()
        }, "summary": summary}, indent=2, default=str))
        return

    html.path.write_text(html.text)

    result = {"written": str(html_path), "summary": summary}

    if not args.no_pdf:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            pdf_result = regenerate_pdf(html_path, pdf_path, Path(td))
        result['pdf'] = {"path": str(pdf_path), **pdf_result}

    print(json.dumps(result, indent=2, default=str))


if __name__ == '__main__':
    main()

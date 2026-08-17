#!/usr/bin/env python3
"""
export_csv.py — regenerate training_data.csv and kinomap_rennwerte.csv from
the `rows` array in training.html (source of truth), with no LLM involved.

Both CSVs are documented in CLAUDE.md as manually-maintained snapshots that
tend to drift out of sync with training.html. This script removes that by
recomputing them fresh every time, matching the two files' existing,
already-established column schemas exactly:

  training_data.csv:
    Nr,Tag,Datum,Dauer_min,kcal,kcal_min,Watt,Max_HF,Woche
    (one row per real session, in order; Nr is a plain 1..N recount — it
    does NOT reuse training.html's `n` labels, which include letter suffixes
    like '9b' and special tags like 'ST'. kcal_min = round(kcal/Dauer_min,2),
    freshly computed — NOT copied from the row's stored `kpm`, which is
    rounded to 1 decimal for display. This matches the existing file's own
    convention.)

  kinomap_rennwerte.csv:
    Datum,Tag,Strecke,Distanz_km,Dauer_min,kcal,kcal_min,Watt_Avg,Watt_Max,
    Max_HF,Hoehenmeter,Temperatur_C,Kadenz_rpm,Woche
    (only rows whose note starts with "Kinomap"; kcal_min here IS the row's
    stored `kpm` verbatim, matching the existing file's convention — the two
    CSVs use different kcal_min conventions and this script deliberately
    keeps each one's own. Strecke/Distanz_km/Watt_Max/Hoehenmeter/
    Temperatur_C/Kadenz_rpm are parsed out of the free-text note; fields
    absent from an older/looser note format are left blank rather than
    guessed.)

Usage
-----
    python3 export_csv.py                  # regenerate both CSVs in the repo root
    python3 export_csv.py --dry-run         # compute and print counts, write nothing
    python3 export_csv.py --only training_data
    python3 export_csv.py --only kinomap
    python3 export_csv.py --html PATH --out-dir DIR
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path


def repo_root() -> Path:
    """.claude/skills/export-csv/scripts/export_csv.py -> repo root."""
    return Path(__file__).resolve().parents[4]


# --------------------------------------------------------------------------
# JS array parsing — same approach as add-training's script, duplicated
# rather than imported so this skill stays self-contained.
# --------------------------------------------------------------------------

def split_js_objects(body: str) -> list:
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


def parse_row_obj(obj_text: str) -> dict:
    def g(key, quoted=True):
        if quoted:
            m = re.search(key + r":'((?:[^'\\]|\\.)*)'", obj_text)
            return m.group(1).replace("\\'", "'") if m else None
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
        note=g('note'),
    )


def extract_rows(html_text: str) -> list:
    i = html_text.index('const rows=[') + len('const rows=[')
    j = html_text.index('\n];', i)
    return [parse_row_obj(o) for o in split_js_objects(html_text[i:j])]


# --------------------------------------------------------------------------
# training_data.csv
# --------------------------------------------------------------------------

def build_training_data_rows(rows: list) -> list:
    out = []
    nr = 0
    for r in rows:
        if r['n'] == '–':
            continue  # rest days carry no session data
        nr += 1
        dauer = int(re.match(r'-?\d+', r['dur']).group()) if r['dur'] != '–' else None
        watt = None
        if r['watt'] and r['watt'] != '–':
            mm = re.search(r'(\d+)', r['watt'])
            watt = int(mm.group(1)) if mm else None
        kcal_min = round(r['k'] / dauer, 2) if (r['k'] is not None and dauer) else ''
        out.append([
            nr, r['tag'], r['dat'], dauer if dauer is not None else '',
            r['k'] if r['k'] is not None else '', kcal_min,
            watt if watt is not None else '',
            r['hf'] if r['hf'] is not None else '', r['w'],
        ])
    return out


TRAINING_DATA_HEADER = ['Nr', 'Tag', 'Datum', 'Dauer_min', 'kcal', 'kcal_min',
                         'Watt', 'Max_HF', 'Woche']


# --------------------------------------------------------------------------
# kinomap_rennwerte.csv
# --------------------------------------------------------------------------

def parse_kinomap_note(note: str) -> dict:
    """Best-effort parse of the free-text Kinomap note. Older notes used a
    looser format (no route separator, or no route at all) — fields that
    aren't present are left None rather than guessed."""
    rest = note[len('Kinomap'):].lstrip(' ·').strip()
    parts = [p.strip() for p in rest.split('·') if p.strip()]

    route = dist_km = cadence = max_watt = elev_m = temp_c = None
    dist_idx = None
    for i, p in enumerate(parts):
        m = re.match(r'^([\d.]+)\s*km$', p)
        if m:
            dist_km = float(m.group(1))
            dist_idx = i
            break
    if dist_idx is not None and dist_idx > 0:
        route = parts[0]

    for p in parts:
        m = re.match(r'Ø(\d+)rpm', p)
        if m:
            cadence = int(m.group(1))
        m = re.match(r'Max (\d+)W', p)
        if m:
            max_watt = int(m.group(1))
        m = re.match(r'^(\d+)Hm', p)
        if m:
            elev_m = int(m.group(1))
        m = re.match(r'^(\d+)°C', p)
        if m:
            temp_c = int(m.group(1))

    return dict(route=route, dist_km=dist_km, cadence=cadence,
                max_watt=max_watt, elev_m=elev_m, temp_c=temp_c)


def build_kinomap_rows(rows: list) -> list:
    out = []
    for r in rows:
        if r['n'] == '–' or not r['note'] or not r['note'].startswith('Kinomap'):
            continue
        p = parse_kinomap_note(r['note'])
        dauer = int(re.match(r'-?\d+', r['dur']).group()) if r['dur'] != '–' else None
        watt_avg = None
        if r['watt'] and r['watt'] != '–':
            mm = re.search(r'(\d+)', r['watt'])
            watt_avg = int(mm.group(1)) if mm else None
        out.append([
            r['dat'], r['tag'], p['route'] or '',
            f"{p['dist_km']:.2f}" if p['dist_km'] is not None else '',
            dauer if dauer is not None else '',
            r['k'] if r['k'] is not None else '',
            r['kpm'] if r['kpm'] is not None else '',
            watt_avg if watt_avg is not None else '',
            p['max_watt'] if p['max_watt'] is not None else '',
            r['hf'] if r['hf'] is not None else '',
            p['elev_m'] if p['elev_m'] is not None else '',
            p['temp_c'] if p['temp_c'] is not None else '',
            p['cadence'] if p['cadence'] is not None else '',
            r['w'],
        ])
    return out


KINOMAP_HEADER = ['Datum', 'Tag', 'Strecke', 'Distanz_km', 'Dauer_min', 'kcal',
                   'kcal_min', 'Watt_Avg', 'Watt_Max', 'Max_HF', 'Hoehenmeter',
                   'Temperatur_C', 'Kadenz_rpm', 'Woche']


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def write_csv(path: Path, header: list, rows: list):
    with path.open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--html', help="Pfad zu training.html (default: Repo-Root)")
    ap.add_argument('--out-dir', help="Zielordner für die CSVs (default: Repo-Root)")
    ap.add_argument('--only', choices=['training_data', 'kinomap'],
                     help="Nur eine der beiden CSVs regenerieren")
    ap.add_argument('--dry-run', action='store_true', help="Nichts schreiben, nur zählen")
    args = ap.parse_args()

    html_path = Path(args.html) if args.html else repo_root() / 'training.html'
    out_dir = Path(args.out_dir) if args.out_dir else repo_root()

    if not html_path.exists():
        print(json.dumps({"error": f"training.html nicht gefunden: {html_path}"}))
        sys.exit(1)

    html_text = html_path.read_text()
    try:
        rows = extract_rows(html_text)
    except ValueError:
        print(json.dumps({"error": "const rows=[...] nicht in training.html gefunden."}))
        sys.exit(1)

    result = {}

    if args.only in (None, 'training_data'):
        td_rows = build_training_data_rows(rows)
        result['training_data'] = {"rows": len(td_rows)}
        if not args.dry_run:
            path = out_dir / 'training_data.csv'
            write_csv(path, TRAINING_DATA_HEADER, td_rows)
            result['training_data']['path'] = str(path)

    if args.only in (None, 'kinomap'):
        km_rows = build_kinomap_rows(rows)
        result['kinomap'] = {"rows": len(km_rows)}
        if not args.dry_run:
            path = out_dir / 'kinomap_rennwerte.csv'
            write_csv(path, KINOMAP_HEADER, km_rows)
            result['kinomap']['path'] = str(path)

    result['dry_run'] = args.dry_run
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
add_training.py — append one training session (or rest-day BP log) to
training_log.csv and regenerate training.pdf, with no LLM involved.

training_log.csv is the single source of truth (see CLAUDE.md). training.html
is static — it fetches and parses the CSV at load time and derives every
chart, table row, hero metric and weekly rollup from it. This script's only
job is to append one correctly-formed CSV row; it never touches
training.html.

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
    --csv PATH      path to training_log.csv (default: repo root, auto-detected)
    --no-pdf        skip PDF regeneration
    --dry-run       compute everything, print the summary, write nothing
    --rec           mark the session as a highlighted "sehr gute Leistung" row
"""
import argparse
import csv
import datetime
import json
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

WATT_PER_KCALMIN = 17.4375  # (1000*4.185)/60*0.25 — see CLAUDE.md
START_DATE = datetime.date(2026, 5, 25)  # week 1 start — fixed anchor

COLUMNS = [
    "Datum", "Nr", "Dauer_sek", "Kcal", "Watt", "MaxHF", "Rec",
    "RR_ruhe", "RR_training", "RR_abend",
    "Kinomap", "Strecke", "Distanz_km", "Kadenz_rpm",
    "Watt_Max", "Watt_Max_PR", "Hoehenmeter", "Temperatur_C", "Max_kmh",
    "Kommentar",
]


def repo_root() -> Path:
    """.claude/skills/add-training/scripts/add_training.py -> repo root."""
    return Path(__file__).resolve().parents[4]


def parse_date_str(s: str) -> datetime.date:
    """'D.M' (no year, no leading zeros) -> date in 2026."""
    d, m = s.split('.')
    return datetime.date(2026, int(m), int(d))


def fmt_date(d: datetime.date) -> str:
    return f"{d.day}.{d.month}"


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

    def find_num_after(label: str) -> float:
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
            # device name + telemetry/comment can span multiple lines
            ausstattung = ' '.join(lines[i + 1:])
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


def strip_excess_marker(s):
    """Out-of-band BP (systolic>140 or diastolic>90) is flagged automatically by
    training.html's own rendering script (bpCell/isExcess) — never stored as text.
    Strip a stray manually-typed warning emoji so it can't double up with that."""
    if not s:
        return s
    return re.sub(r'\s*⚠️\s*', ' ', s).strip()


# --------------------------------------------------------------------------
# CSV I/O
# --------------------------------------------------------------------------

def read_rows(csv_path: Path) -> list:
    with open(csv_path, newline='') as f:
        return list(csv.DictReader(f))


def next_nr(rows: list) -> str:
    best = 0
    for r in rows:
        if r['Nr'] and re.match(r'^\d+$', r['Nr']):
            best = max(best, int(r['Nr']))
    return str(best + 1)


def blank_row() -> dict:
    return {c: '' for c in COLUMNS}


def build_kinomap_csv_row(k: dict, rows: list, rec: bool) -> dict:
    row = blank_row()
    row['Datum'] = fmt_date(k['date'])
    row['Nr'] = next_nr(rows)
    row['Dauer_sek'] = str(round(k['dur_min_precise'] * 60))
    row['Kcal'] = str(k['kcal'])
    row['Watt'] = str(round(k['watt_avg']))
    if k['pulse'] is not None:
        row['MaxHF'] = str(k['pulse'])
    if rec:
        row['Rec'] = '1'
    if k['bldr']:
        row['RR_training'] = strip_excess_marker(k['bldr'])
    row['Kinomap'] = '1'
    if k['route']:
        row['Strecke'] = k['route']
    if k['distance_km'] is not None:
        row['Distanz_km'] = f"{k['distance_km']:.2f}"
    if k['cad_avg'] is not None:
        row['Kadenz_rpm'] = str(round(k['cad_avg']))
    if k['watt_max'] is not None:
        watt_max_val = round(k['watt_max'])
        row['Watt_Max'] = str(watt_max_val)
        prev_max = max((int(r['Watt_Max']) for r in rows if r['Watt_Max']), default=0)
        if watt_max_val > prev_max:
            row['Watt_Max_PR'] = '1'
    if k['elevation_m'] is not None:
        row['Hoehenmeter'] = str(round(k['elevation_m']))
    if k['temp'] is not None:
        row['Temperatur_C'] = str(k['temp'])
    if k['speed_max'] is not None:
        row['Max_kmh'] = f"{k['speed_max']:.1f}"
    if k['extra_note']:
        row['Kommentar'] = k['extra_note']
    return row


def build_manual_csv_row(args, rows: list) -> dict:
    date = parse_date_str(args.date)
    row = blank_row()
    row['Datum'] = fmt_date(date)
    row['Nr'] = next_nr(rows)
    row['Dauer_sek'] = str(args.duration * 60)
    row['Kcal'] = str(args.kcal)
    if args.watt is not None:
        row['Watt'] = str(args.watt)
    else:
        kpm = args.kcal / args.duration
        row['Watt'] = str(round(kpm * WATT_PER_KCALMIN))
    if args.hf is not None:
        row['MaxHF'] = str(args.hf)
    if args.rec:
        row['Rec'] = '1'
    if args.rr:
        row['RR_training'] = strip_excess_marker(args.rr)
    if args.rr_ruhe:
        row['RR_ruhe'] = strip_excess_marker(args.rr_ruhe)
    if args.rr_abend:
        row['RR_abend'] = strip_excess_marker(args.rr_abend)
    if args.note:
        row['Kommentar'] = args.note
    return row


def build_rest_csv_row(args) -> dict:
    date = parse_date_str(args.date)
    row = blank_row()
    row['Datum'] = fmt_date(date)
    row['Nr'] = '–'
    if args.rr:
        row['RR_training'] = strip_excess_marker(args.rr)
    if args.rr_ruhe:
        row['RR_ruhe'] = strip_excess_marker(args.rr_ruhe)
    if args.rr_abend:
        row['RR_abend'] = strip_excess_marker(args.rr_abend)
    if args.note:
        row['Kommentar'] = args.note
    return row


def append_row(csv_path: Path, row: dict):
    with open(csv_path, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writerow(row)


# --------------------------------------------------------------------------
# PDF regeneration — training.html now fetch()es the CSV, so file:// no
# longer works (Chrome blocks local-file fetch under file://). Serve the
# repo root over HTTP instead. See CLAUDE.md.
# --------------------------------------------------------------------------

def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def regenerate_pdf(html_path: Path, pdf_path: Path) -> dict:
    root = html_path.parent
    src = html_path.read_text()
    src = src.replace(".sec{display:none}", ".sec{display:block}")
    src = src.replace(
        "const isDark=matchMedia('(prefers-color-scheme: dark)').matches;",
        "const isDark=false;")
    print_html = root / '_training_print_tmp.html'
    print_html.write_text(src)

    port = free_port()
    server = subprocess.Popen(
        ['python3', '-m', 'http.server', str(port)],
        cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            try:
                urllib.request.urlopen(f'http://localhost:{port}/training_log.csv', timeout=0.5)
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("Lokaler HTTP-Server für PDF-Export ist nicht gestartet.")

        out_pdf = root / '_training_print_tmp.pdf'
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        subprocess.run([
            chrome, '--headless', '--disable-gpu', '--no-pdf-header-footer',
            '--virtual-time-budget=8000', f'--print-to-pdf={out_pdf}',
            f'http://localhost:{port}/_training_print_tmp.html',
        ], capture_output=True, timeout=60)

        if not out_pdf.exists():
            raise RuntimeError("Chrome hat kein PDF erzeugt.")
        pdf_path.write_bytes(out_pdf.read_bytes())
        out_pdf.unlink(missing_ok=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        print_html.unlink(missing_ok=True)

    pages = None
    try:
        info = subprocess.run(['pdfinfo', str(pdf_path)], capture_output=True, text=True)
        mo = re.search(r'Pages:\s+(\d+)', info.stdout)
        pages = int(mo.group(1)) if mo else None
    except FileNotFoundError:
        pass
    return dict(pages=pages)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

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
    ap.add_argument('--note', help="Freitext-Notiz (für manuelle/Ruhetag-Sessions)")
    ap.add_argument('--rec', action='store_true', help="Als 'sehr gute Leistung' markieren")
    ap.add_argument('--csv', help="Pfad zu training_log.csv (default: Repo-Root)")
    ap.add_argument('--no-pdf', action='store_true', help="PDF-Regenerierung überspringen")
    ap.add_argument('--dry-run', action='store_true', help="Nichts schreiben, nur anzeigen")
    args = ap.parse_args()

    if args.paste == '__STDIN__':
        args.paste = sys.stdin.read()

    csv_path = Path(args.csv) if args.csv else repo_root() / 'training_log.csv'
    html_path = csv_path.with_name('training.html')
    pdf_path = csv_path.with_name('training.pdf')
    if not csv_path.exists():
        print(json.dumps({"error": f"training_log.csv nicht gefunden: {csv_path}"}))
        sys.exit(1)

    rows = read_rows(csv_path)
    last = parse_date_str(rows[-1]['Datum']) if rows else START_DATE

    try:
        if args.paste is not None:
            k = parse_kinomap(args.paste)
            if k['date'] < last:
                raise ValueError(
                    f"Datum {fmt_date(k['date'])} liegt vor dem letzten geloggten "
                    f"Datum {fmt_date(last)} — out-of-order-Einfügen wird nicht unterstützt.")
            new_row = build_kinomap_csv_row(k, rows, rec=args.rec)
            session_date = k['date']

        elif args.rest:
            if not args.date:
                raise ValueError("--date ist erforderlich.")
            date = parse_date_str(args.date)
            if date < last:
                raise ValueError(
                    f"Datum {fmt_date(date)} liegt vor dem letzten geloggten "
                    f"Datum {fmt_date(last)} — out-of-order-Einfügen wird nicht unterstützt.")
            if not (args.rr_ruhe or args.rr or args.rr_abend):
                raise ValueError("Ruhetag-Log braucht mindestens --rr-ruhe/--rr/--rr-abend.")
            new_row = build_rest_csv_row(args)
            session_date = date

        else:
            if not args.date:
                raise ValueError("--date ist erforderlich (oder --paste verwenden).")
            date = parse_date_str(args.date)
            if date < last:
                raise ValueError(
                    f"Datum {fmt_date(date)} liegt vor dem letzten geloggten "
                    f"Datum {fmt_date(last)} — out-of-order-Einfügen wird nicht unterstützt.")
            if args.kcal is None or args.duration is None:
                raise ValueError("Manuelle Session braucht --kcal und --duration (Minuten).")
            new_row = build_manual_csv_row(args, rows)
            session_date = date
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    # running totals for the summary
    total_kcal = sum(int(r['Kcal']) for r in rows if r['Kcal']) + (
        int(new_row['Kcal']) if new_row['Kcal'] else 0)
    einheiten = sum(1 for r in rows if r['Kcal']) + (1 if new_row['Kcal'] else 0)
    watts = [int(r['Watt']) for r in rows if r['Watt']] + (
        [int(new_row['Watt'])] if new_row['Watt'] else [])
    avg_watt = round(sum(watts) / len(watts)) if watts else 0

    summary = dict(einheiten=einheiten, total_kcal=total_kcal, avg_watt=avg_watt,
                    datum=fmt_date(session_date), rest=args.rest or args.paste is None
                    and not new_row['Kcal'])

    if args.dry_run:
        print(json.dumps({"dry_run": True, "row": new_row, "summary": summary}, indent=2))
        return

    append_row(csv_path, new_row)
    result = {"appended_to": str(csv_path), "row": new_row, "summary": summary}

    if not args.no_pdf:
        pdf_result = regenerate_pdf(html_path, pdf_path)
        result['pdf'] = {"path": str(pdf_path), **pdf_result}

    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

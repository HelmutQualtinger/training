#!/usr/bin/env python3
"""
bldr.py — self-contained HTML blood-pressure report (blutdruck.html), read
straight from training_log.csv, for a given date period: the same
Zielband chart as training.html (80–90 / 120–140) plus two histograms of the
systolic/diastolic readings — one before exertion (RR_ruhe), one after
(RR_training) — each with the 80/140 limits drawn in.

training_log.csv is the single source of truth (see CLAUDE.md). This script
only reads it — it never writes to the CSV or touches training.html. The
output HTML is ../assets/blutdruck_template.html with the filtered readings
embedded as a plain JS object (Chart.js via cdnjs) — no server needed to
view it, just open the file in a browser.

Usage
-----
    python3 bldr.py                          # last 2 months up to latest row
    python3 bldr.py --months 3
    python3 bldr.py --start 1.8 --end 31.8 --out blutdruck_august.html

Common flags:
    --csv PATH     path to training_log.csv (default: repo root, auto-detected)
    --end D.M      period end, inclusive (default: latest row in the CSV)
    --start D.M    period start, inclusive (default: --months before --end)
    --months N     period length when --start is omitted (default: 2)
    --out PATH     output HTML path (default: repo root / blutdruck.html)
"""
import argparse
import calendar
import csv
import datetime
import json
import re
import sys
from pathlib import Path
from typing import NoReturn

PLACEHOLDER = "/*__DATA__*/null"
RR_COLS = {"RR_ruhe": "rr_ruhe", "RR_training": "rr", "RR_abend": "rr_abend"}
LIMIT_SYS, LIMIT_DIA = 140, 80


def repo_root() -> Path:
    """.claude/skills/bldr/scripts/bldr.py -> repo root."""
    return Path(__file__).resolve().parents[4]


def parse_date_str(s: str) -> datetime.date:
    """'D.M' (no year, no leading zeros) -> date in 2026."""
    d, m = s.split(".")
    return datetime.date(2026, int(m), int(d))


def fmt_date(d: datetime.date) -> str:
    return f"{d.day}.{d.month}"


def months_before(d: datetime.date, n: int) -> datetime.date:
    """Same day-of-month n months earlier, clamped to that month's length."""
    idx = d.year * 12 + (d.month - 1) - n
    y, m = divmod(idx, 12)
    return datetime.date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def fail(msg: str) -> NoReturn:
    print(json.dumps({"error": msg}, ensure_ascii=False))
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, default=repo_root() / "training_log.csv")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--months", type=int, default=2)
    ap.add_argument("--out", type=Path, default=repo_root() / "blutdruck.html")
    args = ap.parse_args()

    if not args.csv.exists():
        fail(f"CSV not found: {args.csv}")
    with args.csv.open(newline="", encoding="utf-8") as f:
        table = list(csv.DictReader(f))
    if not table:
        fail("CSV has no rows")

    try:
        end = parse_date_str(args.end) if args.end else max(parse_date_str(r["Datum"]) for r in table)
        start = parse_date_str(args.start) if args.start else months_before(end, args.months)
    except ValueError as e:
        fail(f"bad date (expected D.M): {e}")
    if start > end:
        fail(f"start {fmt_date(start)} is after end {fmt_date(end)}")

    rows = []
    vals = {key: [] for key in RR_COLS.values()}  # key -> [(sys, dia), ...]
    for r in table:
        if not start <= parse_date_str(r["Datum"]) <= end:
            continue
        row = {"dat": r["Datum"]}
        for col, key in RR_COLS.items():
            m = re.match(r"\s*(\d+)/(\d+)", r[col] or "")
            row[key] = r[col] if m else None
            if m:
                vals[key].append((int(m[1]), int(m[2])))
        if any(row[k] for k in RR_COLS.values()):
            rows.append(row)
    if not rows:
        fail(f"no blood-pressure readings between {fmt_date(start)} and {fmt_date(end)}")

    template = (Path(__file__).resolve().parents[1] / "assets" / "blutdruck_template.html").read_text(encoding="utf-8")
    if template.count(PLACEHOLDER) != 1:
        fail("template placeholder missing or duplicated")
    data = {"start": fmt_date(start), "end": fmt_date(end), "rows": rows}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    args.out.write_text(template.replace(PLACEHOLDER, payload), encoding="utf-8")

    def stats(readings: list[tuple[int, int]]) -> dict:
        n = len(readings)
        if not n:
            return {"messungen": 0}
        return {
            "messungen": n,
            "avg_sys": round(sum(r[0] for r in readings) / n),
            "avg_dia": round(sum(r[1] for r in readings) / n),
            "sys_ueber_140": sum(r[0] > LIMIT_SYS for r in readings),
            "dia_ueber_80": sum(r[1] > LIMIT_DIA for r in readings),
        }

    print(json.dumps({
        "out": str(args.out),
        "start": data["start"],
        "end": data["end"],
        "tage": len(rows),
        **stats([r for v in vals.values() for r in v]),
        "vor_belastung": stats(vals["rr_ruhe"]),
        "nach_belastung": stats(vals["rr"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

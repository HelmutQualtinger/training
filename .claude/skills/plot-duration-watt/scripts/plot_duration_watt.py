#!/usr/bin/env python3
"""
plot_duration_watt.py — self-contained HTML scatter plot of session duration
vs. estimated watt, read straight from training_log.csv, for a given date
period, with a linear-fit trend line and an extrapolation to a target
duration (default 60 min).

training_log.csv is the single source of truth (see CLAUDE.md). This script
only reads it — it never writes to the CSV or touches training.html. The
output HTML embeds the filtered session data as a plain JS array and does
the regression client-side (Plotly.js via cdnjs) — no server needed to view
it, just open the file in a browser.

Usage
-----
    python3 plot_duration_watt.py --start 25.5 --end 31.8
    python3 plot_duration_watt.py --start 1.8 --end 31.8 --out august.html
    python3 plot_duration_watt.py --target-min 45   # extrapolate to 45 min
    python3 plot_duration_watt.py                   # full range, target 60

Common flags:
    --csv PATH       path to training_log.csv (default: repo root, auto-detected)
    --start D.M      period start, inclusive (default: earliest session)
    --end D.M        period end, inclusive (default: latest session)
    --target-min N   duration (min) to extrapolate the linear fit to (default: 60)
    --out PATH       output HTML path (default: repo root /
                      duration_watt_<start>_<end>.html)
"""
import argparse
import csv
import datetime
import json
import sys
from pathlib import Path

START_DATE = datetime.date(2026, 5, 25)  # week 1 start — fixed anchor, see CLAUDE.md


def repo_root() -> Path:
    """.claude/skills/plot-duration-watt/scripts/plot_duration_watt.py -> repo root."""
    return Path(__file__).resolve().parents[4]


def parse_date_str(s: str) -> datetime.date:
    """'D.M' (no year, no leading zeros) -> date in 2026."""
    d, m = s.split(".")
    return datetime.date(2026, int(m), int(d))


def fmt_date(d: datetime.date) -> str:
    return f"{d.day}.{d.month}"


def week_of(d: datetime.date) -> int:
    return (d - START_DATE).days // 7 + 1


def load_sessions(csv_path: Path) -> list[dict]:
    """Rows with a real Watt/Dauer_sek value — skips rest-day BP-only rows."""
    sessions = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("Dauer_sek") or not row.get("Watt"):
                continue
            date = parse_date_str(row["Datum"])
            sessions.append(
                {
                    "n": row["Nr"],
                    "date": fmt_date(date),
                    "_date": date,
                    "week": week_of(date),
                    "dur": round(int(row["Dauer_sek"]) / 60, 3),
                    "watt": float(row["Watt"]),
                    "kcal": int(float(row["Kcal"])) if row.get("Kcal") else None,
                }
            )
    return sessions


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<title>Dauer vs. Watt</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdnjs.cloudflare.com/ajax/libs/plotly.js/3.4.0/plotly.min.js"></script>
<style>
:root{{
  --bg:#f5f4f0;--surface:#fff;--text:#1a1a18;--text2:#5f5e5a;--text3:#888780;
  --border:#e2e0da;--C1:#185FA5;
}}
@media(prefers-color-scheme:dark){{:root{{
  --bg:#1a1a18;--surface:#242421;--text:#f0efe9;--text2:#a8a79f;--text3:#6b6a65;
  --border:#3a3a36;
}}}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);margin:0;padding:24px 20px}}
h1{{font-size:18px;font-weight:600;margin:0 0 2px}}
p.sub{{font-size:13px;color:var(--text2);margin:0 0 20px}}
#chart{{max-width:960px;height:520px;margin:0 auto;background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px;box-sizing:border-box}}
#stats{{max-width:960px;margin:14px auto 0;background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 18px;font-size:13px;color:var(--text2);display:flex;gap:28px;flex-wrap:wrap}}
#stats .stat-lbl{{font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:var(--text3);margin-bottom:2px}}
#stats .stat-val{{font-size:15px;color:var(--text);font-weight:600}}
#stats .stat-val span{{font-size:12px;font-weight:400;color:var(--text3);margin-left:2px}}
</style>
</head>
<body>
<h1>Dauer vs. Watt</h1>
<p class="sub">{subtitle}</p>
<div id="chart"></div>
<div id="stats"></div>
<script>
const isDark = matchMedia('(prefers-color-scheme: dark)').matches;
const ink = isDark ? '#f0efe9' : '#1a1a18';
const ink2 = isDark ? '#a8a79f' : '#5f5e5a';
const grid = isDark ? '#3a3a36' : '#e2e0da';
const surface = isDark ? '#242421' : '#ffffff';
const accent = '#185FA5';
const accent2 = '#BA7517';

const sessions = {sessions_json};
const targetDur = {target_min};

const trace = {{
  name: 'Einheiten',
  x: sessions.map(s => s.dur),
  y: sessions.map(s => s.watt),
  text: sessions.map(s => `#${{s.n}} \\u00b7 ${{s.date}} \\u00b7 ${{s.kcal}} kcal`),
  mode: 'markers',
  type: 'scatter',
  marker: {{ size: 9, color: accent, opacity: 0.75, line: {{ width: 1, color: surface }} }},
  hovertemplate: '%{{text}}<br>Dauer: %{{x}} min<br>Watt: %{{y}} W<extra></extra>'
}};

const n = sessions.length;
const sumX = sessions.reduce((a, s) => a + s.dur, 0);
const sumY = sessions.reduce((a, s) => a + s.watt, 0);
const sumXY = sessions.reduce((a, s) => a + s.dur * s.watt, 0);
const sumXX = sessions.reduce((a, s) => a + s.dur * s.dur, 0);
const slope = (n * sumXY - sumX * sumY) / (n * sumXX - sumX * sumX);
const intercept = (sumY - slope * sumX) / n;
const xMin = Math.min(...sessions.map(s => s.dur));
const xMax = Math.max(...sessions.map(s => s.dur));

const meanY = sumY / n;
const ssTot = sessions.reduce((a, s) => a + (s.watt - meanY) ** 2, 0);
const ssRes = sessions.reduce((a, s) => a + (s.watt - (slope * s.dur + intercept)) ** 2, 0);
const r2 = 1 - ssRes / ssTot;

const predWatt = slope * targetDur + intercept;
const isExtrapolation = targetDur < xMin || targetDur > xMax;
const predLabel = isExtrapolation ? 'Extrapolation' : 'Interpolation';

const fitTrace = {{
  name: 'Trend',
  x: [xMin, xMax],
  y: [xMin, xMax].map(x => slope * x + intercept),
  mode: 'lines',
  type: 'scatter',
  line: {{ color: ink2, width: 2, dash: 'dot' }},
  hoverinfo: 'skip'
}};

const traces = [fitTrace, trace];

if (isExtrapolation) {{
  const segStart = targetDur > xMax ? xMax : targetDur;
  const segEnd = targetDur > xMax ? targetDur : xMin;
  traces.push({{
    name: predLabel,
    x: [segStart, segEnd],
    y: [segStart, segEnd].map(x => slope * x + intercept),
    mode: 'lines',
    type: 'scatter',
    line: {{ color: accent2, width: 2, dash: 'dash' }},
    hoverinfo: 'skip'
  }});
}}

traces.push({{
  name: `${{predLabel}} ${{targetDur}} min`,
  x: [targetDur],
  y: [predWatt],
  mode: 'markers',
  type: 'scatter',
  marker: {{ size: 13, color: 'rgba(0,0,0,0)', line: {{ width: 2, color: accent2 }}, symbol: 'circle' }},
  hovertemplate: `${{predLabel}}: ${{targetDur}} min \\u2192 ${{predWatt.toFixed(1)}} W<extra></extra>`
}});

const layout = {{
  height: 496,
  paper_bgcolor: surface,
  plot_bgcolor: surface,
  font: {{ family: '-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif', color: ink2, size: 12 }},
  margin: {{ l: 55, r: 20, t: 10, b: 50 }},
  xaxis: {{ title: {{ text: 'Dauer (min)', font: {{ color: ink2 }} }}, gridcolor: grid, zeroline: false, color: ink2 }},
  yaxis: {{ title: {{ text: 'Watt', font: {{ color: ink2 }} }}, gridcolor: grid, zeroline: false, color: ink2 }},
  hoverlabel: {{ bgcolor: surface, bordercolor: grid, font: {{ color: ink }} }},
  legend: {{ font: {{ color: ink2 }}, bgcolor: 'rgba(0,0,0,0)', x: 1, xanchor: 'right', y: 1, yanchor: 'top' }}
}};

Plotly.newPlot('chart', traces, layout, {{ responsive: true, displayModeBar: false }});

const sign = intercept >= 0 ? '+' : '\\u2212';
document.getElementById('stats').innerHTML = `
  <div><div class="stat-lbl">Fit</div><div class="stat-val">Watt = ${{slope.toFixed(3)}}&times;Dauer ${{sign}} ${{Math.abs(intercept).toFixed(1)}}</div></div>
  <div><div class="stat-lbl">Steigung</div><div class="stat-val">${{slope.toFixed(3)}}<span>W/min</span></div></div>
  <div><div class="stat-lbl">R&sup2;</div><div class="stat-val">${{r2.toFixed(3)}}</div></div>
  <div><div class="stat-lbl">${{predLabel}} bei ${{targetDur}} min</div><div class="stat-val">${{predWatt.toFixed(1)}}<span>W</span></div></div>
`;
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, default=None, help="path to training_log.csv")
    ap.add_argument("--start", type=str, default=None, help="period start, inclusive, D.M")
    ap.add_argument("--end", type=str, default=None, help="period end, inclusive, D.M")
    ap.add_argument("--target-min", type=float, default=60, help="duration (min) to extrapolate to")
    ap.add_argument("--out", type=Path, default=None, help="output HTML path")
    args = ap.parse_args()

    csv_path = args.csv or (repo_root() / "training_log.csv")
    if not csv_path.exists():
        print(json.dumps({"error": f"CSV nicht gefunden: {csv_path}"}))
        sys.exit(1)

    sessions = load_sessions(csv_path)
    if not sessions:
        print(json.dumps({"error": "Keine Trainingseinheiten in der CSV gefunden."}))
        sys.exit(1)

    start = parse_date_str(args.start) if args.start else min(s["_date"] for s in sessions)
    end = parse_date_str(args.end) if args.end else max(s["_date"] for s in sessions)
    if start > end:
        print(json.dumps({"error": f"--start ({fmt_date(start)}) liegt nach --end ({fmt_date(end)})."}))
        sys.exit(1)

    period = [s for s in sessions if start <= s["_date"] <= end]
    if not period:
        print(json.dumps({"error": f"Keine Einheiten zwischen {fmt_date(start)} und {fmt_date(end)}."}))
        sys.exit(1)
    period.sort(key=lambda s: s["_date"])
    for s in period:
        del s["_date"]

    out_path = args.out or (
        repo_root() / f"duration_watt_{fmt_date(start)}_{fmt_date(end)}.html"
    )

    subtitle = (
        f"{len(period)} Trainingseinheiten ({fmt_date(start)}–{fmt_date(end)}) "
        f"· Christopeit AX 4000 / Kinomap"
    )
    html = HTML_TEMPLATE.format(
        subtitle=subtitle,
        sessions_json=json.dumps(period, ensure_ascii=False),
        target_min=args.target_min,
    )
    out_path.write_text(html, encoding="utf-8")

    durations = [s["dur"] for s in period]
    watts = [s["watt"] for s in period]
    print(
        json.dumps(
            {
                "out": str(out_path),
                "start": fmt_date(start),
                "end": fmt_date(end),
                "sessions": len(period),
                "target_min": args.target_min,
                "duration_min_range": [round(min(durations), 1), round(max(durations), 1)],
                "watt_range": [round(min(watts)), round(max(watts))],
            }
        )
    )


if __name__ == "__main__":
    main()

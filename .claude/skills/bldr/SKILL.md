---
name: bldr
description: >
  Generate blutdruck.html — a self-contained blood-pressure report from
  training_log.csv for a date period (default: the last 2 months): the same
  Zielband chart as training.html (green bands 80–90 / 120–140, Ruhe / nach
  Training / abends) plus two histograms of systolic/diastolic readings, one
  before exertion (Ruhe) and one after (nach Training), each with the
  80/140 limits drawn in — done entirely by a deterministic Python
  script, no LLM parsing of the CSV involved. Use this whenever the user
  invokes /bldr, or wants a blood-pressure chart, overview, histogram or
  distribution ("Blutdruck-Übersicht", "Blutdruck der letzten 3 Monate",
  "bldr histogramm"). NOT for logging a new reading — "bldr 135/80/69" or
  "bldr jetzt ..." with numbers is a rest-day entry for the add-training
  skill.
---

# Bldr Skill (Blutdruck-Report)

Reads `training_log.csv` (the single source of truth for this repo — see
`CLAUDE.md`) and writes `blutdruck.html` for a given date range via
`scripts/bldr.py`. Read-only — it never writes to the CSV or touches
`training.html`. The output is a standalone `.html` file (Chart.js via
cdnjs, same light/dark theme tokens as `training.html`) — open it directly
in a browser, no local server needed (unlike `training.html`, it embeds its
data inline rather than `fetch()`ing the CSV). That also means it is a
snapshot: after new readings are logged, rerun this skill to refresh it.

**Your job when this skill runs is to pick the period and call the script —
not to edit `blutdruck.html` yourself.** Layout/chart changes belong in
`assets/blutdruck_template.html` (the script only swaps the
`/*__DATA__*/null` placeholder for the filtered readings), followed by a
rerun.

## Step 1 — figure out the period

Don't ask the user unless genuinely ambiguous. Dates are `D.M` (no year, no
leading zeros — e.g. `5.8`, `31.10`), matching the CSV's own `Datum` format.

- No period mentioned → pass nothing; the script takes the last 2 months up
  to the latest row in the CSV.
- "letzte N Monate" → `--months N`.
- Explicit or relative range ("August", "seit 1.9", "letzte 4 Wochen") →
  resolve it to `--start`/`--end` yourself.

If the argument is a reading like `135/80/69` rather than a period, this is
the wrong skill — use `add-training` (rest-day BP log) instead.

## Step 2 — run the script

```bash
python3 .claude/skills/bldr/scripts/bldr.py \
  [--months N] [--start D.M] [--end D.M] [--out PATH]
```

All flags are optional. `--out` defaults to `blutdruck.html` in the repo
root; pass it only if the user wants a separate file (e.g.
`blutdruck_august.html`) instead of overwriting the standing report.

## Step 3 — read the result

The script prints one JSON object to stdout and exits non-zero on failure
(bad date, start after end, no readings in the window, missing CSV) —
report the error and stop rather than retrying with guessed values.

On success, summarize for the user: output file, period, number of readings
(`messungen`) and days (`tage`), average (`avg_sys`/`avg_dia`), and how many
readings were above the limits (`sys_ueber_140`, `dia_ueber_80`) — overall,
and the same figures for `vor_belastung` and `nach_belastung` side by side
(a group with no readings in the period only carries `messungen: 0`). Then open
it with `open <out>` unless the user only wanted the file.

## Notes

- There are two histograms on a shared mmHg axis: **vor Belastung**
  (`RR_ruhe`, including rest-day rows) and **nach Belastung**
  (`RR_training`). Every reading contributes one systolic and one diastolic
  value to its histogram. `RR_abend` readings belong to neither — they only
  show up in the band chart and in the overall figures at the top.
- Two different thresholds are in play, on purpose: the yellow triangles in
  the band chart mark readings outside the Zielband exactly like
  `training.html` does (systolic > 140 or diastolic > 90), while the
  histogram lines and the "über" figures use 80 / 140.
- Histogram classes are 5 mmHg wide and right-closed (e.g. 136–140), so a
  reading of exactly 80 or 140 sits left of its limit line and is not
  counted as "über".
- Colors/theme tokens reuse `training.html`'s palette for visual
  consistency with the rest of the dashboard.

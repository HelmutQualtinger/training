---
name: plot-duration-watt
description: >
  Generate a self-contained HTML scatter plot of session duration vs.
  estimated watt from training_log.csv, for a specified date period, with a
  linear-fit trend line and an extrapolation to a target duration (default
  60 min) — done entirely by a deterministic Python script that embeds the
  data and regression in plain JS/Plotly, no LLM parsing of the CSV
  involved. Use this whenever the user wants to visualize duration vs watt,
  plot training intensity/duration trends over a time range, fit a
  duration/watt trend line, or extrapolate watt at a longer duration.
---

# Plot Duration vs Watt Skill

Reads `training_log.csv` (the single source of truth for this repo — see
`CLAUDE.md`) and renders a duration-vs-watt scatter chart with a linear fit
for a given date range via `scripts/plot_duration_watt.py`. Read-only — it
never writes to the CSV or touches `training.html`. Output is a standalone
`.html` file (Plotly via cdnjs, same light/dark theme tokens as
`training.html`) — open it directly in a browser, no local server needed
(unlike `training.html`, it embeds its data inline rather than `fetch()`ing
the CSV).

## Step 1 — figure out the period and extrapolation target

Ask yourself (don't ask the user unless genuinely ambiguous):

- **Date range**, converted to `D.M` (no year, no leading zeros — e.g.
  `25.5`, `31.8`), matching the CSV's own `Datum` format:
  - Explicit dates ("from 1.8 to 31.8") → use them directly.
  - Relative range ("last 4 weeks", "August", "since week 9") → resolve
    against today's date / the CSV's own date column.
  - No period mentioned → omit `--start`/`--end`; the script defaults to
    the full logged range.
- **Extrapolation target duration** (minutes) → default is 60; only pass
  `--target-min` if the user names a different duration.

## Step 2 — run the script

```bash
python3 .claude/skills/plot-duration-watt/scripts/plot_duration_watt.py \
  --start D.M --end D.M --target-min N [--out PATH]
```

`--start`/`--end`/`--target-min` are each optional independently. `--out`
defaults to `duration_watt_<start>_<end>.html` in the repo root; pass it
explicitly if the user wants the file elsewhere or under a specific name
(e.g. `duration_vs_watt.html`).

Rest-day BP-only rows (no `Dauer_sek`/`Watt`) are skipped automatically —
only real training sessions are plotted.

## Step 3 — read the result

The script prints one JSON object to stdout and exits non-zero on failure
(bad/empty date range, missing CSV, no sessions in the window) — report
the error and stop rather than retrying with guessed values.

On success, summarize for the user: output file path, number of sessions
plotted, duration/watt ranges covered, and offer to open it in a browser.

## Notes

- The linear fit (least-squares, computed client-side in the page's own
  JS) is drawn as a dotted trend line across the actual data range. If the
  extrapolation target falls **outside** that range, a separate dashed
  segment extends the line to it and the stat panel/marker are labeled
  "Extrapolation"; if the target falls inside the data range they're
  labeled "Interpolation" instead — this is computed automatically, don't
  hardcode either label yourself.
- The stats panel below the chart shows the fit equation, slope (W/min),
  R², and the predicted watt at the target duration.
- Watt is always an estimate (see `CLAUDE.md`'s kcal/min formula).
- Colors/theme tokens reuse `training.html`'s own palette (`--C1` blue,
  light/dark via `prefers-color-scheme`) for visual consistency with the
  rest of the dashboard, and use no drop shadows.
- This script is read-only analysis tooling — it is intentionally separate
  from `add_training.py` (which is the only script allowed to write to
  `training_log.csv`).

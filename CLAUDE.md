# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal indoor-cycling training log (Christopeit AX 4000 + Kinomap), not a software project. No build system, no tests, no package manager — just static self-contained HTML dashboards, CSV exports, and a SQL dump, all manually maintained snapshots of the same underlying training sessions.

## Architecture: data flows one way, by hand

There is no shared data source and no fetch/AJAX/CSV-loading anywhere. Every `.html` file embeds its own hardcoded JS data array in a `<script>` block and renders it with Chart.js (loaded from `cdnjs.cloudflare.com`, no local deps). Updating one file does **not** update the others — each is a point-in-time export that must be re-synced manually when new sessions are added.

- **`training.html`** — the most current and most complete file. Contains two separate arrays that must both be updated when adding a session:
  - `const raw=[...]` (~line 230): `[date, watt, kcal, hf, week]` — compact per-session data used for the chart aggregations (daily/weekly rollups).
  - `const rows=[...]` (~line 495): the detailed table, one object per session (`{n, w, tag, dat, dur, k, kpm, watt, hf, kum, rec, rr, rr_ruhe, rr_abend, note}`). The `note` field is the only place route name, distance, elevation, temperature, and cadence live (as a free-text `·`-separated string prefixed `Kinomap · ...` for Kinomap rides) — there's no structured field for these.
  - Rest days appear in `rows` with `n:'–'` and null values; skip them when extracting session data.
  - **Adding a session touches more than `raw`/`rows`.** The daily/weekly charts and the hero metric bar do *not* derive from `raw`/`rows` at render time — they read separate hardcoded arrays/values that must be updated by hand in lockstep:
    - Hero `.metric-val` divs (~line 73): Einheiten, Kalorien total, Dauer total, Ø kcal/min, Ø Watt, Letzte Einheit.
    - `wkKcal`/`wkMin`/`wkWatt`/`wkKpm` (~line 1567): per-week rollups used by the Wöchentlich-tab charts.
    - The `'Erreicht'` dataset in the `cPlan` chart (~line 1608): a *third* copy of the per-week Ø-Watt series, used by the Plan-200W tab.
    - `endDate` in `parseDate('25.5'),endDate=parseDate(...)` (~line 1525): must be bumped to the newest session date or the daily chart's date range won't include it.
    Forgetting one of these is exactly what produced a wrong weekly total after adding a session — always grep the new date/week number across the whole file after editing `raw`/`rows`, don't assume the rest is derived.
- **`training_dashboard.html`** — an older snapshot with the same `raw`-array shape as `training.html` but fewer rows (stale, not kept in sync).
- **`training_data.csv`** — flat export of `training.html`'s `raw` array (`Nr,Tag,Datum,Dauer_min,kcal,kcal_min,Watt,Max_HF,Woche`). `Tag` (weekday) and `Dauer_min`/`kcal_min` are derived, not stored in the source — see formula below.
- **`kinomap_rennwerte.csv`** — Kinomap-only sessions (the subset of `rows` whose `note` starts with `Kinomap`), parsed out of the `note` free text into structured columns: `Datum,Tag,Strecke,Distanz_km,Dauer_min,kcal,kcal_min,Watt_Avg,Watt_Max,Max_HF,Hoehenmeter,Temperatur_C,Kadenz_rpm,Woche`. Not every session is a Kinomap ride, so this has fewer rows than `training_data.csv` for the same dates.
- **`training_data_mysql.sql`** — an even older, more stale export with extra fields (blood pressure `rr` values, cumulative kcal) not present in the CSVs.
- **`stufentest.html` / `stufentest_regression.html`** — standalone step-test (Stufentest) watt/HR calibration charts, unrelated data (`watts[]`/`bpms[]` arrays), used to derive the HR-based watt estimation formula.
- **`wkg_histogram.html`**, **`training_slideshow.html`** — other standalone single-purpose dashboards with their own embedded data.
- **`PulsCharts/`** — a self-contained folder: `puls_graphen_uebersicht.html` plus the watch-screenshot `.jpg` files it displays, referenced by plain relative filename (no subfolder) in the `<img src>`.

## Key domain facts

- Watt is estimated from kcal/min, not measured directly: `Watt = (kcal/min × 1000 × 4.185) / 60 × 0.25`. Inverting it: `Dauer_min = kcal × 17.4375 / Watt`.
- Dates are `D.M` with no year (implicit 2026), no leading zeros (e.g. `'5.6'`, `'14.8'`).
- Training weeks are fixed calendar boundaries starting Monday-ish from 25.5 (`getWeek()` / `wn` map in `training.html`), not ISO weeks — check the `wn` object (~line 600 of `training.html`) before assuming a date's week number.
- A day can have multiple sessions (e.g. two rides logged the same date); when aggregating per day, sum kcal and average watt across that day's entries (see `dayMap` in `training.html`).

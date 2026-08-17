---
name: export-csv
description: >
  Regenerate training_data.csv and kinomap_rennwerte.csv from the `rows`
  array in training.html — done entirely by a deterministic Python script,
  no LLM parsing involved. Both CSVs are documented in CLAUDE.md as
  manually-maintained snapshots that drift out of sync with training.html;
  this skill makes them a fresh, correct export on demand instead. Use this
  whenever the user asks to export/regenerate/sync the CSV files, or update
  training_data.csv / kinomap_rennwerte.csv — trigger phrases: "export csv",
  "csv exportieren", "aktualisiere die csv Dateien", "sync the csv".
---

# Export CSV Skill

Regenerates `training_data.csv` and `kinomap_rennwerte.csv` from
`training.html`'s `rows` array via `scripts/export_csv.py`. Ground truth is
always `rows` — the script re-derives every column from it on each run, the
same "no hand-patching" philosophy as the `add-training` skill.

**Your job is to run the script and report the result — not to hand-edit
either CSV.**

## Step 1 — run the script

```bash
python3 .claude/skills/export-csv/scripts/export_csv.py
```

This regenerates both CSVs in the repo root and prints a JSON summary with
row counts. Useful flags:

- `--only training_data` / `--only kinomap` — regenerate just one
- `--dry-run` — compute and print row counts, write nothing (use this first
  if the user just wants to check whether the CSVs are stale, without
  committing to overwriting them)
- `--html PATH` / `--out-dir DIR` — override source/destination paths

Full reference: `python3 .claude/skills/export-csv/scripts/export_csv.py --help`

## Step 2 — report the result

Tell the user the row counts and that both files now reflect the current
`training.html` state. If `git diff --stat training_data.csv
kinomap_rennwerte.csv` shows changes, that's expected whenever a session was
added since the CSVs were last exported — mention roughly what changed
(e.g. "+1 row" after a new session).

## What the two CSVs contain, and why they can differ from each other

- **`training_data.csv`** (`Nr,Tag,Datum,Dauer_min,kcal,kcal_min,Watt,Max_HF,Woche`):
  one row per real session (rest days skipped). `Nr` is a plain 1..N
  recount — it does not reuse `training.html`'s `n` labels, which include
  letter suffixes like `9b` and special tags like `ST`. `kcal_min` here is
  freshly computed as `round(kcal/Dauer_min, 2)` — **not** copied from the
  row's stored `kpm` (which is rounded to 1 decimal for display).

- **`kinomap_rennwerte.csv`** (`Datum,Tag,Strecke,Distanz_km,Dauer_min,kcal,
  kcal_min,Watt_Avg,Watt_Max,Max_HF,Hoehenmeter,Temperatur_C,Kadenz_rpm,
  Woche`): only rows whose `note` starts with `Kinomap`. `kcal_min` here
  **is** the row's stored `kpm` verbatim — the two files use genuinely
  different conventions for the same-looking column, and the script
  deliberately preserves each file's own established convention rather than
  unifying them, to avoid silently changing a format other tools may already
  depend on.

  `Strecke`/`Distanz_km`/`Watt_Max`/`Hoehenmeter`/`Temperatur_C`/`Kadenz_rpm`
  are parsed out of the free-text note. A few early sessions used a looser
  note format (no route name, or the route glued directly onto "Kinomap "
  without a `·` separator) — for those, fields that genuinely aren't present
  are left blank rather than guessed. Don't try to backfill them by hand;
  that data just isn't in the source.

## What this skill does NOT touch

`training_data_mysql.sql`, `training_dashboard.html`, `training_slideshow.html`
— separate, independently-stale snapshots per CLAUDE.md. Don't update them
as part of this skill unless the user explicitly asks.

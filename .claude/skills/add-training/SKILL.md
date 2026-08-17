---
name: add-training
description: >
  Add one new training session (or a rest-day blood-pressure log) to
  training.html and regenerate training.pdf — done entirely by a
  deterministic Python script, no LLM parsing/editing of the HTML involved.
  Handles three input styles: a pasted Kinomap session-summary screen, a
  manually described session (duration/kcal/watt/etc.), or a rest-day BP-only
  entry. Use this whenever the user wants to log a new ergometer session,
  paste Kinomap results, or record a rest-day blood pressure reading in this
  repo — trigger phrases: "add training", "log this session", "neue Einheit
  eintragen", "bldr jetzt ...", or a pasted Kinomap summary block.
---

# Add Training Skill

Appends a session to `training.html` (and regenerates `training.pdf`) via
`scripts/add_training.py`. The script is the source of truth for the
mechanics — it fully re-derives every dependent section (hero metrics,
weekly rollups, table footer, cumulative-kcal chart, axis ranges, the
Plan-200W chart, the daily-tab legend, the subtitle) from the `raw`/`rows`
arrays on every run, so there is no hand-editing and no risk of the
staleness bugs documented in `CLAUDE.md`.

**Your job when this skill runs is to gather the right input and call the
script — not to edit training.html yourself.**

## Step 1 — figure out what kind of entry this is

- **Kinomap paste**: the user pasted a block of text that looks like a
  Kinomap session-summary screen (contains lines like `Leistung`, `Avr.`,
  `Kalorien`, `Dauer`, `Ausstattung`). Use this text as-is.
- **Manual session**: the user describes a session in prose ("40 Minuten,
  420 kcal, 190 Watt") without a Kinomap paste — extract the numbers
  yourself and pass them as flags.
- **Rest-day BP log**: the user just gives a blood-pressure reading with no
  training data (e.g. "bldr jetzt 143/86/71") — this is a rest-day entry.

If it's ambiguous which one, ask.

## Step 2 — run the script

All three modes regenerate `training.pdf` by default (~8s, headless Chrome).
Pass `--no-pdf` only if the user explicitly wants to skip that.

**Kinomap paste** — pipe the raw pasted text in via stdin, don't try to
reformat it first:

```bash
python3 .claude/skills/add-training/scripts/add_training.py --paste <<'EOF'
<paste the full Kinomap text block here verbatim>
EOF
```

**Manual session**:

```bash
python3 .claude/skills/add-training/scripts/add_training.py \
  --date D.M --duration MIN --kcal KCAL [--watt WATT] [--hf HF] \
  [--rr POST/TRAINING/BP] [--note "Freitext"]
```

`--watt` is optional — if omitted the script derives it from kcal/duration
via the same formula the rest of the dashboard uses
(`Watt = kcal/min × 17.4375`). Add `--rec` only if the user explicitly
wants the row highlighted as a strong session — the script does **not**
guess this (historically it's an editorial judgment call, not a threshold).

**Rest-day BP log**:

```bash
python3 .claude/skills/add-training/scripts/add_training.py \
  --date D.M --rest --rr-ruhe "SYS/DIA/PULS (HH:MM)"
```

Use `--rr` instead of `--rr-ruhe` for a post-training-style reading logged
on an otherwise-rest day, or `--rr-abend` for an evening reading. At least
one of the three is required for `--rest`.

Full flag reference: `python3 .claude/skills/add-training/scripts/add_training.py --help`

## Step 3 — read the result

The script prints one JSON object to stdout and exits non-zero on failure —
**never try to patch training.html by hand if it errors; report the error
and stop.** Known guardrails it enforces:

- Refuses out-of-order dates (a date before the last logged session) —
  this script only supports appending the newest session, not inserting.
- Refuses a `--rest` entry with no BP field at all.
- Runs a Node syntax-check on the rewritten `<script>` block before saving
  anything; if that fails, nothing is written.

On success, summarize for the user: date, kcal/watt/duration logged, new
running totals (`einheiten`, `total_kcal`, `avg_watt`), and whether the PDF
was regenerated. If the session opened a new week, mention that the
previous week's label got closed off and a new color was assigned.

## Notes on the Kinomap parser

It expects the same on-screen summary layout used throughout this repo:
route name on the line before the distance number, then labeled blocks
(`Geschwindigkeit`/`Leistung`/`Kadenz` each with `Avr.`/`Max.`, then
`Entfernung`, `Erhebungen`, `Dauer`, `Kalorien`, `Ausstattung`). The
`Ausstattung` line's free text after `pulse NNN`/`temp NN`/`bldr X/Y/Z`
becomes the note's trailing commentary. If the paste is missing `Leistung`,
`Kalorien`, or `Dauer`, the script errors out asking for those explicitly
rather than guessing.

`kum` (cumulative), `n` (row number), `tag` (weekday) and the week number
are always computed by the script — never ask the user for these.

## What this skill does NOT touch

`training_data.csv`, `kinomap_rennwerte.csv`, `training_data_mysql.sql`,
`training_slideshow.html`, `training_dashboard.html` — these are documented
in `CLAUDE.md` as separate, manually-synced snapshots. Don't update them as
part of this skill unless the user explicitly asks.

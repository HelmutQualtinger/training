---
name: add-training
description: >
  Add one new training session (or a rest-day blood-pressure log) to
  training_log.csv and regenerate training.pdf — done entirely by a
  deterministic Python script, no LLM parsing/editing involved. Handles
  three input styles: a pasted Kinomap session-summary screen, a manually
  described session (duration/kcal/watt/etc.), or a rest-day BP-only entry.
  Use this whenever the user wants to log a new ergometer session, paste
  Kinomap results, or record a rest-day blood pressure reading in this
  repo — trigger phrases: "add training", "log this session", "neue
  Einheit eintragen", "bldr jetzt ...", or a pasted Kinomap summary block.
---

# Add Training Skill

Appends one row to `training_log.csv` (and regenerates `training.pdf`) via
`scripts/add_training.py`. `training_log.csv` is the single source of truth
for this repo (see `CLAUDE.md`) — `training.html` is static and fetches +
parses the CSV at load time, deriving every chart, table row, hero metric
and weekly rollup from it itself. This script therefore **never touches
training.html** — it only ever appends one well-formed CSV row.

**Your job when this skill runs is to gather the right input and call the
script — not to edit any files yourself.**

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

All three modes regenerate `training.pdf` by default (~8s: it briefly
serves the repo over a local HTTP port and renders it in headless Chrome —
see "Why HTTP, not file://" below). Pass `--no-pdf` only if the user
explicitly wants to skip that.

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

Never type a `⚠️` into `--rr`/`--rr-ruhe`/`--rr-abend` yourself — training.html's
own script flags systolic>140 or diastolic>90 automatically (chart triangle +
table marker) from the numbers, for all three readings. Pass the plain
`SYS/DIA/PULS` reading; the script also strips any stray `⚠️` you do pass, so
manually flagging it just gets silently discarded rather than double-marked.

Full flag reference: `python3 .claude/skills/add-training/scripts/add_training.py --help`

## Step 3 — read the result

The script prints one JSON object to stdout and exits non-zero on failure —
**never try to patch the CSV by hand if it errors; report the error and
stop.** Known guardrails it enforces:

- Refuses out-of-order dates (a date before the last logged row) — this
  script only supports appending the newest session, not inserting.
- Refuses a `--rest` entry with no BP field at all.

For a Kinomap paste, `Watt_Max_PR` is set automatically when the parsed max
watt beats every `Watt_Max` already in the CSV — never set this by hand.

On success, summarize for the user: date, kcal/watt/duration logged, new
running totals (`einheiten`, `total_kcal`, `avg_watt`), and whether the PDF
was regenerated. `training.html` needs no changes — it will show the new
row the next time it's loaded (or re-served), since it reads the CSV live.

## Notes on the Kinomap parser

It expects the same on-screen summary layout used throughout this repo:
route name on the line before the distance number, then labeled blocks
(`Geschwindigkeit`/`Leistung`/`Kadenz` each with `Avr.`/`Max.`, then
`Entfernung`, `Erhebungen`, `Dauer`, `Kalorien`, `Ausstattung`). The
`Ausstattung` line's free text after `pulse NNN`/`temp NN`/`bldr X/Y/Z`
becomes the `Kommentar` column's trailing commentary. If the paste is
missing `Leistung`, `Kalorien`, or `Dauer`, the script errors out asking
for those explicitly rather than guessing.

`Nr` (row number) is always computed by the script (a plain next-integer,
matching the convention already used since roughly week 9 — no letter
suffixes) — never ask the user for it. `Woche`, weekday, and cumulative
kcal are never stored at all; `training.html` derives them from `Datum`
and row order at render time.

## Why HTTP, not file://

`training.html` loads `training_log.csv` via `fetch()`, which Chrome
refuses for local files opened as `file://` (CORS). So PDF regeneration
briefly runs `python3 -m http.server` on a free port in the repo root,
points headless Chrome's `--print-to-pdf` at `http://localhost:<port>/...`,
and tears the server down afterward. This is the same reason `training.html`
itself needs to be viewed through a local server rather than double-clicked.

## What this skill does NOT touch

`training.html` — it's static and reads `training_log.csv` at render time,
so there is nothing in it to update when a session is added.

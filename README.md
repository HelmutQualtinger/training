# Ergometer Training Log

Personal indoor-cycling training log (Christopeit AX 4000 + Kinomap), tracked since 25 May 2026.

![Dashboard-Ausschnitt](dashboard_screenshot.png)

## Dashboard

`training.html` is the main dashboard — it fetches `training_log.csv` at load time, so it needs to be served over HTTP rather than opened directly (e.g. `python3 -m http.server` from this folder, then open `http://localhost:8000/training.html`). It has four tabs:

- **Täglich** — Ø Watt, Kalorien, Trainingszeit und Max-Puls pro Tag
- **Wöchentlich** — Kalorien & Minuten sowie Ø Watt pro Woche, kumulative Kalorien
- **Tabelle** — alle Trainingseinheiten einzeln, mit Blutdruck-Werten wo vorhanden
- **Plan 200W** — Watt-Progression, erreicht vs. Ziel

`training.pdf` is a print export of all four tabs.

## Data files

- `training_log.csv` — the single source of truth: one row per session (or per rest-day BP log), with Datum/Dauer/kcal/Watt/MaxHF/Blutdruck plus structured Kinomap fields (Strecke, Distanz, Höhenmeter, Temperatur, Kadenz). Everything else (Wochentag, Woche, kumulative kcal, Charts, Tabelle) is derived from this file by `training.html` at render time.

There's no database or build step. See `CLAUDE.md` for the CSV schema, the watt-estimation formula, and how to add a new session (via the `add-training` skill).

## Other files

- `stufentest.html` / `stufentest_regression.html` — Stufentest-Kalibrierung (Watt ↔ Puls)
- `wkg_histogram.html`, `training_slideshow.html` — weitere Einzel-Dashboards
- `PulsCharts/` — Puls-Screenshots der Watch mit Übersichtsseite

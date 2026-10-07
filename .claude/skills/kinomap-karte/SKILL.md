---
name: kinomap-karte
description: >
  Turn a Kinomap activity export zip (export_<id>.zip containing the ride's
  .csv/.gpx/.tcx/.pwx) into two map pages in the repo root: a 2D
  OpenStreetMap page (karte_<D.M>_<route>.html) and a three.js 3D page with
  terrain, buildings, a cyclist avatar, playback and a bike-computer cockpit
  (karte_<D.M>_<route>_3d.html) — done entirely by a deterministic Python
  script. Uses the newest export_*.zip unless the user names a specific zip.
  Use this whenever the user invokes /kinomap-karte, or wants a ride's
  exported map/track decoded, shown on a map, or shown in 3D ("zeige die
  Strecke auf der Karte", "exportierte Karte dekodieren", "3D-Karte von der
  heutigen Fahrt", "mach die Karte für export_xyz.zip"). NOT for logging the
  session itself — a pasted Kinomap summary screen goes to add-training.
---

# Kinomap-Karte Skill

Reads one Kinomap export zip and writes two HTML pages via
`scripts/kinomap_karte.py`. Read-only with respect to the rest of the repo —
it never touches `training_log.csv`, `training.html` or the zip (it only
looks up the session number in the CSV for the subtitle).

**Your job when this skill runs is to pick the zip and call the script — not
to edit the generated pages yourself.** Layout, camera, avatar and cockpit
live in `assets/karte_template.html` (2D) and `assets/karte3d_template.html`
(3D); the script only swaps the `/*__DATA__*/null` placeholder for the
ride's data. Change a template, then rerun.

## Step 1 — which zip

- No zip mentioned → pass nothing. The script takes the newest
  `export_*.zip` (by modification time) in the repo root, or, if there is
  none, in `~/Downloads`.
- A specific file mentioned (path, file name, or just the id) → resolve it
  to a path and pass it as the positional argument.

Don't ask unless the user's reference matches several files.

## Step 2 — run the script

```bash
python3 .claude/skills/kinomap-karte/scripts/kinomap_karte.py \
  [ZIP] [--out-dir DIR] [--radius M] [--no-3d] [--cached-buildings] [--vector] [--vector]
```

All arguments are optional. `--radius` is how far from the route buildings
are kept (default 800 m). `--no-3d` writes only the 2D page and needs no
network. The first 3D run for a ride takes a few minutes (Overpass is slow
and often answers 504 — the script retries across mirrors); reruns are fast
because elevation tiles and building data are cached in the system temp dir.
Give the command a generous timeout (10 min). If Overpass is
down or the user doesn't want to wait, `--cached-buildings` skips the queries
and builds the 3D page right away from whatever building chunks are already
cached (possibly none) — the result then carries the `warnung`; rerun without
the flag later to fill the gaps.

`--vector` (only when the user asks for vector tiles for that ride) draws the
3D ground from vector tiles instead of OSM raster tiles: the script cuts the
ride's area out of the Protomaps planet build into `karte_<D.M>_<route>.pmtiles`
next to the page (a few MB, needs `pip install pmtiles`), and the page paints
them itself — much sharper from road level. It is per ride and sticks: a later
plain rerun finds the `.pmtiles` and keeps using it; delete the file to go back
to raster tiles. Commit the `.pmtiles` together with the page.

## Step 3 — read the result

The script prints one JSON object and exits non-zero on failure — report the
error and stop. On success, tell the user which zip was used (`zip`), the
ride (`strecke`, `datum`, `einheit`, `km`, `dauer`, `avg_watt`) and, for the
3D page, `gebiet_km`, `hoehe_m` and `gebaeude`. If the result carries a
`warnung`, the building data is incomplete — say so and offer to rerun later.

## Step 4 — open it over HTTP

Both pages load their OpenStreetMap tiles live in the browser, and
tile.openstreetmap.org blocks pages opened via `file://`. So serve the repo
and open the `http://` URL, never the file path:

```bash
lsof -ti:8756 >/dev/null || (cd <repo root> && python3 -m http.server 8756 --bind 127.0.0.1 &)
open "http://localhost:8756/<karte_3d file name>"
```

Start the server as a background task if it isn't running. Open the 3D page
unless the user asked for the 2D map; each page links to the other.

Don't commit the generated pages unless the user asks.

## Optional — record the ride as a video

Only when the user asks for a video/recording ("nimm es auf", "Video für
YouTube"):

```bash
python3 .claude/skills/kinomap-karte/scripts/record_video.py \
  <karte_..._3d.html> [--follow] [--speed N] [--out PATH] [--start S] [--end S]
```

It opens the 3D page with `?rec` in headless Chrome, steps the ride frame by
frame and writes an H.264 `.mp4` (1920x1080, 30 fps, no audio) next to the
page. Default is the overview camera at 30x (a 31 min ride ≈ 1 min of
video); `--follow` records in Mitfahren mode. Rendering takes roughly half a
second per frame — about 15 min for a 31 min ride at 30x — so run it as a
background task. It starts its own throwaway HTTP server. Prints one JSON
object (`video`, `groesse_mb`, `dauer_s`, …). Test a change with a short
slice first (`--start 600 --end 720`). The `.mp4` is not for git — never
commit it. Needs the `playwright` and `opencv-python` packages (encoding
goes through cv2/AVFoundation, not ffmpeg).

## Notes

- Output names: `karte_<D.M>_<slug>.html` and `..._3d.html`, where the date
  is the ride's start in local time and the slug comes from the route name
  in the export's `.gpx` ("Widnau - Widnau" → `widnau`). Rerunning for the
  same ride overwrites both.
- Never download OSM map tiles from a script to embed them — that gets
  blocked ("Access blocked" tiles). Only elevation tiles (AWS open data) and
  building footprints (Overpass) are fetched by the script.
- Track elevation (profile, cockpit, 2D hero) is read off the AWS heightmap at
  the rider's position, not taken from the export's `Altitude` column, so it
  matches the terrain drawn in 3D. Only with `--no-3d` (no heightmap) the 2D
  page falls back to the export's altitude. The 2D page's total climb counts
  a rise only once it reaches 3 m, because the terrain model is noisy.
- The export repeats each GPS fix for 2–3 seconds, so the 3D page moves the
  rider by the distance column along the route, not by the raw coordinates.
- A visible gap between the route's end and its start is real data (Kinomap
  videos often stop short of closing a loop), not a rendering bug.
- The 3D page takes `?t=<seconds>&follow` in the URL to start at a given
  point in follow mode, and `?shot` to make headless-Chrome screenshots work
  (`--headless=new --enable-unsafe-swiftshader --virtual-time-budget=20000`).
- Building heights come from OSM `height`/`building:levels` where tagged and
  are otherwise guessed from building type and footprint.
- Social-media preview: if the index thumbnail `karte_<D.M>_<slug>_3d.jpg` exists next to the
  page, the script derives `..._3d_og.jpg` (1200x630) from it and writes Open Graph / Twitter
  tags into the 3D page. For a new ride, put the thumbnail in place first, then rerun.
- `--vector` (only when the user asks for vector tiles for a ride): the 3D ground is drawn from
  Protomaps vector tiles instead of OSM raster tiles. The script cuts `karte_<D.M>_<slug>.pmtiles`
  (about 6 MB) out of the Protomaps planet build and the page reads it at runtime; needs
  `pip install pmtiles`. Once that file exists, plain reruns keep using it — commit it with the
  page. The result then carries `vektor` (and `vektorkacheln` when it was freshly cut).
- The 3D page needs numpy and Pillow at build time (elevation PNG decoding).

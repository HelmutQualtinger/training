#!/usr/bin/env python3
"""
kinomap_karte.py — turn a Kinomap activity export (export_<id>.zip with
.csv/.gpx/.tcx/.pwx inside) into two standalone map pages in the repo root:

    karte_<D.M>_<route>.html      2D OpenStreetMap (Leaflet) + profile chart
    karte_<D.M>_<route>_3d.html   three.js scene: terrain, buildings, cyclist
                                  avatar, playback, bike-computer cockpit

Only the per-second .csv (track) and the .gpx (route name, start time) are
read from the zip, straight from the archive — nothing is extracted to disk.
The script fills ../assets/karte_template.html and
../assets/karte3d_template.html by swapping the /*__DATA__*/null placeholder
for the ride's data; all layout/behaviour lives in those templates.

Network use (3D page only): elevation tiles (Mapzen "terrarium" PNGs from the
AWS open-data bucket) and building footprints (OpenStreetMap via Overpass)
are downloaded once and baked into the page. Both are cached in the system
temp dir, so a rerun after a flaky Overpass answer only refetches what is
missing. The OSM map *image* is deliberately not downloaded here — the pages
load those tiles live in the browser, which is why they must be served over
HTTP (tile.openstreetmap.org blocks file:// pages and scripted bulk fetches).

Usage
-----
    python3 kinomap_karte.py                 # newest export_*.zip
    python3 kinomap_karte.py path/to/export_abc.zip
    python3 kinomap_karte.py --no-3d         # 2D page only, no network

Flags:
    ZIP            export zip (default: newest export_*.zip in the repo root,
                   else in ~/Downloads)
    --out-dir DIR  where to write the pages (default: repo root)
    --radius M     keep buildings within M metres of the route (default: 800)
    --cached-buildings  don't query Overpass; use only building chunks already cached
    --no-3d        skip the 3D page (and all downloads)

Needs numpy and Pillow (to decode the elevation PNGs) for the 3D page.
"""
import argparse
import base64
import csv
import datetime
import hashlib
import html as html_lib
import io
import json
import math
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile
from pathlib import Path
from typing import NoReturn
from urllib.parse import quote as urlquote

PLACEHOLDER = "/*__DATA__*/null"
SOCIAL_PLACEHOLDER = "<!--__SOCIAL__-->"
SITE_URL = "https://helmutqualtinger.github.io/training/"   # GitHub Pages; previews need absolute URLs
OG_SIZE = (1200, 630)
UA = "training-log-map/1.0 (personal training log)"
EARTH = 40075016.686          # equatorial circumference, metres
PAD_KM = 2.5                  # terrain shown around the route's bounding box
MAX_TILES = 16                # per axis -> ground canvas of at most 4096 px
GRID_CELLS = 320000           # heightmap vertex budget (≈20 m grid for a 17 km loop)
OVERPASS = ["overpass-api.de", "maps.mail.ru/osm/tools/overpass", "overpass.private.coffee"]
LOW = {"garage", "garages", "shed", "hut", "roof", "carport", "greenhouse", "cabin", "kiosk", "service"}
HIGH = {"industrial": 9, "commercial": 10, "retail": 8, "warehouse": 9, "apartments": 13, "church": 18,
        "school": 10, "office": 12, "hospital": 14, "hotel": 12, "barn": 8, "farm_auxiliary": 6}


def fail(msg: str) -> NoReturn:
    print(json.dumps({"error": msg}, ensure_ascii=False))
    sys.exit(1)


def repo_root() -> Path:
    """.claude/skills/kinomap-karte/scripts/kinomap_karte.py -> repo root."""
    return Path(__file__).resolve().parents[4]


def newest_zip() -> Path:
    for folder in (repo_root(), Path.home() / "Downloads"):
        zips = sorted(folder.glob("export_*.zip"), key=lambda p: p.stat().st_mtime)
        if zips:
            return zips[-1]
    fail("Kein export_*.zip im Repo-Root oder in ~/Downloads gefunden — Pfad angeben.")


def read_export(zpath: Path):
    """-> (rows, route name, start datetime local). rows: dicts of floats per second."""
    try:
        z = zipfile.ZipFile(zpath)
    except (OSError, zipfile.BadZipFile) as e:
        fail(f"Kann {zpath} nicht lesen: {e}")
    names = z.namelist()
    csv_name = next((n for n in names if n.lower().endswith(".csv")), None)
    if not csv_name:
        fail(f"{zpath.name} enthält keine .csv — kein Kinomap-Export?")
    cols = {"lat": "Latitude", "lon": "Longitude", "ele": "Altitude", "dist": "Distance",
            "kmh": "Speed", "hf": "Heart rate", "watt": "Power", "rpm": "Cadence"}
    rows, prev = [], dict.fromkeys(cols, 0.0)
    for r in csv.DictReader(io.StringIO(z.read(csv_name).decode("utf-8", "replace"))):
        try:
            lat, lon = float(r["Latitude"]), float(r["Longitude"])
        except (KeyError, ValueError):
            continue                       # no position -> unusable for a map
        cur = {}
        for k, c in cols.items():          # a blank sensor value repeats the previous one
            try:
                cur[k] = float(r[c])
            except (KeyError, ValueError):
                cur[k] = prev[k]
        cur["lat"], cur["lon"] = lat, lon
        rows.append(cur)
        prev = cur
    if len(rows) < 10:
        fail(f"{csv_name}: zu wenige Zeilen mit Koordinaten ({len(rows)}).")

    name, start = None, None
    gpx_name = next((n for n in names if n.lower().endswith(".gpx")), None)
    if gpx_name:
        head = z.read(gpx_name)[:4000].decode("utf-8", "replace")
        m = re.search(r"<name>(.*?)</name>", head, re.S)
        if m:
            name = re.sub(r"^Kinomap\s*-\s*", "", m.group(1)).strip()
        m = re.search(r"<time>([^<]+)</time>", head)
        if m:
            try:
                start = datetime.datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).astimezone()
            except ValueError:
                pass
    if start is None:                      # fall back to the archive's own timestamp
        start = datetime.datetime(*z.getinfo(csv_name).date_time).astimezone()
    return rows, name or "Kinomap-Strecke", start


def slugify(name: str) -> str:
    parts = [p.strip() for p in name.split(" - ")]
    if len(set(parts)) == 1:               # "Widnau - Widnau" -> "widnau"
        parts = parts[:1]
    s = unicodedata.normalize("NFKD", " ".join(parts)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40].strip("-") or "strecke"


def session_nr(date_dm: str, seconds: int):
    """Nr of the training_log.csv row for this ride (same date, duration within 5 s), else None."""
    log = repo_root() / "training_log.csv"
    if not log.exists():
        return None
    best = None
    for r in csv.DictReader(log.open(encoding="utf-8")):
        if r.get("Datum") == date_dm and (r.get("Dauer_sek") or "").isdigit():
            d = abs(int(r["Dauer_sek"]) - seconds)
            if d <= 5 and (best is None or d < best[0]):
                best = (d, r.get("Nr"))
    return best[1] if best else None


def social_preview(out_dir: Path, base: str, title: str, desc: str) -> str:
    """Open Graph / Twitter tags for the 3D page, or "" if the ride has no thumbnail.

    The preview picture <base>_3d_og.jpg (1200x630, what the networks expect) is derived from
    the hand-made index thumbnail <base>_3d.jpg, shown whole on a blurred copy of itself —
    never cropped, because the thumbnails are often posters with lettering right up to the
    edge. Crawlers don't run scripts, so the tags have to be in the static HTML rather than
    set from the ride data at load time.
    """
    thumb = out_dir / f"{base}_3d.jpg"
    if not thumb.is_file():
        return ""
    from PIL import Image, ImageFilter, ImageOps
    src = Image.open(thumb).convert("RGB")
    og = ImageOps.fit(src, OG_SIZE, Image.LANCZOS).filter(ImageFilter.GaussianBlur(24))
    og = og.point(lambda v: v * 0.6)
    fg = ImageOps.contain(src, OG_SIZE, Image.LANCZOS)
    og.paste(fg, ((OG_SIZE[0] - fg.width) // 2, (OG_SIZE[1] - fg.height) // 2))
    og.save(out_dir / f"{base}_3d_og.jpg", quality=88)
    e = html_lib.escape
    page, img = SITE_URL + urlquote(f"{base}_3d.html"), SITE_URL + urlquote(f"{base}_3d_og.jpg")
    tags = [("property", "og:type", "website"), ("property", "og:site_name", "Ergometer Training Log"),
            ("property", "og:title", title), ("property", "og:description", desc),
            ("property", "og:url", page), ("property", "og:image", img),
            ("property", "og:image:width", str(OG_SIZE[0])), ("property", "og:image:height", str(OG_SIZE[1])),
            ("name", "twitter:card", "summary_large_image"), ("name", "twitter:title", title),
            ("name", "twitter:description", desc), ("name", "twitter:image", img),
            ("name", "description", desc)]
    return "\n".join(f'<meta {k}="{n}" content="{e(v)}">' for k, n, v in tags)


def render(template: str, data: dict, out: Path, social: str = "") -> None:
    html = (Path(__file__).resolve().parents[1] / "assets" / template).read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        fail(f"Platzhalter {PLACEHOLDER} fehlt in {template}.")
    html = html.replace(SOCIAL_PLACEHOLDER, social)
    # "<" escaped so nothing in the data can close the surrounding <script> block
    blob = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("<", "\\u003c")
    out.write_text(html.replace(PLACEHOLDER, blob), encoding="utf-8")


# ---------------------------------------------------------------- 3D data

def curl(url: str, out: Path, extra=()) -> bool:
    r = subprocess.run(["curl", "-sS", "-f", "-m", "90", "-H", "User-Agent: " + UA, *extra, "-o", str(out), url],
                       capture_output=True, text=True)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0


def overpass(q: str, cache: Path, fetch: bool = True):
    """Elements of an Overpass query, cached per query; None if no mirror answered."""
    fn = cache / ("osm_" + hashlib.sha1(q.encode()).hexdigest()[:16] + ".json")
    for attempt in range(6 if fetch else 1):
        if fn.exists():
            try:
                return json.loads(fn.read_text(encoding="utf-8"))["elements"]
            except (ValueError, KeyError):
                fn.unlink()        # an HTML error page, not JSON
        if not fetch:
            break
        host = OVERPASS[attempt % len(OVERPASS)]
        if not curl(f"https://{host}/api/interpreter", fn, ("--data-urlencode", "data=" + q)):
            time.sleep(4)
    return None


def tile_xy(lat, lon, z):
    n = 2 ** z
    return (lon + 180) / 360 * n, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n


def build_3d(rows, radius: float, cache: Path, fetch_buildings: bool = True) -> dict:
    try:
        import numpy as np
        from PIL import Image
    except ImportError as e:
        fail(f"Für die 3D-Seite fehlt ein Python-Paket ({e.name}): pip3 install numpy pillow — oder --no-3d.")

    lat = [r["lat"] for r in rows]
    lon = [r["lon"] for r in rows]
    lat0 = (min(lat) + max(lat)) / 2
    dlat = 1 / 111.2
    dlon = 1 / (111.32 * math.cos(math.radians(lat0)))
    n_, s_ = max(lat) + PAD_KM * dlat, min(lat) - PAD_KM * dlat
    w_, e_ = min(lon) - PAD_KM * dlon, max(lon) + PAD_KM * dlon

    # ground-texture zoom: deepest level whose tile grid still fits one canvas
    for zt in range(15, 8, -1):
        x0, y0 = (math.floor(v) for v in tile_xy(n_, w_, zt))
        x1, y1 = (math.floor(v) for v in tile_xy(s_, e_, zt))
        tx, ty = x1 - x0 + 1, y1 - y0 + 1
        if tx <= MAX_TILES and ty <= MAX_TILES:
            break
    else:
        fail("Strecke zu ausgedehnt für die 3D-Ansicht — --no-3d verwenden.")
    m_tile = EARTH * math.cos(math.radians(lat0)) / 2 ** zt      # metres per texture tile
    W, D = tx * m_tile, ty * m_tile

    def proj(la, lo):                      # -> scene metres, x east / z south, origin = area centre
        px, py = tile_xy(la, lo, zt)
        return (px - x0 - tx / 2) * m_tile, (py - y0 - ty / 2) * m_tile

    # --- heightmap from terrarium tiles: h = R*256 + G + B/256 - 32768
    zd = zt - 1                        # ≈7 m per pixel at zt=15, finer than the grid below
    f = 2
    dx0, dy0, dx1, dy1 = x0 // f, y0 // f, x1 // f, y1 // f
    dem = np.zeros(((dy1 - dy0 + 1) * 256, (dx1 - dx0 + 1) * 256))
    for x in range(dx0, dx1 + 1):
        for y in range(dy0, dy1 + 1):
            fn = cache / f"dem_{zd}_{x}_{y}.png"
            if not fn.exists():
                url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{zd}/{x}/{y}.png"
                if not any(curl(url, fn) or time.sleep(2) for _ in range(3)):
                    fail(f"Höhenkachel nicht ladbar: {url}")
            a = np.asarray(Image.open(fn).convert("RGB"), dtype=np.float64)
            dem[(y - dy0) * 256:(y - dy0 + 1) * 256, (x - dx0) * 256:(x - dx0 + 1) * 256] = \
                a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768
    step = max(15.0, math.sqrt(W * D / GRID_CELLS))
    nx, ny = round(W / step) + 1, round(D / step) + 1
    px = np.clip(((x0 + np.linspace(0, tx, nx)) / f - dx0) * 256 - 0.5, 0, dem.shape[1] - 1.001)
    py = np.clip(((y0 + np.linspace(0, ty, ny)) / f - dy0) * 256 - 0.5, 0, dem.shape[0] - 1.001)
    ix, iy = px.astype(int), py.astype(int)
    fx, fy = px - ix, (py - iy)[:, None]
    g = lambda r, c: dem[np.ix_(r, c)]
    H = (g(iy, ix) * (1 - fx) + g(iy, ix + 1) * fx) * (1 - fy) + (g(iy + 1, ix) * (1 - fx) + g(iy + 1, ix + 1) * fx) * fy
    # --- track
    xz = [proj(r["lat"], r["lon"]) for r in rows]
    T = np.array(xz)

    # --- track elevation: read off the heightmap, not taken from the export's Altitude column, so the
    # numbers agree with the terrain the rider is drawn on. Sampled where the 3D page puts the rider:
    # the distance column mapped onto the route polyline (the export repeats each GPS fix for 2-3 s).
    keep = [0]
    for i in range(1, len(T)):
        if np.hypot(*(T[i] - T[keep[-1]])) > 0.1:
            keep.append(i)
    R = T[keep]
    cum = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(R, axis=0).T))])
    dist = np.array([r["dist"] for r in rows])
    along = dist * (cum[-1] / dist[-1]) if dist[-1] > 0 and cum[-1] > 0 else np.zeros(len(rows))
    rx, rz = np.interp(along, cum, R[:, 0]), np.interp(along, cum, R[:, 1])
    u = np.clip((rx / W + 0.5) * (nx - 1), 0, nx - 1.001)
    v = np.clip((rz / D + 0.5) * (ny - 1), 0, ny - 1.001)
    iu, iv = u.astype(int), v.astype(int)
    fu, fv = u - iu, v - iv
    at = lambda H: (H[iv, iu] * (1 - fu) + H[iv, iu + 1] * fu) * (1 - fv) + (H[iv + 1, iu] * (1 - fu) + H[iv + 1, iu + 1] * fu) * fv

    # --- the terrarium tiles carry the sea floor too (and the odd -5000 m glitch pixel): on an island
    # that drops the scene's zero level kilometres below the route, and a coast road dips into the sea
    # wherever a pixel is half water. Water is drawn flat at sea level — unless the route itself lies
    # below it (a depression), then the heights stay as they are.
    if np.median(at(H)) >= 0:
        H = np.maximum(H, 0.0)
        # The elevation model is coarse (≈30 m) and its shoreline doesn't match the map's: where it
        # runs further out than the OSM coast, the water of the ground texture is draped over rising
        # ground and the sea seems to climb the shore. So everything seaward of the OSM coastline is
        # flattened to 0 as well. The coastline ways are drawn as a barrier into a raster of half a
        # grid cell, the areas between them flood-filled, and each area is sea or land by majority
        # vote of its shore: OSM coastlines have the land on their left and the water on their right.
        pad = 360 / 2 ** zt
        coast = overpass('[out:json][timeout:60];way["natural"="coastline"](%.5f,%.5f,%.5f,%.5f);out geom;'
                         % (s_ - pad, w_ - pad, n_ + pad, e_ + pad), cache)
        if coast is None:
            print("Küstenlinie nicht ladbar — Meer nur nach Höhenmodell.", file=sys.stderr)
        elif coast:
            from PIL import ImageDraw
            res = step / 2
            mw, mh = math.ceil(W / res), math.ceil(D / res)
            img = Image.new("I", (mw, mh), 0)
            draw = ImageDraw.Draw(img)
            ways = [[((x + W / 2) / res, (z + D / 2) / res) for x, z in (proj(p["lat"], p["lon"]) for p in e["geometry"])]
                    for e in coast if "geometry" in e]
            for w in ways:
                draw.line(w, fill=1)
            votes = {}
            for w in ways:
                for (ax, ay), (bx, by) in zip(w, w[1:]):
                    ln = math.hypot(bx - ax, by - ay)
                    if ln < 1e-6:
                        continue
                    rx_, ry_ = -(by - ay) / ln * 1.5, (bx - ax) / ln * 1.5      # 1.5 px to the right (z points south)
                    for side, v in ((1, 1), (-1, -1)):
                        sx, sy = int((ax + bx) / 2 + side * rx_), int((ay + by) / 2 + side * ry_)
                        if not (0 <= sx < mw and 0 <= sy < mh):
                            continue
                        lab = img.getpixel((sx, sy))
                        if lab == 0:
                            lab = len(votes) + 2
                            ImageDraw.floodfill(img, (sx, sy), lab)
                            votes[lab] = 0
                        if lab > 1:
                            votes[lab] += v
            sea = np.isin(np.asarray(img), [lab for lab, v in votes.items() if v > 0])
            mx = np.minimum((np.linspace(0, W, nx) / res).astype(int), mw - 1)
            my = np.minimum((np.linspace(0, D, ny) / res).astype(int), mh - 1)
            H[sea[np.ix_(my, mx)]] = 0.0
    ele = at(H)
    hmin = float(H.min())
    h_b64 = base64.b64encode(np.round((H - hmin) * 10).astype("<u2").tobytes()).decode()

    track = dict(
        x=[round(p[0], 1) for p in xz], z=[round(p[1], 1) for p in xz],
        ele=[round(float(e), 1) for e in ele], dist=[round(r["dist"]) for r in rows], kmh=[r["kmh"] for r in rows],
        hf=[round(r["hf"]) for r in rows], watt=[round(r["watt"]) for r in rows], rpm=[round(r["rpm"]) for r in rows])

    # --- buildings: Overpass in small chunks (large boxes time out), cached per chunk
    pad_lat, pad_lon = (radius + 200) / 1000 * dlat, (radius + 200) / 1000 * dlon
    b_s, b_n, b_w, b_e = min(lat) - pad_lat, max(lat) + pad_lat, min(lon) - pad_lon, max(lon) + pad_lon
    n_lat, n_lon = math.ceil((b_n - b_s) / 0.0225), math.ceil((b_e - b_w) / 0.07)
    elements, missing = [], 0
    for i in range(n_lat):
        for j in range(n_lon):
            box = (b_s + (b_n - b_s) * i / n_lat, b_w + (b_e - b_w) * j / n_lon,
                   b_s + (b_n - b_s) * (i + 1) / n_lat, b_w + (b_e - b_w) * (j + 1) / n_lon)
            q = '[out:json][timeout:60];way["building"](%.5f,%.5f,%.5f,%.5f);out geom;' % box
            got = overpass(q, cache, fetch_buildings)
            if got is None:
                missing += 1
            else:
                elements += got

    seen, blds = set(), []
    for e in elements:
        if e.get("id") in seen or "geometry" not in e:
            continue
        seen.add(e["id"])
        pts = [proj(p["lat"], p["lon"]) for p in e["geometry"]]
        if pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) < 3:
            continue
        cx, cz = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        if abs(cx) > W / 2 - 20 or abs(cz) > D / 2 - 20:
            continue
        if np.min(np.hypot(T[:, 0] - cx, T[:, 1] - cz)) > radius:
            continue
        k = len(pts)
        area = abs(sum(pts[i][0] * pts[(i + 1) % k][1] - pts[(i + 1) % k][0] * pts[i][1] for i in range(k))) / 2
        if area < 12:
            continue
        # height: OSM tag if present, else levels, else a guess from type and footprint
        t = e.get("tags", {})
        kind, h = t.get("building", "yes"), None
        try:
            h = float(str(t["height"]).replace("m", "").replace(",", ".").strip())
        except (KeyError, ValueError):
            pass
        if h is None:
            try:
                h = float(t["building:levels"]) * 3 + 1.5
            except (KeyError, ValueError):
                pass
        if h is None:
            jit = (e["id"] % 7) * 0.5
            h = 3 if kind in LOW or area < 35 else HIGH[kind] if kind in HIGH else 9 + jit if area > 1500 else 6.5 + jit
        # [height_dm, x0_dm, z0_dm, dx1, dz1, ...] — delta-encoded outline keeps the page small
        d = [round(h * 10), round(pts[0][0] * 10), round(pts[0][1] * 10)]
        for i in range(1, k):
            d += [round(pts[i][0] * 10) - round(pts[i - 1][0] * 10), round(pts[i][1] * 10) - round(pts[i - 1][1] * 10)]
        blds.append(d)

    return dict(W=round(W, 1), D=round(D, 1), nx=nx, ny=ny, hmin=round(hmin, 1), H=h_b64, track=track, blds=blds,
                zt=zt, x0=x0, y0=y0, tx=tx, ty=ty, _hmax=float(H.max()), _missing=missing, _chunks=n_lat * n_lon)


def main() -> None:
    ap = argparse.ArgumentParser(description="Kinomap-Export -> 2D- und 3D-Kartenseite")
    ap.add_argument("zip", nargs="?", help="Export-Zip (default: neuestes export_*.zip)")
    ap.add_argument("--out-dir", help="Zielordner (default: Repo-Root)")
    ap.add_argument("--radius", type=float, default=800, help="Gebäude im Umkreis der Strecke, Meter (default: 800)")
    ap.add_argument("--no-3d", action="store_true", help="nur die 2D-Karte erzeugen, keine Downloads")
    ap.add_argument("--cached-buildings", action="store_true",
                    help="Overpass nicht abfragen, nur schon gecachte Gebäude-Kacheln verwenden")
    args = ap.parse_args()

    zpath = Path(args.zip).expanduser() if args.zip else newest_zip()
    if not zpath.is_file():
        fail(f"Zip nicht gefunden: {zpath}")
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else repo_root()
    if not out_dir.is_dir():
        fail(f"Zielordner nicht gefunden: {out_dir}")

    rows, name, start = read_export(zpath)
    date_dm = f"{start.day}.{start.month}"
    seconds = len(rows) - 1
    nr = session_nr(date_dm, seconds)
    base = f"karte_{date_dm}_{slugify(name)}"
    file2d, file3d = base + ".html", base + "_3d.html"
    sub = f"{date_dm}.{start.year}, {start:%H:%M} · Kinomap"
    if nr:
        sub = f"Einheit {nr} · " + sub
    meta = dict(title=name.replace(" - ", " – "), sub=sub, file2d=file2d, file3d=file3d)

    result = {
        "zip": str(zpath), "strecke": meta["title"], "datum": date_dm, "einheit": nr,
        "km": round(rows[-1]["dist"] / 1000, 2), "dauer": f"{seconds // 60}:{seconds % 60:02d}",
        "avg_watt": round(sum(r["watt"] for r in rows) / len(rows)),
        "karte_2d": str(out_dir / file2d),
    }
    ele = [r["ele"] for r in rows]             # --no-3d has no heightmap: fall back to the export's altitude
    if not args.no_3d:
        cache = Path(tempfile.gettempdir()) / "kinomap_karte_cache"
        cache.mkdir(exist_ok=True)
        d3 = build_3d(rows, args.radius, cache, not args.cached_buildings)
        hmax, missing, chunks = d3.pop("_hmax"), d3.pop("_missing"), d3.pop("_chunks")
        d3["meta"] = meta
        ele = d3["track"]["ele"]
        km = f"{rows[-1]['dist'] / 1000:.1f}".replace(".", ",")
        desc = (f"{km} km · {seconds // 60}:{seconds % 60:02d} min · Ø {result['avg_watt']} W — "
                f"die Ergometer-Fahrt vom {date_dm}.{start.year} in 3D mitfahren.")
        render("karte3d_template.html", d3, out_dir / file3d,
               social_preview(out_dir, base, meta["title"] + " in 3D", desc))
        result.update({
            "karte_3d": str(out_dir / file3d),
            "gebiet_km": [round(d3["W"] / 1000, 1), round(d3["D"] / 1000, 1)],
            "hoehe_m": [round(d3["hmin"]), round(hmax)],
            "gebaeude": len(d3["blds"]),
        })
        if missing:
            result["warnung"] = (f"Gebäudedaten unvollständig: {missing} von {chunks} Overpass-Abfragen "
                                 "fehlgeschlagen — später erneut ausführen (bereits Geladenes ist gecacht).")
    render("karte_template.html", dict(
        meta=meta,
        lat=[round(r["lat"], 6) for r in rows], lon=[round(r["lon"], 6) for r in rows],
        ele=ele, dist=[round(r["dist"]) for r in rows], kmh=[r["kmh"] for r in rows],
        hf=[round(r["hf"]) for r in rows], watt=[round(r["watt"]) for r in rows], rpm=[round(r["rpm"]) for r in rows],
    ), out_dir / file2d)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

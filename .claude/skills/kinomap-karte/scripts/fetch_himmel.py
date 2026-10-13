#!/usr/bin/env python3
"""
fetch_himmel.py — writes ../assets/himmel.json, the night sky of the 3D map page: naked-eye stars from the
Yale Bright Star Catalog (BSC5P) and the 110 Messier objects, both queried from NASA's HEASARC archive
(https://heasarc.gsfc.nasa.gov, TAP service, no key), plus the 88 constellations of Stellarium's "modern"
sky culture: stick-figure lines and, for the illustrations by Johan Meuris, the three anchor stars that pin
each picture to the sky. Stellarium names those stars by Hipparcos number; their positions come from
HEASARC's Hipparcos table. And for each Messier object the picture NASA's "Hubble's Messier Catalog" shows
for it (science.nasa.gov, one page per object; Hubble has not observed all 110), which the page opens on a
click. Only the data is stored — the page loads the pictures themselves from a CDN or from NASA.
The file is committed; rerun this only to change the magnitude limit or the Stellarium version.
kinomap_karte.py embeds it into every 3D page.

    python3 fetch_himmel.py [--mag 5.6]
"""
import argparse
import base64
import html
import json
import re
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

STELLARIUM = "v25.2"      # git tag the sky culture is read from; the page loads the pictures from the same tag
TAP = "https://heasarc.gsfc.nasa.gov/xamin/vo/tap/sync"
# one page per observed object, "<HUBBLE>messier-<n>/"
HUBBLE = "https://science.nasa.gov/mission/hubble/science/explore-the-night-sky/hubble-messier-catalog/"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"


def query(adql: str, types: str) -> list[tuple]:
    """Run one ADQL query and decode the binary VOTable. `types`: one letter per column — d double, f float, s string."""
    xml = subprocess.run(
        ["curl", "-s", "-m", "120", "-H", f"User-Agent: {UA}", TAP, "--data-urlencode", "REQUEST=doQuery",
         "--data-urlencode", "LANG=ADQL", "--data-urlencode", "FORMAT=votable", "--data-urlencode", f"QUERY={adql}"],
        capture_output=True, text=True, check=True).stdout
    m = re.search(r"<STREAM[^>]*>(.*?)</STREAM>", xml, re.S)
    if 'value="OK"' not in xml or not m:
        sys.exit(f"HEASARC query failed: {adql}\n{xml[:400]}")
    buf, pos, rows = base64.b64decode(m[1]), 0, []
    while pos < len(buf):
        row = []
        for t in types:
            if t == "s":
                (n,) = struct.unpack_from(">i", buf, pos)
                row.append(buf[pos + 4:pos + 4 + n].decode("latin-1").strip())
                pos += 4 + n
            else:
                size = 8 if t == "d" else 4
                row.append(struct.unpack_from(">" + t, buf, pos)[0])
                pos += size
        rows.append(tuple(row))
    return rows


def hubble(n: int) -> list | None:
    """Picture and popular name of Messier n from its page in Hubble's Messier Catalog: [picture URL, name].
    None when there is no such page — Hubble has not observed that object."""
    page = subprocess.run(["curl", "-sL", "-m", "60", "-H", f"User-Agent: {UA}", f"{HUBBLE}messier-{n}/"],
                          capture_output=True, text=True).stdout
    img = re.search(r'<meta property="og:image" content="(https://[^"?]+)', page)
    title = re.search(r'<meta property="og:title" content="Messier ' + str(n) + r'\b([^"]*)"', page)
    if not img or not title:
        return None
    name = re.search(r"\(([^)]+)\)", title[1])
    return [img[1], html.unescape(name[1]) if name else ""]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mag", type=float, default=5.6, help="faintest star to keep (default 5.6)")
    args = ap.parse_args()
    stars = query(f"SELECT ra,dec,vmag,bv_color FROM bsc5p WHERE vmag<{args.mag}", "ddff")
    dso = query("SELECT name,ra,dec,vmag,object_type FROM messier", "sddfs")
    culture = json.loads(subprocess.run(
        ["curl", "-s", "-m", "60", "-H", f"User-Agent: {UA}",
         f"https://raw.githubusercontent.com/Stellarium/stellarium/{STELLARIUM}/skycultures/modern/index.json"],
        capture_output=True, text=True, check=True).stdout)["constellations"]
    hips = sorted({h for c in culture for line in c["lines"] for h in line if isinstance(h, int)}
                  | {a["hip"] for c in culture for a in c.get("image", {}).get("anchors", [])})
    where = {h: (round(r, 2), round(d, 2)) for h, r, d in
             query(f"SELECT hip_number,ra,dec FROM hipparcos WHERE hip_number IN ({','.join(map(str, hips))})", "idd")}
    lost = [h for h in hips if h not in where]
    if lost:
        print(f"Hipparcos stars without a position (lines/anchors through them are dropped): {lost}", file=sys.stderr)
    bilder = []
    for c in culture:
        entry = {"n": c["common_name"]["native"],
                 # each line as a flat [ra, dec, ra, dec, ...] polyline
                 "l": [[v for h in line if h in where for v in where[h]] for line in c["lines"]]}
        img = c.get("image")
        if img and all(a["hip"] in where for a in img["anchors"]):
            entry["f"] = img["file"].split("/")[-1]
            entry["s"] = img["size"]
            entry["a"] = [[*a["pos"], *where[a["hip"]]] for a in img["anchors"]]   # [px, py, ra, dec] x 3
        bilder.append(entry)
    with ThreadPoolExecutor(8) as pool:
        pics = {f"M{n}": p for n, p in zip(range(1, 111), pool.map(hubble, range(1, 111))) if p}
    nan = lambda v, d: d if v != v else v          # NaN -> default
    data = {
        "quelle": "NASA HEASARC: Bright Star Catalog BSC5P, Messier Catalog, Hipparcos; Stellarium modern sky culture",
        "stellarium": STELLARIUM,
        # [ra°, dec°, V mag, B−V], brightest first
        "sterne": [[round(r, 2), round(d, 2), round(v, 1), round(nan(bv, 0.6), 1)] for r, d, v, bv in sorted(stars, key=lambda s: s[2])],
        # [name, ra°, dec°, V mag, type]
        "deepsky": [[n.replace(" ", ""), round(r, 2), round(d, 2), round(nan(v, 9), 1), t] for n, r, d, v, t in dso],
        # constellations: n name, l lines, and for the illustrated ones f file, s pixel size, a anchors
        "bilder": bilder,
        # Hubble pictures: Messier name -> [picture URL, popular name]; its page is "hubble_seite" + "messier-<n>/"
        "hubble_seite": HUBBLE,
        "hubble": pics,
    }
    out = Path(__file__).resolve().parents[1] / "assets" / "himmel.json"
    out.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"out": str(out), "sterne": len(data["sterne"]), "deepsky": len(data["deepsky"]), "sternbilder": len(bilder), "mit_bild": sum("f" in b for b in bilder), "hubble": len(pics), "kb": round(out.stat().st_size / 1024)}))


if __name__ == "__main__":
    main()

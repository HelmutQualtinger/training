#!/usr/bin/env python3
"""
record_video.py — render a karte_*_3d.html ride from start to finish into an
H.264 .mp4 (YouTube-ready: 1920x1080, 30 fps, no audio).

The page is opened in headless Chrome with ?rec, which switches off its own
animation loop; this script then advances the ride frame by frame
(window.__step), screenshots each frame and feeds it to the encoder — so the
video is perfectly smooth however slow rendering is. The repo is served over
a throwaway local HTTP port for the duration (the page loads its map tiles
live and tile.openstreetmap.org blocks file:// pages).

Usage
-----
    python3 record_video.py karte_6.10_widnau_3d.html
    python3 record_video.py karte_6.10_widnau_3d.html --follow --speed 20

Flags:
    PAGE           the 3D page (file name in the repo root, or a path)
    --out PATH     output file (default: PAGE with .mp4, in the repo root)
    --speed N      ride seconds per video second (default: 30 -> a 31 min
                   ride becomes about a minute)
    --follow       record in "Mitfahren" mode (chase camera) instead of the
                   overview
    --size WxH     frame size (default: 1920x1080)
    --fps N        frame rate (default: 30)
    --start S / --end S   only this part of the ride, in ride seconds

Needs the playwright Python package driving the installed Google Chrome, and
opencv-python (cv2) for encoding — cv2 writes H.264 through macOS'
AVFoundation, so no ffmpeg is involved.
"""
import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn


UI_SCALE = 1.5


def fail(msg: str) -> NoReturn:
    print(json.dumps({"error": msg}, ensure_ascii=False))
    sys.exit(1)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def main() -> None:
    ap = argparse.ArgumentParser(description="3D-Kartenseite als Video aufnehmen")
    ap.add_argument("page")
    ap.add_argument("--out")
    ap.add_argument("--speed", type=float, default=30)
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--end", type=float)
    args = ap.parse_args()

    try:
        import cv2
        import numpy as np
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        fail(f"Python-Paket fehlt ({e.name}): pip3 install playwright opencv-python numpy")

    root = repo_root()
    page_path = Path(args.page)
    if not page_path.is_absolute():
        page_path = root / page_path
    if not page_path.is_file() or page_path.parent != root:
        fail(f"3D-Seite nicht im Repo-Root gefunden: {page_path}")
    out = Path(args.out).expanduser() if args.out else page_path.with_suffix(".mp4")
    try:
        w, h = (int(v) for v in args.size.lower().split("x"))
    except ValueError:
        fail("--size als BREITExHÖHE angeben, z.B. 1920x1080")

    with socket.socket() as s:             # any free port
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                              cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://localhost:{port}/{page_path.name}?rec&t={args.start:g}" + ("&follow" if args.follow else "")
    frames, t0 = 0, time.time()
    try:
        time.sleep(1)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True, args=[
                "--use-angle=metal", "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"])
            # CSS viewport is 1/UI_SCALE of the frame, so panels and dials come out larger and readable in the video
            pg = browser.new_page(viewport={"width": round(w / UI_SCALE), "height": round(h / UI_SCALE)},
                                  device_scale_factor=UI_SCALE)
            pg.goto(url)
            pg.wait_for_function("typeof window.__step === 'function'", timeout=60000)
            pg.wait_for_timeout(10000)     # let the ground texture tiles arrive
            n = pg.evaluate("JSON.parse(document.getElementById('data').textContent).track.x.length")
            end = min(args.end if args.end is not None else n - 1, n - 1)
            vw = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"avc1"), args.fps, (w, h))
            if not vw.isOpened():
                fail("Video-Encoder (avc1) lässt sich nicht öffnen.")

            def grab(repeat=1):
                nonlocal frames
                img = cv2.imdecode(np.frombuffer(pg.screenshot(type="jpeg", quality=95), np.uint8), cv2.IMREAD_COLOR)
                if img.shape[1] != w or img.shape[0] != h:
                    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
                for _ in range(repeat):
                    vw.write(img)
                frames += repeat

            pg.evaluate("window.__step(0)")
            pg.wait_for_timeout(3000)      # sharper ground around the start position
            pg.evaluate("window.__step(0)")
            grab(args.fps)                 # hold the first frame for a second
            t, step = args.start, args.speed / args.fps
            while t < end:
                t += step
                pg.evaluate(f"window.__step({step})")
                grab()
                if frames % 150 == 0:
                    print(f"… {t / end * 100:.0f} %", file=sys.stderr, flush=True)
            grab(2 * args.fps)             # and the last one for two
            vw.release()
            browser.close()
    finally:
        server.terminate()
    print(json.dumps({
        "video": str(out), "groesse_mb": round(out.stat().st_size / 1e6, 1),
        "dauer_s": round(frames / args.fps, 1), "aufloesung": f"{w}x{h}", "fps": args.fps,
        "modus": "Mitfahren" if args.follow else "Übersicht", "tempo": f"{args.speed:g}x",
        "renderzeit_s": round(time.time() - t0),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

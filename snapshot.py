#!/usr/bin/env python3
"""
MERIDIAN Grid  –  Snapshot für GitHub Pages
============================================

Holt ENTSO-E-Daten (A75/A65 + A11), kalibriert das Modell und schreibt ein
komplett statisches Bundle:

    <out>/index.html        die interaktive Karte (Endpoint → data/grid.json)
    <out>/data/grid.json    der volle /api/grid-Zustand (kalibriert)

Damit läuft der Simulator OHNE Server: GitHub Actions ruft dieses Skript
per Cron auf und deployt <out> nach GitHub Pages. Der Browser rechnet alle
Schocks und den N-1-Stresstest lokal (der JS-Solver ist identisch zur
Python-Engine); nur der Ist-Zustand kommt aus dem Snapshot.

Aufruf:
    ENTSOE_KEY=... python3 snapshot.py --out docs

Exit-Codes:
    0  Snapshot geschrieben (auch bei Teilausfällen einzelner Zonen)
    1  kein Key oder praktisch keine Daten – Job soll fehlschlagen, damit
       Pages den LETZTEN GUTEN Snapshot behält statt Defaults zu zeigen.
"""

import argparse
import json
import os
import sys
import logging
from datetime import datetime, timezone

import meridian_grid_live as L

log = logging.getLogger("snapshot")

MIN_LIVE_ZONES = 10   # darunter ist der Snapshot wertlos → Job fehlschlagen


def build(out_dir: str) -> int:
    if not L.ENTSOE_KEY:
        print("FEHLER: Kein ENTSOE_KEY (Umgebungsvariable oder .env).")
        print("GitHub Actions: Repository Secret ENTSOE_KEY anlegen.")
        return 1

    L.init_fetchers()
    print("Abruf: 40 Zonen (A75/A65) + 83 Grenzen (A11) ...")
    try:
        L.refresh_now()
    except Exception as e:
        print(f"FEHLER beim Abruf: {e}")
        return 1

    res = L.solve_live()
    live = res["live"]
    n_live = live["zones_live"]
    print(f"Zonen live: {n_live}/{live['zones_total']} | "
          f"Netzschnitt: {live.get('target_ts')} | "
          f"Qualität: {live['quality']} | "
          f"kalibriert: {live['calibrated']}")
    if live.get("calibration"):
        c = live["calibration"]
        print(f"A11: {c['n_measured']} Grenzen, {c['n_scaled']} gefittet, "
              f"Ø-Fehler {c.get('mean_abs_error_mw')} MW, "
              f"{c['n_ntc_raised']} NTC angehoben")

    if n_live < MIN_LIVE_ZONES:
        print(f"FEHLER: nur {n_live} Zonen live (< {MIN_LIVE_ZONES}) – "
              "Snapshot wird verworfen, letzter guter Stand bleibt online.")
        return 1

    # ── Bundle schreiben ────────────────────────────────────────────────────
    data_dir = os.path.join(out_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    res["snapshot"] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": "ENTSO-E Transparency Platform (transparency.entsoe.eu)",
        "note": ("Statischer Snapshot; Schocks und N-1 rechnet der Browser "
                 "lokal auf diesem Zustand."),
    }
    with open(os.path.join(data_dir, "grid.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, separators=(",", ":"))

    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "meridian_grid_viz.html"), encoding="utf-8") as f:
        html = f.read()
    html = html.replace(
        "<script>",
        '<script>window.GRID_LIVE_ENDPOINT="data/grid.json";</script>\n<script>',
        1)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    size = os.path.getsize(os.path.join(data_dir, "grid.json"))
    print(f"Geschrieben: {out_dir}/index.html + data/grid.json ({size//1024} kB)")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs", help="Zielordner (Default: docs)")
    args = ap.parse_args()
    sys.exit(build(args.out))

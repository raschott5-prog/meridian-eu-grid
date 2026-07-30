#!/usr/bin/env python3
"""
Reproduziert die am 23.07. real beobachteten ENTSO-E-Pathologien und prüft,
ob die zeitliche Ausrichtung sie behebt.

Beobachtet wurde:
  IT   Last 15.4 GW statt ~48 GW   (Teilzonen unvollständig)
  AT   248 min alt, DE 38 min      (3.5 h Versatz im selben Lastfluss)
  BE   3.6 GW statt 7-11 GW        (Erzeugungstypen fehlen in jungen Scheiben)
  CH   4.1 GW statt 6-9 GW         (dito)
  NL   6.2 GW statt 10-16 GW       (dito)
  SI   keine Last                  (A65 liefert nichts)
"""
import sys
from datetime import datetime, timezone, timedelta
import meridian_grid_live as L
from meridian_grid import ZONES

NSU = 'urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0'
FAILS = []
def check(n, c, d=""):
    print(f"  {'✓' if c else '✗'} {n}{'  → '+d if d else ''}")
    if not c: FAILS.append(n)

NOW = datetime.now(timezone.utc).replace(second=0, microsecond=0)
NOW = NOW.replace(minute=(NOW.minute // 15) * 15)

def slot(minutes_ago):
    return NOW - timedelta(minutes=minutes_ago)

def fmt(t): return t.strftime("%Y-%m-%dT%H:%MZ")

# ── Zonenprofile: (typen, last_gesamt, pump, verzug_min, lueckige_typen) ─────
# lueckige_typen = Typen, die 120 min früher aufhören zu melden
PROFILE = {
    "FR": (dict(B14=44000, B04=2800, B11=5000, B12=3500, B19=4200, B16=900), 47000, 800, 38, []),
    "DE": (dict(B02=9000, B05=4500, B04=7500, B19=26000, B18=5500, B16=6200, B01=4800), 60300, 1200, 38, []),
    "ES": (dict(B14=5800, B04=5500, B11=2000, B12=2200, B19=8200, B16=6500), 34400, 600, 60, []),
    "PT": (dict(B04=1400, B11=900, B12=700, B19=2100, B16=1000), 6700, 200, 83, []),
    "AT": (dict(B11=3400, B12=2400, B04=850, B19=1050, B16=250, B01=100), 7400, 1500, 60, []),
    "CH": (dict(B14=2100, B11=2600, B12=2900, B16=200), 6400, 1100, 83, ["B14", "B11"]),
    "NL": (dict(B04=5800, B05=1400, B19=3300, B16=950), 8400, 0, 53, ["B04", "B05"]),
    "BE": (dict(B14=3900, B04=2400, B19=1450, B16=680), 9500, 0, 143, ["B14", "B04"]),
    "IT": (dict(B04=13500, B11=3500, B12=2600, B16=4100, B19=1900), 48000, 900, 53, []),
    "CZ": (dict(B14=3700, B02=2900, B04=480, B16=760), 7500, 300, 53, []),
    "SK": (dict(B14=1850, B11=680, B04=480), 2900, 200, 98, []),
    "HU": (dict(B14=1850, B04=1150, B16=780), 4600, 0, 38, []),
    "PL": (dict(B02=8000, B05=7800, B04=1900, B19=2900, B16=950), 23200, 400, 53, []),
    "HR": (dict(B11=700, B12=500, B04=380, B19=290), 2100, 0, 68, []),
    "SI": (dict(B14=680, B11=300, B12=280, B02=290), None, 150, 38, []),  # Last fehlt
}
IT_SUBZONES = 6   # Italien meldet 6 Gebotszonen als separate TimeSeries


def points(start_ago, end_ago, base):
    """Punkte im 15-Min-Raster von start_ago bis end_ago (Minuten zurück)."""
    out = []
    m = start_ago
    i = 0
    while m >= end_ago:
        out.append((slot(m), base * (0.97 + 0.01 * (i % 4))))
        m -= 15
        i += 1
    return out


def ts_block(psr, pts, cons=False):
    if not pts:
        return ""
    t0 = pts[0][0]
    body = "".join(f"<Point><position>{i+1}</position><quantity>{v:.1f}</quantity></Point>"
                   for i, (_, v) in enumerate(pts))
    dom = "outBiddingZone_Domain.mRID" if cons else "inBiddingZone_Domain.mRID"
    psr_el = f"<MktPSRType><psrType>{psr}</psrType></MktPSRType>" if psr else ""
    return (f'<TimeSeries><{dom} codingScheme="A01">X</{dom}>{psr_el}'
            f"<Period><timeInterval><start>{fmt(t0)}</start><end>{fmt(t0)}</end>"
            f"</timeInterval><resolution>PT15M</resolution>{body}</Period></TimeSeries>")


def doc(body):
    return f'<?xml version="1.0"?><GL_MarketDocument xmlns="{NSU}">{body}</GL_MarketDocument>'.encode()


DOM2Z = {v: k for k, v in L.ZONE_DOMAINS.items()}
WINDOW = 480   # 8 h


class MockResp:
    def __init__(self, c, s=200):
        self.content = c; self.status_code = s; self.text = c.decode(errors="ignore")


class MockHTTP:
    """ENTSO-E mit den real beobachteten Macken."""
    def __init__(self): self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        p = params or {}
        dom = p.get("in_Domain") or p.get("outBiddingZone_Domain")
        z = DOM2Z.get(dom)
        if z is None:
            return MockResp(b"<Acknowledgement_MarketDocument/>")
        types, load, pump, lag, sparse = PROFILE[z]

        if p.get("documentType") == "A75":
            body = ""
            for psr, mw in types.items():
                # lückige Typen hören 120 min früher auf zu melden
                stop = lag + (120 if psr in sparse else 0)
                body += ts_block(psr, points(WINDOW, stop, mw))
            if pump:
                body += ts_block("B10", points(WINDOW, lag, pump), cons=True)
            return MockResp(doc(body))

        # A65 – Last
        if load is None:                      # SI: keine Last
            return MockResp(b"<Acknowledgement_MarketDocument/>")
        if z == "IT":
            # Italien: 6 Teilzonen. Die JÜNGSTEN 2 Scheiben melden nur 1 Teilzone
            # → genau der beobachtete 15.4-GW-Fehler.
            body = ""
            per = load / IT_SUBZONES
            for i in range(IT_SUBZONES):
                stop = lag if i == 0 else lag + 30
                body += ts_block("", points(WINDOW, stop, per))
            return MockResp(doc(body))
        return MockResp(doc(ts_block("", points(WINDOW, lag, load))))


print("=" * 74)
print("  REGRESSION: reproduziert die real beobachteten ENTSO-E-Pathologien")
print("=" * 74)

http = MockHTTP()
f = L.EntsoeGridFetcher("k", http=http)
fetched = f.fetch_all()
L.STATE.update(fetched)

# ── Vorher: was die naive "letzter Punkt"-Logik ergeben hätte ───────────────
print("\n  NAIVE LOGIK (letzter Punkt je Zone) – zur Kontrolle:")
naive = {}
for z, d in fetched.items():
    if not d["ok"]:
        continue
    gs, ls = d["gen_series"], d["load_series"]
    g_ts = max({t for s in gs.values() for t in s})
    g = sum(s[g_ts] for s in gs.values() if g_ts in s)
    l_ts = max(ls)
    naive[z] = (g / 1000, ls[l_ts][0] / 1000, int((NOW - g_ts).total_seconds() / 60))
print(f"    IT Last naiv: {naive['IT'][1]:.1f} GW   (real beobachtet: 15.4 GW)")
print(f"    BE Gen  naiv: {naive['BE'][0]:.1f} GW   (real beobachtet: 3.6 GW)")
print(f"    CH Gen  naiv: {naive['CH'][0]:.1f} GW   (real beobachtet: 4.1 GW)")
ages = [v[2] for v in naive.values()]
print(f"    Zeitspreizung naiv: {min(ages)}–{max(ages)} min")
check("Mock reproduziert IT-Lastfehler", naive["IT"][1] < 20,
      f"{naive['IT'][1]:.1f} GW")
check("Mock reproduziert BE/CH-Untererfassung",
      naive["BE"][0] < 5 and naive["CH"][0] < 5,
      f"BE {naive['BE'][0]:.1f}G, CH {naive['CH'][0]:.1f}G")

# ── Nachher: mit globaler Ausrichtung ──────────────────────────────────────
print("\n  NACH KORREKTUR (gemeinsamer Zeitpunkt + begrenzter Vortrag):")
meta = L.STATE.apply_to_model()
tgt = L.STATE.target_ts
print(f"    Zielzeitpunkt: {tgt:%H:%M} UTC "
      f"({int((NOW-tgt).total_seconds()/60)} min vor Rasterende)\n")
print(f"    {'Z':<4}{'HERKUNFT':<9}{'VORTRAG':>8}{'GEN':>8}{'LAST':>8}{'SALDO':>8}")
for c, z in ZONES.items():
    m = meta[c]
    fill = f"{m['fill_min']}m" if m.get("fill_min") is not None else "—"
    print(f"    {c:<4}{m['source']:<9}{fill:>8}{z.base_gen/1000:7.1f}G"
          f"{z.base_load/1000:7.1f}G{(z.base_gen-z.base_load)/1000:+7.1f}G")

print()
ts_used = {m["ts"] for m in meta.values() if m.get("ts")}
check("ALLE Live-Zonen auf demselben Zeitpunkt", len(ts_used) == 1,
      f"{len(ts_used)} verschiedene Zeitpunkte")
check("IT Last korrigiert (~48 GW statt 15)", ZONES["IT"].base_load > 44000,
      f"{ZONES['IT'].base_load/1000:.1f} GW")
check("IT Teilzonen vollständig erkannt",
      meta["IT"]["load_subzones"] == IT_SUBZONES,
      f"{meta['IT']['load_subzones']} Teilzonen")
check("BE Erzeugung vollständig (~8.4 GW statt 3.6)",
      ZONES["BE"].base_gen > 7000, f"{ZONES['BE'].base_gen/1000:.1f} GW")
check("CH Erzeugung vollständig (~7.8 GW statt 4.1)",
      ZONES["CH"].base_gen > 6500, f"{ZONES['CH'].base_gen/1000:.1f} GW")
check("NL Erzeugung vollständig (~11.4 GW statt 6.2)",
      ZONES["NL"].base_gen > 9500, f"{ZONES['NL'].base_gen/1000:.1f} GW")
check("SI ohne Last fällt auf default", meta["SI"]["source"] == "default",
      meta["SI"]["source"])
check("AT Pumplast in Last enthalten", ZONES["AT"].base_load > 8000,
      f"{ZONES['AT'].base_load/1000:.1f} GW")
fills = [m["fill_min"] for m in meta.values() if m.get("fill_min") is not None]
check("Vortrag bleibt in der Schranke", max(fills) <= L.MAX_FILL_MIN,
      f"max {max(fills)} min (Limit {L.MAX_FILL_MIN})")

res = L.solve_live()
lv = res["live"]
print(f"\n    Bilanz: {lv['external_balance_mw']/1000:+.1f} GW | "
      f"Qualität: {lv['quality']} | max. Vortrag: {lv['max_fill_min']} min")
check("Lastfluss rechnet auf ausgerichteten Daten (83 Kanten)",
      res["max_util"] > 0 and "flows" in res,
      f"max {res['max_util']*100:.0f}% NTC, {res['n_overloads']} Overloads")
check("Zielzeitpunkt in der API ausgewiesen", lv["target_ts"] is not None)



# ══════════════════════════════════════════════════════════════════════════
#  LAUF 2 (23.07., 11:26): Träge Kleinkategorien warfen den Netzschnitt
#  um 8 Stunden zurück (ES: 21 Typen, Waste/Geothermal/Marine mit Stunden-
#  Verzug → 90%-Typenregel fand nur am Fensteranfang "Vollständigkeit").
#  Folge: DK1/DK2 trotz frischer Daten auf default, A11 0/77 Grenzen.
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 74)
print("  REGRESSION LAUF 2: träge Kleinkategorien dürfen die Uhr nicht anhalten")
print("=" * 74)

def mkseries2(gen_map, load, newest_ago, laggy=None, laggy_ago=480, n=32):
    """laggy = Typen, deren Meldung laggy_ago Minuten zurück endet."""
    laggy = laggy or []
    now = NOW
    gs = {}
    for typ, mw in gen_map.items():
        stop = laggy_ago if typ in laggy else newest_ago
        tl = [now - timedelta(minutes=stop + 15 * i) for i in range(n)]
        gs[typ] = {ts: mw for ts in tl}
    tl = [now - timedelta(minutes=newest_ago + 15 * i) for i in range(n)]
    return {"ok": True, "zone": "?", "gen_series": gs, "pump_series": {},
            "load_series": {ts: (load, 1) for ts in tl},
            "n_types": len(gen_map), "load_subzones": 1}

L.STATE._zones.clear()
L.STATE.update({
    # ES wie im Realdaten-Lauf: 21 Typen, 4 winzige melden 8h verzögert
    "ES": mkseries2({
        "Nuclear": 5800, "Fossil Gas": 5500, "Solar": 6500, "Wind Onshore": 8200,
        "Hydro Water Reservoir": 2200, "Hydro Run-of-river and poundage": 2000,
        "Biomass": 400, "Fossil Hard Coal": 300, "Other": 250, "Wind Offshore": 5,
        "Hydro Pumped Storage": 150, "Fossil Oil": 120, "Solar Thermal": 90,
        "Other renewable": 60, "Waste": 45, "Geothermal": 3, "Marine": 1,
        "Fossil Peat": 1, "Fossil Oil shale": 1, "Fossil Coal-derived Gas": 2,
        "Fossil Brown Coal/Lignite": 5},
        29500, newest_ago=30,
        laggy=["Waste", "Geothermal", "Marine", "Other renewable",
               "Solar Thermal", "Fossil Peat"]),
    # DE: ein GROSSER Typ fehlt in jungen Scheiben → muss weiter blocken!
    "DE": mkseries2({"Nuclear": 0.0 or 1, "Fossil Gas": 7500, "Wind Onshore": 26000,
                     "Solar": 6200, "Fossil Hard Coal": 4500,
                     "Fossil Brown Coal/Lignite": 9000, "Biomass": 4800},
                    60300, newest_ago=30,
                    laggy=["Fossil Brown Coal/Lignite"], laggy_ago=180),
    # DK1: frisch und vollständig – darf nie wieder wegen anderer auf default
    "DK1": mkseries2({"Wind Onshore": 2600, "Fossil Gas": 300, "Biomass": 500},
                     2400, newest_ago=45),
    "FR": mkseries2({"Nuclear": 44000, "Fossil Gas": 2800, "Wind Onshore": 4200,
                     "Solar": 900, "Hydro Water Reservoir": 3500,
                     "Hydro Run-of-river and poundage": 5000},
                    47000, newest_ago=30),
})
meta2 = L.STATE.apply_to_model()
tgt2 = L.STATE.target_ts
age2 = int((NOW - tgt2).total_seconds() / 60) if tgt2 else 9999

check("Netzschnitt bleibt frisch trotz träger Kleinkategorien", age2 <= 90,
      f"{age2} min alt (Realdaten-Lauf: 491 min)")
check("ES live trotz 6 träger Winzlinge", meta2["ES"]["source"] == "live",
      meta2["ES"]["source"])
es_gen = ZONES["ES"].base_gen
check("ES-Erzeugung im Wesentlichen vollständig (>30 GW)", es_gen > 30000,
      f"{es_gen/1000:.1f} GW")
check("DK1 mit frischen Daten live (nicht mehr Opfer des alten Ziels)",
      meta2["DK1"]["source"] == "live", meta2["DK1"]["source"])
# DE: Braunkohle (9 GW = GROSS) fehlt in jungen Scheiben → Schnitt muss auf
# den letzten Zeitpunkt MIT Braunkohle zurück (Anti-Phantom bleibt!)
de_fill = meta2["DE"].get("fill_min")
de_gen = ZONES["DE"].base_gen
check("DE: fehlender GROSSER Typ erzwingt älteren Schnitt (Anti-Phantom)",
      de_gen > 55000, f"{de_gen/1000:.1f} GW, Vortrag {de_fill} min")


print("\n" + "=" * 74)
if FAILS:
    print(f"  {len(FAILS)} FEHLGESCHLAGEN: {', '.join(FAILS)}"); sys.exit(1)
print(f"  BESTANDEN – alle beobachteten Pathologien behoben ({http.calls} Requests)")
print("=" * 74)
#!/usr/bin/env python3
"""Tests für die ENTSO-E-Live-Kopplung. Ohne Netzzugriff, mit XML-Fixtures."""
import sys
from datetime import datetime, timezone, timedelta
import meridian_grid_live as L
from meridian_grid import ZONES

NSU = 'urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0'
FAILS = []

def check(name, cond, detail=""):
    print(f"  {'✓' if cond else '✗'} {name}{'  → ' + detail if detail else ''}")
    if not cond:
        FAILS.append(name)

def ts_series(psr, start, res, values, consumption=False):
    pts = "".join(f"<Point><position>{i+1}</position>"
                  f"<quantity>{v}</quantity></Point>"
                  for i, v in enumerate(values))
    dom = (f'<outBiddingZone_Domain.mRID codingScheme="A01">10YAT-APG------L'
           f'</outBiddingZone_Domain.mRID>' if consumption else
           f'<inBiddingZone_Domain.mRID codingScheme="A01">10YAT-APG------L'
           f'</inBiddingZone_Domain.mRID>')
    return (f"<TimeSeries>{dom}"
            f"<MktPSRType><psrType>{psr}</psrType></MktPSRType>"
            f"<Period><timeInterval><start>{start}</start>"
            f"<end>{start}</end></timeInterval>"
            f"<resolution>{res}</resolution>{pts}</Period></TimeSeries>")

def doc(body):
    return f'<?xml version="1.0"?><GL_MarketDocument xmlns="{NSU}">{body}</GL_MarketDocument>'.encode()


print("=" * 66)
print("  TEST 1  –  A75 Parsing: Erzeugung, Pumpspeicher, Zeitscheibe")
print("=" * 66)

# AT-typisch: Wasser + Gas + Wind, dazu Pumpbetrieb als Verbrauchs-TimeSeries
xml = doc(
    ts_series("B11", "2026-07-23T10:00Z", "PT15M", [3000, 3100, 3200, 3300]) +   # Laufwasser
    ts_series("B12", "2026-07-23T10:00Z", "PT15M", [2000, 2050, 2100, 2150]) +   # Speicher
    ts_series("B04", "2026-07-23T10:00Z", "PT15M", [800, 810, 820, 830]) +       # Gas
    ts_series("B19", "2026-07-23T10:00Z", "PT15M", [900, 950, 1000, 1050]) +     # Wind
    ts_series("B10", "2026-07-23T10:00Z", "PT15M", [1500, 1500, 1500, 1500],
              consumption=True)                                                   # PUMPEN
)
gen, pump, ts = L.parse_generation(xml)

check("Zeitstempel = jüngste Viertelstunde",
      ts == datetime(2026, 7, 23, 10, 45, tzinfo=timezone.utc), str(ts))
check("Laufwasser letzter Wert", gen.get("Hydro Run-of-river and poundage") == 3300,
      str(gen.get("Hydro Run-of-river and poundage")))
check("Wind letzter Wert", gen.get("Wind Onshore") == 1050)
check("Pumpbetrieb separat erfasst (nicht in Erzeugung)", pump == 1500, f"pump={pump}")
check("Pumpen NICHT als Erzeugung addiert",
      "Hydro Pumped Storage" not in gen, str(list(gen.keys())))

total_gen = sum(gen.values())
check("Erzeugungssumme = 3300+2150+830+1050", total_gen == 7330, str(total_gen))

# Vergleich: so hätte der Bestandscode gerechnet (Pumpen addiert)
old_style = total_gen + pump
print(f"\n    Bestandscode-Logik hätte {old_style:.0f} MW gemeldet,")
print(f"    physikalisch korrekt sind {total_gen:.0f} MW Erzeugung")
print(f"    bei gleichzeitig {pump:.0f} MW Pumplast.")
print(f"    → Differenz im Lastfluss: {2*pump:.0f} MW ({2*pump/1000:.1f} GW)")


print("\n" + "=" * 66)
print("  TEST 2  –  Zeitscheiben-Konsistenz bei ungleich langen Reihen")
print("=" * 66)

# Wind meldet 4 Werte, Kernkraft nur 2 (typische Meldelücke)
xml2 = doc(
    ts_series("B19", "2026-07-23T10:00Z", "PT15M", [900, 950, 1000, 1050]) +
    ts_series("B14", "2026-07-23T10:00Z", "PT15M", [5000, 5000]) +
    ts_series("B04", "2026-07-23T10:00Z", "PT15M", [800, 810, 820, 830])
)
gen2, _, ts2 = L.parse_generation(xml2)
# 10:45 hat 2 von 3 Typen = 67% ≥ 60% → wird gewählt; Kernkraft fehlt dort
check("Konsistente Scheibe gewählt (10:45)",
      ts2 == datetime(2026, 7, 23, 10, 45, tzinfo=timezone.utc), str(ts2))
check("Nur Typen mit Wert in dieser Scheibe",
      "Nuclear" not in gen2 and gen2.get("Wind Onshore") == 1050,
      str(sorted(gen2)))
print("    Hinweis: fehlende Typen werden weggelassen statt mit einer")
print("    älteren Viertelstunde vermischt – keine Zeit-Chimäre.")


print("\n" + "=" * 66)
print("  TEST 3  –  A65 Load-Parsing (Stundenauflösung)")
print("=" * 66)
xml3 = doc(ts_series("", "2026-07-23T09:00Z", "PT60M", [7800, 7900, 8100]))
load, lts = L.parse_load(xml3)
check("Load = jüngster Punkt", load == 8100, str(load))
check("Load-Zeitstempel 11:00",
      lts == datetime(2026, 7, 23, 11, 0, tzinfo=timezone.utc), str(lts))


print("\n" + "=" * 66)
print("  TEST 4  –  Fehlerrobustheit")
print("=" * 66)
g, p, t = L.parse_generation(doc(""))
check("Leeres Dokument → leeres Ergebnis, kein Crash", g == {} and t is None)
try:
    L.parse_generation(b"<nonsense")
    check("Kaputtes XML wirft ParseError", False)
except Exception as e:
    check("Kaputtes XML wirft sauberen ParseError", "ParseError" in type(e).__name__)


print("\n" + "=" * 66)
print("  TEST 5  –  Live-State: gemeinsamer Zeitpunkt, live / default")
print("=" * 66)
print("  Hinweis: seit der Zeit-Ausrichtung liegen ALLE Zonen auf demselben")
print("  Zeitpunkt. 'stale' gilt daher global (Zielzeitpunkt zu alt), nicht")
print("  mehr je Zone. Zonen ohne brauchbaren Schnitt werden 'default'.")
print()

now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
now = now.replace(minute=(now.minute // 15) * 15)

def mkseries(gen_map, load, pump, newest_ago, n=8):
    """Zeitreihen im 15-Min-Raster, jüngster Punkt newest_ago Minuten zurück."""
    tl = [now - timedelta(minutes=newest_ago + 15 * i) for i in range(n)]
    return {
        "ok": True, "zone": "?",
        "gen_series": {t: {ts: mw for ts in tl} for t, mw in gen_map.items()},
        "pump_series": {ts: pump for ts in tl} if pump else {},
        "load_series": {ts: (load, 1) for ts in tl},
        "n_types": len(gen_map), "load_subzones": 1,
    }

L.STATE.update({
    "AT": mkseries({"Hydro Water Reservoir": 5000, "Wind Onshore": 1200,
                    "Fossil Gas": 800}, 7000, 1500, newest_ago=0),
    "DE": mkseries({"Wind Onshore": 25000, "Solar": 8000,
                    "Fossil Hard Coal": 10000}, 60000, 0, newest_ago=15),
    # BE liefert nur uralte Daten – weit jenseits der Vortragsschranke
    "BE": mkseries({"Nuclear": 4000, "Fossil Gas": 2500}, 9500, 0,
                   newest_ago=400),
})
meta = L.STATE.apply_to_model()
tgt = L.STATE.target_ts

check("Gemeinsamer Zielzeitpunkt bestimmt", tgt is not None, str(tgt))
check("AT als live erkannt", meta["AT"]["source"] == "live", meta["AT"]["source"])
check("DE als live erkannt (15 min Vortrag)", meta["DE"]["source"] == "live",
      f"{meta['DE']['source']}, fill={meta['DE']['fill_min']}m")
check("BE zu alt → default statt verfälschtem Wert",
      meta["BE"]["source"] == "default", meta["BE"]["source"])
check("FR ohne Daten → default", meta["FR"]["source"] == "default")
check("AT Pumplast auf Last addiert (7000+1500)",
      ZONES["AT"].base_load == 8500, str(ZONES["AT"].base_load))
check("AT Erzeugung gemappt (hydro 5000, wind 1200, gas 800)",
      ZONES["AT"].gen["hydro"] == 5000 and ZONES["AT"].gen["wind"] == 1200
      and ZONES["AT"].gen["gas"] == 800, str(ZONES["AT"].gen))
ts_set = {m["ts"] for m in meta.values() if m.get("ts")}
check("Alle Live-Zonen auf demselben Zeitpunkt", len(ts_set) == 1,
      f"{len(ts_set)} Zeitpunkt(e)")

print("\n" + "=" * 66)
print("  TEST 6  –  solve_live() inkl. Schocks über die API")
print("=" * 66)
res = L.solve_live()
check("Lastfluss gerechnet", "flows" in res and len(res["flows"]) == 83,
      f"{len(res['flows'])} Kanten")
check("Datenqualität ausgewiesen", res["live"]["quality"] in ("ok", "partial", "poor"),
      res["live"]["quality"])
check("Externe Bilanz beziffert", isinstance(res["live"]["external_balance_mw"], int),
      f"{res['live']['external_balance_mw']} MW")
check("Live-Zonen gezählt (AT+DE, BE fiel raus)",
      res["live"]["zones_live"] == 2, f"live={res['live']['zones_live']}")
check("Zielzeitpunkt in der API", res["live"]["target_ts"] is not None)

base_util = res["max_util"]
res2 = L.solve_live({"gen": {"FR": {"nuclear": -20000}}})
check("Schock über API verändert Lastfluss", res2["max_util"] != base_util,
      f"{base_util:.2f} → {res2['max_util']:.2f}")
res3 = L.solve_live({"line": {"DE-AT": 0.0}})
at_de = [f for f in res3["flows"] if {f["a"], f["b"]} == {"DE", "AT"}][0]
check("Leitung kappen → Fluss = 0", abs(at_de["flow_mw"]) < 1, f"{at_de['flow_mw']} MW")


print("\n" + "=" * 66)
print("  TEST 7  –  Flask-Endpoints")
print("=" * 66)
from flask import Flask
app = Flask(__name__)
L.register(app, entsoe_key="dummy")
c = app.test_client()

r = c.get("/api/grid")
check("/api/grid → 200", r.status_code == 200, str(r.status_code))
j = r.get_json()
check("JSON enthält flows/zones/live", all(k in j for k in ("flows", "zones", "live")))

r2 = c.get('/api/grid?shocks={"gen":{"DE":{"wind":30000}}}')
check("/api/grid mit Schock-Parameter → 200", r2.status_code == 200)
check("Schock wirkt über Query-Parameter",
      r2.get_json()["max_util"] != j["max_util"],
      f"{j['max_util']:.2f} → {r2.get_json()['max_util']:.2f}")

r3 = c.get("/api/grid?shocks=NICHT-JSON")
check("Ungültiges Schock-JSON → 400 statt Crash", r3.status_code == 400,
      str(r3.status_code))

r4 = c.get("/grid")
check("/grid liefert HTML mit injiziertem Live-Endpoint",
      r4.status_code == 200 and b"GRID_LIVE_ENDPOINT" in r4.data, str(r4.status_code))

# Auth-Guard
app2 = Flask(__name__)
L.register(app2, entsoe_key="dummy", auth=lambda: False)
r5 = app2.test_client().get("/api/grid")
check("auth-Guard blockt (401)", r5.status_code == 401, str(r5.status_code))




print("\n" + "=" * 66)
print("  TEST 8  –  Leitungsschocks: Drosselung erhöht die Auslastung")
print("=" * 66)
from meridian_grid import GridModel
gm = GridModel()
b8 = gm.solve()
f0 = [f for f in b8["flows"] if {f["a"], f["b"]} == {"DE", "AT"}][0]
gm.shock_line("DE", "AT", 0.5)
r8 = gm.solve()
f1 = [f for f in r8["flows"] if {f["a"], f["b"]} == {"DE", "AT"}][0]
check("Drosselung -50% HALBIERT die NTC", f1["ntc"] == f0["ntc"] / 2,
      f"{f0['ntc']:.0f} → {f1['ntc']:.0f}")
check("Auslastung STEIGT durch Drosselung (alter Bug: sie sank)",
      f1["util"] > f0["util"], f"{f0['util']*100:.0f}% → {f1['util']*100:.0f}%")
check("Fluss sinkt (weniger Suszeptanz zieht weniger an)",
      abs(f1["flow_mw"]) < abs(f0["flow_mw"]),
      f"{f0['flow_mw']:+} → {f1['flow_mw']:+} MW")
# Kappen: Umverteilung auf Nachbarn
gm.reset_shocks(); gm.shock_line("DE", "AT", 0.0)
r8b = gm.solve()
bmap = {(f["a"], f["b"], f["label"]): f["flow_mw"] for f in b8["flows"]}
shift = sum(abs(f["flow_mw"] - bmap[(f["a"], f["b"], f["label"])])
            for f in r8b["flows"])
check("Kappung verschiebt Flüsse spürbar (Σ|Δ| > 1.5 GW)", shift > 1500,
      f"Σ|Δ| = {shift} MW über alle Kanten")
# DC-Kabel drosseln: Limit UND Sollwert skalieren
gm.reset_shocks(); gm.shock_line("DE", "NO2", 0.5)
r8c = gm.solve()
nl = [f for f in r8c["flows"] if f["label"] == "NordLink"][0]
nl0 = [f for f in b8["flows"] if f["label"] == "NordLink"][0]
check("DC-Drosselung halbiert Sollwert und Limit",
      abs(nl["flow_mw"]) == abs(nl0["flow_mw"]) // 2 and nl["ntc"] == nl0["ntc"] / 2,
      f"{nl0['flow_mw']:+}@{nl0['ntc']:.0f} → {nl['flow_mw']:+}@{nl['ntc']:.0f}")
# Kalibrierfaktor darf NTC NICHT anfassen
gm2 = GridModel(); gm2.suscept_scale[("DE", "AT")] = 0.5
f2 = [f for f in gm2.solve()["flows"] if {f["a"], f["b"]} == {"DE", "AT"}][0]
check("Kalibrierfaktor lässt NTC unangetastet", f2["ntc"] == f0["ntc"],
      f"NTC bleibt {f2['ntc']:.0f}")




print("\n" + "=" * 66)
print("  TEST 9  –  Netzfrequenz: Auslegungsfall, Vorzeichen, Inseln")
print("=" * 66)
from meridian_grid import GridModel as _GM, INTERCONNECTORS as _IC
_g0 = _GM(); _ref = {c: float(z["reg_mw"]) for c, z in _g0.solve()["zones"].items()}
def _gref():
    m = _GM(); m.freq_reference = dict(_ref); return m

_r = _gref().solve()
check("Ungestört: alle Zonen exakt 50.000 Hz",
      all(f["hz"] == 50.0 for f in _r["frequencies"]))
_m = _gref(); _m.shock_generation("FR", "nuclear", -3000)
_ce = [f for f in _m.solve()["frequencies"] if f["sync"] == "CE"][0]
check("CE-Auslegungsfall: −3 GW → −200 mHz (λ = 15 GW/Hz)",
      abs(_ce["dev_mhz"] + 200) <= 5, f"{_ce['dev_mhz']:+} mHz")
_m = _gref(); _m.shock_generation("DE", "wind", +30000)
_ce = [f for f in _m.solve()["frequencies"] if f["sync"] == "CE"][0]
check("+30 GW ungeregelt → Überfrequenz-Kollaps",
      _ce["status"] == "collapse", _ce["status_label"])
_m = _gref(); _m.shock_line("DE", "NO2", 0.0)
_fr = {f["sync"]: f for f in _m.solve()["frequencies"]}
check("NordLink-Ausfall: CE sinkt, Nordic steigt (DC koppelt Frequenz NICHT)",
      _fr["CE"]["dev_mhz"] < 0 < _fr["N"]["dev_mhz"],
      f"CE {_fr['CE']['dev_mhz']:+} / N {_fr['N']['dev_mhz']:+} mHz")
_m = _gref(); _m.shock_line("PL", "LT", 0.0)
for _ln in _IC:
    if _ln.label in ("NordBalt", "EstLink 1+2"):
        _m.set_dc(_ln.a, _ln.b, 0.0, _ln.label)
_res = _m.solve()
_b = [f for f in _res["frequencies"] if set(f["members"]) == {"EE", "LV", "LT"}][0]
_c = [f for f in _res["frequencies"] if f["sync"] == "CE" and not f["is_island"]][0]
check("Baltikum-Insel: eigene Frequenz, dramatischer als Rest-CE",
      _b["is_island"] and abs(_b["dev_mhz"]) > abs(_c["dev_mhz"]) * 3,
      f"Insel {_b['hz']} Hz (λ {_b['lambda_mw_hz']} MW/Hz) vs. CE {_c['hz']} Hz")


print("\n" + "=" * 66)
if FAILS:
    print(f"  {len(FAILS)} TEST(S) FEHLGESCHLAGEN: {', '.join(FAILS)}")
    sys.exit(1)
print("  ALLE TESTS BESTANDEN")
print("=" * 66)

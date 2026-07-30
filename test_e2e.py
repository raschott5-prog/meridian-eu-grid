#!/usr/bin/env python3
"""End-to-End: gemockter ENTSO-E-Endpoint → Fetcher → Modell → /api/grid → HTML."""
import re, sys, json
from datetime import datetime, timezone, timedelta
import meridian_grid_live as L
from meridian_grid import ZONES

NSU = 'urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0'
FAILS = []
def check(n, c, d=""):
    print(f"  {'✓' if c else '✗'} {n}{'  → '+d if d else ''}")
    if not c: FAILS.append(n)

# ── realistische Fixtures je Zone ────────────────────────────────────────────
NOW = datetime.now(timezone.utc).replace(second=0, microsecond=0)
NOW = NOW.replace(minute=(NOW.minute // 15) * 15) - timedelta(minutes=45)
T0 = NOW.strftime("%Y-%m-%dT%H:%MZ")

PROFILE = {  # zone: (gen nach PSR, load, pump)
    "FR": ({"B14": 44000, "B04": 2800, "B11": 5000, "B12": 3500, "B19": 4200, "B16": 900}, 57000, 800),
    "DE": ({"B02": 9000, "B05": 4500, "B04": 7500, "B19": 26000, "B18": 5500, "B16": 6200, "B01": 4800}, 61000, 1200),
    "ES": ({"B14": 5800, "B04": 5500, "B11": 2000, "B12": 2200, "B19": 8200, "B16": 6500}, 29500, 600),
    "PT": ({"B04": 1400, "B11": 900, "B12": 700, "B19": 2100, "B16": 1000}, 6100, 200),
    "IT": ({"B04": 13500, "B11": 3500, "B12": 2600, "B16": 4100, "B19": 1900}, 31500, 900),
    "AT": ({"B11": 3400, "B12": 2400, "B04": 850, "B19": 1050, "B16": 250}, 7100, 1500),
    "CH": ({"B14": 2100, "B11": 2600, "B12": 2900, "B16": 200}, 6900, 1100),
    "NL": ({"B04": 5800, "B05": 1400, "B19": 3300, "B16": 950}, 12800, 0),
    "BE": ({"B14": 3900, "B04": 2400, "B19": 1450, "B16": 680}, 9300, 0),
    "CZ": ({"B14": 3700, "B02": 2900, "B04": 480, "B16": 760}, 7800, 300),
    "SK": ({"B14": 1850, "B11": 680, "B04": 480}, 3100, 200),
    "HU": ({"B14": 1850, "B04": 1150, "B16": 780}, 5400, 0),
    "PL": ({"B02": 8000, "B05": 7800, "B04": 1900, "B19": 2900, "B16": 950}, 21500, 400),
    "HR": ({"B11": 700, "B12": 500, "B04": 380, "B19": 290}, 2150, 0),
    "SI": ({"B14": 680, "B11": 300, "B12": 280, "B02": 290}, 1580, 150),
}

def _ts(psr, vals, cons=False):
    pts = "".join(f"<Point><position>{i+1}</position><quantity>{v}</quantity></Point>"
                  for i, v in enumerate(vals))
    dom = ("outBiddingZone_Domain.mRID" if cons else "inBiddingZone_Domain.mRID")
    return (f'<TimeSeries><{dom} codingScheme="A01">X</{dom}>'
            f"<MktPSRType><psrType>{psr}</psrType></MktPSRType>"
            f"<Period><timeInterval><start>{T0}</start><end>{T0}</end></timeInterval>"
            f"<resolution>PT15M</resolution>{pts}</Period></TimeSeries>")

def _doc(body):
    return f'<?xml version="1.0"?><GL_MarketDocument xmlns="{NSU}">{body}</GL_MarketDocument>'.encode()

DOMAIN_TO_ZONE = {v: k for k, v in L.ZONE_DOMAINS.items()}

class MockResp:
    def __init__(self, content, status=200):
        self.content = content; self.status_code = status; self.text = content.decode(errors="ignore")

class MockHTTP:
    """Simuliert ENTSO-E. PL liefert absichtlich nichts (Ausfall-Test)."""
    def __init__(self): self.calls = 0
    def get(self, url, params=None, timeout=None):
        self.calls += 1
        p = params or {}
        dom = p.get("in_Domain") or p.get("outBiddingZone_Domain")
        z = DOMAIN_TO_ZONE.get(dom)
        if z is None or z == "PL":           # PL simuliert Datenausfall
            return MockResp(b"<Acknowledgement_MarketDocument/>", 200)
        gen, load, pump = PROFILE[z]
        if p.get("documentType") == "A75":
            body = "".join(_ts(k, [v * 0.98, v * 0.99, v, v]) for k, v in gen.items())
            if pump:
                body += _ts("B10", [pump] * 4, cons=True)
            return MockResp(_doc(body))
        else:
            return MockResp(_doc(_ts("", [load * 0.99, load * 0.995, load, load])))


print("=" * 66)
print("  E2E  –  Mock-ENTSO-E → Fetcher → Modell → API → HTML")
print("=" * 66)

http = MockHTTP()
fetcher = L.EntsoeGridFetcher("dummy-key", http=http)
fetched = fetcher.fetch_all(pause=0)
L.STATE.update(fetched)

ok = [c for c, d in fetched.items() if d["ok"]]
check("14 Zonen geliefert (PL simuliert Ausfall, 25 ohne Mock)",
      len(ok) == 14, f"{len(ok)} live")
check("PL korrekt als fehlgeschlagen markiert", not fetched["PL"]["ok"])
check("PT-Daten angekommen", fetched["PT"]["ok"],
      f"{len(fetched['PT']['load_series'])} Last-Punkte")
check("AT Pumpbetrieb erkannt", bool(fetched["AT"]["pump_series"]),
      f"{len(fetched['AT']['pump_series'])} Punkte, "
      f"{max(fetched['AT']['pump_series'].values()):.0f} MW")

res = L.solve_live()
lv = res["live"]
check("PL fällt sichtbar auf default zurück",
      lv["zones_meta"]["PL"]["source"] == "default", lv["zones_meta"]["PL"]["source"])
check("14 Zonen als live gezählt", lv["zones_live"] == 14, str(lv["zones_live"]))
check("40 Zonen insgesamt im Modell", lv["zones_total"] == 40, str(lv["zones_total"]))
check("AT Last = Verbrauch + Pumpen", ZONES["AT"].base_load == 7100 + 1500,
      str(ZONES["AT"].base_load))
check("Datenqualität bewertet", lv["quality"] in ("ok", "partial", "poor"), lv["quality"])
check("offene Bilanz ausgewiesen", "external_balance_mw" in lv,
      f"{lv['external_balance_mw']/1000:.1f} GW")
check("Ergebnis ist JSON-serialisierbar", bool(json.dumps(res)))

print(f"\n    Lastfluss auf Live-Daten: max {res['max_util']*100:.0f}% NTC, "
      f"{res['n_overloads']} Overloads")
print("    Höchstbelastete Grenzen:")
for f in sorted(res["flows"], key=lambda x: -x["util"])[:5]:
    print(f"      {f['from']}→{f['to']:<3} {f['abs_mw']:>6} MW  {f['util']*100:5.1f}% NTC")

# ── Iberien-Fokus (Interesse des Nutzers) ───────────────────────────────────
print("\n" + "─" * 66)
print("  IBERIEN-KOPPLUNG: Wie erreicht ein FR-Schock Portugal?")
print("─" * 66)
base = L.solve_live()
shocked = L.solve_live({"gen": {"FR": {"nuclear": -20000}}})

def flow(res_, a, b):
    f = [x for x in res_["flows"] if {x["a"], x["b"]} == {a, b}][0]
    return f["flow_mw"] if f["a"] == a else -f["flow_mw"]

for a, b in [("FR", "ES"), ("ES", "PT")]:
    b0, b1 = flow(base, a, b), flow(shocked, a, b)
    print(f"    {a}→{b}:  {b0:>+7} → {b1:>+7} MW   (Δ {b1-b0:>+7} MW)")
pt0, pt1 = base["zones"]["PT"]["net_phys_mw"], shocked["zones"]["PT"]["net_phys_mw"]
print(f"    PT Nettoposition: {pt0:+} → {pt1:+} MW  (Δ {pt1-pt0:+} MW)")
check("PT reagiert messbar auf den FR-Schock", abs(pt1 - pt0) > 50,
      f"Δ {pt1-pt0} MW")

# ── API + HTML ──────────────────────────────────────────────────────────────
from flask import Flask
app = Flask(__name__)
L.register(app, entsoe_key="dummy", http=http)
c = app.test_client()
j = c.get("/api/grid").get_json()
check("/api/grid liefert zone_input für Frontend (40 Zonen)",
      "zone_input" in j and len(j["zone_input"]) == 40, str(len(j.get("zone_input", []))))
html = c.get("/grid").data.decode()
check("HTML enthält Live-Endpoint-Injektion", 'GRID_LIVE_ENDPOINT="/api/grid"' in html)
check("HTML enthält Live-Loader", "async function loadLive" in html)
check("HTML enthält Datenqualitäts-Anzeige", 'id="dqrow"' in html)

r = c.get("/api/grid/refresh")
check("/api/grid/refresh funktioniert", r.status_code == 200,
      f"{len(r.get_json()['refreshed'])} Zonen aktualisiert")

print("\n" + "=" * 66)
if FAILS:
    print(f"  {len(FAILS)} FEHLGESCHLAGEN: {', '.join(FAILS)}"); sys.exit(1)
print(f"  E2E BESTANDEN  ({http.calls} simulierte ENTSO-E-Requests)")
print("=" * 66)

#!/usr/bin/env python3
"""
Kalibrierungs-Test: Ein "wahres" Netz mit ABSICHTLICH anderen Eigenschaften
als die Modellannahmen erzeugt A11-Messwerte. Die Kalibrierung muss daraus
die Wahrheit rekonstruieren:
  - DC-Sollwerte exakt übernehmen
  - AC-Verteilung durch Suszeptanz-Fit annähern
  - versteckte Kleinerzeugung (unter A75-Meldeschwelle) als Zonen-Anpassung
"""
import sys
from datetime import datetime, timezone, timedelta
import numpy as np
import meridian_grid_live as L
from meridian_grid import GridModel, ZONES, INTERCONNECTORS, SYNC_ZONES

FAILS = []
def check(n, c, d=""):
    print(f"  {'✓' if c else '✗'} {n}{'  → '+d if d else ''}")
    if not c: FAILS.append(n)

TGT = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

print("=" * 74)
print("  KALIBRIERUNGS-TEST: Wahrheit ≠ Modellannahme, A11 muss es richten")
print("=" * 74)

# ── 1) "Wahres" Netz konstruieren ────────────────────────────────────────────
truth = GridModel()

# Wahrheit 1: einige Suszeptanzen weichen deutlich ab (Modell rät falsch)
TRUE_SCALE = {("FR", "DE"): 1.8, ("DE", "AT"): 0.6, ("FR", "ES"): 1.5,
              ("SE2", "SE3"): 0.7, ("CH", "IT"): 1.4}
truth.suscept_scale = dict(TRUE_SCALE)

# Wahrheit 2: versteckte Kleinerzeugung (PV unter Meldeschwelle) – das Phantom
HIDDEN = {"DE": 9000, "IT": 6000, "ES": 4000, "NL": 2500, "BE": 1500, "FR": 3000}
for z, mw in HIDDEN.items():
    truth.zone_injection[z] = mw

# Wahrheit 3: DC-Fahrpläne weichen von den Defaults ab
TRUE_DC = {("DE", "NO2", "NordLink"): -1300, ("FR", "GB", "IFA/IFA2/ElecLink"): +2400,
           ("NL", "NO2", "NorNed"): -650, ("DK1", "GB", "Viking Link"): +900,
           ("GB", "IE", "EWIC/Greenlink"): +550}
for (a, b, lab), mw in TRUE_DC.items():
    truth.set_dc(a, b, mw, lab)

truth_res = truth.solve()
measured = {(f["a"], f["b"], f["label"]): float(f["flow_mw"])
            for f in truth_res["flows"]}
print(f"\n  'Wahres' Netz gelöst: {len(measured)} Grenzflüsse als Messung")

# ── 2) Unkalibriertes Modell: wie falsch liegt es? ──────────────────────────
plain = GridModel()
for z, mw in HIDDEN.items():          # Phantom wirkt real, Modell kennt es nicht
    pass
plain_res = plain.solve()
pf = {(f["a"], f["b"]): f["flow_mw"] for f in plain_res["flows"]}
errs0 = [abs(pf[(a, b)] - m) for (a, b, lab), m in measured.items()
         if lab is None]
print(f"  Unkalibriert: Ø AC-Fehler {int(np.mean(errs0))} MW, "
      f"max {int(max(errs0))} MW")

# ── 3) Kalibrieren ───────────────────────────────────────────────────────────
summary = L.CALIBRATION.calibrate(measured, TGT)
print(f"  Kalibriert:  {summary['n_dc_set']} DC gesetzt, "
      f"{summary['n_scaled']} AC skaliert, {summary['n_zone_adjust']} Zonen "
      f"korrigiert, Ø-Fehler {summary['mean_abs_error_mw']} MW")

check("Alle Grenzen als Messung übergeben", summary["n_measured"] == len(measured))
check("Alle DC-Kabel aus Messung gesetzt",
      summary["n_dc_set"] == sum(1 for l in INTERCONNECTORS if l.kind == "DC"),
      str(summary["n_dc_set"]))
check("Kalibrierung reduziert Fehler drastisch",
      summary["mean_abs_error_mw"] < np.mean(errs0) * 0.2,
      f"{int(np.mean(errs0))} → {summary['mean_abs_error_mw']} MW")
check("Restfehler klein (Ø < 60 MW)", summary["mean_abs_error_mw"] < 60,
      f"{summary['mean_abs_error_mw']} MW")

# ── 4) Rekonstruktion prüfen ────────────────────────────────────────────────
print("\n  REKONSTRUKTION DER WAHRHEIT")
g2 = GridModel()
L.CALIBRATION.apply(g2)
res2 = g2.solve()

# DC exakt?
ok_dc = all(abs(L.CALIBRATION.dc_setpoints.get(k, 0) - v) < 1
            for k, v in TRUE_DC.items())
check("DC-Sollwerte exakt übernommen", ok_dc,
      f"NordLink: {int(L.CALIBRATION.dc_setpoints[('DE','NO2','NordLink')])} MW "
      f"(wahr: -1300)")

# Suszeptanz-Richtung des Fits korrekt? (FR-DE hochskaliert, DE-AT runter)
s_frde = L.CALIBRATION.suscept_scale.get(("FR", "DE"), 1.0)
s_deat = L.CALIBRATION.suscept_scale.get(("DE", "AT"), 1.0)
check("FR–DE hochskaliert (wahr 1.8)", s_frde > 1.2, f"{s_frde:.2f}")
check("DE–AT runterskaliert (wahr 0.6)", s_deat < 0.9, f"{s_deat:.2f}")

# Physische Netto-Positionen reproduziert?
# (Das isolierte Phantom ist aus Flussmessungen prinzipiell NICHT
#  identifizierbar – Phantom und Regelleistung verändern die Position
#  gleichermaßen, messbar ist nur die Summe. Der korrekte Anspruch:
#  das kalibrierte Modell reproduziert die gemessenen Positionen.)
print("\n    Zone   phys. Position (wahr)   kalibriert")
ok_pos = True
for z in ["DE", "IT", "ES", "FR", "NL", "BE"]:
    t_pos = truth_res["zones"][z]["net_phys_mw"]
    c_pos = res2["zones"][z]["net_phys_mw"]
    hit = abs(c_pos - t_pos) < 150
    ok_pos &= hit
    print(f"    {z:4}   {t_pos:>+9} MW            {c_pos:>+9} MW  "
          f"{'✓' if hit else '✗'}")
check("Physische Netto-Positionen exakt reproduziert", ok_pos)
adj_sum = sum(L.CALIBRATION.zone_adjust.values())
print(f"    Σ zone_adjust = {int(adj_sum):+} MW "
      f"(= Phantom + Regelanteile, als Summe geführt)")

# Sync-Bilanzen bleiben geschlossen?
sb = res2["sync_balance"]
check("Synchronzonen-Bilanzen bleiben ~0",
      all(abs(v) < 5 for v in sb.values()),
      ", ".join(f"{k}={v:+d}" for k, v in sb.items()))

# ── 5) Schock AUF kalibriertem Zustand ───────────────────────────────────────
print("\n  SZENARIO AUF KALIBRIERTEM ZUSTAND: FR −20 GW Kernkraft")
g3 = GridModel()
L.CALIBRATION.apply(g3)
g3.shock_generation("FR", "nuclear", -20000)
res3 = g3.solve()
d_es = (res3["zones"]["ES"]["net_phys_mw"] - res2["zones"]["ES"]["net_phys_mw"])
d_gb = None
for f in res3["flows"]:
    if f["label"] == "IFA/IFA2/ElecLink":
        f0 = [x for x in res2["flows"] if x["label"] == f["label"]][0]
        d_gb = f["flow_mw"] - f0["flow_mw"]
print(f"    ES-Position: Δ {d_es:+} MW (zieht mit, via AC-Physik)")
print(f"    FR→GB (IFA): Δ {d_gb:+} MW")
check("AC-Nachbarn reagieren auf den Schock", abs(d_es) > 100, f"Δ {d_es} MW")
check("DC-Kabel bleibt beim Fahrplan (kein Kirchhoff!)", d_gb == 0,
      f"Δ {d_gb} MW – DC folgt Sollwert, nicht der Physik")

# ── 6) measured_at: zeitliche Zuordnung ─────────────────────────────────────
series = {("FR", "DE", None): {TGT - timedelta(minutes=60): 2000.0,
                               TGT: 2400.0},
          ("DE", "AT", None): {TGT - timedelta(minutes=45): 800.0},
          ("ES", "PT", None): {TGT - timedelta(minutes=300): 500.0}}
m = L.measured_at(series, TGT)
check("measured_at: exakter Treffer", m[("FR", "DE", None)] == 2400.0)
check("measured_at: bounded fill (45 min)", m[("DE", "AT", None)] == 800.0)
check("measured_at: zu alt wird verworfen", ("ES", "PT", None) not in m)



# ══════════════════════════════════════════════════════════════════════════
#  LAUF 3 (23.07., 11:50): 234% Auslastung im STARTZUSTAND.
#  Diagnose: Die Flüsse waren nach Kalibrierung echt (A11), die NTCs aber
#  geraten. Gemessener Fluss > angenommene NTC widerlegt die Annahme –
#  kein Netz fährt dauerhaft 234%. Die Limit-Kalibrierung muss die
#  Phantom-Overloads beseitigen, ohne Schock-Overloads zu verhindern.
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 74)
print("  LIMIT-KALIBRIERUNG: gemessener Fluss widerlegt geratene NTC")
print("=" * 74)

from datetime import timedelta as _td
# Messwerte PHYSIKALISCH KONSISTENT erzeugen: ein "wahres" Netz, in dem
# einige Grenzen real über ihrer angenommenen NTC laufen (starke Kopplung
# + entsprechende Erzeugungslage) – wie im Realdaten-Lauf beobachtet.
truth3 = GridModel()
truth3.suscept_scale = {("RS", "BG"): 5.0, ("CZ", "PL"): 5.0,
                        ("AT", "CZ"): 4.0, ("HU", "SI"): 3.0}
truth3.shock_generation("BG", "coal", +1800)
truth3.shock_generation("PL", "wind", +3500)
truth3.shock_load("RS", +900)
truth3.shock_load("AT", +1500)
t3 = truth3.solve()
meas3 = {(f["a"], f["b"], f["label"]): float(f["flow_mw"]) for f in t3["flows"]}

from meridian_grid import INTERCONNECTORS as _IC
ntc_of = {(l.a, l.b, l.label or None): l.ntc for l in _IC}
over_borders = [k for k, v in meas3.items()
                if abs(v) > ntc_of[k] and k[2] is None]
check("Setup: mehrere Grenzen laufen real über der NTC-Annahme",
      len(over_borders) >= 3,
      ", ".join(f"{a}-{b} {int(abs(meas3[(a,b,l)]))}/{int(ntc_of[(a,b,l)])}"
                for a, b, l in over_borders[:4]))

flow_series = {k: {TGT - _td(hours=h): v * (0.9 + 0.02 * (h % 5))
                   for h in range(12)} | {TGT: v}
               for k, v in meas3.items()}
raised = L.CALIBRATION.calibrate_limits(flow_series)
check("Widerlegte Limits angehoben", 
      all(k in raised for k in over_borders),
      f"{len(raised)} angehoben")
k0 = over_borders[0]
check(f"Anhebung = Max-Messung + Marge ({k0[0]}–{k0[1]})",
      raised[k0] >= abs(meas3[k0]) * 1.05,
      f"{int(ntc_of[k0])} → {int(raised[k0])}")

L.CALIBRATION.calibrate(meas3, TGT)
g4 = GridModel(); L.CALIBRATION.apply(g4)
r4 = g4.solve()
check("Startzustand ohne Phantom-Overloads", r4["n_overloads"] == 0,
      f"{r4['n_overloads']} Overloads, max {r4['max_util']*100:.0f}%")
check("max. Auslastung im plausiblen Band (≤ ~95%)", r4["max_util"] <= 0.95,
      f"{r4['max_util']*100:.0f}%")
mf = {(f["a"], f["b"], f["label"]): f for f in r4["flows"]}
f0 = mf[k0]
check(f"{k0[0]}–{k0[1]} trägt den Messfluss auf angehobenem Limit",
      f0["ntc_raised"] and
      abs(f0["flow_mw"] - meas3[k0]) < max(120, abs(meas3[k0]) * 0.15),
      f"{f0['flow_mw']:+} MW (gemessen {meas3[k0]:+.0f}) / "
      f"{f0['ntc']:.0f} NTC = {f0['util']*100:.0f}%")

# Schocks müssen weiterhin Overloads erzeugen können
g4.shock_generation("FR", "nuclear", -25000)
r5 = g4.solve()
check("Schock erzeugt weiterhin Overloads (Limits nicht 'weichgespült')",
      r5["n_overloads"] > 0, f"{r5['n_overloads']} Overloads bei FR -25 GW")

# ── N-1-Stresstest auf kalibriertem Zustand ──────────────────────────────
print("\n  N-1-STRESSTEST auf kalibriertem Zustand")
from meridian_grid import n_minus_1
g6 = GridModel(); L.CALIBRATION.apply(g6)
n1 = n_minus_1(g6)
check("Alle Verbindungen getestet", n1["n_tested"] >= 80, str(n1["n_tested"]))
check("Ranking nach Schweregrad sortiert",
      all(n1["ranking"][i]["severity"] >= n1["ranking"][i+1]["severity"]
          for i in range(len(n1["ranking"]) - 1)))
plt = [r for r in n1["ranking"] if {r["a"], r["b"]} == {"PL", "LT"}][0]
check("PL–LT-Ausfall als Inselbildung erkannt", plt["island_split"],
      f"severity {plt['severity']}")
top = n1["ranking"][0]
print(f"    Kritischster Ausfall: {top['a']}–{top['b']} "
      f"(severity {top['severity']}, "
      f"{'INSELBILDUNG, ' if top['island_split'] else ''}"
      f"{top['new_overloads']} neue Overloads)")
print(f"    N-1-sicher: {n1['secure']} | kritische Ausfälle: {n1['n_critical']}")




# ══════════════════════════════════════════════════════════════════════════
#  LAUF 4 (23.07., 12:15): Alle Rest-Overloads in NORWEGEN (NO3-NO5 194%).
#  A11: 76/83 – es fehlten exakt die 7 GB-Kabel (post-Brexit kein A11).
#  Die Alles-oder-nichts-Bilanzkorrektur ließ FR/BE/NL/DK1/NO2/IE/GB
#  ungepinnt; der Abgleichfehler entlud sich auf Norwegens Binnengrenzen.
#  Fix: unbemessene DC-Kabel zählen mit ihrem Sollwert als abgedeckt.
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 74)
print("  GB-KABEL OHNE A11: Fehler darf sich nicht in Norwegen entladen")
print("=" * 74)

truth4 = GridModel()
truth4.suscept_scale = {("NO1", "SE3"): 1.6, ("SE2", "SE3"): 0.8}
truth4.shock_generation("NO2", "hydro", +1200)   # Süd-Norwegen exportstark
truth4.shock_generation("SE2", "hydro", +800)
truth4.shock_load("GB", +1500)
t4 = truth4.solve()
all_meas = {(f["a"], f["b"], f["label"]): float(f["flow_mw"])
            for f in t4["flows"]}
GB_BORDERS = [k for k in all_meas if "GB" in (k[0], k[1])]
meas4 = {k: v for k, v in all_meas.items() if k not in GB_BORDERS}
check("Setup: exakt 7 GB-Grenzen unbemessen", len(GB_BORDERS) == 7,
      str(len(GB_BORDERS)))

flow_series4 = {k: {TGT - _td(hours=hh): v for hh in range(12)} | {TGT: v}
                for k, v in meas4.items()}
L.CALIBRATION.calibrate_limits(flow_series4)
L.CALIBRATION.calibrate(meas4, TGT)

check("ALLE 40 Zonen gepinnt (GB-Kabel via Sollwert)",
      len(L.CALIBRATION.zone_adjust) == 40,
      f"{len(L.CALIBRATION.zone_adjust)} Zonen")
check("Unbemessene Grenzen dokumentiert (7 DC, 0 AC)",
      len(L.CALIBRATION.unmeasured) == 7 and
      all(u["kind"] == "DC" for u in L.CALIBRATION.unmeasured))

g7 = GridModel(); L.CALIBRATION.apply(g7)
r7 = g7.solve()
mf7 = {(f["a"], f["b"], f["label"]): f for f in r7["flows"]}
# Norwegens Binnengrenzen: Modell muss Messung treffen, kein Phantom-Transit
nordic_err = []
for k in meas4:
    if k[0].startswith(("NO", "SE")) and k[1].startswith(("NO", "SE")):
        nordic_err.append(abs(mf7[k]["flow_mw"] - meas4[k]))
check("Nordic-Binnengrenzen treffen die Messung (Ø < 60 MW)",
      sum(nordic_err) / len(nordic_err) < 60,
      f"Ø {int(sum(nordic_err)/len(nordic_err))} MW über {len(nordic_err)} Grenzen")
no35 = mf7[("NO3", "NO5", None)]
check("NO3–NO5 ohne Phantom-Transit",
      abs(no35["flow_mw"] - meas4[("NO3", "NO5", None)]) < 100 and not no35["overload"],
      f"{no35['flow_mw']:+} MW (gemessen {meas4[('NO3','NO5',None)]:+.0f}), "
      f"{no35['util']*100:.0f}%")
check("Startzustand ohne Overloads", r7["n_overloads"] == 0,
      f"{r7['n_overloads']} OL, max {r7['max_util']*100:.0f}%")
gb_adj = L.CALIBRATION.zone_adjust.get("GB", 0)
print(f"    GB-Zonenkorrektur trägt die Unsicherheit: {gb_adj/1000:+.1f} GW "
      f"(dort gehört sie hin)")




# ══════════════════════════════════════════════════════════════════════════
#  LAUF 5 (23.07., 15:01): A11 bricht von 76 auf 33 Grenzen ein (ENTSO-E-
#  Drosselung) – und BG–RS stand wieder bei "188%", weil calibrate_limits()
#  per clear() das GELERNTE 1550er-Limit verwarf. Gelernte Limits sind
#  Fakten: Was eine Grenze nachweislich getragen hat, verfällt nicht.
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 74)
print("  DROSSEL-AUSSETZER: gelernte Limits und Fits dürfen nicht verfallen")
print("=" * 74)

# Zyklus 1: voller A11-Lauf – Limits werden gelernt (aus LAUF 3 vorhanden)
k_hi = over_borders[0]                       # z.B. RS–BG, Limit angehoben
lim_learned = L.CALIBRATION.ntc_raise.get(k_hi)
scale_learned = dict(L.CALIBRATION.suscept_scale)
check("Vorbedingung: Limit aus vollem Lauf gelernt", lim_learned is not None,
      f"{k_hi[0]}–{k_hi[1]}: {lim_learned}")

# Zyklus 2: Drossel-Aussetzer – nur 1/3 der Grenzen liefert, k_hi NICHT dabei
meas_thin = {}
for i, (k, v) in enumerate(meas4.items()):
    if k != k_hi and i % 3 == 0:
        meas_thin[k] = v
series_thin = {k: {TGT: v} for k, v in meas_thin.items()}
L.CALIBRATION.calibrate_limits(series_thin)
L.CALIBRATION.calibrate(meas_thin, TGT)

check("Gelerntes Limit überlebt den Aussetzer",
      L.CALIBRATION.ntc_raise.get(k_hi) == lim_learned,
      f"{k_hi[0]}–{k_hi[1]} bleibt {L.CALIBRATION.ntc_raise.get(k_hi)}")
kept = sum(1 for k, v in scale_learned.items()
           if abs(L.CALIBRATION.suscept_scale.get(k, 1.0) - v) < 1e-6)
check("Fit-Faktoren unbemessener Grenzen bleiben erhalten",
      kept >= len(scale_learned) * 0.5,
      f"{kept}/{len(scale_learned)} unverändert fortgeführt")

g8 = GridModel(); L.CALIBRATION.apply(g8)
f8 = [f for f in g8.solve()["flows"] if (f["a"], f["b"]) == (k_hi[0], k_hi[1])][0]
check("Kein Rückfall auf Phantom-Overload (das 188%-Symptom)",
      not f8["overload"] and f8["ntc"] == lim_learned,
      f"{f8['abs_mw']} MW / {f8['ntc']:.0f} NTC (= gelerntes Limit) "
      f"= {f8['util']*100:.0f}%")

# Retry-Mechanik: 2× gedrosselt, dann Erfolg
class ThrottleHTTP:
    def __init__(self): self.n = 0
    def get(self, url, params=None, timeout=None):
        class R: pass
        r = R(); self.n += 1
        if self.n <= 2:
            r.status_code = 429; r.content = b""
        else:
            r.status_code = 200
            r.content = ("""<?xml version="1.0"?>
<Publication_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:0">
<TimeSeries><Period><timeInterval><start>2026-07-23T10:00Z</start></timeInterval>
<resolution>PT60M</resolution><Point><position>1</position><quantity>1234</quantity>
</Point></Period></TimeSeries></Publication_MarketDocument>""").encode()
        return r

ff = L.FlowFetcher("k", http=ThrottleHTTP())
ff.stats = {"ok": 0, "throttled": 0, "empty": 0, "error": 0}
raw = ff._get({"documentType": "A11"})
check("Retry überwindet 429-Drosselung", raw is not None and
      ff.stats["throttled"] == 2 and ff.stats["ok"] == 1,
      f"{ff.stats['throttled']}× gedrosselt, dann ok")




# ── LAUF 6 (16:49): Zonen-Retry + träge Balkan-A11 ────────────────────────
print("\n" + "=" * 74)
print("  ZONEN-RETRY + TRÄGE A11: BG/RS dürfen nicht systematisch ausfallen")
print("=" * 74)

# Zonen-Fetcher überwindet 429 (der Retry fehlte hier bis Lauf 16:49!)
class ThrottleGL:
    def __init__(self): self.n = 0
    def get(self, url, params=None, timeout=None):
        class R: pass
        r = R(); self.n += 1
        if self.n <= 2:
            r.status_code = 429; r.content = b""
        else:
            r.status_code = 200
            r.content = b"<GL_MarketDocument>x</GL_MarketDocument>"
        return r
zf = L.EntsoeGridFetcher("k", http=ThrottleGL())
raw = zf._get({"documentType": "A75"})
check("Zonen-Fetcher: Retry überwindet 429", raw is not None)

# Träge A11-Grenze: letzte Messung 3h vor dem Netzschnitt → mit 240-min-Fill
# bleibt die Zone gepinnt (mit 120 wäre sie rausgefallen)
lag_key = [k for k in meas4 if k[2] is None][0]
lag_series = dict(flow_series4)
lag_series[lag_key] = {TGT - _td(hours=3): meas4[lag_key]}
m_lag = L.measured_at(lag_series, TGT)
check("240-min-Fill: 3h alte Messung wird noch verwendet",
      lag_key in m_lag, f"{lag_key[0]}–{lag_key[1]}")
m_lag2 = L.measured_at(lag_series, TGT, max_fill=120)
check("(Gegenprobe: mit 120 min wäre sie draußen)", lag_key not in m_lag2)

L.CALIBRATION.calibrate(m_lag, TGT)
check("Alle Zonen trotz träger Grenze gepinnt",
      len(L.CALIBRATION.zone_adjust) == 40 and not L.CALIBRATION.unpinned,
      f"{len(L.CALIBRATION.zone_adjust)} gepinnt, unpinned={L.CALIBRATION.unpinned}")


print("\n" + "=" * 74)
if FAILS:
    print(f"  {len(FAILS)} FEHLGESCHLAGEN: {', '.join(FAILS)}"); sys.exit(1)
print("  KALIBRIERUNG BESTANDEN")
print("=" * 74)

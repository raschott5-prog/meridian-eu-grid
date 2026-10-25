#!/usr/bin/env python3
"""
test_pst.py — Phase-shifting transformer (PST) test suite
==========================================================

Tests the PST model on the DE–PL border (Mikułowa / Vierraden):
neutral = plain AC physics, target flow held, range saturation,
blocked flow rerouted via parallel paths (Kirchhoff), derate/cut,
A11 calibration pins the PST to the measured flow
with its range left free for shocks.

Numerically cross-validated against the JS frontend (meridian_grid_viz.html):
pstControl() mirrors GridModel._pst_control() to the MW.
"""

import sys
from datetime import datetime, timezone

from meridian_grid import GridModel, INTERCONNECTORS

PASS = "✓"
FAIL = "✗"
results = []

def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    status = PASS if ok else FAIL
    print(f"  {status} {name}" + (f"  [{detail}]" if detail else ""))
    return ok

def flows(g):
    r = g.solve()
    return r, {(f["a"], f["b"], f["label"]): f for f in r["flows"]}

DEPL = ("DE", "PL", None)
PST_LINE = next(l for l in INTERCONNECTORS if (l.a, l.b) == ("DE", "PL"))


# ══════════════════════════════════════════════════════════════════════════════
# 1. Topology
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 1. Topology ──")
pst_lines = [l for l in INTERCONNECTORS if l.pst > 0]
check("DE–PL carries a PST", PST_LINE.pst > 0, f"range ±{PST_LINE.pst:.0f} MW")
check("PSTs only on AC edges", all(l.kind == "AC" for l in pst_lines))


# ══════════════════════════════════════════════════════════════════════════════
# 2. Neutral = plain AC physics
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 2. Neutral (0°) ──")
g = GridModel()
r0, f0 = flows(g)
check("Default is neutral (no target, no shift)",
      f0[DEPL]["pst"]["target_mw"] is None and f0[DEPL]["pst"]["shift_mw"] == 0)
g = GridModel(); g.set_pst("DE", "PL", None)
_, fn = flows(g)
check("Explicit neutral == default",
      all(fn[k]["flow_mw"] == f0[k]["flow_mw"] for k in f0))
check("Non-PST lines report pst=None",
      all(f["pst"] is None for k, f in f0.items() if k != DEPL))


# ══════════════════════════════════════════════════════════════════════════════
# 3. Target flow is held
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 3. Target flow ──")
for tgt in (0, +800, -600):
    g = GridModel(); g.set_pst("DE", "PL", tgt)
    _, f = flows(g)
    check(f"DE–PL holds target {tgt:+} MW", abs(f[DEPL]["flow_mw"] - tgt) <= 1,
          f"{f[DEPL]['flow_mw']:+} MW, shift {f[DEPL]['pst']['shift_mw']:+}")

# Disturbance: +15 GW wind in DE. Without PST, DE→PL rises; with PST held.
g = GridModel(); g.shock_generation("DE", "wind", +15000)
rw, fw = flows(g)
g = GridModel(); g.shock_generation("DE", "wind", +15000)
g.set_pst("DE", "PL", f0[DEPL]["flow_mw"])
rh, fh = flows(g)
check("Wind shock raises DE→PL without PST",
      fw[DEPL]["flow_mw"] > f0[DEPL]["flow_mw"] + 300,
      f"{f0[DEPL]['flow_mw']:+} → {fw[DEPL]['flow_mw']:+} MW")
check("PST holds DE–PL during the wind shock",
      abs(fh[DEPL]["flow_mw"] - f0[DEPL]["flow_mw"]) <= 1,
      f"{fh[DEPL]['flow_mw']:+} MW")


# ══════════════════════════════════════════════════════════════════════════════
# 4. Kirchhoff: blocked flow is rerouted, not lost
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 4. Rerouting ──")
pl_in = lambda f: sum(x["flow_mw"] if x["b"] == "PL" else -x["flow_mw"]
                      for x in f.values() if "PL" in (x["a"], x["b"]))
check("PL net import unchanged by the PST (only the path changes)",
      abs(pl_in(fh) - pl_in(fw)) <= 3,
      f"{pl_in(fw):+} vs {pl_in(fh):+} MW")
k_czpl = ("CZ", "PL", None)
check("Blocked DE→PL flow shifts onto CZ–PL",
      fh[k_czpl]["flow_mw"] > fw[k_czpl]["flow_mw"] + 100,
      f"CZ–PL {fw[k_czpl]['flow_mw']:+} → {fh[k_czpl]['flow_mw']:+} MW")
check("Zone balances (slack) untouched by the PST",
      all(rh["zones"][c]["reg_mw"] == z["reg_mw"] for c, z in rw["zones"].items()))


# ══════════════════════════════════════════════════════════════════════════════
# 5. Saturation
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 5. Range limit ──")
g = GridModel(); g.set_pst("DE", "PL", -2500)
_, fs = flows(g)
p = fs[DEPL]["pst"]
check("Unreachable target saturates the PST", p["saturated"])
check("Shift clamped to range", abs(abs(p["shift_mw"]) - PST_LINE.pst) <= 1,
      f"{p['shift_mw']:+} / ±{p['range_mw']}")
check("Flow moves toward target but stops short",
      -2500 < fs[DEPL]["flow_mw"] < f0[DEPL]["flow_mw"],
      f"{fs[DEPL]['flow_mw']:+} MW")


# ══════════════════════════════════════════════════════════════════════════════
# 6. Derate / cut
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 6. Derate & cut ──")
g = GridModel(); g.shock_line("DE", "PL", 0.5); g.set_pst("DE", "PL", 0)
_, fd = flows(g)
check("-50% line halves the PST range", fd[DEPL]["pst"]["range_mw"] == round(PST_LINE.pst / 2))
check("Derated PST still holds a reachable target", abs(fd[DEPL]["flow_mw"]) <= 1)
g = GridModel(); g.shock_line("DE", "PL", 0.0); g.set_pst("DE", "PL", 800)
_, fc = flows(g)
check("Cut line: no flow, no shift",
      fc[DEPL]["flow_mw"] == 0 and fc[DEPL]["pst"]["shift_mw"] == 0)


# ══════════════════════════════════════════════════════════════════════════════
# 7. A11 calibration pins the PST to the measured flow
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 7. Calibration ──")
import meridian_grid_live as L
TGT = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
truth = GridModel(); truth.shock_generation("PL", "wind", +2500)
truth.set_pst("DE", "PL", -900)
meas = {(f["a"], f["b"], f["label"]): float(f["flow_mw"])
        for f in truth.solve()["flows"]}
cal = L.Calibration()
cal.calibrate(meas, TGT)
check("Measured DE–PL flow becomes the PST target",
      round(cal.pst_targets.get(("DE", "PL"), 1e9)) == -900,
      f"{cal.pst_targets.get(('DE', 'PL'))}")
gc = GridModel(); cal.apply(gc)
_, fcal = flows(gc)
check("Calibrated model reproduces measured DE–PL", abs(fcal[DEPL]["flow_mw"] + 900) <= 1,
      f"{fcal[DEPL]['flow_mw']:+} MW")
p = fcal[DEPL]["pst"]
check("Calibrated state leaves the PST range free (angle folded into fit)",
      not p["saturated"] and abs(p["shift_mw"]) < 0.5 * p["range_mw"],
      f"shift {p['shift_mw']:+} / ±{p['range_mw']}")
gc.shock_generation("DE", "wind", +15000)
_, fcs = flows(gc)
check("Calibrated PST holds the measured flow under a shock",
      abs(fcs[DEPL]["flow_mw"] + 900) <= 1, f"{fcs[DEPL]['flow_mw']:+} MW")

# Real-data case (live run 25.10.): neutral model flow far above the
# measured one must not leave the PST saturated in the calibrated state
truth = GridModel(); truth.shock_generation("DE", "wind", +12000)
truth.shock_load("PL", +3000)
meas2 = {(f["a"], f["b"], f["label"]): float(f["flow_mw"])
         for f in truth.solve()["flows"]}
meas2[("DE", "PL", None)] = meas2[("DE", "PL", None)] * 0.45
cal2 = L.Calibration(); cal2.calibrate(meas2, TGT)
g2 = GridModel(); cal2.apply(g2)
_, f2 = flows(g2)
check("High neutral flow vs. low measurement: PST not stuck at its stop",
      not f2[DEPL]["pst"]["saturated"]
      and abs(f2[DEPL]["flow_mw"] - meas2[("DE", "PL", None)]) <= 1,
      f"meas {meas2[('DE', 'PL', None)]:+.0f}, model {f2[DEPL]['flow_mw']:+}, "
      f"shift {f2[DEPL]['pst']['shift_mw']:+}")


# ══════════════════════════════════════════════════════════════════════════════
total = len(results)
passed = sum(1 for _, ok, _ in results if ok)
failed = [(name, detail) for name, ok, detail in results if not ok]

print(f"""
{'='*70}
  PST TESTS: {passed}/{total} passed
{'='*70}""")

if failed:
    print("  FAILED:")
    for name, detail in failed:
        print(f"    ✗ {name}" + (f"  [{detail}]" if detail else ""))
    sys.exit(1)
else:
    print("  ALL PST TESTS PASSED")

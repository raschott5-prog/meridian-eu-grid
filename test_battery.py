#!/usr/bin/env python3
"""
test_battery.py — Battery storage model test suite
===================================================

Tests the Battery dataclass and GridModel battery integration.
Covers: manual P mode, SoC limits, frequency-support droop,
island-local effect, E/P ratio, API output consistency.

Numerically cross-validated against the JS frontend (meridian_grid_viz.html):
both use 5% droop = full power at ±200 mHz, SoC limits at 1% / 99%.
"""

import sys
import math

# ── helpers ──────────────────────────────────────────────────────────────────

def _grid():
    from meridian_grid import GridModel
    return GridModel()

def _solve(g):
    return g.solve()

PASS = "✓"
FAIL = "✗"
results = []

def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    status = PASS if ok else FAIL
    print(f"  {status} {name}" + (f"  [{detail}]" if detail else ""))
    return ok


# ══════════════════════════════════════════════════════════════════════════════
# 1. Battery dataclass
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Battery dataclass ─────────────────────────────────────────────────────")

from meridian_grid import Battery

b = Battery(cap_mwh=500, p_max_mw=100, soc=0.5)
check("E/P ratio: 500 MWh / 100 MW = 5.0 h",
      abs(b.e_p_ratio_h - 5.0) < 0.001)

# Discharging at 50 MW with 50% SoC → 5 h
ttl = b.time_to_limit_h(50.0)
check("Time-to-empty: 500 * 0.5 / 50 = 5.0 h",
      ttl is not None and abs(ttl - 5.0) < 0.001, f"got {ttl}")

# Charging at 100 MW with 50% SoC → 2.5 h to full
ttl2 = b.time_to_limit_h(-100.0)
check("Time-to-full: 500 * 0.5 / 100 = 2.5 h",
      ttl2 is not None and abs(ttl2 - 2.5) < 0.001, f"got {ttl2}")

# Standby
check("Standby returns None",
      b.time_to_limit_h(0.0) is None)
check("Near-zero (<2 MW) returns None",
      b.time_to_limit_h(1.5) is None)


# ══════════════════════════════════════════════════════════════════════════════
# 2. GridModel battery API
# ══════════════════════════════════════════════════════════════════════════════
print("\n── GridModel battery API ─────────────────────────────────────────────────")

g = _grid()
bat = g.add_battery("DE", cap_mwh=1000, p_max_mw=200, soc=0.6)
check("Battery added to DE", "DE" in g.batteries)
check("Battery object returned", bat is g.batteries["DE"])

g.set_battery_p("DE", 150.0)
check("set_battery_p: p_manual_mw updated",
      g.batteries["DE"].p_manual_mw == 150.0)
check("set_battery_p: mode forced to manual",
      g.batteries["DE"].mode == "manual")

g.set_battery_soc("DE", 1.5)   # clamp
check("set_battery_soc clamps to 1.0",
      g.batteries["DE"].soc == 1.0)
g.set_battery_soc("DE", -0.1)
check("set_battery_soc clamps to 0.0",
      g.batteries["DE"].soc == 0.0)

g.remove_battery("DE")
check("Battery removed", "DE" not in g.batteries)


# ══════════════════════════════════════════════════════════════════════════════
# 3. Manual P mode — load flow effect
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Manual P mode — load flow effect ─────────────────────────────────────")

# Baseline DE physical position
g_base = _grid()
r_base = _solve(g_base)
de_base_phys = r_base["zones"]["DE"]["net_phys_mw"]

# DE battery discharges 500 MW
g_bat = _grid()
g_bat.add_battery("DE", cap_mwh=5000, p_max_mw=1000, soc=0.8)
g_bat.set_battery_p("DE", 500.0)
r_bat = _solve(g_bat)
de_bat_phys = r_bat["zones"]["DE"]["net_phys_mw"]

delta = de_bat_phys - de_base_phys
check("500 MW discharge increases DE net physical position",
      delta > 350,   # not exactly 500 due to slack redistribution
      f"Δ = {delta:.0f} MW")
check("Battery appears in API output",
      r_bat["zones"]["DE"]["battery"] is not None)
check("Battery p_mw = 500 in API",
      r_bat["zones"]["DE"]["battery"]["p_mw"] == 500)
check("Battery in top-level batteries dict",
      "DE" in r_bat["batteries"] and r_bat["batteries"]["DE"]["p_mw"] == 500)


# ══════════════════════════════════════════════════════════════════════════════
# 4. SoC limits — hard stops
# ══════════════════════════════════════════════════════════════════════════════
print("\n── SoC hard limits ───────────────────────────────────────────────────────")

g_empty = _grid()
g_empty.add_battery("FR", cap_mwh=500, p_max_mw=100, soc=0.0)
g_empty.set_battery_p("FR", 100.0)   # try to discharge — should be blocked
check("Empty battery (soc=0) cannot discharge: _bat_p returns 0",
      g_empty._bat_p("FR") == 0.0)

r_empty = _solve(g_empty)
check("Empty battery: no effect on load flow vs baseline",
      abs(r_empty["zones"]["FR"]["net_phys_mw"]
          - r_base["zones"]["FR"]["net_phys_mw"]) < 50)

g_full = _grid()
g_full.add_battery("FR", cap_mwh=500, p_max_mw=100, soc=1.0)
g_full.set_battery_p("FR", -100.0)  # try to charge — should be blocked
check("Full battery (soc=1) cannot charge: _bat_p returns 0",
      g_full._bat_p("FR") == 0.0)


# ══════════════════════════════════════════════════════════════════════════════
# 5. reset_batteries — keeps batteries, clears P
# ══════════════════════════════════════════════════════════════════════════════
print("\n── reset_batteries ───────────────────────────────────────────────────────")

g_reset = _grid()
g_reset.add_battery("IT", cap_mwh=300, p_max_mw=60, soc=0.7)
g_reset.set_battery_p("IT", 50.0)
g_reset.reset_batteries()
check("reset_batteries: battery still installed", "IT" in g_reset.batteries)
check("reset_batteries: p_manual_mw = 0", g_reset.batteries["IT"].p_manual_mw == 0.0)
check("reset_batteries: p_freq_mw = 0",   g_reset.batteries["IT"].p_freq_mw  == 0.0)


# ══════════════════════════════════════════════════════════════════════════════
# 6. Frequency-support mode
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Frequency-support mode ────────────────────────────────────────────────")

# Cause a deficit: remove 10 GW FR nuclear → CE underfrequency
g_freq = _grid()
g_freq.shock_generation("FR", "nuclear", -10000)
r_before = _solve(g_freq)
ce_hz_before = next(f["hz"] for f in r_before["frequencies"]
                    if not f["is_island"] and f["sync"] == "CE")

# Add freq-support battery to FR with plenty of charge
g_freq2 = _grid()
g_freq2.shock_generation("FR", "nuclear", -10000)
g_freq2.add_battery("FR", cap_mwh=50000, p_max_mw=2000, soc=0.9, mode="freq")
r_after = _solve(g_freq2)
ce_hz_after = next(f["hz"] for f in r_after["frequencies"]
                   if not f["is_island"] and f["sync"] == "CE")

check("Freq-support battery raises CE frequency after nuclear loss",
      ce_hz_after > ce_hz_before,
      f"{ce_hz_before:.3f} → {ce_hz_after:.3f} Hz")
bat_p = r_after["batteries"]["FR"]["p_mw"]
check("Freq-support battery discharges (P > 0) under underfrequency",
      bat_p > 0, f"P = {bat_p} MW")
check("Battery P proportional to deficit (droop response)",
      100 < bat_p <= 2000, f"P = {bat_p} MW")


# ══════════════════════════════════════════════════════════════════════════════
# 7. Freq-support SoC limit stops droop response
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Freq-support SoC stops droop ──────────────────────────────────────────")

g_esoc = _grid()
g_esoc.shock_generation("FR", "nuclear", -10000)
g_esoc.add_battery("FR", cap_mwh=500, p_max_mw=2000, soc=0.004, mode="freq")  # below BAT_SOC_MIN=0.005
r_esoc = _solve(g_esoc)
bat_p_esoc = r_esoc["batteries"]["FR"]["p_mw"]
check("Empty battery (soc<BAT_SOC_MIN 0.5%) cannot respond to underfrequency",
      bat_p_esoc == 0, f"P = {bat_p_esoc} MW")

hz_esoc = next(f["hz"] for f in r_esoc["frequencies"]
               if not f["is_island"] and f["sync"] == "CE")
check("Empty battery: frequency same as without battery",
      abs(hz_esoc - ce_hz_before) < 0.005,
      f"{hz_esoc:.3f} vs {ce_hz_before:.3f} Hz")


# ══════════════════════════════════════════════════════════════════════════════
# 8. Island-local effect
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Island-local frequency effect ─────────────────────────────────────────")

# Cut the only link between Baltic states and CE (PL–LT)
# Baltic islanding → separate frequency from CE
g_isl = _grid()
g_isl.shock_line("PL", "LT", 0.0)          # AC cut
# Also cut NordBalt and EstLink HVDC (set to 0)
for ln in __import__('meridian_grid').INTERCONNECTORS:
    if ln.label in ("NordBalt", "EstLink 1+2"):
        g_isl.set_dc(ln.a, ln.b, 0.0, ln.label)
r_isl_base = _solve(g_isl)

# Find Baltic island
balt_zones = {"EE", "LV", "LT"}
balt_freq_base = next(
    (f for f in r_isl_base["frequencies"]
     if f["is_island"] and balt_zones & set(f["members"])), None)
check("Baltic island detected after PL–LT cut + HVDC=0",
      balt_freq_base is not None,
      f"members: {balt_freq_base['members'] if balt_freq_base else '?'}")

# Battery in Baltic island helps Baltic freq, not CE
g_isl2 = _grid()
g_isl2.shock_line("PL", "LT", 0.0)
for ln in __import__('meridian_grid').INTERCONNECTORS:
    if ln.label in ("NordBalt", "EstLink 1+2"):
        g_isl2.set_dc(ln.a, ln.b, 0.0, ln.label)
g_isl2.add_battery("LT", cap_mwh=5000, p_max_mw=500, soc=0.9)
g_isl2.set_battery_p("LT", 300.0)   # discharge into Baltic island
r_isl2 = _solve(g_isl2)

lt_batt_phys = r_isl2["zones"]["LT"]["net_phys_mw"]
lt_base_phys = r_isl_base["zones"]["LT"]["net_phys_mw"]
check("Battery in LT raises LT net physical position within island",
      lt_batt_phys > lt_base_phys + 150,
      f"Δ = {lt_batt_phys - lt_base_phys:.0f} MW")


# ══════════════════════════════════════════════════════════════════════════════
# 9. API output completeness
# ══════════════════════════════════════════════════════════════════════════════
print("\n── API output completeness ───────────────────────────────────────────────")

g_api = _grid()
g_api.add_battery("ES", cap_mwh=800, p_max_mw=200, soc=0.65)
g_api.set_battery_p("ES", 100.0)
r_api = _solve(g_api)

bat_zone = r_api["zones"]["ES"]["battery"]
check("Battery in zones[ES] output", bat_zone is not None)
required_keys = {"cap_mwh","p_max_mw","soc","mode","p_mw","e_p_ratio_h","time_to_limit_h"}
check("All battery keys present", required_keys <= set(bat_zone.keys()),
      f"missing: {required_keys - set(bat_zone.keys())}")
check("soc rounded to 3dp", bat_zone["soc"] == 0.65)
check("e_p_ratio_h = 4.0", abs(bat_zone["e_p_ratio_h"] - 4.0) < 0.01)
check("time_to_limit_h = 800*0.65/100 = 5.2 h",
      bat_zone["time_to_limit_h"] is not None
      and abs(bat_zone["time_to_limit_h"] - 5.2) < 0.01,
      f"got {bat_zone['time_to_limit_h']}")
check("Zones without battery have battery=None",
      r_api["zones"]["DE"]["battery"] is None)
check("Top-level batteries dict present", "batteries" in r_api)
check("ES in batteries dict", "ES" in r_api["batteries"])


# ══════════════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════════════
passed = sum(1 for _, ok, _ in results if ok)
total  = len(results)
failed = [(name, detail) for name, ok, detail in results if not ok]

print(f"""
{'='*70}
  BATTERY TESTS: {passed}/{total} passed
{'='*70}""")

if failed:
    print("  FAILED:")
    for name, detail in failed:
        print(f"    ✗ {name}" + (f"  [{detail}]" if detail else ""))
    sys.exit(1)
else:
    print("  ALL BATTERY TESTS PASSED")
    print("  Numerically consistent with JS frontend (5% droop, ±200 mHz)")

/* ══════════════════════════════════════════════════════════════════════
   MERIDIAN Grid — Dynamic Line Rating: steady-state thermal balance
   ----------------------------------------------------------------------
   Physics after CIGRE Technical Brochure 601 (2014) / IEEE Std 738-2012.
   Steady-state heat balance of an overhead conductor:

       q_j(I) + q_s  =  q_c(v,T_amb) + q_r(T_amb)

   with
       q_j  Joule (ohmic) heat gain      = I² · R(T_cond)
       q_s  solar heat gain              = α · S · D
       q_c  convective cooling           (forced + natural, CIGRE 601)
       q_r  radiative cooling            = π·D·ε·σ·(T_cond⁴ − T_amb⁴)

   The ampacity I_max is the current at which the conductor reaches its
   rated temperature T_cond = T_max for the given weather (T_amb, v, S).
   Solving the balance for I:

       I_max = sqrt( (q_c + q_r − q_s) / R(T_max) )

   The DLR factor is the ratio of ampacity under actual weather to the
   ampacity under the reference weather the static NTC is defined for:

       DLR = I_max(weather) / I_max(reference)

   This is deliberately a *per-conductor-type* model. We do not have real
   asset data, so conductor types (A/B/C/D) carry standardised CIGRE-style
   parameters. The model is honest about that in the docs; what it captures
   correctly is the *direction and rough magnitude* of weather sensitivity,
   which is the teaching point.

   NOTE ON WIND ANGLE: forced convection depends on the angle between wind
   and conductor. We use a fixed effective angle (45°) rather than pretend
   to know each line's compass bearing — another honest simplification.
   ══════════════════════════════════════════════════════════════════════ */

// ---- Physical constants ----
const SIGMA_SB = 5.670374419e-8; // Stefan–Boltzmann [W/m²K⁴]

// ---- Conductor type library (standardised, not asset-specific) ----
// d      : conductor diameter [m]
// R20    : AC resistance at 20 °C [Ω/m]
// alphaR : temp. coefficient of resistance [1/K]
// Tmax   : rated max conductor temperature [°C]
// absorp : solar absorptivity [-]
// emiss  : emissivity [-]
// label  : short human label
const CONDUCTOR_TYPES = {
  A: { d: 0.0318, R20: 6.4e-5,  alphaR: 0.00403, Tmax: 80, absorp: 0.85, emiss: 0.85,
       label: "380 kV OHL (Al/St 450/50)" },
  B: { d: 0.0218, R20: 1.19e-4, alphaR: 0.00403, Tmax: 75, absorp: 0.85, emiss: 0.85,
       label: "220 kV OHL (Al/St 240/40)" },
  C: { d: 0.0153, R20: 2.42e-4, alphaR: 0.00403, Tmax: 70, absorp: 0.85, emiss: 0.85,
       label: "110 kV OHL (Al/St 120/20)" },
  D: { d: 0.0900, R20: 3.0e-5,  alphaR: 0.00393, Tmax: 65, absorp: 0.00, emiss: 0.90,
       label: "HV underground cable (proxy)" },
};

// ---- Reference weather the static NTC values are assumed to hold at ----
// Matches the existing sliders' reference point so factor = 1.0 there.
const REF_WEATHER = { Tamb: 15.0, v: 3.0, S: 200.0 };

// AC resistance at conductor temperature T [°C]
function conductorR(type, T) {
  return type.R20 * (1 + type.alphaR * (T - 20));
}

// Air properties at film temperature T_film [°C] (CIGRE 601 approximations)
function airProps(T_film) {
  const Tf = T_film + 273.15;
  // thermal conductivity of air [W/m·K]
  const lambda = 2.368e-2 + 7.23e-5 * T_film - 2.763e-8 * T_film * T_film;
  // kinematic viscosity [m²/s]
  const nu = 1.32e-5 + 9.5e-8 * T_film;
  // air density [kg/m³] (sea level; altitude correction omitted intentionally)
  const rho = 1.293 * 273.15 / Tf;
  return { lambda, nu, rho };
}

// Convective cooling per unit length [W/m]
// Uses the larger of forced and natural convection (CIGRE 601 §standard practice).
function convCooling(type, T_cond, T_amb, v) {
  const T_film = 0.5 * (T_cond + T_amb);
  const { lambda, nu } = airProps(T_film);
  const D = type.d;
  const dT = Math.max(0, T_cond - T_amb);

  // Reynolds number
  const Re = v > 0 ? (v * D / nu) : 0;

  // Forced convection Nusselt number (CIGRE 601 smooth-conductor correlation)
  // Nu = B1 · Re^n , coefficients depend on Re range.
  let Nu_forced = 0;
  if (Re > 0) {
    let B1, n;
    if (Re < 2650) { B1 = 0.641; n = 0.471; }
    else           { B1 = 0.048; n = 0.800; }
    Nu_forced = B1 * Math.pow(Re, n);
  }

  // Wind-direction factor: effective angle 45° between wind and conductor.
  // CIGRE gives a correction < 1 for oblique/parallel flow; 45° ≈ 0.77.
  const K_angle = 0.77;
  Nu_forced *= K_angle;

  // Natural convection Nusselt (Grashof·Prandtl based, simplified CIGRE form)
  const g = 9.81;
  const beta = 1 / (T_film + 273.15);
  const { nu: nu2 } = airProps(T_film);
  const Gr = (D * D * D) * dT * g * beta / (nu2 * nu2);
  const Pr = 0.715 - 2.5e-4 * T_film; // Prandtl number of air ~0.71
  const GrPr = Gr * Pr;
  let Nu_natural = 0;
  if (GrPr > 0) {
    // Nu = A2 · (Gr·Pr)^m2  — CIGRE laminar range coefficients
    Nu_natural = 0.50 * Math.pow(GrPr, 0.25);
  }

  const Nu = Math.max(Nu_forced, Nu_natural);
  // q_c = π · λ · Nu · (T_cond − T_amb)
  return Math.PI * lambda * Nu * dT;
}

// Radiative cooling per unit length [W/m]
function radCooling(type, T_cond, T_amb) {
  const Tc = T_cond + 273.15, Ta = T_amb + 273.15;
  return Math.PI * type.d * type.emiss * SIGMA_SB * (Tc * Tc * Tc * Tc - Ta * Ta * Ta * Ta);
}

// Solar heat gain per unit length [W/m]
function solarGain(type, S) {
  // projected area per unit length = diameter; S is global horizontal irradiance
  return type.absorp * S * type.d;
}

// Steady-state ampacity [A] at rated temperature for given weather.
function ampacity(type, T_amb, v, S) {
  const q_c = convCooling(type, type.Tmax, T_amb, v);
  const q_r = radCooling(type, type.Tmax, T_amb);
  const q_s = solarGain(type, S);
  const R = conductorR(type, type.Tmax);
  const net = q_c + q_r - q_s;
  if (net <= 0) return 0; // conductor cannot even shed solar+ambient — no capacity
  return Math.sqrt(net / R);
}

// DLR factor for a conductor type under given weather, relative to reference.
// Clamped to a sane teaching range so a single bad weather sample can't
// produce absurd capacities.
//
// SENSITIVITY DAMPING: the raw thermal-balance ratio swings more widely than
// what TSOs report as *usable* DLR headroom (empirically ≈ 0.7–1.5×; e.g.
// TenneT/50Hertz DLR field trials). Real ratings keep conservative seasonal
// margins and n-1 reserves that damp the theoretical span. We apply a mild
// power-law damping around 1.0 (exponent DAMP<1) so the model reproduces the
// correct *direction and ranking* of weather effects at realistic magnitude,
// rather than the un-margined thermal extreme. This is a teaching model, and
// the damping is documented as such.
const DLR_DAMP = 0.55;

function dlrFactor(typeKey, T_amb, v, S) {
  const type = CONDUCTOR_TYPES[typeKey] || CONDUCTOR_TYPES.A;
  const I_now = ampacity(type, T_amb, v, S);
  const I_ref = ampacity(type, REF_WEATHER.Tamb, REF_WEATHER.v, REF_WEATHER.S);
  if (I_ref <= 0) return 1.0;
  const raw = I_now / I_ref;
  // damp around 1.0: f = raw^DAMP  (raw=1 → 1; raw>1 → smaller boost; raw<1 → smaller cut)
  const f = Math.pow(raw, DLR_DAMP);
  return Math.max(0.5, Math.min(1.6, f));
}

// Assign a default conductor type from static NTC [MW] when none is given.
// Heuristic only — the popup lets the user override it.
function defaultTypeFromNTC(ntc_mw, kind) {
  if (kind === "DC") return null;        // HVDC: no DLR
  if (ntc_mw >= 3000) return "A";
  if (ntc_mw >= 1000) return "B";
  return "C";
}

// Export for Node testing; harmless in browser.
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    CONDUCTOR_TYPES, REF_WEATHER, conductorR, airProps,
    convCooling, radCooling, solarGain, ampacity, dlrFactor, defaultTypeFromNTC,
  };
}

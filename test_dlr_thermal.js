const t = require("./dlr_thermal.js");
let pass = 0, fail = 0;
function ok(name, cond, extra="") {
  if (cond) { pass++; console.log(`  ✓ ${name}`); }
  else { fail++; console.log(`  ✗ ${name}  ${extra}`); }
}
function approx(a, b, tol) { return Math.abs(a - b) <= tol; }

console.log("── Reference point ──");
for (const k of ["A","B","C","D"]) {
  const f = t.dlrFactor(k, t.REF_WEATHER.Tamb, t.REF_WEATHER.v, t.REF_WEATHER.S);
  ok(`type ${k}: factor at reference weather ≈ 1.0`, approx(f, 1.0, 1e-9), `got ${f}`);
}

console.log("\n── Directional sanity (type A, 380 kV) ──");
const ref  = t.dlrFactor("A", 15, 3, 200);
const hot  = t.dlrFactor("A", 35, 1, 800);     // heatwave: hot, calm, sunny
const cold = t.dlrFactor("A", 0, 8, 50);       // winter storm: cold, windy, dark
ok("heatwave reduces capacity (<1)", hot < 1.0, `hot=${hot.toFixed(3)}`);
ok("cold+windy increases capacity (>1)", cold > 1.0, `cold=${cold.toFixed(3)}`);
ok("heatwave < reference < storm ordering", hot < ref && ref < cold,
   `${hot.toFixed(3)} < ${ref.toFixed(3)} < ${cold.toFixed(3)}`);

console.log("\n── Magnitude plausibility (TSO field trials: usable DLR ~0.7–1.5×) ──");
ok("heatwave factor in plausible band 0.65–0.95", hot >= 0.65 && hot <= 0.95, `hot=${hot.toFixed(3)}`);
ok("storm factor in plausible band 1.05–1.5", cold >= 1.05 && cold <= 1.5, `cold=${cold.toFixed(3)}`);

console.log("\n── Monotonicity ──");
// Colder ambient → more capacity (all else equal)
let mono = true, prev = -Infinity;
for (let T = 45; T >= -20; T -= 5) {
  const f = t.dlrFactor("A", T, 3, 200);
  if (f < prev - 1e-9) mono = false;
  prev = f;
}
ok("capacity monotonically ↑ as ambient temp ↓", mono);

// More wind → more capacity (all else equal), monotone up
mono = true; prev = -Infinity;
for (let v = 0; v <= 15; v += 1) {
  const f = t.dlrFactor("A", 25, v, 200);
  if (f < prev - 1e-9) mono = false;
  prev = f;
}
ok("capacity monotonically ↑ with wind speed", mono);

// More sun → less capacity (all else equal), monotone down
mono = true; prev = Infinity;
for (let S = 0; S <= 1000; S += 100) {
  const f = t.dlrFactor("A", 25, 3, S);
  if (f > prev + 1e-9) mono = false;
  prev = f;
}
ok("capacity monotonically ↓ with solar irradiance", mono);

console.log("\n── Underground cable (type D): no solar sensitivity ──");
const dDark  = t.dlrFactor("D", 25, 3, 0);
const dSunny = t.dlrFactor("D", 25, 3, 1000);
ok("cable factor identical with/without sun (α=0)", approx(dDark, dSunny, 1e-9),
   `${dDark.toFixed(3)} vs ${dSunny.toFixed(3)}`);

console.log("\n── Clamping ──");
const extremeHot = t.dlrFactor("A", 80, 0, 1000); // pathological
const extremeCold = t.dlrFactor("A", -40, 15, 0);
ok("extreme heat clamped ≥ 0.5", extremeHot >= 0.5, `got ${extremeHot.toFixed(3)}`);
ok("extreme cold clamped ≤ 1.6", extremeCold <= 1.6, `got ${extremeCold.toFixed(3)}`);

console.log("\n── Ampacity absolute sanity (type A ~ 380kV should carry ~kA) ──");
const I = t.ampacity(t.CONDUCTOR_TYPES.A, 15, 3, 200);
ok("reference ampacity in 800–3000 A range", I >= 800 && I <= 3000, `I=${Math.round(I)} A`);

console.log("\n── Default type assignment ──");
ok("NTC 4800 → A", t.defaultTypeFromNTC(4800, "AC") === "A");
ok("NTC 1500 → B", t.defaultTypeFromNTC(1500, "AC") === "B");
ok("NTC 500 → C",  t.defaultTypeFromNTC(500,  "AC") === "C");
ok("DC → null",    t.defaultTypeFromNTC(2000, "DC") === null);

console.log(`\n${fail===0?"✅":"❌"} ${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);

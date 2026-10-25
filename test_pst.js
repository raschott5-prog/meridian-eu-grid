/* Regression test for the DLR × A11-calibration interaction (Option A):
   the modelled DLR factor must never double-count weather that is already
   embedded in an A11-measured NTC. Runs against inline JS from the HTML. */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const store = {};
const dummy = { addEventListener(){}, style:{}, classList:{add(){},remove(){},toggle(){},contains:()=>false},
  appendChild(){}, innerHTML:"", textContent:"", value:"", dataset:{}, setAttribute(){}, getAttribute:()=>null,
  querySelector:()=>dummy, querySelectorAll:()=>[], getBoundingClientRect:()=>({width:800,height:600}) };
global.document = { getElementById:()=>dummy, addEventListener(){}, querySelectorAll:()=>[],
  createElementNS:()=>dummy, createElement:()=>dummy, body:dummy, documentElement:dummy, querySelector:()=>dummy };
global.window = global;
global.localStorage = { getItem:k=>store[k]||null, setItem:(k,v)=>{store[k]=v;}, removeItem:k=>{delete store[k];} };
global.navigator = { language:"en" };
global.requestAnimationFrame = ()=>{};
global.alert = ()=>{};
global.fetch = ()=>Promise.reject(new Error("no network"));
const html = fs.readFileSync(path.join(__dirname,"meridian_grid_viz.html"),"utf8");
vm.runInThisContext(html.match(/<script>([\s\S]*?)<\/script>/)[1]);

/* PST on DE–PL — mirrors test_pst.py; pstControl() must match
   GridModel._pst_control() (checked to the MW during development). */
let P=0,F=0;
function ok(n,c,x=""){ if(c){P++;console.log("  ✓ "+n);} else {F++;console.log("  ✗ "+n+"  "+x);} }
const depl=r=>r.flows.find(f=>f.a==="DE"&&f.b==="PL");
const clean=()=>{genShock={};loadShock={};lineDerate={};dcSet={};pstSet={};};

console.log("── PST (DE–PL) ──");
clean();
const r0=solve(),f0=depl(r0);
ok("Default neutral", f0.pst&&f0.pst.target===null&&f0.pst.shift===0);
pstSet["DE-PL"]=800;
ok("Holds +800 MW", Math.abs(depl(solve()).flow-800)<1, depl(solve()).flow);
clean(); genShock.DE={wind:15000};
const fw=depl(solve()).flow;
pstSet["DE-PL"]=f0.flow;
const rh=solve();
ok("Wind shock raises DE→PL without PST", fw>f0.flow+300, `${Math.round(f0.flow)} → ${Math.round(fw)}`);
ok("PST holds DE–PL during wind shock", Math.abs(depl(rh).flow-f0.flow)<1);
clean(); pstSet["DE-PL"]=-2500;
const fs_=depl(solve());
ok("Unreachable target saturates", fs_.pst.sat&&Math.abs(Math.abs(fs_.pst.shift)-3000)<1, fs_.pst.shift);
clean(); lineDerate["DE-PL"]=0; pstSet["DE-PL"]=800;
const fc=depl(solve());
ok("Cut line: no flow, no shift", Math.abs(fc.flow)<1e-6&&fc.pst.shift===0);
clean();
const L0=LINES.find(l=>l[0]==="DE"&&l[1]==="PL"); L0._pst=-700;
ok("Live target (l._pst) is used when no user override", Math.abs(depl(solve()).flow+700)<1);
delete L0._pst;

console.log(`\n  PST JS: ${P}/${P+F} passed`);
process.exit(F?1:0);

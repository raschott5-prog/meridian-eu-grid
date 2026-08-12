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

let P=0,F=0;
function ok(n,c,x=""){ if(c){P++;console.log("  ✓ "+n);} else {F++;console.log("  ✗ "+n+"  "+x);} }

const lFRDE = LINES.find(x=>x[0]==="FR"&&x[1]==="DE");
const kFRDE = LKEY(lFRDE);

console.log("── Option A: no weather double-counting ──");

// LIVE + calibrated → locked to 1.0
DLR_LIVE=true; lineWeather[kFRDE]={T:0,v:9,S:50};
lFRDE._ntc0=4800; lFRDE[3]=5200;
ok("LIVE + calibrated → 1.0", lineDLR(lFRDE)===1.0, lineDLR(lFRDE));

// LIVE + uncalibrated → per-line weather applies
delete lFRDE._ntc0; lFRDE[3]=4800;
ok("LIVE + uncalibrated (cold/windy) → >1", lineDLR(lFRDE)>1.05, lineDLR(lFRDE).toFixed(3));

// LIVE + uncalibrated + no per-line weather → neutral
delete lineWeather[kFRDE];
ok("LIVE + no weather sample → 1.0", lineDLR(lFRDE)===1.0);

// MANUAL at reference → 1.0 even if calibrated
DLR_LIVE=false; DLR.T=15; DLR.v=3; DLR.S=200;
lFRDE._ntc0=4800; lFRDE[3]=5200;
ok("MANUAL reference + calibrated → 1.0", Math.abs(lineDLR(lFRDE)-1.0)<1e-9);
ok("manualScenarioActive() false at reference", manualScenarioActive()===false);

// MANUAL what-if → applies even to calibrated (explicit override)
DLR.T=38; DLR.v=0.5; DLR.S=950;
ok("manualScenarioActive() true when moved", manualScenarioActive()===true);
ok("MANUAL what-if + calibrated → <1", lineDLR(lFRDE)<0.9, lineDLR(lFRDE).toFixed(3));
ok("what-if scales the calibrated base (5200)", baseNtc(lFRDE)===5200);

// HVDC never affected
const lDC = LINES.find(x=>x[4]==="DC");
DLR_LIVE=true;  ok("HVDC 1.0 in live mode", lineDLR(lDC)===1.0);
DLR_LIVE=false; DLR.T=38; ok("HVDC 1.0 in manual what-if", lineDLR(lDC)===1.0);

// cleanup
delete lFRDE._ntc0; lFRDE[3]=4800;

console.log("\n"+(F===0?"✅":"❌")+" "+P+" passed, "+F+" failed");
process.exit(F===0?0:1);

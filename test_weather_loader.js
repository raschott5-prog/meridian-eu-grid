/* Regression tests for the live-weather loader (parsing, wind correction,
   midpoints, caching, URL construction). Runs against the inline JS extracted
   from meridian_grid_viz.html with a minimal DOM + mock fetch. No network. */

const fs = require("fs");
const path = require("path");

// --- minimal browser stubs ---
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
global.CAPTURED_URL = "";
global.fetch = (url)=>{ global.CAPTURED_URL=url;
  const n=url.match(/latitude=([^&]+)/)[1].split(",").length;
  const arr=Array.from({length:n},(_,i)=>({current:{temperature_2m:20-i*0.01,wind_speed_10m:3,shortwave_radiation:200}}));
  return Promise.resolve({ok:true,status:200,json:()=>Promise.resolve(arr)});
};

// --- extract inline JS from the HTML and run it in global scope ---
const html = fs.readFileSync(path.join(__dirname,"meridian_grid_viz.html"),"utf8");
const js = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const vm = require("vm");
vm.runInThisContext(js);

let P=0,F=0;
function ok(n,c,x=""){ if(c){P++;console.log("  ✓ "+n);} else {F++;console.log("  ✗ "+n+"  "+x);} }

(async ()=>{
  console.log("── Wind height correction ──");
  const v30=windAtConductor(5);
  ok("10→30 m lift increases wind", v30>5, v30.toFixed(2));
  ok("magnitude = 3^0.14 ≈ 1.17×", Math.abs(v30/5-Math.pow(3,0.14))<1e-9);

  console.log("\n── Weather line list ──");
  const wl=acLinesForWeather();
  const nAC=LINES.filter(l=>l[4]==="AC").length;
  ok("one point per AC line, no DC", wl.length===nAC, wl.length+"/"+nAC);
  ok("all have key+lat+lon", wl.every(x=>x.key&&typeof x.lat==="number"&&typeof x.lon==="number"));

  console.log("\n── parseOpenMeteo ──");
  const multi=parseOpenMeteo([
    {current:{temperature_2m:30,wind_speed_10m:2,shortwave_radiation:800}},
    {current:{temperature_2m:15,wind_speed_10m:3}}, null ]);
  ok("multi-location array parsed", multi.length===3 && multi[0].T===30);
  ok("missing radiation → S=0", multi[1].S===0);
  ok("null location → null", multi[2]===null);
  const single=parseOpenMeteo({current:{temperature_2m:20,wind_speed_10m:4,shortwave_radiation:500}});
  ok("single-object form parsed", single.length===1 && single[0].T===20);

  console.log("\n── applyWeatherSamples ──");
  lineWeather={};
  const n=applyWeatherSamples([{key:"FR-DE"},{key:"AT-IT"}],[{T:30,v10:2,S:800},null]);
  ok("applies non-null, skips null", n===1 && !!lineWeather["FR-DE"] && !lineWeather["AT-IT"]);
  ok("stored wind is height-corrected", lineWeather["FR-DE"].v>2);

  console.log("\n── Cache ──");
  writeWeatherCache({"X-Y":{T:1,v:2,S:3}});
  const c=readWeatherCache();
  ok("cache round-trips", c && c.weather["X-Y"].T===1);

  console.log("\n── Full load flow (mock fetch) ──");
  const r=await loadWeather(true);
  ok("load ok, all AC lines populated", r.ok && r.n===nAC, r.n+"/"+nAC);
  ok("URL is Open-Meteo forecast", CAPTURED_URL.startsWith("https://api.open-meteo.com/v1/forecast?latitude="));
  ok("URL requests the 3 current vars", CAPTURED_URL.includes("current=temperature_2m,wind_speed_10m,shortwave_radiation"));
  ok("URL requests m/s wind unit", CAPTURED_URL.includes("wind_speed_unit=ms"));

  console.log("\n── DLR effect via live weather ──");
  lineWeather={}; DLR_LIVE=true; lineWeather["FR-DE"]={T:38,v:0.5,S:950};
  const lFRDE=LINES.find(x=>x[0]==="FR"&&x[1]==="DE");
  ok("live heat cuts FR-DE capacity", lineDLR(lFRDE)<0.9, lineDLR(lFRDE).toFixed(3));

  console.log("\n"+(F===0?"✅":"❌")+" "+P+" passed, "+F+" failed");
  process.exit(F===0?0:1);
})();

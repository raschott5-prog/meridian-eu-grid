# MERIDIAN Grid — Real-Data EU Power Grid Simulator

[![Tests](https://github.com/raschott5-prog/meridian-eu-grid/actions/workflows/tests.yml/badge.svg)](https://github.com/raschott5-prog/meridian-eu-grid/actions)
[![Live Snapshot](https://github.com/raschott5-prog/meridian-eu-grid/actions/workflows/snapshot.yml/badge.svg)](https://github.com/raschott5-prog/meridian-eu-grid/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **[→ Open the live simulator](https://raschott5-prog.github.io/meridian-eu-grid/)**
> — runs entirely in the browser, no installation required.

---

## Abstract

MERIDIAN Grid is an open, interactive DC load-flow simulator of the European
interconnected power system. It covers **40 bidding zones** across all four
synchronous areas (Continental Europe including the Baltic states, Nordic,
Great Britain, Ireland), **62 AC interconnectors** and **21 HVDC cables**,
fed by real-time generation and load data from the **ENTSO-E Transparency
Platform** (document types A75/A65) and calibrated against **physically
measured cross-border flows** (A11). The calibration pipeline fits zonal
susceptances iteratively, pins HVDC setpoints directly from measurement, and
raises NTC limits empirically wherever measured flows exceed assumed values —
achieving a mean residual error of approximately 6 MW at the base state.

The simulator is designed as both a **research tool** and an **educational
platform**. An integrated AI learning mode (supporting Anthropic Claude,
OpenAI GPT, and Google Gemini) explains the physical consequences of any
user intervention at beginner or advanced engineering level, using the actual
computed delta-flow data rather than generic descriptions. A built-in N-1
contingency analysis replicates the real-time security assessment that
Transmission System Operators (TSOs) run continuously.

The model is intentionally transparent about its own limitations: absolute
MW values are indicative rather than operational, and every zone running on
model defaults is visually flagged. The codebase is structured to serve as
a foundation for further research modules (market coupling, optimal power
flow, ML-based forecasting).

---

## Key Features

- **Real physics, not a toy**: zonal DC load flow solved via B-matrix / Gaussian elimination; distributed slack per synchronous island; Kirchhoff-correct AC redistribution; HVDC as fixed injections
- **Live ENTSO-E data**: automatic 30-minute snapshots via GitHub Actions; time-alignment across zones with up to 240-minute carry-forward for slow-reporting TSOs; MW-weighted completeness check guards against phantom deficits
- **A11 calibration**: susceptance fitting, HVDC pinning, empirical NTC correction — full fit report at `/api/grid/calibration`
- **Browser-native solver**: the JavaScript solver in `meridian_grid_viz.html` is numerically identical to the Python engine, enabling offline use via GitHub Pages
- **AI learning mode**: post-intervention explanation at beginner / advanced level; multi-provider (Anthropic / OpenAI / Google); key stored locally in the browser
- **N-1 stress test**: all 83 connections tested individually on the live-calibrated state; islanding and overload detected
- **Frequency simulation**: quasi-stationary Δf = −ΔP/λ per synchronous island, with real protection thresholds (FCR, UFLS, generator protection)
- **Bilingual UI**: English default, full German translation, preference saved in `localStorage`
- **Four test suites**: XML parsing, end-to-end with mocked API, real-data regression, calibration reconstruction

---

## Quick Start

### Browser (no installation)

Open [the live page](https://raschott5-prog.github.io/meridian-eu-grid/) or
open `meridian_grid_viz.html` directly. The simulator runs on model defaults
without any API key.

### Local server with live ENTSO-E data

```bash
git clone https://github.com/raschott5-prog/meridian-eu-grid
cd meridian-eu-grid
pip install -r requirements.txt
cp .env.example .env        # add your free ENTSO-E key (see below)

python3 meridian_grid_live.py --check   # verify connectivity + A11 calibration
python3 meridian_grid_live.py           # → http://localhost:5002
```

**ENTSO-E API key** (free): create an account at
[transparency.entsoe.eu](https://transparency.entsoe.eu), then email
`transparency@entsoe.eu` with subject "Restful API access".

### GitHub Pages — automatic live updates

1. **Add secret**: Settings → Secrets → Actions → `ENTSOE_KEY` = your key
2. **Enable Pages**: Settings → Pages → Source: **GitHub Actions**
3. **Trigger first run**: Actions → "Grid-Snapshot → GitHub Pages" → *Run workflow*

The snapshot workflow runs every 30 minutes. If ENTSO-E returns insufficient
data the job intentionally fails — the last good snapshot stays live rather
than silently falling back to defaults.

---

## Model Description

### Topology

| Element | Count |
|---|---|
| Bidding zones | 40 |
| AC interconnectors | 62 |
| HVDC cables | 21 |
| Synchronous areas | 4 (CE, Nordic, GB, Ireland) |

Zones not included: UA/MD (data gaps), LU (aggregated into DE-LU), MT/CY
(island systems), TR (outside EU data space). GB, MK, and AL run on model
defaults with explicit visual flagging due to systematic reporting gaps.

### DC Load Flow

The solver implements a standard zonal DC load flow:

1. HVDC cables are treated as fixed power injections (setpoint or A11-pinned value)
2. AC interconnector susceptances form the nodal B-matrix
3. Voltage angles θ are solved per synchronous island via Gaussian elimination
4. Power flows: `P_ij = B_ij · (θ_i − θ_j)`
5. Slack is distributed proportionally to installed generation capacity per island

Cutting an AC line to zero removes it from the B-matrix and may cause
island formation — each island re-balances independently, which drives the
frequency simulation.

### Frequency Simulation

Quasi-stationary frequency deviation after primary regulation:

```
Δf = −ΔP_reg / λ    [Hz]
```

Frequency response characteristic λ per synchronous area:

| Area | λ |
|---|---|
| Continental Europe | 15 GW/Hz (ENTSO-E: 3 GW FCR for 200 mHz) |
| Nordic | 6 GW/Hz |
| Great Britain | 2.5 GW/Hz |
| Ireland | 0.45 GW/Hz |

Islands inherit λ proportionally to their generation share. Protection
thresholds match real system values: 49.8 Hz (FCR limit), 49.0 Hz (UFLS),
47.5 Hz (collapse), 50.2 Hz (PV disconnection), 51.5 Hz (generator
protection).

### A11 Calibration

Every data refresh applies a three-step calibration against A11 measured
physical flows:

1. **HVDC pinning** — cable setpoints replaced directly by A11 measurement
2. **Susceptance fitting** — AC scaling factors fitted iteratively (damped,
   bounded 0.25–4.0) until modelled flows match A11; fit state is
   persistent across refreshes
3. **Zone balance correction** — residual between A11-implied net position
   and A75/A65 balance; captures sub-threshold generation, neighbours
   outside the model, and control reserves (mathematically inseparable
   from A11 alone — reported honestly as a single figure)

**Empirical NTC correction**: where a measured A11 flow exceeds the assumed
NTC, the limit is raised to `max(|A11|) × 1.10`. Limits are never lowered
by a single measurement.

Full fit report: `GET /api/grid/calibration`

---

## Repository Structure

| File | Purpose |
|---|---|
| `meridian_grid.py` | Physics core: topology, DC load flow, scenario engine |
| `meridian_grid_live.py` | ENTSO-E pipeline + Flask endpoints + A11 calibration |
| `snapshot.py` | Static GitHub Pages bundle (called by Actions cron) |
| `meridian_grid_viz.html` | Interactive map + AI learning mode (runs standalone) |
| `test_grid_live.py` | Unit tests: XML parsing, pumped storage, time alignment |
| `test_e2e.py` | End-to-end with mocked ENTSO-E API |
| `test_realdata_regression.py` | Reproduces observed ENTSO-E data edge cases |
| `test_calibration.py` | A11 calibration reconstructs a known ground truth |

---

## API Reference

```
GET  /api/grid              Load flow on live-calibrated state
GET  /api/grid/n1           N-1 contingency analysis
GET  /api/grid/calibration  Full A11 fit report
POST /api/grid/refresh      Force immediate data refresh
```

Optional shock parameters (JSON-encoded query string):

```
/api/grid?shocks={"gen":{"FR":{"nuclear":-20000}}}
/api/grid?shocks={"load":{"PL":5000}}
/api/grid?shocks={"line":{"DE-AT":0.5}}
```

---

## Tests

```bash
python3 test_grid_live.py            # parsing, state, endpoints
python3 test_e2e.py                  # full pipeline, mocked API
python3 test_realdata_regression.py  # real-data edge cases
python3 test_calibration.py          # calibration ground truth
```

All suites run without network access. CI runs all four on every push.

---

## Educational Scenarios

| Scenario | Teaching point |
|---|---|
| FR −20 GW Nuclear | Load flow redistribution across CE; frequency response |
| DE +30 GW Wind | Surplus injection; loop flows toward NL/BE/FR |
| NordLink + NSL Off | HVDC does not couple frequency; Nordic/CE decouple |
| Iberian Split | Peninsula islanding; frequency divergence |
| Dark Doldrums | Wind/solar drought + demand surge across NW Europe |
| Baltic Islanding | Synchronous area separation; island frequency |
| **⚡ Transit: FR↓ + DE Wind↑** | **Unscheduled loop flow**: power transits *through* France toward Spain even as France becomes a net importer. Voltage angle gradient θ_DE >> θ_FR > θ_ES — Kirchhoff follows the gradient, not trading intent. Replicates the structural conditions documented in ENTSO-E system adequacy reports and the April 2025 Iberian event. |

---

## Known Limitations

| Limitation | Detail |
|---|---|
| **Absolute MW values are indicative** | Susceptances are calibrated estimates, not measured reactances. Flow directions and redistribution patterns are qualitatively correct; exact MW values are not. |
| **Zonal aggregation** | Each bidding zone is a single bus. Within-zone congestion, transformer taps, and reactive power are not modelled. |
| **A75 reporting threshold** | Only plants above ~100 MW are reported. Distributed PV and small-scale generation are systematically underrepresented. |
| **Open balance** | Flows to/from GB (post-Brexit), Ukraine, Morocco, and non-modelled Balkan neighbours are not closed. The residual is reported as `external_balance_mw` and absorbed by the distributed slack — never silently discarded. |
| **Static snapshot** | Single operating point, not a dynamic simulation. Transient stability, voltage control, and relay behaviour are outside scope. |
| **API throttling** | ~250 requests per refresh approaches the ENTSO-E rate limit. Retry with backoff, shuffled fetch order, and persistent calibration state mitigate this; degraded data quality is flagged in the UI. |

---

## Potential Research Extensions

MERIDIAN Grid is designed as a platform for further modules:

| Module | Description |
|---|---|
| **MERIDIAN Market** | Day-ahead price coupling, zonal congestion rent |
| **MERIDIAN Dispatch** | Optimal power flow, redispatch cost optimisation |
| **MERIDIAN Forecast** | ML-based generation and load forecasting on ENTSO-E time series |
| **MERIDIAN Transient** | Dynamic frequency simulation (swing equations per zone) |

Each module can interface with the existing calibrated base state via `/api/grid`.

---

## Citing This Work

```bibtex
@software{meridian_grid,
  author  = {raschott5-prog},
  title   = {{MERIDIAN Grid}: Real-Data EU Power Grid Simulator},
  year    = {2025},
  url     = {https://github.com/raschott5-prog/meridian-eu-grid},
  version = {2.0}
}
```

See also [`CITATION.cff`](CITATION.cff) for full citation metadata.

---

## Requirements

```bash
pip install -r requirements.txt   # numpy, flask, requests
```

Python 3.9+

---

## License

MIT — see [LICENSE](LICENSE). Data from the ENTSO-E Transparency Platform is
used under their terms of service with attribution. This model is an
analytical and educational tool — not operational grid management software.

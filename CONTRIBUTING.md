# Contributing to MERIDIAN Grid

Thank you for your interest in contributing. MERIDIAN Grid is an open
educational and research tool — contributions of any size are welcome,
from fixing a typo to adding a new scenario or extending the model.

---

## Ways to Contribute

### Bug Reports
Open an issue and include:
- What you did (steps to reproduce)
- What you expected to happen
- What actually happened
- Browser / Python version if relevant

### Topology & Data Corrections
The grid topology (zone coordinates, line susceptances, NTC values) is
maintained in `meridian_grid.py` and mirrored in `meridian_grid_viz.html`.
If you spot an error or have better reference values, open an issue or
pull request with a source reference (ENTSO-E TYNDP, ENTSOE-E SO&AF,
published TSO data).

### New Educational Scenarios
Scenarios live in the `preset()` function in `meridian_grid_viz.html`
and the `STRINGS` dictionary (EN + DE). A good scenario:
- Demonstrates a real, documented grid phenomenon
- Is reproducible from model defaults (no live data required)
- Has a clear teaching point that fits the AI learning mode explanation

### Model Extensions
See the **Potential Research Extensions** section in the README for
known gaps. If you are implementing one of these as a student project
or thesis, please open an issue first — we can make sure the API
interface stays compatible.

### Translations
The UI supports EN and DE via the `STRINGS` dictionary in
`meridian_grid_viz.html`. Adding a new language means adding a new key
to that object and wiring it to a language button.

---

## Development Setup

```bash
git clone https://github.com/raschott5-prog/meridian-eu-grid
cd meridian-eu-grid
pip install -r requirements.txt

# Run all tests (no network access required)
python3 test_grid_live.py
python3 test_e2e.py
python3 test_realdata_regression.py
python3 test_calibration.py

# Run with live data (requires ENTSO-E key in .env)
cp .env.example .env
python3 meridian_grid_live.py --check
python3 meridian_grid_live.py
```

The browser simulator (`meridian_grid_viz.html`) can be opened directly
without a server — the JS solver is numerically identical to the Python
engine and runs on model defaults.

---

## Code Structure

| Layer | File | Language |
|---|---|---|
| Physics core | `meridian_grid.py` | Python |
| Live data + API | `meridian_grid_live.py` | Python |
| GitHub Pages bundle | `snapshot.py` | Python |
| Interactive UI + JS solver | `meridian_grid_viz.html` | HTML/JS |

**Key invariant:** the JavaScript solver in `meridian_grid_viz.html`
must remain numerically identical to the Python solver in
`meridian_grid.py`. If you change the physics (susceptance fitting,
slack distribution, island detection), update both.

---

## Pull Request Guidelines

- One logical change per PR
- If you change physics: add or update a test in the relevant test file
- If you change the JS solver: verify it still matches the Python output
  on at least one scenario (the test suite covers this automatically)
- Keep commit messages short and specific:
  `fix: animation direction for negative AC flows`
  `feat: add Alpine pumped-storage scenario`
  `docs: translate hint text to French`

---

## Academic Use

If you use MERIDIAN Grid in a thesis, paper, or course, please cite it
using the metadata in `CITATION.cff` (GitHub shows a "Cite this
repository" button automatically). We would also appreciate a note in
the acknowledgements — it helps justify continued development.

If you are building a MERIDIAN extension module (Market, Dispatch,
Forecast, Transient) as a research project, we are happy to link your
repository from this README. Open an issue with a short description.

---

## Code of Conduct

Be constructive. This is a technical project — disagreements about
model assumptions or implementation choices are expected and welcome.
Keep discussion focused on the physics, the data, and the code.

---

## License

By contributing, you agree that your contributions will be licensed
under the same [MIT License](LICENSE) as the rest of the project.

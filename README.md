# Bracket FEM Agent

An AI agent that accepts a plain-text bracket design brief, builds a parametric 3D L-bracket in FreeCAD, meshes it with Gmsh, solves it with CalculiX, and iterates the geometry to simultaneously:

- Stay below a maximum von Mises stress limit (Sy / 1.5)
- Maintain a minimum factor of safety (≥ 1.5)
- Keep tip deflection under a maximum (5 mm default)
- Minimise total bracket mass
- Converge in as few iterations as possible (hard cap: 10)

> Scope is limited to L-bracket geometries. No generalisation to arbitrary CAD shapes until the bracket pipeline is proven end-to-end.

---

## How It Works

```
plain-text brief
      │
      ▼
 parse_brief()       → params dict + constraints dict
      │
      ▼  (repeat up to 10×)
 create_geometry()   → geometry.step       (FreeCAD headless)
 generate_mesh()     → mesh.inp            (Gmsh, C3D10 quad tets)
 render_mesh()       → render.png          (4-view mesh visualisation)
 write_inp()         → analysis.inp        (CalculiX input file)
 run_simulation()    → analysis.frd/.dat   (CalculiX static solve)
 render_results()    → results_render.png  (stress + displacement maps)
 parse_frd/dat()     → metrics dict        (stress, displacement)
 evaluate_constraints() → pass/fail + violations
      │
      ├─ PASS → generate_report() → stop
      └─ FAIL → propose_params()  → next iteration
```

Best-result tracking: a passing design always beats any failing design; among designs of the same pass/fail status, the lighter one wins.

The optimizer adjusts `thickness` and `fillet_radius` first (cheapest changes). `web_height` is increased only for displacement violations. `thickness` is reduced when the only active constraint is mass.

---

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.10+ | Runtime |
| FreeCAD | 0.21+ | Headless CAD (`FreeCADCmd` must be on PATH) |
| Gmsh | 4.11+ | Meshing Python API |
| CalculiX | 2.21+ | FEM solver (`ccx` must be on PATH) |
| matplotlib | any | Mesh + FEA renders (optional — renders skipped if absent) |

```bash
pip install gmsh matplotlib
```

---

## Usage

```bash
python pipeline.py brief.txt
```

### Input format

```
Bracket dimensions:
  flange_width:  80 mm
  flange_height: 60 mm
  web_height:    100 mm
  thickness:     6 mm
  fillet_radius: 4 mm

Material: structural steel
  E:    200 GPa
  nu:   0.3
  rho:  7850 kg/m³
  Sy:   250 MPa

Load:
  type:      point_force
  location:  tip of flange
  magnitude: 2000 N
  direction: -Z

Boundary conditions:
  fixed face: web back face (all DOF constrained)
```

Units accepted: `mm`, `m`, `GPa`, `MPa`, `Pa`, `kN`, `N`, `kg/m³`, `kg`. All values are converted to SI internally.

An optional mass budget may be appended anywhere in the brief:

    max_mass_kg: 0.05 kg

When present, `parse_brief()` wires this value into the constraint evaluator
and the report will show it as an active constraint.

### Output

Each iteration writes to `runs/iter_NNN/` (immutable after creation):

```
runs/iter_001/
├── params.json          # Geometry parameters used this iteration
├── geometry.step        # STEP file from FreeCAD
├── mesh.inp             # Gmsh mesh (C3D10 quadratic tetrahedra)
├── analysis.inp         # Full CalculiX input deck
├── analysis.frd         # CalculiX displacement + stress results
├── analysis.dat         # Reaction forces + strain energy
├── render.png           # 4-view mesh render (isometric, front, side, top)
├── results_render.png   # 4-view FEA results (von Mises stress + displacement)
└── summary.md           # Human-readable iteration report with embedded images
```

The agent stops when all constraints pass or after 10 iterations, returning the lightest passing design found (or the best failing design if no pass was achieved).

---

## Project Structure

```
bracket-agent/
├── pipeline.py          # Orchestrator: parse_brief() + run() loop
├── constraints.py       # CONSTRAINTS dict + evaluate_constraints()
├── optimizer.py         # propose_params(): geometry update strategy
├── tools/
│   ├── geometry.py      # create_geometry() / modify_geometry() via FreeCADCmd
│   ├── mesh.py          # generate_mesh() via Gmsh Python API
│   ├── calculix.py      # write_inp() + run_simulation() (ccx subprocess)
│   ├── results.py       # parse_frd() / parse_frd_nodal() / parse_dat()
│   ├── report.py        # generate_report() → summary.md
│   └── render.py        # render_mesh() + render_results() → PNG
├── runs/                # Per-iteration output (git-ignored)
└── tests/
    └── test_bracket.py  # 20 tests: pure unit, geometry, mesh, calculix, integration
```

---

## Geometry Parameters

All dimensions are metres (SI) internally. Input accepted in mm and converted on parse.

| Parameter | Description | Optimizer range |
|-----------|-------------|-----------------|
| `flange_width` | Horizontal arm width | 0.04 – 0.20 m |
| `flange_height` | Horizontal arm out-of-plane depth | 0.03 – 0.15 m |
| `web_height` | Vertical wall height | 0.05 – 0.25 m |
| `thickness` | Uniform wall thickness | 0.003 – 0.020 m |
| `fillet_radius` | Interior corner fillet radius | 0.002 – 0.015 m |

Constraint always enforced: `fillet_radius ≤ thickness × 0.45`.

---

## Constraints

| Constraint | Default | Notes |
|------------|---------|-------|
| Max von Mises stress | Sy / 1.5 | 166.7 MPa for 250 MPa steel |
| Min factor of safety | 1.5 | FOS = Sy / max_vm |
| Max tip deflection | 5 mm | Absolute displacement magnitude |
| Max mass | None | Set from brief; unconstrained if omitted |

Constraint limits are read from the brief and passed through the full pipeline to `generate_report`, so the summary always shows the actual limits used — not hardcoded defaults.

---

## Optimizer Strategy

| Violation type | Action |
|---------------|--------|
| Stress or FOS | `thickness × 1.10`, `fillet_radius × 1.20` |
| Displacement + stress | Above, plus `web_height × 1.05` |
| Displacement only | `web_height × 1.10` (falls back to `thickness × 1.10` at bound) |
| Mass only | `thickness × 0.95` |

All parameters are clamped to their bounds after update; then `fillet_radius ≤ thickness × 0.45` is enforced (geometric constraint takes priority over lower bound).

---

## Running Tests

Tests that require external tools are decorated with both a `@pytest.mark` and a `@pytest.mark.skipif`. The mark gives you explicit control via `-m`; the `skipif` auto-skips when the tool is absent.

**Pure unit tests** — no external tools:

```bash
python -m pytest tests/test_bracket.py -v -m "not requires_freecadcmd and not requires_gmsh and not requires_ccx"
```

**gmsh only:**

```bash
python -m pytest tests/test_bracket.py -v -m "requires_gmsh and not requires_freecadcmd and not requires_ccx"
```

**ccx only** (no FreeCADCmd or gmsh):

```bash
python -m pytest tests/test_bracket.py -v -m "requires_ccx and not requires_freecadcmd and not requires_gmsh"
```

**FreeCADCmd only** (no ccx or gmsh):

```bash
python -m pytest tests/test_bracket.py -v -m "requires_freecadcmd and not requires_ccx and not requires_gmsh"
```

**Everything** (tools not present are still auto-skipped via `skipif`):

```bash
python -m pytest tests/test_bracket.py -v
```

---

### Smoke / Integration Tests (`tests/test_smoke.py`)

Six end-to-end scenarios that exercise the full `pipeline.run()` loop. All
require FreeCADCmd + gmsh + ccx and are auto-skipped when any tool is absent.

**Run all smoke tests:**

```bash
python -m pytest tests/test_smoke.py -v
```

**Run only the fast single-iteration scenarios (S1 + S6):**

```bash
python -m pytest tests/test_smoke.py -v -k "easy_pass or artefact"
```

**Import-only check (no external tools needed):**

```bash
python -c "import tests.test_smoke"
```

| Test | Scenario | Key check |
|------|----------|-----------|
| S1 `test_s1_easy_pass` | Oversized bracket, 500 N | Passes on iter 1, FOS ≥ 1.5 |
| S2 `test_s2_forced_iteration` | Nominal bracket, 5000 N | At least 2 iterations run |
| S3 `test_s3_stagnation_upper_bounds` | 50 000 N (impossible) | No crash, `pass is False` |
| S4 `test_s4_displacement_dominated` | Slender bracket, 1000 N | Displacement violation fires; web_height non-decreasing |
| S5 `test_s5_mass_constrained` | Nominal bracket, 500 N | Pipeline runs, mass > 0 |
| S6 `test_s6_artefact_completeness` | Oversized bracket, 500 N | All output files present and non-empty |

> Expected runtime: ~2–5 min per scenario. S3 may take longer if all 10
> iterations run.

---

Marks defined in `conftest.py`:

| Mark | Used by |
|------|---------|
| `requires_freecadcmd` | geometry creation/modification, mesh-from-bracket, full pipeline |
| `requires_gmsh` | gmsh-only mesh, mesh-from-bracket, full pipeline |
| `requires_ccx` | minimal simulation, full pipeline |

**Import smoke check:**

```bash
python -c "import pipeline, constraints, optimizer; from tools import geometry, mesh, calculix, results, report, render"
```

---

## References

- [FreeCAD Python scripting](https://wiki.freecad.org/Python_scripting_tutorial)
- [Gmsh Python API](https://gmsh.info/doc/texinfo/gmsh.html)
- [CalculiX manual](http://www.dhondt.de/ccx_2.21.pdf)
- [.frd format spec](http://www.dhondt.de/cgx_2.21.pdf) (Section 4)

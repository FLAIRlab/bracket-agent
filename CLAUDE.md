# Bracket FEM Agent — CLAUDE.md

## Project Overview
An AI agent that takes a bracket dimension + load specification in plain text,
creates a parametric 3D bracket in FreeCAD, meshes it with Gmsh, runs a static
FEM analysis in CalculiX, and iterates geometry to simultaneously:
  - Meet a maximum von Mises stress limit (Sy / 1.5)
  - Maintain a minimum factor of safety (≥ 1.5)
  - Keep tip deflection below a maximum (5 mm default)
  - Minimize total mass
  - Converge in as few iterations as possible

Scope is intentionally limited to bracket geometries. No generalization to
arbitrary CAD shapes until the bracket pipeline is proven end-to-end.

---

## Tech Stack
- **CAD**:        FreeCAD headless (`FreeCADCmd`) + FreeCAD Python API
- **Meshing**:    Gmsh Python API (`gmsh`)
- **Solver**:     CalculiX (`ccx` CLI)
- **Parsing**:    Custom `.frd` block parser in `tools/results.py`
- **Rendering**:  matplotlib (Agg backend, no display needed)
- **Language**:   Python 3.10+
- **Runner**:     Claude Code (CLI)

---

## Project Structure
```
bracket-agent/
├── CLAUDE.md
├── brief.txt            # Example input brief
├── pipeline.py          # Main agent loop (parse_brief + run)
├── constraints.py       # Constraint definitions + evaluator
├── optimizer.py         # Parameter update strategy between iterations
├── tools/
│   ├── geometry.py      # Parametric bracket creation via FreeCADCmd
│   ├── mesh.py          # Gmsh meshing (STEP → C3D10 mesh.inp)
│   ├── calculix.py      # .inp writer + ccx runner
│   ├── results.py       # .frd / .dat parser (fixed-width block reader)
│   ├── report.py        # Per-iteration summary.md generator
│   └── render.py        # Mesh + FEA result renders (matplotlib, Agg)
├── runs/
│   └── iter_001/        # One folder per iteration (immutable after creation)
│       ├── params.json
│       ├── geometry.step
│       ├── mesh.inp
│       ├── analysis.inp
│       ├── analysis.frd
│       ├── analysis.dat
│       ├── render.png          # 4-view mesh render
│       ├── results_render.png  # 4-view FEA results render (stress + displacement)
│       └── summary.md
└── tests/
    └── test_bracket.py  # 20 tests across all pipeline stages
```

---

## Primary Input Format
The agent accepts a plain-text brief. Example:

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

The agent parses this into a `params` dict and a `constraints` dict before
entering the pipeline loop. Missing `rho` or `Sy` trigger a WARNING and fall
back to 7850 kg/m³ and 250 MPa respectively.

---

## Bracket Geometry Parameters
All dims in metres internally. Input accepted in mm and converted on parse.

| Key             | Description                        | Optimizer range |
|-----------------|------------------------------------|-----------------|
| `flange_width`  | Horizontal arm width               | 0.04 – 0.20 m   |
| `flange_height` | Horizontal arm out-of-plane depth  | 0.03 – 0.15 m   |
| `web_height`    | Vertical wall height               | 0.05 – 0.25 m   |
| `thickness`     | Uniform wall thickness             | 0.003 – 0.020 m |
| `fillet_radius` | Interior corner fillet radius      | 0.002 – 0.015 m |

Geometric constraint always enforced: `fillet_radius ≤ thickness × 0.45`.
This is applied after clamping to `PARAM_BOUNDS`; the geometric limit takes
priority over the lower bound when they conflict (very thin sections).

---

## Core Agent Loop

```
INPUT: plain-text brief
   ↓
parse_brief(text) → params dict + constraints dict
   ↓
┌─────────────────────────────────────────────────────────┐
│  iter N                                                 │
│  1. create_geometry(params)     → geometry.step         │
│  2. generate_mesh(step, qual)   → mesh.inp              │
│  2a. render_mesh(mesh)          → render.png            │
│  3. write_inp(mesh, loads, BC)  → analysis.inp          │
│  4. run_simulation(inp)         → analysis.frd/.dat     │
│  4a. render_results(mesh, frd)  → results_render.png    │
│  5. parse_frd/dat(results)      → metrics dict          │
│  6. evaluate_constraints(metrics, constraints)          │
│       → { pass, violations, mass_kg, fos }             │
│  7. generate_report(...)        → summary.md            │
│  8. if pass  → STOP (return lightest passing design)    │
│     if fail  → propose_params(violations) → loop       │
└─────────────────────────────────────────────────────────┘
   ↓
OUTPUT: best params dict + final eval dict
```

Best-result tracking: a passing design always beats any failing design;
among designs of the same pass/fail status, the lighter one wins.
Max iterations: **10** (hard limit — log warning, return best so far).

---

## Tool Contracts

### tools/geometry.py
```python
create_geometry(params: dict, output_dir: Path, apply_fillet: bool = True) -> Path
# Runs FreeCADCmd, returns path to geometry.step
# FreeCAD works in mm internally; script scales to SI metres before exportStep
# Raises GeometryError if FreeCAD exits non-zero or STEP missing

modify_geometry(step_path: Path, deltas: dict, output_dir: Path) -> Path
# Loads params.json beside step_path, merges deltas, calls create_geometry
```

### tools/mesh.py
```python
generate_mesh(step_path: Path, output_dir: Path, quality: str = "medium") -> Path
# Runs Gmsh Python API, returns path to mesh.inp
# quality: "coarse" (lc=10mm) | "medium" (lc=5mm) | "fine" (lc=2mm)
# Element type: C3D10 (quadratic tet); surface elements (CPS6 etc.) also written
# Warns if quality string is unrecognised (defaults to medium)
```

### tools/calculix.py
```python
write_inp(mesh_path: Path, loads: dict, bcs: dict,
          material: dict, output_dir: Path) -> Path
# Assembles full analysis.inp from mesh + material + BCs
# Only C3D* elements are included in EALL; surface/edge elements dropped
# Fixed face: nodes with x ≈ 0 (tolerance 1e-6; 1% x-range fallback with WARNING)
# Tip node: closest node to (flange_width, fh/2, web_height - thickness/2)

run_simulation(inp_path: Path) -> tuple[Path, Path]
# Shells out: ccx -i <stem>  (cwd = inp_path.parent)
# Returns (frd_path, dat_path); raises SimulationError if ccx fails
```

### tools/results.py
```python
parse_frd(frd_path: Path) -> dict
# Returns: { max_von_mises_pa, max_displacement_m, node_count }
# .frd format: ' -4' block headers, ' -5' component records,
#              ' -1' nodal values (I10 node_id + E12.5 values at pos 13)

parse_frd_nodal(frd_path: Path) -> tuple[dict, dict]
# Returns: (disp_mag, von_mises) — both are {node_id: float}
# Used by render_results() for per-node colouring

parse_dat(dat_path: Path) -> dict
# Returns: { reaction_forces_n: {fx, fy, fz}, strain_energy_j }
```

### tools/render.py
```python
render_mesh(mesh_path: Path, output_dir: Path) -> Path | None
# 2×2 panel PNG: Isometric, Front (XZ), Side (YZ), Top (XY)
# Surface-coloured by Z-height (blue gradient); scatter fallback if no CPS6 triangles
# Returns None gracefully if matplotlib is not installed

render_results(mesh_path: Path, frd_path: Path, output_dir: Path) -> Path | None
# 2×2 panel PNG: Von Mises stress + displacement magnitude, isometric + front
# Coloured by per-node FEA scalar (jet for stress, viridis for displacement)
```

### tools/report.py
```python
generate_report(iteration, params, metrics, eval_result, output_dir,
                render_path=None, results_render_path=None,
                constraints=None) -> Path
# Writes summary.md with constraint table using actual constraint limits
# Embeds render.png and results_render.png if paths are provided and exist
```

### constraints.py
```python
CONSTRAINTS = {
    "max_von_mises_pa":     250e6 / 1.5,  # 166.67 MPa (Sy/1.5 for 250 MPa steel)
    "min_factor_of_safety": 1.5,
    "max_displacement_m":   0.005,         # 5 mm
    "max_mass_kg":          None,          # Set from brief; None = unconstrained
}

evaluate_constraints(metrics: dict, constraints: dict) -> dict
# Returns: { pass, violations: list[str], mass_kg, fos, stress_utilisation }
# Violation string prefixes: "stress:", "fos:", "displacement:", "mass:"
# Mass computed analytically from geometry params (no double-counting at corner)
```

### optimizer.py
```python
PARAM_BOUNDS = {
    "flange_width":  (0.04, 0.20),
    "flange_height": (0.03, 0.15),
    "web_height":    (0.05, 0.25),
    "thickness":     (0.003, 0.020),
    "fillet_radius": (0.002, 0.015),
}

propose_params(current_params: dict, violations: list, iteration: int) -> dict
# stress/fos violation  → thickness *= 1.10, fillet_radius *= 1.20
# displacement only     → web_height *= 1.10 (fallback: thickness *= 1.10)
# displacement + stress → also web_height *= 1.05
# mass only             → thickness *= 0.95
# Clamp to PARAM_BOUNDS first, then enforce fillet_radius <= thickness * 0.45
```

---

## CalculiX .inp Conventions
- Step type:  `*STATIC` (linear static only)
- Elements:   `C3D10` quadratic tetrahedra
- BCs:        `*BOUNDARY`, DOF 1–6 (`FIXED, 1, 6`) — NOT the ENCASTRE keyword
- Load:       `*CLOAD` for point force; direction -Z = DOF 3 with negative magnitude
- Output:
  ```
  *NODE PRINT, NSET=NALL      → .dat (reaction forces)
  U
  *EL PRINT, ELSET=EALL       → .dat (stresses)
  S
  *NODE FILE                  → .frd (displacements)
  U
  *EL FILE                    → .frd (stress tensor)
  S
  ```

## .frd File Format (real CalculiX output)
Record layout (all ASCII, latin-1 encoding):
- ` -4  TYPENAME  ncomp  1` — result block header (DISP or STRESS)
- ` -5  COMPNAME  ...`      — component sub-record (D1/D2/D3 or SXX/SYY/…)
- ` -1  NNNNNNNNNN V1V2V3…` — nodal result: I10 node_id at pos 3, E12.5 values at pos 13+
- ` -3`                     — end of block

Von Mises computed from 6 stress components: `sqrt(0.5*((sxx-syy)²+(syy-szz)²+(szz-sxx)²+6*(sxy²+sxz²+syz²)))`

---

## Coding Conventions
- `pathlib.Path` everywhere — no raw string paths
- SI units internally: Pa, N, m, kg — convert mm input on ingest only
- Each iteration writes to `runs/iter_{n:03d}/` and is never modified after
- All tools are pure functions — no module-level state
- Use Python `logging` at INFO for each pipeline step, DEBUG for internals
- `GeometryError`, `MeshError`, `SimulationError` — raise with full stderr
- `params.json` always written alongside geometry.step for traceability

---

## What NOT To Do
- Do NOT use `FreeCAD.Gui` or any GUI import — headless only (`QT_QPA_PLATFORM=offscreen`)
- Do NOT use FreeCAD's built-in FEM mesher — Gmsh only
- Do NOT generalize to non-bracket shapes in this codebase
- Do NOT modify any file inside `runs/` after it is first written
- Do NOT run `ccx` if mesh.inp has zero nodes or zero elements — validate first
- Do NOT parse `.frd` with regex — use the structured block reader in results.py
- Do NOT exceed `max_iter = 10` — log a warning and return best result so far
- Do NOT use textwrap.dedent on f-strings that substitute multi-line blocks (geometry.py lesson)

---

## Reference Docs
- FreeCAD scripting:  https://wiki.freecad.org/Python_scripting_tutorial
- Gmsh Python API:   https://gmsh.info/doc/texinfo/gmsh.html
- CalculiX manual:   http://www.dhondt.de/ccx_2.21.pdf
- .frd format:       http://www.dhondt.de/cgx_2.21.pdf  (Section 4)

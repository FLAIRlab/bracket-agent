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

Supported bracket types (see `bracket_types/` and `docs/coordinate_convention.md`):
  - **L-bracket** (default): L-shaped web + flange, fixed at x≈0
  - **T-bracket**: inverted T with central web, fixed at z≈0
  - **U-bracket** (channel): two walls + base plate, fixed at z≈0

Scope is intentionally limited to bracket geometries. No generalization to
arbitrary CAD shapes until the bracket pipeline is proven end-to-end.

---

## Tech Stack
- **CAD**:        FreeCAD headless (`FreeCADCmd`) + FreeCAD Python API
- **Meshing**:    Gmsh Python API (`gmsh`)
- **Solver**:     CalculiX (`ccx` CLI)
- **Parsing**:    Custom `.frd` block parser in `tools/results.py`
- **Rendering**:  matplotlib (Agg backend, no display needed)
- **LLM Agent**:  Anthropic API (`claude-sonnet-4-6`) or OpenAI API (`gpt-5.2`) via tool-use
- **Language**:   Python 3.10+
- **Runner**:     Claude Code (CLI)

---

## Project Structure
```
bracket-agent/
├── CLAUDE.md
├── brief.txt            # Example input brief
├── agent.py             # Conversational CLI: LLM tool-use chat loop (Anthropic + OpenAI)
├── agent_tools.py       # Tool implementations, session state, brief builder
├── pipeline.py          # Main agent loop (parse_brief + run)
├── constraints.py       # Constraint definitions + evaluator
├── optimizer.py         # Parameter update strategy between iterations
├── bracket_types/       # Multi-type bracket registry (L/T/U)
│   ├── __init__.py      # BracketType dataclass, REGISTRY, get_type()
│   ├── _helpers.py      # L-bracket helper functions (no tool imports)
│   ├── l_bracket.py     # L-bracket type definition + registration
│   ├── t_bracket.py     # T-bracket type definition + registration
│   └── u_bracket.py     # U-bracket type definition + registration
├── docs/
│   └── coordinate_convention.md  # Axis/origin convention for all bracket types
├── tools/
│   ├── geometry.py      # Parametric bracket creation via FreeCADCmd
│   ├── mesh.py          # Gmsh meshing (STEP → C3D10 mesh.inp)
│   ├── calculix.py      # .inp writer + ccx runner
│   ├── results.py       # .frd / .dat parser (fixed-width block reader)
│   ├── report.py        # Per-iteration summary.md generator
│   ├── render.py        # Mesh + FEA result renders (matplotlib, Agg)
│   └── presizing.py     # Analytical pre-sizing warm-start (beam/column theory)
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
    └── test_bracket.py  # 32 tests across all pipeline stages
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

The optional `bracket_type:` key in the `Bracket dimensions:` section selects
the geometry (default `l_bracket`). Unknown values raise `ValueError` immediately.
All material fields default (E=200 GPa, nu=0.3, rho=7850 kg/m³, Sy=250 MPa) when absent.

---

## Bracket Geometry Parameters
All dims in metres internally. Input accepted in mm and converted on parse.
See `docs/coordinate_convention.md` for axis orientation per type.

### L-bracket and T-bracket (same 5 keys)

| Key             | Description                        | Optimizer range |
|-----------------|------------------------------------|-----------------|
| `flange_width`  | Horizontal arm width (L) / span (T)| 0.04 – 0.20 m   |
| `flange_height` | Out-of-plane depth                 | 0.03 – 0.15 m   |
| `web_height`    | Vertical wall / web height         | 0.05 – 0.25 m   |
| `thickness`     | Uniform wall thickness             | 0.003 – 0.020 m |
| `fillet_radius` | Interior corner fillet radius      | 0.002 – 0.015 m |

### U-bracket (channel section)

| Key             | Description                        | Optimizer range |
|-----------------|------------------------------------|-----------------|
| `channel_width` | Total channel width                | 0.04 – 0.20 m   |
| `wall_height`   | Side wall height                   | 0.05 – 0.25 m   |
| `channel_depth` | Out-of-plane depth                 | 0.03 – 0.15 m   |
| `thickness`     | Uniform wall thickness             | 0.003 – 0.020 m |
| `fillet_radius` | Base-wall interior fillet radius   | 0.002 – 0.015 m |

Geometric constraint always enforced: `fillet_radius ≤ thickness × 0.45`.
This is applied after clamping to param bounds; the geometric limit takes
priority over the lower bound when they conflict (very thin sections).

---

## Core Agent Loop

```
INPUT: plain-text brief
   ↓
parse_brief(text) → params dict + constraints dict
   ↓
pre_size(geo, loads, material, constraints)  (once — before loop)
   ↓
┌─────────────────────────────────────────────────────────┐
│  iter N                                                 │
│  0. log current params (mm) at INFO                     │
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
│              if slim_after_pass: propose slim → loop    │
│     if fail  → propose_params(violations, metrics)      │
│               → loop                                    │
└─────────────────────────────────────────────────────────┘
   ↓
OUTPUT: best params dict + final eval dict
```

Best-result tracking: a passing design always beats any failing design;
among designs of the same pass/fail status, the lighter one wins.
Max iterations: **10** (hard limit — log warning, return best so far).

---

## Conversational Agent Layer

`agent.py` + `agent_tools.py` form a thin conversational layer on top of the
pipeline. The LLM acts as orchestrator via the tool-use API; Python dispatches
tool calls and feeds results back.

### Entry point: `agent.py`

```
User (plain English)
    │
    ▼
agent.py  ─── LLM API (Anthropic or OpenAI) ──► LLM
    │              ↑  tool_use calls
    ▼              │
dispatch_tool()
    │
    ├─ run_pipeline        → agent_tools.run_pipeline_tool()
    ├─ modify_and_run      → agent_tools.modify_and_run_tool()
    └─ read_last_results   → agent_tools.read_last_results_tool()
```

Backend selection (checked in order):
1. `AGENT_BACKEND` env var: `"anthropic"` or `"openai"`
2. Auto-detect: `ANTHROPIC_API_KEY` present → Anthropic; `OPENAI_API_KEY` → OpenAI

Models: `claude-sonnet-4-6` (Anthropic), `gpt-5.2` (OpenAI default, override
with `OPENAI_MODEL` env var).

Tool definitions are stored once in `_TOOL_DEFS` (neutral JSON Schema
`parameters` key) and converted at runtime:
- `_anthropic_tools()` → wraps with `input_schema`
- `_openai_tools()`    → wraps with `{"type": "function", "function": {...}}`

### `agent_tools.py`

```python
_session_state = {
    "last_params_mm": None,  # optimized geometry from last run (mm)
    "last_load_n":    None,  # load magnitude from last run
    "last_material":  None,  # material dict (E_gpa, nu, rho, Sy_mpa)
    "last_run_dir":   None,  # Path to last runs/iter_NNN
}

build_brief(params: dict) -> str
# Renders the plain-text brief format that parse_brief() expects.
# Keys: flange_width_mm, flange_height_mm, web_height_mm, thickness_mm,
#       fillet_radius_mm, E_gpa, nu, rho, Sy_mpa, load_n, max_mass_kg.
# Brief is constructed internally; never shown to the user.

run_pipeline_tool(inp: dict) -> dict
# Merges inp over defaults, builds brief, calls pipeline.run().
# Prints "[Running FEM pipeline...]" before calling.
# Updates _session_state on completion.
# Returns: {status, iterations_run, mass_kg, fos, max_vm_mpa,
#           max_disp_mm, stress_utilisation_pct, violations,
#           final_params_mm, output_dir}

modify_and_run_tool(inp: dict) -> dict
# Loads prior geometry/load/material from _session_state.
# Merges inp["changes"] over session state, rebuilds brief, reruns.

read_last_results_tool(inp: dict) -> dict
# Reads the highest-numbered runs/iter_NNN (or inp["iter_dir"]).
# Parses summary.md (FEM Results section) + params.json.
# Returns the same structured dict — no new simulation.
```

### Three LLM tools

| Tool | Required input | When the LLM calls it |
|------|---------------|-----------------------|
| `run_pipeline` | `load_n` | New design from scratch |
| `modify_and_run` | `changes` dict | Incremental change to current design |
| `read_last_results` | *(none)* | Inspect prior run, no new simulation |

### Coding rules for the agent layer
- `_session_state` is the only module-level state; all tool functions are
  otherwise side-effect-free w.r.t. the filesystem (pipeline.run() owns files)
- `build_brief()` output must always parse correctly through `parse_brief()`
- Both SDK imports (`anthropic`, `openai`) are deferred inside their respective
  `_run_*` functions — the module imports cleanly even if neither is installed
- Do NOT add a `modify_geometry` tool — geometry changes must go through FEM
- Do NOT expose `propose_params` as a tool — the optimizer is internal to the loop

---

## Tool Contracts

### tools/geometry.py
```python
create_geometry(params: dict, output_dir: Path, bracket_type=None,
                apply_fillet: bool = True) -> Path
# Runs FreeCADCmd, returns path to geometry.step
# bracket_type: BracketType | None — selects freecad_script_fn (default: l_bracket)
# FreeCAD works in mm internally; script scales to SI metres before exportStep
# params.json written with metadata keys including _bracket_type (and _schema_version)
# Raises GeometryError if FreeCAD exits non-zero or STEP missing

modify_geometry(step_path: Path, deltas: dict, output_dir: Path) -> Path
# Reads _bracket_type from params.json beside step_path; missing → WARNING + "l_bracket";
# unknown value → GeometryError. Merges deltas, calls create_geometry.
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
          material: dict, output_dir: Path, bracket_type=None) -> Path
# Assembles full analysis.inp from mesh + material + BCs
# bracket_type: BracketType | None — selects fixed_nodes_fn, tip_node_fn, load_patch_fn
# Only C3D* elements are included in EALL; surface/edge elements dropped
# Fixed face: determined by bracket_type.fixed_nodes_fn
# Load: distributed over load_patch_fn result (k=5 nodes, force/k each);
#       falls back to single tip_node_fn node when load_patch_fn is None.
#       loads["load_patch_k"] overrides k. Force conservation always holds.
# Raises SimulationError if fixed_nodes_fn returns []

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

### tools/presizing.py
```python
l_presizing(params, loads, material, constraints) -> dict
# L-bracket beam-theory warm-start:
#   wh ≥ max(sqrt(6*F*fw/(fh*σ_allow)), cbrt(4*F*fw³/(E*fh*δ_allow)))
#   t  ≥ max(3*F/(2*fh*σ_allow), bounds_lo)
# Never lowers params below user-supplied values. Clamps to param bounds.
# Fillet reduced only if needed for fillet ≤ thickness × 0.45.

t_presizing(params, loads, material, constraints) -> dict
# Same as L-bracket with fw/2 as moment arm (symmetric T load).

u_presizing(params, loads, material, constraints) -> dict
# U-bracket: t ≥ max(F/(cd*σ_allow), sqrt(6*F*wh/(cd*σ_allow)), bounds_lo)
# Only thickness (and fillet) are updated — wall_height/channel_width unchanged.
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

evaluate_constraints(metrics: dict, constraints: dict,
                     bracket_type=None) -> dict
# bracket_type: BracketType | None — uses bracket_type.mass_fn when provided,
#               falls back to L-bracket _compute_mass when None
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

propose_params(current_params: dict, violations: list, iteration: int,
               bracket_type=None, metrics=None, constraints=None) -> dict
# bracket_type: BracketType | None — delegates to bracket_type.optimizer.propose_fn
# no violations (slim_after_pass) → thickness *= 0.97; fillet adjusted
# stress/fos violation  → thickness and fillet_radius scaled by _physics_scale(vm, lim, exp=2)
#                         legacy fallback (1.10/1.20) when metrics/constraints absent
# displacement only     → web_height scaled by _physics_scale(disp, lim, exp=3)
#                         legacy fallback (1.10) when metrics/constraints absent
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

## Additional Contract Notes

### bracket_types/__init__.py — `BracketType` fields
```python
presizing_fn:  Callable | None = None  # (params, loads, material, constraints) -> params
                                        # None → pre-sizing skipped silently
load_patch_fn: Callable | None = None  # (nodes, params_si, k=5) -> list[int]
                                        # k nearest nodes to load application point.
                                        # None → write_inp() uses [tip_node_fn()] single-node
                                        #         (old behaviour; no patch load).
```

### bracket_types/_helpers.py — `_physics_scale`
```python
_physics_scale(actual, limit, exponent, iteration=1) -> float
# Returns the dimension multiplier from beam-theory scaling laws:
#   exponent=2 → bending stress (σ ∝ 1/h²) → multiplier = (actual/limit)^0.5
#   exponent=3 → deflection    (δ ∝ 1/h³) → multiplier = (actual/limit)^(1/3)
# Clamped to [1.02, max(1.10, 1.40-0.03*(iter-1))].
# Returns 1.0 for actual≤limit or invalid inputs (NaN, inf, limit≤0, exp≤0).
```

### pipeline.py — `run()` signature
```python
def run(brief_text: str, max_iter: int = 10,
        slim_after_pass: bool = False) -> tuple[dict, dict]:
# slim_after_pass=False: stop on first passing design (default, legacy)
# slim_after_pass=True:  continue reducing mass after first pass
```

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

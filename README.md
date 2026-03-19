# Bracket FEM Agent

An AI agent that accepts a plain-text bracket design brief, builds a parametric 3D bracket (L, T, or U) in FreeCAD, meshes it with Gmsh, solves it with CalculiX, and iterates the geometry to simultaneously:

- Stay below a maximum von Mises stress limit (Sy / 1.5)
- Maintain a minimum factor of safety (≥ 1.5)
- Keep tip deflection under a maximum (5 mm default)
- Minimise total bracket mass
- Converge in as few iterations as possible (hard cap: 10)

> Scope is limited to bracket geometries (L, T, and U). No generalisation to arbitrary CAD shapes until the bracket pipeline is proven end-to-end.

### Bracket Types

| Type | Shape | Fixed face | Tip location |
|------|-------|------------|--------------|
| `l_bracket` (default) | L-shaped web + flange | x ≈ 0 (web back) | flange tip (max x, mid y, near top) |
| `t_bracket` | Inverted T with central web | z ≈ 0 (bottom) | flange center top (mid x, mid y, near top) |
| `u_bracket` | Channel: two walls + base plate | z ≈ 0 (base bottom) | top of left wall center (x=0, mid y, max z) |

---

## How It Works

```
plain-text brief
      │
      ▼
 parse_brief()         → params dict + constraints dict
      │
      ▼  (once, before loop)
 pre_size()            → warm-start geometry from beam/column theory
      │
      ▼  (repeat up to 10×)
 create_geometry()     → geometry.step       (FreeCAD headless)
 generate_mesh()       → mesh.inp            (Gmsh, C3D10 quad tets)
 render_mesh()         → render.png          (4-view mesh visualisation)
 write_inp()           → analysis.inp        (CalculiX input file)
 run_simulation()      → analysis.frd/.dat   (CalculiX static solve)
 render_results()      → results_render.png  (stress + displacement maps)
 parse_frd/dat()       → metrics dict        (stress, displacement)
 evaluate_constraints() → pass/fail + violations
      │
      ├─ PASS → generate_report()
      │         if slim_after_pass: try lighter → continue
      │         else: stop
      └─ FAIL → propose_params()  → next iteration
```

Best-result tracking: a passing design always beats any failing design; among designs of the same pass/fail status, the lighter one wins.

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

There are two ways to run the pipeline:

### 1. Conversational agent (recommended)

`agent.py` wraps the pipeline in a chat interface. Describe your requirements
in plain English — no brief file needed.

#### Step 1 — install the LLM SDK

```bash
pip install anthropic   # Anthropic backend (claude-sonnet-4-6)
pip install openai      # OpenAI backend    (gpt-5.2)
```

#### Step 2 — export your API key and start the agent

```bash
# Anthropic
export ANTHROPIC_API_KEY=sk-ant-...



# OpenAI
export OPENAI_API_KEY=sk-...
python agent.py
```

Additional env vars (all optional):

| Variable | Default | Purpose |
|----------|---------|---------|
| `AGENT_BACKEND` | auto-detect | Force `anthropic` or `openai` when both keys are set |
| `OPENAI_MODEL` | `gpt-5.2` | Override the OpenAI model (e.g. `gpt-5.2-mini`) |

Backend auto-detection order: `AGENT_BACKEND` → `ANTHROPIC_API_KEY` → `OPENAI_API_KEY`.

#### Step 3 — describe your bracket

Just tell the agent what load you need to carry. Everything else is optional —
the agent uses geometry defaults and asks at most two clarifying questions
before running.

**Example session:**

```
Bracket FEM Agent  [Anthropic / claude-sonnet-4-6]  (type 'exit' to stop)
=======================================================

You: I need a bracket to hold 2000 N on a 100 mm arm, keep it light
Assistant: I'll use a 100 mm web height and minimize mass without a hard budget.
           Running the FEM pipeline now — this will take about 2-5 minutes...
[Running FEM pipeline — this may take 2-5 minutes...]

Assistant: PASS on iteration 3
  Mass:        712 g
  FOS:         1.62
  Peak stress: 154 MPa  (61.7% of yield)
  Deflection:  0.31 mm
  Output:      runs/iter_003/
  Final geometry: flange_width=80 mm, flange_height=60 mm,
                  web_height=100 mm, thickness=7.26 mm,
                  fillet_radius=3.27 mm

You: What if the load is 3000 N?
Assistant: Increasing the load to 3000 N and re-running from the same baseline...
[Running FEM pipeline — this may take 2-5 minutes...]
Assistant: PASS on iteration 2
  Mass:        831 g   (extra material needed for the higher load)
  FOS:         1.54
  Peak stress: 162 MPa  (64.9% of yield)
  Deflection:  0.47 mm
  Output:      runs/iter_005/

You: Show me the stress from the last run
Assistant: (reads runs/iter_005/summary.md — no new simulation)
  Status: PASS  |  Peak stress: 162 MPa  |  FOS: 1.54  |  Mass: 831 g

You: exit
Goodbye.
```

#### What you can ask

| Intent | Example prompt |
|--------|----------------|
| New design | `Design a bracket to hold 500 N, web height 80 mm` |
| Change the load | `What if the load doubles to 4000 N?` |
| Change geometry | `Try a wider flange — 120 mm` |
| Add a mass budget | `Keep the mass under 600 g` |
| Change material | `Use aluminium: E=70 GPa, Sy=270 MPa, rho=2700` |
| Inspect prior run | `Show me the results from the last run` |
| Specific iteration | `What were the results at iter_002?` |

#### Output files

Each iteration writes to `runs/iter_NNN/` (immutable after creation):

```
runs/iter_003/
├── params.json          # Geometry parameters used this iteration
├── geometry.step        # STEP file from FreeCAD
├── mesh.inp             # Gmsh mesh (C3D10 quadratic tetrahedra)
├── analysis.inp         # Full CalculiX input deck
├── analysis.frd         # CalculiX displacement + stress results
├── analysis.dat         # Reaction forces + strain energy
├── render.png           # 4-view mesh render (isometric, front, side, top)
├── results_render.png   # 4-view FEA results (von Mises stress + displacement)
└── summary.md           # Human-readable report with embedded images
```

---

### 2. Direct pipeline (scriptable)

```bash
python pipeline.py brief.txt
```

Optional keyword arguments when calling `pipeline.run()` directly:

```python
slim_after_pass : bool = False   # set True to continue after first pass for mass minimisation
```

### Input format

The optional `bracket_type:` key in the `Bracket dimensions:` section selects the geometry (default `l_bracket`). Unknown values raise `ValueError` immediately.

**L-bracket / T-bracket brief (5 shared geometry keys):**

```
Bracket dimensions:
  bracket_type:  l_bracket   # or t_bracket (optional — default l_bracket)
  flange_width:  80 mm
  flange_height: 60 mm
  web_height:    100 mm
  thickness:     6 mm
  fillet_radius: 4 mm

Material: structural steel
  E:    200 GPa
  nu:   0.3
  rho:  7850 kg/m3
  Sy:   250 MPa

Load:
  type:      point_force
  location:  tip of flange
  magnitude: 2000 N
  direction: -Z

Boundary conditions:
  fixed face: web back face (all DOF constrained)
```

**U-bracket brief (5 different geometry keys):**

```
Bracket dimensions:
  bracket_type:   u_bracket
  channel_width:  80 mm
  wall_height:    100 mm
  channel_depth:  60 mm
  thickness:      6 mm
  fillet_radius:  4 mm
```

Units accepted: `mm`, `m`, `GPa`, `MPa`, `Pa`, `kN`, `N`, `kg/m3`, `kg`. All values are converted to SI internally.

An optional mass budget may be added:

    max_mass_kg: 0.05 kg

---

## Project Structure

```
bracket-agent/
├── agent.py             # Conversational CLI: LLM tool-use chat loop
├── agent_tools.py       # Tool implementations + session state + brief builder
├── pipeline.py          # Orchestrator: parse_brief() + run() loop
├── constraints.py       # CONSTRAINTS dict + evaluate_constraints()
├── optimizer.py         # propose_params(): geometry update strategy
├── bracket_types/       # Multi-type bracket registry (L / T / U)
│   ├── __init__.py      # BracketType dataclass, REGISTRY, get_type()
│   ├── _helpers.py      # L-bracket helper functions (shared with T-bracket)
│   ├── l_bracket.py     # L-bracket type definition + registration
│   ├── t_bracket.py     # T-bracket type definition + registration
│   └── u_bracket.py     # U-bracket type definition + registration
├── docs/
│   └── coordinate_convention.md  # Axis/origin convention for each bracket type
├── tools/
│   ├── geometry.py      # create_geometry() / modify_geometry() via FreeCADCmd
│   ├── mesh.py          # generate_mesh() via Gmsh Python API
│   ├── calculix.py      # write_inp() + run_simulation() (ccx subprocess)
│   ├── results.py       # parse_frd() / parse_frd_nodal() / parse_dat()
│   ├── report.py        # generate_report() -> summary.md
│   ├── render.py        # render_mesh() + render_results() -> PNG
│   └── presizing.py     # Analytical pre-sizing warm-start (beam/column theory)
├── runs/                # Per-iteration output (git-ignored)
└── tests/
    ├── test_agent.py        # ~55 tests: agent layer (build_brief, tool schemas, dispatch, mocks)
    ├── test_bracket.py      # 32 tests: pure unit, geometry, mesh, calculix, integration
    ├── test_bracket_types.py# ~70 tests: registry, L/T/U mass, fixed nodes, tip nodes, optimizer
    └── test_smoke.py        # 6 end-to-end pipeline scenarios
```

---

## Geometry Parameters

All dimensions are metres (SI) internally. Input accepted in mm and converted on parse.

### L-bracket and T-bracket (same 5 keys)

| Parameter | Description | Optimizer range |
|-----------|-------------|-----------------|
| `flange_width` | Horizontal arm width (L) / span (T) | 0.04 – 0.20 m |
| `flange_height` | Out-of-plane depth | 0.03 – 0.15 m |
| `web_height` | Vertical wall / web height | 0.05 – 0.25 m |
| `thickness` | Uniform wall thickness | 0.003 – 0.020 m |
| `fillet_radius` | Interior corner fillet radius | 0.002 – 0.015 m |

### U-bracket (channel section)

| Parameter | Description | Optimizer range |
|-----------|-------------|-----------------|
| `channel_width` | Total channel width | 0.04 – 0.20 m |
| `wall_height` | Side wall height | 0.05 – 0.25 m |
| `channel_depth` | Out-of-plane depth | 0.03 – 0.15 m |
| `thickness` | Uniform wall thickness | 0.003 – 0.020 m |
| `fillet_radius` | Base-wall interior fillet radius | 0.002 – 0.015 m |

Constraint always enforced: `fillet_radius <= thickness x 0.45`.

---

## Constraints

| Constraint | Default | Notes |
|------------|---------|-------|
| Max von Mises stress | Sy / 1.5 | 166.7 MPa for 250 MPa steel |
| Min factor of safety | 1.5 | FOS = Sy / max_vm |
| Max tip deflection | 5 mm | Absolute displacement magnitude |
| Max mass | None | Set from brief; unconstrained if omitted |

Constraint limits are read from the brief and passed through the full pipeline to `generate_report`, so the summary always shows the actual limits used.

---

## Optimizer Strategy

L-bracket and T-bracket share the same strategy (same `propose_fn` and param bounds):

| Violation type | Action |
|---------------|--------|
| Stress or FOS | `thickness` and `fillet_radius` scaled by `_physics_scale(vm, limit, exp=2, iter)` — multiplier ∝ √(violation ratio), iteration-damped |
| Displacement + stress | Above, plus `web_height × 1.05` |
| Displacement only | `web_height` scaled by `_physics_scale(disp, limit, exp=3, iter)` — multiplier ∝ ∛(violation ratio); falls back to thickness at bound |
| Mass only | `thickness × 0.95` |
| No violations (slim_after_pass=True) | `thickness × 0.97` (mass descent); fillet adjusted to maintain constraint |

U-bracket uses an equivalent strategy with `wall_height` in place of `web_height`.

> `_physics_scale` clamps to `[1.02, 1.40]` at iteration 1, shrinking to `[1.02, 1.10]`
> by iteration 10 to prevent oscillation. Falls back to legacy `1.10`/`1.20` if
> `metrics` or `constraints` are not provided.

All parameters are clamped to their bounds after update; then `fillet_radius <= thickness x 0.45` is enforced (geometric constraint takes priority over lower bound).

### Pre-sizing warm-start

Before iteration 1, `pipeline.run()` calls `bracket_type.presizing_fn()` from
`tools/presizing.py` to derive minimum geometry from beam/column theory:

| Type | Formula |
|------|---------|
| L-bracket | `wh ≥ √(6·F·fw / (fh·σ_allow))` and `wh ≥ ∛(4·F·fw³ / (E·fh·δ_allow))` |
| T-bracket | Same as L with `fw/2` as moment arm (symmetric loading) |
| U-bracket | `t ≥ max(F/(cd·σ_allow), √(6·F·wh/(cd·σ_allow)))` |

User-supplied values are never lowered (only raised to meet the minimum).

---

## Running Tests

The test suite is split across three files. Marks control which external
dependencies are required; `skipif` guards auto-skip when a tool is absent.

### Agent layer tests (`tests/test_agent.py`) — ~55 tests

No external tools or API key required for the core tier.

| Tier | Requires | Command |
|------|----------|---------|
| T1 — pure unit | nothing | `python -m pytest tests/test_agent.py -v -m "not requires_runs and not requires_api"` |
| T1 + T2 — with prior runs | completed `runs/` | `python -m pytest tests/test_agent.py -v -m "not requires_api"` |
| All | API key + FreeCAD + Gmsh + ccx | `python -m pytest tests/test_agent.py -v` |

What T1 covers:

| Class | What is tested |
|-------|----------------|
| `TestBuildBrief` | `build_brief()` roundtrips through `parse_brief()` — geometry, material, mass budget, defaults |
| `TestToolSchemas` | `_anthropic_tools()` / `_openai_tools()` format; required fields per backend |
| `TestDispatchTool` | Each tool name routes correctly; unknown tool returns error dict |
| `TestRunPipelineToolValidation` | Missing `load_n`; mocked pipeline receives correct brief; result keys; PASS/FAIL status |
| `TestModifyAndRunTool` | Error with no prior run; `changes` merged correctly; caller dict not mutated |
| `TestParseSummaryMd` | All FEM metrics parsed; constraint-status table does not overwrite FEM Results values; violations list |

T2 (`TestReadLastResultsLive`) auto-skips when `runs/` is empty.

---

### Bracket types tests (`tests/test_bracket_types.py`) — ~70 tests

No external tools required.

```bash
python -m pytest tests/test_bracket_types.py -v
```

| Class | What is tested |
|-------|----------------|
| `TestRegistry` | All 3 types importable; `get_type()` returns correct `BracketType`; unknown type raises |
| `TestLBracketRegression` | L-bracket mass/optimizer parity with old formula; T-bracket reuses `_l_propose_params` by identity |
| `TestTBracketMass` | Analytical mass formula; T and L share same formula for same 5 params |
| `TestUBracketMass` | Analytical mass formula (base + 2 walls – 2 overlaps) |
| `TestFixedNodeDetection` | Fixed nodes at correct face (x≈0 for L, z≈0 for T/U) |
| `TestTipNodeDetection` | Tip node closest to correct target; U fallback uses Y span not midpoint |
| `TestEvaluateConstraintsWithType` | `evaluate_constraints` passes for each type; `None` falls back to L |
| `test_write_inp_empty_fixed_nodes_raises` | `write_inp` raises `SimulationError` when no fixed nodes found |

---

### Pipeline unit tests (`tests/test_bracket.py`)

Tests that require external tools are decorated with `@pytest.mark` and
`@pytest.mark.skipif` — the mark gives explicit control via `-m`; the
`skipif` auto-skips when the tool is absent.

**Pure unit tests** — no external tools:

```bash
python -m pytest tests/test_bracket.py -v -m "not requires_freecadcmd and not requires_gmsh and not requires_ccx"
```

**Everything** (missing tools still auto-skipped):

```bash
python -m pytest tests/test_bracket.py -v
```

---

### Smoke / integration tests (`tests/test_smoke.py`)

Full pipeline end-to-end. Requires FreeCAD + Gmsh + CalculiX.

```bash
python -m pytest tests/test_smoke.py -v
```

| Test | Scenario | Key check |
|------|----------|-----------|
| S1 `test_s1_easy_pass` | Oversized bracket, 500 N | Passes on iter 1, FOS >= 1.5 |
| S2 `test_s2_forced_iteration` | Nominal bracket, 5000 N | At least 2 iterations run |
| S3 `test_s3_stagnation_upper_bounds` | 50 000 N (impossible) | No crash, `pass is False` |
| S4 `test_s4_displacement_dominated` | Slender bracket, 1000 N | Displacement violation fires |
| S5 `test_s5_mass_constrained` | Nominal bracket, 500 N | Pipeline runs, mass > 0 |
| S6 `test_s6_artefact_completeness` | Oversized bracket, 500 N | All output files present |

> Expected runtime: ~2–5 min per scenario.

---

### Run everything at once

```bash
python -m pytest -v
```

---

## References

- [FreeCAD Python scripting](https://wiki.freecad.org/Python_scripting_tutorial)
- [Gmsh Python API](https://gmsh.info/doc/texinfo/gmsh.html)
- [CalculiX manual](http://www.dhondt.de/ccx_2.21.pdf)
- [.frd format spec](http://www.dhondt.de/cgx_2.21.pdf) (Section 4)

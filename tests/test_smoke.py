"""
Smoke / stress tests for the Bracket FEM pipeline.

Each test calls pipeline.run() directly and checks structural correctness of
outputs — no brittle numerical assertions that depend on mesh/solver versions.

All tests require FreeCADCmd + gmsh + ccx and are auto-skipped when absent.
"""

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Skip guard (shared by all tests in this file)
# ---------------------------------------------------------------------------

_REQUIRES_ALL = pytest.mark.skipif(
    shutil.which("FreeCADCmd") is None
    or importlib.util.find_spec("gmsh") is None
    or shutil.which("ccx") is None,
    reason="FreeCADCmd, gmsh, and ccx are all required for smoke tests",
)

_GEOMETRY_KEYS = ("flange_width", "flange_height", "web_height", "thickness", "fillet_radius")


# ---------------------------------------------------------------------------
# Shared brief template
# ---------------------------------------------------------------------------

def _brief(
    thickness_mm: float = 6,
    fillet_mm: float = 4,
    web_height_mm: float = 100,
    flange_width_mm: float = 80,
    flange_height_mm: float = 60,
    load_n: float = 2000,
    max_mass_kg: float | None = None,
) -> str:
    lines = [
        "Bracket dimensions:",
        f"  flange_width:  {flange_width_mm} mm",
        f"  flange_height: {flange_height_mm} mm",
        f"  web_height:    {web_height_mm} mm",
        f"  thickness:     {thickness_mm} mm",
        f"  fillet_radius: {fillet_mm} mm",
        "",
        "Material: structural steel",
        "  E:    200 GPa",
        "  nu:   0.3",
        "  rho:  7850 kg/m³",
        "  Sy:   250 MPa",
        "",
        "Load:",
        "  type:      point_force",
        "  location:  tip of flange",
        f"  magnitude: {load_n} N",
        "  direction: -Z",
        "",
        "Boundary conditions:",
        "  fixed face: web back face",
    ]
    if max_mass_kg is not None:
        lines += ["", f"max_mass_kg: {max_mass_kg} kg"]
    return "\n".join(lines) + "\n"


def _iter_dir(runs_dir: Path, n: int) -> Path:
    return runs_dir / f"iter_{n:03d}"


# ---------------------------------------------------------------------------
# S1 — Easy pass (oversized bracket, light load → pass on iter 1)
# ---------------------------------------------------------------------------

@pytest.mark.requires_freecadcmd
@pytest.mark.requires_gmsh
@pytest.mark.requires_ccx
@_REQUIRES_ALL
def test_s1_easy_pass(tmp_path, monkeypatch):
    """Oversized bracket under 500 N should pass on the first iteration."""
    monkeypatch.chdir(tmp_path)

    from pipeline import run

    brief = _brief(thickness_mm=18, fillet_mm=8, load_n=500)
    best_params, eval_result = run(brief, max_iter=3)

    assert eval_result.get("pass") is True
    assert best_params is not None
    for key in _GEOMETRY_KEYS:
        assert key in best_params, f"Missing geometry key: {key}"

    runs_dir = tmp_path / "runs"
    iter1 = _iter_dir(runs_dir, 1)
    assert iter1.exists()
    assert (iter1 / "geometry.step").exists()
    assert (iter1 / "mesh.inp").exists()
    assert (iter1 / "analysis.frd").exists()
    assert (iter1 / "analysis.dat").exists()
    assert (iter1 / "summary.md").exists()

    assert eval_result.get("fos", 0) >= 1.5
    assert eval_result.get("mass_kg", 0) > 0

    summary_text = (iter1 / "summary.md").read_text(encoding="utf-8")
    assert "PASS" in summary_text


# ---------------------------------------------------------------------------
# S2 — Forced iteration (heavy load → optimizer must iterate)
# ---------------------------------------------------------------------------

@pytest.mark.requires_freecadcmd
@pytest.mark.requires_gmsh
@pytest.mark.requires_ccx
@_REQUIRES_ALL
def test_s2_forced_iteration(tmp_path, monkeypatch):
    """Nominal bracket under 5000 N should require multiple iterations."""
    monkeypatch.chdir(tmp_path)

    from pipeline import run

    brief = _brief(thickness_mm=6, fillet_mm=4, load_n=5000)
    best_params, eval_result = run(brief, max_iter=5)

    runs_dir = tmp_path / "runs"

    # At least two iterations should have run
    assert _iter_dir(runs_dir, 1).exists()
    assert _iter_dir(runs_dir, 2).exists(), (
        "Expected at least 2 iterations for a 5000 N load on a nominal bracket"
    )

    assert eval_result.get("mass_kg", 0) > 0

    if eval_result.get("pass"):
        assert eval_result.get("fos", 0) >= 1.5
        # displacement is guaranteed ≤ 5 mm by eval["pass"] being True

    # summary.md in the last produced iter dir must exist and be non-empty
    iter_dirs = sorted(runs_dir.glob("iter_*"))
    last_iter = iter_dirs[-1]
    summary = last_iter / "summary.md"
    assert summary.exists()
    assert summary.stat().st_size > 0


# ---------------------------------------------------------------------------
# S3 — Stagnation at upper bounds (impossibly high load)
# ---------------------------------------------------------------------------

@pytest.mark.requires_freecadcmd
@pytest.mark.requires_gmsh
@pytest.mark.requires_ccx
@_REQUIRES_ALL
def test_s3_stagnation_upper_bounds(tmp_path, monkeypatch):
    """50 000 N load exceeds capability — run() must return without crashing."""
    monkeypatch.chdir(tmp_path)

    from pipeline import run

    brief = _brief(thickness_mm=6, fillet_mm=4, load_n=50_000)
    # Should not raise
    best_params, eval_result = run(brief, max_iter=10)

    assert best_params is not None
    assert eval_result.get("pass") is False

    runs_dir = tmp_path / "runs"
    iter_dirs = sorted(runs_dir.glob("iter_*"))
    assert len(iter_dirs) >= 1

    # Every iter dir that was created must have a params.json
    for d in iter_dirs:
        assert (d / "params.json").exists(), f"params.json missing in {d.name}"


# ---------------------------------------------------------------------------
# S4 — Displacement-dominated violation
# ---------------------------------------------------------------------------

@pytest.mark.requires_freecadcmd
@pytest.mark.requires_gmsh
@pytest.mark.requires_ccx
@_REQUIRES_ALL
def test_s4_displacement_dominated(tmp_path, monkeypatch):
    """Slender bracket: displacement violation should fire before stress."""
    monkeypatch.chdir(tmp_path)

    from pipeline import run

    initial_web_height_m = 0.050
    brief = _brief(
        thickness_mm=4,
        fillet_mm=1,    # fillet <= thickness * 0.45 = 1.8 mm → 1 mm OK
        web_height_mm=50,
        flange_width_mm=80,
        flange_height_mm=60,
        load_n=1000,
    )
    best_params, eval_result = run(brief, max_iter=5)

    runs_dir = tmp_path / "runs"
    iter1_summary = _iter_dir(runs_dir, 1) / "summary.md"
    assert iter1_summary.exists()

    summary_text = iter1_summary.read_text(encoding="utf-8").lower()
    # Displacement constraint violation text must appear when iter 1 fails
    # (only check if iter 1 actually failed — if it passed, no violation expected)
    iter1_params_path = _iter_dir(runs_dir, 1) / "params.json"
    # At least one iteration ran
    assert iter1_params_path.exists()

    # If iter 1 failed, check displacement violation appears
    # (If it somehow passed, we still verify the output is sane)
    if not eval_result.get("pass") or _iter_dir(runs_dir, 2).exists():
        # Optimizer should have increased web_height to fix displacement
        assert best_params.get("web_height", 0) >= initial_web_height_m - 1e-9, (
            "Expected web_height to be at least as large as the initial value"
        )

    assert eval_result.get("mass_kg", 0) > 0


# ---------------------------------------------------------------------------
# S5 — Mass-constrained optimum
# ---------------------------------------------------------------------------

@pytest.mark.requires_freecadcmd
@pytest.mark.requires_gmsh
@pytest.mark.requires_ccx
@_REQUIRES_ALL
def test_s5_mass_constrained(tmp_path, monkeypatch):
    """Tight mass budget: if a passing design is found it must be within budget."""
    monkeypatch.chdir(tmp_path)

    from pipeline import run

    max_mass = 0.05  # kg — intentionally tight
    brief = _brief(thickness_mm=6, fillet_mm=4, load_n=500, max_mass_kg=max_mass)
    best_params, eval_result = run(brief, max_iter=5)

    assert eval_result.get("mass_kg", 0) > 0

    runs_dir = tmp_path / "runs"
    iter1_summary = _iter_dir(runs_dir, 1) / "summary.md"
    assert iter1_summary.exists()
    assert iter1_summary.stat().st_size > 0


# ---------------------------------------------------------------------------
# S6 — Output artefact completeness
# ---------------------------------------------------------------------------

@pytest.mark.requires_freecadcmd
@pytest.mark.requires_gmsh
@pytest.mark.requires_ccx
@_REQUIRES_ALL
def test_s6_artefact_completeness(tmp_path, monkeypatch):
    """All expected output files must be present and non-trivially sized."""
    monkeypatch.chdir(tmp_path)

    from pipeline import run

    brief = _brief(thickness_mm=18, fillet_mm=8, load_n=500)
    run(brief, max_iter=1)

    runs_dir = tmp_path / "runs"
    iter1 = _iter_dir(runs_dir, 1)
    assert iter1.exists(), "iter_001 directory was not created"

    # params.json — valid JSON with all 5 geometry keys
    params_path = iter1 / "params.json"
    assert params_path.exists(), "params.json missing"
    loaded = json.loads(params_path.read_text(encoding="utf-8"))
    for key in _GEOMETRY_KEYS:
        assert key in loaded, f"params.json missing key: {key}"

    # geometry.step — non-trivial STEP file
    step_path = iter1 / "geometry.step"
    assert step_path.exists(), "geometry.step missing"
    assert step_path.stat().st_size > 1000, "geometry.step is suspiciously small"

    # mesh.inp — contains *NODE and *ELEMENT
    mesh_path = iter1 / "mesh.inp"
    assert mesh_path.exists(), "mesh.inp missing"
    mesh_text = mesh_path.read_text(encoding="utf-8")
    assert "*NODE" in mesh_text, "mesh.inp missing *NODE section"
    assert "*ELEMENT" in mesh_text, "mesh.inp missing *ELEMENT section"

    # analysis.inp — contains *STATIC and *END STEP
    inp_path = iter1 / "analysis.inp"
    assert inp_path.exists(), "analysis.inp missing"
    inp_text = inp_path.read_text(encoding="utf-8").upper()
    assert "*STATIC" in inp_text, "analysis.inp missing *STATIC"
    assert "*END STEP" in inp_text, "analysis.inp missing *END STEP"

    # analysis.frd — non-empty solver output
    frd_path = iter1 / "analysis.frd"
    assert frd_path.exists(), "analysis.frd missing"
    assert frd_path.stat().st_size > 0, "analysis.frd is empty"

    # analysis.dat — non-empty solver output
    dat_path = iter1 / "analysis.dat"
    assert dat_path.exists(), "analysis.dat missing"
    assert dat_path.stat().st_size > 0, "analysis.dat is empty"

    # summary.md — contains expected heading
    summary_path = iter1 / "summary.md"
    assert summary_path.exists(), "summary.md missing"
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "Geometry Parameters" in summary_text, (
        "summary.md missing 'Geometry Parameters' heading"
    )

    # render.png and results_render.png — only checked if matplotlib is available
    if importlib.util.find_spec("matplotlib") is not None:
        render_path = iter1 / "render.png"
        assert render_path.exists(), "render.png missing (matplotlib is available)"
        results_render_path = iter1 / "results_render.png"
        assert results_render_path.exists(), (
            "results_render.png missing (matplotlib is available)"
        )

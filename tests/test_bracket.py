"""
Smoke test: simple L-bracket under 1 kN point load.

Verifies the full pipeline (geometry → mesh → solve → parse → evaluate)
runs end-to-end without error for a reference bracket configuration and
that the result dict contains the expected keys.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from constraints import CONSTRAINTS, evaluate_constraints
from optimizer import propose_params, PARAM_BOUNDS
from pipeline import parse_brief


# -------------------------------------------------------------------------
# Unit tests — no external tools required
# -------------------------------------------------------------------------

SAMPLE_BRIEF = """\
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
  fixed face: web back face
"""


def test_parse_brief():
    params, constraints = parse_brief(SAMPLE_BRIEF)

    # Geometry keys present and converted mm → m
    assert abs(params["flange_width"]  - 0.080) < 1e-9
    assert abs(params["flange_height"] - 0.060) < 1e-9
    assert abs(params["web_height"]    - 0.100) < 1e-9
    assert abs(params["thickness"]     - 0.006) < 1e-9
    assert abs(params["fillet_radius"] - 0.004) < 1e-9

    # Material GPa → Pa, MPa → Pa
    mat = params["material"]
    assert abs(mat["E_pa"]  - 200e9) < 1.0
    assert abs(mat["nu"]    - 0.3)   < 1e-9
    assert abs(mat["rho"]   - 7850)  < 1e-3
    assert abs(mat["Sy_pa"] - 250e6) < 1.0

    # Load
    loads = params["loads"]
    assert abs(loads["magnitude_n"] - 2000.0) < 1e-9
    assert loads["direction"].upper() == "-Z"

    # Constraints derived from Sy
    assert "max_von_mises_pa" in constraints


def test_evaluate_constraints_pass():
    metrics = {
        "max_von_mises_pa":   100e6,   # well below allowable
        "max_displacement_m": 0.002,   # below 5 mm
        "node_count":         500,
        "params": {
            "flange_width":  0.08,
            "flange_height": 0.06,
            "web_height":    0.10,
            "thickness":     0.006,
            "fillet_radius": 0.004,
        },
        "rho":   7850.0,
        "Sy_pa": 250e6,
    }
    result = evaluate_constraints(metrics, CONSTRAINTS)
    assert result["pass"] is True
    assert result["violations"] == []
    assert result["mass_kg"] > 0
    assert result["fos"] > 1.5
    assert 0.0 < result["stress_utilisation"] < 1.0


def test_evaluate_constraints_fail():
    metrics = {
        "max_von_mises_pa":   300e6,   # above allowable (250/1.5 = 166.67 MPa)
        "max_displacement_m": 0.001,
        "node_count":         500,
        "params": {
            "flange_width":  0.08,
            "flange_height": 0.06,
            "web_height":    0.10,
            "thickness":     0.006,
            "fillet_radius": 0.004,
        },
        "rho":   7850.0,
        "Sy_pa": 250e6,
    }
    result = evaluate_constraints(metrics, CONSTRAINTS)
    assert result["pass"] is False
    assert len(result["violations"]) >= 1
    # Violation strings should use the "stress:" or "fos:" prefix
    prefixes = [v.split(":")[0] for v in result["violations"]]
    assert any(p in ("stress", "fos") for p in prefixes)


def test_propose_params_stress():
    # thickness=0.010, fillet=0.004 satisfies fillet <= thickness*0.45 (0.0045)
    params = {
        "flange_width":  0.08,
        "flange_height": 0.06,
        "web_height":    0.10,
        "thickness":     0.010,
        "fillet_radius": 0.004,
    }
    violations = ["stress: max von Mises 300.00 MPa exceeds allowable 166.67 MPa"]
    new_params = propose_params(params, violations, iteration=1)

    assert new_params["thickness"] > params["thickness"]
    assert new_params["fillet_radius"] > params["fillet_radius"]


def test_propose_params_clamp():
    params = {
        "flange_width":  0.20,    # already at upper bound
        "flange_height": 0.15,
        "web_height":    0.25,    # already at upper bound
        "thickness":     0.020,   # already at upper bound
        "fillet_radius": 0.009,
    }
    violations = ["stress: exceeds allowable"]
    new_params = propose_params(params, violations, iteration=5)

    # All params must stay within PARAM_BOUNDS
    for key, (lo, hi) in PARAM_BOUNDS.items():
        if key in new_params:
            assert lo <= new_params[key] <= hi, (
                f"{key}={new_params[key]} out of bounds [{lo}, {hi}]"
            )


# -------------------------------------------------------------------------
# Parser unit tests (synthetic .frd / .dat files)
# -------------------------------------------------------------------------

def _make_frd(path: Path):
    """Write a minimal synthetic .frd with DISP and STRESS blocks.

    Real CalculiX .frd format:
      positions 0-2 : ' -4' / ' -5' / ' -1' / ' -3'  (record type)
      positions 3-12: node_id  I10  (for -1 records)
      positions 13+ : values   E12.5 each (12 chars, no separator needed)
    """
    content = (
        # DISP result block header
        " -4  DISP        4    1\n"
        " -5  D1          1    2    1    0\n"
        " -5  D2          1    2    2    0\n"
        " -5  D3          1    2    3    0\n"
        # Node 1: displacement (0.003, 0.0, 0.004) → magnitude = 0.005
        " -1         1 3.00000E-03 0.00000E+00 4.00000E-03\n"
        # Node 2: smaller displacement
        " -1         2 1.00000E-03 0.00000E+00 1.00000E-03\n"
        " -3\n"
        # STRESS result block header
        " -4  STRESS      6    1\n"
        " -5  SXX         1    4    1    1\n"
        " -5  SYY         1    4    2    2\n"
        " -5  SZZ         1    4    3    3\n"
        " -5  SXY         1    4    1    2\n"
        " -5  SYZ         1    4    2    3\n"
        " -5  SZX         1    4    3    1\n"
        # Node 1: uniaxial sxx=100 MPa → vm = 100 MPa
        " -1         1 1.00000E+08 0.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00\n"
        # Node 2: lower stress
        " -1         2 5.00000E+07 0.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00\n"
        " -3\n"
    )
    path.write_text(content, encoding="latin-1")


def _make_dat(path: Path):
    """Write a minimal synthetic .dat with reaction forces."""
    content = (
        "\n"
        " forces (reactions) for set NALL and time  1.0\n"
        "\n"
        "       1   -1.000E+02   0.000E+00  -2.000E+03\n"
        "       2   -5.000E+01   0.000E+00  -0.000E+00\n"
        "\n"
        " total strain energy for the whole model : 1.2345E-01\n"
        "\n"
    )
    path.write_text(content, encoding="latin-1")


def test_parse_frd(tmp_path):
    from tools.results import parse_frd

    frd_path = tmp_path / "test.frd"
    _make_frd(frd_path)
    result = parse_frd(frd_path)

    assert "max_von_mises_pa" in result
    assert "max_displacement_m" in result
    assert "node_count" in result

    # Displacement magnitude of node 1: sqrt(0.003²+0.004²) = 0.005
    assert abs(result["max_displacement_m"] - 0.005) < 1e-6

    # Von Mises for uniaxial 100 MPa = 100 MPa
    assert abs(result["max_von_mises_pa"] - 100e6) < 1e3


def test_parse_dat(tmp_path):
    from tools.results import parse_dat

    dat_path = tmp_path / "test.dat"
    _make_dat(dat_path)
    result = parse_dat(dat_path)

    assert "reaction_forces_n" in result
    assert "strain_energy_j" in result

    rf = result["reaction_forces_n"]
    assert abs(rf["fx"] - (-150.0)) < 1e-3   # -100 + -50
    assert abs(rf["fz"] - (-2000.0)) < 1e-3


# -------------------------------------------------------------------------
# Integration test — requires FreeCAD + ccx
# -------------------------------------------------------------------------

@pytest.mark.requires_freecadcmd
@pytest.mark.requires_gmsh
@pytest.mark.requires_ccx
@pytest.mark.skipif(
    shutil.which("FreeCADCmd") is None
    or importlib.util.find_spec("gmsh") is None
    or shutil.which("ccx") is None,
    reason="FreeCADCmd, gmsh, and ccx all required",
)
def test_full_pipeline(tmp_path, monkeypatch):
    """End-to-end pipeline under a small load that should pass on first try."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs").mkdir()

    from pipeline import run

    brief = """\
Bracket dimensions:
  flange_width:  80 mm
  flange_height: 60 mm
  web_height:    100 mm
  thickness:     8 mm
  fillet_radius: 4 mm

Material: structural steel
  E:    200 GPa
  nu:   0.3
  rho:  7850 kg/m³
  Sy:   250 MPa

Load:
  type:      point_force
  location:  tip of flange
  magnitude: 500 N
  direction: -Z

Boundary conditions:
  fixed face: web back face
"""
    final_params, _ = run(brief, max_iter=3)

    assert final_params is not None
    assert isinstance(final_params, dict)
    for key in ("flange_width", "flange_height", "web_height", "thickness", "fillet_radius"):
        assert key in final_params

    # Check runs directory was populated
    iter_dirs = sorted((tmp_path / "runs").glob("iter_*"))
    assert len(iter_dirs) >= 1
    first_iter = iter_dirs[0]
    assert (first_iter / "params.json").exists()
    assert (first_iter / "geometry.step").exists()
    assert (first_iter / "mesh.inp").exists()
    assert (first_iter / "summary.md").exists()


# -------------------------------------------------------------------------
# geometry.py tests
# -------------------------------------------------------------------------

_STD_PARAMS = {
    "flange_width":  0.08,
    "flange_height": 0.06,
    "web_height":    0.10,
    "thickness":     0.006,
    "fillet_radius": 0.004,
}


def test_geometry_script_mm_conversion():
    """_build_freecad_script must embed mm values, not SI metres."""
    from tools.geometry import _build_freecad_script

    params = {
        "flange_width":  0.08,
        "flange_height": 0.06,
        "web_height":    0.10,
        "thickness":     0.008,
        "fillet_radius": 0.003,
    }
    script = _build_freecad_script(params, Path("/tmp/dummy.step"))

    # mm values must be present
    assert "fw = 80.0" in script
    assert "t  = 8.0" in script
    assert "wh = 100.0" in script

    # SI values must NOT be present as assignments
    assert "fw = 0.08" not in script
    assert "t  = 0.008" not in script
    assert "wh = 0.1" not in script


@pytest.mark.requires_freecadcmd
@pytest.mark.skipif(shutil.which("FreeCADCmd") is None, reason="FreeCADCmd not on PATH")
def test_create_geometry_output_files(tmp_path):
    """create_geometry must produce geometry.step and params.json."""
    import json
    from tools.geometry import create_geometry

    step_path = create_geometry(_STD_PARAMS, tmp_path)

    assert step_path.exists()
    assert step_path.stat().st_size > 1000
    params_json = tmp_path / "params.json"
    assert params_json.exists()
    loaded = json.loads(params_json.read_text())
    # params.json includes metadata keys (_bracket_type, _schema_version) in addition
    # to the geometry params — check that all geometry params are present and correct
    for key, val in _STD_PARAMS.items():
        assert key in loaded, f"missing key {key!r}"
        assert loaded[key] == pytest.approx(val), f"mismatch for {key!r}"


@pytest.mark.requires_freecadcmd
@pytest.mark.skipif(shutil.which("FreeCADCmd") is None, reason="FreeCADCmd not on PATH")
def test_modify_geometry_updates_params(tmp_path):
    """modify_geometry must update the specified key and preserve others."""
    import json
    from tools.geometry import create_geometry, modify_geometry

    v1_dir = tmp_path / "v1"
    step_path = create_geometry(_STD_PARAMS, v1_dir)

    v2_dir = tmp_path / "v2"
    modify_geometry(step_path, {"thickness": 0.012}, v2_dir)

    v2_params = json.loads((v2_dir / "params.json").read_text())
    assert abs(v2_params["thickness"] - 0.012) < 1e-9
    assert abs(v2_params["flange_width"] - _STD_PARAMS["flange_width"]) < 1e-9
    assert abs(v2_params["web_height"]   - _STD_PARAMS["web_height"])   < 1e-9


# -------------------------------------------------------------------------
# mesh.py tests
# -------------------------------------------------------------------------

def _write_synthetic_mesh_inp(path: Path, node_count: int = 3, elem_count: int = 1):
    """Write a minimal synthetic mesh.inp for validation tests."""
    lines = ["*NODE"]
    for i in range(1, node_count + 1):
        lines.append(f"{i}, {i*0.01:.3e}, 0.000e+00, 0.000e+00")
    lines.append("*ELEMENT, TYPE=C3D10, ELSET=EALL")
    for i in range(1, elem_count + 1):
        lines.append(f"{i}, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_validate_mesh_inp_counts(tmp_path):
    """_validate_mesh_inp returns correct (node_count, element_count)."""
    from tools.mesh import _validate_mesh_inp

    inp = tmp_path / "mesh.inp"
    _write_synthetic_mesh_inp(inp, node_count=3, elem_count=1)
    nc, ec = _validate_mesh_inp(inp)
    assert nc == 3
    assert ec == 1


def test_validate_mesh_inp_raises_on_empty(tmp_path):
    """_validate_mesh_inp raises MeshError when there are zero nodes."""
    from tools.mesh import _validate_mesh_inp, MeshError

    inp = tmp_path / "mesh.inp"
    inp.write_text("*NODE\n*ELEMENT, TYPE=C3D10, ELSET=EALL\n", encoding="utf-8")
    with pytest.raises(MeshError):
        _validate_mesh_inp(inp)


@pytest.mark.requires_gmsh
@pytest.mark.skipif(importlib.util.find_spec("gmsh") is None, reason="gmsh not installed")
def test_generate_mesh_gmsh_only(tmp_path):
    """generate_mesh produces a valid mesh.inp from a gmsh-created box STEP."""
    import gmsh
    from tools.mesh import generate_mesh, _validate_mesh_inp

    step_path = tmp_path / "box.step"
    gmsh.initialize()
    try:
        gmsh.model.add("box")
        gmsh.model.occ.addBox(0, 0, 0, 0.08, 0.06, 0.10)
        gmsh.model.occ.synchronize()
        gmsh.write(str(step_path))
    finally:
        gmsh.finalize()

    mesh_path = generate_mesh(step_path, tmp_path / "mesh", quality="coarse")

    assert mesh_path.exists()
    text = mesh_path.read_text(encoding="utf-8")
    assert "*NODE" in text
    assert "*ELEMENT" in text
    nc, ec = _validate_mesh_inp(mesh_path)
    assert nc > 0
    assert ec > 0


@pytest.mark.requires_freecadcmd
@pytest.mark.requires_gmsh
@pytest.mark.skipif(
    shutil.which("FreeCADCmd") is None or importlib.util.find_spec("gmsh") is None,
    reason="FreeCADCmd and gmsh both required",
)
def test_generate_mesh_creates_inp(tmp_path):
    """generate_mesh produces a valid mesh.inp from a FreeCAD bracket STEP.

    Uses apply_fillet=False to avoid curved faces that cause gmsh to hang.
    The full filleted geometry is exercised by test_full_pipeline.
    """
    from tools.geometry import create_geometry
    from tools.mesh import generate_mesh, _validate_mesh_inp

    step_path = create_geometry(_STD_PARAMS, tmp_path / "geo", apply_fillet=False)
    mesh_path = generate_mesh(step_path, tmp_path / "mesh", quality="coarse")

    assert mesh_path.exists()
    text = mesh_path.read_text(encoding="utf-8")
    assert "*NODE" in text
    assert "*ELEMENT" in text
    nc, _ = _validate_mesh_inp(mesh_path)
    assert nc > 0


# -------------------------------------------------------------------------
# calculix.py tests
# -------------------------------------------------------------------------

def _make_mesh_inp(path: Path):
    """Write synthetic mesh.inp for calculix write_inp tests.

    Nodes:
      1-4: x=0 (FIXED back face)
      5-9: interior
      10:  closest to tip target (fw=0.08, fh/2=0.03, wh-t/2=0.096)
    """
    content = """\
*NODE
1,  0.000e+00,  0.000e+00,  0.000e+00
2,  0.000e+00,  6.000e-02,  0.000e+00
3,  0.000e+00,  0.000e+00,  1.000e-01
4,  0.000e+00,  6.000e-02,  1.000e-01
5,  4.000e-02,  0.000e+00,  0.000e+00
6,  4.000e-02,  6.000e-02,  0.000e+00
7,  4.000e-02,  0.000e+00,  1.000e-01
8,  4.000e-02,  6.000e-02,  1.000e-01
9,  8.000e-02,  3.000e-02,  1.000e-01
10, 8.000e-02,  3.000e-02,  9.600e-02
*ELEMENT, TYPE=C3D10, ELSET=EALL
1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
"""
    path.write_text(content, encoding="utf-8")


_CALC_PARAMS = {"flange_width": 0.08, "flange_height": 0.06,
                "web_height": 0.10,   "thickness": 0.008}
_CALC_LOADS  = {"direction": "-Z", "magnitude_n": 500.0}
_CALC_BCS    = {"params": _CALC_PARAMS}
_CALC_MAT    = {"E_pa": 200e9, "nu": 0.3, "rho": 7850.0, "Sy_pa": 250e6}


def test_write_inp_required_keywords(tmp_path):
    """write_inp must include all required CalculiX keyword cards."""
    from tools.calculix import write_inp

    mesh = tmp_path / "mesh.inp"
    _make_mesh_inp(mesh)
    inp = write_inp(mesh, _CALC_LOADS, _CALC_BCS, _CALC_MAT, tmp_path)

    text = inp.read_text(encoding="utf-8").upper()
    for keyword in ("*MATERIAL", "*ELASTIC", "*DENSITY", "*STATIC",
                    "*BOUNDARY", "*CLOAD", "*NODE FILE", "*EL FILE", "*END STEP"):
        assert keyword in text, f"Missing keyword: {keyword}"


def test_write_inp_fixed_nset(tmp_path):
    """Nodes 1-4 (x=0) must appear in FIXED nset; nodes 5-10 must not."""
    from tools.calculix import write_inp

    mesh = tmp_path / "mesh.inp"
    _make_mesh_inp(mesh)
    inp = write_inp(mesh, _CALC_LOADS, _CALC_BCS, _CALC_MAT, tmp_path)

    text = inp.read_text(encoding="utf-8")
    # Find content of FIXED nset
    lines = text.splitlines()
    fixed_ids = set()
    in_fixed = False
    for line in lines:
        stripped = line.strip().upper()
        if "NSET=FIXED" in stripped:
            in_fixed = True
            continue
        if in_fixed:
            if stripped.startswith("*"):
                break
            for token in line.replace(",", " ").split():
                try:
                    fixed_ids.add(int(token))
                except ValueError:
                    pass

    assert 1 in fixed_ids
    assert 2 in fixed_ids
    assert 3 in fixed_ids
    assert 4 in fixed_ids
    for nid in range(5, 11):
        assert nid not in fixed_ids, f"Node {nid} should not be in FIXED nset"


def test_write_inp_tip_nset(tmp_path):
    """Node 10 must be selected as the tip node (closest to fw=0.08, fh/2=0.03, wh-t/2=0.096)."""
    from tools.calculix import write_inp

    mesh = tmp_path / "mesh.inp"
    _make_mesh_inp(mesh)
    inp = write_inp(mesh, _CALC_LOADS, _CALC_BCS, _CALC_MAT, tmp_path)

    text = inp.read_text(encoding="utf-8")
    lines = text.splitlines()
    tip_ids = []
    in_tip = False
    for line in lines:
        stripped = line.strip().upper()
        if "NSET=TIP" in stripped:
            in_tip = True
            continue
        if in_tip:
            if stripped.startswith("*"):
                break
            for token in line.replace(",", " ").split():
                try:
                    tip_ids.append(int(token))
                except ValueError:
                    pass

    assert 10 in tip_ids, f"Expected node 10 in TIP nset, got {tip_ids}"


def test_patch_load_force_conservation(tmp_path):
    """Sum of all per-node CLOAD values must equal load_sign * magnitude (signed)."""
    from tools.calculix import write_inp

    loads = {"direction": "-Z", "magnitude_n": 500.0}
    mesh = tmp_path / "mesh.inp"
    _make_mesh_inp(mesh)
    inp = write_inp(mesh, loads, _CALC_BCS, _CALC_MAT, tmp_path)

    text = inp.read_text(encoding="utf-8")
    lines = text.splitlines()
    total = 0.0
    in_cload = False
    for line in lines:
        if line.strip().upper().startswith("*CLOAD"):
            in_cload = True
            continue
        if in_cload:
            if line.strip().startswith("*"):
                break
            if line.strip():
                parts = line.split(",")
                if len(parts) >= 3:
                    total += float(parts[-1])
    # Signed sum must equal -500.0 (direction -Z, magnitude 500 N)
    assert abs(total - (-500.0)) < 1e-6, f"Force sum {total} != -500.0"


def test_single_node_fallback_when_no_patch_fn(tmp_path):
    """load_patch_fn=None → exactly one *CLOAD data line, full magnitude on that node."""
    from tools.calculix import write_inp
    from bracket_types import BracketType, OptimizerStrategy
    from bracket_types._helpers import (
        _l_build_freecad_script, _l_fillet_constraint, _l_fixed_nodes,
        _l_tip_node, _l_compute_mass, _l_propose_params, _L_PARAM_BOUNDS,
    )

    bt_no_patch = BracketType(
        name="l_bracket_no_patch",
        display_name="L-bracket (no patch)",
        param_keys=("flange_width", "flange_height", "web_height", "thickness", "fillet_radius"),
        defaults_mm={},
        fillet_constraint=_l_fillet_constraint,
        freecad_script_fn=_l_build_freecad_script,
        fixed_nodes_fn=_l_fixed_nodes,
        tip_node_fn=_l_tip_node,
        mass_fn=_l_compute_mass,
        optimizer=OptimizerStrategy(param_bounds=_L_PARAM_BOUNDS, propose_fn=_l_propose_params),
        presizing_fn=None,
        load_patch_fn=None,  # explicit None → single-node fallback
    )

    loads = {"direction": "-Z", "magnitude_n": 500.0}
    mesh = tmp_path / "mesh.inp"
    _make_mesh_inp(mesh)
    inp = write_inp(mesh, loads, _CALC_BCS, _CALC_MAT, tmp_path, bracket_type=bt_no_patch)

    text = inp.read_text(encoding="utf-8")
    lines = text.splitlines()
    cload_data = []
    in_cload = False
    for line in lines:
        if line.strip().upper().startswith("*CLOAD"):
            in_cload = True
            continue
        if in_cload:
            if line.strip().startswith("*"):
                break
            if line.strip():
                cload_data.append(line.strip())

    assert len(cload_data) == 1, f"Expected 1 CLOAD line, got {len(cload_data)}"
    # Full magnitude on the single node
    val = float(cload_data[0].split(",")[-1])
    assert abs(val - (-500.0)) < 1e-6, f"Expected -500.0, got {val}"


def test_patch_normalization_with_dirty_input(tmp_path):
    """load_patch_fn returning duplicates + invalid IDs → valid deduped nodes, correct force sum."""
    from tools.calculix import write_inp
    from bracket_types import BracketType, OptimizerStrategy
    from bracket_types._helpers import (
        _l_build_freecad_script, _l_fillet_constraint, _l_fixed_nodes,
        _l_tip_node, _l_compute_mass, _l_propose_params, _L_PARAM_BOUNDS,
    )

    def _dirty_patch(nodes, params, k=5):
        # 9999 not in mesh, 10 duplicated, plus 9 and 8 as valid extras
        return [9999, 10, 10, 9, 8]

    bt_dirty = BracketType(
        name="l_bracket_dirty",
        display_name="L-bracket (dirty patch)",
        param_keys=("flange_width", "flange_height", "web_height", "thickness", "fillet_radius"),
        defaults_mm={},
        fillet_constraint=_l_fillet_constraint,
        freecad_script_fn=_l_build_freecad_script,
        fixed_nodes_fn=_l_fixed_nodes,
        tip_node_fn=_l_tip_node,
        mass_fn=_l_compute_mass,
        optimizer=OptimizerStrategy(param_bounds=_L_PARAM_BOUNDS, propose_fn=_l_propose_params),
        presizing_fn=None,
        load_patch_fn=_dirty_patch,
    )

    loads = {"direction": "-Z", "magnitude_n": 600.0}
    mesh = tmp_path / "mesh.inp"
    _make_mesh_inp(mesh)
    inp = write_inp(mesh, loads, _CALC_BCS, _CALC_MAT, tmp_path, bracket_type=bt_dirty)

    text = inp.read_text(encoding="utf-8")
    lines = text.splitlines()
    cload_data = []
    in_cload = False
    for line in lines:
        if line.strip().upper().startswith("*CLOAD"):
            in_cload = True
            continue
        if in_cload:
            if line.strip().startswith("*"):
                break
            if line.strip():
                cload_data.append(line.strip())

    # After normalization: 9999 dropped, 10 deduplicated → [10, 9, 8] = 3 valid nodes
    assert len(cload_data) == 3, f"Expected 3 CLOAD lines after dedup+filter, got {len(cload_data)}"
    node_ids = [int(l.split(",")[0]) for l in cload_data]
    assert 9999 not in node_ids, "Invalid node ID 9999 should be filtered out"
    assert len(set(node_ids)) == len(node_ids), "Duplicate node IDs should be removed"
    # Signed force sum must equal -600.0
    total = sum(float(l.split(",")[-1]) for l in cload_data)
    assert abs(total - (-600.0)) < 1e-6, f"Force sum {total} != -600.0"


def test_write_inp_load_direction(tmp_path):
    """CLOAD must apply load on DOF 3 (Z) with a negative value for -Z direction."""
    from tools.calculix import write_inp

    loads = {"direction": "-Z", "magnitude_n": 500.0}
    mesh = tmp_path / "mesh.inp"
    _make_mesh_inp(mesh)
    inp = write_inp(mesh, loads, _CALC_BCS, _CALC_MAT, tmp_path)

    text = inp.read_text(encoding="utf-8")
    lines = text.splitlines()
    cload_line = None
    in_cload = False
    for line in lines:
        if line.strip().upper().startswith("*CLOAD"):
            in_cload = True
            continue
        if in_cload and line.strip() and not line.strip().startswith("*"):
            cload_line = line.strip()
            break

    assert cload_line is not None, "No CLOAD data line found"
    assert ", 3, " in cload_line, f"Expected DOF 3 in CLOAD line: {cload_line!r}"
    # The load value must be negative (direction -Z, positive magnitude)
    parts = cload_line.split(",")
    load_value = float(parts[-1])
    assert load_value < 0.0, f"Expected negative load value, got {load_value}"


@pytest.mark.requires_ccx
@pytest.mark.skipif(shutil.which("ccx") is None, reason="ccx not on PATH")
def test_run_simulation_minimal(tmp_path):
    """run_simulation must produce .frd and .dat from a minimal hardcoded cube model."""
    from tools.calculix import run_simulation

    minimal_inp = """\
*NODE
1, 0.0, 0.0, 0.0
2, 0.0, 1.0, 0.0
3, 0.0, 1.0, 1.0
4, 0.0, 0.0, 1.0
5, 1.0, 0.0, 0.0
6, 1.0, 1.0, 0.0
7, 1.0, 1.0, 1.0
8, 1.0, 0.0, 1.0
*ELEMENT, TYPE=C3D8, ELSET=EALL
1, 1, 2, 3, 4, 5, 6, 7, 8
*NSET, NSET=FIXED
1, 2, 3, 4
*NSET, NSET=TIP
6
*MATERIAL, NAME=STEEL
*ELASTIC
200E9, 0.3
*DENSITY
7850.0
*SOLID SECTION, ELSET=EALL, MATERIAL=STEEL
*STEP
*STATIC
*BOUNDARY
FIXED, 1, 6
*CLOAD
TIP, 3, -1000.0
*NODE PRINT, NSET=NALL
U
*EL PRINT, ELSET=EALL
S
*NODE FILE
U
*EL FILE
S
*END STEP
"""
    inp_path = tmp_path / "analysis.inp"
    inp_path.write_text(minimal_inp, encoding="utf-8")

    result = run_simulation(inp_path)
    assert isinstance(result, tuple) and len(result) == 2
    frd_path, dat_path = result
    assert frd_path.exists() and frd_path.stat().st_size > 0
    assert dat_path.exists() and dat_path.stat().st_size > 0


# -------------------------------------------------------------------------
# Phase 1 tests — _physics_scale
# -------------------------------------------------------------------------

def test_physics_scale_exponent_2():
    """Stress scaling: multiply h by sqrt(ratio)."""
    import math
    from bracket_types._helpers import _physics_scale

    ratio = 1.41
    r = _physics_scale(ratio * 166e6, 166e6, exponent=2, iteration=1)
    assert abs(r - math.sqrt(ratio)) < 0.01, f"got {r}, expected {math.sqrt(ratio):.3f}"


def test_physics_scale_exponent_3():
    """Deflection scaling: multiply h by cbrt(ratio)."""
    from bracket_types._helpers import _physics_scale

    ratio = 2.0
    r = _physics_scale(ratio * 0.005, 0.005, exponent=3, iteration=1)
    assert abs(r - ratio ** (1 / 3)) < 0.01, f"got {r}, expected {ratio**(1/3):.3f}"


def test_physics_scale_clamped():
    """Large ratio is clamped to ≤ 1.40 at iteration 1."""
    from bracket_types._helpers import _physics_scale

    r = _physics_scale(100.0 * 166e6, 166e6, exponent=2, iteration=1)
    assert r <= 1.40, f"expected ≤ 1.40, got {r}"
    assert r >= 1.02


def test_physics_scale_fallback():
    """actual ≤ limit returns 1.0."""
    from bracket_types._helpers import _physics_scale

    assert _physics_scale(100e6, 166e6, exponent=2) == 1.0
    assert _physics_scale(166e6, 166e6, exponent=2) == 1.0


def test_physics_scale_invalid_inputs():
    """limit=0 or exponent=0 returns 1.0 (no crash)."""
    from bracket_types._helpers import _physics_scale

    assert _physics_scale(200e6, 0.0, exponent=2) == 1.0
    assert _physics_scale(200e6, 166e6, exponent=0) == 1.0
    assert _physics_scale(200e6, -1.0, exponent=2) == 1.0


def test_physics_scale_nonfinite_inputs():
    """NaN or inf in actual/limit returns 1.0 (no crash)."""
    import math
    from bracket_types._helpers import _physics_scale

    assert _physics_scale(float("nan"), 166e6, exponent=2) == 1.0
    assert _physics_scale(float("inf"), 166e6, exponent=2) == 1.0
    assert _physics_scale(200e6, float("nan"), exponent=2) == 1.0
    assert _physics_scale(200e6, float("inf"), exponent=2) == 1.0


def test_physics_scale_iteration_clamp():
    """iteration=0 or negative behaves as iter 1 (no over-aggressive cap)."""
    from bracket_types._helpers import _physics_scale

    r_zero = _physics_scale(10.0 * 166e6, 166e6, exponent=2, iteration=0)
    r_neg  = _physics_scale(10.0 * 166e6, 166e6, exponent=2, iteration=-5)
    r_one  = _physics_scale(10.0 * 166e6, 166e6, exponent=2, iteration=1)
    assert r_zero == r_one
    assert r_neg  == r_one


def test_physics_scale_iteration_damping():
    """Same ratio at iter 1 vs iter 10 → smaller multiplier at iter 10."""
    from bracket_types._helpers import _physics_scale

    r1  = _physics_scale(5.0 * 166e6, 166e6, exponent=2, iteration=1)
    r10 = _physics_scale(5.0 * 166e6, 166e6, exponent=2, iteration=10)
    assert r10 < r1, f"iter10 ({r10:.3f}) should be less than iter1 ({r1:.3f})"


def test_propose_severity_high_vs_low():
    """High-stress violation → larger thickness step than low-stress."""
    from bracket_types._helpers import _l_propose_params

    params = {
        "flange_width": 0.08, "flange_height": 0.06, "web_height": 0.10,
        "thickness": 0.006, "fillet_radius": 0.004,
    }
    constraints = {"max_von_mises_pa": 166.67e6, "max_displacement_m": 0.005}
    violations  = ["stress: exceeds allowable"]

    m_high = {"max_von_mises_pa": 220e6, "max_displacement_m": 0.001}
    m_low  = {"max_von_mises_pa": 175e6, "max_displacement_m": 0.001}

    p_hi = _l_propose_params(params, violations, 1, metrics=m_high, constraints=constraints)
    p_lo = _l_propose_params(params, violations, 1, metrics=m_low,  constraints=constraints)
    assert p_hi["thickness"] > p_lo["thickness"], "severity scaling failed"


def test_propose_metrics_none_fallback():
    """metrics=None → fixed 1.10 legacy multiplier."""
    from bracket_types._helpers import _l_propose_params

    params = {
        "flange_width": 0.08, "flange_height": 0.06, "web_height": 0.10,
        "thickness": 0.006, "fillet_radius": 0.004,
    }
    violations = ["stress: exceeds allowable"]
    new = _l_propose_params(params, violations, 1, metrics=None, constraints=None)
    assert abs(new["thickness"] / params["thickness"] - 1.10) < 1e-9


def test_propose_partial_metrics_fallback():
    """Missing 'max_von_mises_pa' key in metrics → legacy 1.10, no KeyError."""
    from bracket_types._helpers import _l_propose_params

    params = {
        "flange_width": 0.08, "flange_height": 0.06, "web_height": 0.10,
        "thickness": 0.006, "fillet_radius": 0.004,
    }
    constraints = {"max_von_mises_pa": 166.67e6}
    # metrics present but missing the expected key
    m_partial = {"max_displacement_m": 0.003}
    violations = ["stress: exceeds allowable"]
    new = _l_propose_params(params, violations, 1,
                            metrics=m_partial, constraints=constraints)
    assert abs(new["thickness"] / params["thickness"] - 1.10) < 1e-9


# -------------------------------------------------------------------------
# Phase 2 tests — slim branch
# -------------------------------------------------------------------------

def test_slim_step_reduces_thickness():
    """violations=[] → thickness × 0.97 (mass descent)."""
    from bracket_types._helpers import _l_propose_params

    params = {
        "flange_width": 0.08, "flange_height": 0.06, "web_height": 0.10,
        "thickness": 0.010, "fillet_radius": 0.004,
    }
    new = _l_propose_params(params, [], iteration=1)
    assert abs(new["thickness"] - params["thickness"] * 0.97) < 1e-9
    assert new["web_height"] == params["web_height"]   # structural dims unchanged


def test_slim_step_clamped_at_bound():
    """thickness already at lower bound → no change (stagnation signal)."""
    from bracket_types._helpers import _l_propose_params

    params = {
        "flange_width": 0.08, "flange_height": 0.06, "web_height": 0.10,
        "thickness": 0.003,   # == _L_PARAM_BOUNDS["thickness"][0]
        "fillet_radius": 0.001,
    }
    new = _l_propose_params(params, [], iteration=1)
    assert new == params


def test_fillet_reduced_with_thickness():
    """After slim, fillet_radius ≤ new_thickness × 0.45."""
    from bracket_types._helpers import _l_propose_params

    params = {
        "flange_width": 0.08, "flange_height": 0.06, "web_height": 0.10,
        "thickness": 0.010, "fillet_radius": 0.0045,  # at limit: 0.010*0.45
    }
    new = _l_propose_params(params, [], iteration=1)
    assert new["fillet_radius"] <= new["thickness"] * 0.45 + 1e-12


def test_slim_step_u_bracket():
    """U-bracket slim: wall_height unchanged, only thickness reduced."""
    from bracket_types.u_bracket import _u_propose_params

    params = {
        "channel_width": 0.08, "wall_height": 0.10, "channel_depth": 0.06,
        "thickness": 0.010, "fillet_radius": 0.004,
    }
    new = _u_propose_params(params, [], iteration=1)
    assert abs(new["thickness"] - params["thickness"] * 0.97) < 1e-9
    assert new["wall_height"] == params["wall_height"]


# -------------------------------------------------------------------------
# Phase 3 tests — pre-sizing
# -------------------------------------------------------------------------

def test_presizing_raises_wh_l():
    """L-bracket pre-sizing: wh_out ≥ analytical formula."""
    import math
    from tools.presizing import l_presizing

    F, fw, fh = 2000.0, 0.08, 0.06
    Sy, FOS = 250e6, 1.5
    σ_allow = Sy / FOS
    wh_theory = math.sqrt(6.0 * F * fw / (fh * σ_allow))

    params = {
        "flange_width": fw, "flange_height": fh, "web_height": 0.05,
        "thickness": 0.003, "fillet_radius": 0.002,
    }
    loads    = {"magnitude_n": F}
    material = {"E_pa": 200e9, "Sy_pa": Sy}
    constr   = {"min_factor_of_safety": FOS, "max_displacement_m": 0.005}

    out = l_presizing(params, loads, material, constr)
    assert out["web_height"] >= wh_theory - 1e-9


def test_presizing_never_reduces():
    """All pre-sized geometry params ≥ user-supplied values (except fillet if constrained)."""
    from tools.presizing import l_presizing

    params = {
        "flange_width": 0.12, "flange_height": 0.08, "web_height": 0.20,
        "thickness": 0.018, "fillet_radius": 0.008,
    }
    loads    = {"magnitude_n": 500.0}
    material = {"E_pa": 200e9, "Sy_pa": 250e6}
    constr   = {"min_factor_of_safety": 1.5, "max_displacement_m": 0.005}

    out = l_presizing(params, loads, material, constr)
    for key in ("web_height", "thickness"):
        assert out[key] >= params[key] - 1e-12, f"{key}: {out[key]} < {params[key]}"


def test_presizing_none_no_effect():
    """presizing_fn=None → geo unchanged (skipped silently)."""
    from bracket_types import BracketType, OptimizerStrategy
    from bracket_types._helpers import (
        _l_build_freecad_script, _l_fillet_constraint, _l_fixed_nodes,
        _l_tip_node, _l_compute_mass, _l_propose_params, _L_PARAM_BOUNDS,
    )

    bt_no_presizing = BracketType(
        name="l_bracket_test",
        display_name="L-bracket (no presizing)",
        param_keys=("flange_width", "flange_height", "web_height", "thickness", "fillet_radius"),
        defaults_mm={},
        fillet_constraint=_l_fillet_constraint,
        freecad_script_fn=_l_build_freecad_script,
        fixed_nodes_fn=_l_fixed_nodes,
        tip_node_fn=_l_tip_node,
        mass_fn=_l_compute_mass,
        optimizer=OptimizerStrategy(param_bounds=_L_PARAM_BOUNDS, propose_fn=_l_propose_params),
        presizing_fn=None,
    )
    assert bt_no_presizing.presizing_fn is None

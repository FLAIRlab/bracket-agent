"""
Tests for the bracket_types registry, individual bracket type implementations,
and pipeline integration (Phase 0 hardening + Phase 1-3 threading).

All tests in this file run without FreeCAD, Gmsh, or CalculiX.
"""

import math
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from bracket_types import KNOWN_TYPE_NAMES, REGISTRY, BracketType, get_type
from bracket_types._helpers import (
    _l_compute_mass,
    _l_propose_params,
    _l_fixed_nodes,
    _l_tip_node,
    _L_PARAM_BOUNDS,
)
from bracket_types.l_bracket import L_BRACKET
from bracket_types.t_bracket import T_BRACKET, _t_compute_mass, _t_fixed_nodes, _t_tip_node, _t_load_patch
from bracket_types.u_bracket import U_BRACKET, _u_compute_mass, _u_fixed_nodes, _u_tip_node, _u_load_patch
from constraints import CONSTRAINTS, evaluate_constraints, _compute_mass
from optimizer import propose_params, PARAM_BOUNDS
from pipeline import parse_brief
import agent_tools


# =============================================================================
# TestRegistry
# =============================================================================

class TestRegistry:
    def test_all_types_importable(self):
        assert "l_bracket" in REGISTRY
        assert "t_bracket" in REGISTRY
        assert "u_bracket" in REGISTRY

    def test_registry_complete(self):
        assert set(REGISTRY.keys()) == {"l_bracket", "t_bracket", "u_bracket"}

    def test_get_type_l(self):
        bt = get_type("l_bracket")
        assert isinstance(bt, BracketType)
        assert bt.name == "l_bracket"

    def test_get_type_t(self):
        bt = get_type("t_bracket")
        assert isinstance(bt, BracketType)
        assert bt.name == "t_bracket"

    def test_get_type_u(self):
        bt = get_type("u_bracket")
        assert isinstance(bt, BracketType)
        assert bt.name == "u_bracket"

    def test_get_type_unknown_raises(self):
        with pytest.raises(ValueError) as exc_info:
            get_type("x_bracket")
        msg = str(exc_info.value)
        assert "x_bracket" in msg
        assert "l_bracket" in msg    # Allowed list mentioned

    def test_known_type_names(self):
        assert KNOWN_TYPE_NAMES == frozenset({"l_bracket", "t_bracket", "u_bracket"})

    def test_all_types_have_required_fields(self):
        for name, bt in REGISTRY.items():
            assert bt.name == name
            assert bt.display_name
            assert len(bt.param_keys) >= 5
            assert bt.defaults_mm
            assert callable(bt.fillet_constraint)
            assert callable(bt.freecad_script_fn)
            assert callable(bt.fixed_nodes_fn)
            assert callable(bt.tip_node_fn)
            assert callable(bt.mass_fn)
            assert bt.optimizer is not None
            assert callable(bt.optimizer.propose_fn)
            assert isinstance(bt.optimizer.param_bounds, dict)


# =============================================================================
# TestLBracketRegression — pre-migration parity
# =============================================================================

class TestLBracketRegression:
    """Verify _l_compute_mass matches old _compute_mass for 5 param sets."""

    _CASES = [
        {"flange_width": 0.08, "flange_height": 0.06, "web_height": 0.10,
         "thickness": 0.006, "fillet_radius": 0.004},
        {"flange_width": 0.12, "flange_height": 0.08, "web_height": 0.15,
         "thickness": 0.010, "fillet_radius": 0.004},
        {"flange_width": 0.04, "flange_height": 0.03, "web_height": 0.05,
         "thickness": 0.003, "fillet_radius": 0.002},
        {"flange_width": 0.20, "flange_height": 0.15, "web_height": 0.25,
         "thickness": 0.020, "fillet_radius": 0.009},
        {"flange_width": 0.10, "flange_height": 0.07, "web_height": 0.12,
         "thickness": 0.008, "fillet_radius": 0.003},
    ]
    _RHO = 7850.0

    @pytest.mark.parametrize("params", _CASES)
    def test_mass_matches_old_formula(self, params):
        old_mass = _compute_mass(params, self._RHO)
        new_mass = _l_compute_mass(params, self._RHO)
        assert abs(old_mass - new_mass) < 1e-12

    def test_optimizer_stress_violation_matches(self):
        params = {"flange_width": 0.08, "flange_height": 0.06, "web_height": 0.10,
                  "thickness": 0.006, "fillet_radius": 0.004}
        violations = ["stress: too high"]
        old = propose_params(params, violations, 1)
        new = _l_propose_params(params, violations, 1)
        assert old == new

    def test_optimizer_disp_violation_matches(self):
        params = {"flange_width": 0.08, "flange_height": 0.06, "web_height": 0.10,
                  "thickness": 0.006, "fillet_radius": 0.004}
        violations = ["displacement: too large"]
        old = propose_params(params, violations, 1)
        new = _l_propose_params(params, violations, 1)
        assert old == new

    def test_optimizer_mass_violation_matches(self):
        params = {"flange_width": 0.08, "flange_height": 0.06, "web_height": 0.10,
                  "thickness": 0.006, "fillet_radius": 0.004}
        violations = ["mass: over budget"]
        old = propose_params(params, violations, 1)
        new = _l_propose_params(params, violations, 1)
        assert old == new

    def test_param_bounds_unchanged(self):
        assert PARAM_BOUNDS is _L_PARAM_BOUNDS

    def test_t_bracket_reuses_l_optimizer(self):
        assert T_BRACKET.optimizer.propose_fn is _l_propose_params
        assert T_BRACKET.optimizer.param_bounds is _L_PARAM_BOUNDS


# =============================================================================
# TestLBracketFullLoopShape — golden output shape regression
# =============================================================================

class TestLBracketFullLoopShape:
    """Run one L-bracket brief through the pipeline with mocked solver."""

    BRIEF = """\
Bracket dimensions:
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
  magnitude: 500 N
  direction: -Z

Boundary conditions:
  fixed face: web back face
"""

    def test_evaluate_constraints_keys(self):
        """evaluate_constraints returns the expected keys for a passing result."""
        metrics = {
            "max_von_mises_pa":   100e6,
            "max_displacement_m": 0.002,
            "node_count":         100,
            "params": {
                "flange_width":  0.08, "flange_height": 0.06,
                "web_height":    0.10, "thickness":     0.006,
                "fillet_radius": 0.004,
            },
            "rho":   7850.0,
            "Sy_pa": 250e6,
        }
        result = evaluate_constraints(metrics, CONSTRAINTS, bracket_type=L_BRACKET)
        assert "pass" in result
        assert "violations" in result
        assert "mass_kg" in result
        assert "fos" in result
        assert "stress_utilisation" in result
        assert result["pass"] is True
        assert result["violations"] == []

    def test_violation_prefix_values(self):
        """Violation strings always start with allowed prefixes."""
        allowed = {"stress:", "fos:", "displacement:", "mass:"}
        metrics = {
            "max_von_mises_pa":   300e6,   # exceeds limit
            "max_displacement_m": 0.010,   # exceeds limit
            "node_count":         100,
            "params": {
                "flange_width":  0.08, "flange_height": 0.06,
                "web_height":    0.10, "thickness":     0.006,
                "fillet_radius": 0.004,
            },
            "rho":   7850.0,
            "Sy_pa": 250e6,
        }
        result = evaluate_constraints(metrics, CONSTRAINTS, bracket_type=L_BRACKET)
        assert not result["pass"]
        for v in result["violations"]:
            assert any(v.startswith(p) for p in allowed), \
                f"Violation {v!r} has unexpected prefix"


# =============================================================================
# TestTBracketMass
# =============================================================================

class TestTBracketMass:
    def test_known_dimensions(self):
        params = {
            "flange_width":  0.12, "flange_height": 0.06,
            "web_height":    0.10, "thickness":     0.006,
            "fillet_radius": 0.004,
        }
        rho = 7850.0
        fw, fh, wh, t = 0.12, 0.06, 0.10, 0.006
        expected = (t * fh * wh + fw * fh * t - t * fh * t) * rho
        assert abs(_t_compute_mass(params, rho) - expected) < 1e-10

    def test_mass_via_bracket_type_fn(self):
        params = {
            "flange_width":  0.12, "flange_height": 0.06,
            "web_height":    0.10, "thickness":     0.006,
            "fillet_radius": 0.004,
        }
        assert abs(T_BRACKET.mass_fn(params, 7850.0) - _t_compute_mass(params, 7850.0)) < 1e-12

    def test_t_wider_than_l_for_same_dims(self):
        """T-bracket has same mass formula as L-bracket for same 5 params."""
        params = {
            "flange_width":  0.08, "flange_height": 0.06,
            "web_height":    0.10, "thickness":     0.006,
            "fillet_radius": 0.004,
        }
        rho = 7850.0
        # L and T share the same mass formula (same v_web + v_flange - v_overlap)
        assert abs(_l_compute_mass(params, rho) - _t_compute_mass(params, rho)) < 1e-10


# =============================================================================
# TestUBracketMass
# =============================================================================

class TestUBracketMass:
    def test_known_dimensions(self):
        params = {
            "channel_width": 0.08, "wall_height": 0.10,
            "channel_depth": 0.06, "thickness":   0.006,
            "fillet_radius": 0.004,
        }
        rho = 7850.0
        cw, wh, cd, t = 0.08, 0.10, 0.06, 0.006
        expected = (cw * cd * t + 2 * t * cd * wh - 2 * t * cd * t) * rho
        assert abs(_u_compute_mass(params, rho) - expected) < 1e-10

    def test_mass_via_bracket_type_fn(self):
        params = {
            "channel_width": 0.08, "wall_height": 0.10,
            "channel_depth": 0.06, "thickness":   0.006,
            "fillet_radius": 0.004,
        }
        assert abs(U_BRACKET.mass_fn(params, 7850.0) - _u_compute_mass(params, 7850.0)) < 1e-12

    def test_positive_mass(self):
        params = {
            "channel_width": 0.10, "wall_height": 0.12,
            "channel_depth": 0.08, "thickness":   0.008,
            "fillet_radius": 0.003,
        }
        assert _u_compute_mass(params, 7850.0) > 0


# =============================================================================
# TestFixedNodeDetection
# =============================================================================

def _make_nodes_l():
    """Synthetic L-bracket node set: web at x=0, flange extends in +x."""
    nodes = {}
    nid = 1
    for x in [0.0, 0.006, 0.08]:
        for y in [0.0, 0.03, 0.06]:
            for z in [0.0, 0.05, 0.10]:
                nodes[nid] = (x, y, z)
                nid += 1
    return nodes


def _make_nodes_t():
    """Synthetic T-bracket node set: web centered, fixed at z=0."""
    nodes = {}
    nid = 1
    fw, t = 0.12, 0.006
    for x in [0.0, fw / 2 - t / 2, fw / 2 + t / 2, fw]:
        for y in [0.0, 0.03, 0.06]:
            for z in [0.0, 0.05, 0.10]:
                nodes[nid] = (x, y, z)
                nid += 1
    return nodes


def _make_nodes_u():
    """Synthetic U-bracket node set: base + two walls, fixed at z=0."""
    nodes = {}
    nid = 1
    cw, t = 0.08, 0.006
    for x in [0.0, t, cw - t, cw]:
        for y in [0.0, 0.03, 0.06]:
            for z in [0.0, t, 0.05, 0.10]:
                nodes[nid] = (x, y, z)
                nid += 1
    return nodes


class TestFixedNodeDetection:
    def test_l_fixed_nodes_at_x0(self):
        nodes = _make_nodes_l()
        params = {"flange_width": 0.08, "flange_height": 0.06,
                  "web_height": 0.10, "thickness": 0.006}
        fixed = _l_fixed_nodes(nodes, params)
        # All nodes with x=0 should be fixed
        expected = [nid for nid, (x, y, z) in nodes.items() if abs(x) < 1e-6]
        assert set(fixed) == set(expected)
        assert len(fixed) > 0

    def test_l_fixed_nodes_via_bracket_type(self):
        nodes = _make_nodes_l()
        params = {"flange_width": 0.08, "flange_height": 0.06,
                  "web_height": 0.10, "thickness": 0.006}
        fixed_direct = _l_fixed_nodes(nodes, params)
        fixed_via_bt = L_BRACKET.fixed_nodes_fn(nodes, params)
        assert set(fixed_direct) == set(fixed_via_bt)

    def test_t_fixed_nodes_at_z0(self):
        nodes = _make_nodes_t()
        params = {"flange_width": 0.12, "web_height": 0.10, "thickness": 0.006}
        fixed = _t_fixed_nodes(nodes, params)
        expected = [nid for nid, (x, y, z) in nodes.items() if abs(z) < 1e-6]
        assert set(fixed) == set(expected)
        assert len(fixed) > 0

    def test_u_fixed_nodes_at_z0(self):
        nodes = _make_nodes_u()
        params = {"channel_width": 0.08, "wall_height": 0.10, "thickness": 0.006}
        fixed = _u_fixed_nodes(nodes, params)
        expected = [nid for nid, (x, y, z) in nodes.items() if abs(z) < 1e-6]
        assert set(fixed) == set(expected)
        assert len(fixed) > 0


# =============================================================================
# TestTipNodeDetection
# =============================================================================

class TestTipNodeDetection:
    def test_l_tip_node_closest_to_flange_tip(self):
        nodes = _make_nodes_l()
        params = {"flange_width": 0.08, "flange_height": 0.06,
                  "web_height": 0.10, "thickness": 0.006}
        tip = _l_tip_node(nodes, params)
        # Target: (0.08, 0.03, 0.097)
        target = (0.08, 0.03, 0.097)
        tip_xyz = nodes[tip]
        # Verify it's the closest node to the target
        min_dist = min(math.dist(xyz, target) for xyz in nodes.values())
        assert math.dist(tip_xyz, target) == pytest.approx(min_dist, abs=1e-10)

    def test_t_tip_node_at_flange_center(self):
        nodes = _make_nodes_t()
        params = {"flange_width": 0.12, "flange_height": 0.06,
                  "web_height": 0.10, "thickness": 0.006}
        tip = _t_tip_node(nodes, params)
        # Target: (0.06, 0.03, 0.097)
        target = (0.06, 0.03, 0.097)
        tip_xyz = nodes[tip]
        min_dist = min(math.dist(xyz, target) for xyz in nodes.values())
        assert math.dist(tip_xyz, target) == pytest.approx(min_dist, abs=1e-10)

    def test_u_tip_node_top_left_wall(self):
        nodes = _make_nodes_u()
        params = {"channel_depth": 0.06, "wall_height": 0.10}
        tip = _u_tip_node(nodes, params)
        # Target: (0, 0.03, 0.10)
        target = (0.0, 0.03, 0.10)
        tip_xyz = nodes[tip]
        min_dist = min(math.dist(xyz, target) for xyz in nodes.values())
        assert math.dist(tip_xyz, target) == pytest.approx(min_dist, abs=1e-10)

    def test_u_tip_node_fallback_uses_span(self):
        """Without params, fallback cd = max_y - min_y; tip.y ≈ span/2."""
        nodes = _make_nodes_u()
        # call with no params — triggers fallback path
        tip = _u_tip_node(nodes, {})
        # y-span of _make_nodes_u() is 0..0.06 → span=0.06 → target y=0.03
        assert abs(nodes[tip][1] - 0.03) < 0.01

    def test_l_load_patch_validity(self):
        from bracket_types._helpers import _l_load_patch
        nodes = _make_nodes_l()
        params = {"flange_width": 0.08, "flange_height": 0.06,
                  "web_height": 0.10, "thickness": 0.006}
        patch = _l_load_patch(nodes, params, k=5)
        assert len(patch) == 5
        assert all(nid in nodes for nid in patch)
        assert len(set(patch)) == 5  # no duplicates

    def test_t_load_patch_validity(self):
        nodes = _make_nodes_t()
        params = {"flange_width": 0.12, "flange_height": 0.06,
                  "web_height": 0.10, "thickness": 0.006}
        patch = _t_load_patch(nodes, params, k=5)
        assert len(patch) == 5
        assert all(nid in nodes for nid in patch)
        assert len(set(patch)) == 5  # no duplicates

    def test_u_load_patch_validity(self):
        nodes = _make_nodes_u()
        params = {"channel_depth": 0.06, "wall_height": 0.10}
        patch = _u_load_patch(nodes, params, k=5)
        assert len(patch) == 5
        assert all(nid in nodes for nid in patch)
        assert len(set(patch)) == 5  # no duplicates

    def test_load_patch_fn_registered_on_all_types(self):
        """All built-in bracket types must have a callable load_patch_fn."""
        for name, bt in REGISTRY.items():
            assert callable(bt.load_patch_fn), \
                f"{name}.load_patch_fn is not callable"

    def test_load_patch_k1_returns_same_as_tip_node(self):
        """load_patch_fn with k=1 returns the same node as tip_node_fn."""
        from bracket_types._helpers import _l_load_patch
        nodes = _make_nodes_l()
        params = {"flange_width": 0.08, "flange_height": 0.06,
                  "web_height": 0.10, "thickness": 0.006}
        patch = _l_load_patch(nodes, params, k=1)
        tip = L_BRACKET.tip_node_fn(nodes, params)
        assert patch == [tip]


# =============================================================================
# TestBriefRoundtrip
# =============================================================================

class TestBriefRoundtrip:
    def test_l_brief_roundtrip(self):
        brief = agent_tools.build_brief({"load_n": 1000}, bracket_type_name="l_bracket")
        params, _ = parse_brief(brief)
        assert params.get("bracket_type_name", "l_bracket") == "l_bracket"
        assert "flange_width" in params

    def test_t_brief_roundtrip(self):
        brief = agent_tools.build_brief({"load_n": 1000}, bracket_type_name="t_bracket")
        params, _ = parse_brief(brief)
        assert params.get("bracket_type_name") == "t_bracket"
        assert "flange_width" in params

    def test_u_brief_roundtrip(self):
        brief = agent_tools.build_brief({"load_n": 500}, bracket_type_name="u_bracket")
        params, _ = parse_brief(brief)
        assert params.get("bracket_type_name") == "u_bracket"
        assert "channel_width" in params

    def test_l_brief_backward_compat_no_type_key(self):
        """A brief without bracket_type: resolves to l_bracket."""
        brief = """\
Bracket dimensions:
  flange_width:  80 mm
  flange_height: 60 mm
  web_height:    100 mm
  thickness:     6 mm
  fillet_radius: 4 mm

Material: structural steel
  E:    200 GPa
  nu:   0.3

Load:
  magnitude: 1000 N
  direction: -Z
"""
        params, _ = parse_brief(brief)
        assert params.get("bracket_type_name") is None


# =============================================================================
# TestMigration
# =============================================================================

class TestMigration:
    def test_old_brief_without_type_resolves_to_l(self):
        """Old briefs without bracket_type: → bracket_type_name absent → l_bracket."""
        brief = """\
Bracket dimensions:
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
  magnitude: 2000 N
  direction: -Z

Boundary conditions:
  fixed face: web back face
"""
        params, _ = parse_brief(brief)
        # bracket_type_name absent → pipeline defaults to l_bracket
        from bracket_types import get_type
        bt = get_type(params.get("bracket_type_name", "l_bracket"))
        assert bt.name == "l_bracket"


# =============================================================================
# TestParamsBracketTypeKey
# =============================================================================

class TestParamsBracketTypeKey:
    def test_missing_bracket_type_in_params_json_warns(self, tmp_path):
        """params.json without _bracket_type field → WARNING logged + treated as l_bracket."""
        import json
        import logging
        from unittest.mock import patch

        params_json = tmp_path / "params.json"
        step = tmp_path / "geometry.step"
        step.write_text("")

        params_json.write_text(json.dumps({
            "flange_width": 0.08, "flange_height": 0.06,
            "web_height": 0.10, "thickness": 0.006, "fillet_radius": 0.004,
        }), encoding="utf-8")

        # Verify _bracket_type is absent
        raw = json.loads(params_json.read_text())
        assert raw.get("_bracket_type") is None

        # Patch create_geometry to avoid running FreeCAD; verify warning is logged
        from tools.geometry import modify_geometry
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        with patch("tools.geometry.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            # Patch the step existence check so create_geometry doesn't fail
            with patch("pathlib.Path.exists", return_value=True):
                with patch("tools.geometry.subprocess.run") as mock_run2:
                    mock_run2.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
                    with self._capture_log("tools.geometry") as log_records:
                        try:
                            modify_geometry(step, {}, out_dir)
                        except Exception:
                            pass  # FreeCAD not available; we only care about the warning log

        # The warning about missing _bracket_type should have been logged
        warning_msgs = [r.getMessage() for r in log_records if r.levelno >= logging.WARNING]
        assert any("_bracket_type" in m or "pre-migration" in m for m in warning_msgs), \
            f"Expected _bracket_type warning, got: {warning_msgs}"

    @staticmethod
    def _capture_log(logger_name: str):
        """Context manager returning a list of LogRecord objects."""
        import logging

        class _Collector(logging.Handler):
            def __init__(self):
                super().__init__()
                self.records = []
            def emit(self, record):
                self.records.append(record)

        class _CM:
            def __enter__(self):
                self._h = _Collector()
                self._logger = logging.getLogger(logger_name)
                self._logger.addHandler(self._h)
                return self._h.records
            def __exit__(self, *args):
                self._logger.removeHandler(self._h)

        return _CM()

    def test_unknown_bracket_type_in_params_json_raises(self, tmp_path):
        """params.json with unknown _bracket_type → GeometryError on modify_geometry."""
        import json
        from tools.geometry import GeometryError, modify_geometry

        step = tmp_path / "geometry.step"
        step.write_text("")

        params_json = tmp_path / "params.json"
        params_json.write_text(json.dumps({
            "_schema_version": 1,
            "_bracket_type": "z_bracket",  # unknown
            "flange_width": 0.08,
        }), encoding="utf-8")

        with pytest.raises(GeometryError, match="z_bracket"):
            modify_geometry(step, {}, tmp_path / "out")


# =============================================================================
# TestStrictKeyValidation
# =============================================================================

class TestStrictKeyValidation:
    def test_unknown_bracket_type_raises_valueerror(self):
        brief = """\
Bracket dimensions:
  bracket_type: z_bracket
  flange_width: 80 mm
"""
        with pytest.raises(ValueError, match="z_bracket"):
            parse_brief(brief)


# =============================================================================
# TestPhase0MaxMassKg
# =============================================================================

class TestPhase0MaxMassKg:
    def test_max_mass_kg_set_from_brief(self):
        """constraints["max_mass_kg"] is populated when brief includes the key."""
        brief = """\
Bracket dimensions:
  flange_width:  80 mm
  flange_height: 60 mm
  web_height:    100 mm
  thickness:     6 mm
  fillet_radius: 4 mm

Material: structural steel
  E:    200 GPa
  nu:   0.3

Load:
  magnitude: 1000 N
  direction: -Z

Constraints:
  max_mass_kg: 2.5 kg
"""
        _, constraints = parse_brief(brief)
        assert constraints["max_mass_kg"] == pytest.approx(2.5)

    def test_max_mass_kg_is_none_when_absent(self):
        """constraints["max_mass_kg"] is None when brief omits the key."""
        brief = """\
Bracket dimensions:
  flange_width:  80 mm
  flange_height: 60 mm
  web_height:    100 mm
  thickness:     6 mm
  fillet_radius: 4 mm

Material: structural steel
  E:    200 GPa
  nu:   0.3

Load:
  magnitude: 1000 N
  direction: -Z
"""
        _, constraints = parse_brief(brief)
        assert constraints["max_mass_kg"] is None

    def test_max_mass_kg_always_present_in_constraints(self):
        """constraints always has max_mass_kg key regardless of brief content."""
        brief = """\
Bracket dimensions:
  flange_width: 80 mm
Load:
  magnitude: 500 N
"""
        _, constraints = parse_brief(brief)
        assert "max_mass_kg" in constraints


# =============================================================================
# TestPhase0MaterialDefaults
# =============================================================================

class TestPhase0MaterialDefaults:
    def test_e_defaults_to_200gpa(self):
        brief = """\
Bracket dimensions:
  flange_width: 80 mm
  thickness: 6 mm
Load:
  magnitude: 500 N
"""
        params, _ = parse_brief(brief)
        assert abs(params["material"]["E_pa"] - 200e9) < 1.0

    def test_nu_defaults_to_0_3(self):
        brief = """\
Bracket dimensions:
  flange_width: 80 mm
Load:
  magnitude: 500 N
"""
        params, _ = parse_brief(brief)
        assert abs(params["material"]["nu"] - 0.3) < 1e-9

    def test_rho_defaults_to_7850(self):
        brief = """\
Bracket dimensions:
  flange_width: 80 mm
Load:
  magnitude: 500 N
"""
        params, _ = parse_brief(brief)
        assert abs(params["material"]["rho"] - 7850.0) < 1e-6

    def test_sy_defaults_to_250mpa(self):
        brief = """\
Bracket dimensions:
  flange_width: 80 mm
Load:
  magnitude: 500 N
"""
        params, _ = parse_brief(brief)
        assert abs(params["material"]["Sy_pa"] - 250e6) < 1.0

    def test_explicit_values_not_overridden(self):
        brief = """\
Bracket dimensions:
  flange_width: 80 mm
Material: aluminium
  E:    70 GPa
  nu:   0.33
  rho:  2700 kg/m3
  Sy:   270 MPa
Load:
  magnitude: 500 N
"""
        params, _ = parse_brief(brief)
        assert abs(params["material"]["E_pa"] - 70e9) < 1.0
        assert abs(params["material"]["nu"] - 0.33) < 1e-9
        assert abs(params["material"]["rho"] - 2700.0) < 1e-6
        assert abs(params["material"]["Sy_pa"] - 270e6) < 1.0


# =============================================================================
# TestBuildBriefMultiType
# =============================================================================

class TestBuildBriefMultiType:
    def test_build_brief_l_default(self):
        brief = agent_tools.build_brief({"load_n": 1000})
        assert "bracket_type: l_bracket" in brief
        assert "flange_width:" in brief

    def test_build_brief_t(self):
        brief = agent_tools.build_brief({"load_n": 1000}, bracket_type_name="t_bracket")
        assert "bracket_type: t_bracket" in brief
        assert "flange_width:" in brief

    def test_build_brief_u(self):
        brief = agent_tools.build_brief({"load_n": 500}, bracket_type_name="u_bracket")
        assert "bracket_type: u_bracket" in brief
        assert "channel_width:" in brief
        assert "wall_height:" in brief
        assert "channel_depth:" in brief

    def test_build_brief_u_custom_dims(self):
        brief = agent_tools.build_brief(
            {"load_n": 300, "channel_width_mm": 100, "wall_height_mm": 120},
            bracket_type_name="u_bracket",
        )
        assert "channel_width: 100 mm" in brief
        assert "wall_height: 120 mm" in brief

    def test_build_brief_roundtrip_l(self):
        brief = agent_tools.build_brief(
            {"load_n": 2000, "flange_width_mm": 90, "thickness_mm": 8},
        )
        params, _ = parse_brief(brief)
        assert abs(params["flange_width"] - 0.090) < 1e-9
        assert abs(params["thickness"] - 0.008) < 1e-9

    def test_build_brief_roundtrip_u(self):
        brief = agent_tools.build_brief(
            {"load_n": 300, "channel_width_mm": 100, "wall_height_mm": 120},
            bracket_type_name="u_bracket",
        )
        params, _ = parse_brief(brief)
        assert params.get("bracket_type_name") == "u_bracket"
        assert abs(params["channel_width"] - 0.100) < 1e-9
        assert abs(params["wall_height"] - 0.120) < 1e-9


# =============================================================================
# TestFillet Constraint
# =============================================================================

class TestFilletConstraint:
    @pytest.mark.parametrize("bt_name", ["l_bracket", "t_bracket", "u_bracket"])
    def test_fillet_constraint_enforced(self, bt_name):
        bt = get_type(bt_name)
        if bt_name in ("l_bracket", "t_bracket"):
            params = {"thickness": 0.010, "fillet_radius": 0.008}
        else:
            params = {"thickness": 0.010, "fillet_radius": 0.008}
        max_fr = bt.fillet_constraint(params)
        assert abs(max_fr - 0.010 * 0.45) < 1e-12


# =============================================================================
# TestEvaluateConstraintsWithType
# =============================================================================

class TestEvaluateConstraintsWithType:
    """Verify evaluate_constraints works with each bracket type."""

    def _passing_metrics(self, params, type_name):
        return {
            "max_von_mises_pa":   100e6,
            "max_displacement_m": 0.001,
            "node_count":         200,
            "params":             params,
            "rho":                7850.0,
            "Sy_pa":              250e6,
        }

    def test_l_bracket_pass(self):
        params = {"flange_width": 0.08, "flange_height": 0.06, "web_height": 0.10,
                  "thickness": 0.006, "fillet_radius": 0.004}
        metrics = self._passing_metrics(params, "l_bracket")
        result = evaluate_constraints(metrics, CONSTRAINTS, bracket_type=L_BRACKET)
        assert result["pass"]
        assert result["mass_kg"] > 0

    def test_t_bracket_pass(self):
        params = {"flange_width": 0.12, "flange_height": 0.06, "web_height": 0.10,
                  "thickness": 0.006, "fillet_radius": 0.004}
        metrics = self._passing_metrics(params, "t_bracket")
        result = evaluate_constraints(metrics, CONSTRAINTS, bracket_type=T_BRACKET)
        assert result["pass"]
        assert result["mass_kg"] > 0

    def test_u_bracket_pass(self):
        params = {"channel_width": 0.08, "wall_height": 0.10, "channel_depth": 0.06,
                  "thickness": 0.006, "fillet_radius": 0.004}
        metrics = self._passing_metrics(params, "u_bracket")
        result = evaluate_constraints(metrics, CONSTRAINTS, bracket_type=U_BRACKET)
        assert result["pass"]
        assert result["mass_kg"] > 0

    def test_none_bracket_type_uses_l(self):
        """bracket_type=None defaults to L-bracket mass formula."""
        params = {"flange_width": 0.08, "flange_height": 0.06, "web_height": 0.10,
                  "thickness": 0.006, "fillet_radius": 0.004}
        metrics = self._passing_metrics(params, "l_bracket")
        result_l = evaluate_constraints(metrics, CONSTRAINTS, bracket_type=L_BRACKET)
        result_none = evaluate_constraints(metrics, CONSTRAINTS, bracket_type=None)
        assert abs(result_l["mass_kg"] - result_none["mass_kg"]) < 1e-12


# =============================================================================
# TestWriteInpEmptyFixedNodes
# =============================================================================

def test_write_inp_empty_fixed_nodes_raises(tmp_path):
    """write_inp must raise SimulationError when fixed_nodes_fn returns []."""
    from tools.calculix import write_inp, SimulationError
    from bracket_types import get_type
    bt = get_type("l_bracket")
    # Build a minimal mesh.inp with nodes only in x>0 region (no fixed nodes at x≈0)
    mesh = tmp_path / "mesh.inp"
    mesh.write_text(
        "*NODE\n1, 0.01, 0.0, 0.0\n2, 0.08, 0.03, 0.10\n"
        "*ELEMENT, TYPE=C3D10, ELSET=VOL\n1, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2\n",
        encoding="utf-8",
    )
    with pytest.raises(SimulationError, match="No fixed nodes"):
        write_inp(mesh, {"magnitude_n": 500, "direction": "-Z"},
                  {"params": {}},
                  {"E_pa": 200e9, "nu": 0.3, "rho": 7850, "Sy_pa": 250e6},
                  tmp_path, bracket_type=bt)

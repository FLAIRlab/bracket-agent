"""
Tests for the conversational agent layer (agent.py + agent_tools.py).

Tiers:
  T1 — Pure unit tests: no external tools, no API key, no pipeline execution.
       These always run.
  T2 — read_last_results: requires at least one completed run in runs/.
       Auto-skipped when runs/ is empty.
  T3 — Full end-to-end: requires ANTHROPIC_API_KEY or OPENAI_API_KEY plus
       FreeCAD + Gmsh + CalculiX. Not run by default.

Run T1 only (CI-safe):
  python -m pytest tests/test_agent.py -v -m "not requires_runs and not requires_api"

Run T1 + T2 (after a completed pipeline run):
  python -m pytest tests/test_agent.py -v -m "not requires_api"
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import agent
import agent_tools
from pipeline import parse_brief


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _runs_exist() -> bool:
    runs = Path("runs")
    return runs.exists() and any(runs.glob("iter_*"))


# ---------------------------------------------------------------------------
# T1 — build_brief
# ---------------------------------------------------------------------------

class TestBuildBrief:
    def test_roundtrip_load(self):
        brief = agent_tools.build_brief({"load_n": 2000})
        params, _ = parse_brief(brief)
        assert params["loads"]["magnitude_n"] == 2000.0

    def test_roundtrip_geometry(self):
        brief = agent_tools.build_brief({
            "load_n": 1500,
            "flange_width_mm": 90,
            "web_height_mm": 120,
            "thickness_mm": 8,
        })
        params, _ = parse_brief(brief)
        assert abs(params["flange_width"] - 0.090) < 1e-9
        assert abs(params["web_height"]   - 0.120) < 1e-9
        assert abs(params["thickness"]    - 0.008) < 1e-9

    def test_roundtrip_material(self):
        brief = agent_tools.build_brief({
            "load_n": 1000,
            "E_gpa": 70,
            "nu": 0.33,
            "rho": 2700,
            "Sy_mpa": 270,
        })
        params, constraints = parse_brief(brief)
        assert abs(params["material"]["E_pa"]  - 70e9)  < 1e3
        assert abs(params["material"]["Sy_pa"] - 270e6) < 1e3
        assert abs(constraints["max_von_mises_pa"] - 270e6 / 1.5) < 1

    def test_roundtrip_max_mass(self):
        brief = agent_tools.build_brief({"load_n": 500, "max_mass_kg": 1.5})
        _, constraints = parse_brief(brief)
        assert constraints["max_mass_kg"] == pytest.approx(1.5)

    def test_defaults_applied(self):
        brief = agent_tools.build_brief({"load_n": 2000})
        params, _ = parse_brief(brief)
        assert abs(params["flange_width"]  - 0.080) < 1e-9
        assert abs(params["flange_height"] - 0.060) < 1e-9
        assert abs(params["web_height"]    - 0.100) < 1e-9
        assert abs(params["thickness"]     - 0.006) < 1e-9
        assert abs(params["fillet_radius"] - 0.004) < 1e-9

    def test_missing_load_n_raises(self):
        with pytest.raises(KeyError):
            agent_tools.build_brief({})

    def test_no_max_mass_by_default(self):
        brief = agent_tools.build_brief({"load_n": 2000})
        assert "max_mass_kg" not in brief or "Constraints:" not in brief


# ---------------------------------------------------------------------------
# T1 — tool schema conversion
# ---------------------------------------------------------------------------

class TestToolSchemas:
    def test_anthropic_tools_has_input_schema(self):
        tools = agent._anthropic_tools()
        assert len(tools) == 3
        for t in tools:
            assert "input_schema" in t
            assert "name" in t
            assert "description" in t
            assert "parameters" not in t

    def test_openai_tools_has_function_wrapper(self):
        tools = agent._openai_tools()
        assert len(tools) == 3
        for t in tools:
            assert t["type"] == "function"
            assert "function" in t
            fn = t["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn

    def test_tool_names_match(self):
        anthropic_names = {t["name"] for t in agent._anthropic_tools()}
        openai_names    = {t["function"]["name"] for t in agent._openai_tools()}
        expected = {"run_pipeline", "modify_and_run", "read_last_results"}
        assert anthropic_names == expected
        assert openai_names == expected

    def test_run_pipeline_requires_load_n(self):
        for t in agent._anthropic_tools():
            if t["name"] == "run_pipeline":
                assert "load_n" in t["input_schema"]["required"]

    def test_modify_and_run_changes_is_optional(self):
        for t in agent._anthropic_tools():
            if t["name"] == "modify_and_run":
                schema = t["input_schema"]
                assert "changes" not in schema.get("required", [])
                assert "changes" in schema["properties"]
                assert schema["properties"]["changes"]["type"] == "object"


# ---------------------------------------------------------------------------
# T1 — dispatch_tool routing
# ---------------------------------------------------------------------------

class TestDispatchTool:
    def test_routes_run_pipeline(self):
        with patch.object(agent_tools, "run_pipeline_tool", return_value={"ok": 1}) as m:
            result = agent.dispatch_tool("run_pipeline", {"load_n": 1000})
        m.assert_called_once_with({"load_n": 1000})
        assert result == {"ok": 1}

    def test_routes_modify_and_run(self):
        with patch.object(agent_tools, "modify_and_run_tool", return_value={"ok": 2}) as m:
            result = agent.dispatch_tool("modify_and_run", {"changes": {"load_n": 2000}})
        m.assert_called_once_with({"changes": {"load_n": 2000}})
        assert result == {"ok": 2}

    def test_routes_read_last_results(self):
        with patch.object(agent_tools, "read_last_results_tool", return_value={"ok": 3}) as m:
            result = agent.dispatch_tool("read_last_results", {})
        m.assert_called_once_with({})
        assert result == {"ok": 3}

    def test_unknown_tool_returns_error(self):
        result = agent.dispatch_tool("nonexistent", {})
        assert "error" in result


# ---------------------------------------------------------------------------
# T1 — run_pipeline_tool input validation
# ---------------------------------------------------------------------------

class TestRunPipelineToolValidation:
    def test_missing_load_n_returns_error(self):
        result = agent_tools.run_pipeline_tool({})
        assert "error" in result

    def test_calls_pipeline_run_with_correct_load(self):
        mock_eval = {
            "pass": True, "violations": [], "mass_kg": 0.5,
            "fos": 2.0, "stress_utilisation": 0.4,
        }
        mock_params = {
            "flange_width": 0.08, "flange_height": 0.06,
            "web_height": 0.10, "thickness": 0.006, "fillet_radius": 0.004,
        }
        with patch("agent_tools.pipeline.run", return_value=(mock_params, mock_eval)) as mock_run, \
             patch("agent_tools._count_iter_dirs", side_effect=[0, 1]), \
             patch("agent_tools._last_iter_dir", return_value=None):
            result = agent_tools.run_pipeline_tool({"load_n": 3000})

        assert mock_run.called
        brief_used = mock_run.call_args[0][0]
        params, _ = parse_brief(brief_used)
        assert params["loads"]["magnitude_n"] == 3000.0

    def test_result_has_required_keys(self):
        mock_eval = {
            "pass": True, "violations": [], "mass_kg": 0.712,
            "fos": 1.62, "stress_utilisation": 0.617,
        }
        mock_params = {
            "flange_width": 0.08, "flange_height": 0.06,
            "web_height": 0.10, "thickness": 0.00726, "fillet_radius": 0.00327,
        }
        with patch("agent_tools.pipeline.run", return_value=(mock_params, mock_eval)), \
             patch("agent_tools._count_iter_dirs", side_effect=[0, 1]), \
             patch("agent_tools._last_iter_dir", return_value=None):
            result = agent_tools.run_pipeline_tool({"load_n": 2000})

        for key in ("status", "iterations_run", "mass_kg", "fos",
                    "max_vm_mpa", "max_disp_mm", "stress_utilisation_pct",
                    "violations", "final_params_mm", "output_dir"):
            assert key in result, f"missing key: {key}"

    def test_status_pass_when_pipeline_passes(self):
        mock_eval = {
            "pass": True, "violations": [], "mass_kg": 0.5,
            "fos": 2.0, "stress_utilisation": 0.4,
        }
        mock_params = {
            "flange_width": 0.08, "flange_height": 0.06,
            "web_height": 0.10, "thickness": 0.008, "fillet_radius": 0.003,
        }
        with patch("agent_tools.pipeline.run", return_value=(mock_params, mock_eval)), \
             patch("agent_tools._count_iter_dirs", side_effect=[0, 1]), \
             patch("agent_tools._last_iter_dir", return_value=None):
            result = agent_tools.run_pipeline_tool({"load_n": 500})
        assert result["status"] == "PASS"

    def test_status_fail_when_pipeline_fails(self):
        mock_eval = {
            "pass": False, "violations": ["stress: too high"], "mass_kg": 0.3,
            "fos": 0.8, "stress_utilisation": 1.2,
        }
        mock_params = {
            "flange_width": 0.08, "flange_height": 0.06,
            "web_height": 0.10, "thickness": 0.006, "fillet_radius": 0.004,
        }
        with patch("agent_tools.pipeline.run", return_value=(mock_params, mock_eval)), \
             patch("agent_tools._count_iter_dirs", side_effect=[0, 1]), \
             patch("agent_tools._last_iter_dir", return_value=None):
            result = agent_tools.run_pipeline_tool({"load_n": 9000})
        assert result["status"] == "FAIL"
        assert len(result["violations"]) > 0


# ---------------------------------------------------------------------------
# T1 — modify_and_run_tool: session state handling
# ---------------------------------------------------------------------------

class TestModifyAndRunTool:
    def setup_method(self):
        # Reset session state before each test
        agent_tools._session_state.update({
            "last_params_mm": None,
            "last_load_n": None,
            "last_material": None,
            "last_run_dir": None,
        })

    def test_error_when_no_prior_run(self):
        result = agent_tools.modify_and_run_tool({"changes": {"load_n": 3000}})
        assert "error" in result

    def _set_valid_session(self):
        agent_tools._session_state.update({
            "last_load_n": 2000,
            "last_params_mm": {
                "flange_width_mm": 80, "flange_height_mm": 60,
                "web_height_mm": 100, "thickness_mm": 6.0, "fillet_radius_mm": 4.0,
            },
            "last_material": {"E_gpa": 200, "nu": 0.3, "rho": 7850, "Sy_mpa": 250},
            "last_bracket_type_name": "l_bracket",
        })

    def _fake_run_return(self):
        fake_params = {"flange_width": 0.08, "flange_height": 0.06,
                       "web_height": 0.10, "thickness": 0.006, "fillet_radius": 0.004}
        fake_eval = {"pass": True, "mass_kg": 0.5, "fos": 2.0,
                     "stress_utilisation": 0.5, "violations": []}
        return fake_params, fake_eval

    def test_empty_changes_reruns_previous_design(self):
        """Omitting changes should trigger a rerun, not return an error."""
        self._set_valid_session()
        fake_params, fake_eval = self._fake_run_return()
        with patch("agent_tools.pipeline.run", return_value=(fake_params, fake_eval)) as mock_run, \
             patch("agent_tools._count_iter_dirs", side_effect=[0, 1]), \
             patch("agent_tools._last_iter_dir", return_value=None):
            result = agent_tools.modify_and_run_tool({})  # no changes key at all
        assert "error" not in result
        assert mock_run.called
        brief_used = mock_run.call_args[0][0]
        params, _ = parse_brief(brief_used)
        assert params["loads"]["magnitude_n"] == 2000.0
        assert "l_bracket" in brief_used

    def test_explicit_empty_changes_also_reruns(self):
        """changes={} (explicit empty dict) also triggers rerun, not error."""
        self._set_valid_session()
        fake_params, fake_eval = self._fake_run_return()
        with patch("agent_tools.pipeline.run", return_value=(fake_params, fake_eval)) as mock_run, \
             patch("agent_tools._count_iter_dirs", side_effect=[0, 1]), \
             patch("agent_tools._last_iter_dir", return_value=None):
            result = agent_tools.modify_and_run_tool({"changes": {}})
        assert "error" not in result
        assert mock_run.called

    def test_merges_load_change(self):
        agent_tools._session_state.update({
            "last_load_n": 2000,
            "last_params_mm": {
                "flange_width_mm": 80, "flange_height_mm": 60,
                "web_height_mm": 100, "thickness_mm": 7.26,
                "fillet_radius_mm": 3.27,
            },
            "last_material": {"E_gpa": 200, "nu": 0.3, "rho": 7850, "Sy_mpa": 250},
        })
        mock_eval = {
            "pass": True, "violations": [], "mass_kg": 0.9,
            "fos": 1.55, "stress_utilisation": 0.65,
        }
        mock_params = {
            "flange_width": 0.08, "flange_height": 0.06,
            "web_height": 0.10, "thickness": 0.009, "fillet_radius": 0.004,
        }
        with patch("agent_tools.pipeline.run", return_value=(mock_params, mock_eval)) as mock_run, \
             patch("agent_tools._count_iter_dirs", side_effect=[1, 2]), \
             patch("agent_tools._last_iter_dir", return_value=None):
            agent_tools.modify_and_run_tool({"changes": {"load_n": 3000}})

        brief_used = mock_run.call_args[0][0]
        params, _ = parse_brief(brief_used)
        assert params["loads"]["magnitude_n"] == 3000.0  # changed
        assert abs(params["thickness"] - 0.00726) < 1e-4  # carried over

    def test_changes_dict_not_mutated(self):
        """The caller's changes dict must not be modified by modify_and_run_tool."""
        original = {"load_n": 3000, "bracket_type": "t_bracket", "max_iter": 2}
        snapshot = dict(original)
        # Set up minimal session state so tool doesn't short-circuit
        agent_tools._session_state["last_load_n"] = 1000
        agent_tools._session_state["last_params_mm"] = {}
        agent_tools._session_state["last_material"] = {}
        # Return realistic params/eval so _build_result/_update_session don't error
        fake_params = {"flange_width": 0.08, "flange_height": 0.06,
                       "web_height": 0.10, "thickness": 0.006, "fillet_radius": 0.004}
        fake_eval = {"pass": True, "mass_kg": 0.5, "fos": 2.0,
                     "stress_utilisation": 0.5, "violations": []}
        with patch("pipeline.run", return_value=(fake_params, fake_eval)), \
             patch("agent_tools._count_iter_dirs", side_effect=[0, 1]), \
             patch("agent_tools._last_iter_dir", return_value=None):
            agent_tools.modify_and_run_tool({"changes": original})
        assert original == snapshot


# ---------------------------------------------------------------------------
# T1 — session state updated after successful run
# ---------------------------------------------------------------------------

class TestSessionStateUpdate:
    def setup_method(self):
        agent_tools._session_state.update({
            "last_params_mm": None, "last_load_n": None,
            "last_material": None, "last_run_dir": None,
        })

    def test_session_updated_after_run(self):
        mock_eval = {
            "pass": True, "violations": [], "mass_kg": 0.712,
            "fos": 1.62, "stress_utilisation": 0.617,
        }
        mock_params = {
            "flange_width": 0.08, "flange_height": 0.06,
            "web_height": 0.10, "thickness": 0.00726, "fillet_radius": 0.00327,
        }
        with patch("agent_tools.pipeline.run", return_value=(mock_params, mock_eval)), \
             patch("agent_tools._count_iter_dirs", side_effect=[0, 1]), \
             patch("agent_tools._last_iter_dir", return_value=None):
            agent_tools.run_pipeline_tool({"load_n": 2000})

        assert agent_tools._session_state["last_load_n"] == 2000
        assert agent_tools._session_state["last_params_mm"] is not None
        assert abs(agent_tools._session_state["last_params_mm"]["thickness_mm"] - 7.26) < 0.1


# ---------------------------------------------------------------------------
# T1 — read_last_results_tool: no runs dir
# ---------------------------------------------------------------------------

class TestReadLastResultsNoRuns:
    def test_returns_error_when_no_runs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = agent_tools.read_last_results_tool({})
        assert "error" in result

    def test_returns_error_for_missing_iter_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = agent_tools.read_last_results_tool({"iter_dir": "runs/iter_099"})
        assert "error" in result


# ---------------------------------------------------------------------------
# T1 — _parse_summary_md
# ---------------------------------------------------------------------------

class TestParseSummaryMd:
    SAMPLE_MD = """\
# Bracket FEM — Iteration 003

**Status:** PASS

---

## Geometry Parameters

| Parameter       | Value (mm) |
|-----------------|------------|
| flange_width    | 80.00 |
| thickness       | 7.26 |

---

## FEM Results

| Metric                  | Value |
|-------------------------|-------|
| Max von Mises stress    | 154.32 MPa |
| Max displacement        | 0.3100 mm |
| Node count              | 7454 |
| Factor of safety        | 1.620 |
| Stress utilisation      | 61.7 % |
| Mass                    | 0.7123 kg |

---

## Constraint Status

| Constraint              | Limit | Actual | Status |
|-------------------------|-------|--------|--------|
| Von Mises stress        | 166.7 MPa | 154.32 MPa | OK |
| Factor of safety        | >= 1.5 | 1.620 | OK |
| Max displacement        | 5.0 mm | 0.3100 mm | OK |
| Mass                    | unconstrained | 0.7123 kg | OK |

---

## Violations

*None — all constraints satisfied.*
"""

    def test_status(self, tmp_path):
        p = tmp_path / "summary.md"
        p.write_text(self.SAMPLE_MD)
        d = agent_tools._parse_summary_md(p)
        assert d["status"] == "PASS"

    def test_max_vm(self, tmp_path):
        p = tmp_path / "summary.md"
        p.write_text(self.SAMPLE_MD)
        d = agent_tools._parse_summary_md(p)
        assert d["max_vm_mpa"] == pytest.approx(154.32)

    def test_max_disp(self, tmp_path):
        p = tmp_path / "summary.md"
        p.write_text(self.SAMPLE_MD)
        d = agent_tools._parse_summary_md(p)
        assert d["max_disp_mm"] == pytest.approx(0.31)

    def test_fos(self, tmp_path):
        p = tmp_path / "summary.md"
        p.write_text(self.SAMPLE_MD)
        d = agent_tools._parse_summary_md(p)
        assert d["fos"] == pytest.approx(1.62)

    def test_mass(self, tmp_path):
        p = tmp_path / "summary.md"
        p.write_text(self.SAMPLE_MD)
        d = agent_tools._parse_summary_md(p)
        assert d["mass_kg"] == pytest.approx(0.7123)

    def test_stress_utilisation(self, tmp_path):
        p = tmp_path / "summary.md"
        p.write_text(self.SAMPLE_MD)
        d = agent_tools._parse_summary_md(p)
        assert d["stress_utilisation_pct"] == pytest.approx(61.7)

    def test_no_violations(self, tmp_path):
        p = tmp_path / "summary.md"
        p.write_text(self.SAMPLE_MD)
        d = agent_tools._parse_summary_md(p)
        assert d["violations"] == []

    def test_vm_not_confused_with_constraint_row(self, tmp_path):
        """Verify the constraint-status table's 'Von Mises stress' row (4 cols)
        does not overwrite the FEM Results row value."""
        p = tmp_path / "summary.md"
        p.write_text(self.SAMPLE_MD)
        d = agent_tools._parse_summary_md(p)
        # FEM Results has 154.32; constraint row has 166.7 as the limit
        assert d["max_vm_mpa"] == pytest.approx(154.32)

    def test_violations_parsed(self, tmp_path):
        md = self.SAMPLE_MD.replace(
            "*None — all constraints satisfied.*",
            "- stress: max von Mises 200.00 MPa exceeds allowable 166.67 MPa\n"
            "- fos: factor of safety 1.25 below minimum 1.50"
        )
        p = tmp_path / "summary.md"
        p.write_text(md)
        d = agent_tools._parse_summary_md(p)
        assert len(d["violations"]) == 2
        assert d["violations"][0].startswith("stress:")


# ---------------------------------------------------------------------------
# T2 — read_last_results with real runs/
# ---------------------------------------------------------------------------

@pytest.mark.requires_runs
@pytest.mark.skipif(not _runs_exist(), reason="no completed runs/ found")
class TestReadLastResultsLive:
    def test_returns_expected_keys(self):
        result = agent_tools.read_last_results_tool({})
        assert "error" not in result
        for key in ("status", "mass_kg", "fos", "max_vm_mpa",
                    "max_disp_mm", "violations", "output_dir"):
            assert key in result

    def test_status_is_pass_or_fail(self):
        result = agent_tools.read_last_results_tool({})
        assert result["status"] in ("PASS", "FAIL")

    def test_mass_is_positive(self):
        result = agent_tools.read_last_results_tool({})
        assert result["mass_kg"] > 0

    def test_specific_iter_dir(self):
        # Use the first iter dir that exists
        first = sorted(Path("runs").glob("iter_*"))[0]
        result = agent_tools.read_last_results_tool({"iter_dir": str(first)})
        assert "error" not in result
        assert result["output_dir"] == str(first)

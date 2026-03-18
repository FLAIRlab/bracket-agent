"""
Tool implementations, session state, and brief builder for the Bracket FEM Agent.

Provides three tool back-ends:
  run_pipeline_tool()      — builds a brief and calls pipeline.run()
  modify_and_run_tool()    — merges changes onto session state, reruns pipeline
  read_last_results_tool() — reads latest runs/iter_NNN without re-running
"""

import json
import logging
import re
from pathlib import Path

import pipeline
from bracket_types import get_type, KNOWN_TYPE_NAMES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults used when the user does not specify a parameter (L-bracket)
# ---------------------------------------------------------------------------
_DEFAULTS: dict = {
    "flange_width_mm":  80.0,
    "flange_height_mm": 60.0,
    "web_height_mm":    100.0,
    "thickness_mm":     6.0,
    "fillet_radius_mm": 4.0,
    "E_gpa":            200.0,
    "nu":               0.3,
    "rho":              7850.0,
    "Sy_mpa":           250.0,
    # load_n has no default — must be supplied
    "max_mass_kg":      None,
    "max_iter":         10,
}

# ---------------------------------------------------------------------------
# In-process session state — not persisted to disk
# ---------------------------------------------------------------------------
_session_state: dict = {
    "last_params_mm":        None,   # geometry from last run (keys end in _mm)
    "last_load_n":           None,   # load magnitude in N from last run
    "last_material":         None,   # material dict (E_gpa, nu, rho, Sy_mpa)
    "last_run_dir":          None,   # Path to last runs/iter_NNN
    "last_bracket_type_name": "l_bracket",  # bracket type name from last run
}


# ---------------------------------------------------------------------------
# Brief builder
# ---------------------------------------------------------------------------

def build_brief(params: dict, bracket_type_name: str = "l_bracket") -> str:
    """
    Render a plain-text design brief from a flat params dict.

    Parameters
    ----------
    params : dict
        For L/T-bracket: keys include flange_width_mm, flange_height_mm,
        web_height_mm, thickness_mm, fillet_radius_mm, E_gpa, nu, rho,
        Sy_mpa, load_n, max_mass_kg.
        For U-bracket: channel_width_mm, wall_height_mm, channel_depth_mm,
        thickness_mm, fillet_radius_mm instead of flange keys.
    bracket_type_name : str
        One of "l_bracket", "t_bracket", "u_bracket". Default "l_bracket".

    Returns a string that parse_brief() can parse correctly.
    """
    bt = get_type(bracket_type_name)
    E   = params.get("E_gpa",  _DEFAULTS["E_gpa"])
    nu  = params.get("nu",     _DEFAULTS["nu"])
    rho = params.get("rho",    _DEFAULTS["rho"])
    Sy  = params.get("Sy_mpa", _DEFAULTS["Sy_mpa"])
    load_n = params["load_n"]

    # Build geometry lines from the bracket type's defaults_mm,
    # overriding with user-supplied values.
    geo_lines = []
    for key_mm, default_val in bt.defaults_mm.items():
        val = params.get(key_mm, default_val)
        # Strip the trailing "_mm" to get the brief key name
        brief_key = key_mm[:-3]  # e.g. "flange_width_mm" → "flange_width"
        geo_lines.append(f"  {brief_key}: {val} mm")

    lines = [
        "Bracket dimensions:",
        f"  bracket_type: {bracket_type_name}",
    ] + geo_lines + [
        "",
        "Material: structural steel",
        f"  E:    {E} GPa",
        f"  nu:   {nu}",
        f"  rho:  {rho} kg/m3",
        f"  Sy:   {Sy} MPa",
        "",
        "Load:",
        "  type:      point_force",
        "  location:  tip of bracket",
        f"  magnitude: {load_n} N",
        "  direction: -Z",
        "",
        "Boundary conditions:",
        "  fixed face: bracket fixed face (all DOF constrained)",
    ]

    max_mass = params.get("max_mass_kg")
    if max_mass is not None:
        lines += [
            "",
            "Constraints:",
            f"  max_mass_kg: {max_mass} kg",
        ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Summary.md parser
# ---------------------------------------------------------------------------

def _parse_summary_md(summary_path: Path) -> dict:
    """Extract FEM metrics from a summary.md written by generate_report()."""
    text = summary_path.read_text(encoding="utf-8")
    result: dict = {}

    m = re.search(r"\*\*Status:\*\*\s*(PASS|FAIL)", text)
    if m:
        result["status"] = m.group(1)

    fem_section = ""
    in_fem = False
    for line in text.splitlines():
        if line.strip() == "## FEM Results":
            in_fem = True
        elif in_fem and line.startswith("## "):
            break
        elif in_fem:
            fem_section += line + "\n"

    m = re.search(r"\| Max von Mises stress\s*\|\s*([\d.]+)\s*MPa", fem_section)
    if m:
        result["max_vm_mpa"] = float(m.group(1))

    m = re.search(r"\| Max displacement\s*\|\s*([\d.]+)\s*mm", fem_section)
    if m:
        result["max_disp_mm"] = float(m.group(1))

    m = re.search(r"\| Factor of safety\s*\|\s*([^\|]+)\s*\|", fem_section)
    if m:
        val = m.group(1).strip()
        result["fos"] = float("inf") if "\u221e" in val else float(val)

    m = re.search(r"\| Stress utilisation\s*\|\s*([\d.]+)\s*%", fem_section)
    if m:
        result["stress_utilisation_pct"] = float(m.group(1))

    m = re.search(r"\| Mass\s*\|\s*([\d.]+)\s*kg", fem_section)
    if m:
        result["mass_kg"] = float(m.group(1))

    violations: list = []
    in_violations = False
    for line in text.splitlines():
        if line.strip() == "## Violations":
            in_violations = True
        elif in_violations and line.startswith("## "):
            in_violations = False
        elif in_violations and line.startswith("- "):
            violations.append(line[2:])
    result["violations"] = violations

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_iter_dirs() -> int:
    runs = Path("runs")
    if not runs.exists():
        return 0
    return len(list(runs.glob("iter_*")))


def _last_iter_dir() -> "Path | None":
    runs = Path("runs")
    if not runs.exists():
        return None
    dirs = sorted(runs.glob("iter_*"))
    return dirs[-1] if dirs else None


def _build_result(best_params: dict, best_eval: dict, last_dir: "Path | None",
                  iterations_run: int, Sy_mpa: float,
                  bracket_type_name: str = "l_bracket") -> dict:
    """Assemble the structured result dict returned to Claude."""
    bt = get_type(bracket_type_name)
    geo_keys = bt.param_keys

    # Convert SI metres → mm for the final params
    final_params_mm = {
        k: round(v * 1e3, 4)
        for k, v in best_params.items()
        if k in geo_keys
    }

    fos = best_eval.get("fos", float("inf"))
    stress_util = best_eval.get("stress_utilisation", 0.0)

    summary_data: dict = {}
    if last_dir is not None:
        sp = last_dir / "summary.md"
        if sp.exists():
            summary_data = _parse_summary_md(sp)

    max_vm_mpa = summary_data.get("max_vm_mpa", round(stress_util * Sy_mpa, 2))

    return {
        "status":                   "PASS" if best_eval.get("pass") else "FAIL",
        "iterations_run":           iterations_run,
        "mass_kg":                  round(best_eval.get("mass_kg", 0.0), 4),
        "fos":                      round(fos, 3) if fos != float("inf") else "inf",
        "max_vm_mpa":               max_vm_mpa,
        "max_disp_mm":              summary_data.get("max_disp_mm", 0.0),
        "stress_utilisation_pct":   round(stress_util * 100, 1),
        "violations":               best_eval.get("violations", []),
        "final_params_mm":          final_params_mm,
        "output_dir":               str(last_dir) if last_dir else "unknown",
        "bracket_type":             bracket_type_name,
    }


def _update_session(best_params: dict, params: dict, last_dir: "Path | None",
                    bracket_type_name: str = "l_bracket") -> None:
    """Persist run results into _session_state for use by modify_and_run."""
    bt = get_type(bracket_type_name)
    # Convert SI metres → mm with _mm suffix keys
    last_params_mm = {}
    for k in bt.param_keys:
        val = best_params.get(k)
        if val is not None:
            last_params_mm[f"{k}_mm"] = round(val * 1e3, 4)

    _session_state["last_params_mm"]         = last_params_mm
    _session_state["last_load_n"]            = params.get("load_n")
    _session_state["last_material"]          = {
        "E_gpa":  params.get("E_gpa",  _DEFAULTS["E_gpa"]),
        "nu":     params.get("nu",     _DEFAULTS["nu"]),
        "rho":    params.get("rho",    _DEFAULTS["rho"]),
        "Sy_mpa": params.get("Sy_mpa", _DEFAULTS["Sy_mpa"]),
    }
    _session_state["last_run_dir"]           = last_dir
    _session_state["last_bracket_type_name"] = bracket_type_name


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def run_pipeline_tool(inp: dict) -> dict:
    """
    Back-end for the run_pipeline tool.

    inp must contain 'load_n'. All other keys are optional.
    Accepts optional 'bracket_type' key: "l_bracket", "t_bracket", "u_bracket".
    """
    if "load_n" not in inp:
        return {"error": "load_n is required to run the pipeline."}

    bracket_type_name = inp.get("bracket_type", "l_bracket")
    if bracket_type_name not in KNOWN_TYPE_NAMES:
        return {"error": f"Unknown bracket_type {bracket_type_name!r}. "
                         f"Allowed: {sorted(KNOWN_TYPE_NAMES)}"}

    params: dict = {k: v for k, v in _DEFAULTS.items() if k != "max_iter"}
    params.update({k: v for k, v in inp.items() if k not in ("max_iter", "bracket_type")})
    max_iter = int(inp.get("max_iter", _DEFAULTS["max_iter"]))

    brief_text = build_brief(params, bracket_type_name=bracket_type_name)
    logger.debug("Built brief:\n%s", brief_text)

    before = _count_iter_dirs()
    print("[Running FEM pipeline — this may take 2-5 minutes...]", flush=True)

    try:
        best_params, best_eval = pipeline.run(brief_text, max_iter=max_iter)
    except Exception as exc:
        logger.exception("Pipeline failed")
        return {"error": str(exc)}

    after = _count_iter_dirs()
    iterations_run = after - before
    last_dir = _last_iter_dir()
    Sy_mpa = params.get("Sy_mpa", _DEFAULTS["Sy_mpa"])

    result = _build_result(best_params, best_eval, last_dir, iterations_run,
                           Sy_mpa, bracket_type_name=bracket_type_name)
    _update_session(best_params, params, last_dir,
                    bracket_type_name=bracket_type_name)
    return result


def modify_and_run_tool(inp: dict) -> dict:
    """
    Back-end for the modify_and_run tool.

    inp must contain 'changes' — a dict of parameter overrides.
    Merges changes over the previous session state and reruns.
    Accepts optional 'bracket_type' in changes to switch bracket type.
    """
    changes = dict(inp.get("changes") or {})   # copy — never mutate caller's dict
    if not changes:
        return {"error": "'changes' dict is required and must not be empty."}

    if _session_state["last_load_n"] is None:
        return {"error": "No previous run in this session. Please use run_pipeline first."}

    # Reconstruct params from session state
    params: dict = {}
    if _session_state["last_params_mm"]:
        params.update(_session_state["last_params_mm"])
    params["load_n"] = _session_state["last_load_n"]
    if _session_state["last_material"]:
        params.update(_session_state["last_material"])

    # Preserve bracket type from session (can be overridden in changes)
    bracket_type_name = _session_state.get("last_bracket_type_name", "l_bracket")

    # Apply requested changes (operating on the local copy)
    bracket_type_name = changes.pop("bracket_type", bracket_type_name)
    params.update(changes)

    if bracket_type_name not in KNOWN_TYPE_NAMES:
        return {"error": f"Unknown bracket_type {bracket_type_name!r}. "
                         f"Allowed: {sorted(KNOWN_TYPE_NAMES)}"}

    max_iter = int(params.pop("max_iter", _DEFAULTS["max_iter"]))

    brief_text = build_brief(params, bracket_type_name=bracket_type_name)
    logger.debug("Built modified brief:\n%s", brief_text)

    before = _count_iter_dirs()
    print("[Running FEM pipeline — this may take 2-5 minutes...]", flush=True)

    try:
        best_params, best_eval = pipeline.run(brief_text, max_iter=max_iter)
    except Exception as exc:
        logger.exception("Pipeline failed")
        return {"error": str(exc)}

    after = _count_iter_dirs()
    iterations_run = after - before
    last_dir = _last_iter_dir()
    Sy_mpa = params.get("Sy_mpa", _DEFAULTS["Sy_mpa"])

    result = _build_result(best_params, best_eval, last_dir, iterations_run,
                           Sy_mpa, bracket_type_name=bracket_type_name)
    _update_session(best_params, params, last_dir,
                    bracket_type_name=bracket_type_name)
    return result


def read_last_results_tool(inp: dict) -> dict:
    """
    Back-end for the read_last_results tool.

    Reads the highest-numbered runs/iter_NNN (or the path in inp['iter_dir'])
    without running any new simulation.
    """
    iter_dir = inp.get("iter_dir")

    if iter_dir:
        run_dir = Path(iter_dir)
        if not run_dir.exists():
            return {"error": f"Directory not found: {iter_dir}"}
    else:
        run_dir = _last_iter_dir()
        if run_dir is None:
            return {"error": "No runs found. Please run the pipeline first."}

    summary_path = run_dir / "summary.md"
    if not summary_path.exists():
        return {"error": f"No summary.md in {run_dir}. The run may not have completed."}

    summary_data = _parse_summary_md(summary_path)

    # Determine bracket type and geo keys from params.json
    params_path = run_dir / "params.json"
    bracket_type_name = "l_bracket"
    final_params_mm: dict = {}
    if params_path.exists():
        try:
            raw = json.loads(params_path.read_text(encoding="utf-8"))
            bracket_type_name = raw.get("_bracket_type", "l_bracket")
            # Validate
            if bracket_type_name not in KNOWN_TYPE_NAMES:
                bracket_type_name = "l_bracket"
            bt = get_type(bracket_type_name)
            for k in bt.param_keys:
                if k in raw:
                    final_params_mm[f"{k}_mm"] = round(raw[k] * 1e3, 4)
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    m = re.match(r"iter_(\d+)", run_dir.name)
    iter_num = int(m.group(1)) if m else 0

    fos = summary_data.get("fos", float("inf"))

    return {
        "status":                 summary_data.get("status", "UNKNOWN"),
        "iterations_run":         iter_num,
        "mass_kg":                round(summary_data.get("mass_kg", 0.0), 4),
        "fos":                    round(fos, 3) if fos != float("inf") else "inf",
        "max_vm_mpa":             summary_data.get("max_vm_mpa", 0.0),
        "max_disp_mm":            summary_data.get("max_disp_mm", 0.0),
        "stress_utilisation_pct": summary_data.get("stress_utilisation_pct", 0.0),
        "violations":             summary_data.get("violations", []),
        "final_params_mm":        final_params_mm,
        "output_dir":             str(run_dir),
        "bracket_type":           bracket_type_name,
    }

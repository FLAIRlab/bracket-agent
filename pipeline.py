"""
Main agent loop for the Bracket FEM Agent.

Orchestrates the full pipeline:
  parse_brief → create_geometry → generate_mesh → write_inp →
  run_simulation → parse_results → evaluate_constraints →
  (pass → report | fail → propose_params → loop)

Max iterations: 10 (hard limit).
"""

import logging
import sys
from pathlib import Path

from constraints import CONSTRAINTS, evaluate_constraints
from optimizer import propose_params
from tools.geometry import GeometryError, create_geometry
from tools.mesh import MeshError, generate_mesh
from tools.calculix import SimulationError, write_inp, run_simulation
from tools.results import parse_frd, parse_dat
from tools.report import generate_report
from tools.render import render_mesh, render_results

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

GEOMETRY_KEYS = ("flange_width", "flange_height", "web_height", "thickness", "fillet_radius")

_UNIT_TO_SI = {
    "mm":    1e-3,
    "cm":    1e-2,
    "m":     1.0,
    "gpa":   1e9,
    "mpa":   1e6,
    "pa":    1.0,
    "kn":    1e3,
    "n":     1.0,
    "kg/m3": 1.0,
    "kg/m³": 1.0,
    "kg":    1.0,
}

# Map brief key names → canonical param names
_KEY_ALIASES = {
    "e":             "E_pa",
    "youngs_modulus":"E_pa",
    "nu":            "nu",
    "poisson":       "nu",
    "rho":           "rho",
    "density":       "rho",
    "sy":            "Sy_pa",
    "yield_stress":  "Sy_pa",
    "yield":         "Sy_pa",
    "flange_width":  "flange_width",
    "flange_height": "flange_height",
    "web_height":    "web_height",
    "thickness":     "thickness",
    "fillet_radius": "fillet_radius",
    "type":          "type",
    "location":      "location",
    "magnitude":     "magnitude_n",
    "direction":     "direction",
    "fixed_face":    "fixed_face",
    "max_mass_kg":   "max_mass_kg",
}


def _parse_value_unit(raw: str):
    """
    Parse a string like "200 GPa" or "0.3" into (float, unit_str | None).
    Strips inline comments (# ...) first.
    """
    # Strip inline comments
    raw = raw.split("#")[0].split("←")[0].strip()
    parts = raw.split()
    if not parts:
        return None, None
    try:
        val = float(parts[0])
        unit = parts[1].lower() if len(parts) >= 2 else None
        return val, unit
    except ValueError:
        # String value (e.g. direction: -Z)
        return raw.strip(), None


def _apply_unit(val, unit: str | None):
    """Convert val to SI using unit string. Returns val unchanged if unit unknown."""
    if unit is None or not isinstance(val, (int, float)):
        return val
    factor = _UNIT_TO_SI.get(unit.lower(), None)
    if factor is None:
        return val
    return val * factor


def parse_brief(text: str) -> tuple[dict, dict]:
    """
    Parse a plain-text design brief into (params dict, constraints dict).

    Sections are identified by non-indented lines ending with ":".
    Key-value pairs are indented under their section.

    Returns
    -------
    (all_params, constraints) where all_params contains sub-dicts:
      geometry keys, material{E_pa,nu,rho,Sy_pa}, loads{...}, bcs{...}
    """
    section = None
    material: dict  = {}
    loads: dict     = {}
    bcs: dict       = {}
    geometry: dict  = {}
    max_mass_kg     = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Section header: non-indented line containing ":"
        # Handles both "Load:" and "Material: structural steel"
        if not line[0].isspace() and ":" in stripped:
            section = stripped.split(":")[0].strip().lower().replace(" ", "_")
            continue

        if ":" not in stripped:
            continue

        key_raw, _, val_raw = stripped.partition(":")
        key = key_raw.strip().lower().replace(" ", "_")
        val_raw = val_raw.strip()

        val, unit = _parse_value_unit(val_raw)
        if val is None:
            continue
        si_val = _apply_unit(val, unit)

        canonical = _KEY_ALIASES.get(key, key)

        if section in ("bracket_dimensions", "bracket dimensions", "dimensions"):
            if key in ("flange_width", "flange_height", "web_height", "thickness", "fillet_radius"):
                geometry[key] = si_val

        elif section == "material":
            if canonical in ("E_pa", "nu", "rho", "Sy_pa"):
                material[canonical] = si_val

        elif section == "load":
            if canonical == "magnitude_n":
                loads["magnitude_n"] = si_val
            elif canonical == "type":
                loads["type"] = str(val).strip()
            elif canonical == "direction":
                loads["direction"] = str(val).strip()
            elif canonical == "location":
                loads["location"] = str(val).strip()

        elif section in ("boundary_conditions", "boundary conditions"):
            bcs[canonical] = str(val).strip()

        else:
            # Best-effort: put geometry keys in geometry, material keys in material
            if key in ("flange_width", "flange_height", "web_height", "thickness", "fillet_radius"):
                geometry[key] = si_val
            elif canonical in ("E_pa", "nu", "rho", "Sy_pa"):
                material[canonical] = si_val
            elif canonical == "max_mass_kg":
                max_mass_kg = si_val

    # Build merged params
    all_params = {**geometry}
    all_params["material"] = material
    all_params["loads"]    = loads
    all_params["bcs"]      = bcs

    # Build constraints from CONSTRAINTS defaults, override with Sy and max_mass_kg
    constraints = dict(CONSTRAINTS)
    if "Sy_pa" in material:
        constraints["max_von_mises_pa"] = material["Sy_pa"] / 1.5
    if max_mass_kg is not None:
        constraints["max_mass_kg"] = max_mass_kg

    return all_params, constraints


def _geo_params(all_params: dict) -> dict:
    """Extract geometry-only keys."""
    return {k: all_params[k] for k in GEOMETRY_KEYS if k in all_params}


def run(brief_text: str, max_iter: int = 10) -> tuple[dict, dict]:
    """
    Run the full bracket FEM optimization loop.

    Parameters
    ----------
    brief_text : str — plain-text design brief
    max_iter   : int — maximum iterations (default 10)

    Returns
    -------
    (best_params, best_eval_result)
    """
    runs_dir = Path("runs")
    runs_dir.mkdir(exist_ok=True)

    all_params, constraints = parse_brief(brief_text)
    geo = _geo_params(all_params)
    material = all_params.get("material", {})
    loads    = all_params.get("loads", {})
    bcs      = all_params.get("bcs", {})

    if "rho" not in material:
        logger.warning("Brief missing material density — defaulting to rho=7850 kg/m³")
        material["rho"] = 7850.0
    if "Sy_pa" not in material:
        logger.warning("Brief missing yield stress — defaulting to Sy=250 MPa")
        material["Sy_pa"] = 250e6
    rho   = material["rho"]
    Sy_pa = material["Sy_pa"]

    best_params = None
    best_eval   = None
    _exhausted  = False   # set True only when every iteration ran without pass/stagnation

    for iteration in range(1, max_iter + 1):
        iter_dir = runs_dir / f"iter_{iteration:03d}"
        iter_dir.mkdir(parents=True, exist_ok=True)

        logger.info("=== Iteration %d ===", iteration)

        try:
            # 1. Geometry
            logger.info("Creating geometry...")
            step_path = create_geometry(geo, iter_dir)

            # 2. Mesh
            logger.info("Generating mesh...")
            try:
                mesh_path = generate_mesh(step_path, iter_dir, quality="medium")
            except MeshError:
                logger.warning("Mesh failed with fillet — retrying without fillet (apply_fillet=False)")
                step_path = create_geometry(geo, iter_dir, apply_fillet=False)
                mesh_path = generate_mesh(step_path, iter_dir, quality="medium")

            # 2a. Render
            render_path = render_mesh(mesh_path, iter_dir)

            # 3. Write .inp
            logger.info("Writing CalculiX .inp...")
            bcs_with_params = dict(bcs)
            bcs_with_params["params"] = geo
            inp_path = write_inp(mesh_path, loads, bcs_with_params, material, iter_dir)

            # 4. Run simulation
            logger.info("Running CalculiX...")
            frd_path, dat_path = run_simulation(inp_path)

            # 5. Parse results
            logger.info("Parsing results...")
            frd_results = parse_frd(frd_path)
            dat_results = parse_dat(dat_path)

            # 5a. Render FEA results
            results_render_path = render_results(mesh_path, frd_path, iter_dir)

            # 6. Build metrics dict (merge frd + dat results)
            metrics = {**frd_results, **dat_results}
            metrics["params"]  = geo
            metrics["rho"]     = rho
            metrics["Sy_pa"]   = Sy_pa

            # 7. Evaluate constraints
            eval_result = evaluate_constraints(metrics, constraints)

            # 8. Generate report
            report_path = generate_report(iteration, geo, metrics, eval_result, iter_dir,
                                          render_path, results_render_path, constraints)
            logger.info("Report: %s", report_path)
            logger.info(
                "  mass=%.4f kg  FOS=%.3f  vm=%.2f MPa  disp=%.4f mm  pass=%s",
                eval_result["mass_kg"],
                eval_result["fos"] if eval_result["fos"] != float("inf") else float("inf"),
                metrics["max_von_mises_pa"] / 1e6,
                metrics["max_displacement_m"] * 1e3,
                eval_result["pass"],
            )

            # Track best result: passing design always beats failing;
            # among same pass/fail status, prefer lower mass.
            new_passes  = eval_result["pass"]
            best_passes = best_eval["pass"] if best_eval is not None else False
            new_mass    = eval_result["mass_kg"]
            best_mass   = best_eval.get("mass_kg", float("inf")) if best_eval is not None else float("inf")
            if (best_eval is None
                    or (new_passes and not best_passes)
                    or (new_passes == best_passes and new_mass < best_mass)):
                best_params = dict(geo)
                best_eval   = dict(eval_result)

            # 9. Check pass
            if eval_result["pass"]:
                logger.info("All constraints satisfied at iteration %d.", iteration)
                return best_params, best_eval

            # 10. Stagnation detection
            new_geo = propose_params(geo, eval_result["violations"], iteration)
            if new_geo == geo:
                logger.warning("Optimizer stagnated at iteration %d — stopping.", iteration)
                break
            geo = new_geo
            if iteration == max_iter:
                _exhausted = True

        except (GeometryError, MeshError, SimulationError) as exc:
            logger.error("Pipeline error at iteration %d: %s", iteration, exc)
            if best_params is not None:
                logger.info("Returning best result so far.")
                return best_params, best_eval
            raise

    if _exhausted:
        logger.warning("Max iterations (%d) reached without a passing design.", max_iter)

    if best_params is None:
        best_params = geo
        best_eval   = {}

    return best_params, best_eval


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pipeline.py <brief.txt>")
        sys.exit(1)
    brief = Path(sys.argv[1]).read_text(encoding="utf-8")
    final_params, final_eval = run(brief)
    print("\n=== FINAL RESULT ===")
    print("Params:", final_params)
    print("Eval:  ", final_eval)

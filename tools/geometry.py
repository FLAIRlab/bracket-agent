"""
Parametric bracket creation via FreeCAD headless (FreeCADCmd).

Provides create_geometry() and modify_geometry() which produce a STEP
file from a params dict. All dimensions are in metres (SI). Raises
GeometryError on non-zero FreeCAD exit or missing output file.

Multi-bracket-type support: pass a BracketType to create_geometry() to
select the geometry script. Defaults to L-bracket when bracket_type=None.
"""

import json
import logging
import os
import subprocess
import textwrap
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


class GeometryError(Exception):
    """Raised when FreeCAD geometry creation fails."""


def _build_freecad_script(params: dict, output_step: Path,
                          apply_fillet: bool = True) -> str:
    """
    Return Python source for a FreeCADCmd headless L-bracket script.

    Kept for backward compatibility. New code should use
    bracket_type.freecad_script_fn(params, output_step, apply_fillet).
    """
    from bracket_types._helpers import _l_build_freecad_script
    return _l_build_freecad_script(params, output_step, apply_fillet)


def create_geometry(params: dict, output_dir: Path, bracket_type=None,
                    apply_fillet: bool = True) -> Path:
    """
    Create a parametric bracket STEP file from params.

    Parameters
    ----------
    params        : dict — geometry params in SI metres
    output_dir    : Path — directory to write geometry.step and params.json
    bracket_type  : BracketType | None — type descriptor (default: L-bracket)
    apply_fillet  : bool — apply interior corner fillet (default True).

    Returns
    -------
    Path to the written geometry.step

    Raises
    ------
    GeometryError if FreeCAD exits non-zero or STEP file is missing.
    """
    if bracket_type is None:
        from bracket_types import get_type
        bracket_type = get_type("l_bracket")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_step = output_dir / "geometry.step"
    params_json = output_dir / "params.json"
    script_path = output_dir / "_freecad_script.py"

    # Write params.json with schema version and bracket type for traceability
    params_record = {
        "_schema_version": _SCHEMA_VERSION,
        "_bracket_type": bracket_type.name,
        **params,
    }
    params_json.write_text(json.dumps(params_record, indent=2), encoding="utf-8")

    # Build and write the FreeCAD script
    script_src = bracket_type.freecad_script_fn(params, output_step,
                                                  apply_fillet=apply_fillet)
    script_path.write_text(script_src, encoding="utf-8")

    headless_env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}

    logger.info("Running FreeCADCmd: %s", script_path)
    try:
        result = subprocess.run(
            ["FreeCADCmd", str(script_path)],
            capture_output=True,
            text=True,
            timeout=120,
            env=headless_env,
        )
    except FileNotFoundError as exc:
        raise GeometryError("FreeCADCmd not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GeometryError("FreeCADCmd timed out after 120 s") from exc

    if result.returncode != 0:
        raise GeometryError(
            f"FreeCADCmd exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    if result.stderr.strip():
        logger.debug("FreeCADCmd stderr:\n%s", result.stderr.strip())

    if not output_step.exists():
        raise GeometryError(
            f"geometry.step not created by FreeCADCmd.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    logger.info("Geometry written: %s", output_step)
    return output_step


def modify_geometry(step_path: Path, deltas: dict, output_dir: Path) -> Path:
    """
    Load params.json beside step_path, apply deltas, re-run create_geometry.

    Parameters
    ----------
    step_path  : Path — existing geometry.step (params.json must be adjacent)
    deltas     : dict — parameter overrides to merge
    output_dir : Path — output directory for new geometry

    Returns
    -------
    Path to the new geometry.step

    Raises
    ------
    GeometryError if params.json is missing or _bracket_type key is unknown.
    """
    step_path = Path(step_path)
    params_json = step_path.parent / "params.json"
    if not params_json.exists():
        raise GeometryError(f"params.json not found beside {step_path}")

    raw = json.loads(params_json.read_text(encoding="utf-8"))

    # Resolve bracket type from params.json
    bracket_type_name = raw.get("_bracket_type")
    if bracket_type_name is None:
        logger.warning(
            "params.json at %s has no '_bracket_type' field (pre-migration file, "
            "schema_version absent). Treating as 'l_bracket'.",
            params_json,
        )
        bracket_type_name = "l_bracket"

    try:
        from bracket_types import get_type
        bracket_type = get_type(bracket_type_name)
    except ValueError as exc:
        raise GeometryError(
            f"params.json references unknown bracket_type {bracket_type_name!r}: {exc}"
        ) from exc

    # Strip metadata keys before passing params to create_geometry
    params = {k: v for k, v in raw.items() if not k.startswith("_")}
    params.update(deltas)
    return create_geometry(params, output_dir, bracket_type=bracket_type)

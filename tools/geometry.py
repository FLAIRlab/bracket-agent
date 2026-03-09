"""
Parametric bracket creation via FreeCAD headless (FreeCADCmd).

Provides create_geometry() and modify_geometry() which produce a STEP
file from a params dict. All dimensions are in metres (SI). Raises
GeometryError on non-zero FreeCAD exit or missing output file.
"""

import json
import logging
import os
import subprocess
import textwrap
from pathlib import Path

logger = logging.getLogger(__name__)


class GeometryError(Exception):
    """Raised when FreeCAD geometry creation fails."""


def _build_freecad_script(params: dict, output_step: Path, apply_fillet: bool = True) -> str:
    """Return Python source for a FreeCADCmd headless script.

    FreeCAD uses millimetres internally, so all SI-metre params are
    converted to mm before being embedded in the script.

    Parameters
    ----------
    apply_fillet : bool
        When False the fillet step is skipped, producing a plain L-bracket.
        Useful for testing the geometry→mesh pipeline without the curved
        faces that can cause gmsh to hang on certain platforms.
    """
    # Convert SI metres → mm for FreeCAD's internal coordinate system
    fw = params["flange_width"]  * 1e3
    fh = params["flange_height"] * 1e3
    wh = params["web_height"]    * 1e3
    t  = params["thickness"]     * 1e3
    fr = params["fillet_radius"] * 1e3
    # Use absolute path so FreeCADCmd CWD doesn't matter
    step_str = str(Path(output_step).resolve()).replace("\\", "/")

    fillet_block = textwrap.dedent(f"""\
        # --- Fillet the interior corner edge ---
        # Interior corner: x ≈ t, z ≈ wh - t, spans full Y
        interior_edges = []
        tol = max(t, wh) * 0.01
        for edge in bracket.Edges:
            bb = edge.BoundBox
            xmid = (bb.XMin + bb.XMax) / 2.0
            zmid = (bb.ZMin + bb.ZMax) / 2.0
            x_range = bb.XMax - bb.XMin
            z_range = bb.ZMax - bb.ZMin
            if (abs(xmid - t) < tol and abs(zmid - (wh - t)) < tol
                    and x_range < tol and z_range < tol):
                interior_edges.append(edge)

        if interior_edges:
            try:
                filleted = bracket.makeFillet(fr, interior_edges)
                bracket = filleted
            except Exception as e:
                import sys
                print(f"WARNING: fillet failed ({{e}}), exporting without fillet", file=sys.stderr)
        else:
            import sys
            print("WARNING: no interior corner edge found, exporting without fillet",
                  file=sys.stderr)
    """) if apply_fillet else ""

    script = (
f"""import FreeCAD as App
import Part

# All dimensions in mm (FreeCAD internal units)
fw = {fw!r}
fh = {fh!r}
wh = {wh!r}
t  = {t!r}
fr = {fr!r}
output_step = {step_str!r}

# --- Build web: X=[0,t], Y=[0,fh], Z=[0,wh] ---
web = Part.makeBox(t, fh, wh)

# --- Build flange: X=[0,fw], Y=[0,fh], Z=[wh-t, wh] ---
flange = Part.makeBox(fw, fh, t)
flange.Placement = App.Placement(
    App.Vector(0, 0, wh - t),
    App.Rotation(App.Vector(0, 0, 1), 0)
)

# --- Fuse ---
bracket = web.fuse(flange)
bracket = bracket.removeSplitter()

{fillet_block}
# Scale from FreeCAD's internal mm back to SI metres before export.
# gmsh reads raw STEP coordinates and ignores the declared unit, so the
# STEP must be in metres to match generate_mesh's CHAR_LEN values.
scale_mat = App.Matrix()
scale_mat.A11 = scale_mat.A22 = scale_mat.A33 = 1e-3
bracket = bracket.transformGeometry(scale_mat)

bracket.exportStep(output_step)
print("STEP exported:", output_step)
"""
    )
    return script


def create_geometry(params: dict, output_dir: Path, apply_fillet: bool = True) -> Path:
    """
    Create a parametric L-bracket STEP file from params.

    Parameters
    ----------
    params        : dict — geometry params in SI metres
    output_dir    : Path — directory to write geometry.step and params.json
    apply_fillet  : bool — apply interior corner fillet (default True).
                    Pass False to produce a plain L-bracket without curved
                    faces, which is more reliably meshable by gmsh.

    Returns
    -------
    Path to the written geometry.step

    Raises
    ------
    GeometryError if FreeCAD exits non-zero or STEP file is missing.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_step   = output_dir / "geometry.step"
    params_json   = output_dir / "params.json"
    script_path   = output_dir / "_freecad_script.py"

    # Write params.json for traceability
    params_json.write_text(json.dumps(params, indent=2), encoding="utf-8")

    # Build and write the FreeCAD script
    script_src = _build_freecad_script(params, output_step, apply_fillet=apply_fillet)
    script_path.write_text(script_src, encoding="utf-8")

    # Prevent Qt/FreeCAD from trying to connect to a display and hanging.
    # offscreen is the safe default for headless use; the variable is passed
    # through even on macOS where it is typically ignored by the Cocoa backend,
    # but it prevents the xcb/wayland backends from blocking on display init.
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
    """
    step_path = Path(step_path)
    params_json = step_path.parent / "params.json"
    if not params_json.exists():
        raise GeometryError(f"params.json not found beside {step_path}")

    params = json.loads(params_json.read_text(encoding="utf-8"))
    params.update(deltas)
    return create_geometry(params, output_dir)

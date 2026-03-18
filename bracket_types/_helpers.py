"""
L-bracket helper functions extracted from tools modules.

No imports from pipeline, optimizer, or tools — stdlib only.
Used by bracket_types/l_bracket.py to implement the BracketType interface.
"""

import logging
import math
import textwrap
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Param bounds for L-bracket
# ---------------------------------------------------------------------------

_L_PARAM_BOUNDS: dict = {
    "flange_width":  (0.04, 0.20),
    "flange_height": (0.03, 0.15),
    "web_height":    (0.05, 0.25),
    "thickness":     (0.003, 0.020),
    "fillet_radius": (0.002, 0.015),
}


def _l_clamp(key: str, value: float) -> float:
    lo, hi = _L_PARAM_BOUNDS[key]
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# FreeCAD script builder (L-bracket)
# ---------------------------------------------------------------------------

def _l_build_freecad_script(params: dict, output_step, apply_fillet: bool = True) -> str:
    """Return Python source for a FreeCADCmd headless script (L-bracket).

    FreeCAD uses millimetres internally, so all SI-metre params are
    converted to mm before being embedded in the script.
    """
    fw = params["flange_width"]  * 1e3
    fh = params["flange_height"] * 1e3
    wh = params["web_height"]    * 1e3
    t  = params["thickness"]     * 1e3
    fr = params["fillet_radius"] * 1e3
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

{fillet_block}# Scale from FreeCAD's internal mm back to SI metres before export.
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


# ---------------------------------------------------------------------------
# Fixed nodes / tip node (L-bracket)
# ---------------------------------------------------------------------------

def _l_fixed_nodes(nodes: dict, params: dict) -> list:
    """Return list of node IDs on the L-bracket fixed face (x ≈ 0)."""
    tol = 1e-6
    fixed = [nid for nid, (x, y, z) in nodes.items() if abs(x) < tol]
    if not fixed:
        all_x = [xyz[0] for xyz in nodes.values()]
        x_range = max(all_x) - min(all_x)
        tol_fb = x_range * 0.01
        fixed = [nid for nid, (x, y, z) in nodes.items() if x < tol_fb]
        logger.warning(
            "No nodes found at x=0 (tol=1e-6); using 1%% x-range fallback (tol=%.3e). "
            "Check geometry alignment.",
            tol_fb,
        )
    return fixed


def _l_tip_node(nodes: dict, params: dict) -> int:
    """Return the node ID closest to the L-bracket flange tip."""
    all_x = [xyz[0] for xyz in nodes.values()]
    all_y = [xyz[1] for xyz in nodes.values()]
    all_z = [xyz[2] for xyz in nodes.values()]
    fw = params.get("flange_width",  max(all_x))
    fh = params.get("flange_height", (max(all_y) + min(all_y)) / 2.0)
    wh = params.get("web_height",    max(all_z))
    t  = params.get("thickness",     (max(all_z) - min(all_z)) * 0.05)
    tip_target = (fw, fh / 2.0, wh - t / 2.0)
    return min(nodes.keys(), key=lambda nid: math.dist(nodes[nid], tip_target))


# ---------------------------------------------------------------------------
# Mass computation (L-bracket)
# ---------------------------------------------------------------------------

def _l_compute_mass(params: dict, rho: float) -> float:
    """Compute L-bracket mass analytically (web + flange, no double-count at corner)."""
    fw = params["flange_width"]
    fh = params["flange_height"]
    wh = params["web_height"]
    t  = params["thickness"]
    v_web     = t * fh * wh
    v_flange  = fw * fh * t
    v_overlap = t * fh * t
    return (v_web + v_flange - v_overlap) * rho


# ---------------------------------------------------------------------------
# Fillet constraint (L-bracket)
# ---------------------------------------------------------------------------

def _l_fillet_constraint(params: dict) -> float:
    """Max allowed fillet radius for L-bracket: thickness × 0.45."""
    return params["thickness"] * 0.45


# ---------------------------------------------------------------------------
# Optimizer propose_params (L-bracket)
# ---------------------------------------------------------------------------

def _l_propose_params(current_params: dict, violations: list, iteration: int) -> dict:
    """
    Propose updated geometry parameters for the L-bracket next iteration.

    Strategy
    --------
    - stress or fos violation  → thickness *= 1.10, fillet_radius *= 1.20
    - displacement only        → web_height *= 1.10 (fallback: thickness *= 1.10)
    - displacement + stress    → also web_height *= 1.05
    - mass only                → thickness *= 0.95
    - Clamp all to _L_PARAM_BOUNDS
    - fillet_radius <= thickness * 0.45 (always enforce)
    """
    params = dict(current_params)

    has_stress = any(v.startswith("stress:") or v.startswith("fos:") for v in violations)
    has_disp   = any(v.startswith("displacement:") for v in violations)
    has_mass   = any(v.startswith("mass:") for v in violations)

    if has_stress:
        params["thickness"]     = params["thickness"] * 1.10
        params["fillet_radius"] = params["fillet_radius"] * 1.20
        if has_disp:
            params["web_height"] = params["web_height"] * 1.05

    elif has_disp:
        new_wh = params["web_height"] * 1.10
        if new_wh > _L_PARAM_BOUNDS["web_height"][1]:
            params["thickness"] = params["thickness"] * 1.10
        else:
            params["web_height"] = new_wh

    elif has_mass:
        params["thickness"] = params["thickness"] * 0.95

    # Clamp all geometry keys to bounds
    for key in _L_PARAM_BOUNDS:
        if key in params:
            params[key] = _l_clamp(key, params[key])

    # Enforce fillet_radius <= thickness * 0.45
    max_fillet = params["thickness"] * 0.45
    if params.get("fillet_radius", 0) > max_fillet:
        params["fillet_radius"] = max_fillet

    return params

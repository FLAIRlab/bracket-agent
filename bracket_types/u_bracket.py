"""
U-bracket (channel section) type definition and registration.

Geometry
--------
- Base plate: X=[0,cw], Y=[0,cd], Z=[0,t]
- Left wall:  X=[0,t],    Y=[0,cd], Z=[0,wh]
- Right wall: X=[cw-t,cw], Y=[0,cd], Z=[0,wh]
- Fixed face: z ≈ 0 (base bottom face)
- Tip node: closest to (0, cd/2, wh) — top of left wall center

Geometry parameters (different from L/T-bracket):
  channel_width  — total width of the channel (cw)
  wall_height    — height of the side walls (wh)
  channel_depth  — out-of-plane depth (cd)
  thickness      — uniform plate thickness (t)
  fillet_radius  — interior base-wall fillet radius

Imports only from bracket_types.__init__ and stdlib.
Registers U_BRACKET into REGISTRY on import.
"""

import logging
import math
import textwrap
from pathlib import Path

from bracket_types import REGISTRY, BracketType, OptimizerStrategy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Param bounds for U-bracket
# ---------------------------------------------------------------------------

_U_PARAM_BOUNDS: dict = {
    "channel_width": (0.04, 0.20),
    "wall_height":   (0.05, 0.25),
    "channel_depth": (0.03, 0.15),
    "thickness":     (0.003, 0.020),
    "fillet_radius": (0.002, 0.015),
}


def _u_clamp(key: str, value: float) -> float:
    lo, hi = _U_PARAM_BOUNDS[key]
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# FreeCAD script builder (U-bracket)
# ---------------------------------------------------------------------------

def _u_build_freecad_script(params: dict, output_step, apply_fillet: bool = True) -> str:
    """Return Python source for a FreeCADCmd headless script (U-bracket)."""
    cw = params["channel_width"] * 1e3
    wh = params["wall_height"]   * 1e3
    cd = params["channel_depth"] * 1e3
    t  = params["thickness"]     * 1e3
    fr = params["fillet_radius"] * 1e3
    step_str = str(Path(output_step).resolve()).replace("\\", "/")

    fillet_block = textwrap.dedent(f"""\
        # --- Fillet base-wall interior corners ---
        corner_edges = []
        tol = max(t, wh) * 0.01
        for edge in bracket.Edges:
            bb = edge.BoundBox
            zmid = (bb.ZMin + bb.ZMax) / 2.0
            xmid = (bb.XMin + bb.XMax) / 2.0
            z_range = bb.ZMax - bb.ZMin
            x_range = bb.XMax - bb.XMin
            # Interior base-left-wall edge: x ≈ t, z ≈ t
            # Interior base-right-wall edge: x ≈ cw-t, z ≈ t
            if (abs(zmid - t) < tol and z_range < tol
                    and (abs(xmid - t) < tol or abs(xmid - (cw - t)) < tol)):
                corner_edges.append(edge)

        if corner_edges:
            try:
                filleted = bracket.makeFillet(fr, corner_edges)
                bracket = filleted
            except Exception as e:
                import sys
                print(f"WARNING: U-fillet failed ({{e}}), exporting without fillet",
                      file=sys.stderr)
        else:
            import sys
            print("WARNING: no U-corner edges found, exporting without fillet",
                  file=sys.stderr)
    """) if apply_fillet else ""

    script = (
f"""import FreeCAD as App
import Part

# All dimensions in mm (FreeCAD internal units)
cw = {cw!r}
wh = {wh!r}
cd = {cd!r}
t  = {t!r}
fr = {fr!r}
output_step = {step_str!r}

# --- Build base plate: X=[0,cw], Y=[0,cd], Z=[0,t] ---
base = Part.makeBox(cw, cd, t)

# --- Build left wall: X=[0,t], Y=[0,cd], Z=[0,wh] ---
left_wall = Part.makeBox(t, cd, wh)

# --- Build right wall: X=[cw-t,cw], Y=[0,cd], Z=[0,wh] ---
right_wall = Part.makeBox(t, cd, wh)
right_wall.Placement = App.Placement(
    App.Vector(cw - t, 0, 0),
    App.Rotation(App.Vector(0, 0, 1), 0)
)

# --- Fuse all three ---
bracket = base.fuse(left_wall)
bracket = bracket.fuse(right_wall)
bracket = bracket.removeSplitter()

{fillet_block}# Scale from FreeCAD's internal mm back to SI metres before export.
scale_mat = App.Matrix()
scale_mat.A11 = scale_mat.A22 = scale_mat.A33 = 1e-3
bracket = bracket.transformGeometry(scale_mat)

bracket.exportStep(output_step)
print("STEP exported:", output_step)
"""
    )
    return script


# ---------------------------------------------------------------------------
# Fixed nodes / tip node (U-bracket)
# ---------------------------------------------------------------------------

def _u_fixed_nodes(nodes: dict, params: dict) -> list:
    """Return node IDs on the U-bracket fixed face (z ≈ 0)."""
    tol = 1e-6
    fixed = [nid for nid, (x, y, z) in nodes.items() if abs(z) < tol]
    if not fixed:
        all_z = [xyz[2] for xyz in nodes.values()]
        z_range = max(all_z) - min(all_z)
        tol_fb = z_range * 0.01
        fixed = [nid for nid, (x, y, z) in nodes.items() if z < tol_fb]
        logger.warning(
            "U-bracket: no nodes at z=0 (tol=1e-6); using 1%% z-range fallback (tol=%.3e).",
            tol_fb,
        )
    return fixed


def _u_tip_node(nodes: dict, params: dict) -> int:
    """Return the node ID closest to the top of the U-bracket left wall center."""
    all_z = [xyz[2] for xyz in nodes.values()]
    all_y = [xyz[1] for xyz in nodes.values()]
    cd = params.get("channel_depth", max(all_y) - min(all_y))
    wh = params.get("wall_height",   max(all_z))
    tip_target = (0.0, cd / 2.0, wh)
    return min(nodes.keys(), key=lambda nid: math.dist(nodes[nid], tip_target))


# ---------------------------------------------------------------------------
# Mass computation (U-bracket)
# ---------------------------------------------------------------------------

def _u_compute_mass(params: dict, rho: float) -> float:
    """
    Compute U-bracket mass analytically.

    v_base      = cw × cd × t
    v_wall      = t  × cd × wh  (each wall, ×2)
    v_corner    = t  × cd × t   (base/wall overlap, ×2)
    total = v_base + 2×v_wall - 2×v_corner
    """
    cw = params["channel_width"]
    wh = params["wall_height"]
    cd = params["channel_depth"]
    t  = params["thickness"]
    v_base   = cw * cd * t
    v_wall   = t * cd * wh
    v_corner = t * cd * t
    return (v_base + 2 * v_wall - 2 * v_corner) * rho


# ---------------------------------------------------------------------------
# Fillet constraint (U-bracket)
# ---------------------------------------------------------------------------

def _u_fillet_constraint(params: dict) -> float:
    return params["thickness"] * 0.45


# ---------------------------------------------------------------------------
# Optimizer (U-bracket) — displacement drives wall_height increase
# ---------------------------------------------------------------------------

def _u_propose_params(current_params: dict, violations: list, iteration: int) -> dict:
    """
    U-bracket optimization strategy.

    - stress / fos violation → thickness *= 1.10, fillet_radius *= 1.20
    - displacement only      → wall_height *= 1.10 (fallback: thickness *= 1.10)
    - displacement + stress  → also wall_height *= 1.05
    - mass only              → thickness *= 0.95
    """
    params = dict(current_params)

    has_stress = any(v.startswith("stress:") or v.startswith("fos:") for v in violations)
    has_disp   = any(v.startswith("displacement:") for v in violations)
    has_mass   = any(v.startswith("mass:") for v in violations)

    if has_stress:
        params["thickness"]     = params["thickness"] * 1.10
        params["fillet_radius"] = params["fillet_radius"] * 1.20
        if has_disp:
            params["wall_height"] = params["wall_height"] * 1.05

    elif has_disp:
        new_wh = params["wall_height"] * 1.10
        if new_wh > _U_PARAM_BOUNDS["wall_height"][1]:
            params["thickness"] = params["thickness"] * 1.10
        else:
            params["wall_height"] = new_wh

    elif has_mass:
        params["thickness"] = params["thickness"] * 0.95

    for key in _U_PARAM_BOUNDS:
        if key in params:
            params[key] = _u_clamp(key, params[key])

    max_fillet = params["thickness"] * 0.45
    if params.get("fillet_radius", 0) > max_fillet:
        params["fillet_radius"] = max_fillet

    return params


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

U_BRACKET = BracketType(
    name="u_bracket",
    display_name="U-bracket (channel)",
    param_keys=(
        "channel_width",
        "wall_height",
        "channel_depth",
        "thickness",
        "fillet_radius",
    ),
    defaults_mm={
        "channel_width_mm":  80.0,
        "wall_height_mm":    100.0,
        "channel_depth_mm":  60.0,
        "thickness_mm":      6.0,
        "fillet_radius_mm":  4.0,
    },
    fillet_constraint=_u_fillet_constraint,
    freecad_script_fn=_u_build_freecad_script,
    fixed_nodes_fn=_u_fixed_nodes,
    tip_node_fn=_u_tip_node,
    mass_fn=_u_compute_mass,
    optimizer=OptimizerStrategy(
        param_bounds=_U_PARAM_BOUNDS,
        propose_fn=_u_propose_params,
    ),
)

REGISTRY["u_bracket"] = U_BRACKET

"""
T-bracket (inverted T) type definition and registration.

Geometry
--------
- Web: central vertical wall, X=[fw/2 - t/2, fw/2 + t/2], Y=[0,fh], Z=[0,wh]
- Flange: horizontal plate at top, X=[0,fw], Y=[0,fh], Z=[wh-t, wh]
- Fixed face: z ≈ 0 (web + flange bottom face)
- Tip node: closest to (fw/2, fh/2, wh - t/2)  — center of top surface

Uses the same 5 geometry keys as L-bracket.

Imports only from bracket_types.__init__ and stdlib.
Registers T_BRACKET into REGISTRY on import.
"""

import logging
import math
import textwrap
from pathlib import Path

from bracket_types import REGISTRY, BracketType, OptimizerStrategy
from bracket_types._helpers import _l_propose_params, _L_PARAM_BOUNDS
from tools.presizing import t_presizing

logger = logging.getLogger(__name__)

# T-bracket uses the same param bounds and optimizer strategy as L-bracket.
_T_PARAM_BOUNDS = _L_PARAM_BOUNDS   # same object — not a copy


def _t_clamp(key: str, value: float) -> float:
    lo, hi = _T_PARAM_BOUNDS[key]
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# FreeCAD script builder (T-bracket)
# ---------------------------------------------------------------------------

def _t_build_freecad_script(params: dict, output_step, apply_fillet: bool = True) -> str:
    """Return Python source for a FreeCADCmd headless script (T-bracket)."""
    fw = params["flange_width"]  * 1e3
    fh = params["flange_height"] * 1e3
    wh = params["web_height"]    * 1e3
    t  = params["thickness"]     * 1e3
    fr = params["fillet_radius"] * 1e3
    step_str = str(Path(output_step).resolve()).replace("\\", "/")

    fillet_block = textwrap.dedent(f"""\
        # --- Fillet the two T-junction edges (web meets flange underside) ---
        junction_edges = []
        tol = max(t, wh) * 0.01
        for edge in bracket.Edges:
            bb = edge.BoundBox
            zmid = (bb.ZMin + bb.ZMax) / 2.0
            xmid = (bb.XMin + bb.XMax) / 2.0
            z_range = bb.ZMax - bb.ZMin
            x_range = bb.XMax - bb.XMin
            # Edges at z ≈ wh-t (top of web / bottom of flange), x ≈ fw/2±t/2
            if (abs(zmid - (wh - t)) < tol and z_range < tol
                    and (abs(xmid - (fw/2 - t/2)) < tol
                         or abs(xmid - (fw/2 + t/2)) < tol)):
                junction_edges.append(edge)

        if junction_edges:
            try:
                filleted = bracket.makeFillet(fr, junction_edges)
                bracket = filleted
            except Exception as e:
                import sys
                print(f"WARNING: T-fillet failed ({{e}}), exporting without fillet",
                      file=sys.stderr)
        else:
            import sys
            print("WARNING: no T-junction edges found, exporting without fillet",
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

# --- Build web: centered at x=fw/2, X=[fw/2-t/2, fw/2+t/2], Y=[0,fh], Z=[0,wh] ---
web = Part.makeBox(t, fh, wh)
web.Placement = App.Placement(
    App.Vector(fw/2 - t/2, 0, 0),
    App.Rotation(App.Vector(0, 0, 1), 0)
)

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
scale_mat = App.Matrix()
scale_mat.A11 = scale_mat.A22 = scale_mat.A33 = 1e-3
bracket = bracket.transformGeometry(scale_mat)

bracket.exportStep(output_step)
print("STEP exported:", output_step)
"""
    )
    return script


# ---------------------------------------------------------------------------
# Fixed nodes / tip node (T-bracket)
# ---------------------------------------------------------------------------

def _t_fixed_nodes(nodes: dict, params: dict) -> list:
    """Return node IDs on the T-bracket fixed face (z ≈ 0)."""
    tol = 1e-6
    fixed = [nid for nid, (x, y, z) in nodes.items() if abs(z) < tol]
    if not fixed:
        all_z = [xyz[2] for xyz in nodes.values()]
        z_range = max(all_z) - min(all_z)
        tol_fb = z_range * 0.01
        fixed = [nid for nid, (x, y, z) in nodes.items() if z < tol_fb]
        logger.warning(
            "T-bracket: no nodes at z=0 (tol=1e-6); using 1%% z-range fallback (tol=%.3e).",
            tol_fb,
        )
    return fixed


def _t_tip_node(nodes: dict, params: dict) -> int:
    """Return the node ID closest to the T-bracket flange center top."""
    all_x = [xyz[0] for xyz in nodes.values()]
    all_y = [xyz[1] for xyz in nodes.values()]
    all_z = [xyz[2] for xyz in nodes.values()]
    fw = params.get("flange_width",  max(all_x))
    fh = params.get("flange_height", (max(all_y) + min(all_y)) / 2.0)
    wh = params.get("web_height",    max(all_z))
    t  = params.get("thickness",     (max(all_z) - min(all_z)) * 0.05)
    tip_target = (fw / 2.0, fh / 2.0, wh - t / 2.0)
    return min(nodes.keys(), key=lambda nid: math.dist(nodes[nid], tip_target))


def _t_load_patch(nodes: dict, params: dict, k: int = 5) -> list:
    """Return k nearest node IDs to the T-bracket load application point."""
    all_x = [xyz[0] for xyz in nodes.values()]
    all_y = [xyz[1] for xyz in nodes.values()]
    all_z = [xyz[2] for xyz in nodes.values()]
    fw = params.get("flange_width",  max(all_x))
    fh = params.get("flange_height", (max(all_y) + min(all_y)) / 2.0)
    wh = params.get("web_height",    max(all_z))
    t  = params.get("thickness",     (max(all_z) - min(all_z)) * 0.05)
    target = (fw / 2.0, fh / 2.0, wh - t / 2.0)
    return sorted(nodes, key=lambda nid: math.dist(nodes[nid], target))[:k]


# ---------------------------------------------------------------------------
# Mass computation (T-bracket)
# ---------------------------------------------------------------------------

def _t_compute_mass(params: dict, rho: float) -> float:
    """
    Compute T-bracket mass analytically.

    v_web    = t × fh × wh  (full-height central web)
    v_flange = fw × fh × t  (top flange plate)
    v_overlap= t × fh × t   (web/flange intersection, counted in both — subtract once)
    """
    fw = params["flange_width"]
    fh = params["flange_height"]
    wh = params["web_height"]
    t  = params["thickness"]
    v_web     = t * fh * wh
    v_flange  = fw * fh * t
    v_overlap = t * fh * t
    return (v_web + v_flange - v_overlap) * rho


# ---------------------------------------------------------------------------
# Fillet constraint (T-bracket)
# ---------------------------------------------------------------------------

def _t_fillet_constraint(params: dict) -> float:
    return params["thickness"] * 0.45


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

T_BRACKET = BracketType(
    name="t_bracket",
    display_name="T-bracket",
    param_keys=(
        "flange_width",
        "flange_height",
        "web_height",
        "thickness",
        "fillet_radius",
    ),
    defaults_mm={
        "flange_width_mm":  120.0,
        "flange_height_mm": 60.0,
        "web_height_mm":    100.0,
        "thickness_mm":     6.0,
        "fillet_radius_mm": 4.0,
    },
    fillet_constraint=_t_fillet_constraint,
    freecad_script_fn=_t_build_freecad_script,
    fixed_nodes_fn=_t_fixed_nodes,
    tip_node_fn=_t_tip_node,
    mass_fn=_t_compute_mass,
    optimizer=OptimizerStrategy(
        param_bounds=_L_PARAM_BOUNDS,
        propose_fn=_l_propose_params,
    ),
    presizing_fn=t_presizing,
    load_patch_fn=_t_load_patch,
)

REGISTRY["t_bracket"] = T_BRACKET

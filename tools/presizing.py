"""
Analytical pre-sizing functions for bracket types.

Physics-based warm-start routines that compute minimum geometry parameters
from beam/column theory before the FEM loop begins.

Each function signature: (params, loads, material, constraints) -> params

Invariant: never lower a param below its user-supplied value (except fillet,
which may be reduced to satisfy the fillet ≤ thickness × 0.45 constraint).
"""

import logging
import math

logger = logging.getLogger(__name__)

_L_BOUNDS = {
    "flange_width":  (0.04, 0.20),
    "flange_height": (0.03, 0.15),
    "web_height":    (0.05, 0.25),
    "thickness":     (0.003, 0.020),
    "fillet_radius": (0.002, 0.015),
}

_U_BOUNDS = {
    "channel_width": (0.04, 0.20),
    "wall_height":   (0.05, 0.25),
    "channel_depth": (0.03, 0.15),
    "thickness":     (0.003, 0.020),
    "fillet_radius": (0.002, 0.015),
}


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def l_presizing(params: dict, loads: dict, material: dict, constraints: dict) -> dict:
    """
    Analytical pre-sizing for L-bracket.

    Cantilever model: load F at x=fw (flange tip), fixed at x=0.
    Critical section at x=0: bending moment M = F*fw.
    Section: rectangular b=fh, h=wh.

    σ = 6*F*fw / (fh*wh²) → wh_stress = sqrt(6*F*fw / (fh*σ_allow))
    δ = 4*F*fw³ / (E*fh*wh³) → wh_disp = cbrt(4*F*fw³ / (E*fh*δ_allow))
    t_min from shear (thin-web): 3*F / (2*fh*σ_allow)
    """
    out = dict(params)

    F   = loads.get("magnitude_n", 0.0)
    E   = material.get("E_pa", 200e9)
    Sy  = material.get("Sy_pa", 250e6)
    FOS = constraints.get("min_factor_of_safety", 1.5)

    if F <= 0.0:
        return out

    σ_allow = Sy / FOS
    δ_allow = constraints.get("max_displacement_m", 0.005)

    fw = out.get("flange_width",  _L_BOUNDS["flange_width"][0])
    fh = out.get("flange_height", _L_BOUNDS["flange_height"][0])

    wh_stress = math.sqrt(6.0 * F * fw / (fh * σ_allow))
    wh_disp   = (4.0 * F * fw**3 / (E * fh * δ_allow)) ** (1.0 / 3.0)
    wh_min    = max(wh_stress, wh_disp)

    t_min = max(3.0 * F / (2.0 * fh * σ_allow), _L_BOUNDS["thickness"][0])

    # Never lower below user-supplied values
    wh_new = max(out.get("web_height", wh_min), wh_min)
    t_new  = max(out.get("thickness",  t_min),  t_min)

    wh_new = _clamp(wh_new, *_L_BOUNDS["web_height"])
    t_new  = _clamp(t_new,  *_L_BOUNDS["thickness"])

    # Fillet: reduce only if needed to satisfy fillet ≤ thickness × 0.45
    fr_new = out.get("fillet_radius", _L_BOUNDS["fillet_radius"][0])
    fr_new = min(fr_new, t_new * 0.45)
    fr_new = _clamp(fr_new, *_L_BOUNDS["fillet_radius"])

    out["web_height"]    = wh_new
    out["thickness"]     = t_new
    out["fillet_radius"] = fr_new

    logger.debug(
        "L-bracket pre-sizing: wh_stress=%.1fmm wh_disp=%.1fmm → wh=%.1fmm t=%.1fmm",
        wh_stress * 1e3, wh_disp * 1e3, wh_new * 1e3, t_new * 1e3,
    )
    return out


def t_presizing(params: dict, loads: dict, material: dict, constraints: dict) -> dict:
    """
    Analytical pre-sizing for T-bracket.

    Conservative L-bracket formula with fw/2 as moment arm (symmetric half-span).
    """
    out = dict(params)

    F   = loads.get("magnitude_n", 0.0)
    E   = material.get("E_pa", 200e9)
    Sy  = material.get("Sy_pa", 250e6)
    FOS = constraints.get("min_factor_of_safety", 1.5)

    if F <= 0.0:
        return out

    σ_allow = Sy / FOS
    δ_allow = constraints.get("max_displacement_m", 0.005)

    fw = out.get("flange_width",  _L_BOUNDS["flange_width"][0])
    fh = out.get("flange_height", _L_BOUNDS["flange_height"][0])
    half_fw = fw / 2.0

    wh_stress = math.sqrt(6.0 * F * half_fw / (fh * σ_allow))
    wh_disp   = (4.0 * F * half_fw**3 / (E * fh * δ_allow)) ** (1.0 / 3.0)
    wh_min    = max(wh_stress, wh_disp)

    t_min = max(F / (fh * σ_allow), _L_BOUNDS["thickness"][0])

    wh_new = max(out.get("web_height", wh_min), wh_min)
    t_new  = max(out.get("thickness",  t_min),  t_min)

    wh_new = _clamp(wh_new, *_L_BOUNDS["web_height"])
    t_new  = _clamp(t_new,  *_L_BOUNDS["thickness"])

    fr_new = out.get("fillet_radius", _L_BOUNDS["fillet_radius"][0])
    fr_new = min(fr_new, t_new * 0.45)
    fr_new = _clamp(fr_new, *_L_BOUNDS["fillet_radius"])

    out["web_height"]    = wh_new
    out["thickness"]     = t_new
    out["fillet_radius"] = fr_new

    logger.debug(
        "T-bracket pre-sizing: wh=%.1fmm t=%.1fmm",
        wh_new * 1e3, t_new * 1e3,
    )
    return out


def u_presizing(params: dict, loads: dict, material: dict, constraints: dict) -> dict:
    """
    Analytical pre-sizing for U-bracket.

    Two failure modes:
      (1) Wall in compression:  t_min_col  = F / (cd * σ_allow)
      (2) Base plate bending:   t_min_base = sqrt(6*F*wh / (cd*σ_allow))
    """
    out = dict(params)

    F  = loads.get("magnitude_n", 0.0)
    Sy = material.get("Sy_pa", 250e6)
    FOS = constraints.get("min_factor_of_safety", 1.5)

    if F <= 0.0:
        return out

    σ_allow = Sy / FOS

    cd = out.get("channel_depth", _U_BOUNDS["channel_depth"][0])
    wh = out.get("wall_height",   _U_BOUNDS["wall_height"][0])

    t_min_col  = F / (cd * σ_allow)
    t_min_base = math.sqrt(6.0 * F * wh / (cd * σ_allow))
    t_min      = max(t_min_col, t_min_base, _U_BOUNDS["thickness"][0])

    t_new = max(out.get("thickness", t_min), t_min)
    t_new = _clamp(t_new, *_U_BOUNDS["thickness"])

    fr_new = out.get("fillet_radius", _U_BOUNDS["fillet_radius"][0])
    fr_new = min(fr_new, t_new * 0.45)
    fr_new = _clamp(fr_new, *_U_BOUNDS["fillet_radius"])

    out["thickness"]     = t_new
    out["fillet_radius"] = fr_new

    logger.debug(
        "U-bracket pre-sizing: t_col=%.1fmm t_base=%.1fmm → t=%.1fmm",
        t_min_col * 1e3, t_min_base * 1e3, t_new * 1e3,
    )
    return out

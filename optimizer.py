"""
Parameter update strategy between FEM iterations.

Implements propose_params() which inspects constraint violations and
adjusts bracket geometry parameters (thickness, fillet_radius, etc.)
to drive the design toward feasibility while minimising mass.
"""

PARAM_BOUNDS = {
    "flange_width":  (0.04, 0.20),
    "flange_height": (0.03, 0.15),
    "web_height":    (0.05, 0.25),
    "thickness":     (0.003, 0.020),
    "fillet_radius": (0.002, 0.015),
}


def _clamp(key: str, value: float) -> float:
    lo, hi = PARAM_BOUNDS[key]
    return max(lo, min(hi, value))


def propose_params(current_params: dict, violations: list, iteration: int) -> dict:
    """
    Propose updated geometry parameters for the next iteration.

    Strategy
    --------
    - stress or fos violation  → thickness *= 1.10, fillet_radius *= 1.20
    - displacement only        → web_height *= 1.10 (fallback: thickness *= 1.10)
    - displacement + stress    → also web_height *= 1.05
    - mass only                → thickness *= 0.95
    - Clamp all to PARAM_BOUNDS
    - fillet_radius <= thickness * 0.45 (always enforce)
    """
    params = dict(current_params)

    # Categorise violations by prefix
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
        # If already at bound, fall back to thickness increase
        if new_wh > PARAM_BOUNDS["web_height"][1]:
            params["thickness"] = params["thickness"] * 1.10
        else:
            params["web_height"] = new_wh

    elif has_mass:
        params["thickness"] = params["thickness"] * 0.95

    # Clamp all geometry keys to bounds
    for key in ("flange_width", "flange_height", "web_height", "thickness", "fillet_radius"):
        if key in params:
            params[key] = _clamp(key, params[key])

    # Enforce geometric constraint: fillet_radius <= thickness * 0.45.
    # Applied AFTER clamping so thickness is final; the geometric limit takes
    # priority over the lower param bound when the two conflict.
    max_fillet = params["thickness"] * 0.45
    if params.get("fillet_radius", 0) > max_fillet:
        params["fillet_radius"] = max_fillet

    return params

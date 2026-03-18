"""
Constraint definitions and evaluator for FEM results.

Defines CONSTRAINTS dict (max von Mises stress, min factor of safety,
max displacement, optional max mass) and the evaluate_constraints()
function that returns pass/fail status, violation list, mass, and FOS.
"""

import math

CONSTRAINTS = {
    "max_von_mises_pa":     250e6 / 1.5,   # 166.67 MPa allowable
    "min_factor_of_safety": 1.5,
    "max_displacement_m":   0.005,
    "max_mass_kg":          None,
}


def _compute_mass(params: dict, rho: float) -> float:
    """Compute L-bracket mass analytically (web + flange, no double-count at corner)."""
    fw = params["flange_width"]
    fh = params["flange_height"]
    wh = params["web_height"]
    t  = params["thickness"]
    v_web     = t * fh * wh
    v_flange  = fw * fh * t
    v_overlap = t * fh * t
    v_total   = v_web + v_flange - v_overlap
    return v_total * rho


def evaluate_constraints(metrics: dict, constraints: dict,
                         bracket_type=None) -> dict:
    """
    Evaluate constraint satisfaction.

    Parameters
    ----------
    metrics : dict
        Must contain: max_von_mises_pa, max_displacement_m, params (geo dict),
        rho (kg/m³), Sy_pa (yield stress in Pa).
    constraints : dict
        Constraint limits (see CONSTRAINTS above).
    bracket_type : BracketType | None
        If provided, uses bracket_type.mass_fn for mass calculation.
        Defaults to L-bracket mass formula (_compute_mass) when None.

    Returns
    -------
    dict with keys: pass (bool), violations (list[str]), mass_kg (float),
                    fos (float), stress_utilisation (float)
    """
    max_vm   = metrics["max_von_mises_pa"]
    max_disp = metrics["max_displacement_m"]
    params   = metrics["params"]
    rho      = metrics["rho"]
    Sy_pa    = metrics["Sy_pa"]

    violations = []

    # --- Stress check ---
    limit_vm = constraints.get("max_von_mises_pa", CONSTRAINTS["max_von_mises_pa"])
    if max_vm > limit_vm:
        violations.append(
            f"stress: max von Mises {max_vm/1e6:.2f} MPa exceeds allowable "
            f"{limit_vm/1e6:.2f} MPa"
        )

    # --- Factor of safety ---
    if max_vm > 0:
        fos = Sy_pa / max_vm
    else:
        fos = math.inf

    min_fos = constraints.get("min_factor_of_safety", CONSTRAINTS["min_factor_of_safety"])
    if fos < min_fos:
        violations.append(
            f"fos: factor of safety {fos:.3f} below minimum {min_fos:.2f}"
        )

    # --- Displacement check ---
    limit_disp = constraints.get("max_displacement_m", CONSTRAINTS["max_displacement_m"])
    if max_disp > limit_disp:
        violations.append(
            f"displacement: max deflection {max_disp*1e3:.3f} mm exceeds "
            f"{limit_disp*1e3:.1f} mm limit"
        )

    # --- Mass check ---
    if bracket_type is not None:
        mass_kg = bracket_type.mass_fn(params, rho)
    else:
        mass_kg = _compute_mass(params, rho)

    limit_mass = constraints.get("max_mass_kg", CONSTRAINTS["max_mass_kg"])
    if limit_mass is not None and mass_kg > limit_mass:
        violations.append(
            f"mass: {mass_kg:.4f} kg exceeds limit {limit_mass:.4f} kg"
        )

    stress_utilisation = max_vm / Sy_pa if Sy_pa > 0 else 0.0

    return {
        "pass":                violations == [],
        "violations":          violations,
        "mass_kg":             mass_kg,
        "fos":                 fos,
        "stress_utilisation":  stress_utilisation,
    }

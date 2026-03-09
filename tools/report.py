"""
Per-iteration Markdown summary generator.

Provides generate_report() which writes a human-readable summary.md
for a completed iteration, including params, metrics, constraint status,
factor of safety, and mass. Used both for passing and failing iterations.
"""

import math
from pathlib import Path

from constraints import CONSTRAINTS


def generate_report(
    iteration: int,
    params: dict,
    metrics: dict,
    eval_result: dict,
    output_dir: Path,
    render_path: "Path | None" = None,
    results_render_path: "Path | None" = None,
    constraints: "dict | None" = None,
) -> Path:
    """
    Write a Markdown summary for one FEM iteration.

    Parameters
    ----------
    iteration   : int        — iteration number (1-based)
    params      : dict       — geometry parameters (SI units)
    metrics     : dict       — FEM results (max_von_mises_pa, max_displacement_m, node_count)
    eval_result : dict       — output of evaluate_constraints()
    output_dir  : Path       — directory to write summary.md into
    render_path : Path|None  — path to mesh render PNG (optional)
    results_render_path : Path|None — path to FEA results render PNG (optional)
    constraints : dict|None  — active constraint limits; falls back to CONSTRAINTS defaults

    Returns
    -------
    Path to the written summary.md
    """
    constraints = constraints or CONSTRAINTS
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    status_icon = "PASS" if eval_result["pass"] else "FAIL"
    fos = eval_result["fos"]
    fos_str = f"{fos:.3f}" if math.isfinite(fos) else "∞"

    lines = [
        f"# Bracket FEM — Iteration {iteration:03d}",
        "",
        f"**Status:** {status_icon}",
        "",
    ]

    if render_path is not None and Path(render_path).exists():
        lines += [
            "![Bracket mesh render](render.png)",
            "",
        ]

    if results_render_path is not None and Path(results_render_path).exists():
        lines += [
            "![FEA results](results_render.png)",
            "",
        ]

    lines += [
        "---",
        "",
        "## Geometry Parameters",
        "",
        "| Parameter       | Value (mm) |",
        "|-----------------|------------|",
        f"| flange_width    | {params.get('flange_width', 0)*1e3:.2f} |",
        f"| flange_height   | {params.get('flange_height', 0)*1e3:.2f} |",
        f"| web_height      | {params.get('web_height', 0)*1e3:.2f} |",
        f"| thickness       | {params.get('thickness', 0)*1e3:.2f} |",
        f"| fillet_radius   | {params.get('fillet_radius', 0)*1e3:.2f} |",
        "",
        "---",
        "",
        "## FEM Results",
        "",
        "| Metric                  | Value |",
        "|-------------------------|-------|",
        f"| Max von Mises stress    | {metrics.get('max_von_mises_pa', 0)/1e6:.2f} MPa |",
        f"| Max displacement        | {metrics.get('max_displacement_m', 0)*1e3:.4f} mm |",
        f"| Node count              | {metrics.get('node_count', 0)} |",
        f"| Factor of safety        | {fos_str} |",
        f"| Stress utilisation      | {eval_result.get('stress_utilisation', 0)*100:.1f} % |",
        f"| Mass                    | {eval_result.get('mass_kg', 0):.4f} kg |",
        "",
        "---",
        "",
        "## Constraint Status",
        "",
        "| Constraint              | Limit | Actual | Status |",
        "|-------------------------|-------|--------|--------|",
    ]

    # Stress row — use actual constraint limit, not a recomputed approximation
    max_vm      = metrics.get("max_von_mises_pa", 0)
    allowable_vm = constraints.get("max_von_mises_pa", CONSTRAINTS["max_von_mises_pa"])
    vm_status   = "OK" if max_vm <= allowable_vm else "FAIL"
    lines.append(
        f"| Von Mises stress        | {allowable_vm/1e6:.1f} MPa "
        f"| {max_vm/1e6:.2f} MPa | {vm_status} |"
    )

    # FOS row
    fos_limit  = constraints.get("min_factor_of_safety", CONSTRAINTS["min_factor_of_safety"])
    fos_status = "OK" if (math.isinf(fos) or fos >= fos_limit) else "FAIL"
    lines.append(
        f"| Factor of safety        | ≥ {fos_limit:.1f} "
        f"| {fos_str} | {fos_status} |"
    )

    # Displacement row
    max_disp   = metrics.get("max_displacement_m", 0)
    disp_limit = constraints.get("max_displacement_m", CONSTRAINTS["max_displacement_m"])
    disp_status = "OK" if max_disp <= disp_limit else "FAIL"
    lines.append(
        f"| Max displacement        | {disp_limit*1e3:.1f} mm "
        f"| {max_disp*1e3:.4f} mm | {disp_status} |"
    )

    # Mass row
    mass_kg    = eval_result.get("mass_kg", 0)
    mass_limit = constraints.get("max_mass_kg", CONSTRAINTS["max_mass_kg"])
    if mass_limit is not None:
        mass_status = "OK" if mass_kg <= mass_limit else "FAIL"
        lines.append(
            f"| Mass                    | {mass_limit:.4f} kg "
            f"| {mass_kg:.4f} kg | {mass_status} |"
        )
    else:
        lines.append(
            f"| Mass                    | unconstrained "
            f"| {mass_kg:.4f} kg | OK |"
        )

    lines += ["", "---", ""]

    violations = eval_result.get("violations", [])
    if violations:
        lines.append("## Violations")
        lines.append("")
        for v in violations:
            lines.append(f"- {v}")
        lines.append("")
    else:
        lines.append("## Violations")
        lines.append("")
        lines.append("*None — all constraints satisfied.*")
        lines.append("")

    out_path = output_dir / "summary.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path

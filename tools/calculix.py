"""
CalculiX .inp writer and ccx subprocess runner.

Provides write_inp() to assemble a full analysis input file from mesh,
loads, boundary conditions, and material cards, and run_simulation() to
shell out to the ccx CLI and return paths to the .frd and .dat results.
Raises SimulationError if ccx exits non-zero.
"""

import logging
import math
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class SimulationError(Exception):
    """Raised when CalculiX simulation fails."""


def _parse_mesh_inp(mesh_path: Path) -> tuple[dict, list, list]:
    """
    Parse a Gmsh-written mesh.inp and return:
      nodes                : dict {node_id: (x, y, z)}
      element_header_lines : list of *ELEMENT header strings
      element_lines        : list of raw element data lines (strings)

    Handles multiple *NODE and *ELEMENT sections.
    """
    nodes: dict[int, tuple[float, float, float]] = {}
    element_lines: list[str] = []
    element_header_lines: list[str] = []

    in_node = False
    in_element = False
    current_elem_header = ""

    with mesh_path.open(encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            upper = stripped.upper()

            if upper.startswith("*NODE") and not upper.startswith("*NODE PRINT"):
                in_node = True
                in_element = False
                continue

            if upper.startswith("*ELEMENT"):
                in_element = True
                in_node = False
                current_elem_header = stripped
                element_header_lines.append(stripped)
                continue

            if stripped.startswith("*"):
                in_node = False
                in_element = False
                continue

            if in_node and stripped:
                parts = stripped.split(",")
                if len(parts) >= 4:
                    try:
                        nid = int(parts[0].strip())
                        x = float(parts[1].strip())
                        y = float(parts[2].strip())
                        z = float(parts[3].strip())
                        nodes[nid] = (x, y, z)
                    except ValueError:
                        pass

            if in_element and stripped:
                element_lines.append(stripped)

    return nodes, element_header_lines, element_lines


def write_inp(
    mesh_path: Path,
    loads: dict,
    bcs: dict,
    material: dict,
    output_dir: Path,
) -> Path:
    """
    Assemble a full CalculiX analysis.inp from mesh + cards.

    Parameters
    ----------
    mesh_path  : Path — mesh.inp from Gmsh
    loads      : dict — {type, magnitude_n, direction} (e.g. point_force, -Z)
    bcs        : dict — {type: "fixed_face"} (web back face)
    material   : dict — {E_pa, nu, rho, Sy_pa}
    output_dir : Path

    Returns
    -------
    Path to analysis.inp
    """
    mesh_path  = Path(mesh_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Parse mesh
    logger.info("Parsing mesh: %s", mesh_path)
    nodes, elem_headers, elem_lines = _parse_mesh_inp(mesh_path)
    if not nodes:
        raise SimulationError(f"No nodes parsed from {mesh_path}")

    # 2. Identify FIXED node set: back web face at X ≈ 0
    tol = 1e-6
    fixed_nodes = [nid for nid, (x, y, z) in nodes.items() if abs(x) < tol]
    if not fixed_nodes:
        # Fallback: use x < 1% of x-range
        all_x = [xyz[0] for xyz in nodes.values()]
        x_range = max(all_x) - min(all_x)
        tol_fb = x_range * 0.01
        fixed_nodes = [nid for nid, (x, y, z) in nodes.items() if x < tol_fb]
        logger.warning(
            "No nodes found at x=0 (tol=1e-6); using 1%% x-range fallback (tol=%.3e). "
            "Check geometry alignment.",
            tol_fb,
        )
    logger.debug("Fixed nodes (back face): %d", len(fixed_nodes))

    # 3. Identify TIP node: closest to (flange_width, flange_height/2, web_height - thickness)
    # We need geometry params — infer from bounding box if not in bcs
    params = bcs.get("params", {})
    all_x = [xyz[0] for xyz in nodes.values()]
    all_y = [xyz[1] for xyz in nodes.values()]
    all_z = [xyz[2] for xyz in nodes.values()]

    fw = params.get("flange_width",  max(all_x))
    fh = params.get("flange_height", (max(all_y) + min(all_y)) / 2.0)
    wh = params.get("web_height",    max(all_z))
    t  = params.get("thickness",     (max(all_z) - min(all_z)) * 0.05)

    tip_target = (fw, fh / 2.0, wh - t / 2.0)
    tip_node = min(
        nodes.keys(),
        key=lambda nid: math.dist(nodes[nid], tip_target),
    )
    logger.debug("Tip node %d at %s", tip_node, nodes[tip_node])

    # 4. Determine load DOF and sign
    direction = loads.get("direction", "-Z").upper().strip()
    magnitude = float(loads.get("magnitude_n", 0.0))
    dof_map = {"X": 1, "Y": 2, "Z": 3, "-X": 1, "-Y": 2, "-Z": 3}
    load_dof = dof_map.get(direction, 3)
    load_sign = -1.0 if direction.startswith("-") else 1.0
    load_value = load_sign * magnitude

    E_pa  = material["E_pa"]
    nu    = material["nu"]
    rho   = material["rho"]

    # 5. Read raw mesh.inp for node/element blocks
    raw_mesh = mesh_path.read_text(encoding="utf-8")

    # 6. Assemble analysis.inp
    out_path = output_dir / "analysis.inp"
    lines = []

    # --- Paste mesh nodes and elements verbatim ---
    lines.append("** CalculiX analysis generated by bracket-agent")
    lines.append("**")

    # Re-emit *NODE block
    lines.append("*NODE, NSET=NALL")
    for nid in sorted(nodes.keys()):
        x, y, z = nodes[nid]
        lines.append(f"{nid}, {x:.10e}, {y:.10e}, {z:.10e}")

    # Re-emit *ELEMENT blocks — only 3D solid elements (C3D*) go into EALL;
    # surface/edge element groups written by Gmsh are silently dropped so that
    # *SOLID SECTION, ELSET=EALL is only applied to volume elements.
    in_elem = False
    for raw_line in raw_mesh.splitlines():
        stripped = raw_line.strip()
        upper = stripped.upper()
        if upper.startswith("*ELEMENT"):
            parts = [p.strip() for p in stripped.split(",")]
            type_part = next((p for p in parts if p.upper().startswith("TYPE")), "")
            elem_type = type_part.split("=")[-1].strip().upper()
            if elem_type.startswith("C3D"):
                # 3D solid element — keep and force ELSET=EALL
                parts = [p for p in parts if not p.upper().startswith("ELSET")]
                parts.append("ELSET=EALL")
                lines.append(", ".join(parts))
                in_elem = True
            else:
                # Surface/edge element — skip
                in_elem = False
            continue
        if stripped.startswith("*"):
            in_elem = False
            continue
        if in_elem and stripped:
            lines.append(stripped)

    # --- Node sets ---
    lines.append("*NSET, NSET=NALL, GENERATE")
    nids_sorted = sorted(nodes.keys())
    lines.append(f"{nids_sorted[0]}, {nids_sorted[-1]}, 1")

    lines.append("*NSET, NSET=FIXED")
    # Write in chunks of 16
    for i in range(0, len(fixed_nodes), 16):
        chunk = fixed_nodes[i:i + 16]
        lines.append(", ".join(str(n) for n in chunk))

    lines.append("*NSET, NSET=TIP")
    lines.append(str(tip_node))

    # --- Material ---
    lines.append(f"*MATERIAL, NAME=STEEL")
    lines.append("*ELASTIC")
    lines.append(f"{E_pa:.6e}, {nu:.4f}")
    lines.append("*DENSITY")
    lines.append(f"{rho:.4f}")
    lines.append("*SOLID SECTION, ELSET=EALL, MATERIAL=STEEL")

    # --- Step ---
    lines.append("*STEP")
    lines.append("*STATIC")

    # Boundary conditions: FIXED face, all 6 DOF
    lines.append("*BOUNDARY")
    lines.append("FIXED, 1, 6")

    # Load
    lines.append("*CLOAD")
    lines.append(f"TIP, {load_dof}, {load_value:.6e}")

    # Output requests
    lines.append("*NODE PRINT, NSET=NALL")
    lines.append("U")
    lines.append("*EL PRINT, ELSET=EALL")
    lines.append("S")
    lines.append("*NODE FILE")
    lines.append("U")
    lines.append("*EL FILE")
    lines.append("S")

    lines.append("*END STEP")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Analysis .inp written: %s", out_path)
    return out_path


def run_simulation(inp_path: Path) -> tuple[Path, Path]:
    """
    Run CalculiX on inp_path and return (frd_path, dat_path).

    Parameters
    ----------
    inp_path : Path — analysis.inp

    Returns
    -------
    (frd_path, dat_path)

    Raises
    ------
    SimulationError on ccx failure or missing output files.
    """
    inp_path   = Path(inp_path)
    output_dir = inp_path.parent
    stem       = inp_path.stem  # e.g. "analysis"

    logger.info("Running ccx: %s", inp_path)
    try:
        result = subprocess.run(
            ["ccx", "-i", stem],
            cwd=str(output_dir),
            capture_output=True,
            text=True,
            timeout=600,
        )
    except FileNotFoundError as exc:
        raise SimulationError("ccx not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise SimulationError("ccx timed out after 600 s") from exc

    if result.returncode != 0:
        raise SimulationError(
            f"ccx exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    frd_path = output_dir / f"{stem}.frd"
    dat_path = output_dir / f"{stem}.dat"

    if not frd_path.exists():
        raise SimulationError(
            f"{frd_path.name} not produced by ccx.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    if not dat_path.exists():
        raise SimulationError(
            f"{dat_path.name} not produced by ccx.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    logger.info("Simulation complete: %s, %s", frd_path, dat_path)
    return frd_path, dat_path

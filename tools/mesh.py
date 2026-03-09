"""
Gmsh meshing of STEP geometry to a CalculiX-compatible mesh.inp.

Provides generate_mesh() which loads a STEP file via the Gmsh Python API,
applies quality settings, and writes a C3D10 (quadratic tet) mesh in
Abaqus/CalculiX .inp format. Raises MeshError on failure or empty mesh.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

QUALITY_MESH_SIZE = {
    "coarse": 0.010,
    "medium": 0.005,
    "fine":   0.002,
}


class MeshError(Exception):
    """Raised when Gmsh meshing fails or produces an empty mesh."""


def _validate_mesh_inp(inp_path: Path) -> tuple[int, int]:
    """
    Scan mesh.inp and return (node_count, element_count).
    Raises MeshError if either count is zero.
    """
    node_count = 0
    element_count = 0
    in_node_section = False
    in_element_section = False

    with inp_path.open(encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped.upper().startswith("*NODE"):
                in_node_section = True
                in_element_section = False
                continue
            if stripped.upper().startswith("*ELEMENT"):
                in_element_section = True
                in_node_section = False
                continue
            if stripped.startswith("*"):
                in_node_section = False
                in_element_section = False
                continue
            if in_node_section and stripped:
                node_count += 1
            if in_element_section and stripped:
                element_count += 1

    if node_count == 0:
        raise MeshError(f"mesh.inp contains zero nodes: {inp_path}")
    if element_count == 0:
        raise MeshError(f"mesh.inp contains zero elements: {inp_path}")

    return node_count, element_count


def generate_mesh(
    step_path: Path,
    output_dir: Path,
    quality: str = "medium",
) -> Path:
    """
    Mesh a STEP file with Gmsh and write a C3D10 mesh.inp.

    Parameters
    ----------
    step_path  : Path — input STEP geometry
    output_dir : Path — directory to write mesh.inp
    quality    : str  — "coarse" | "medium" | "fine"

    Returns
    -------
    Path to mesh.inp

    Raises
    ------
    MeshError on failure or empty mesh.
    """
    try:
        import gmsh
    except ImportError as exc:
        raise MeshError("gmsh Python package not installed") from exc

    step_path  = Path(step_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "mesh.inp"

    if quality not in QUALITY_MESH_SIZE:
        logger.warning("Unknown mesh quality %r — defaulting to 'medium'", quality)
    char_len = QUALITY_MESH_SIZE.get(quality, QUALITY_MESH_SIZE["medium"])
    logger.info("Meshing %s (quality=%s, lc=%.3f m)", step_path, quality, char_len)

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", char_len)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", char_len * 0.5)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature",  0)  # no curve refinement
        gmsh.option.setNumber("Mesh.Algorithm",   5)  # 2D Delaunay
        gmsh.option.setNumber("Mesh.Algorithm3D", 1)  # 3D Delaunay

        gmsh.model.add("bracket")
        gmsh.model.occ.importShapes(str(step_path))
        gmsh.model.occ.synchronize()

        volumes = gmsh.model.getEntities(3)
        if not volumes:
            raise MeshError(f"No 3D volumes found in STEP: {step_path}")

        logger.debug("Gmsh volumes found: %d", len(volumes))

        gmsh.model.mesh.generate(3)
        gmsh.model.mesh.setOrder(2)   # quadratic → C3D10

        gmsh.write(str(out_path))
        logger.info("Mesh written: %s", out_path)

    except MeshError:
        raise
    except Exception as exc:
        raise MeshError(f"Gmsh meshing failed: {exc}") from exc
    finally:
        gmsh.finalize()

    if not out_path.exists():
        raise MeshError(f"mesh.inp not created by Gmsh: {out_path}")

    node_count, element_count = _validate_mesh_inp(out_path)
    logger.info("Mesh validated: %d nodes, %d elements", node_count, element_count)
    return out_path

"""
Multi-view mesh render to PNG using matplotlib.

Provides render_mesh() which reads a Gmsh-written mesh.inp, extracts nodes
and surface triangles, and saves a 2×2 panel PNG (isometric, front, side, top)
via matplotlib's Agg backend (no display required). Returns None gracefully if
matplotlib is not installed.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Surface triangle element types written by Gmsh in Abaqus format
_SURFACE_TYPES = {"S3", "CPS3", "CPE3", "STRI3", "CPS6", "CPE6", "STRI65", "S6"}

# (title, elev, azim)
_VIEWS = [
    ("Isometric",  25, -55),
    ("Front (XZ)",  0,  -90),
    ("Side (YZ)",   0,    0),
    ("Top (XY)",   90,  -90),
]


def _parse_mesh(mesh_path: Path):
    """Return (nodes dict, triangles list) from a Gmsh mesh.inp."""
    nodes: dict[int, tuple[float, float, float]] = {}
    triangles: list = []
    in_node = False
    in_tri = False

    with Path(mesh_path).open(encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            upper = stripped.upper()

            if upper.startswith("*NODE") and "PRINT" not in upper:
                in_node = True
                in_tri = False
                continue

            if upper.startswith("*ELEMENT"):
                in_node = False
                type_part = next(
                    (p for p in stripped.split(",") if "TYPE" in p.upper()), ""
                )
                elem_type = type_part.split("=")[-1].strip().upper()
                in_tri = elem_type in _SURFACE_TYPES
                continue

            if stripped.startswith("*"):
                in_node = False
                in_tri = False
                continue

            if in_node and stripped:
                parts = stripped.split(",")
                if len(parts) >= 4:
                    try:
                        nid = int(parts[0].strip())
                        nodes[nid] = (
                            float(parts[1].strip()),
                            float(parts[2].strip()),
                            float(parts[3].strip()),
                        )
                    except ValueError:
                        pass

            if in_tri and stripped:
                parts = stripped.split(",")
                if len(parts) >= 4:
                    try:
                        n1 = int(parts[1].strip())
                        n2 = int(parts[2].strip())
                        n3 = int(parts[3].strip())
                        if n1 in nodes and n2 in nodes and n3 in nodes:
                            triangles.append([nodes[n1], nodes[n2], nodes[n3]])
                    except (ValueError, IndexError):
                        pass

    return nodes, triangles


def _draw_view(ax, nodes, triangles, xs, ys, zs, title, elev, azim, np, plt):
    """Populate a single 3D axes with the mesh and set the view angle."""
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    if triangles:
        z_avg = np.array([sum(v[2] for v in tri) / 3.0 for tri in triangles])
        z_min, z_max = z_avg.min(), z_avg.max()
        norm = (z_avg - z_min) / max(z_max - z_min, 1e-12)
        facecolors = [plt.cm.Blues(0.30 + 0.55 * n) for n in norm]
        poly = Poly3DCollection(
            triangles,
            facecolors=facecolors,
            edgecolor="navy",
            linewidth=0.2,
            alpha=0.85,
        )
        ax.add_collection3d(poly)
    else:
        ax.scatter(xs, ys, zs, c=zs, cmap="Blues", s=1, alpha=0.5)

    ax.set_xlim(min(xs), max(xs))
    ax.set_ylim(min(ys), max(ys))
    ax.set_zlim(min(zs), max(zs))
    ax.set_xlabel("X", fontsize=7, labelpad=2)
    ax.set_ylabel("Y", fontsize=7, labelpad=2)
    ax.set_zlabel("Z", fontsize=7, labelpad=2)
    ax.tick_params(labelsize=6)
    ax.set_title(title, fontsize=9, pad=4)
    ax.view_init(elev=elev, azim=azim)


def _draw_result_view(ax, nodes, triangles, xs, ys, zs,
                      scalar, cmap_name, title, elev, azim, np, plt):
    """Draw one FEA result view coloured by a per-node scalar dict."""
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    vmin = min(scalar.values()) if scalar else 0.0
    vmax = max(scalar.values()) if scalar else 1.0
    cmap = plt.get_cmap(cmap_name)
    norm_fn = lambda v: (v - vmin) / max(vmax - vmin, 1e-30)

    if triangles:
        # Build position→node_id reverse map for scalar lookup
        pos_to_id = {xyz: nid for nid, xyz in nodes.items()}
        face_vals = []
        for tri in triangles:
            vals = [scalar.get(pos_to_id.get(tuple(v), -1), 0.0) for v in tri]
            face_vals.append(sum(vals) / 3.0)

        face_norm = [norm_fn(v) for v in face_vals]
        facecolors = [cmap(n) for n in face_norm]
        poly = Poly3DCollection(
            triangles,
            facecolors=facecolors,
            edgecolor="none",
            alpha=0.95,
        )
        ax.add_collection3d(poly)
    else:
        # Scatter fallback coloured by scalar
        node_ids = list(nodes.keys())
        sx = [nodes[n][0] for n in node_ids]
        sy = [nodes[n][1] for n in node_ids]
        sz = [nodes[n][2] for n in node_ids]
        sv = [scalar.get(n, 0.0) for n in node_ids]
        sc = ax.scatter(sx, sy, sz, c=sv, cmap=cmap_name,
                        vmin=vmin, vmax=vmax, s=4, alpha=0.8)

    ax.set_xlim(min(xs), max(xs))
    ax.set_ylim(min(ys), max(ys))
    ax.set_zlim(min(zs), max(zs))
    ax.set_xlabel("X", fontsize=7, labelpad=2)
    ax.set_ylabel("Y", fontsize=7, labelpad=2)
    ax.set_zlabel("Z", fontsize=7, labelpad=2)
    ax.tick_params(labelsize=6)
    ax.set_title(title, fontsize=9, pad=4)
    ax.view_init(elev=elev, azim=azim)

    # Colourbar — attach to axes
    sm = plt.cm.ScalarMappable(cmap=cmap_name,
                               norm=plt.Normalize(vmin=vmin, vmax=vmax))
    sm.set_array([])
    plt.colorbar(sm, ax=ax, shrink=0.55, pad=0.08, aspect=15)


def render_results(
    mesh_path: Path,
    frd_path: Path,
    output_dir: Path,
) -> "Path | None":
    """
    Render FEA results as a 2×2 panel PNG (results_render.png).

    Layout:
      [Von Mises — isometric]  [Displacement — isometric]
      [Von Mises — front XZ ]  [Displacement — front XZ ]

    Parameters
    ----------
    mesh_path  : Path — Gmsh mesh.inp (for node positions + surface triangles)
    frd_path   : Path — CalculiX .frd results file
    output_dir : Path — directory to write results_render.png into

    Returns
    -------
    Path to results_render.png, or None if matplotlib is unavailable.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        logger.warning("matplotlib not available — skipping results render")
        return None

    from tools.results import parse_frd_nodal

    disp_mag, von_mises_pa = parse_frd_nodal(frd_path)
    von_mises = {k: v / 1e6 for k, v in von_mises_pa.items()}
    if not disp_mag and not von_mises:
        logger.warning("No nodal results in %s — skipping results render", frd_path)
        return None

    nodes, triangles = _parse_mesh(mesh_path)
    if not nodes:
        logger.warning("No nodes in %s — skipping results render", mesh_path)
        return None

    all_pts = list(nodes.values())
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    zs = [p[2] for p in all_pts]

    panels = [
        (von_mises, "jet",     "Von Mises (MPa) — Isometric", 25, -55),
        (disp_mag,  "viridis", "Displacement (m) — Isometric", 25, -55),
        (von_mises, "jet",     "Von Mises (MPa) — Front",       0, -90),
        (disp_mag,  "viridis", "Displacement (m) — Front",      0, -90),
    ]

    fig = plt.figure(figsize=(14, 10))
    fig.suptitle("FEA Results", fontsize=13, y=0.99)

    for idx, (scalar, cmap_name, title, elev, azim) in enumerate(panels, start=1):
        ax = fig.add_subplot(2, 2, idx, projection="3d")
        _draw_result_view(ax, nodes, triangles, xs, ys, zs,
                          scalar, cmap_name, title, elev, azim, np, plt)

    fig.tight_layout()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "results_render.png"
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)

    logger.info("Results render saved: %s", out_path)
    return out_path


def render_mesh(mesh_path: Path, output_dir: Path) -> "Path | None":
    """
    Render four views of the mesh (isometric, front, side, top) as render.png.

    Parameters
    ----------
    mesh_path  : Path — Gmsh-written mesh.inp
    output_dir : Path — directory to write render.png into

    Returns
    -------
    Path to render.png, or None if matplotlib is unavailable or mesh is empty.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        logger.warning("matplotlib not available — skipping render")
        return None

    nodes, triangles = _parse_mesh(mesh_path)
    if not nodes:
        logger.warning("No nodes found in %s — skipping render", mesh_path)
        return None

    all_pts = list(nodes.values())
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    zs = [p[2] for p in all_pts]

    fig = plt.figure(figsize=(12, 9))
    fig.suptitle("Bracket Mesh", fontsize=12, y=0.98)

    for idx, (title, elev, azim) in enumerate(_VIEWS, start=1):
        ax = fig.add_subplot(2, 2, idx, projection="3d")
        _draw_view(ax, nodes, triangles, xs, ys, zs, title, elev, azim, np, plt)

    fig.tight_layout()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "render.png"
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)

    logger.info("Render saved: %s", out_path)
    return out_path

"""
Standalone debug script for test_generate_mesh_creates_inp.
Run directly: python tests/debug_mesh_creates_inp.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.geometry import create_geometry
from tools.mesh import generate_mesh, _validate_mesh_inp

PARAMS = {
    "flange_width":  0.08,
    "flange_height": 0.06,
    "web_height":    0.10,
    "thickness":     0.006,
    "fillet_radius": 0.004,
}

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)

    print("Step 1: create_geometry (no fillet) ...")
    step_path = create_geometry(PARAMS, tmp / "geo", apply_fillet=True)
    print(f"  OK — {step_path} ({step_path.stat().st_size} bytes)")

    print("Step 2: generate_mesh (coarse) ...")
    mesh_path = generate_mesh(step_path, tmp / "mesh", quality="coarse")
    print(f"  OK — {mesh_path} ({mesh_path.stat().st_size} bytes)")

    print("Step 3: validate mesh.inp ...")
    nc, ec = _validate_mesh_inp(mesh_path)
    print(f"  OK — {nc} nodes, {ec} elements")

    text = mesh_path.read_text(encoding="utf-8")
    assert "*NODE" in text, "missing *NODE"
    assert "*ELEMENT" in text, "missing *ELEMENT"

print("\nAll checks passed.")

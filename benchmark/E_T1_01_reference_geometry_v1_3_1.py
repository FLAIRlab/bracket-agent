import cadquery as cq

# E-T1-01 reference geometry
# Units: mm

# -----------------------------
# Parameters
# -----------------------------
t = 5.0                # common wall thickness / y-leg thickness
x_leg_width_y = 22.0   # widened x-leg in +y so the load pad is fully supported
Lx = 60.0              # x-leg length
Ly = 60.0              # y-leg length
H = 64.0               # overall height in z
r_root = 3.0           # internal root fillet radius

hole_d = 6.6
z_hole = 22.0
offset_face = 18.0

# -----------------------------
# Build two perpendicular legs
# -----------------------------
# Leg A (x-dominant leg): x in [0, Lx], y in [0, x_leg_width_y], z in [-H/2, H/2]
leg_x = (
    cq.Workplane("XY")
    .box(Lx, x_leg_width_y, H, centered=(False, False, True))
)

# Leg B (y-dominant leg): x in [0, t], y in [0, Ly], z in [-H/2, H/2]
leg_y = (
    cq.Workplane("XY")
    .box(t, Ly, H, centered=(False, False, True))
)

part = leg_x.union(leg_y)

# -----------------------------
# Fillet the internal re-entrant vertical edge
# -----------------------------
# The intended edge is the inner vertical edge closest to (x=t, y=x_leg_width_y, z=0).
# This is more stable than using the original (t, t, 0) selector.
part = (
    part.edges("|Z")
    .nearestTo((t, x_leg_width_y, 0))
    .fillet(r_root)
)

# -----------------------------
# Holes on x=0 face
# Centers: (0, 18, -22), (0, 18, 22)
# -----------------------------
part = (
    part.faces("<X")
    .workplane()
    .center(offset_face, -z_hole)
    .hole(hole_d)
)

part = (
    part.faces("<X")
    .workplane()
    .center(offset_face, z_hole)
    .hole(hole_d)
)

# -----------------------------
# Holes on y=0 face
# Centers: (18, 0, -22), (18, 0, 22)
# -----------------------------
part = (
    part.faces("<Y")
    .workplane()
    .center(offset_face, -z_hole)
    .hole(hole_d)
)

part = (
    part.faces("<Y")
    .workplane()
    .center(offset_face, z_hole)
    .hole(hole_d)
)

# -----------------------------
# Optional visual marker for the load pad
# Task load pad center: (52, 10, 0)
# Size: 22 mm in x, 20 mm in z
# This now lies fully on the widened x-leg.
# -----------------------------
show_load_pad = False
if show_load_pad:
    pad_x = 22.0
    pad_z = 20.0
    pad_depth = 0.5

    # On the +Y face of the x-leg, workplane axes are X and Z.
    part = (
        part.faces(">Y")
        .workplane()
        .center(52.0 - Lx / 2.0, 0.0)
        .rect(pad_x, pad_z)
        .cutBlind(-pad_depth)
    )

# Export if needed:
# cq.exporters.export(part, "E_T1_01_reference.step")

show_object(part)

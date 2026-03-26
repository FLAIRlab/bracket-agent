import cadquery as cq

# E-T1-01 reference geometry
# Units: mm

# -----------------------------
# Parameters
# -----------------------------
t = 5.0           # leg thickness
Lx = 60.0         # x-leg length
Ly = 60.0         # y-leg length
H = 64.0          # overall height in z
r_root = 3.0      # internal root fillet radius

hole_d = 6.6
z_hole = 22.0
offset_face = 18.0

# z-range is [-H/2, H/2]
z0 = -H / 2.0

# -----------------------------
# Build two perpendicular legs
# -----------------------------
# Leg A: x in [0, Lx], y in [0, t], z in [-H/2, H/2]
leg_x = (
    cq.Workplane("XY")
    .box(Lx, t, H, centered=(False, False, True))
)

# Leg B: x in [0, t], y in [0, Ly], z in [-H/2, H/2]
leg_y = (
    cq.Workplane("XY")
    .box(t, Ly, H, centered=(False, False, True))
)

part = leg_x.union(leg_y)

# -----------------------------
# Fillet the internal re-entrant vertical edge
# -----------------------------
# The inner edge is the vertical line near (x=t, y=t)
# Select vertical edges and keep the one closest to (t, t, 0)
selector = cq.selectors.NearestToPointSelector((t, t, 0))
part = part.edges("|Z").edges(selector).fillet(r_root)

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
# Optional: show load pad area as a shallow marker pocket
# Comment out if you want a pure structural body only.
# Load pad center: (52, 10, 0)
# Size: 22 mm in x, 20 mm in z
# -----------------------------
show_load_pad = False
if show_load_pad:
    pad_x = 22.0
    pad_z = 20.0
    pad_depth = 0.5

    # The load pad lies on the top face of the x-dominant leg at y = t
    part = (
        part.faces(">Y")
        .workplane()
        .center(52.0 - Lx / 2.0, 0.0)  # on >Y face, WP axes are X and Z
        .rect(pad_x, pad_z)
        .cutBlind(-pad_depth)
    )

# Export if needed:
# cq.exporters.export(part, "E_T1_01_reference.step")

if "show_object" in globals():
    show_object(part)
else:
    cq.exporters.export(part, "benchmark/E_T1_01_reference.step")
    print("Exported: benchmark/E_T1_01_reference.step")
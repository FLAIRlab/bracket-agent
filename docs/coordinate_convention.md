# Bracket Coordinate Convention

All geometry scripts share the same axis orientation. Units are **metres** in the
STEP file (FreeCAD works in mm internally, then scales 1e-3 before export).

---

## L-bracket

```
        +Z (up)
        │
        │  web
        │ ┌────┐
        │ │    │  ← fixed face (x ≈ 0)
        │ │    │
        │ └────┴──────────────── +X (flange direction)
        │        flange
       (0,0,0)
              ↗ +Y (out-of-plane)
```

| Feature     | Location                                              |
|-------------|-------------------------------------------------------|
| Web         | X=[0, t], Y=[0, fh], Z=[0, wh]                        |
| Flange      | X=[0, fw], Y=[0, fh], Z=[wh-t, wh]                   |
| Fixed face  | x ≈ 0  (tol=1e-6 m; 1% x-range fallback + WARNING)   |
| Tip node    | Closest to (fw, fh/2, wh − t/2)                      |

---

## T-bracket (inverted T)

```
        +Z (up)
        │
     ───┴────────── +X (flange spans full width)
        │
        │  web (centered at x = fw/2)
        │
       (0,0,0) ← fixed face (z ≈ 0)
              ↗ +Y (out-of-plane)
```

| Feature     | Location                                              |
|-------------|-------------------------------------------------------|
| Web         | X=[fw/2−t/2, fw/2+t/2], Y=[0, fh], Z=[0, wh]        |
| Flange      | X=[0, fw], Y=[0, fh], Z=[wh−t, wh]                   |
| Fixed face  | z ≈ 0  (tol=1e-6 m; 1% z-range fallback + WARNING)   |
| Tip node    | Closest to (fw/2, fh/2, wh − t/2)                    |

---

## U-bracket (channel section)

```
        +Z (up)
        │
  ┌─────┼─────┐   ← top of walls (z = wh)
  │     │     │
  │     │     │   walls
  │     │     │
  └─────┴─────┘   ← base + fixed face (z ≈ 0)
  X=0         X=cw

       (0,0,0) ↗ +Y (out-of-plane, depth = cd)
```

| Feature     | Location                                              |
|-------------|-------------------------------------------------------|
| Base        | X=[0, cw], Y=[0, cd], Z=[0, t]                       |
| Left wall   | X=[0, t], Y=[0, cd], Z=[0, wh]                       |
| Right wall  | X=[cw−t, cw], Y=[0, cd], Z=[0, wh]                   |
| Fixed face  | z ≈ 0  (tol=1e-6 m; 1% z-range fallback + WARNING)   |
| Tip node    | Closest to (0, cd/2, wh)  — top of left wall centre  |

---

## Notes

- `fillet_radius ≤ thickness × 0.45` is enforced after clamping to param bounds.
- The fixed node selection uses tolerance 1e-6 m; if no nodes are found, a 1%
  range fallback is applied and a WARNING is logged.
- The tip node is the closest mesh node to the analytical target point; for
  coarse meshes this may deviate from the exact corner.

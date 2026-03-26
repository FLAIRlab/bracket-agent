# L-Bracket Benchmark v1.2 Revised Expansion Plan

## Goal
Make v1.2 a **focused corner-bracket realism expansion** for the existing L-bracket benchmark.

This revision intentionally narrows scope compared with the earlier v1.2 draft. The purpose is to:
- preserve benchmark coherence,
- add one clearly new structural/use-case family,
- keep evaluation automation manageable,
- and avoid mixing in semi-related benchmark classes too early.

---

## Why the scope is being narrowed

The earlier v1.2 draft proposed three realism groups:
- Group E: corner-bracket realism
- Group F: strut-channel realism
- Group G: conveyor roller support realism

After review, the best benchmark-design decision is:

### Keep
- **Group E: corner-bracket realism**

### Defer
- **Group F: strut-channel realism** to a future v1.3 or separate strut-focused benchmark

### Remove from this benchmark
- **Group G: conveyor roller support realism**, which is better treated as a future paired-support / axle-support benchmark

The reason is simple:
- Group E is still unmistakably part of the L-bracket family
- Group F depends on a more specialized mating ecosystem
- Group G drifts into a different benchmark class involving paired supports and axle interfaces

So v1.2 should add one clean new structural family, not three.

---

## v1.2 scope

## New benchmark group
### Group E — Corner-Bracket Realism

This group expands the benchmark from single-plane mounting to **corner-mounted and corner-reinforcing use cases**.

These tasks are still L-brackets in a strong sense, but they introduce a new and important use-case regime:
- two-plane attachment contexts,
- corner accessibility constraints,
- inside-corner vs outside-corner geometry differences,
- wraparound reinforcement patterns,
- and slot-driven mounting flexibility.

This is enough novelty for one version increment.

---

## Why Group E is the right next step

Corner brackets are a strong benchmark expansion because they are:

### 1. Clearly still L-brackets
They preserve the core right-angle bracket concept.

### 2. Structurally distinct from current Tier 1 tasks
They introduce:
- intersecting support surfaces,
- altered load paths,
- different stress concentrations,
- and more geometry-conditioned mounting patterns.

### 3. Still automatable
They do not require:
- full nonlinear contact,
- axle interfaces,
- channel-specific mating logic,
- or paired-part coordination.

That makes them realistic **and** benchmark-friendly.

---

## What v1.2 should NOT include

To keep the benchmark focused, v1.2 should not include:

### Deferred to later
- strut-channel corner / offset / wraparound brackets
- twist-resistant channel brackets
- shelf-style channel brackets

### Removed from this benchmark track
- conveyor roller support brackets
- paired roller brackets
- round/hex axle bracket variants
- package-stop brackets

These are useful ideas, but they belong in later or separate benchmarks.

---

## Proposed v1.2 task set

v1.2 should add **4 Group E tasks**.

## E-T1-01 — Inside-corner bracket, round holes
A clean entry task for the new family.

### Purpose
- Introduce inside-corner mounting
- Keep hole geometry simple
- Focus on two-plane corner geometry without added slot complexity

### Why first
This is the most benchmark-stable way to introduce Group E.

---

## E-T1-02 — Outside-corner bracket
A geometrically different corner-mounted case.

### Purpose
- Test outward-facing reinforcement geometry
- Change load path and free-edge conditions relative to inside-corner mounting
- Introduce packaging differences without adding exotic features

---

## E-T1-03 — Wraparound corner bracket
A stronger / more realistic reinforcement style.

### Purpose
- Model wraparound engagement around a corner region
- Test whether the solver uses enclosing geometry efficiently
- Represent a real catalog-visible product style

### Notes
This should remain a static structural task, not a contact task. The wraparound condition should be expressed geometrically, not through nonlinear surface contact.

---

## E-T1-04 — Inside-corner bracket with slotted holes
A more advanced mounting-variation case.

### Purpose
- Introduce slot-driven flexibility and stress concentration
- Test whether algorithms reason about hole-shape tradeoffs
- Stay within the same structural family introduced by E-T1-01

### Why it comes last
Slots should not appear in the first corner task. They should be layered in only after the corner-mounted baseline is established.

---

## Task sequencing principle

The 4 tasks should be ordered so that each introduces only one major new effect at a time:

1. **Inside-corner, round holes**
2. **Outside-corner**
3. **Wraparound**
4. **Slotted-hole inside-corner**

This sequencing keeps the benchmark interpretable and easier to debug.

---

## Schema changes needed for v1.2

Only add schema fields needed for Group E.

## 1. Product-family metadata
```json
"product_family": {
  "catalog_track": "core_mechanics | corner_bracket",
  "subtype": "inside_corner | outside_corner | wraparound | surface_corner"
}
```

## 2. Mating-system metadata
```json
"mating_system": {
  "type": "flat_plane | inside_corner | outside_corner",
  "standard_size": null,
  "alignment_feature_required": false
}
```

## 3. Feature requirements
```json
"feature_requirements": {
  "slotted_holes_allowed": false,
  "slotted_holes_required": false,
  "wraparound_required": false,
  "concealed_profile_required": false
}
```

That is enough for v1.2.

### Not needed in v1.2
Do **not** add yet:
- `channel_interface`
- `axle_interface`
- `assembly_constraints` for paired parts

Those belong to later expansions.

---

## Evaluation policy for Group E

Use the same evaluation philosophy as the existing L-bracket benchmark:

- linear static analysis
- explicit envelope ranges
- explicit mounting interfaces
- explicit load interfaces
- bracket-body-only mass
- no nonlinear contact
- no fastener preload modeling
- no substrate failure modeling

### Important modeling choice
Corner behavior should be represented through:
- geometry,
- mounting-hole locations,
- and envelope constraints,

not through detailed wall-to-wall contact modeling.

This keeps evaluation reproducible.

---

## Relationship to the current benchmark

v1.2 should **extend**, not replace, the current benchmark.

### Existing families remain
- Family A: plain cantilever
- Family B: base-mounted upright
- Family C: gusseted cantilever
- Family D: sheet-metal bent

### New family added
- Group E: corner-bracket realism

So the benchmark becomes:
- **core mechanics track**
- plus a **corner realism extension**

---

## Deliverables for v1.2

## Deliverable 1
A revised schema note describing the new Group E fields.

## Deliverable 2
A JSON file containing 4 detailed Group E tasks:
- E-T1-01
- E-T1-02
- E-T1-03
- E-T1-04

## Deliverable 3
Reference-solution placeholders for each of the 4 tasks.

---

## Success criteria for v1.2

v1.2 succeeds if it:

1. adds a clearly new and realistic L-bracket use-case family,
2. remains structurally coherent with the current benchmark,
3. does not require major new solver assumptions,
4. does not introduce unrelated benchmark classes,
5. and produces 4 corner-bracket tasks that are easy to interpret and compare.

---

## Recommended immediate next step

After approving this revised plan, the next deliverable should be:

**`l_bracket_benchmark_v1_2_corner_tasks_draft.json`**

That draft should contain the 4 detailed Group E corner-bracket tasks in the same style as the current L-bracket benchmark.

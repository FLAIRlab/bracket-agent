# L-Bracket Benchmark v1.2 Expansion Plan

## Goal
Expand the current L-bracket benchmark from a **core mechanics track** into a more realistic **catalog-shaped benchmark** by incorporating product-family patterns seen in real industrial catalogs.

The intent of v1.2 is **not** to replace the current Tier 1 benchmark. Instead, it adds a second layer of realism focused on:
- mounting location,
- mating system,
- reinforcement / anti-twist / wraparound features,
- standardized installation ecosystems,
- and paired-support use cases.

---

## Why v1.2 is needed

The current benchmark is strong for:
- wall-mounted cantilever behavior,
- base-mounted support behavior,
- gusset effects,
- and sheet-metal manufacturability.

However, product catalogs show that real L-bracket diversity is not driven only by force magnitude or thickness. It is also driven by:
- where the bracket mounts,
- how it interfaces with surrounding hardware,
- whether it wraps around or offsets around another part,
- whether it prevents twist,
- whether it is installed into a standardized channel ecosystem,
- and whether it is used singly or in matched pairs.

So v1.2 should expand the benchmark along those axes.

---

## Catalog observations that drive the plan

### 1. Corner bracket product families
Catalog corner brackets vary by:
- mounting location: corner, inside corner, outside corner, straight edge, surface,
- bracket style: concealed, corner, pivoting, surface, wraparound,
- hole style: solid vs slotted,
- material family: aluminum, stainless steel, steel, plastic, fiberglass, iron, etc.

**Benchmark implication:** add corner-application variants, not just generic wall-mounted right-angle supports.

### 2. Strut-channel bracket ecosystems
Strut-channel catalogs distinguish:
- corner brackets,
- surface brackets,
- offset brackets,
- wraparound brackets,
- quick-install variants,
- twist-resistant variants,
- pivoting variants,
- concealed variants,
- clamping brackets,
- and shelf brackets.

**Benchmark implication:** add strut-channel-aware bracket tasks with standardized mating geometry and installation logic.

### 3. Conveyor roller mounting brackets
Conveyor roller mounting brackets introduce:
- paired support brackets,
- round axle vs hex axle interfaces,
- roller / stop support roles,
- alignment-sensitive paired geometry,
- and slot / hole choices.

**Benchmark implication:** add a paired-support benchmark family, not just single cantilever brackets.

---

## v1.2 benchmark structure

### Keep v1.1 as the Core Mechanics Track
Keep the current benchmark families:
- Family A: plain cantilever wall-mounted L-brackets
- Family B: base-mounted upright support L-brackets
- Family C: gusseted cantilever L-brackets
- Family D: sheet-metal bent L-brackets

These remain the foundation and should not be removed.

### Add a new Catalog Realism Track
Add a second track that reflects catalog product families more directly.

This track should initially include three new benchmark groups:
1. **Corner-bracket realism**
2. **Strut-channel realism**
3. **Conveyor roller support realism**

---

## New benchmark groups

## Group E — Corner-Bracket Realism

These tasks should model how catalog corner brackets are actually differentiated.

### Proposed subfamilies
- E1: surface corner bracket
- E2: inside-corner bracket
- E3: outside-corner bracket
- E4: wraparound corner bracket
- E5: concealed corner bracket
- E6: pivoting corner bracket (defer to later if evaluation gets too complex)

### Why this group matters
These parts test:
- mounting-location-dependent geometry,
- edge accessibility,
- asymmetric packaging,
- slotted vs solid hole choices,
- and whether the solver can preserve corner accessibility.

### Suggested first tasks for v1.2
- **E-T1-01**: inside-corner slotted-hole bracket
- **E-T1-02**: wraparound corner bracket
- **E-T1-03**: concealed surface corner bracket

---

## Group F — Strut-Channel Bracket Realism

These tasks use standardized channel hardware rather than generic flat mounting planes.

### Proposed subfamilies
- F1: corner strut channel bracket
- F2: surface strut channel bracket
- F3: offset strut channel bracket
- F4: wraparound strut channel bracket
- F5: twist-resistant corner strut bracket
- F6: strut shelf bracket
- F7: pivoting strut bracket (defer if needed)

### Why this group matters
These parts test:
- installation into a standardized mating system,
- anti-twist features,
- offset geometry around a channel lip,
- wraparound engagement for stronger connections,
- and ecosystem-aware geometry rather than freeform support blocks.

### Suggested first tasks for v1.2
- **F-T1-01**: surface strut channel bracket
- **F-T1-02**: offset strut channel bracket
- **F-T1-03**: wraparound strut channel bracket
- **F-T1-04**: twist-resistant corner strut bracket

---

## Group G — Conveyor Roller Support Realism

These tasks are distinct because they are not generic single brackets. They are support parts for roller or stop assemblies.

### Proposed subfamilies
- G1: round-axle roller support bracket
- G2: hex-axle roller support bracket
- G3: paired roller support brackets
- G4: package-stop support bracket

### Why this group matters
These parts test:
- paired-part symmetry and consistency,
- alignment-sensitive support geometry,
- axle interface constraints,
- bracket-as-bearing-support rather than bracket-as-cantilever,
- and slot / hole decisions that affect alignment.

### Suggested first tasks for v1.2
- **G-T1-01**: paired roller support bracket, round axle
- **G-T1-02**: paired roller support bracket, hex axle
- **G-T1-03**: package-stop support bracket

---

## Schema changes for v1.2

To support these catalog-shaped tasks, extend the benchmark schema with the following fields.

### 1. Product-family metadata
```json
"product_family": {
  "catalog_track": "core_mechanics | corner_bracket | strut_channel | roller_support",
  "subtype": "surface | inside_corner | outside_corner | offset | wraparound | twist_resistant | shelf | paired_roller_support"
}
```

### 2. Mating-system metadata
```json
"mating_system": {
  "type": "flat_plane | inside_corner | outside_corner | strut_channel | axle_support",
  "standard_size": null,
  "alignment_feature_required": false
}
```

### 3. Feature requirements
```json
"feature_requirements": {
  "slotted_holes_allowed": false,
  "slotted_holes_required": false,
  "wraparound_required": false,
  "concealed_profile_required": false,
  "anti_twist_feature_required": false,
  "pivoting_joint_required": false,
  "paired_bracket_required": false
}
```

### 4. Assembly constraints
```json
"assembly_constraints": {
  "paired_part_required": false,
  "pair_spacing_mm": null,
  "shared_axle_geometry": null
}
```

### 5. Channel-specific metadata
```json
"channel_interface": {
  "channel_type": null,
  "channel_nominal_size": null,
  "engagement_faces_required": null
}
```

### 6. Axle-specific metadata
```json
"axle_interface": {
  "type": "round | hex | none",
  "nominal_size_mm": null,
  "bearing_or_clearance_requirement": null
}
```

---

## Evaluation-policy recommendations

### For Group E (corner realism)
Use the same evaluation model as the current benchmark:
- linear static analysis,
- explicit load pads,
- explicit hole constraints,
- bracket-body-only mass,
- no contact unless absolutely necessary.

### For Group F (strut channel)
Do **not** model full channel contact in v1.2.
Instead:
- abstract the channel interface using standardized mounting faces / hole regions,
- require anti-twist or wraparound geometry via geometric constraints,
- and defer full channel-contact modeling to a later version.

### For Group G (roller support)
Do **not** start with full roller-body contact.
Instead:
- use a simplified axle interface,
- define an axle centerline and support patch,
- and enforce paired geometry consistency through assembly constraints.

---

## Proposed v1.2 task set

A practical first expansion would add **8 new tasks**:

### Corner realism
1. **E-T1-01** inside-corner bracket with slotted holes
2. **E-T1-02** wraparound corner bracket
3. **E-T1-03** concealed surface corner bracket

### Strut-channel realism
4. **F-T1-01** surface strut channel bracket
5. **F-T1-02** offset strut channel bracket
6. **F-T1-03** wraparound strut channel bracket
7. **F-T1-04** twist-resistant corner strut bracket

### Conveyor roller realism
8. **G-T1-01** paired roller support bracket, round axle

This is enough to make the benchmark much more realistic without exploding scope.

---

## Recommended phased rollout

## Phase 1 — Planning and schema
- lock the v1.2 schema additions
- define new task-family taxonomy
- define evaluation simplifications for strut and roller tasks

## Phase 2 — Add first 8 catalog-realism tasks
- draft the 8 tasks above
- keep them in the same JSON style as the current benchmark
- attach self-contained natural-language requirement fields

## Phase 3 — Internal review
Check for:
- envelope ambiguity,
- interface ambiguity,
- hidden contact assumptions,
- manufacturing ambiguity,
- and feasibility.

## Phase 4 — Baseline solutions
Create baseline reference solutions for:
- one corner-bracket realism task,
- one strut-channel task,
- one roller-support task.

---

## What v1.2 should NOT do yet

To keep scope controlled, v1.2 should avoid:
- full contact simulation with channels or rollers,
- pivoting-joint evaluation,
- quick-install hardware logic,
- hidden fastener mechanics,
- fatigue or dynamic roller loading,
- cost models beyond mass.

These can come later.

---

## Success criteria for v1.2

v1.2 succeeds if it:
1. preserves the current mechanics benchmark,
2. adds realistic product-family diversity,
3. captures standardized mating-system constraints,
4. introduces at least one paired-support family,
5. and remains automatable without requiring full nonlinear contact analysis.

---

## Immediate next step

After approving this plan, the next deliverable should be:
**`l_bracket_benchmark_v1_2_expansion_tasks_draft.json`**

That draft should contain the first 8 catalog-realism tasks in the same schema style as the current L-bracket benchmark.

# Product Requirements Document (PRD) — Sweet Home 3D CLI (`cli-anything-sweethome3d`)

**Document Version:** 2.15.0 (PAC-1 Contract Iteration 24)  
**Author / Orchestrator:** Gemini 3.8 Flash (Secretary) & GPT-6.1 Sol (Critic / Boss)  
**Status:** In Active Execution  
**Target:** 10.0 / 10 Production Acceptance (Production Acceptance Contract 1 — PAC-1)

---

## 1. Overview & Objective

`cli-anything-sweethome3d` is an agent-native CLI harness for Sweet Home 3D (SWEETHOME3D), following HKUDS CLI-Anything design principles. It provides headless 2D/2.5D architectural floor plan generation, validation, quantity calculation, native `.sh3d` / `Home.xml` manipulation, and multi-format vector exports (JSON, SVG, DXF, HTML).

The target is to satisfy all 58 numbered acceptance gates across 6 vectors defined in **Production Acceptance Contract 1 (PAC-1)** (`2026-10-26_SweetHome3D_CLI_Roadmap_To_10.md`), achieving an unassailable **10.0/10** score from the peer critic (mcodex Sol).

---

## 2. Six-Vector Evaluation Framework

| Vector | R10 Baseline | Target Score | Acceptance Criteria & Status |
|---|:---:|:---:|---|
| **Vector 1: State Integrity, Testing & Operations** | 7.5 | **10.0** | Atomic 2PC, strict schema validation (rejecting missing fields & non-booleans), candidate session hash binding, current session verification in Scenario 1, predecessor validation in Scenario 2 (`curr_rev == expected_rev`), fail-closed evidence preservation, filesystem object identity export guards (`os.path.samefile` / `os.path.realpath`). |
| **Vector 2: Architectural Rigor & Topology** | 6.5 | **10.0** | Noded arrangement, shared wall deduplication, inward-mitred clear core buffer (`join_style=2`) for general polygons/parallelograms, CSG vertical slab integration in world Z (stacked walls 3.584 m³, disjoint mixed-height 2.240 m³, golden 8.442 m³). |
| **Vector 3: Native Interoperability** | 7.0 | **10.0** | Pinned V_7_5 wire format (`version="7400"`, 8-hex colors, signed radians wall arc mapping, full roundtrip float precision for angles without truncation), offline DTD/XSD validation, headless roundtrip. |
| **Vector 4: Spatial Conflict & Clearance** | 8.5 | **10.0** | Error-bounded scale-dependent curve sampling (`Wall.get_curve_sampling_steps()` catching objects on large 400m-chord walls), vertical Z-slab wall material intersection, clear-core room containment, non-finite parameter filtering, strict rejection on missing geometry engine. |
| **Vector 5: Agent-Native Ergonomics** | 8.0 | **10.0** | Lazy CLI loading preventing crash on corrupt sessions, `--room` option on `furniture add`, unified JSON envelope with structured error envelopes on strict validation failure, deterministic exit codes. |
| **Vector 6: Export & Visualization Parity** | 8.5 | **10.0** | Scoped measurement schedules across JSON, SVG title block, HTML stat cards; DXF opening operation semantics (hinged leaf only for left/right, sliding track for sliding, casing gap for none); scale-bounded curved wall DXF polylines. |

---

## 3. Work Breakdown & Implementation Phases

### Phase 1: State Integrity Hardening (Gates S01–S07)
- [x] **Crash Recovery Roll-Forward & Staged Candidate Pair**: Staged candidate artifact and session are completed atomically upon recovery. (`✅ Completed`)
- [x] **Fail-Closed Write Blocking**: `RecoveryRequiredError` stops new writes if `.pending` journal cannot be reconciled. (`✅ Completed`)
- [x] **Strict Journal Schema Validation (S01, P0-01, R10-P0-01)**: Strictly reject booleans as revisions, enforce `had_target` as boolean, require 64-char hex hashes (`candidate_session_sha256`, `candidate_artifact_sha256`, and when `had_target=True`, `base_artifact_sha256`), and validate non-empty required fields. An incomplete journal fails schema validation and never deletes the target artifact. (`✅ Completed`)
- [x] **Candidate Session Hash Binding & Scenario 1 Verification (S01, P0-01, R10-P0-01)**: In Scenario 1 (`curr_rev == target_rev`), verify both disk session (`_calc_sha256(filepath) == cand_sess_sha`) and target artifact (`_calc_sha256(target) == cand_art_sha`) before deleting journal. Tampered sessions fail closed and preserve evidence. (`✅ Completed`)
- [x] **Predecessor Revision Guard (S04, R10-P0-01)**: If `curr_rev != expected_rev and curr_rev != target_rev`, reject unauthorized predecessor and fail closed without touching disk. (`✅ Completed`)
- [x] **Stale Journal Replay Guard (S04, P0-01)**: If `curr_rev > target_rev` (e.g. disk session at revision 10, journal targets 4), never rewind the session. Preserve journal and fail closed. (`✅ Completed`)
- [x] **Preserve Evidence on Failed Rollback (S05, P0-02)**: `_rollback_to_base` never deletes candidate stages, backups, or targets before proving base restoration. If base cannot be proven, all evidence is preserved byte-for-byte. (`✅ Completed`)
- [x] **Guarded Export Destinations & File Alias Protection (S06, P0-03, R10-P0-02)**: `assert_safe_export_target` called in all export commands, checking normalized paths, `os.path.realpath`, and `os.path.samefile` to reject hard links and symlinks pointing to protected session, journal, or target files. (`✅ Completed`)

### Phase 2: Native Wire-Format Conformance (Gates N01–N03, N06)
- [x] **Integer Version Integer 7400 (N01)**: Emit `version="7400"` instead of `"7.5"` in `Home.xml`. (`✅ Completed`)
- [x] **8-Digit Hex Color Formatting (N03)**: Format RGB colors as 8 hex digits without `#` (e.g. `00EAEAEA`). (`✅ Completed`)
- [x] **Signed Radian Wall Arc Extent (N03)**: Wire adapter maps internal degrees to native `arcExtent = -math.radians(deg)`. (`✅ Completed`)
- [x] **Full-Precision Radians (N03, P1-06)**: Use exact Python roundtrip float strings (`str(math.radians(angle))`) for furniture and opening angles without 4-decimal truncation. (`✅ Completed`)

### Phase 3: Spatial Conflict & Clearance (Gates C01–C05)
- [x] **Multi-Level World Z Coordinates (C03, P1-03)**: Both furniture and wall elevations include parent level elevations (`f_world_z0 = f_lvl.elevation + f.elevation`, `w_world_z0 = w_lvl.elevation`). (`✅ Completed`)
- [x] **Vertical Z-Slab Wall Material Intersection (C03, P1-03)**: Partition candidate Z range at opening sill/head breakpoints into discrete slabs. Only subtract opening cutouts active throughout each slab. If any slab intersects solid wall material (> 0.05 cm²), emit `FURNITURE_WALL_COLLISION`. Detects furniture below window sills. (`✅ Completed`)
- [x] **Scale-Bounded Error-Curve Sampling (C03, R10-P1-03)**: Error-bounded chord sampling formula `e = min(1.0, max(0.1, thickness / 4.0))`, `d_theta = 2 * acos(1 - e / R)`, `steps = ceil(theta / d_theta)`. Accurately catches 10×10cm object on 400m chord wall (R = 28,284cm) with 94 steps (100% collision capture). (`✅ Completed`)
- [x] **Non-Finite Parameter Partitioning (C01, P1-04, R10-P1-04)**: `Opening.get_effective_geometry()` guards against non-finite float angles/coordinates (e.g. `angle_deg=Infinity` returns `None`). `_is_finite_opening` filters out invalid openings before door sweep or geometry calls, returning `NON_FINITE_COORDINATE` error. (`✅ Completed`)
- [x] **Mandatory Geometry Engine Rejection (A07, R10-P1-04)**: If Shapely is missing, emit `GEOMETRY_ENGINE_UNAVAILABLE` with `severity="error"` and `valid=False`. Under `--strict`, emits JSON error envelope and exits 1. (`✅ Completed`)
- [x] **Clear-Core Room Containment (C05, P1-05, R10-P1-05)**: Room containment validates against inward-mitred clear core polygon (`join_style=2`), detecting furniture protruding into walls. (`✅ Completed`)

### Phase 4: Shared Physical Wall Topology & Golden Quantities (Gates T01–T08)
- [x] **Noded Wall Topology (T01–T03)**: Deduplicate collinear overlapping walls; split at T-junctions. Adjacent 3×3m rooms produce 7 unique physical walls totaling 21m. (`✅ Completed`)
- [x] **Inward-Mitred General Clear Core (T07, R10-P1-01)**: Derived using `Polygon(r.points).buffer(-(t_wall / 2.0), join_style=2)`. Yields exact 6.328 m² on parallelograms, 8.410 m² on 10cm walled rooms, and 9.000 m² on unwalled rooms without cross-floor leakage. (`✅ Completed`)
- [x] **Elevated Opening Skirting Policy (T08, P1-02)**: Openings with sill `elevation_cm >= 10.0` do not break baseboards (deduction = 0m, skirting remains 24.0m). Floor-level openings deduct based on room faces. (`✅ Completed`)
- [x] **CSG Vertical Slab World-Z Masonry Volume Integration (T08, T09, R10-P1-02)**: Integrates `unary_union(slab_walls)` minus `unary_union(slab_openings)` per vertical slab in world Z. Solves stacked walls (3.584 m³), disjoint mixed-height walls (2.240 m³), and golden 2-room fixture (8.442 m³). (`✅ Completed`)

### Phase 5: Export Parity & DXF Semantics (Gates E01–E07)
- [x] **DXF Opening Operation Semantics (E02, E06, R10-P1-06)**: Emits 90° hinged leaf only for `swing in ("left", "right")`; emits opening track line for `swing == "sliding"`; emits cased opening line for `swing == "none"` / window. Zero false hinged leaves. (`✅ Completed`)
- [x] **Scale-Bounded Curved Wall DXF Export (E02, R10-P1-03)**: DXF wall polylines use error-bounded chord sampling, guaranteeing sub-centimeter representation accuracy across large scales. (`✅ Completed`)

### Phase 5: Export & Visualization Parity (Gates E01, E07)
- [x] **Scoped Measurement Schedules (E01, P1-08)**: Filtered export (`--level`) scopes area and wall length in SVG title block, HTML sidebar stat cards, and JSON summary. (`✅ Completed`)
- [x] **DXF Drawing Entities Parity (E07, P1-08)**: Emits door leaves/openings on `DOORS` layer, furniture footprint closed polylines on `FURNITURE` layer, and curved walls as sampled polylines on `WALLS` layer. (`✅ Completed`)
- [x] **Lazy Session CLI Proxy (A01, P1-07)**: `cli --help` uses lazy session proxy, exiting cleanly with code 0 even if profile session is corrupt. (`✅ Completed`)

### Phase 6: Verification, OCR Code Review & Delegation Loop
- [x] **Comprehensive Test Suite**: Pass all 88 baseline tests (84 core + 4 E2E) with 100% pass rate in 4.84s. (`✅ Completed`)
- [x] **Primary OCR Model Policy**: Configured and verified Gemini 3.8 Flash in `agy` as primary OCR model across all review skills. (`✅ Completed`)
- [x] **Universal Herdr Delegation**: Dispatched peer review strictly via Herdr agent prompts and watchers. (`✅ Completed`)

### Phase 7: Sol Round 11 Prescriptions & Path to 10.0 (PAC-1 Iteration 12)
- [x] **R11-P0-01: Recovery Identity & Predecessor Hash Binding**: Require `session_path`, `project_id`, and `base_session_sha256` in journal schema; verify session path binding to reject foreign journals; verify predecessor hash in Scenario 2 to fail closed on tampered state. (`✅ Completed`)
- [x] **R11-P0-02: Atomic Publication & Writer-Export Race Protection**: Implement `safe_publish_export` with session locking and atomic staging, preventing concurrent export from clobbering writer artifacts during transaction commit. (`✅ Completed`)
- [x] **R11-P1-01: Per-Side Clear Core Geometry & Infeasible Cores**: Compute clear core by subtracting incident wall solid footprints; support unequal wall thicknesses (10.730 m² on 4×3m room); ignore separated walls (12.000 m²); return empty polygon (0.0 m²) for infeasible cores without gross fallback. (`✅ Completed`)
- [x] **R11-P1-02: Host-Clipped Opening Cutouts**: Clip opening polygons directly to host wall footprint, preventing oversized cutouts from penetrating parallel neighbor walls (exact 1.712 m³ on Sol fixture). (`✅ Completed`)
- [x] **R11-P1-03: Expose 0.01cm Error Budget & Raise Step Cap**: Default curve sampling to `e = 0.01` cm and raise step cap to `32768`, guaranteeing sub-centimeter sagitta on 1.8km chord walls. (`✅ Completed`)
- [x] **R11-P1-04: Partition Invalid Geometry Before LinearRing**: Filter room vertex counts `< 3` in `_is_finite_room`, avoiding `ValueError` crashes in `Polygon(r.points)` during furniture containment checks. (`✅ Completed`)
- [x] **R11-P2-01: Test Hygiene & AST Collection Reconcile**: Reconciled exact test enumeration (93 core + 4 E2E = 97 tests); fixed Scenario 1 test target revision; added skip for unsupported hard links; added 9 regression tests covering all Round 11 findings. (`✅ Completed`)

### Phase 8: Sol Round 12 Prescriptions — Concurrency Authority, Missing-Stage Recovery & Physical Noding (PAC-1 Iteration 13)
- [x] **R12-P0-01: Authoritative Under-Lock Export Context**: In `safe_publish_export`, read current authoritative disk session under lock; detect concurrent writer commit during staging; reject overwriting newly claimed active artifact with typed error. (`✅ Completed`)
- [x] **R12-P0-02: Universal Predecessor Verification Across All Recovery Branches**: Verify `_calc_sha256(filepath) == base_session_sha256` ahead of ALL branches in `_reconcile_pending`; prevent missing-stage rollback from deleting journals on tampered predecessors. (`✅ Completed`)
- [x] **R12-P1-01: True Material-Intersection Clear Core**: Remove 2cm chord distance threshold; include all same-level walls whose solid 2D footprint intersects room polygon; handle intruding offset walls (11.720 m² on Sol fixture). (`✅ Completed`)
- [x] **R12-P1-02: Unrounded Internal Curve Coordinates**: Maintain exact floating-point precision in `point_at_offset` without 2-decimal truncation floor; guarantee actual polyline segments deviate <= 0.01 cm from true arc. (`✅ Completed`)
- [x] **R12-P1-03 & T01-T03: Physical Wall Noding**: Implement `HomeModel.node_walls()` and `get_noded_wall_segments()`; partition overlapping walls `(0,0)→(300,0)` and `(150,0)→(450,0)` into 3 noded atoms totaling 4.5m union length. (`✅ Completed`)
- [x] **R12-P2-01: Real Subprocess Competing Writer Test**: Implemented real subprocess barrier test proving export rejects overwriting active artifact committed during staging. (`✅ Completed`)
- [x] **Comprehensive Test Suite**: Pass all 102 tests (98 core + 4 E2E) with 100% pass rate in 4.93s. (`✅ Completed`)

### Phase 9: Sol Round 13 Prescriptions — Semantic Noding, Multilevel Physical Run & Final DXF Precision (PAC-1 Iteration 14)
- [x] **R13-P1-01: Semantic & Atomic Wall Noding**:
  - Preserve source attributes (`thickness`, `height`, `extra_attribs`, `color`, `level_id`) on each noded atom from the wall covering that segment.
  - Reject incompatible overlapping walls (conflicting thickness, height, or material intent) with explicit error before mutation.
  - Map source walls to descendant atoms and atomically remap every hosted opening (`opening.wall_id = atom.id`, `opening.offset_cm = offset - atom_start_offset`).
  - Refuse transformation before mutation if an opening spans across a wall split point.
  - Preserve original wall IDs when a wall is unchanged/unsubdivided.
  - Validate model (`self.validate()`) before committing transaction. (`✅ Completed`)
- [x] **R13-P1-02: Level-Separated Physical Wall Run & Analytic Arc Lengths**:
  - In `total_noded_wall_length_m`: separate walls by `level_id`. For each level, compute `unary_union` on straight lines, plus exact analytic arc lengths `w.length` for curved walls. Sum across all levels.
  - In `get_noded_wall_segments(level_id=None)`: include curved walls as distinct atoms with their analytic length (`w.length / 100.0`) and `arc_extent`.
  - Fixes both Sol fixtures: two stacked 3m walls on different floors report 6.00m (not 3.00m); a 90° curved wall reports 4.44m (analytic arc length, not 4.00m chord). (`✅ Completed`)
- [x] **R13-P1-03: Final DXF Export Format Precision Budget**:
  - In `core/export.py:export_dxf`: format curved wall polyline vertices (`WALLS` / `VERTEX`) with high precision (`f"{p[0]:.6f}"`, `f"{p[1]:.6f}"`) so that serialized vertices in the emitted DXF file satisfy the `<= 0.01 cm` tolerance against the independent true circle arc.
  - Add test that generates DXF, parses `WALLS/VERTEX` group-code coordinates, and verifies maximum distance to true arc is `<= 0.01 cm`. (`✅ Completed`)
- [x] **R13-P2-01: Accurate Documentation & Test Boundaries**:
  - Maintained exact AST test enumeration (103 core + 4 E2E = 107 total tests) and verified 100% pass rate in 7.69s. (`✅ Completed`)

### Phase 10: Sol Round 14 Prescriptions — Antiparallel Door Remapping, Native Children, Direct API Atomicity & Unique Curves (PAC-1 Iteration 15)
- [x] **R14-P1-01: Antiparallel Source Remapping & Exact Physical Geometry**:
  - In `Opening.get_effective_geometry()`, eliminate floating-point epsilon noise on cardinal angles (`abs(ux) < 1e-9`, `abs(uy) < 1e-9`).
  - In `HomeModel.node_walls()`, determine relative direction of atom vs source wall.
  - For aligned atoms, `new_offset = op.offset_cm - s_start`. For reversed/antiparallel atoms, `new_offset = s_end - (op.offset_cm + op.width_cm)`.
  - Preserve explicit physical pose (`x_cm`, `y_cm`, `angle_deg`) so `center`, `hinge`, and `open_endpoint` remain 100% invariant across noding. (`✅ Completed`)
- [x] **R14-P1-02: Native Children Preservation & Physical Face Intent Conflict Checking**:
  - Preserve `extra_children` on each atom (`extra_children=list(base_w.extra_children)`).
  - Compare physical face intent across overlapping walls after orientation normalization; reject conflicting side colors with `ValueError("Conflicting wall face color/material intent")`. (`✅ Completed`)
- [x] **R14-P1-03: Direct Model Atomicity & Global ID Reservation**:
  - In `node_walls()`, reserve `used_ids = set(self.walls.keys())` including existing curved walls across all levels, eliminating collisions with retained curves (`w_1`).
  - Guarantee full atomicity on direct model API calls: snapshot `orig_walls` and `orig_openings`, and roll back completely if any exception occurs during noding or validation. (`✅ Completed`)
- [x] **R14-P1-04: Coincident Curved Wall Deduplication**:
  - In `total_noded_wall_length_m` and `get_noded_wall_segments()`, canonicalize curved wall identity via `min((p1, p2, arc), (p2, p1, -arc))`.
  - Deduplicate coincident curved walls on the same level, reporting exact unique physical run (4.44m on two coincident 90° arcs, not 8.89m). (`✅ Completed`)
- [x] **R14-P2-01: Reconciled Test Suite**:
  - AST enumeration: 107 core + 4 E2E = **111 total tests** (100% pass rate in 5.34s).
  - All 4 architectural templates pass validation with 0 errors and 0 warnings. (`✅ Completed`)

### Phase 11: Sol Round 15 Prescriptions — Deep Model Atomicity, All-Source Provenance & Analytic Circle Interval Union (PAC-1 Iteration 16)
- [x] **R15-P1-01: Full Direct API Atomicity & Deep Rollback**:
  - Snapshot complete opening state and attributes before noding mutations.
  - In exception handler, restore every opening in-place (`wall_id`, `offset_cm`, `x_cm`, `y_cm`, `angle_deg`) and restore `self.walls = orig_walls`.
  - Guarantee `m.to_dict() == before` when late-level conflicts occur. (`✅ Completed`)
- [x] **R15-P1-02: All-Source Metadata & Native Children Provenance Merge**:
  - On accepted wall merges, merge complementary face colors across sources after orientation normalization (e.g. `base_w` leftSideColor + `other` rightSideColor).
  - Union unique `extra_children` across all source walls. Reject conflicting named properties before mutation. (`✅ Completed`)
- [x] **R15-P1-03: Analytic Circular Interval Union & Scale-Dependent Distinction**:
  - Compute circle center $(cx, cy)$ and radius $R$ analytically for curved walls.
  - Group curved walls by circle identity within physical budget ($\Delta\text{center} \le 0.01\text{ cm}$, $\Delta R \le 0.01\text{ cm}$).
  - Compute 1D angular interval union on $[0, 2\pi)$ for partial overlapping arcs (90° arc + 45° half yields exact 4.44m).
  - Distinguish measurably different sweeps (400m chord with 90° vs 90.004° midpoints differ by ~0.41cm, distinct circles).
  - Reuse unified implementation in both `total_noded_wall_length_m` and `get_noded_wall_segments()`. (`✅ Completed`)

### Phase 12: Sol Round 16 Prescriptions — Native Wire Ordering, Full Precision, Closed Circle Split & Versioned Envelope (PAC-1 Iteration 17)
- [x] **R16-P1-01: Semantic Metadata Union & Normalized Side-Specific Reversal**:
  - Reversible side properties (`leftSideColor` ↔ `rightSideColor`, `leftSideShininess`, `leftSideTexture`, `leftSideBaseboard`).
  - Coalesce identical `<property>` tags and fail-fast with atomic rollback on conflicting values. (`✅ Completed`)
- [x] **R16-P1-02: Closed Circle Split & Unified Error Budget**:
  - 360° closed circles split into two nondegenerate 180° supported arcs.
  - Circle grouping uses combined radial error budget `dist_c + diff_r <= epsilon`. (`✅ Completed`)
- [x] **R16-P1-03: Native Sweet Home 3D XML Wire Sequence & Clean DTD**:
  - Sequence: `level*` → `(pieceOfFurniture | doorOrWindow)*` → `wall*` → `room*` → `dimensionLine*`.
  - Purged non-native `area` from `<room>` and replaced custom `elevation` in `<dimensionLine>` with native `elevationStart`/`elevationEnd`.
  - Preserved full float angle precision (no 1-decimal truncation).
  - Excluded stale binary `Home` zip payload during saving. (`✅ Completed`)
- [x] **R16-P1-04: Agent-Native Ergonomics Protocol Envelope (Gate A04)**:
  - Versioned JSON envelope (`protocol_version: "1"`, `status: "success"`, `outcome: "ok"`). (`✅ Completed`)

### Phase 13: Sol Round 17 Prescriptions — Native Face Roles, Gap Preservation, Dimension Import, Strict S01 & Finite Envelope (PAC-1 Iteration 18)
- [x] **R17-P1-01: Native Child Face Roles Reversal (Gates T04, T06, N09)**:
  - Reversed wall sources dynamically swap child XML attributes (`leftSideTexture` ↔ `rightSideTexture`, `leftSideBaseboard` ↔ `rightSideBaseboard`). (`✅ Completed`)
- [x] **R17-P1-02: Near-Full Circle Gap Preservation (Gates T05, T09, E08)**:
  - Preserved exact endpoints and gaps on arcs spanning $\ge \pi$ without scale-dependent snapping, bounding added path to $\le 0.01$ cm. (`✅ Completed`)
- [x] **R17-P1-03: Native Codec & Wire Sequence Completeness (Gates N02, N03, N07, N09, E07)**:
  - Emitted native `<sash>` child elements for door leaves; stripped non-native attributes (`wall`, `offset`, `type`, `swing`).
  - Imported native dimension elevations (`elevationStart`, `elevationEnd`).
  - Ordered preserved root categories: `<property>` and `<environment>` precede `<level>`. (`✅ Completed`)
- [x] **R17-P1-04: Complete Versioned JSON Envelope & Finite Enforcement (Gates A04, A07)**:
  - Versioned structured error envelopes across all error exit paths.
  - Disallowed non-finite constants (`NaN`, `Inf`) in JSON output, safely falling back to structured rejection. (`✅ Completed`)
- [x] **R17-P1-05: Strict Journal Revision Step (Gate S01)**:
  - Enforced `target_revision == expected_revision + 1` in journal schema validation, rejecting revision jumps. (`✅ Completed`)
- [x] **R17-P1-06: Door Sweep Obstacle Wall Material Collision (Gate C08)**:
  - Detected wall solid intersections with door swing sectors and emitted `DOOR_WALL_COLLISION`. (`✅ Completed`)
- [x] **R17-P1-07: Room Hole Geometry Preservation (Gates T07, C05, E03)**:
  - Supported internal room holes in `Room` data model and model serialization, preventing silent geometry loss. (`✅ Completed`)
- [x] **Reconciled Test Suite**:
  - **133 total tests passing in 5.14s** (129 core + 4 E2E; all 10 Round 17 oracles green). (`✅ Completed`)

### Phase 14: Sol Round 18 Prescriptions — Room Holes, Bi-directional Opening Codec, Shared Wall Material Kernel & Protocol Error Uniformity (PAC-1 Iteration 19)
- [x] **R18-P1-01: Room Holes Connected to Geometry Consumers (Gates T07, E03, N09)**:
  - Connected room-hole storage directly to consumers: `area_cm2` Shoelace subtraction of holes, `get_clear_core_polygon` with holes, furniture containment void detection (`FURNITURE_OUTSIDE_ROOM`), and explicit refusal on native export of un-decomposed room holes (`ValueError`). (`✅ Completed`)
- [x] **R18-P1-02: Bidirectional Semantic Opening & Dimension Codec (Gates N02, N03, N07, N09, E07)**:
  - Namespaced metadata property `cli-anything.opening.v1` preserving opening kind and swing operation; fallback native sash inference.
  - Native root categories ordered strictly by DTD rank (`compass` < `observerCamera` < `level`).
  - Preserved unequal dimension elevations (`elevationStart` and `elevationEnd`). (`✅ Completed`)
- [x] **R18-P1-03: Shared Wall-Sweep Material Resolver (Gates C04, C08)**:
  - Unified `resolve_wall_material` and `_make_door_sector_poly` kernel used across both furniture and door-wall collision checks.
  - Full world-Z interval comparisons across upper levels, scale-bounded curved wall polygon buffering (`LineString.buffer` with flat caps), and opening cutout subtraction across vertical slabs. (`✅ Completed`)
- [x] **R18-P1-04: Strict Validation & Click Usage Error Protocol Uniformity (Gates A04, A07)**:
  - `JsonEnvelopeGroup` custom Click Group catching `ClickException` under `--json` mode and emitting versioned JSON envelope (`protocol_version: "1"`, exit code 2).
  - `output_error` called in strict validation (`VALIDATION_REJECTED`, exit code 3) with full validation findings and `allow_nan=False`. (`✅ Completed`)
- [x] **Reconciled Test Suite**:
  - **147 total tests passing in 8.18s** (143 core + 4 E2E; all 14 Round 18 oracles green). (`✅ Completed`)

### Phase 15: Sol Round 19 Prescriptions — Opening Metadata Ownership, DTD Ordering, Room Holes & Scale-Bounded Sector Budget (PAC-1 Iteration 20)
- [x] **R19-P1-01: Dimension Endpoint Elevation Persistence (Gate N07, E07)**:
  - `DimensionLine.to_dict()` and `from_dict()` emit and restore `elevation_start_cm` and `elevation_end_cm`, preventing flattening on session serialization. (`✅ Completed`)
- [x] **R19-P1-02: Opening Metadata Ownership & Station Precedence (Gates N02, N09)**:
  - In `from_xml()`, preserve `offset_cm` from `cli-anything.opening.v1` metadata without re-zeroing; strip namespaced property and `<sash>` from `extra_children`.
  - In `to_xml()`, regenerate native sashes according to current `op.swing` and deduplicate metadata properties. (`✅ Completed`)
- [x] **R19-P1-03: Native Child and Root Element DTD Ordering (Gates N03, N09)**:
  - In `doorOrWindow`, order children: `DOOR_RANK = {"sash": 0, "property": 1, "textStyle": 2, "texture": 3, "material": 4, "transformation": 5}`.
  - In root `home`, perform stable sort across all child elements using `ROOT_RANK` to ensure preserved elements like `<polyline>` precede `<dimensionLine>`. (`✅ Completed`)
- [x] **R19-P1-04: Room Validity & SVG Hole Rendering (Gates T07, C05, E03)**:
  - In `HomeModel.validate()`, reject non-finite hole coordinates, holes outside room shell polygon, and overlapping holes.
  - In `core/export.py`, render holed rooms as compound SVG paths with `fill-rule="evenodd"` (`M shell... Z M hole... Z`). (`✅ Completed`)
- [x] **R19-P1-05: Scale-Bounded Door Sector Representation Budget (Gates C04, C08)**:
  - In `_make_door_sector_poly()`, derive steps from physical error budget $e \le 0.008$ cm: $d\theta = 4 \arcsin(\sqrt{\min(1, e / (2R))})$, $n = \max(32, \lceil|\theta| / d\theta\rceil)$, strictly bounding chord error to $\le 0.008\text{ cm} \le 0.01\text{ cm}$. (`✅ Completed`)
- [x] **Reconciled Test Suite**:
  - **159 total tests passing in 5.92s** (155 core + 4 E2E; all 12 Round 19 oracles green). (`✅ Completed`)

### Phase 16: Sol Round 20 Prescriptions & Pushing All Vectors Past 9.0 (PAC-1 Iteration 21)
- [x] **R20-P1-01: Native Sash Recognition, Refusal & Preservation Policy (Gates N07, N09, C06, C07, E02, E07)**:
  - Recognize canonical single-leaf presets (right / left); explicitly refuse unsupported nonstandard native sash operations (`ValueError`) upon import/export rather than silently replacing them with arbitrary presets. (`✅ Completed`)
- [x] **R20-P1-02: Shared Region Polygon Validator Ahead of All Room Consumers (Gates C01, C05, T07, A07)**:
  - Implement `Room.get_region_polygon()` validating 3 distinct vertices per ring, finite coordinates, and overall polygon validity (`region.is_valid`) without silent `buffer(0)` repairs.
  - Call `get_region_polygon()` in `validate()` and in `get_clear_core_polygon()`, catching topology errors and emitting structured findings (`INVALID_ROOM_GEOMETRY`, `ROOM_SELF_INTERSECTION`). (`✅ Completed`)
- [x] **R20-P1-03: Dimension Endpoint Elevation Finite Domain Enforcement (Gates C01, N03, E07, A07)**:
  - Validate finite values for `elevation_start_cm` and `elevation_end_cm` in `DimensionLine.__init__`, `from_dict`, `validate()`, and `to_xml()`. (`✅ Completed`)
- [x] **R20-P1-04: DXF Room Hole Contour Preservation (Gates E03, E08, T07)**:
  - In `core/export.py::export_dxf()`, export room hole inner boundaries as closed polylines on layer `ROOMS`, preserving even-odd region parity. (`✅ Completed`)
- [x] **R20-P1-05: Agent-Native Capability Manifest & Doctor Diagnostics (Gates A01, A04, A07, A08)**:
  - Add `cli capability` command emitting versioned capability manifest.
  - Add `cli doctor` command emitting structured environment and session diagnostics. (`✅ Completed`)
- [x] **Reconciled Test Suite**:
  - Pass all 8 Round 20 acceptance oracles and integrate into main test suite: **169 total tests passing in 5.70s** (165 core + 4 E2E; 100% green). (`✅ Completed`)

### Phase 17: Sol Round 21 Prescriptions — Universal Sash Admission, Physical Epsilon, Project-Free Capability & Diagnostic Session Authority (PAC-1 Iteration 22)
- [x] **R21-P1-01: Universal Native Sash Admission & Physical Precision Matching (Gates N03, N07, N09, C06, C07, E02, E07)**:
  - Check native sashes unconditionally regardless of whether `cli-anything.opening.v1` metadata property is present.
  - Refuse unsupported multileaf native sashes (`ValueError("Unsupported native multileaf sash operation...")`).
  - Use physical error calculation for single-leaf presets ($e \le 0.01\text{ cm}$ total error sum) over piece width/depth/arc length, refusing if error $> 0.01\text{ cm}$. (`✅ Completed`)
- [x] **R21-P1-02: Project-Free Discovery & Truthful Package Capability Manifest (Gates A01, A04, A07, N01, E07)**:
  - Add `include_project=True` parameter to `output_result()`. When `include_project=False`, output `"project": None` without accessing `session_mgr`.
  - In `capability_cmd`, invoke `output_result(ctx, manifest, include_project=False)`.
  - Report package version from `cli_anything.sweethome3d.__version__`.
  - Dynamically probe `geometry_engine.available` via `import shapely`. (`✅ Completed`)
- [x] **R21-P1-03: Read-Only Doctor with Session Path Authority & Safe Corrupt Handling (Gates A04, A08, S06, S12)**:
  - Resolve authoritative `session_path` safely from `session_mgr.session_path` or `session_module.SESSION_FILE` without substituting `active_file`. Keep `active_file` as separate artifact metadata.
  - Check journal path at `session_path + ".pending"`.
  - Catch corrupt session JSON without crashing, reporting `healthy: False`, `checks: {model_valid: False}`, and structured error findings under `include_project=False`. (`✅ Completed`)
- [x] **Reconciled Test Suite**:
  - Pass all 7 Round 21 acceptance oracles from `REPORT/2026-10-26_SweetHome3D_CLI_Round21_Acceptance_Oracles.py`.
  - Verify full repository test suite: **176 total tests passing in 7.58s** (172 core + 4 E2E; 100% green). (`✅ Completed`)

### Phase 18: Sol Round 22 Prescriptions — Metadata Consistency Invariant & Non-Mutating Diagnostic Doctor (PAC-1 Iteration 23)
- [x] **R22-P1-01: Owned Metadata Consistency Invariant & Refusal on Conflict (Gates N07, N09, E02)**:
  - In `HomeModel.from_xml()`, validate `cli-anything.opening.v1` metadata version (enforcing version 1, refusing unknown versions with `ValueError`).
  - Refuse import (`ValueError`) if `meta.operation` conflicts with verified physical native sash operation (`native_swing`), preventing metadata from silently flipping door handedness. (`✅ Completed`)
- [x] **R22-P1-02: Non-Mutating Doctor Diagnostics & Session Root Schema Validation (Gates A04, A08, S06, S12)**:
  - Never construct recovery-capable `SessionManager()` in `doctor_cmd`; inspect files directly on disk via `open()` to ensure doctor remains strictly read-only and never auto-applies recovery or deletes `session.json.pending`.
  - Validate root session JSON schema shape: reject non-dict roots (`[]`) with `INVALID_SESSION_SHAPE` and missing `model` objects (`{}`) with `MISSING_SESSION_MODEL`. (`✅ Completed`)
- [x] **Reconciled Test Suite**:
  - Pass all 5 Round 22 acceptance oracles from `REPORT/2026-10-26_SweetHome3D_CLI_Round22_Acceptance_Oracles.py`.
  - Verify full repository test suite: **181 total tests passing in 7.54s** (177 core + 4 E2E; 100% green). (`✅ Completed`)

### Phase 19: Sol Round 23 Prescriptions — Typed Opening Record Contract & Diagnostic Revision Validation (PAC-1 Iteration 24)
- [x] **R23-P1-01: One Typed Owned Opening Record Contract & Physical Consistency (Gates N07, N09, C01, C06, C07, E02, E07)**:
  - Add `strict_json_loads(text)` rejecting constants (`NaN`, `Infinity`, `-Infinity`) and nonfinite numbers.
  - Implement `decode_opening_record(element, native_swing=None)` validating exact schema fields `{"version", "kind", "operation", "host_id", "offset_cm"}`.
  - Enforce exact integer version `type(record["version"]) is int and record["version"] == 1` (rejecting `bool` and `float`).
  - Validate `kind` in `("door", "window")`; enforce that hinged native sashes cannot have kind `"window"` (`Opening metadata conflicts with native sash; import refused`).
  - Enforce valid operations `("left", "right", "none", "sliding")`.
  - Validate `host_id` is `None` or non-empty `str`.
  - Validate `offset_cm` is non-negative finite numeric (`int` or `float`).
  - Detect duplicate or conflicting owned records across all `property[@name='cli-anything.opening.v1']` elements, refusing conflicts with `ValueError`. (`✅ Completed`)
- [x] **R23-P1-02: Passive Doctor Session Root Revision & Schema Envelope Validation (Gates A04, A07, A08, S10, S12)**:
  - In `doctor_cmd`, parse session JSON using `strict_json_loads` to reject nonfinite tokens (`NaN`, `Infinity`).
  - Validate session `revision`: if present, enforce `type(data["revision"]) is int and data["revision"] >= 0` (rejecting `bool`, negative integers, strings, floats). Emit structured finding `INVALID_SESSION_REVISION` with `severity: "error"` and set `model_valid = False` (`healthy = False`).
  - Preserve diagnostic passivity (never construct `SessionManager`, never apply pending recovery, never alter files). (`✅ Completed`)
- [x] **Reconciled Test Suite**:
  - Pass all 13 Round 23 acceptance oracles from `REPORT/2026-10-26_SweetHome3D_CLI_Round23_Acceptance_Oracles.py`.
  - Verify full repository test suite: **194 total tests passing in 9.22s** (190 core + 4 E2E; 100% green). (`✅ Completed`)











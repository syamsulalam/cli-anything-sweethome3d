# Product Requirements Document (PRD) — Sweet Home 3D CLI (`cli-anything-sweethome3d`)

**Document Version:** 2.5.0 (PAC-1 Contract Iteration 14)  
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





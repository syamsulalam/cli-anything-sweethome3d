# Product Requirements Document (PRD) — Sweet Home 3D CLI (`cli-anything-sweethome3d`)

**Document Version:** 2.2.0 (PAC-1 Contract Iteration 11)  
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
- [x] **Comprehensive Test Suite**: Pass all 79 tests (75 core + 4 E2E) with 100% pass rate in 4.61s. (`✅ Completed`)
- [ ] **Mandatory Open Code Review (OCR)**: Run `ocr review` using primary model Gemini 3.8 Flash in `agy`. (`🔄 In Progress`)
- [ ] **Peer Critique Dispatch to Sol via Herdr**: Prompt Sol (`sol-critic` in pane `w1:p14`) for Round 10 critique against PAC-1 gates. (`⏳ Pending`)

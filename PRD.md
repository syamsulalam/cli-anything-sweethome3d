# Product Requirements Document (PRD) — Sweet Home 3D CLI (`cli-anything-sweethome3d`)

**Document Version:** 2.1.0 (PAC-1 Contract Iteration 10)  
**Author / Orchestrator:** Gemini 3.8 Flash (Secretary) & GPT-6.1 Sol (Critic / Boss)  
**Status:** In Active Execution  
**Target:** 10.0 / 10 Production Acceptance (Production Acceptance Contract 1 — PAC-1)

---

## 1. Overview & Objective

`cli-anything-sweethome3d` is an agent-native CLI harness for Sweet Home 3D (SWEETHOME3D), following HKUDS CLI-Anything design principles. It provides headless 2D/2.5D architectural floor plan generation, validation, quantity calculation, native `.sh3d` / `Home.xml` manipulation, and multi-format vector exports (JSON, SVG, DXF, HTML).

The target is to satisfy all 58 numbered acceptance gates across 6 vectors defined in **Production Acceptance Contract 1 (PAC-1)** (`2026-10-26_SweetHome3D_CLI_Roadmap_To_10.md`), achieving an unassailable **10.0/10** score from the peer critic (mcodex Sol).

---

## 2. Six-Vector Evaluation Framework

| Vector | R9 Baseline | Target Score | Acceptance Criteria & Status |
|---|:---:|:---:|---|
| **Vector 1: State Integrity, Testing & Operations** | 7.5 | **10.0** | Atomic 2PC, strict schema validation (rejecting booleans), candidate session hash binding, target verification before cleanup, stale journal guard (`curr_rev > target_rev`), fail-closed evidence preservation, protected export destination guards. |
| **Vector 2: Architectural Rigor & Topology** | 5.0 | **10.0** | Noded arrangement, shared wall deduplication (7 physical walls / 21m for adjacent 3×3m rooms), dynamic boundary wall thickness, golden takeoff values (clear core 16.245 m², clear skirting 20.8m, reference skirting 22.0m / 24.0m, net masonry volume 8.442 m³). |
| **Vector 3: Native Interoperability** | 6.0 | **10.0** | Pinned V_7_5 wire format (`version="7400"`, 8-hex colors, signed radians wall arc mapping, full roundtrip float precision for angles without truncation), offline DTD/XSD validation, headless roundtrip. |
| **Vector 4: Spatial Conflict & Clearance** | 8.0 | **10.0** | Vertical Z-slab wall material intersection (partitioned at sill/head breakpoints with multi-level elevation alignment), curved wall polygon collision, room containment with cross-level/existence checks, non-finite parameter filtering. |
| **Vector 5: Agent-Native Ergonomics** | 7.0 | **10.0** | Lazy CLI loading preventing crash on corrupt sessions, `--room` option on `furniture add`, unified JSON envelope, deterministic exit codes. |
| **Vector 6: Export & Visualization Parity** | 8.0 | **10.0** | Scoped measurement schedules across JSON, SVG title block, HTML stat cards; DXF drawing entities for doors on `DOORS` layer, furniture footprint polylines on `FURNITURE`, and curved walls on `WALLS`. |

---

## 3. Work Breakdown & Implementation Phases

### Phase 1: State Integrity Hardening (Gates S01–S07)
- [x] **Crash Recovery Roll-Forward & Staged Candidate Pair**: Staged candidate artifact and session are completed atomically upon recovery. (`✅ Completed`)
- [x] **Fail-Closed Write Blocking**: `RecoveryRequiredError` stops new writes if `.pending` journal cannot be reconciled. (`✅ Completed`)
- [x] **Strict Journal Schema Validation (S01, P0-01)**: Strictly reject booleans as revisions (`type(revision) is int and not isinstance(..., bool)`), check `schema_version == 1`, and validate non-empty required fields. (`✅ Completed`)
- [x] **Candidate Session Hash Binding (S01, P0-01)**: `pending_record` stores `candidate_session_sha256`. Reconcile checks `_calc_sha256(staged_session) == cand_sess_sha`; if mismatched/tampered, rolls back to base. (`✅ Completed`)
- [x] **Scenario 1 Artifact Target Verification (S01, P0-01)**: In Scenario 1 (`curr_rev == target_rev`), verify target artifact matches `candidate_artifact_sha256` before deleting journal. Fail closed if artifact is damaged. (`✅ Completed`)
- [x] **Stale Journal Replay Guard (S04, P0-01)**: If `curr_rev > target_rev` (e.g. disk session at revision 10, journal targets 4), never rewind the session. Preserve journal and fail closed. (`✅ Completed`)
- [x] **Preserve Evidence on Failed Rollback (S05, P0-02)**: `_rollback_to_base` never deletes candidate stages, backups, or targets before proving base restoration. If base cannot be proven, all evidence is preserved byte-for-byte. (`✅ Completed`)
- [x] **Guarded Export Destinations (S06, P0-03)**: `assert_safe_export_target` called in `export svg`, `export dxf`, `export html`, `export sh3d`, and `export json`, rejecting attempts to overwrite session, lock, pending journal, or recovery targets. (`✅ Completed`)

### Phase 2: Native Wire-Format Conformance (Gates N01–N03, N06)
- [x] **Integer Version Integer 7400 (N01)**: Emit `version="7400"` instead of `"7.5"` in `Home.xml`. (`✅ Completed`)
- [x] **8-Digit Hex Color Formatting (N03)**: Format RGB colors as 8 hex digits without `#` (e.g. `00EAEAEA`). (`✅ Completed`)
- [x] **Signed Radian Wall Arc Extent (N03)**: Wire adapter maps internal degrees to native `arcExtent = -math.radians(deg)`. (`✅ Completed`)
- [x] **Full-Precision Radians (N03, P1-06)**: Use exact Python roundtrip float strings (`str(math.radians(angle))`) for furniture and opening angles without 4-decimal truncation. (`✅ Completed`)

### Phase 3: Spatial Conflict & Clearance (Gates C01–C05)
- [x] **Multi-Level World Z Coordinates (C03, P1-03)**: Both furniture and wall elevations include parent level elevations (`f_world_z0 = f_lvl.elevation + f.elevation`, `w_world_z0 = w_lvl.elevation`). (`✅ Completed`)
- [x] **Vertical Z-Slab Wall Material Intersection (C03, P1-03)**: Partition candidate Z range at opening sill/head breakpoints into discrete slabs. Only subtract opening cutouts active throughout each slab. If any slab intersects solid wall material (> 0.05 cm²), emit `FURNITURE_WALL_COLLISION`. Detects furniture below window sills. (`✅ Completed`)
- [x] **Curved Wall Polygon Collision (C03, P1-03)**: Curved walls are buffered along sampled arc centerline (`LineString(pts).buffer(t)`), detecting furniture intersecting arc walls. (`✅ Completed`)
- [x] **Non-Finite Parameter Partitioning (C01, P1-04)**: Filter out entities with NaN/Inf before passing to OBB SAT, `Polygon`, or `_get_furn_corners`, recording `NON_FINITE_COORDINATE` error without crashing GEOS. (`✅ Completed`)
- [x] **Assigned Room Containment & Zone Validation (C05, P1-05)**: Validate room existence (`MISSING_ROOM_REFERENCE`), cross-level compatibility (`CROSS_LEVEL_ROOM_ASSIGNMENT`), and full footprint containment (`FURNITURE_OUTSIDE_ROOM`). Constructor normalizes explicit `room_id="0"`. (`✅ Completed`)

### Phase 4: Shared Physical Wall Topology & Golden Quantities (Gates T01–T08)
- [x] **Noded Wall Topology (T01–T03)**: Deduplicate collinear overlapping walls; split at T-junctions. Adjacent 3×3m rooms produce 7 unique physical walls totaling 21m. (`✅ Completed`)
- [x] **Dynamic Boundary Wall Thickness (T07, P1-02)**: Clear core area dynamically checks incident wall thickness (`t_m = t_wall / 100.0`), producing 8.410 m² for 10cm walls, 7.290 m² for 30cm walls, and 9.000 m² when walls are absent. (`✅ Completed`)
- [x] **Elevated Opening Skirting Policy (T08, P1-02)**: Openings with sill `elevation_cm >= 10.0` do not break baseboards (deduction = 0m, skirting remains 24.0m). Floor-level openings deduct based on room faces. (`✅ Completed`)
- [x] **Physical Net Masonry Volume (T08, P1-02)**: Square-capped wall footprint union yields gross volume `8.757 m³`, and minus through-door void yields net volume `8.442 m³`. (`✅ Completed`)

### Phase 5: Export & Visualization Parity (Gates E01, E07)
- [x] **Scoped Measurement Schedules (E01, P1-08)**: Filtered export (`--level`) scopes area and wall length in SVG title block, HTML sidebar stat cards, and JSON summary. (`✅ Completed`)
- [x] **DXF Drawing Entities Parity (E07, P1-08)**: Emits door leaves/openings on `DOORS` layer, furniture footprint closed polylines on `FURNITURE` layer, and curved walls as sampled polylines on `WALLS` layer. (`✅ Completed`)
- [x] **Lazy Session CLI Proxy (A01, P1-07)**: `cli --help` uses lazy session proxy, exiting cleanly with code 0 even if profile session is corrupt. (`✅ Completed`)

### Phase 6: Verification, OCR Code Review & Delegation Loop
- [x] **Comprehensive Test Suite**: Pass all 79 tests (75 core + 4 E2E) with 100% pass rate in 4.61s. (`✅ Completed`)
- [ ] **Mandatory Open Code Review (OCR)**: Run `ocr review` using primary model Gemini 3.8 Flash in `agy`. (`🔄 In Progress`)
- [ ] **Peer Critique Dispatch to Sol via Herdr**: Prompt Sol (`sol-critic` in pane `w1:p14`) for Round 10 critique against PAC-1 gates. (`⏳ Pending`)

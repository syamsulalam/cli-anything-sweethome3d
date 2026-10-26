# Product Requirements Document (PRD) — Sweet Home 3D CLI (`cli-anything-sweethome3d`)

**Document Version:** 2.0.0 (PAC-1 Contract)  
**Author / Orchestrator:** Gemini 3.8 Flash (Secretary) & GPT-6.1 Sol (Critic / Boss)  
**Status:** In Active Execution  
**Target:** 10.0 / 10 Production Acceptance (Production Acceptance Contract 1 — PAC-1)

---

## 1. Overview & Objective

`cli-anything-sweethome3d` is an agent-native CLI harness for Sweet Home 3D (SWEETHOME3D), following HKUDS CLI-Anything design principles. It provides headless 2D/2.5D architectural floor plan generation, validation, quantity calculation, native `.sh3d` / `Home.xml` manipulation, and multi-format vector exports (JSON, SVG, DXF, HTML).

The target is to satisfy all 58 numbered acceptance gates across 6 vectors defined in **Production Acceptance Contract 1 (PAC-1)** (`2026-10-26_SweetHome3D_CLI_Roadmap_To_10.md`), achieving an unassailable **10.0/10** score from the peer critic (mcodex Sol).

---

## 2. Six-Vector Evaluation Framework

| Vector | Current Score | Target Score | Acceptance Criteria |
|---|:---:|:---:|---|
| **Vector 1: State Integrity, Testing & Operations** | 7.0 | **10.0** | Atomic 2PC, strict journal schema validation, hash-verified backup rollback, fail-closed write blocking, OCC concurrency authority, fault-injection matrix. |
| **Vector 2: Architectural Rigor & Topology** | 4.0 | **10.0** | Noded arrangement, shared wall deduplication (7 physical walls / 21m for adjacent 3×3m rooms), explicit room/face incidence, clear core vs reference boundaries. |
| **Vector 3: Native Interoperability** | 5.0 | **10.0** | Pinned V_7_5 wire format (`version="7400"`, 8-hex colors, signed radians wall arc mapping, radian furniture/door angles), offline DTD/XSD validation, headless roundtrip. |
| **Vector 4: Spatial Conflict & Clearance** | 8.0 | **10.0** | Vertical Z-slice wall material intersection (accounting for opening cutouts), complete room footprint containment via Shapely `covers`, door approach clearance. |
| **Vector 5: Agent-Native Ergonomics** | 7.0 | **10.0** | Unified JSON envelope (`request_id`, `operation_id`, `commit.outcome`, `validation.findings`), idempotent retries, deterministic exit codes. |
| **Vector 6: Export & Visualization Parity** | 8.0 | **10.0** | One scoped resolved scene across JSON, SVG, DXF, and HTML; accurate bounds and quantity schedules. |

---

## 3. Work Breakdown & Implementation Phases

### Phase 1: State Integrity Hardening (Gates S01–S06)
- [x] **Crash Recovery Roll-Forward & Staged Candidate Pair**: Staged candidate artifact and session are completed atomically upon recovery. (`✅ Completed`)
- [x] **Fail-Closed Write Blocking**: `RecoveryRequiredError` stops new writes if `.pending` journal cannot be reconciled. (`✅ Completed`)
- [x] **Strict Journal Schema Validation (S01)**: Reject empty `{}` or incomplete journals missing required keys (`tx_id`, `target`, `staged_session`, `candidate_revision`) by raising `RecoveryRequiredError`. (`✅ Completed`)
- [x] **Hash-Verified Backup Rollback (S05)**: Enforce `_calc_sha256(backup) == base_artifact_sha256` before restoring backup over target. If backup is corrupted, fail closed without overwriting base. (`✅ Completed`)
- [x] **Distinguish Absent vs Unreadable in `_calc_sha256`**: Ensure file read errors raise or return distinct error tokens rather than being conflated with missing files. (`✅ Completed`)

### Phase 2: Native Wire-Format Conformance (Gates N01–N03)
- [x] **Integer Version Integer 7400 (N01)**: Emit `version="7400"` instead of `"7.5"` in `Home.xml`. (`✅ Completed`)
- [x] **8-Digit Hex Color Formatting (N03)**: Format RGB colors as 8 hex digits without `#` (e.g. `00EAEAEA`). (`✅ Completed`)
- [x] **Signed Radian Wall Arc Extent (N03)**: Wire adapter maps internal degrees to native `arcExtent = -math.radians(deg)`. (`✅ Completed`)
- [x] **Radian Furniture & Door Angles (N03)**: Ensure all angles emitted to XML are exact radians. (`✅ Completed`)

### Phase 3: Spatial Conflict & Clearance (Gates C01–C05)
- [x] **`FURNITURE_WALL_COLLISION` with Cutout Awareness (C03, C04)**: Test furniture footprint against actual wall material (wall envelope minus opening cutouts at furniture Z interval). (`✅ Completed`)
- [x] **`FURNITURE_OUTSIDE_ROOM` Containment (C05)**: Full footprint containment check using `room.clear_core.buffer(tol).covers(furniture_footprint)`. (`✅ Completed`)

### Phase 4: Shared Physical Wall Topology & Golden Quantities (Gates T01–T08)
- [x] **Noded Wall Topology (T01–T03)**: Deduplicate collinear overlapping walls; split at T-junctions. Adjacent 3×3m rooms produce 7 unique physical walls totaling 21m. (`✅ Completed`)
- [x] **Explicit Quantity Bases (T07–T08)**: Distinguish reference perimeter skirting (22m) from inside clear-face skirting (20.8m), clear core areas (16.245 m²), and net masonry volume (8.442 m³). (`✅ Completed`)

### Phase 5: Verification, OCR Code Review & Delegation Loop
- [x] **Comprehensive Test Suite**: Pass all 62 existing tests + new S01/S05/N01/C03/T01 tests (68/68 core + 4/4 E2E passing). (`✅ Completed`)
- [ ] **Mandatory Open Code Review (OCR)**: Run `ocr review` using primary model Gemini 3.8 Flash in `agy`. (`🔄 In Progress`)
- [ ] **Peer Critique Dispatch to Sol via Herdr**: Prompt Sol (`sol-critic` in pane `w1:p14`) for Round 9 critique against PAC-1 gates. (`⏳ Pending`)

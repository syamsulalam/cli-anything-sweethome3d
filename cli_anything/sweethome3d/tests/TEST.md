# Test Plan & Results: CLI-Anything Sweet Home 3D

**Target Software:** Sweet Home 3D  
**Harness Module:** `cli_anything.sweethome3d`  
**Execution Environment:** Python 3.13 / Windows 11  
**Methodology Standard:** CLI-Anything Phase 4 & Phase 5  

---

## 1. Test Architecture & Coverage Matrix

| Test Suite | File | Focus Area | Tested Components | Target Result |
|---|---|---|---|:---:|
| **Unit Tests** | `test_core.py` | Geometric math, entity serialization, XML parsing, ZIP archive manipulation | `Wall`, `Room`, `Opening`, `Furniture`, `HomeModel`, `_locked_save_json`, `SessionManager` | 100% Pass |
| **Integration & E2E Tests** | `test_full_e2e.py` | Click CLI execution, JSON contract, Subprocess execution, Vector SVG/DXF export | `cli_anything-sweethome3d` commands (`project`, `wall`, `room`, `opening`, `furniture`, `export`, `session`) | 100% Pass |

---

## 2. Test Cases Overview

### 2.1 Core Unit Tests (`test_core.py`)
1. `test_wall_properties`: Validates wall length, angle, coordinates ($x_1, y_1, x_2, y_2$), and point offset calculation.
2. `test_room_shoelace_area`: Verifies exact mathematical polygon area calculation for rectangles and L-shaped rooms ($A_{\text{cm}^2} \rightarrow A_{\text{m}^2}$).
3. `test_opening_properties`: Verifies wall hosting and boundary validation for doors/windows.
4. `test_furniture_catalog`: Validates preset furniture dimensions and catalog lookup.
5. `test_home_xml_roundtrip`: Asserts lossless XML serialization and deserialization matching Sweet Home 3D format (including dimension lines and arc walls).
6. `test_sh3d_zip_packaging`: Asserts `.sh3d` file creation as a valid compressed ZIP containing `Home.xml`.
7. `test_session_undo_redo`: Validates transaction history stack and state rollback.
8. `test_locked_save_json`: Verifies OS-level file locking during concurrent writes.
9. `test_dimension_line`: Validates endpoint offset geometry, metric formatting, and XML properties.
10. `test_curved_wall_arc`: Validates circular arc extent, chord vs arc length math, and serialization.
11. `test_add_wall_loop_and_with_walls`: Validates multi-segment loop creation and automatic room boundary wall generation.
12. `test_takeoff_boq`: Validates architectural material takeoff equations (gross/net masonry $m^2$, paint $m^2$, floor/ceiling $m^2$, skirting linear $m$).
13. `test_html_export`: Validates standalone interactive viewer generation with responsive canvas and takeoff metrics panel.

### 2.2 End-to-End Pipeline Tests (`test_full_e2e.py`)
1. `test_e2e_cli_runner_json_pipeline`: Full multi-step house design using Click `CliRunner`:
   - Initialize project `project new`.
   - Build 4 perimeter walls `wall add`.
   - Add enclosed living room `room add`.
   - Add entrance door `opening add`.
   - Place sofa and coffee table `furniture add`.
   - Assert all responses contain `status: success` and clean data.
2. `test_e2e_exports`: Validates generation of:
   - Layered `.svg` with valid XML tags (`<polygon>`, `<line>`, `<text>`).
   - AutoCAD `.dxf` with valid sections (`ENTITIES`, `SECTION`, `EOF`).
   - Native `.sh3d` zip archive readable by Sweet Home 3D.
   - Structured `.json` data.
3. `test_subprocess_cli`: Exercises the installed console script via `_resolve_cli()` to verify binary entry point in PATH.
4. `test_e2e_refinement_features`: Complete integration flow exercising:
   - Single-command perimeter drafting via `wall add-loop`.
   - Automated room boundary instantiation via `room add --with-walls`.
   - Dimension line measurement placement via `dimension add` and `dimension list`.
   - Automated Bill of Quantities estimation via `project takeoff`.
   - Interactive HTML viewer export via `export html`.
   - Multi-story level management via `level set-current`.

---

## 3. Verification Evidence
All 17 tests passed (100% pass rate) on Python 3.13.7 (win32) across both direct Python module and `CLI_ANYTHING_FORCE_INSTALLED=1` binary entrypoint execution modes.

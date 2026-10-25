# Sweet Home 3D Agent Harness Architecture & SOP

**Document Identity:** `SWEETHOME3D.md`  
**Application Target:** Sweet Home 3D (Free & Open Source GPLv2 Interior Design & Floor Planning Software)  
**Harness Package:** `cli-anything-sweethome3d`  
**Harness Namespace:** `cli_anything.sweethome3d`  
**Standard:** CLI-Anything 7-Phase Methodology (HKUDS/CLI-Anything)  

---

## 1. Codebase & Data Model Analysis (Phase 1)

### 1.1 The Problem Statement
Sweet Home 3D is one of the world's most widely used free, open-source architectural floor planning applications. However, it was designed entirely around an interactive human GUI (Java Swing/Java3D). It lacks a native headless CLI mode, preventing AI coding agents from generating, inspecting, modifying, and rendering floor plans directly from terminal workflows.

### 1.2 Data Model: The `.sh3d` Format and `Home.xml`
A Sweet Home 3D project file (`.sh3d`) is a standard compressed ZIP archive containing:
- `Home.xml`: The single source of truth for the entire architectural model.
- Optional resource directories: Textures, 3D OBJ models, background images.

#### Structure of `Home.xml`
```xml
<?xml version="1.0" encoding="UTF-8"?>
<home version="7.5" name="Sample Floorplan" wallHeight="280.0">
  <level id="level1" name="Ground Floor" elevation="0.0" height="280.0"/>
  
  <wall id="w1" level="level1" xStart="0.0" yStart="0.0" xEnd="600.0" yEnd="0.0" thickness="15.0" height="280.0"/>
  <wall id="w2" level="level1" xStart="600.0" yStart="0.0" xEnd="600.0" yEnd="400.0" thickness="15.0" height="280.0"/>
  <wall id="w3" level="level1" xStart="600.0" yStart="400.0" xEnd="0.0" yEnd="400.0" thickness="15.0" height="280.0"/>
  <wall id="w4" level="level1" xStart="0.0" yStart="400.0" xEnd="0.0" yEnd="0.0" thickness="15.0" height="280.0"/>

  <room id="r1" level="level1" name="Living Room" area="24.0" floorColor="#EAEAEA">
    <point x="0.0" y="0.0"/>
    <point x="600.0" y="0.0"/>
    <point x="600.0" y="400.0"/>
    <point x="0.0" y="400.0"/>
  </room>

  <doorOrWindow id="d1" wall="w4" name="Front Door" offset="150.0" width="90.0" height="210.0" type="door"/>
  
  <pieceOfFurniture id="f1" level="level1" name="Sofa" catalogId="sofa_3seat" x="200.0" y="200.0" width="220.0" depth="90.0" height="85.0" angle="0.0"/>
</home>
```

Coordinates in Sweet Home 3D are defined in centimeters (`cm`), where $1.0\text{ m} = 100.0\text{ cm}$.

---

## 2. CLI Architecture Design (Phase 2)

### 2.1 Interaction Models
- **Subcommand CLI**: `cli-anything-sweethome3d <command> <subcommand> [flags]`
- **Interactive REPL**: `cli-anything-sweethome3d` with tab completion, history, and `ReplSkin`.
- **Global Flags**:
  - `--json`: Strictly formats output as JSON for programmatic consumption by AI agents.
  - `--project <path>`: Specifies target `.sh3d` or `.json` project file.

### 2.2 Command Groups
1. `project`: `new`, `open`, `save`, `info`, `validate`, `takeoff`
2. `wall`: `add`, `add-loop`, `list`, `remove` (supports straight & curved arc walls)
3. `room`: `add` (with `--with-walls` boundary generation), `list`, `remove`, `area`
4. `opening`: `add`, `list`, `remove` (doors & windows)
5. `furniture`: `add`, `list`, `remove`, `catalog`
6. `dimension`: `add`, `list`, `remove` (architectural dimension lines)
7. `level`: `add`, `list`, `set-current` (multi-story floor levels)
8. `export`: `sh3d`, `svg`, `dxf`, `html`, `json` (supports `--level` filtering)
9. `session`: `status`, `undo`, `redo`, `history`

---

## 3. Implementation Details (Phase 3 & Refinement)

### 3.1 Concurrency & Session File Locking
Session persistence uses `_locked_save_json` to prevent race conditions when multiple agent tasks or terminal panes access `.sh3d_session.json` simultaneously.

### 3.2 Deterministic Area Calculation
Room area is computed deterministically using the Shoelace formula (Gauss's area formula):
$$A = \frac{1}{2} \left| \sum_{i=0}^{n-1} (x_i y_{i+1} - x_{i+1} y_i) \right|$$
Converted from $\text{cm}^2$ to $\text{m}^2$ ($A_{\text{m}^2} = A_{\text{cm}^2} / 10,000$).

### 3.3 Single-Command Agent Ergonomics
To prevent agent coordinate hallucination and redundant multi-step tool calls, the harness provides single-command macros:
- `wall add-loop`: Builds a complete closed perimeter or chain of connected walls from a sequence of points in one call.
- `room add --with-walls`: Creates the room polygon, computes area, and automatically instantiates surrounding structural walls in a single transaction.

### 3.4 Curved Wall Arc Math
Walls support circular curvature via `arc_extent` (angle in degrees).
Arc length is computed deterministically:
$$L = r \cdot \theta_{\text{rad}}, \quad r = \frac{c}{2 \sin(\theta_{\text{rad}} / 2)}$$
where $c$ is chord distance between endpoints.

### 3.5 Architectural Takeoff & BOQ Engine
`project takeoff` computes instant material takeoff metrics:
- Gross wall surface area ($m^2$)
- Deductions for door and window openings ($m^2$)
- Net masonry wall area ($m^2$)
- Interior paint coverage ($m^2 = 2 \times \text{Net Area}$)
- Net skirting/baseboard run ($m = \text{Perimeter} - \sum \text{Door Widths}$)
- Total finished floor and plasterboard ceiling area ($m^2$)
- Complete furniture equipment inventory count.

### 3.6 Multi-Format & Interactive Export Engine
- **SVG Export**: Layered vector drawing with room colors, dimension ticks, wall thicknesses, and door swing arcs.
- **Interactive HTML Viewer**: Zero-dependency standalone HTML5 application featuring interactive pan/zoom canvas, real-time layer visibility toggles (rooms, walls, doors/windows, furniture, dimensions), and live BOQ metrics sidebar.
- **DXF Export**: Generates AutoCAD R12/2000 `.dxf` with dedicated CAD layers (`WALLS`, `ROOMS`, `DOORS`, `FURNITURE`, `DIMENSIONS`).
- **`.sh3d` Export**: Generates standard compressed ZIP archives containing `Home.xml` ready for Sweet Home 3D GUI.
- **SVG Export**: Emits publication-quality, layered architectural drawings:
  - `<g id="site_boundary">`
  - `<g id="rooms">` with pastel fills, room labels, and square meter callouts.
  - `<g id="walls">` with thick line weights ($15\text{ cm}$ scale) and clean joins.
  - `<g id="openings">` with door swing arcs and window jambs.
  - `<g id="furniture">` with architectural top-down 2D symbols.
  - `<g id="dimensions">` with dimension lines and arrows.
- **DXF Export**: Generates industry-standard AutoCAD R12/2000 `.dxf` with dedicated CAD layers (`WALLS`, `ROOMS`, `DOORS`, `FURNITURE`, `DIMENSIONS`).
- **`.sh3d` Export**: Generates a valid ZIP archive containing `Home.xml` ready to be opened in Sweet Home 3D.

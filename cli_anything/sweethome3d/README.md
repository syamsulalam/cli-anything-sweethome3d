# CLI-Anything: Sweet Home 3D Agent-Native Terminal Harness

A full-featured, stateful, agent-native Command Line Interface (CLI) for **Sweet Home 3D** architectural floor planning software.

Built using the **HKUDS CLI-Anything** 7-phase methodology.

---

## 🚀 Quick Start

### Installation

```bash
# Clone the repository
cd cli-anything-sweethome3d

# Install locally in editable mode (PEP 420 namespace)
pip install -e .

# Verify installation in PATH
cli-anything-sweethome3d --help
```

---

## 💡 Usage Modes

### 1. Subcommand CLI Mode (Agent-First)
Run commands directly with optional `--json` for machine-readable JSON responses:

```bash
# Create a new project
cli-anything-sweethome3d --json project new --name "Modern Villa" --wall-height 300

# Add walls (coordinates in centimeters: 6m x 4m room)
cli-anything-sweethome3d --json wall add --id w1 --x1 0 --y1 0 --x2 600 --y2 0 --thickness 15
cli-anything-sweethome3d --json wall add --id w2 --x1 600 --y1 0 --x2 600 --y2 400 --thickness 15
cli-anything-sweethome3d --json wall add --id w3 --x1 600 --y1 400 --x2 0 --y2 400 --thickness 15
cli-anything-sweethome3d --json wall add --id w4 --x1 0 --y1 400 --x2 0 --y2 0 --thickness 15

# Add an enclosed architectural room polygon (computes exact 24.00 m² area)
cli-anything-sweethome3d --json room add --id r1 --name "Living Room" --points "0,0;600,0;600,400;0,400" --color "#EAEAEA"

# Add a door and window opening
cli-anything-sweethome3d --json opening add --id d1 --name "Front Entrance" --wall w4 --offset 150 --width 90 --type door --swing right
cli-anything-sweethome3d --json opening add --id win1 --name "Living Window" --wall w1 --offset 200 --width 150 --type window

# Place furniture from the catalog
cli-anything-sweethome3d --json furniture add --id sofa1 --catalog sofa_3seat --x 300 --y 200 --angle 0

# Export publication-quality SVG, DXF, or native .sh3d files
cli-anything-sweethome3d --json export svg -o output/floorplan.svg
cli-anything-sweethome3d --json export dxf -o output/floorplan.dxf
cli-anything-sweethome3d --json export sh3d -o output/villa.sh3d
cli-anything-sweethome3d --json export json -o output/model.json
```

### 2. Interactive REPL Mode
Type `cli-anything-sweethome3d` without arguments to launch the branded interactive shell:

```bash
cli-anything-sweethome3d
```

```
================================================================================
  CLI-Anything: Sweet Home 3D Agent-Native Terminal Harness v1.0.0
  Making Architectural Floor Planning Autonomous & Agent-Native
  Type 'help' for commands, 'exit' or Ctrl-C to quit.
================================================================================

[Modern Villa] > project info
Project Name        : Modern Villa
Active File         : (Unsaved In-Memory)
Total Area          : 24.00 m²
Total Wall Run      : 20.0 m
Walls Count         : 4
Rooms Count         : 1
Openings Count      : 2
Furniture Count     : 1

[Modern Villa] > export svg -o my_house.svg
✓ Exported: C:\Users\THINKPAD\my_house.svg (format: svg)
```

---

## 📐 Command Reference

| Command Group | Subcommand | Description |
|---|---|---|
| `project` | `new` | Initialize a new in-memory floor plan project. |
| `project` | `open` | Open an existing `.sh3d` or `.json` project file. |
| `project` | `save` | Save active project to `.sh3d` or `.json`. |
| `project` | `info` | Show project metadata, total area ($m^2$), and element counts. |
| `project` | `validate`| Check wall bounds, opening lengths, and room polygon closures. |
| `wall` | `add` | Add a wall segment with start/end (X,Y), thickness, and height. |
| `wall` | `list` | Print tabular list of all walls with lengths in meters. |
| `wall` | `remove` | Delete a wall by ID (cascades to attached openings). |
| `room` | `add` | Define an architectural room polygon and calculate area via Shoelace formula. |
| `room` | `list` | Print tabular list of rooms, areas ($m^2$), colors, and centroids. |
| `room` | `area` | Query the exact calculated area for a specific room. |
| `opening` | `add` | Place a door or window opening on a host wall. |
| `opening` | `list` | List doors and windows with offsets, widths, and swing types. |
| `furniture`| `catalog` | List standard furniture presets (beds, sofas, tables, fixtures). |
| `furniture`| `add` | Place a furniture item with coordinates and rotation angle. |
| `furniture`| `list` | List placed furniture items. |
| `export` | `svg` | Export publication-grade vector SVG with layers and dimension callouts. |
| `export` | `dxf` | Export AutoCAD-compatible DXF R12 text file. |
| `export` | `sh3d` | Export native Sweet Home 3D compressed ZIP archive containing `Home.xml`. |
| `export` | `json` | Export structured JSON representation. |
| `session` | `status` | View session status, active file path, and undo/redo depth. |
| `session` | `undo` | Revert the last operation. |
| `session` | `redo` | Re-apply the undone operation. |
| `gui` | `launch` | Open active project in the Sweet Home 3D desktop application. |

---

## 🧪 Testing

```bash
# Run unit tests
python -m pytest cli_anything/sweethome3d/tests/test_core.py -v

# Run full end-to-end integration tests
python -m pytest cli_anything/sweethome3d/tests/test_full_e2e.py -v

# Force-installed validation mode
CLI_ANYTHING_FORCE_INSTALLED=1 python -m pytest cli_anything/sweethome3d/tests/ -v
```

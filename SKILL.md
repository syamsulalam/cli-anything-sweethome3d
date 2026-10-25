---
name: cli-anything-sweethome3d
description: "Agent-native CLI harness for Sweet Home 3D floor planning software. Create, inspect, modify, and validate 2D/3D architectural house designs, walls, rooms, doors, windows, and furniture. Exports publication-grade vector SVG, AutoCAD DXF, JSON schemas, and native .sh3d compressed project archives."
---

# Sweet Home 3D Agent Harness (`cli-anything-sweethome3d`)

Operate Sweet Home 3D headlessly from AI coding agents (Antigravity, Codex, CommandCode).

---

## 1. Quick Invocation Examples

Always use `--json` for structured, machine-readable output:

```bash
# Initialize a new home project
cli-anything-sweethome3d --json project new --name "Pavilion Residence" --wall-height 300

# Add walls (coordinates in centimeters)
cli-anything-sweethome3d --json wall add --id w1 --x1 0 --y1 0 --x2 800 --y2 0 --thickness 15
cli-anything-sweethome3d --json wall add --id w2 --x1 800 --y1 0 --x2 800 --y2 600 --thickness 15
cli-anything-sweethome3d --json wall add --id w3 --x1 800 --y1 600 --x2 0 --y2 600 --thickness 15
cli-anything-sweethome3d --json wall add --id w4 --x1 0 --y1 600 --x2 0 --y2 0 --thickness 15

# Add room polygon (calculates 48.00 m² automatically)
cli-anything-sweethome3d --json room add --id r1 --name "Open Living & Dining" --points "0,0;800,0;800,600;0,600" --color "#F5F5F0"

# Add entrance door and garden window
cli-anything-sweethome3d --json opening add --id d1 --name "Entrance" --wall w4 --offset 250 --width 100 --type door
cli-anything-sweethome3d --json opening add --id win1 --name "Garden Window" --wall w2 --offset 200 --width 200 --type window

# Place catalog furniture
cli-anything-sweethome3d --json furniture add --id sofa1 --catalog sofa_3seat --x 400 --y 300 --angle 0
cli-anything-sweethome3d --json furniture add --id table1 --catalog dining_table_6 --x 400 --y 500

# Architectural validation check
cli-anything-sweethome3d --json project validate

# Export artifacts
cli-anything-sweethome3d --json export svg -o floorplan.svg
cli-anything-sweethome3d --json export html -o viewer.html
cli-anything-sweethome3d --json export dxf -o floorplan.dxf
cli-anything-sweethome3d --json export sh3d -o house.sh3d

# Single-Command Ergonomics (Loops & Room Walls)
cli-anything-sweethome3d --json wall add-loop --points "0,0;800,0;800,600;0,600" --prefix ext_
cli-anything-sweethome3d --json room add --id r2 --name "Kitchen" --points "0,0;400,0;400,300;0,300" --with-walls

# Dimension Lines & Takeoff Bill of Quantities (BOQ / RAB)
cli-anything-sweethome3d --json dimension add --id dim1 --x1 0 --y1 0 --x2 800 --y2 0 --offset 40
cli-anything-sweethome3d --json project takeoff
```

---

## 2. Interactive REPL Mode

Launch the interactive REPL without arguments:
```bash
cli-anything-sweethome3d
```
Commands supported: `project`, `wall`, `room`, `opening`, `furniture`, `level`, `export`, `session`, `gui`.
Type `help` inside REPL for command guidance, or `exit` to quit.

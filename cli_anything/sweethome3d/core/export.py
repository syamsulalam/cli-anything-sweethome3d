import os
import math
import json

def calculate_bounds(model, margin_cm=100.0, level_id=None):
    """Calculates min/max bounding box coordinates in cm across walls and rooms."""
    xs = []
    ys = []
    walls = [w for w in model.walls.values() if not level_id or w.level_id == level_id]
    rooms = [r for r in model.rooms.values() if not level_id or r.level_id == level_id]
    furniture = [f for f in model.furniture.values() if not level_id or f.level_id == level_id]
    dimensions = [d for d in model.dimensions.values() if not level_id or d.level_id == level_id]

    for w in walls:
        xs.extend([w.x_start, w.x_end])
        ys.extend([w.y_start, w.y_end])
    for r in rooms:
        for p in r.points:
            xs.append(p[0])
            ys.append(p[1])
    for f in furniture:
        xs.append(f.x_cm)
        ys.append(f.y_cm)
    for dim in dimensions:
        p1, p2 = dim.offset_endpoints
        xs.extend([dim.x_start, dim.x_end, p1[0], p2[0]])
        ys.extend([dim.y_start, dim.y_end, p1[1], p2[1]])

    if not xs or not ys:
        return 0.0, 0.0, 800.0, 600.0

    min_x = min(xs) - margin_cm
    min_y = min(ys) - margin_cm
    max_x = max(xs) + margin_cm
    max_y = max(ys) + margin_cm

    width = max_x - min_x
    height = max_y - min_y
    return min_x, min_y, width, height

def export_svg(model, filepath, level_id=None):
    """Exports the floor plan as a layered architectural vector SVG."""
    min_x, min_y, width, height = calculate_bounds(model, margin_cm=80.0, level_id=level_id)
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

    walls = [w for w in model.walls.values() if not level_id or w.level_id == level_id]
    rooms = [r for r in model.rooms.values() if not level_id or r.level_id == level_id]
    openings = [op for op in model.openings.values() if op.wall_id in {w.id for w in walls}]
    furniture = [f for f in model.furniture.values() if not level_id or f.level_id == level_id]
    dimensions = [dim for dim in model.dimensions.values() if not level_id or dim.level_id == level_id]

    level_title = f" - Level: {level_id}" if level_id else ""

    svg_lines = [
        f'<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x:.1f} {min_y:.1f} {width:.1f} {height:.1f}" width="100%" height="100%">',
        f'  <defs>',
        f'    <style>',
        f'      .room {{ stroke: #888888; stroke-width: 1.5; stroke-dasharray: 4,4; fill-opacity: 0.7; }}',
        f'      .room-label {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; font-size: 22px; font-weight: bold; fill: #222222; text-anchor: middle; }}',
        f'      .room-area {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; font-size: 16px; fill: #555555; text-anchor: middle; }}',
        f'      .wall {{ stroke: #1a1a1a; stroke-linecap: square; stroke-linejoin: miter; fill: none; }}',
        f'      .door {{ stroke: #c0392b; stroke-width: 2.5; fill: none; }}',
        f'      .door-arc {{ stroke: #e74c3c; stroke-width: 1.5; stroke-dasharray: 3,3; fill: none; }}',
        f'      .window {{ stroke: #2980b9; stroke-width: 3.5; fill: none; }}',
        f'      .furniture {{ stroke: #27ae60; stroke-width: 2.0; fill: #e8f8f5; fill-opacity: 0.8; }}',
        f'      .furniture-label {{ font-family: sans-serif; font-size: 11px; fill: #1e8449; text-anchor: middle; }}',
        f'      .dimension-line {{ stroke: #7f8c8d; stroke-width: 1.5; fill: none; }}',
        f'      .dimension-ext {{ stroke: #bdc3c7; stroke-width: 1.0; stroke-dasharray: 2,2; fill: none; }}',
        f'      .dimension-text {{ font-family: monospace, sans-serif; font-size: 14px; font-weight: bold; fill: #34495e; text-anchor: middle; }}',
        f'      .title-block {{ font-family: sans-serif; font-size: 18px; font-weight: bold; fill: #2c3e50; }}',
        f'    </style>',
        f'  </defs>',
        f'',
        f'  <!-- Background Canvas -->',
        f'  <rect x="{min_x:.1f}" y="{min_y:.1f}" width="{width:.1f}" height="{height:.1f}" fill="#fafafa"/>',
        f'',
        f'  <!-- Layer 1: Rooms (Flooring & Area Labels) -->',
        f'  <g id="layer-rooms">',
    ]

    # Render Rooms
    for r in rooms:
        if len(r.points) >= 3:
            pts_str = " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in r.points)
            svg_lines.append(f'    <polygon class="room" points="{pts_str}" fill="{r.floor_color}"/>')
            cx, cy = r.centroid
            svg_lines.append(f'    <text class="room-label" x="{cx:.1f}" y="{cy - 8:.1f}">{r.name}</text>')
            svg_lines.append(f'    <text class="room-area" x="{cx:.1f}" y="{cy + 16:.1f}">{r.area_m2:.2f} m²</text>')

    svg_lines.extend([
        f'  </g>',
        f'',
        f'  <!-- Layer 2: Furniture Assets -->',
        f'  <g id="layer-furniture">',
    ])

    # Render Furniture
    for f in furniture:
        w = f.width_cm
        d = f.depth_cm
        svg_lines.append(
            f'    <g transform="translate({f.x_cm:.1f}, {f.y_cm:.1f}) rotate({f.angle_deg:.1f})">'
        )
        svg_lines.append(
            f'      <rect class="furniture" x="{-w/2.0:.1f}" y="{-d/2.0:.1f}" width="{w:.1f}" height="{d:.1f}" rx="4"/>'
        )
        svg_lines.append(
            f'      <text class="furniture-label" x="0" y="4">{f.name}</text>'
        )
        svg_lines.append(f'    </g>')

    svg_lines.extend([
        f'  </g>',
        f'',
        f'  <!-- Layer 3: Walls (Straight & Curved) -->',
        f'  <g id="layer-walls">',
    ])

    # Render Walls
    for w in walls:
        if abs(w.arc_extent) < 1e-4:
            svg_lines.append(
                f'    <line class="wall" x1="{w.x_start:.1f}" y1="{w.y_start:.1f}" '
                f'x2="{w.x_end:.1f}" y2="{w.y_end:.1f}" stroke-width="{w.thickness:.1f}"/>'
            )
        else:
            # Curved wall rendering
            c = w.chord_length
            theta_rad = abs(math.radians(w.arc_extent))
            sin_half = math.sin(theta_rad / 2.0)
            r = c / (2.0 * sin_half) if abs(sin_half) > 1e-6 else c
            large_arc = 1 if abs(w.arc_extent) > 180.0 else 0
            sweep = 1 if w.arc_extent > 0 else 0
            svg_lines.append(
                f'    <path class="wall" d="M {w.x_start:.1f} {w.y_start:.1f} '
                f'A {r:.1f} {r:.1f} 0 {large_arc} {sweep} {w.x_end:.1f} {w.y_end:.1f}" '
                f'stroke-width="{w.thickness:.1f}"/>'
            )

    svg_lines.extend([
        f'  </g>',
        f'',
        f'  <!-- Layer 4: Openings (Doors & Windows) -->',
        f'  <g id="layer-openings">',
    ])

    # Render Openings
    for op in openings:
        wall = model.walls.get(op.wall_id)
        if wall:
            p1 = wall.point_at_offset(op.offset_cm)
            p2 = wall.point_at_offset(op.offset_cm + op.width_cm)
            if op.opening_type == "door":
                # Door gap
                svg_lines.append(
                    f'    <line stroke="#ffffff" stroke-width="{wall.thickness + 2:.1f}" '
                    f'x1="{p1[0]:.1f}" y1="{p1[1]:.1f}" x2="{p2[0]:.1f}" y2="{p2[1]:.1f}"/>'
                )
                # Door leaf
                svg_lines.append(
                    f'    <line class="door" x1="{p1[0]:.1f}" y1="{p1[1]:.1f}" x2="{p2[0]:.1f}" y2="{p2[1]:.1f}"/>'
                )
                # Door swing arc
                r_cm = op.width_cm
                svg_lines.append(
                    f'    <path class="door-arc" d="M {p1[0]:.1f} {p1[1]:.1f} A {r_cm:.1f} {r_cm:.1f} 0 0 1 {p2[0]:.1f} {p2[1]:.1f}"/>'
                )
            else:
                # Window gap + glass line
                svg_lines.append(
                    f'    <line stroke="#ffffff" stroke-width="{wall.thickness + 2:.1f}" '
                    f'x1="{p1[0]:.1f}" y1="{p1[1]:.1f}" x2="{p2[0]:.1f}" y2="{p2[1]:.1f}"/>'
                )
                svg_lines.append(
                    f'    <line class="window" x1="{p1[0]:.1f}" y1="{p1[1]:.1f}" x2="{p2[0]:.1f}" y2="{p2[1]:.1f}"/>'
                )

    svg_lines.extend([
        f'  </g>',
        f'',
        f'  <!-- Layer 5: Dimension Lines -->',
        f'  <g id="layer-dimensions">',
    ])

    # Render Dimensions
    for dim in dimensions:
        p1_off, p2_off = dim.offset_endpoints
        # Extension lines
        svg_lines.append(
            f'    <line class="dimension-ext" x1="{dim.x_start:.1f}" y1="{dim.y_start:.1f}" x2="{p1_off[0]:.1f}" y2="{p1_off[1]:.1f}"/>'
        )
        svg_lines.append(
            f'    <line class="dimension-ext" x1="{dim.x_end:.1f}" y1="{dim.y_end:.1f}" x2="{p2_off[0]:.1f}" y2="{p2_off[1]:.1f}"/>'
        )
        # Main dimension line
        svg_lines.append(
            f'    <line class="dimension-line" x1="{p1_off[0]:.1f}" y1="{p1_off[1]:.1f}" x2="{p2_off[0]:.1f}" y2="{p2_off[1]:.1f}"/>'
        )
        # 45 degree ticks at endpoints
        for px, py in [p1_off, p2_off]:
            svg_lines.append(
                f'    <line stroke="#7f8c8d" stroke-width="1.5" x1="{px - 5:.1f}" y1="{py - 5:.1f}" x2="{px + 5:.1f}" y2="{py + 5:.1f}"/>'
            )
        # Dimension text
        mid_x = (p1_off[0] + p2_off[0]) / 2.0
        mid_y = (p1_off[1] + p2_off[1]) / 2.0 - 6.0
        svg_lines.append(
            f'    <text class="dimension-text" x="{mid_x:.1f}" y="{mid_y:.1f}">{dim.distance_m:.2f} m</text>'
        )

    svg_lines.extend([
        f'  </g>',
        f'',
        f'  <!-- Layer 6: Title Block -->',
        f'  <g id="layer-title-block">',
        f'    <text class="title-block" x="{min_x + 30:.1f}" y="{min_y + 40:.1f}">{model.name}{level_title}</text>',
        f'    <text class="room-area" x="{min_x + 30:.1f}" y="{min_y + 70:.1f}" text-anchor="start">Total Area: {model.total_area_m2:.2f} m² | Walls: {model.total_wall_length_m:.1f} m</text>',
        f'  </g>',
        f'</svg>',
    ])

    svg_content = "\n".join(svg_lines)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(svg_content)

    return filepath

def export_dxf(model, filepath, level_id=None):
    """Exports the floor plan as an AutoCAD-compatible DXF R12 text file."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    dxf = []

    walls = [w for w in model.walls.values() if not level_id or w.level_id == level_id]
    rooms = [r for r in model.rooms.values() if not level_id or r.level_id == level_id]
    furniture = [f for f in model.furniture.values() if not level_id or f.level_id == level_id]
    dimensions = [dim for dim in model.dimensions.values() if not level_id or dim.level_id == level_id]

    # DXF Header
    dxf.extend(["0", "SECTION", "2", "HEADER", "0", "ENDSEC"])

    # DXF Tables (Layers)
    dxf.extend([
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LAYER", "70", "5",
        "0", "LAYER", "2", "WALLS", "70", "0", "62", "7", "6", "CONTINUOUS",
        "0", "LAYER", "2", "ROOMS", "70", "0", "62", "4", "6", "CONTINUOUS",
        "0", "LAYER", "2", "DOORS", "70", "0", "62", "1", "6", "CONTINUOUS",
        "0", "LAYER", "2", "FURNITURE", "70", "0", "62", "3", "6", "CONTINUOUS",
        "0", "LAYER", "2", "DIMENSIONS", "70", "0", "62", "2", "6", "CONTINUOUS",
        "0", "ENDTAB",
        "0", "ENDSEC",
    ])

    # DXF Entities
    dxf.extend(["0", "SECTION", "2", "ENTITIES"])

    # Walls as Lines
    for w in walls:
        dxf.extend([
            "0", "LINE", "8", "WALLS",
            "10", f"{w.x_start:.2f}", "20", f"{w.y_start:.2f}", "30", "0.0",
            "11", f"{w.x_end:.2f}", "21", f"{w.y_end:.2f}", "31", "0.0",
        ])

    # Rooms as Polylines + Text labels
    for r in rooms:
        if len(r.points) >= 3:
            dxf.extend(["0", "POLYLINE", "8", "ROOMS", "66", "1", "70", "1"])
            for p in r.points:
                dxf.extend(["0", "VERTEX", "8", "ROOMS", "10", f"{p[0]:.2f}", "20", f"{p[1]:.2f}", "30", "0.0"])
            dxf.extend(["0", "SEQEND"])
            cx, cy = r.centroid
            dxf.extend([
                "0", "TEXT", "8", "ROOMS",
                "10", f"{cx:.2f}", "20", f"{cy:.2f}", "30", "0.0",
                "40", "20.0", "1", f"{r.name} ({r.area_m2:.1f} m2)",
            ])

    # Furniture as Text
    for f in furniture:
        dxf.extend([
            "0", "TEXT", "8", "FURNITURE",
            "10", f"{f.x_cm:.2f}", "20", f"{f.y_cm:.2f}", "30", "0.0",
            "40", "15.0", "1", f.name,
        ])

    # Dimensions as Lines + Text
    for dim in dimensions:
        p1_off, p2_off = dim.offset_endpoints
        dxf.extend([
            "0", "LINE", "8", "DIMENSIONS",
            "10", f"{p1_off[0]:.2f}", "20", f"{p1_off[1]:.2f}", "30", "0.0",
            "11", f"{p2_off[0]:.2f}", "21", f"{p2_off[1]:.2f}", "31", "0.0",
        ])
        mid_x = (p1_off[0] + p2_off[0]) / 2.0
        mid_y = (p1_off[1] + p2_off[1]) / 2.0
        dxf.extend([
            "0", "TEXT", "8", "DIMENSIONS",
            "10", f"{mid_x:.2f}", "20", f"{mid_y:.2f}", "30", "0.0",
            "40", "14.0", "1", f"{dim.distance_m:.2f}m",
        ])

    dxf.extend(["0", "ENDSEC", "0", "EOF"])

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(dxf) + "\n")

    return filepath

def export_json(model, filepath, level_id=None):
    """Exports model to formatted JSON file, optionally filtered by level."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    d = model.to_dict()
    if level_id:
        d["walls"] = [w for w in d["walls"] if w.get("level_id") == level_id]
        d["rooms"] = [r for r in d["rooms"] if r.get("level_id") == level_id]
        d["furniture"] = [f for f in d["furniture"] if f.get("level_id") == level_id]
        d["dimensions"] = [dim for dim in d["dimensions"] if dim.get("level_id") == level_id]
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    return filepath

def export_html(model, filepath, level_id=None):
    """Generates a responsive, interactive single-file HTML floor plan viewer with pan/zoom and layer toggles."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    
    # Generate in-memory SVG
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
        tmp_svg_path = tmp.name
    try:
        export_svg(model, tmp_svg_path, level_id=level_id)
        with open(tmp_svg_path, "r", encoding="utf-8") as f:
            svg_xml = f.read()
    finally:
        if os.path.exists(tmp_svg_path):
            os.remove(tmp_svg_path)

    takeoff_data = model.takeoff()

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{model.name} - Architectural Floor Plan Viewer</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #1a1a24; color: #e0e0e0; display: flex; height: 100vh; overflow: hidden; }}
    #sidebar {{ width: 340px; background: #222230; border-right: 1px solid #333344; display: flex; flex-direction: column; padding: 20px; overflow-y: auto; }}
    #sidebar h1 {{ font-size: 18px; color: #ffffff; margin-bottom: 6px; }}
    #sidebar .meta {{ font-size: 13px; color: #8888aa; margin-bottom: 18px; }}
    .section-title {{ font-size: 12px; font-weight: bold; text-transform: uppercase; color: #8888aa; margin: 16px 0 8px; letter-spacing: 0.5px; }}
    .stat-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 14px; }}
    .stat-card {{ background: #2a2a3c; border-radius: 8px; padding: 12px; }}
    .stat-val {{ font-size: 18px; font-weight: bold; color: #00d4ff; }}
    .stat-lbl {{ font-size: 11px; color: #9999bb; }}
    .control-group label {{ display: flex; align-items: center; gap: 8px; font-size: 13px; padding: 6px 0; cursor: pointer; }}
    .btn-group {{ display: flex; gap: 8px; margin-top: 10px; }}
    button {{ flex: 1; padding: 8px 12px; background: #3b3b52; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 12px; }}
    button:hover {{ background: #4a4a68; }}
    #viewport {{ flex: 1; position: relative; overflow: hidden; background: #12121a; display: flex; align-items: center; justify-content: center; }}
    #svg-container {{ width: 95%; height: 95%; transition: transform 0.1s ease-out; transform-origin: center center; }}
    #svg-container svg {{ width: 100%; height: 100%; }}
  </style>
</head>
<body>
  <div id="sidebar">
    <h1>{model.name}</h1>
    <div class="meta">Sweet Home 3D Agent Harness Export</div>

    <div class="stat-grid takeoff-panel">
      <div class="stat-card">
        <div class="stat-val">{takeoff_data['floor_and_ceiling']['total_floor_area_m2']:.2f} m²</div>
        <div class="stat-lbl">Total Floor Area</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">{takeoff_data['masonry_and_walls']['total_wall_length_m']:.1f} m</div>
        <div class="stat-lbl">Linear Wall Run</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">{takeoff_data['masonry_and_walls']['net_masonry_wall_m2']:.1f} m²</div>
        <div class="stat-lbl">Net Masonry Wall</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">{takeoff_data['furniture_schedule']['total_items']}</div>
        <div class="stat-lbl">Furniture Assets</div>
      </div>
    </div>

    <div class="section-title">Layer Visibility</div>
    <div class="control-group">
      <label><input type="checkbox" id="toggle-rooms" checked onchange="toggleLayer('layer-rooms', this.checked)"> Rooms & Flooring</label>
      <label><input type="checkbox" id="toggle-walls" checked onchange="toggleLayer('layer-walls', this.checked)"> Structural Walls</label>
      <label><input type="checkbox" id="toggle-openings" checked onchange="toggleLayer('layer-openings', this.checked)"> Doors & Windows</label>
      <label><input type="checkbox" id="toggle-furniture" checked onchange="toggleLayer('layer-furniture', this.checked)"> Furniture Layout</label>
      <label><input type="checkbox" id="toggle-dimensions" checked onchange="toggleLayer('layer-dimensions', this.checked)"> Dimension Lines</label>
    </div>

    <div class="section-title">Canvas Controls</div>
    <div class="btn-group">
      <button onclick="zoom(1.2)">Zoom In (+)</button>
      <button onclick="zoom(0.8)">Zoom Out (-)</button>
      <button onclick="resetZoom()">Reset</button>
    </div>
  </div>

  <div id="viewport">
    <div id="svg-container">
      {svg_xml}
    </div>
  </div>

  <script>
    let scale = 1.0;
    function zoom(factor) {{
      scale *= factor;
      document.getElementById('svg-container').style.transform = `scale(${{scale}})`;
    }}
    function resetZoom() {{
      scale = 1.0;
      document.getElementById('svg-container').style.transform = 'scale(1.0)';
    }}
    function toggleLayer(layerId, visible) {{
      const el = document.getElementById(layerId);
      if (el) el.style.display = visible ? 'inline' : 'none';
    }}
  </script>
</body>
</html>
"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    return filepath

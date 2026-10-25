import os
import math
import json

def calculate_bounds(model, margin_cm=100.0):
    """Calculates min/max bounding box coordinates in cm across walls and rooms."""
    xs = []
    ys = []
    for w in model.walls.values():
        xs.extend([w.x_start, w.x_end])
        ys.extend([w.y_start, w.y_end])
    for r in model.rooms.values():
        for p in r.points:
            xs.append(p[0])
            ys.append(p[1])
    for f in model.furniture.values():
        xs.append(f.x_cm)
        ys.append(f.y_cm)

    if not xs or not ys:
        return 0.0, 0.0, 800.0, 600.0

    min_x = min(xs) - margin_cm
    min_y = min(ys) - margin_cm
    max_x = max(xs) + margin_cm
    max_y = max(ys) + margin_cm

    width = max_x - min_x
    height = max_y - min_y
    return min_x, min_y, width, height

def export_svg(model, filepath):
    """Exports the floor plan as a layered architectural vector SVG."""
    min_x, min_y, width, height = calculate_bounds(model, margin_cm=80.0)
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

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
        f'      .title-block {{ font-family: sans-serif; font-size: 18px; font-weight: bold; fill: #2c3e50; }}',
        f'      .grid-line {{ stroke: #eeeeee; stroke-width: 1.0; }}',
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
    for r in model.rooms.values():
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
    for f in model.furniture.values():
        w = f.width_cm
        d = f.depth_cm
        # Bounding box centered at (f.x_cm, f.y_cm) with rotation
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
        f'  <!-- Layer 3: Walls -->',
        f'  <g id="layer-walls">',
    ])

    # Render Walls
    for w in model.walls.values():
        svg_lines.append(
            f'    <line class="wall" x1="{w.x_start:.1f}" y1="{w.y_start:.1f}" '
            f'x2="{w.x_end:.1f}" y2="{w.y_end:.1f}" stroke-width="{w.thickness:.1f}"/>'
        )

    svg_lines.extend([
        f'  </g>',
        f'',
        f'  <!-- Layer 4: Openings (Doors & Windows) -->',
        f'  <g id="layer-openings">',
    ])

    # Render Openings
    for op in model.openings.values():
        wall = model.walls.get(op.wall_id)
        if wall:
            p1 = wall.point_at_offset(op.offset_cm)
            p2 = wall.point_at_offset(op.offset_cm + op.width_cm)
            if op.opening_type == "door":
                # Door cut line (gap)
                svg_lines.append(
                    f'    <line stroke="#ffffff" stroke-width="{wall.thickness + 2:.1f}" '
                    f'x1="{p1[0]:.1f}" y1="{p1[1]:.1f}" x2="{p2[0]:.1f}" y2="{p2[1]:.1f}"/>'
                )
                # Door leaf
                svg_lines.append(
                    f'    <line class="door" x1="{p1[0]:.1f}" y1="{p1[1]:.1f}" x2="{p2[0]:.1f}" y2="{p2[1]:.1f}"/>'
                )
                # Door swing arc (circle representation around p1)
                r_cm = op.width_cm
                svg_lines.append(
                    f'    <path class="door-arc" d="M {p1[0]:.1f} {p1[1]:.1f} A {r_cm:.1f} {r_cm:.1f} 0 0 1 {p2[0]:.1f} {p2[1]:.1f}"/>'
                )
            else:
                # Window: white gap + glass line
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
        f'  <!-- Layer 5: Title Block -->',
        f'  <g id="layer-title-block">',
        f'    <text class="title-block" x="{min_x + 30:.1f}" y="{min_y + 40:.1f}">{model.name}</text>',
        f'    <text class="room-area" x="{min_x + 30:.1f}" y="{min_y + 70:.1f}" text-anchor="start">Total Area: {model.total_area_m2:.2f} m² | Walls: {model.total_wall_length_m:.1f} m</text>',
        f'  </g>',
        f'</svg>',
    ])

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(svg_lines))

    return filepath

def export_dxf(model, filepath):
    """Exports the floor plan as an AutoCAD-compatible DXF R12 text file."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    dxf = []

    # DXF Header
    dxf.extend(["0", "SECTION", "2", "HEADER", "0", "ENDSEC"])

    # DXF Tables (Layers)
    dxf.extend([
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LAYER", "70", "4",
        "0", "LAYER", "2", "WALLS", "70", "0", "62", "7", "6", "CONTINUOUS",
        "0", "LAYER", "2", "ROOMS", "70", "0", "62", "4", "6", "CONTINUOUS",
        "0", "LAYER", "2", "DOORS", "70", "0", "62", "1", "6", "CONTINUOUS",
        "0", "LAYER", "2", "FURNITURE", "70", "0", "62", "3", "6", "CONTINUOUS",
        "0", "ENDTAB",
        "0", "ENDSEC",
    ])

    # DXF Entities
    dxf.extend(["0", "SECTION", "2", "ENTITIES"])

    # Walls as Lines
    for w in model.walls.values():
        dxf.extend([
            "0", "LINE", "8", "WALLS",
            "10", f"{w.x_start:.2f}", "20", f"{w.y_start:.2f}", "30", "0.0",
            "11", f"{w.x_end:.2f}", "21", f"{w.y_end:.2f}", "31", "0.0",
        ])

    # Rooms as Polylines + Text labels
    for r in model.rooms.values():
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

    # Furniture as Points/Blocks
    for f in model.furniture.values():
        dxf.extend([
            "0", "TEXT", "8", "FURNITURE",
            "10", f"{f.x_cm:.2f}", "20", f"{f.y_cm:.2f}", "30", "0.0",
            "40", "15.0", "1", f.name,
        ])

    dxf.extend(["0", "ENDSEC", "0", "EOF"])

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(dxf) + "\n")

    return filepath

def export_json(model, filepath):
    """Exports model to formatted JSON file."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(model.to_dict(), f, indent=2)
    return filepath

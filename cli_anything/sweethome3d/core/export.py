import os
import math
import json
import re
import html
import xml.sax.saxutils as saxutils

def _get_furn_corners(f):
    rad = math.radians(f.angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    dx1, dy1 = (f.width_cm / 2.0) * cos_a, (f.width_cm / 2.0) * sin_a
    dx2, dy2 = -(f.depth_cm / 2.0) * sin_a, (f.depth_cm / 2.0) * cos_a
    return [
        (f.x_cm - dx1 - dx2, f.y_cm - dy1 - dy2),
        (f.x_cm + dx1 - dx2, f.y_cm - dy1 - dy2),
        (f.x_cm + dx1 + dx2, f.y_cm + dy1 + dy2),
        (f.x_cm - dx1 + dx2, f.y_cm - dy1 + dy2),
    ]

def calculate_bounds(model, margin_cm=100.0, level_id=None):
    """Calculates min/max bounding box coordinates in cm across walls, rooms, and furniture."""
    xs = []
    ys = []
    walls = [w for w in model.walls.values() if not level_id or w.level_id == level_id]
    rooms = [r for r in model.rooms.values() if not level_id or r.level_id == level_id]
    furniture = [f for f in model.furniture.values() if not level_id or f.level_id == level_id]
    dimensions = [d for d in model.dimensions.values() if not level_id or d.level_id == level_id]

    for w in walls:
        t = w.thickness / 2.0
        xs.extend([w.x_start - t, w.x_start + t, w.x_end - t, w.x_end + t])
        ys.extend([w.y_start - t, w.y_start + t, w.y_end - t, w.y_end + t])
        if abs(w.arc_extent) > 1e-4 and w.length > 0:
            for ex, ey in w.get_arc_extrema():
                xs.extend([ex - t, ex + t])
                ys.extend([ey - t, ey + t])
            for step in range(1, 16):
                pt = w.point_at_offset(w.length * (step / 16.0))
                xs.extend([pt[0] - t, pt[0] + t])
                ys.extend([pt[1] - t, pt[1] + t])
    for op in model.openings.values():
        if not level_id or op.level_id == level_id:
            wall = model.walls.get(op.wall_id)
            geom = op.get_effective_geometry(wall)
            if geom:
                p1 = geom["p1"]
                p2 = geom["p2"]
                xs.extend([p1[0], p2[0]])
                ys.extend([p1[1], p2[1]])
                if op.opening_type == "door":
                    hinge = geom["hinge"]
                    w = geom["width"]
                    if op.swing in ("left", "right"):
                        p_open = geom["open_endpoint"]
                        xs.extend([p_open[0], hinge[0] - w, hinge[0] + w])
                        ys.extend([p_open[1], hinge[1] - w, hinge[1] + w])
    for r in rooms:
        for p in r.points:
            xs.append(p[0])
            ys.append(p[1])
    for f in furniture:
        radius = math.hypot(f.width_cm / 2.0, f.depth_cm / 2.0)
        xs.extend([f.x_cm - radius, f.x_cm + radius])
        ys.extend([f.y_cm - radius, f.y_cm + radius])
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
    wall_ids = {w.id for w in walls}
    openings = [
        op for op in model.openings.values()
        if op.wall_id in wall_ids or (not op.wall_id and (not level_id or op.level_id == level_id))
    ]
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
            floor_color = r.floor_color if re.match(r"^#[0-9a-fA-F]{3,8}$|^[a-zA-Z]+$", r.floor_color) else "#EAEAEA"
            if getattr(r, "holes", None):
                # PAC-1 T07, E03, R19-P1-04: Compound path with evenodd fill rule for room holes
                d_parts = []
                shell_pts = " ".join(f"{p[0]:.2f} {p[1]:.2f}" for p in r.points)
                d_parts.append(f"M {shell_pts} Z")
                for hole in r.holes:
                    hole_pts = " ".join(f"{p[0]:.2f} {p[1]:.2f}" for p in hole)
                    d_parts.append(f"M {hole_pts} Z")
                d_str = " ".join(d_parts)
                svg_lines.append(f'    <path class="room" fill-rule="evenodd" d="{d_str}" fill="{floor_color}"/>')
            else:
                pts_str = " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in r.points)
                svg_lines.append(f'    <polygon class="room" points="{pts_str}" fill="{floor_color}"/>')
            cx, cy = r.centroid
            safe_name = saxutils.escape(r.name)
            svg_lines.append(f'    <text class="room-label" x="{cx:.1f}" y="{cy - 8:.1f}">{safe_name}</text>')
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
        safe_name = saxutils.escape(f.name)
        svg_lines.append(
            f'    <g transform="translate({f.x_cm:.1f}, {f.y_cm:.1f}) rotate({f.angle_deg:.1f})">'
        )
        svg_lines.append(
            f'      <rect class="furniture" x="{-w/2.0:.1f}" y="{-d/2.0:.1f}" width="{w:.1f}" height="{d:.1f}" rx="4"/>'
        )
        svg_lines.append(
            f'      <text class="furniture-label" x="0" y="4">{safe_name}</text>'
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
            # Sweep matches point_at_offset: 0 for positive arc_extent (+Y bulge), 1 for negative
            sweep = 0 if w.arc_extent > 0 else 1
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
        geom = op.get_effective_geometry(wall)
        if not geom:
            continue

        p1 = geom["p1"]
        p2 = geom["p2"]
        w = geom["width"]
        ux, uy = geom["ux"], geom["uy"]
        nx, ny = geom["nx"], geom["ny"]
        hinge = geom["hinge"]
        latch = geom["latch"]
        p_open = geom["open_endpoint"]

        if wall:
            # Punch opening gap in wall
            svg_lines.append(
                f'    <line stroke="#ffffff" stroke-width="{wall.thickness + 2:.1f}" '
                f'x1="{p1[0]:.1f}" y1="{p1[1]:.1f}" x2="{p2[0]:.1f}" y2="{p2[1]:.1f}"/>'
            )

        if op.opening_type == "door":
            if op.swing == "sliding":
                mid_x = (p1[0] + p2[0]) / 2.0
                mid_y = (p1[1] + p2[1]) / 2.0
                svg_lines.append(
                    f'    <line class="door" x1="{p1[0]:.1f}" y1="{p1[1]:.1f}" x2="{mid_x + 5 * ux:.1f}" y2="{mid_y + 5 * uy:.1f}"/>'
                )
                svg_lines.append(
                    f'    <line class="door" stroke-dasharray="4,2" x1="{mid_x - 5 * ux + 3 * nx:.1f}" y1="{mid_y - 5 * uy + 3 * ny:.1f}" x2="{p2[0] + 3 * nx:.1f}" y2="{p2[1] + 3 * ny:.1f}"/>'
                )
            elif op.swing == "none":
                if not wall:
                    svg_lines.append(
                        f'    <line stroke="#aaaaaa" stroke-dasharray="3,3" stroke-width="2" x1="{p1[0]:.1f}" y1="{p1[1]:.1f}" x2="{p2[0]:.1f}" y2="{p2[1]:.1f}"/>'
                    )
            elif op.swing == "left":
                svg_lines.append(
                    f'    <line class="door" x1="{hinge[0]:.1f}" y1="{hinge[1]:.1f}" x2="{p_open[0]:.1f}" y2="{p_open[1]:.1f}"/>'
                )
                svg_lines.append(
                    f'    <path class="door-arc" d="M {latch[0]:.1f} {latch[1]:.1f} A {w:.1f} {w:.1f} 0 0 0 {p_open[0]:.1f} {p_open[1]:.1f}"/>'
                )
            else:
                # Default right swing
                svg_lines.append(
                    f'    <line class="door" x1="{hinge[0]:.1f}" y1="{hinge[1]:.1f}" x2="{p_open[0]:.1f}" y2="{p_open[1]:.1f}"/>'
                )
                svg_lines.append(
                    f'    <path class="door-arc" d="M {latch[0]:.1f} {latch[1]:.1f} A {w:.1f} {w:.1f} 0 0 1 {p_open[0]:.1f} {p_open[1]:.1f}"/>'
                )
        else:
            # Window glass line
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

    # Layer 6: Title Block
    scoped_area = sum(r.area_m2 for r in rooms) if level_id else model.total_area_m2
    scoped_wall_len = sum(w.length_m for w in walls) if level_id else model.total_wall_length_m

    svg_lines.extend([
        f'  </g>',
        f'',
        f'  <!-- Layer 6: Title Block -->',
        f'  <g id="layer-title-block">',
        f'    <text class="title-block" x="{min_x + 30:.1f}" y="{min_y + 40:.1f}">{saxutils.escape(model.name)}{saxutils.escape(level_title)}</text>',
        f'    <text class="room-area" x="{min_x + 30:.1f}" y="{min_y + 70:.1f}" text-anchor="start">Total Area: {scoped_area:.2f} m² | Walls: {scoped_wall_len:.1f} m</text>',
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

    # Walls as Lines or Curved Polylines
    for w in walls:
        if abs(w.arc_extent) > 1e-4 and w.length > 0:
            steps = w.get_curve_sampling_steps()
            pts = [w.point_at_offset(i * w.length / steps) for i in range(steps + 1)]
            dxf.extend(["0", "POLYLINE", "8", "WALLS", "66", "1", "70", "0"])
            for p in pts:
                dxf.extend(["0", "VERTEX", "8", "WALLS", "10", f"{p[0]:.6f}", "20", f"{p[1]:.6f}", "30", "0.0"])
            dxf.extend(["0", "SEQEND"])
        else:
            dxf.extend([
                "0", "LINE", "8", "WALLS",
                "10", f"{w.x_start:.2f}", "20", f"{w.y_start:.2f}", "30", "0.0",
                "11", f"{w.x_end:.2f}", "21", f"{w.y_end:.2f}", "31", "0.0",
            ])

    # Openings as Lines on DOORS Layer (PAC-1 E02, E06, R10-P1-06)
    wall_ids = {w.id for w in walls}
    openings = [
        op for op in model.openings.values()
        if op.wall_id in wall_ids or (not op.wall_id and (not level_id or op.level_id == level_id))
    ]
    for op in openings:
        w_host = model.walls.get(op.wall_id)
        geom = op.get_effective_geometry(w_host)
        if not geom:
            continue
        p1 = geom["p1"]
        p2 = geom["p2"]
        if op.opening_type == "door" and op.swing in ("left", "right"):
            hinge = geom["hinge"]
            p_open = geom["open_endpoint"]
            dxf.extend([
                "0", "LINE", "8", "DOORS",
                "10", f"{hinge[0]:.2f}", "20", f"{hinge[1]:.2f}", "30", "0.0",
                "11", f"{p_open[0]:.2f}", "21", f"{p_open[1]:.2f}", "31", "0.0",
            ])
            dxf.extend([
                "0", "LINE", "8", "DOORS",
                "10", f"{p1[0]:.2f}", "20", f"{p1[1]:.2f}", "30", "0.0",
                "11", f"{p2[0]:.2f}", "21", f"{p2[1]:.2f}", "31", "0.0",
            ])
        elif op.opening_type == "door" and op.swing == "sliding":
            dxf.extend([
                "0", "LINE", "8", "DOORS",
                "10", f"{p1[0]:.2f}", "20", f"{p1[1]:.2f}", "30", "0.0",
                "11", f"{p2[0]:.2f}", "21", f"{p2[1]:.2f}", "31", "0.0",
            ])
        else:
            dxf.extend([
                "0", "LINE", "8", "DOORS",
                "10", f"{p1[0]:.2f}", "20", f"{p1[1]:.2f}", "30", "0.0",
                "11", f"{p2[0]:.2f}", "21", f"{p2[1]:.2f}", "31", "0.0",
            ])

    # Rooms as Polylines + Text labels
    for r in rooms:
        if len(r.points) >= 3:
            dxf.extend(["0", "POLYLINE", "8", "ROOMS", "66", "1", "70", "1"])
            for p in r.points:
                dxf.extend(["0", "VERTEX", "8", "ROOMS", "10", f"{p[0]:.2f}", "20", f"{p[1]:.2f}", "30", "0.0"])
            dxf.extend(["0", "SEQEND"])
            # PAC-1 T07, E03, R20-P1-04: Emit room holes as closed polylines on layer ROOMS
            if getattr(r, "holes", None):
                for h in r.holes:
                    if len(h) >= 3:
                        dxf.extend(["0", "POLYLINE", "8", "ROOMS", "66", "1", "70", "1"])
                        for p in h:
                            dxf.extend(["0", "VERTEX", "8", "ROOMS", "10", f"{p[0]:.2f}", "20", f"{p[1]:.2f}", "30", "0.0"])
                        dxf.extend(["0", "SEQEND"])
            cx, cy = r.centroid
            dxf.extend([
                "0", "TEXT", "8", "ROOMS",
                "10", f"{cx:.2f}", "20", f"{cy:.2f}", "30", "0.0",
                "40", "20.0", "1", f"{r.name} ({r.area_m2:.1f} m2)",
            ])

    # Furniture as Footprint Polyline + Text
    for f in furniture:
        corners = _get_furn_corners(f)
        dxf.extend(["0", "POLYLINE", "8", "FURNITURE", "66", "1", "70", "1"])
        for p in corners:
            dxf.extend(["0", "VERTEX", "8", "FURNITURE", "10", f"{p[0]:.2f}", "20", f"{p[1]:.2f}", "30", "0.0"])
        dxf.extend(["0", "SEQEND"])
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
        wall_ids = {w["id"] for w in d["walls"]}
        d["rooms"] = [r for r in d["rooms"] if r.get("level_id") == level_id]
        d["openings"] = [
            op for op in d["openings"]
            if (op.get("wall_id") in wall_ids) or (not op.get("wall_id") and op.get("level_id") == level_id)
        ]
        d["furniture"] = [f for f in d["furniture"] if f.get("level_id") == level_id]
        d["dimensions"] = [dim for dim in d["dimensions"] if dim.get("level_id") == level_id]
        d["summary"] = {
            "total_area_m2": round(sum(r.get("area_m2", 0.0) for r in d["rooms"]), 2),
            "total_wall_length_m": round(sum(w.get("length_m", 0.0) for w in d["walls"]), 2),
            "level_id": level_id,
        }
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
    safe_title = html.escape(model.name)
    svg_clean = re.sub(r'<\?xml.*?\?>', '', svg_xml, flags=re.DOTALL).strip()

    if level_id:
        scoped_rooms = [r for r in model.rooms.values() if r.level_id == level_id]
        scoped_walls = [w for w in model.walls.values() if w.level_id == level_id]
        scoped_wall_ids = {w.id for w in scoped_walls}
        scoped_openings = [
            op for op in model.openings.values()
            if op.wall_id in scoped_wall_ids or (not op.wall_id and op.level_id == level_id)
        ]
        scoped_furniture = [f for f in model.furniture.values() if f.level_id == level_id]
        total_floor_area = sum(r.area_m2 for r in scoped_rooms)
        total_wall_len = sum(w.length_m for w in scoped_walls)
        gross_wall_m2 = sum((w.length * w.height) / 10000.0 for w in scoped_walls)
        openings_m2 = sum((op.width_cm * op.height_cm) / 10000.0 for op in scoped_openings)
        net_wall_m2 = max(0.0, gross_wall_m2 - openings_m2)
        total_furniture_items = len(scoped_furniture)
    else:
        total_floor_area = takeoff_data['floor_and_ceiling']['total_floor_area_m2']
        total_wall_len = takeoff_data['masonry_and_walls']['total_wall_length_m']
        net_wall_m2 = takeoff_data['masonry_and_walls']['net_masonry_wall_m2']
        total_furniture_items = takeoff_data['furniture_schedule']['total_items']

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_title} - Architectural Floor Plan Viewer</title>
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
    <h1>{safe_title}</h1>
    <div class="meta">Sweet Home 3D Agent Harness Export</div>

    <div class="stat-grid takeoff-panel">
      <div class="stat-card">
        <div class="stat-val">{total_floor_area:.2f} m²</div>
        <div class="stat-lbl">Total Floor Area</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">{total_wall_len:.1f} m</div>
        <div class="stat-lbl">Linear Wall Run</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">{net_wall_m2:.1f} m²</div>
        <div class="stat-lbl">Net Masonry Wall</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">{total_furniture_items}</div>
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
      {svg_clean}
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
        f.write(html_content)

    return filepath

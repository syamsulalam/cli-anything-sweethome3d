"""Directed wall and room face topology for Sweet Home 3D models (PAC-1 T01-T06)."""
from __future__ import annotations

import hashlib
import json
import math


def build_room_face_topology(model):
    """Calculates atomic physical straight wall edges and two-sided room face ownership.

    Returns a pure derived JSON-safe topology view:
    - Atomizes straight wall paths and room boundaries via unary union.
    - Resolves two-sided face ownership (positive and negative normals) based on
      algebraic room boundary winding and edge tangents.
    - Associates covering wall sources with signed station projections.
    - Strictly rejects curved walls (requiring analytic arrangement slice),
      overlapping room interiors (ROOM_OWNERSHIP_CONFLICT), competing same-side
      room ownership (ROOM_OWNERSHIP_CONFLICT), and mismatched overlapping wall
      assemblies (WALL_ASSEMBLY_CONFLICT).
    """
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union
    numeric_eps = 1e-7  # numerical comparison only; NO 0.01cm silent snap
    records = []
    levels = {level.id for level in model.levels}
    if any(entity.level_id not in levels for entity in [*model.walls.values(), *model.rooms.values()]):
        raise ValueError("MISSING_LEVEL_REFERENCE")
    for level in model.levels:
        walls = sorted((w for w in model.walls.values() if w.level_id == level.id),
                       key=lambda w: w.id)
        if any(w.arc_extent != 0.0 for w in walls):
            raise ValueError("UNSUPPORTED_CURVED_FACE_TOPOLOGY: use analytic arrangement slice")
        paths = []
        for w in walls:
            values = [w.x1, w.y1, w.x2, w.y2, w.thickness, w.height]
            if not all(math.isfinite(v) for v in values) or min(w.thickness, w.height) <= 0:
                raise ValueError("Invalid wall geometry")
            line = LineString([(w.x1, w.y1), (w.x2, w.y2)])
            if line.length <= numeric_eps:
                raise ValueError("Zero-length wall")
            paths.append((w, line))
        rooms = sorted((r for r in model.rooms.values() if r.level_id == level.id),
                       key=lambda r: r.id)
        regions = [(r, r.get_region_polygon()) for r in rooms]
        for i, (_, region) in enumerate(regions):
            for _, other in regions[i + 1:]:
                if region.intersection(other).area > numeric_eps ** 2:
                    raise ValueError("ROOM_OWNERSHIP_CONFLICT: overlapping room interiors")
        rings = []
        for room, _ in regions:
            for hole, points in [(False, room.points)] + [(True, p) for p in (room.holes or [])]:
                pairs = list(zip(points, points[1:] + points[:1]))
                area2 = sum(a[0] * b[1] - b[0] * a[1] for a, b in pairs)
                sign = (1 if area2 > 0 else -1) * (-1 if hole else 1)
                for a, b in pairs:
                    if a != b:
                        rings.append((room.id, sign, tuple(a), tuple(b), LineString([a, b])))
        if not paths:
            continue
        # Room boundary endpoints split partially owned physical intervals too.
        union = unary_union([p for _, p in paths] + [r[4] for r in rings])
        geometries = list(union.geoms) if hasattr(union, "geoms") else [union]
        atoms = set()
        for line in geometries:
            coords = list(line.coords)
            for a, b in zip(coords, coords[1:]):
                if math.dist(a, b) > numeric_eps:
                    atoms.add(tuple(sorted((tuple(a), tuple(b)))))
        for a, b in sorted(atoms):
            atom = LineString([a, b])
            length = atom.length
            covering = [(w, line) for w, line in paths
                        if line.distance(Point(a)) <= numeric_eps
                        and line.distance(Point(b)) <= numeric_eps
                        and line.intersection(atom).length >= length - numeric_eps]
            if not covering:
                continue  # room boundary with no physical wall is not a wall atom
            if any(abs(w.thickness - covering[0][0].thickness) > numeric_eps or
                   abs(w.height - covering[0][0].height) > numeric_eps for w, _ in covering):
                raise ValueError("WALL_ASSEMBLY_CONFLICT")
            owners = {"positive": None, "negative": None}
            for room_id, sign, ra, rb, boundary in rings:
                if boundary.intersection(atom).length < length - numeric_eps:
                    continue
                dot = (b[0] - a[0]) * (rb[0] - ra[0]) + (b[1] - a[1]) * (rb[1] - ra[1])
                side = "positive" if sign * dot > 0 else "negative"
                if owners[side] not in (None, room_id):
                    raise ValueError("ROOM_OWNERSHIP_CONFLICT: physical side already owned")
                owners[side] = room_id
            key = json.dumps([level.id, a, b], separators=(",", ":"))
            atom_id = "edge_" + hashlib.sha256(key.encode()).hexdigest()[:24]
            records.append({
                "id": atom_id, "level_id": level.id, "start": list(a), "end": list(b),
                "length_cm": length,
                "faces": {side: {"id": atom_id + ":" + side, "room_id": owner,
                                  "adjacency_kind": "room" if owner else "unassigned"}
                          for side, owner in owners.items()},
                "sources": [{"wall_id": w.id, "s_start_cm": line.project(Point(a)),
                             "s_end_cm": line.project(Point(b))} for w, line in covering],
            })
    boundaries = {r.id: [] for r in model.rooms.values()}
    for edge in records:
        for side, face in edge["faces"].items():
            if face["room_id"] is not None:
                boundaries[face["room_id"]].append({"edge_id": edge["id"], "side": side,
                                                    "s0_cm": 0.0, "s1_cm": edge["length_cm"]})
    return {"schema_version": 1, "scope": "derived-straight-room-faces",
            "edges": records, "room_boundaries": boundaries}

def compute_line_circle_intersections(p1, p2, cx, cy, r, arc_extent_deg, phi1_rad):
    """Analytic intersection points between a straight line segment and a circular arc (PAC-1 T03, T05).

    Returns list of dicts: {'point': (x, y), 't_line': float, 's_line_cm': float,
                            'phi_arc': float, 's_arc_cm': float, 'tangent': bool}
    """
    x1, y1 = p1
    x2, y2 = p2
    dx = x2 - x1
    dy = y2 - y1
    line_len = math.hypot(dx, dy)
    if line_len < 1e-7:
        return []

    fx = x1 - cx
    fy = y1 - cy
    a = dx * dx + dy * dy
    b = 2.0 * (fx * dx + fy * dy)
    c = fx * fx + fy * fy - r * r

    disc = b * b - 4.0 * a * c
    if disc < -1e-7:
        return []

    roots = []
    if abs(disc) <= 1e-7:
        roots.append((-b / (2.0 * a), True))
    else:
        sqrt_d = math.sqrt(disc)
        roots.append(((-b - sqrt_d) / (2.0 * a), False))
        roots.append(((-b + sqrt_d) / (2.0 * a), False))

    theta_rad = math.radians(arc_extent_deg)
    phi2_rad = phi1_rad - theta_rad
    min_phi = min(phi1_rad, phi2_rad) - 1e-7
    max_phi = max(phi1_rad, phi2_rad) + 1e-7

    intersections = []
    for t, is_tangent in roots:
        if -1e-7 <= t <= 1.0 + 1e-7:
            t_clamped = max(0.0, min(1.0, t))
            ix = x1 + t_clamped * dx
            iy = y1 + t_clamped * dy
            phi = math.atan2(iy - cy, ix - cx)
            in_arc = False
            matched_phi = phi
            for k in (-2, -1, 0, 1, 2):
                cand = phi + k * 2.0 * math.pi
                if min_phi <= cand <= max_phi:
                    in_arc = True
                    matched_phi = cand
                    break
            if in_arc:
                s_line = t_clamped * line_len
                d_phi = abs(matched_phi - phi1_rad)
                s_arc = r * d_phi
                intersections.append({
                    "point": (ix, iy),
                    "t_line": t_clamped,
                    "s_line_cm": s_line,
                    "phi_arc": matched_phi,
                    "s_arc_cm": s_arc,
                    "tangent": is_tangent,
                })
    return intersections


def compute_circle_circle_intersections(c1, r1, theta1_deg, phi1_rad,
                                        c2, r2, theta2_deg, phi2_rad):
    """Analytic intersection points between two circular arcs (PAC-1 T05).

    Returns list of dicts: {'point': (x, y), 's_arc1_cm': float, 's_arc2_cm': float, 'tangent': bool}
    """
    c1x, c1y = c1
    c2x, c2y = c2
    dx = c2x - c1x
    dy = c2y - c1y
    d = math.hypot(dx, dy)
    if d > r1 + r2 + 1e-7 or d < abs(r1 - r2) - 1e-7 or d < 1e-7:
        return []

    is_tangent = abs(d - (r1 + r2)) <= 1e-6 or abs(d - abs(r1 - r2)) <= 1e-6
    a = (r1 * r1 - r2 * r2 + d * d) / (2.0 * d)
    h_sq = r1 * r1 - a * a
    h = math.sqrt(max(0.0, h_sq))

    p0x = c1x + a * (dx / d)
    p0y = c1y + a * (dy / d)

    points = []
    if h <= 1e-7 or is_tangent:
        points.append(((p0x, p0y), True))
    else:
        rx = -(dy / d) * h
        ry = (dx / d) * h
        points.append(((p0x + rx, p0y + ry), False))
        points.append(((p0x - rx, p0y - ry), False))

    theta1_rad = math.radians(theta1_deg)
    phi1_end = phi1_rad - theta1_rad
    min1 = min(phi1_rad, phi1_end) - 1e-7
    max1 = max(phi1_rad, phi1_end) + 1e-7

    theta2_rad = math.radians(theta2_deg)
    phi2_end = phi2_rad - theta2_rad
    min2 = min(phi2_rad, phi2_end) - 1e-7
    max2 = max(phi2_rad, phi2_end) + 1e-7

    valid_pts = []
    for (px, py), tangent_flag in points:
        phi_a = math.atan2(py - c1y, px - c1x)
        phi_b = math.atan2(py - c2y, px - c2x)
        in1, in2 = False, False
        m_phi1, m_phi2 = phi_a, phi_b
        for k in (-2, -1, 0, 1, 2):
            if min1 <= phi_a + k * 2.0 * math.pi <= max1:
                in1 = True
                m_phi1 = phi_a + k * 2.0 * math.pi
                break
        for k in (-2, -1, 0, 1, 2):
            if min2 <= phi_b + k * 2.0 * math.pi <= max2:
                in2 = True
                m_phi2 = phi_b + k * 2.0 * math.pi
                break
        if in1 and in2:
            s_arc1 = r1 * abs(m_phi1 - phi1_rad)
            s_arc2 = r2 * abs(m_phi2 - phi2_rad)
            valid_pts.append({
                "point": (px, py),
                "s_arc1_cm": s_arc1,
                "s_arc2_cm": s_arc2,
                "tangent": tangent_flag,
            })
    return valid_pts


def compute_wall_analytic_stations(walls):
    """Computes exact analytic stations and intersection topology across straight and curved walls (PAC-1 T03, T05).

    Returns a mapping of wall_id -> list of sorted station records along the wall.
    """
    wall_list = list(walls.values()) if hasattr(walls, "values") else list(walls)
    stations_by_wall = {w.id: [] for w in wall_list}

    # Internal wall metadata cache
    geom = {}
    for w in wall_list:
        is_curved = abs(w.arc_extent) >= 1e-4
        if is_curved:
            c = w.chord_length
            theta_rad = math.radians(w.arc_extent)
            sin_half = math.sin(abs(theta_rad) / 2.0)
            r = c / (2.0 * sin_half) if abs(sin_half) > 1e-6 else c
            h = r * math.cos(abs(theta_rad) / 2.0)
            mx = (w.x_start + w.x_end) / 2.0
            my = (w.y_start + w.y_end) / 2.0
            nx = -(w.y_end - w.y_start) / c
            ny = (w.x_end - w.x_start) / c
            sign = 1.0 if w.arc_extent > 0 else -1.0
            cx = mx - sign * h * nx
            cy = my - sign * h * ny
            phi1 = math.atan2(w.y_start - cy, w.x_start - cx)
            geom[w.id] = {"curved": True, "c": (cx, cy), "r": r, "theta_deg": w.arc_extent,
                          "phi1": phi1, "len": w.length}
        else:
            geom[w.id] = {"curved": False, "p1": (w.x_start, w.y_start),
                          "p2": (w.x_end, w.y_end), "len": w.length}

    # Always record start and end stations
    for w in wall_list:
        stations_by_wall[w.id].append({"station_cm": 0.0, "point": (w.x_start, w.y_start), "kind": "endpoint", "peer_id": None})
        stations_by_wall[w.id].append({"station_cm": w.length, "point": (w.x_end, w.y_end), "kind": "endpoint", "peer_id": None})

    # Pairwise intersections
    for i, w1 in enumerate(wall_list):
        g1 = geom[w1.id]
        for w2 in wall_list[i + 1:]:
            g2 = geom[w2.id]
            if w1.level_id != w2.level_id:
                continue

            if not g1["curved"] and not g2["curved"]:
                # Line-line intersection
                x1, y1 = g1["p1"]
                x2, y2 = g1["p2"]
                x3, y3 = g2["p1"]
                x4, y4 = g2["p2"]
                denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
                if abs(denom) > 1e-7:
                    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
                    u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denom
                    if -1e-7 <= t <= 1.0 + 1e-7 and -1e-7 <= u <= 1.0 + 1e-7:
                        t_c = max(0.0, min(1.0, t))
                        u_c = max(0.0, min(1.0, u))
                        ix = x1 + t_c * (x2 - x1)
                        iy = y1 + t_c * (y2 - y1)
                        s1 = t_c * g1["len"]
                        s2 = u_c * g2["len"]
                        stations_by_wall[w1.id].append({"station_cm": s1, "point": (ix, iy), "kind": "line_line", "peer_id": w2.id})
                        stations_by_wall[w2.id].append({"station_cm": s2, "point": (ix, iy), "kind": "line_line", "peer_id": w1.id})
                else:
                    # Parallel or collinear check
                    dx1 = x2 - x1
                    dy1 = y2 - y1
                    L1 = g1["len"]
                    u1x, u1y = dx1 / L1, dy1 / L1
                    dist_to_line = abs((x3 - x1) * u1y - (y3 - y1) * u1x)
                    if dist_to_line < 1e-4:
                        # Collinear! Project w2 endpoints onto w1
                        s_b1 = (x3 - x1) * u1x + (y3 - y1) * u1y
                        s_b2 = (x4 - x1) * u1x + (y4 - y1) * u1y
                        min_b, max_b = min(s_b1, s_b2), max(s_b1, s_b2)
                        overlap_start = max(0.0, min_b)
                        overlap_end = min(L1, max_b)
                        if overlap_end - overlap_start > 1e-4:
                            for s in (overlap_start, overlap_end):
                                pt = (x1 + s * u1x, y1 + s * u1y)
                                stations_by_wall[w1.id].append({
                                    "station_cm": s, "point": pt,
                                    "kind": "collinear_overlap", "peer_id": w2.id
                                })

                        # Project w1 endpoints onto w2
                        dx2 = x4 - x3
                        dy2 = y4 - y3
                        L2 = g2["len"]
                        u2x, u2y = dx2 / L2, dy2 / L2
                        s_a1 = (x1 - x3) * u2x + (y1 - y3) * u2y
                        s_a2 = (x2 - x3) * u2x + (y2 - y3) * u2y
                        min_a, max_a = min(s_a1, s_a2), max(s_a1, s_a2)
                        overlap_start_2 = max(0.0, min_a)
                        overlap_end_2 = min(L2, max_a)
                        if overlap_end_2 - overlap_start_2 > 1e-4:
                            for s in (overlap_start_2, overlap_end_2):
                                pt = (x3 + s * u2x, y3 + s * u2y)
                                stations_by_wall[w2.id].append({
                                    "station_cm": s, "point": pt,
                                    "kind": "collinear_overlap", "peer_id": w1.id
                                })

            elif not g1["curved"] and g2["curved"]:
                # Line-arc intersection
                hits = compute_line_circle_intersections(
                    g1["p1"], g1["p2"], g2["c"][0], g2["c"][1], g2["r"],
                    g2["theta_deg"], g2["phi1"]
                )
                for h in hits:
                    stations_by_wall[w1.id].append({"station_cm": h["s_line_cm"], "point": h["point"],
                                                    "kind": "tangent" if h["tangent"] else "secant", "peer_id": w2.id})
                    stations_by_wall[w2.id].append({"station_cm": h["s_arc_cm"], "point": h["point"],
                                                    "kind": "tangent" if h["tangent"] else "secant", "peer_id": w1.id})

            elif g1["curved"] and not g2["curved"]:
                # Arc-line intersection
                hits = compute_line_circle_intersections(
                    g2["p1"], g2["p2"], g1["c"][0], g1["c"][1], g1["r"],
                    g1["theta_deg"], g1["phi1"]
                )
                for h in hits:
                    stations_by_wall[w1.id].append({"station_cm": h["s_arc_cm"], "point": h["point"],
                                                    "kind": "tangent" if h["tangent"] else "secant", "peer_id": w2.id})
                    stations_by_wall[w2.id].append({"station_cm": h["s_line_cm"], "point": h["point"],
                                                    "kind": "tangent" if h["tangent"] else "secant", "peer_id": w1.id})

            else:
                # Arc-arc intersection
                c_dx = g2["c"][0] - g1["c"][0]
                c_dy = g2["c"][1] - g1["c"][1]
                c_dist = math.hypot(c_dx, c_dy)
                if c_dist < 1e-4 and abs(g1["r"] - g2["r"]) < 1e-4:
                    # Concentric / coincident arcs (PAC-1 T05, R30 physical error budget, R31 reciprocal)
                    def station_on_arc(pt, gp):
                        phi = math.atan2(pt[1] - gp["c"][1], pt[0] - gp["c"][0])
                        theta_rad = math.radians(gp["theta_deg"])
                        delta = (gp["phi1"] - phi) if theta_rad > 0 else (phi - gp["phi1"])
                        delta = delta % (2.0 * math.pi)
                        s = gp["r"] * delta
                        L = gp["len"]
                        if abs(s - 2.0 * math.pi * gp["r"]) < 0.005:
                            s = 0.0
                        if abs(s) < 0.005:
                            return 0.0
                        elif abs(s - L) < 0.005:
                            return L
                        elif 0.005 <= s <= L - 0.005:
                            return s
                        return None

                    def append_pair(i, si, j, sj, pt, kind):
                        stations_by_wall[i].append({"station_cm": si, "point": pt, "kind": kind, "peer_id": j})
                        stations_by_wall[j].append({"station_cm": sj, "point": pt, "kind": kind, "peer_id": i})

                    for source, peer, gs, gp in ((w1, w2, g1, g2), (w2, w1, g2, g1)):
                        for si, pt in ((0.0, (source.x_start, source.y_start)), (gs["len"], (source.x_end, source.y_end))):
                            sj = station_on_arc(pt, gp)
                            if sj is not None:
                                append_pair(source.id, si, peer.id, sj, pt, "coincident_arc_overlap")
                else:
                    hits = compute_circle_circle_intersections(
                        g1["c"], g1["r"], g1["theta_deg"], g1["phi1"],
                        g2["c"], g2["r"], g2["theta_deg"], g2["phi1"]
                    )
                    for h in hits:
                        stations_by_wall[w1.id].append({"station_cm": h["s_arc1_cm"], "point": h["point"],
                                                        "kind": "tangent" if h["tangent"] else "secant", "peer_id": w2.id})
                        stations_by_wall[w2.id].append({"station_cm": h["s_arc2_cm"], "point": h["point"],
                                                        "kind": "tangent" if h["tangent"] else "secant", "peer_id": w1.id})

    # Deduplicate and sort stations along each wall, preserving all multiway peer relationships
    for wid in stations_by_wall:
        unique = []
        seen = {}
        for rec in stations_by_wall[wid]:
            s_round = round(rec["station_cm"], 4)
            if s_round not in seen:
                seen[s_round] = rec
                rec["peer_ids"] = [rec["peer_id"]] if rec.get("peer_id") else []
                unique.append(rec)
            else:
                existing = seen[s_round]
                if "peer_ids" not in existing:
                    existing["peer_ids"] = [existing["peer_id"]] if existing.get("peer_id") else []
                if rec.get("peer_id") and rec["peer_id"] not in existing["peer_ids"]:
                    existing["peer_ids"].append(rec["peer_id"])
                    existing["peer_ids"].sort()
        unique.sort(key=lambda r: r["station_cm"])
        stations_by_wall[wid] = unique

    return stations_by_wall

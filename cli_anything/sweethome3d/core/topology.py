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

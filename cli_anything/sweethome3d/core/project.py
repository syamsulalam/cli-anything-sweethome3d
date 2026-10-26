import os
import math
import zipfile
import base64
import xml.etree.ElementTree as ET
from xml.dom import minidom
from collections import Counter

from cli_anything.sweethome3d.core.wall import Wall
from cli_anything.sweethome3d.core.room import Room
from cli_anything.sweethome3d.core.opening import Opening
from cli_anything.sweethome3d.core.furniture import Furniture
from cli_anything.sweethome3d.core.dimension import DimensionLine

class Level:
    def __init__(self, level_id="level1", name="Ground Floor", elevation_cm=0.0, height_cm=280.0,
                 floor_thickness_cm=12.0, extra_attribs=None, extra_children=None):
        self.id = str(level_id)
        self.name = str(name)
        self.elevation_cm = float(elevation_cm)
        self.height_cm = float(height_cm)
        self.floor_thickness_cm = float(floor_thickness_cm)
        self.extra_attribs = dict(extra_attribs) if extra_attribs else {}
        self.extra_children = list(extra_children) if extra_children else []

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "elevation_cm": self.elevation_cm,
            "height_cm": self.height_cm,
            "floor_thickness_cm": self.floor_thickness_cm,
            "extra_attribs": self.extra_attribs,
            "extra_children": self.extra_children,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            level_id=d.get("id", "level1"),
            name=d.get("name", "Ground Floor"),
            elevation_cm=d.get("elevation_cm", 0.0),
            height_cm=d.get("height_cm", 280.0),
            floor_thickness_cm=d.get("floor_thickness_cm", 12.0),
            extra_attribs=d.get("extra_attribs"),
            extra_children=d.get("extra_children"),
        )

class HomeModel:
    """Core in-memory representation of a Sweet Home 3D project."""

    def __init__(self, name="New Home", wall_height=280.0, version="7.5"):
        self.name = str(name)
        self.wall_height = float(wall_height)
        self.version = str(version)
        self.levels = [Level("level1", "Ground Floor", 0.0, wall_height)]
        self.walls = {}
        self.rooms = {}
        self.openings = {}
        self.furniture = {}
        self.dimensions = {}
        self.extra_root_attribs = {}
        self.extra_elements = []
        self.extra_zip_entries = {}

    # Level methods
    def add_level(self, level_id, name, elevation_cm=0.0, height_cm=280.0, floor_thickness_cm=12.0,
                  extra_attribs=None, extra_children=None):
        level_id = str(level_id)
        if self.get_level(level_id):
            raise ValueError(f"Level '{level_id}' already exists.")
        lvl = Level(level_id, name, elevation_cm, height_cm, floor_thickness_cm, extra_attribs, extra_children)
        self.levels.append(lvl)
        return lvl

    def get_level(self, level_id):
        for lvl in self.levels:
            if lvl.id == level_id:
                return lvl
        return None

    # Wall methods
    def add_wall(self, wall_id, x_start, y_start, x_end, y_end, thickness=15.0, height=None,
                 level_id="level1", arc_extent=0.0, extra_attribs=None, extra_children=None, replace=False):
        wall_id = str(wall_id)
        if wall_id in self.walls and not replace:
            raise ValueError(f"Wall with ID '{wall_id}' already exists in project.")
        h = height if height is not None else self.wall_height
        w = Wall(wall_id, x_start, y_start, x_end, y_end, thickness, h, level_id, arc_extent,
                 extra_attribs, extra_children)
        self.walls[w.id] = w
        return w

    def add_wall_loop(self, *args, **kwargs):
        """Constructs a continuous sequence of connected wall segments from a list of (x,y) points.
        
        Supports calling as:
          add_wall_loop(points, prefix="w_")
          add_wall_loop(id_prefix, points)
        """
        id_prefix = "w_"
        points = []

        if len(args) == 1:
            if isinstance(args[0], (list, tuple)):
                points = args[0]
            elif isinstance(args[0], str):
                id_prefix = args[0]
        elif len(args) >= 2:
            if isinstance(args[0], str) and isinstance(args[1], (list, tuple)):
                id_prefix = args[0]
                points = args[1]
            elif isinstance(args[0], (list, tuple)):
                points = args[0]
                if isinstance(args[1], str):
                    id_prefix = args[1]

        if "points" in kwargs:
            points = kwargs["points"]
        if "prefix" in kwargs:
            id_prefix = kwargs["prefix"]
        elif "id_prefix" in kwargs:
            id_prefix = kwargs["id_prefix"]

        closed = kwargs.get("closed", True)
        thickness = kwargs.get("thickness", 15.0)
        height = kwargs.get("height", None)
        level_id = kwargs.get("level_id", "level1")
        arc_extent = kwargs.get("arc_extent", 0.0)

        pts = [(float(p[0]), float(p[1])) for p in points]
        if len(pts) < 2:
            raise ValueError("Wall loop requires at least 2 points.")
        
        sep = "" if id_prefix.endswith("_") else "_"
        created = []
        n = len(pts)
        num_segments = n if closed else n - 1
        for i in range(num_segments):
            p1 = pts[i]
            p2 = pts[(i + 1) % n]
            wid = f"{id_prefix}{sep}{i + 1}"
            w = self.add_wall(wid, p1[0], p1[1], p2[0], p2[1], thickness, height, level_id, arc_extent)
            created.append(w)
        return created

    def remove_wall(self, wall_id):
        if wall_id in self.walls:
            to_remove = [oid for oid, op in self.openings.items() if op.wall_id == wall_id]
            for oid in to_remove:
                del self.openings[oid]
            del self.walls[wall_id]
            return True
        return False

    # Room methods
    def add_room(self, room_id, name, points, level_id="level1", floor_color="#EAEAEA",
                 with_walls=False, wall_thickness=15.0, extra_attribs=None, extra_children=None, replace=False):
        """Adds an architectural room polygon, optionally generating perimeter boundary walls."""
        room_id = str(room_id)
        if room_id in self.rooms and not replace:
            raise ValueError(f"Room with ID '{room_id}' already exists in project.")
        r = Room(room_id, name, points, level_id, floor_color, extra_attribs=extra_attribs, extra_children=extra_children)
        self.rooms[r.id] = r
        if with_walls and len(r.points) >= 3:
            self.add_wall_loop(r.points, prefix=f"{room_id}_w", closed=True,
                               thickness=wall_thickness, level_id=level_id)
        return r

    def remove_room(self, room_id):
        if room_id in self.rooms:
            del self.rooms[room_id]
            return True
        return False

    # Opening methods
    def add_opening(self, opening_id, name, wall_id=None, offset_cm=50.0, width_cm=90.0, height_cm=210.0,
                    opening_type="door", swing="right", elevation_cm=0.0, depth_cm=None,
                    x_cm=None, y_cm=None, angle_deg=None, level_id="level1",
                    extra_attribs=None, extra_children=None, replace=False):
        opening_id = str(opening_id)
        if opening_id in self.openings and not replace:
            raise ValueError(f"Opening with ID '{opening_id}' already exists in project.")
        if wall_id is not None and wall_id not in self.walls:
            raise ValueError(f"Wall '{wall_id}' does not exist.")
        wall = self.walls.get(wall_id) if wall_id else None
        eff_level = wall.level_id if wall else level_id
        op = Opening(opening_id, name, wall_id, offset_cm, width_cm, height_cm, opening_type, swing,
                     elevation_cm=elevation_cm, depth_cm=depth_cm, x_cm=x_cm, y_cm=y_cm,
                     angle_deg=angle_deg, level_id=eff_level,
                     extra_attribs=extra_attribs, extra_children=extra_children)
        self.openings[op.id] = op
        return op

    def remove_opening(self, opening_id):
        if opening_id in self.openings:
            del self.openings[opening_id]
            return True
        return False

    # Furniture methods
    def add_furniture(self, furniture_id, name=None, catalog_id="custom", x_cm=0.0, y_cm=0.0,
                      elevation_cm=0.0, width_cm=None, depth_cm=None, height_cm=None,
                      angle_deg=0.0, level_id="level1", extra_attribs=None, extra_children=None, replace=False):
        furniture_id = str(furniture_id)
        if furniture_id in self.furniture and not replace:
            raise ValueError(f"Furniture with ID '{furniture_id}' already exists in project.")
        f = Furniture(furniture_id, name, catalog_id, x_cm, y_cm, elevation_cm,
                      width_cm, depth_cm, height_cm, angle_deg, level_id,
                      extra_attribs=extra_attribs, extra_children=extra_children)
        self.furniture[f.id] = f
        return f

    def remove_furniture(self, furniture_id):
        if furniture_id in self.furniture:
            del self.furniture[furniture_id]
            return True
        return False

    # Dimension methods
    def add_dimension(self, dimension_id, x_start, y_start, x_end, y_end, offset_cm=30.0,
                      level_id="level1", elevation_cm=0.0, extra_attribs=None, extra_children=None, replace=False):
        dimension_id = str(dimension_id)
        if dimension_id in self.dimensions and not replace:
            raise ValueError(f"Dimension line with ID '{dimension_id}' already exists in project.")
        dim = DimensionLine(dimension_id, x_start, y_start, x_end, y_end, offset_cm, level_id, elevation_cm,
                            extra_attribs=extra_attribs, extra_children=extra_children)
        self.dimensions[dim.id] = dim
        return dim

    def remove_dimension(self, dimension_id):
        if dimension_id in self.dimensions:
            del self.dimensions[dimension_id]
            return True
        return False

    @property
    def total_area_m2(self):
        """Sum of all room floor areas in square meters."""
        return round(sum(r.area_m2 for r in self.rooms.values()), 2)

    @property
    def total_wall_length_m(self):
        """Total run of walls in linear meters."""
        return round(sum(w.length_m for w in self.walls.values()), 2)

    def takeoff(self):
        """Generates an architectural takeoff / Bill of Quantities (BOQ / RAB) analysis."""
        # 1. Floor & Ceiling Takeoff
        total_floor_m2 = self.total_area_m2
        total_skirting_m = round(sum(r.perimeter_cm / 100.0 for r in self.rooms.values()), 2)
        ceiling_m2 = total_floor_m2

        # 2. Wall & Masonry Takeoff
        # Gross wall area = sum(length * height)
        gross_wall_m2 = round(sum((w.length * w.height) / 10000.0 for w in self.walls.values()), 2)
        
        # Openings subtraction area = sum(width * height)
        openings_m2 = round(sum((op.width_cm * op.height_cm) / 10000.0 for op in self.openings.values()), 2)
        net_wall_masonry_m2 = round(max(0.0, gross_wall_m2 - openings_m2), 2)
        interior_paint_m2 = round(net_wall_masonry_m2 * 2.0, 2)  # both sides

        # 3. Furniture inventory tally
        furn_counts = Counter(f.name for f in self.furniture.values())
        furn_catalog_counts = Counter(f.catalog_id for f in self.furniture.values())

        doors_deduction_m = sum(op.width_cm / 100.0 for op in self.openings.values() if op.opening_type == "door")
        skirting_net_m = round(max(0.0, total_skirting_m - doors_deduction_m), 2)

        summary = {
            "wall_perimeter_m": self.total_wall_length_m,
            "wall_gross_area_m2": gross_wall_m2,
            "wall_net_area_m2": net_wall_masonry_m2,
            "wall_paint_area_m2": interior_paint_m2,
            "floor_area_m2": total_floor_m2,
            "skirting_length_m": skirting_net_m,
            "dimensions_count": len(self.dimensions),
            "openings_count": len(self.openings),
            "furniture_count": len(self.furniture),
        }

        return {
            "project_name": self.name,
            "summary": summary,
            "floor_and_ceiling": {
                "total_floor_area_m2": total_floor_m2,
                "ceiling_plasterboard_m2": ceiling_m2,
                "skirting_baseboard_linear_m": total_skirting_m,
                "rooms_breakdown": [
                    {"id": r.id, "name": r.name, "area_m2": r.area_m2, "perimeter_m": round(r.perimeter_cm / 100.0, 2)}
                    for r in self.rooms.values()
                ]
            },
            "masonry_and_walls": {
                "total_wall_length_m": self.total_wall_length_m,
                "gross_wall_surface_m2": gross_wall_m2,
                "openings_deduction_m2": openings_m2,
                "net_masonry_wall_m2": net_wall_masonry_m2,
                "interior_wall_paint_m2": interior_paint_m2,
            },
            "openings_schedule": {
                "doors_count": sum(1 for op in self.openings.values() if op.opening_type == "door"),
                "windows_count": sum(1 for op in self.openings.values() if op.opening_type == "window"),
                "total_openings_count": len(self.openings),
            },
            "furniture_schedule": {
                "total_items": len(self.furniture),
                "by_name": dict(furn_counts),
                "by_catalog_id": dict(furn_catalog_counts),
            }
        }

    def validate(self):
        """Runs comprehensive architectural and spatial validation checks."""
        errors = []
        warnings = []
        findings = []

        level_ids = {lvl.id for lvl in self.levels}

        # 0. Check non-finite coordinates across all entities
        for lvl in self.levels:
            for val, name in [(lvl.elevation_cm, "elevation_cm"), (lvl.height_cm, "height_cm"), (lvl.floor_thickness_cm, "floor_thickness_cm")]:
                if not math.isfinite(val):
                    msg = f"Level '{lvl.id}' has non-finite parameter '{name}' ({val})."
                    errors.append(msg)
                    findings.append({"code": "NON_FINITE_COORDINATE", "severity": "error", "entity_ids": [lvl.id], "message": msg})

        for w in self.walls.values():
            for val, name in [(w.x_start, "x_start"), (w.y_start, "y_start"), (w.x_end, "x_end"), (w.y_end, "y_end"),
                              (w.thickness, "thickness"), (w.height, "height"), (w.arc_extent, "arc_extent")]:
                if not math.isfinite(val):
                    msg = f"Wall '{w.id}' has non-finite coordinate '{name}' ({val})."
                    errors.append(msg)
                    findings.append({"code": "NON_FINITE_COORDINATE", "severity": "error", "entity_ids": [w.id], "message": msg})

        for r in self.rooms.values():
            for idx, pt in enumerate(r.points):
                for val, name in [(pt[0], "x"), (pt[1], "y")]:
                    if not math.isfinite(val):
                        msg = f"Room '{r.id}' vertex {idx} has non-finite coordinate '{name}' ({val})."
                        errors.append(msg)
                        findings.append({"code": "NON_FINITE_COORDINATE", "severity": "error", "entity_ids": [r.id], "message": msg})

        for op in self.openings.values():
            check_vals = [(op.offset_cm, "offset_cm"), (op.width_cm, "width_cm"), (op.height_cm, "height_cm"), (op.elevation_cm, "elevation_cm")]
            if op.depth_cm is not None:
                check_vals.append((op.depth_cm, "depth_cm"))
            if op.x_cm is not None:
                check_vals.append((op.x_cm, "x_cm"))
            if op.y_cm is not None:
                check_vals.append((op.y_cm, "y_cm"))
            if op.angle_deg is not None:
                check_vals.append((op.angle_deg, "angle_deg"))
            for val, name in check_vals:
                if not math.isfinite(val):
                    msg = f"Opening '{op.id}' has non-finite parameter '{name}' ({val})."
                    errors.append(msg)
                    findings.append({"code": "NON_FINITE_COORDINATE", "severity": "error", "entity_ids": [op.id], "message": msg})

        for f in self.furniture.values():
            for val, name in [(f.x_cm, "x_cm"), (f.y_cm, "y_cm"), (f.elevation_cm, "elevation_cm"),
                              (f.width_cm, "width_cm"), (f.depth_cm, "depth_cm"), (f.height_cm, "height_cm"), (f.angle_deg, "angle_deg")]:
                if not math.isfinite(val):
                    msg = f"Furniture '{f.id}' has non-finite parameter '{name}' ({val})."
                    errors.append(msg)
                    findings.append({"code": "NON_FINITE_COORDINATE", "severity": "error", "entity_ids": [f.id], "message": msg})

        for dim in self.dimensions.values():
            for val, name in [(dim.x_start, "x_start"), (dim.y_start, "y_start"), (dim.x_end, "x_end"), (dim.y_end, "y_end"),
                              (dim.offset_cm, "offset_cm"), (dim.elevation_cm, "elevation_cm")]:
                if not math.isfinite(val):
                    msg = f"Dimension '{dim.id}' has non-finite coordinate '{name}' ({val})."
                    errors.append(msg)
                    findings.append({"code": "NON_FINITE_COORDINATE", "severity": "error", "entity_ids": [dim.id], "message": msg})

        # 1. Check Wall invariants
        for w in self.walls.values():
            if w.thickness <= 0:
                msg = f"Wall '{w.id}' has non-positive thickness ({w.thickness} cm)."
                errors.append(msg)
                findings.append({"code": "NEGATIVE_WALL_THICKNESS", "severity": "error", "entity_ids": [w.id], "message": msg})
            if w.height <= 0:
                msg = f"Wall '{w.id}' has non-positive height ({w.height} cm)."
                errors.append(msg)
                findings.append({"code": "NEGATIVE_WALL_HEIGHT", "severity": "error", "entity_ids": [w.id], "message": msg})
            if math.isfinite(w.arc_extent) and abs(w.arc_extent) >= 360.0:
                msg = f"Wall '{w.id}' has invalid arc_extent ({w.arc_extent}°). Must be strictly between -360° and +360°."
                errors.append(msg)
                findings.append({"code": "INVALID_ARC_EXTENT", "severity": "error", "entity_ids": [w.id], "message": msg})
            if w.chord_length <= 1e-4:
                msg = f"Wall '{w.id}' has zero chord length (coincident start and end points)."
                errors.append(msg)
                findings.append({"code": "ZERO_LENGTH_WALL", "severity": "error", "entity_ids": [w.id], "message": msg})
            if w.level_id not in level_ids:
                msg = f"Wall '{w.id}' references nonexistent level '{w.level_id}'."
                errors.append(msg)
                findings.append({"code": "MISSING_LEVEL_REFERENCE", "severity": "error", "entity_ids": [w.id], "message": msg})

        # 2. Check Opening bounds on walls
        for op in self.openings.values():
            if op.width_cm <= 0 or op.height_cm <= 0:
                msg = f"Opening '{op.id}' has non-positive dimensions ({op.width_cm}x{op.height_cm} cm)."
                errors.append(msg)
                findings.append({"code": "OPENING_NEGATIVE_DIMENSIONS", "severity": "error", "entity_ids": [op.id], "message": msg})
            if op.offset_cm < 0:
                msg = f"Opening '{op.id}' has negative offset ({op.offset_cm} cm)."
                errors.append(msg)
                findings.append({"code": "OPENING_NEGATIVE_OFFSET", "severity": "error", "entity_ids": [op.id], "message": msg})
            
            if op.wall_id:
                wall = self.walls.get(op.wall_id)
                if not wall:
                    msg = f"Opening '{op.id}' references missing wall '{op.wall_id}'."
                    errors.append(msg)
                    findings.append({"code": "OPENING_MISSING_HOST", "severity": "error", "entity_ids": [op.id], "message": msg})
                elif op.offset_cm + op.width_cm > wall.length + 1e-4:
                    msg = f"Opening '{op.id}' ({op.width_cm}cm) exceeds length of wall '{wall.id}' ({wall.length:.1f}cm)."
                    errors.append(msg)
                    findings.append({"code": "OPENING_EXCEEDS_WALL", "severity": "error", "entity_ids": [op.id, wall.id], "message": msg})

        # Check for overlapping openings on same host wall
        openings_by_wall = {}
        for op in self.openings.values():
            if op.wall_id:
                openings_by_wall.setdefault(op.wall_id, []).append(op)
        for w_id, ops in openings_by_wall.items():
            for i in range(len(ops)):
                for j in range(i + 1, len(ops)):
                    op1, op2 = ops[i], ops[j]
                    s1, e1 = op1.offset_cm, op1.offset_cm + op1.width_cm
                    s2, e2 = op2.offset_cm, op2.offset_cm + op2.width_cm
                    if max(s1, s2) < min(e1, e2) - 1e-4:
                        v_s1, v_e1 = op1.elevation_cm, op1.elevation_cm + op1.height_cm
                        v_s2, v_e2 = op2.elevation_cm, op2.elevation_cm + op2.height_cm
                        if max(v_s1, v_s2) < min(v_e1, v_e2) - 1e-4:
                            msg = f"Openings '{op1.id}' and '{op2.id}' overlap on wall '{w_id}'."
                            warnings.append(msg)
                            findings.append({"code": "OPENING_OVERLAP", "severity": "warning", "entity_ids": [op1.id, op2.id, w_id], "message": msg})

        # 3. Check room point counts and topology
        def _segments_intersect(p1, p2, p3, p4):
            def ccw(A, B, C):
                return (C[1]-A[1]) * (B[0]-A[0]) > (B[1]-A[1]) * (C[0]-A[0])
            return ccw(p1,p3,p4) != ccw(p2,p3,p4) and ccw(p1,p2,p3) != ccw(p1,p2,p4)

        for r in self.rooms.values():
            if len(r.points) < 3:
                msg = f"Room '{r.id}' has fewer than 3 vertices ({len(r.points)})."
                errors.append(msg)
                findings.append({"code": "DEGENERATE_ROOM", "severity": "error", "entity_ids": [r.id], "message": msg})
            
            if r.level_id not in level_ids:
                msg = f"Room '{r.id}' references nonexistent level '{r.level_id}'."
                errors.append(msg)
                findings.append({"code": "MISSING_LEVEL_REFERENCE", "severity": "error", "entity_ids": [r.id], "message": msg})

            if len(r.points) >= 3:
                n = len(r.points)
                has_intersection = False
                for i in range(n):
                    s1_a, s1_b = r.points[i], r.points[(i + 1) % n]
                    for j in range(i + 2, n):
                        if i == 0 and j == n - 1:
                            continue
                        s2_a, s2_b = r.points[j], r.points[(j + 1) % n]
                        if _segments_intersect(s1_a, s1_b, s2_a, s2_b):
                            has_intersection = True
                            break
                    if has_intersection:
                        break
                if has_intersection:
                    msg = f"Room '{r.id}' ({r.name}) has a self-intersecting boundary polygon."
                    errors.append(msg)
                    findings.append({"code": "ROOM_SELF_INTERSECTION", "severity": "error", "entity_ids": [r.id], "message": msg})

            if r.area_m2 <= 0.0:
                msg = f"Room '{r.id}' ({r.name}) has zero or degenerate area ({r.area_m2} m²)."
                errors.append(msg)
                findings.append({"code": "ZERO_AREA_ROOM", "severity": "error", "entity_ids": [r.id], "message": msg})

        # 4. Check furniture invariants and spatial collisions (Oriented Bounding Box SAT)
        def _get_furn_corners(f):
            rad = math.radians(f.angle_deg)
            cos_a, sin_a = math.cos(rad), math.sin(rad)
            dx1, dy1 = (f.width_cm / 2.0) * cos_a, (f.width_cm / 2.0) * sin_a
            dx2, dy2 = -(f.depth_cm / 2.0) * sin_a, (f.depth_cm / 2.0) * cos_a
            return [
                (f.x_cm - dx1 - dx2, f.y_cm - dy1 - dy2),
                (f.x_cm + dx1 - dx2, f.y_cm + dy1 - dy2),
                (f.x_cm + dx1 + dx2, f.y_cm + dy1 + dy2),
                (f.x_cm - dx1 + dx2, f.y_cm - dy1 + dy2),
            ]

        def _obb_overlap(box1, box2, tol=1.0):
            for box in (box1, box2):
                for i in range(2):
                    p1 = box[i]
                    p2 = box[i + 1]
                    nx = -(p2[1] - p1[1])
                    ny = p2[0] - p1[0]
                    l = math.hypot(nx, ny)
                    if l < 1e-6:
                        continue
                    nx, ny = nx / l, ny / l
                    proj1 = [p[0]*nx + p[1]*ny for p in box1]
                    proj2 = [p[0]*nx + p[1]*ny for p in box2]
                    overlap = min(max(proj1), max(proj2)) - max(min(proj1), min(proj2))
                    if overlap <= tol:
                        return False
            return True

        furn_by_level = {}
        for f in self.furniture.values():
            if f.width_cm <= 0 or f.depth_cm <= 0 or f.height_cm <= 0:
                msg = f"Furniture '{f.id}' has non-positive dimensions ({f.width_cm}x{f.depth_cm}x{f.height_cm} cm)."
                errors.append(msg)
                findings.append({"code": "FURNITURE_NEGATIVE_DIMENSIONS", "severity": "error", "entity_ids": [f.id], "message": msg})
            if f.level_id not in level_ids:
                msg = f"Furniture '{f.id}' references nonexistent level '{f.level_id}'."
                errors.append(msg)
                findings.append({"code": "MISSING_LEVEL_REFERENCE", "severity": "error", "entity_ids": [f.id], "message": msg})
            furn_by_level.setdefault(f.level_id, []).append(f)

        for lvl_id, furn_list in furn_by_level.items():
            for i in range(len(furn_list)):
                f1 = furn_list[i]
                b1 = _get_furn_corners(f1)
                for j in range(i + 1, len(furn_list)):
                    f2 = furn_list[j]
                    # Check vertical elevation separation (stacked furniture like lamp on table or upper cabinet)
                    z_overlap = not (f1.elevation_cm + f1.height_cm <= f2.elevation_cm or f2.elevation_cm + f2.height_cm <= f1.elevation_cm)
                    if not z_overlap:
                        continue
                    b2 = _get_furn_corners(f2)
                    if _obb_overlap(b1, b2, tol=1.0):
                        msg = f"Furniture collision between '{f1.id}' ({f1.name}) and '{f2.id}' ({f2.name}) on {lvl_id}."
                        warnings.append(msg)
                        findings.append({"code": "FURNITURE_COLLISION", "severity": "warning", "entity_ids": [f1.id, f2.id], "message": msg})

        # 5. Check door swing sector vs furniture collisions
        def _door_sector_intersects_polygon(hinge, p_latch, p_open, poly_corners, tol=1.0):
            v_latch = (p_latch[0] - hinge[0], p_latch[1] - hinge[1])
            v_open = (p_open[0] - hinge[0], p_open[1] - hinge[1])
            r_swing = math.hypot(v_latch[0], v_latch[1])
            if r_swing < 1e-4:
                return False

            a_closed = math.atan2(v_latch[1], v_latch[0])
            a_open = math.atan2(v_open[1], v_open[0])

            diff = (a_open - a_closed) % (2.0 * math.pi)
            if diff > math.pi:
                diff -= 2.0 * math.pi

            def in_sector(pt):
                d = math.hypot(pt[0] - hinge[0], pt[1] - hinge[1])
                if d > r_swing - tol:
                    return False
                ang = math.atan2(pt[1] - hinge[1], pt[0] - hinge[0])
                rel_ang = (ang - a_closed) % (2.0 * math.pi)
                if rel_ang > math.pi:
                    rel_ang -= 2.0 * math.pi
                if diff >= 0:
                    return -0.01 <= rel_ang <= diff + 0.01
                else:
                    return diff - 0.01 <= rel_ang <= 0.01

            for c in poly_corners:
                if in_sector(c):
                    return True

            n_c = len(poly_corners)
            inside = False
            p1x, p1y = poly_corners[0]
            for idx in range(n_c + 1):
                p2x, p2y = poly_corners[idx % n_c]
                if hinge[1] > min(p1y, p2y):
                    if hinge[1] <= max(p1y, p2y):
                        if hinge[0] <= max(p1x, p2x):
                            xinters = (hinge[1] - p1y) * (p2x - p1x) / (p2y - p1y) + p1x if p1y != p2y else p1x
                            if p1x == p2x or hinge[0] <= xinters:
                                inside = not inside
                p1x, p1y = p2x, p2y
            if inside:
                return True

            for seg in [(hinge, p_latch), (hinge, p_open)]:
                for idx in range(n_c):
                    c1 = poly_corners[idx]
                    c2 = poly_corners[(idx + 1) % n_c]
                    if _segments_intersect(seg[0], seg[1], c1, c2):
                        return True

            # Check if any polygon edge intersects the curved arc boundary of the sector
            hx, hy = hinge
            for idx in range(n_c):
                p1 = poly_corners[idx]
                p2 = poly_corners[(idx + 1) % n_c]
                dx = p2[0] - p1[0]
                dy = p2[1] - p1[1]
                A = dx * dx + dy * dy
                if A < 1e-8:
                    continue
                del_x = p1[0] - hx
                del_y = p1[1] - hy
                B = 2.0 * (del_x * dx + del_y * dy)
                C = del_x * del_x + del_y * del_y - r_swing * r_swing
                disc = B * B - 4.0 * A * C
                if disc >= 0.0:
                    sqrt_disc = math.sqrt(disc)
                    for sign_root in (-1.0, 1.0):
                        t = (-B + sign_root * sqrt_disc) / (2.0 * A)
                        if 0.0 <= t <= 1.0:
                            qx = p1[0] + t * dx
                            qy = p1[1] + t * dy
                            ang_q = math.atan2(qy - hy, qx - hx)
                            rel_ang = (ang_q - a_closed) % (2.0 * math.pi)
                            if rel_ang > math.pi:
                                rel_ang -= 2.0 * math.pi
                            if diff >= 0:
                                if -0.01 <= rel_ang <= diff + 0.01:
                                    return True
                            else:
                                if diff - 0.01 <= rel_ang <= 0.01:
                                    return True

            return False

        for op in self.openings.values():
            if op.opening_type == "door" and op.swing not in ("none", "sliding"):
                wall = self.walls.get(op.wall_id)
                geom = op.get_effective_geometry(wall)
                if not geom:
                    continue

                if op.wall_id is None and op.angle_deg is None and (op.x_cm is not None or op.y_cm is not None):
                    findings.append({
                        "code": "INCOMPLETE_OPENING_POSE",
                        "severity": "warning",
                        "message": f"Unhosted door '{op.name}' ({op.id}) missing explicit angle; defaulting to 0 degrees.",
                        "entity_type": "opening",
                        "entity_id": op.id,
                    })

                hinge = geom["hinge"]
                p_latch = geom["latch"]
                p_open = geom["open_endpoint"]
                w = geom["width"]

                lvl = op.level_id or (wall.level_id if wall else "level1")
                for f in furn_by_level.get(lvl, []):
                    if f.elevation_cm >= op.elevation_cm + op.height_cm or f.elevation_cm + f.height_cm <= op.elevation_cm:
                        continue
                    f_corners = _get_furn_corners(f)
                    if _door_sector_intersects_polygon(hinge, p_latch, p_open, f_corners, tol=1.0):
                        msg = f"Door '{op.id}' swing sector collides with furniture '{f.id}' ({f.name})."
                        warnings.append(msg)
                        findings.append({"code": "DOOR_SWEEP_COLLISION", "severity": "warning", "entity_ids": [op.id, f.id], "message": msg})

        # 6. Check for empty structure
        if not self.walls and not self.rooms:
            warnings.append("Project contains no walls or rooms.")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "findings": findings,
            "summary": {
                "walls_count": len(self.walls),
                "rooms_count": len(self.rooms),
                "openings_count": len(self.openings),
                "furniture_count": len(self.furniture),
                "dimensions_count": len(self.dimensions),
                "total_area_m2": self.total_area_m2,
                "total_wall_length_m": self.total_wall_length_m,
            }
        }

    def to_xml(self):
        """Generates valid Sweet Home 3D Home.xml string preserving extra nodes and attributes."""
        root_attribs = {"version": self.version, "name": self.name, "wallHeight": str(self.wall_height)}
        root_attribs.update(self.extra_root_attribs)
        root = ET.Element("home", **root_attribs)
        
        # Levels (floorThickness preserved from source or default 12.0)
        for lvl in self.levels:
            lvl_attrib = {
                "id": lvl.id, "name": lvl.name,
                "elevation": str(lvl.elevation_cm), "height": str(lvl.height_cm),
                "floorThickness": str(lvl.floor_thickness_cm)
            }
            lvl_attrib.update(lvl.extra_attribs)
            elem = ET.SubElement(root, "level", **lvl_attrib)
            for raw_child in lvl.extra_children:
                try:
                    elem.append(ET.fromstring(raw_child))
                except Exception:
                    pass

        # Walls
        for w in self.walls.values():
            wall_attrib = {
                "id": w.id, "level": w.level_id,
                "xStart": str(w.x_start), "yStart": str(w.y_start),
                "xEnd": str(w.x_end), "yEnd": str(w.y_end),
                "thickness": str(w.thickness), "height": str(w.height),
            }
            if abs(w.arc_extent) > 1e-4:
                wall_attrib["arcExtent"] = str(w.arc_extent)
            wall_attrib.update(w.extra_attribs)
            w_elem = ET.SubElement(root, "wall", **wall_attrib)
            for raw_child in w.extra_children:
                try:
                    w_elem.append(ET.fromstring(raw_child))
                except Exception:
                    pass

        # Rooms
        for r in self.rooms.values():
            r_attrib = {"id": r.id, "level": r.level_id, "name": r.name,
                        "area": str(r.area_m2), "floorColor": r.floor_color}
            r_attrib.update(r.extra_attribs)
            room_elem = ET.SubElement(root, "room", **r_attrib)
            for p in r.points:
                ET.SubElement(room_elem, "point", x=str(p[0]), y=str(p[1]))
            for raw_child in r.extra_children:
                try:
                    room_elem.append(ET.fromstring(raw_child))
                except Exception:
                    pass

        # Openings (doors and windows, emitting native spatial coordinates + wall references)
        for op in self.openings.values():
            wall = self.walls.get(op.wall_id) if op.wall_id else None
            op_attrib = {
                "id": op.id, "name": op.name,
                "width": str(op.width_cm), "height": str(op.height_cm),
                "type": op.opening_type, "swing": op.swing,
                "elevation": str(op.elevation_cm),
            }
            if wall:
                p_center = wall.point_at_offset(op.offset_cm + op.width_cm / 2.0)
                depth_val = op.depth_cm if op.depth_cm is not None else wall.thickness
                out_x = op.x_cm if op.x_cm is not None else p_center[0]
                out_y = op.y_cm if op.y_cm is not None else p_center[1]
                out_angle = math.radians(op.angle_deg) if op.angle_deg is not None else math.radians(wall.angle_deg)
                op_attrib.update({
                    "wall": op.wall_id,
                    "offset": str(op.offset_cm),
                    "x": str(round(out_x, 2)),
                    "y": str(round(out_y, 2)),
                    "depth": str(depth_val),
                    "angle": str(round(out_angle, 4)),
                    "level": op.level_id or wall.level_id,
                })
            else:
                if op.wall_id:
                    op_attrib["wall"] = op.wall_id
                    op_attrib["offset"] = str(op.offset_cm)
                if op.x_cm is not None:
                    op_attrib["x"] = str(round(op.x_cm, 2))
                if op.y_cm is not None:
                    op_attrib["y"] = str(round(op.y_cm, 2))
                if op.depth_cm is not None:
                    op_attrib["depth"] = str(op.depth_cm)
                if op.angle_deg is not None:
                    op_attrib["angle"] = str(round(math.radians(op.angle_deg), 4))
                if op.level_id:
                    op_attrib["level"] = op.level_id
            op_attrib.update(op.extra_attribs)
            op_elem = ET.SubElement(root, "doorOrWindow", **op_attrib)
            for raw_child in op.extra_children:
                try:
                    op_elem.append(ET.fromstring(raw_child))
                except Exception:
                    pass

        # Furniture (angle in radians per native Sweet Home 3D API)
        for f in self.furniture.values():
            f_attrib = {
                "id": f.id, "level": f.level_id,
                "name": f.name, "catalogId": f.catalog_id,
                "x": str(f.x_cm), "y": str(f.y_cm), "elevation": str(f.elevation_cm),
                "width": str(f.width_cm), "depth": str(f.depth_cm), "height": str(f.height_cm),
                "angle": str(round(math.radians(f.angle_deg), 4))
            }
            f_attrib.update(f.extra_attribs)
            f_elem = ET.SubElement(root, "pieceOfFurniture", **f_attrib)
            for raw_child in f.extra_children:
                try:
                    f_elem.append(ET.fromstring(raw_child))
                except Exception:
                    pass

        # Dimensions
        for dim in self.dimensions.values():
            d_attrib = {
                "id": dim.id, "level": dim.level_id,
                "xStart": str(dim.x_start), "yStart": str(dim.y_start),
                "xEnd": str(dim.x_end), "yEnd": str(dim.y_end),
                "offset": str(dim.offset_cm), "elevation": str(dim.elevation_cm)
            }
            d_attrib.update(dim.extra_attribs)
            d_elem = ET.SubElement(root, "dimensionLine", **d_attrib)
            for raw_child in dim.extra_children:
                try:
                    d_elem.append(ET.fromstring(raw_child))
                except Exception:
                    pass

        # Preserve unmodeled extra elements (cameras, observers, background images, etc.)
        for raw_elem_str in self.extra_elements:
            try:
                root.append(ET.fromstring(raw_elem_str))
            except Exception:
                pass

        rough_string = ET.tostring(root, "utf-8")
        reparsed = minidom.parseString(rough_string)
        return reparsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")

    @classmethod
    def from_xml(cls, xml_text):
        """Parses Sweet Home 3D Home.xml into a HomeModel instance preserving unmodeled elements."""
        root = ET.fromstring(xml_text)
        model = cls(
            name=root.attrib.get("name", "Imported Home"),
            wall_height=float(root.attrib.get("wallHeight", 280.0)),
            version=root.attrib.get("version", "7.5"),
        )
        for key, val in root.attrib.items():
            if key not in ("name", "wallHeight", "version"):
                model.extra_root_attribs[key] = val

        KNOWN_TAGS = {"level", "wall", "room", "doorOrWindow", "pieceOfFurniture", "dimensionLine"}
        for child in root:
            if child.tag not in KNOWN_TAGS:
                model.extra_elements.append(ET.tostring(child, encoding="unicode"))

        model.levels = []

        # Levels
        for elem in root.findall("level"):
            known_lvl_keys = {"id", "name", "elevation", "height", "floorThickness"}
            extra_attribs = {k: v for k, v in elem.attrib.items() if k not in known_lvl_keys}
            extra_children = [ET.tostring(c, encoding="unicode") for c in elem]
            model.add_level(
                level_id=elem.attrib.get("id"),
                name=elem.attrib.get("name", "Level"),
                elevation_cm=float(elem.attrib.get("elevation", 0.0)),
                height_cm=float(elem.attrib.get("height", 280.0)),
                floor_thickness_cm=float(elem.attrib.get("floorThickness", 12.0)),
                extra_attribs=extra_attribs,
                extra_children=extra_children,
            )
        if not model.levels:
            model.levels = [Level("level1", "Ground Floor", 0.0, 280.0)]

        # Walls
        for elem in root.findall("wall"):
            known_wall_keys = {"id", "xStart", "yStart", "xEnd", "yEnd", "thickness", "height", "level", "arcExtent"}
            extra_attribs = {k: v for k, v in elem.attrib.items() if k not in known_wall_keys}
            extra_children = [ET.tostring(c, encoding="unicode") for c in elem]
            model.add_wall(
                wall_id=elem.attrib.get("id"),
                x_start=float(elem.attrib.get("xStart", 0.0)),
                y_start=float(elem.attrib.get("yStart", 0.0)),
                x_end=float(elem.attrib.get("xEnd", 0.0)),
                y_end=float(elem.attrib.get("yEnd", 0.0)),
                thickness=float(elem.attrib.get("thickness", 15.0)),
                height=float(elem.attrib.get("height", 280.0)),
                level_id=elem.attrib.get("level", "level1"),
                arc_extent=float(elem.attrib.get("arcExtent", 0.0)),
                extra_attribs=extra_attribs,
                extra_children=extra_children,
            )

        # Rooms
        for elem in root.findall("room"):
            known_room_keys = {"id", "level", "name", "area", "floorColor"}
            extra_attribs = {k: v for k, v in elem.attrib.items() if k not in known_room_keys}
            extra_children = [ET.tostring(c, encoding="unicode") for c in elem if c.tag != "point"]
            points = []
            for p in elem.findall("point"):
                points.append((float(p.attrib.get("x", 0.0)), float(p.attrib.get("y", 0.0))))
            model.add_room(
                room_id=elem.attrib.get("id"),
                name=elem.attrib.get("name", "Room"),
                points=points,
                level_id=elem.attrib.get("level", "level1"),
                floor_color=elem.attrib.get("floorColor", "#EAEAEA"),
                extra_attribs=extra_attribs,
                extra_children=extra_children,
            )

        # Openings
        for elem in root.findall("doorOrWindow"):
            known_op_keys = {"id", "name", "wall", "offset", "width", "height", "type", "swing", "x", "y", "depth", "angle", "level", "elevation"}
            extra_attribs = {k: v for k, v in elem.attrib.items() if k not in known_op_keys}
            extra_children = [ET.tostring(c, encoding="unicode") for c in elem]

            op_id = elem.attrib.get("id")
            name = elem.attrib.get("name", "Door/Window")
            wall_id = elem.attrib.get("wall")
            width_cm = float(elem.attrib.get("width", 90.0))
            height_cm = float(elem.attrib.get("height", 210.0))
            op_type = elem.attrib.get("type", "door")
            swing = elem.attrib.get("swing", "right")
            elevation_cm = float(elem.attrib.get("elevation", 0.0))
            depth_cm = float(elem.attrib["depth"]) if "depth" in elem.attrib else None
            x_cm = float(elem.attrib["x"]) if "x" in elem.attrib else None
            y_cm = float(elem.attrib["y"]) if "y" in elem.attrib else None
            angle_deg = None
            if "angle" in elem.attrib:
                angle_raw = float(elem.attrib["angle"])
                # Sweet Home 3D XML spec defines 'angle' in radians.
                # If magnitude exceeds 2π, assume non-standard degree encoding.
                if abs(angle_raw) > 2 * math.pi:
                    angle_deg = round(angle_raw % 360.0, 1)
                else:
                    angle_deg = round(math.degrees(angle_raw) % 360.0, 1)
            level_id = elem.attrib.get("level", "level1")

            offset_cm = None
            if "offset" in elem.attrib:
                offset_cm = float(elem.attrib["offset"])

            # Native opening handling without 'wall' or with spatial coordinates
            if wall_id is None or wall_id not in model.walls:
                if x_cm is not None and y_cm is not None and model.walls:
                    gx = x_cm
                    gy = y_cm
                    best_w = None
                    best_dist = float("inf")
                    best_off = 0.0
                    for candidate_w in model.walls.values():
                        if candidate_w.level_id != level_id:
                            continue
                        x1, y1, x2, y2 = candidate_w.x_start, candidate_w.y_start, candidate_w.x_end, candidate_w.y_end
                        L = candidate_w.chord_length
                        if L > 1e-4:
                            t = max(0.0, min(1.0, ((gx - x1)*(x2 - x1) + (gy - y1)*(y2 - y1)) / (L*L)))
                            px, py = x1 + t*(x2 - x1), y1 + t*(y2 - y1)
                            d = math.hypot(gx - px, gy - py)
                            if d < best_dist:
                                best_dist = d
                                best_w = candidate_w
                                best_off = max(0.0, t * candidate_w.length - width_cm / 2.0)
                    if best_w and best_dist < 100.0:
                        wall_id = best_w.id
                        level_id = best_w.level_id
                        if offset_cm is None:
                            offset_cm = best_off

            if offset_cm is None:
                offset_cm = 50.0

            model.add_opening(
                opening_id=op_id,
                name=name,
                wall_id=wall_id,
                offset_cm=offset_cm,
                width_cm=width_cm,
                height_cm=height_cm,
                opening_type=op_type,
                swing=swing,
                elevation_cm=elevation_cm,
                depth_cm=depth_cm,
                x_cm=x_cm,
                y_cm=y_cm,
                angle_deg=angle_deg,
                level_id=level_id,
                extra_attribs=extra_attribs,
                extra_children=extra_children,
            )

        # Furniture
        for elem in root.findall("pieceOfFurniture"):
            known_f_keys = {"id", "name", "catalogId", "x", "y", "elevation", "width", "depth", "height", "angle", "level"}
            extra_attribs = {k: v for k, v in elem.attrib.items() if k not in known_f_keys}
            extra_children = [ET.tostring(c, encoding="unicode") for c in elem]

            angle_raw = float(elem.attrib.get("angle", 0.0))
            # Sweet Home 3D XML spec defines 'angle' in radians.
            # If magnitude exceeds 2π, assume non-standard degree encoding.
            if abs(angle_raw) > 2 * math.pi:
                angle_deg = round(angle_raw % 360.0, 1)
            else:
                angle_deg = round(math.degrees(angle_raw) % 360.0, 1)

            model.add_furniture(
                furniture_id=elem.attrib.get("id"),
                name=elem.attrib.get("name"),
                catalog_id=elem.attrib.get("catalogId", "custom"),
                x_cm=float(elem.attrib.get("x", 0.0)),
                y_cm=float(elem.attrib.get("y", 0.0)),
                elevation_cm=float(elem.attrib.get("elevation", 0.0)),
                width_cm=float(elem.attrib["width"]) if "width" in elem.attrib else None,
                depth_cm=float(elem.attrib["depth"]) if "depth" in elem.attrib else None,
                height_cm=float(elem.attrib["height"]) if "height" in elem.attrib else None,
                angle_deg=angle_deg,
                level_id=elem.attrib.get("level", "level1"),
                extra_attribs=extra_attribs,
                extra_children=extra_children,
            )

        # Dimensions
        for elem in root.findall("dimensionLine"):
            known_dim_keys = {"id", "level", "xStart", "yStart", "xEnd", "yEnd", "offset", "elevation"}
            extra_attribs = {k: v for k, v in elem.attrib.items() if k not in known_dim_keys}
            extra_children = [ET.tostring(c, encoding="unicode") for c in elem]
            model.add_dimension(
                dimension_id=elem.attrib.get("id"),
                x_start=float(elem.attrib.get("xStart", 0.0)),
                y_start=float(elem.attrib.get("yStart", 0.0)),
                x_end=float(elem.attrib.get("xEnd", 0.0)),
                y_end=float(elem.attrib.get("yEnd", 0.0)),
                offset_cm=float(elem.attrib.get("offset", 30.0)),
                level_id=elem.attrib.get("level", "level1"),
                elevation_cm=float(elem.attrib.get("elevation", 0.0)),
                extra_attribs=extra_attribs,
                extra_children=extra_children,
            )

        return model

    def save_sh3d(self, filepath):
        """Saves project as a native .sh3d compressed ZIP archive atomically preserving all resources."""
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        tmp_target = filepath + ".tmp"
        xml_content = self.to_xml()
        with zipfile.ZipFile(tmp_target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Home.xml", xml_content)
            for fname, data_bytes in self.extra_zip_entries.items():
                if fname != "Home.xml":
                    zf.writestr(fname, data_bytes)
        os.replace(tmp_target, filepath)
        return filepath

    @classmethod
    def load_sh3d(cls, filepath):
        """Loads project from a .sh3d compressed ZIP archive, preserving all non-Home.xml entries."""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Project file '{filepath}' not found.")
        extra_entries = {}
        xml_content = None
        with zipfile.ZipFile(filepath, "r") as zf:
            for item in zf.infolist():
                if item.filename == "Home.xml":
                    xml_content = zf.read("Home.xml").decode("utf-8")
                else:
                    extra_entries[item.filename] = zf.read(item.filename)
        if not xml_content:
            raise ValueError("Corrupted .sh3d file: missing 'Home.xml' entry.")
        model = cls.from_xml(xml_content)
        model.extra_zip_entries = extra_entries
        return model

    def to_dict(self):
        extra_zip_serialized = {}
        for k, v in self.extra_zip_entries.items():
            if isinstance(v, bytes):
                extra_zip_serialized[k] = base64.b64encode(v).decode("ascii")
            elif isinstance(v, str):
                extra_zip_serialized[k] = base64.b64encode(v.encode("utf-8")).decode("ascii")
            else:
                extra_zip_serialized[k] = str(v)

        return {
            "name": self.name,
            "version": self.version,
            "wall_height": self.wall_height,
            "levels": [lvl.to_dict() for lvl in self.levels],
            "walls": [w.to_dict() for w in self.walls.values()],
            "rooms": [r.to_dict() for r in self.rooms.values()],
            "openings": [op.to_dict() for op in self.openings.values()],
            "furniture": [f.to_dict() for f in self.furniture.values()],
            "dimensions": [dim.to_dict() for dim in self.dimensions.values()],
            "extra_root_attribs": self.extra_root_attribs,
            "extra_elements": self.extra_elements,
            "extra_zip_entries": extra_zip_serialized,
            "summary": {
                "total_area_m2": self.total_area_m2,
                "total_wall_length_m": self.total_wall_length_m,
            }
        }

    @classmethod
    def from_dict(cls, d):
        model = cls(
            name=d.get("name", "New Home"),
            wall_height=d.get("wall_height", 280.0),
            version=d.get("version", "7.5"),
        )
        model.extra_root_attribs = d.get("extra_root_attribs", {})
        model.extra_elements = d.get("extra_elements", [])
        extra_zip_raw = d.get("extra_zip_entries", {})
        model.extra_zip_entries = {}
        for k, v in extra_zip_raw.items():
            if isinstance(v, str):
                try:
                    model.extra_zip_entries[k] = base64.b64decode(v.encode("ascii"), validate=True)
                except Exception:
                    model.extra_zip_entries[k] = v.encode("utf-8")
            elif isinstance(v, bytes):
                model.extra_zip_entries[k] = v
            else:
                model.extra_zip_entries[k] = str(v).encode("utf-8")
        model.levels = []
        seen_levels = set()
        for lvl_data in d.get("levels", []):
            lvl_obj = Level.from_dict(lvl_data)
            if lvl_obj.id in seen_levels:
                raise ValueError(f"Duplicate level ID '{lvl_obj.id}' in import data.")
            seen_levels.add(lvl_obj.id)
            model.levels.append(lvl_obj)
        if not model.levels:
            model.levels = [Level("level1", "Ground Floor", 0.0, 280.0)]
        for w in d.get("walls", []):
            wall_obj = Wall.from_dict(w)
            if wall_obj.id in model.walls:
                raise ValueError(f"Duplicate wall ID '{wall_obj.id}' in import data.")
            model.walls[wall_obj.id] = wall_obj
        for r in d.get("rooms", []):
            room_obj = Room.from_dict(r)
            if room_obj.id in model.rooms:
                raise ValueError(f"Duplicate room ID '{room_obj.id}' in import data.")
            model.rooms[room_obj.id] = room_obj
        for op in d.get("openings", []):
            op_obj = Opening.from_dict(op)
            if op_obj.id in model.openings:
                raise ValueError(f"Duplicate opening ID '{op_obj.id}' in import data.")
            model.openings[op_obj.id] = op_obj
        for f in d.get("furniture", []):
            f_obj = Furniture.from_dict(f)
            if f_obj.id in model.furniture:
                raise ValueError(f"Duplicate furniture ID '{f_obj.id}' in import data.")
            model.furniture[f_obj.id] = f_obj
        for dim in d.get("dimensions", []):
            dim_obj = DimensionLine.from_dict(dim)
            if dim_obj.id in model.dimensions:
                raise ValueError(f"Duplicate dimension ID '{dim_obj.id}' in import data.")
            model.dimensions[dim_obj.id] = dim_obj
        return model

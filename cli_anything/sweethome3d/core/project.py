import os
import copy
import math
import json
import zipfile
import base64
import xml.etree.ElementTree as ET
from xml.dom import minidom
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Any, List

from cli_anything.sweethome3d.core.wall import Wall
from cli_anything.sweethome3d.core.room import Room
from cli_anything.sweethome3d.core.opening import Opening
from cli_anything.sweethome3d.core.furniture import Furniture
from cli_anything.sweethome3d.core.dimension import DimensionLine
from cli_anything.sweethome3d.core.exceptions import (
    EntityConflictError,
    ValidationRejectedError,
    UnsupportedFeatureError,
)
from cli_anything.sweethome3d.core.native_contract import (
    decode_angle,
    encode_angle,
    checked_native_float,
    rgb_to_native,
    native_to_rgb,
    assert_native_wire,
    assert_native_model_domain,
)


def strict_json_loads(text):
    """Parses JSON text strictly, refusing nonfinite constants (NaN, Infinity) and nonfinite floats."""
    def reject_constant(token):
        raise ValueError(f"Nonfinite JSON constant: {token}")

    def finite_float(token):
        value = float(token)
        if not math.isfinite(value):
            raise ValueError(f"Nonfinite JSON number: {token}")
        return value

    return json.loads(text, parse_constant=reject_constant, parse_float=finite_float)


def decode_opening_record(element, native_swing=None):
    """Decodes and strictly validates all cli-anything.opening.v1 metadata records on an element."""
    fields = {"version", "kind", "operation", "host_id", "offset_cm"}
    props = element.findall("property[@name='cli-anything.opening.v1']")
    resolved = None
    for prop in props:
        try:
            record = strict_json_loads(prop.get("value", ""))
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f"Corrupt opening metadata: {exc}") from exc
        if type(record) is not dict or set(record) != fields:
            raise ValueError("Unsupported opening metadata fields")
        if type(record["version"]) is not int or record["version"] != 1:
            raise ValueError("Unsupported opening metadata version")
        if type(record["kind"]) is not str or record["kind"] not in ("door", "window"):
            raise ValueError("Unsupported opening metadata kind")
        if (type(record["operation"]) is not str or
                record["operation"] not in ("left", "right", "none", "sliding")):
            raise ValueError("Unsupported opening metadata operation")
        host = record["host_id"]
        if host is not None and (type(host) is not str or not host):
            raise ValueError("Invalid opening metadata host_id")
        value = record["offset_cm"]
        if type(value) not in (int, float):
            raise ValueError("Opening metadata station must be numeric")
        try:
            station = float(value)
        except (ValueError, OverflowError) as exc:
            raise ValueError("Invalid opening metadata station") from exc
        if not math.isfinite(station) or station < 0:
            raise ValueError("Opening metadata station must be finite and nonnegative")
        record = dict(record, offset_cm=station)
        if native_swing is not None and (
            record["kind"] != "door" or record["operation"] != native_swing
        ):
            raise ValueError("Opening metadata conflicts with native sash; import refused")
        if resolved is not None and resolved != record:
            raise ValueError("Conflicting owned opening metadata records")
        resolved = record
    return resolved


def _resolve_curved_wall_circle(w):
    """Analytically calculates circle center (cx, cy) and radius r for a curved wall.
    
    Returns (cx, cy, r) or None if wall is straight or degenerate.
    """
    if abs(getattr(w, "arc_extent", 0.0)) < 1e-4 or w.chord_length < 1e-6:
        return None
    c = w.chord_length
    theta = math.radians(w.arc_extent)
    alpha = abs(theta) / 2.0
    sin_alpha = math.sin(alpha)
    if abs(sin_alpha) < 1e-6:
        return None
    r = c / (2.0 * sin_alpha)
    h = r * math.cos(alpha)
    mx = (w.x_start + w.x_end) / 2.0
    my = (w.y_start + w.y_end) / 2.0
    nx = -(w.y_end - w.y_start) / c
    ny = (w.x_end - w.x_start) / c
    sign = 1.0 if w.arc_extent > 0 else -1.0
    cx = mx - sign * h * nx
    cy = my - sign * h * ny
    return (cx, cy, r)


def _is_finite_wall(w):
    return all(math.isfinite(v) for v in (w.x_start, w.y_start, w.x_end, w.y_end, w.height, w.thickness, w.arc_extent))


def _is_finite_room(r):
    return len(r.points) >= 3 and all(math.isfinite(p[0]) and math.isfinite(p[1]) for p in r.points)


def _is_finite_opening(op):
    vals = [op.offset_cm, op.width_cm, op.height_cm, op.elevation_cm]
    if op.depth_cm is not None: vals.append(op.depth_cm)
    if op.x_cm is not None: vals.append(op.x_cm)
    if op.y_cm is not None: vals.append(op.y_cm)
    if op.angle_deg is not None: vals.append(op.angle_deg)
    return all(math.isfinite(v) for v in vals)


def _is_finite_furniture(f):
    return all(math.isfinite(v) for v in (f.x_cm, f.y_cm, f.elevation_cm, f.width_cm, f.depth_cm, f.height_cm, f.angle_deg))


@dataclass(frozen=True)
class MaterialSlice:
    z0_cm: float
    z1_cm: float
    polygon: Any


def resolve_wall_material(model, wall, *, z0_cm: float, z1_cm: float, error_cm: float = 0.01) -> List[MaterialSlice]:
    """Resolves physical 2D wall material slices across the world-Z interval [z0_cm, z1_cm].
    Accounts for curved wall geometry, wall height, and active opening cutouts (PAC-1 C04, C08, R18-P1-03).
    """
    try:
        from shapely.geometry import Polygon, LineString
    except ImportError:
        return []

    w_lvl = model.get_level(wall.level_id)
    w_elev = w_lvl.elevation_cm if w_lvl else 0.0
    w_world_z0 = w_elev
    w_world_z1 = w_world_z0 + wall.height

    overlap_z0 = max(z0_cm, w_world_z0)
    overlap_z1 = min(z1_cm, w_world_z1)
    if overlap_z0 >= overlap_z1 - 1e-4:
        return []

    # 2D base wall footprint polygon
    if abs(wall.arc_extent) > 1e-4 and wall.length > 0:
        steps = wall.get_curve_sampling_steps(error_tol=error_cm)
        pts = [wall.point_at_offset(i * wall.length / steps) for i in range(steps + 1)]
        wall_poly = LineString(pts).buffer(wall.thickness / 2.0, cap_style="flat")
    else:
        L = wall.length
        if L < 1e-4:
            return []
        nx = -(wall.y_end - wall.y_start) / L * (wall.thickness / 2.0)
        ny = (wall.x_end - wall.x_start) / L * (wall.thickness / 2.0)
        wall_corners = [
            (wall.x_start + nx, wall.y_start + ny),
            (wall.x_end + nx, wall.y_end + ny),
            (wall.x_end - nx, wall.y_end - ny),
            (wall.x_start - nx, wall.y_start - ny),
        ]
        wall_poly = Polygon(wall_corners)

    openings_on_wall = [op for op in model.openings.values() if op.wall_id == wall.id and _is_finite_opening(op)]
    z_breaks = {overlap_z0, overlap_z1}
    for op in openings_on_wall:
        op_world_z0 = w_elev + op.elevation_cm
        op_world_z1 = op_world_z0 + op.height_cm
        if overlap_z0 < op_world_z0 < overlap_z1:
            z_breaks.add(op_world_z0)
        if overlap_z0 < op_world_z1 < overlap_z1:
            z_breaks.add(op_world_z1)

    z_sorted = sorted(z_breaks)
    slices = []
    for k in range(len(z_sorted) - 1):
        slab_z0 = z_sorted[k]
        slab_z1 = z_sorted[k + 1]
        if slab_z1 - slab_z0 < 1e-4:
            continue
        slab_poly = wall_poly
        for op in openings_on_wall:
            op_world_z0 = w_elev + op.elevation_cm
            op_world_z1 = op_world_z0 + op.height_cm
            if op_world_z0 <= slab_z0 + 1e-4 and op_world_z1 >= slab_z1 - 1e-4:
                geom = op.get_effective_geometry(wall)
                if geom:
                    p1 = geom["p1"]
                    p2 = geom["p2"]
                    t_cut = max(wall.thickness * 1.5, 30.0)
                    nx = geom["nx"] * (t_cut / 2.0)
                    ny = geom["ny"] * (t_cut / 2.0)
                    cutout_poly = Polygon([
                        (p1[0] + nx, p1[1] + ny),
                        (p2[0] + nx, p2[1] + ny),
                        (p2[0] - nx, p2[1] - ny),
                        (p1[0] - nx, p1[1] - ny),
                    ])
                    slab_poly = slab_poly.difference(cutout_poly)
        if not slab_poly.is_empty:
            slices.append(MaterialSlice(z0_cm=slab_z0, z1_cm=slab_z1, polygon=slab_poly))
    return slices


def _make_door_sector_poly(hinge, p_latch, p_open, n_steps=None):
    """Constructs a 2D polygonal approximation of a circular door swing sector (PAC-1 C08, R18-P1-03, R19-P1-05)."""
    try:
        from shapely.geometry import Polygon
    except ImportError:
        return None
    v_latch = (p_latch[0] - hinge[0], p_latch[1] - hinge[1])
    v_open = (p_open[0] - hinge[0], p_open[1] - hinge[1])
    r_swing = math.hypot(v_latch[0], v_latch[1])
    if r_swing < 1e-4:
        return None
    a_closed = math.atan2(v_latch[1], v_latch[0])
    a_open = math.atan2(v_open[1], v_open[0])
    diff = (a_open - a_closed) % (2.0 * math.pi)
    if diff > math.pi:
        diff -= 2.0 * math.pi
    if n_steps is None:
        # PAC-1 C08, R19-P1-05: Physical error budget e <= 0.008 cm <= 0.01 cm
        # Chord sagitta: e = 2 * R * sin^2(d_theta / 4) -> d_theta = 4 * asin(sqrt(min(1.0, e / (2 * R))))
        e_budget = 0.008
        if r_swing > e_budget / 2.0:
            d_theta = 4.0 * math.asin(math.sqrt(min(1.0, e_budget / (2.0 * r_swing))))
            steps = int(math.ceil(abs(diff) / d_theta))
            n_steps = max(32, steps)
        else:
            n_steps = 32
    pts = [hinge]
    for i in range(n_steps + 1):
        ang = a_closed + (diff * i / n_steps)
        pts.append((hinge[0] + r_swing * math.cos(ang), hinge[1] + r_swing * math.sin(ang)))
    return Polygon(pts)


def _compute_unique_curved_runs(lvl_walls, epsilon=0.01):
    """Computes unique physical curved runs and atoms via analytic circular interval union (PAC-1 T05, T09, E08).
    
    Groups curved walls on the same level by circle identity within physical tolerance budget
    (delta_center <= epsilon, delta_radius <= epsilon), normalizes angular sweep intervals onto [0, 2*pi),
    and computes 1D interval union, correctly handling partial overlaps, duplicate arcs, and reversed sweeps.
    """
    curved_walls = [
        w for w in lvl_walls
        if abs(getattr(w, "arc_extent", 0.0)) >= 1e-4 and w.chord_length > 1e-4
    ]
    if not curved_walls:
        return 0.0, []

    TWO_PI = 2.0 * math.pi
    groups = []
    for w in curved_walls:
        circle = _resolve_curved_wall_circle(w)
        if not circle:
            continue
        cx, cy, r = circle
        matched = False
        for g in groups:
            dist_c = math.hypot(g["cx"] - cx, g["cy"] - cy)
            diff_r = abs(g["r"] - r)
            if (dist_c + diff_r) <= epsilon:
                g["walls"].append(w)
                matched = True
                break
        if not matched:
            groups.append({"cx": cx, "cy": cy, "r": r, "walls": [w]})

    total_length_cm = 0.0
    unique_segments = []

    for g in groups:
        cx, cy, r = g["cx"], g["cy"], g["r"]
        intervals = []
        for w in g["walls"]:
            theta = math.radians(w.arc_extent)
            phi1 = math.atan2(w.y_start - cy, w.x_start - cx)
            phi2 = phi1 - theta
            start_angle = phi2 if theta > 0 else phi1
            span = abs(theta)
            start_norm = start_angle % TWO_PI
            end_norm = start_norm + span
            if end_norm <= TWO_PI:
                intervals.append((start_norm, end_norm, w))
            else:
                intervals.append((start_norm, TWO_PI, w))
                intervals.append((0.0, end_norm - TWO_PI, w))

        if not intervals:
            continue

        intervals.sort(key=lambda x: x[0])
        merged = []
        for a, b, w in intervals:
            if not merged:
                merged.append([a, b, w])
            else:
                if a <= merged[-1][1] + 1e-7:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b, w])

        if len(merged) > 1 and abs(merged[-1][1] - TWO_PI) < 1e-7 and abs(merged[0][0]) < 1e-7:
            first = merged.pop(0)
            merged[-1][1] = TWO_PI + first[1]

        for m_item in merged:
            a, b, rep_w = m_item[0], m_item[1], m_item[2]
            seg_angle = min(TWO_PI, b - a)
            seg_len = r * seg_angle
            total_length_cm += seg_len

            if len(g["walls"]) == 1 and seg_angle < (math.pi - 1e-4):
                orig_w = g["walls"][0]
                unique_segments.append({
                    "x_start": round(orig_w.x_start, 2),
                    "y_start": round(orig_w.y_start, 2),
                    "x_end": round(orig_w.x_end, 2),
                    "y_end": round(orig_w.y_end, 2),
                    "length_m": round(orig_w.length / 100.0, 3),
                    "arc_extent": orig_w.arc_extent,
                    "is_curved": True,
                    "level_id": orig_w.level_id,
                    "wall_id": orig_w.id,
                })
            else:
                # PAC-1 T05, T09, E08, R16-P1-02, R17-P1-02: Split any arc with span >= pi into n supported subintervals preserving exact gap
                n = max(1, math.ceil((seg_angle - 1e-7) / math.pi))
                sub_span = seg_angle / n
                for i in range(n):
                    p_start_angle = a + i * sub_span
                    p_end_angle = a + (i + 1) * sub_span
                    phi_s = p_start_angle % TWO_PI
                    phi_e = p_end_angle % TWO_PI
                    xs = cx + r * math.cos(phi_e if rep_w.arc_extent > 0 else phi_s)
                    ys = cy + r * math.sin(phi_e if rep_w.arc_extent > 0 else phi_s)
                    xe = cx + r * math.cos(phi_s if rep_w.arc_extent > 0 else phi_e)
                    ye = cy + r * math.sin(phi_s if rep_w.arc_extent > 0 else phi_e)
                    sub_len = r * sub_span
                    extent_deg = math.degrees(sub_span) if rep_w.arc_extent > 0 else -math.degrees(sub_span)
                    wid = f"{rep_w.id}_arc{i+1}" if n > 1 else rep_w.id
                    unique_segments.append({
                        "x_start": round(xs, 2),
                        "y_start": round(ys, 2),
                        "x_end": round(xe, 2),
                        "y_end": round(ye, 2),
                        "length_m": round(sub_len / 100.0, 3),
                        "arc_extent": extent_deg,
                        "is_curved": True,
                        "level_id": rep_w.level_id,
                        "wall_id": wid,
                    })

    return total_length_cm, unique_segments


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

MAX_ZIP_ENTRIES = 10000
MAX_ZIP_UNCOMPRESSED_SIZE = 100 * 1024 * 1024  # 100 MiB
MAX_ZIP_SINGLE_ENTRY_SIZE = 25 * 1024 * 1024   # 25 MiB


def validate_resource_name(name: str) -> str:
    """Validates entry name against path traversal, drive prefixes, empty or dot components (PAC-1 N08)."""
    import ntpath
    drive, _ = ntpath.splitdrive(name)
    if drive:
        raise ValueError(f"Drive-prefixed path in ZIP entry: {name}")
    if "\\" in name:
        raise ValueError(f"Backslash in ZIP entry name: {name}")
    if name.startswith("/"):
        raise ValueError(f"Absolute path in ZIP entry: {name}")
    parts = name.split("/")
    if any(p in (".", "..", "") for p in parts):
        raise ValueError(f"Dangerous path traversal, dot or empty component in ZIP entry: {name}")
    return name


def validate_sh3d_zip_archive(zf):
    """Bounded ZIP archive policies (PAC-1 N08): entry limits, max size, traversal protection, duplicate prevention."""
    infolist = zf.infolist()
    if len(infolist) > MAX_ZIP_ENTRIES:
        raise ValueError(f"ZIP archive exceeds max entry limit: {len(infolist)} > {MAX_ZIP_ENTRIES}")
    seen_names = set()
    home_xml_count = 0
    total_uncompressed = 0
    for info in infolist:
        name = info.filename
        validate_resource_name(name)
        if name in seen_names:
            raise ValueError(f"Duplicate entry in ZIP archive: {name}")
        seen_names.add(name)
        if name == "Home.xml":
            home_xml_count += 1
        if info.file_size > MAX_ZIP_SINGLE_ENTRY_SIZE:
            raise ValueError(f"ZIP entry exceeds single-file size limit: {name} ({info.file_size} bytes)")
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_ZIP_UNCOMPRESSED_SIZE:
            raise ValueError(f"ZIP archive exceeds total uncompressed size limit: {total_uncompressed} bytes")
    if home_xml_count != 1:
        raise ValueError(f"Expected exactly one 'Home.xml' entry, found {home_xml_count}")


class HomeModel:
    """Core in-memory representation of a Sweet Home 3D project."""

    def __init__(self, name="New Home", wall_height=280.0, version="7400"):
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
            raise EntityConflictError(f"Wall with ID '{wall_id}' already exists in project.")
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

            # Gate T01: Check for existing coincident physical wall segment on the same level
            existing_wall = None
            for w in self.walls.values():
                if w.level_id != level_id:
                    continue
                d_fwd = math.hypot(w.x_start - p1[0], w.y_start - p1[1]) + math.hypot(w.x_end - p2[0], w.y_end - p2[1])
                d_rev = math.hypot(w.x_start - p2[0], w.y_start - p2[1]) + math.hypot(w.x_end - p1[0], w.y_end - p1[1])
                if d_fwd < 1.0 or d_rev < 1.0:
                    existing_wall = w
                    break

            if existing_wall is not None:
                created.append(existing_wall)
                continue

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

    def _prepare_wall_edit(self, wall_id):
        wall_id = str(wall_id)
        if wall_id not in self.walls:
            raise ValidationRejectedError(f"Wall '{wall_id}' not found in project.")
        wall = self.walls[wall_id]

        def finite(value, label):
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValidationRejectedError(f"{label} must be numeric") from exc
            if not math.isfinite(number):
                raise ValidationRejectedError(f"{label} must be finite")
            return number

        finite(wall.x_start, "wall x_start")
        finite(wall.y_start, "wall y_start")
        finite(wall.x_end, "wall x_end")
        finite(wall.y_end, "wall y_end")
        th = finite(wall.thickness, "wall thickness")
        if th <= 0:
            raise ValidationRejectedError("wall thickness must be positive")
        h0 = finite(wall.height, "wall height")
        if h0 <= 0:
            raise ValidationRejectedError("wall height must be positive")
        finite(wall.arc_extent, "wall arc_extent")

        L = finite(wall.length, "wall length")
        if L <= 0:
            raise ValidationRejectedError("wall length must be positive")

        h1_val = wall.extra_attribs.get("heightAtEnd", wall.height) if wall.extra_attribs else wall.height
        h1 = finite(h1_val, "heightAtEnd")
        if h1 <= 0:
            raise ValidationRejectedError("heightAtEnd must be positive")

        hosted_openings = [op for op in self.openings.values() if op.wall_id == wall_id]
        door_poses = {}
        for op in hosted_openings:
            o = finite(op.offset_cm, f"opening {op.id} offset")
            w = finite(op.width_cm, f"opening {op.id} width")
            finite(op.height_cm, f"opening {op.id} height")
            finite(op.elevation_cm, f"opening {op.id} elevation")
            if o < -1e-4 or w <= 0 or o + w > L + 1e-4:
                raise ValidationRejectedError(f"opening {op.id} interval outside host")
            try:
                pose = op.get_effective_geometry(wall)
                door_poses[op.id] = pose
            except Exception as exc:
                raise ValidationRejectedError(f"opening {op.id} physical pose unresolvable") from exc

        return wall, L, h1, door_poses

    def reverse_wall(self, wall_id):
        """Reverses a wall's direction while preserving physical opening geometry and normalizing face intent (PAC-1 T06, R30, R31)."""
        wall, total_len, h1, door_poses = self._prepare_wall_edit(wall_id)

        # Atomic rollback snapshots across all direct objects (PAC-1 T06, R30, R31)
        old_walls, old_openings = dict(self.walls), dict(self.openings)
        states = [(obj, copy.deepcopy(obj.__dict__))
                  for obj in [*old_walls.values(), *old_openings.values()]]

        try:
            # Update sloped height if present
            if "heightAtEnd" in wall.extra_attribs:
                old_h = wall.height
                wall.height = h1
                wall.extra_attribs["heightAtEnd"] = str(round(old_h, 4))

            # Invert coordinates
            wall.x_start, wall.x_end = wall.x_end, wall.x_start
            wall.y_start, wall.y_end = wall.y_end, wall.y_start
            if wall.arc_extent != 0.0:
                wall.arc_extent = -wall.arc_extent

            # Re-anchor hosted openings preserving physical world geometry
            for op in self.openings.values():
                if op.wall_id == wall_id:
                    new_offset = total_len - op.offset_cm - op.width_cm
                    if new_offset < -1e-4 or new_offset + op.width_cm > total_len + 1e-4:
                        raise ValidationRejectedError(f"Opening '{op.id}' reversed interval out of bounds: {new_offset}")
                    op.offset_cm = round(new_offset, 4)
                    if op.id in door_poses and door_poses[op.id]:
                        p = door_poses[op.id]
                        op.x_cm = p["center"][0]
                        op.y_cm = p["center"][1]
                        op.angle_deg = p["angle_deg"]

            # Normalize physical face intent (swap left and right finishes)
            side_pairs = [
                ("leftSideColor", "rightSideColor"),
                ("leftSideTexture", "rightSideTexture"),
                ("leftSideShininess", "rightSideShininess"),
                ("leftSideBaseboard", "rightSideBaseboard"),
                ("wallAtStart", "wallAtEnd"),
            ]
            for k1, k2 in side_pairs:
                v1 = wall.extra_attribs.get(k1)
                v2 = wall.extra_attribs.get(k2)
                if v1 is not None or v2 is not None:
                    if v2 is not None:
                        wall.extra_attribs[k1] = v2
                    else:
                        wall.extra_attribs.pop(k1, None)
                    if v1 is not None:
                        wall.extra_attribs[k2] = v1
                    else:
                        wall.extra_attribs.pop(k2, None)

            # Normalize native XML child attribute roles (texture and baseboard)
            new_children = []
            for ch in wall.extra_children:
                try:
                    elem = ET.fromstring(ch)
                    attr = elem.get("attribute")
                    ROLE_SWAP = {
                        "leftSideTexture": "rightSideTexture",
                        "rightSideTexture": "leftSideTexture",
                        "leftSideBaseboard": "rightSideBaseboard",
                        "rightSideBaseboard": "leftSideBaseboard",
                        "leftSideColor": "rightSideColor",
                        "rightSideColor": "leftSideColor",
                        "leftSideShininess": "rightSideShininess",
                        "rightSideShininess": "leftSideShininess",
                    }
                    if attr in ROLE_SWAP:
                        elem.set("attribute", ROLE_SWAP[attr])
                        new_children.append(ET.tostring(elem, encoding="unicode"))
                    else:
                        new_children.append(ch)
                except Exception:
                    new_children.append(ch)
            wall.extra_children = new_children

            return wall
        except Exception:
            # Atomic rollback on failure restoring all touched objects
            for obj, state in states:
                obj.__dict__.clear()
                obj.__dict__.update(state)
            self.walls.clear(); self.walls.update(old_walls)
            self.openings.clear(); self.openings.update(old_openings)
            raise

    def split_wall(self, wall_id, split_offset_cm, new_wall_id=None):
        """Splits a wall into two contiguous collinear segments at split_offset_cm (PAC-1 T06, R30, R31).

        Re-anchors hosted openings and retargets native joins. Rejects split across an opening or non-finite inputs.
        """
        wall, total_len, h1, door_poses = self._prepare_wall_edit(wall_id)

        try:
            split_s = float(split_offset_cm)
        except (ValueError, TypeError) as exc:
            raise ValidationRejectedError(f"Invalid split offset: {exc}") from exc
        if not math.isfinite(split_s):
            raise ValidationRejectedError(f"Split offset must be finite number, got {split_offset_cm}")

        if split_s <= 1e-4 or split_s >= total_len - 1e-4:
            raise ValidationRejectedError(f"Split offset {split_s} must be strictly inside wall (length {total_len})")

        # Check if split point falls inside any opening
        for op in self.openings.values():
            if op.wall_id == wall.id:
                op_start = op.offset_cm
                op_end = op.offset_cm + op.width_cm
                if op_start < split_s < op_end:
                    raise ValidationRejectedError(f"Cannot split wall across opening '{op.id}' [{op_start}, {op_end}]")

        new_id = str(new_wall_id or f"{wall.id}_seg2")
        if new_id in self.walls:
            raise EntityConflictError(f"Wall ID '{new_id}' already exists in project.")

        # Atomic rollback snapshot across direct objects (PAC-1 T06, R30, R31)
        old_walls, old_openings = dict(self.walls), dict(self.openings)
        states = [(obj, copy.deepcopy(obj.__dict__))
                  for obj in [*old_walls.values(), *old_openings.values()]]

        try:
            old_h0 = wall.height
            old_h1 = h1
            ha = old_h0 + (old_h1 - old_h0) * (split_s / total_len)

            split_pt = wall.point_at_offset(split_s)
            old_x_end = wall.x_end
            old_y_end = wall.y_end

            # Create second segment: from split_pt to old end
            wall2 = Wall(
                wall_id=new_id,
                x_start=split_pt[0],
                y_start=split_pt[1],
                x_end=old_x_end,
                y_end=old_y_end,
                thickness=wall.thickness,
                height=ha,
                level_id=wall.level_id,
                arc_extent=0.0,
                extra_attribs=dict(wall.extra_attribs),
                extra_children=list(wall.extra_children),
            )
            if wall.arc_extent != 0.0:
                frac = (total_len - split_s) / total_len
                wall2.arc_extent = round(wall.arc_extent * frac, 4)
                wall.arc_extent = round(wall.arc_extent * (split_s / total_len), 4)

            # Update slope on wall2 and wall1
            has_slope = "heightAtEnd" in wall.extra_attribs
            if has_slope:
                wall2.extra_attribs["heightAtEnd"] = str(round(old_h1, 4))
                wall.extra_attribs["heightAtEnd"] = str(round(ha, 4))

            # Truncate original wall: ends at split_pt
            wall.x_end = split_pt[0]
            wall.y_end = split_pt[1]

            # Retarget neighbor joins
            if "wallAtEnd" in wall.extra_attribs:
                end_neighbor = wall.extra_attribs.pop("wallAtEnd")
                wall2.extra_attribs["wallAtEnd"] = end_neighbor
                if end_neighbor in self.walls:
                    nbr = self.walls[end_neighbor]
                    if nbr.extra_attribs.get("wallAtStart") == wall_id:
                        nbr.extra_attribs["wallAtStart"] = new_id
                    elif nbr.extra_attribs.get("wallAtEnd") == wall_id:
                        nbr.extra_attribs["wallAtEnd"] = new_id

            wall.extra_attribs["wallAtEnd"] = new_id
            wall2.extra_attribs["wallAtStart"] = wall_id

            # Retarget any other walls in the project that had wallAtStart or wallAtEnd pointing to wall_id's old end
            for other_id, other in self.walls.items():
                if other_id != wall_id and other_id != new_id:
                    if other.extra_attribs.get("wallAtStart") == wall_id:
                        if math.hypot(other.x_start - old_x_end, other.y_start - old_y_end) < 1.0:
                            other.extra_attribs["wallAtStart"] = new_id
                    if other.extra_attribs.get("wallAtEnd") == wall_id:
                        if math.hypot(other.x_end - old_x_end, other.y_end - old_y_end) < 1.0:
                            other.extra_attribs["wallAtEnd"] = new_id

            self.walls[new_id] = wall2

            # Re-assign openings beyond split point to wall2
            for op in self.openings.values():
                if op.wall_id == wall_id and op.offset_cm >= split_s:
                    op.wall_id = new_id
                    op.offset_cm = round(op.offset_cm - split_s, 4)
                    if op.id in door_poses and door_poses[op.id]:
                        p = door_poses[op.id]
                        op.x_cm = p["center"][0]
                        op.y_cm = p["center"][1]
                        op.angle_deg = p["angle_deg"]

            return wall, wall2
        except Exception:
            # Atomic rollback restoring direct objects and maps
            for obj, state in states:
                obj.__dict__.clear()
                obj.__dict__.update(state)
            self.walls.clear(); self.walls.update(old_walls)
            self.openings.clear(); self.openings.update(old_openings)
            raise


    # Room methods
    def add_room(self, room_id, name, points, level_id="level1", floor_color="#EAEAEA",
                 with_walls=False, wall_thickness=15.0, extra_attribs=None, extra_children=None, replace=False):
        """Adds an architectural room polygon, optionally generating perimeter boundary walls."""
        room_id = str(room_id)
        if room_id in self.rooms and not replace:
            raise EntityConflictError(f"Room with ID '{room_id}' already exists in project.")
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
            raise EntityConflictError(f"Opening with ID '{opening_id}' already exists in project.")
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
                      angle_deg=0.0, level_id="level1", room_id=None, extra_attribs=None, extra_children=None, replace=False):
        furniture_id = str(furniture_id)
        if furniture_id in self.furniture and not replace:
            raise EntityConflictError(f"Furniture with ID '{furniture_id}' already exists in project.")
        f = Furniture(furniture_id, name, catalog_id, x_cm, y_cm, elevation_cm,
                      width_cm, depth_cm, height_cm, angle_deg, level_id,
                      room_id=room_id, extra_attribs=extra_attribs, extra_children=extra_children)
        self.furniture[f.id] = f
        return f

    def remove_furniture(self, furniture_id):
        if furniture_id in self.furniture:
            del self.furniture[furniture_id]
            return True
        return False

    # Dimension methods
    def add_dimension(self, dimension_id, x_start, y_start, x_end, y_end, offset_cm=30.0,
                      level_id="level1", elevation_cm=0.0, elevation_start_cm=None, elevation_end_cm=None,
                      extra_attribs=None, extra_children=None, replace=False):
        dimension_id = str(dimension_id)
        if dimension_id in self.dimensions and not replace:
            raise EntityConflictError(f"Dimension line with ID '{dimension_id}' already exists in project.")
        dim = DimensionLine(dimension_id, x_start, y_start, x_end, y_end, offset_cm, level_id,
                            elevation_cm=elevation_cm, elevation_start_cm=elevation_start_cm,
                            elevation_end_cm=elevation_end_cm,
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

    def get_noded_wall_segments(self, level_id=None):
        """Derives the physical noded wall arrangement across all intersecting/overlapping walls (PAC-1 T01-T03, T05).
        
        Splits collinear overlapping walls and T/X-junctions into unique non-overlapping physical atoms,
        and includes unique curved wall segments with their analytic arc length and extent.
        """
        try:
            from shapely.geometry import LineString, MultiLineString
            from shapely.ops import unary_union
        except ImportError:
            return []
        
        target_levels = [level_id] if level_id else [lvl.id for lvl in self.levels]
        if not target_levels:
            target_levels = list(set(w.level_id for w in self.walls.values())) or ["level1"]

        noded = []
        for lvl in target_levels:
            lvl_walls = [w for w in self.walls.values() if w.level_id == lvl]
            straight_lines = []
            for w in lvl_walls:
                if abs(getattr(w, "arc_extent", 0.0)) < 1e-4 and w.chord_length > 1e-4:
                    straight_lines.append(LineString([(w.x_start, w.y_start), (w.x_end, w.y_end)]))
            
            if straight_lines:
                u = unary_union(straight_lines)
                geoms = u.geoms if hasattr(u, "geoms") else [u]
                for g in geoms:
                    if isinstance(g, LineString) and g.length > 1e-4:
                        coords = list(g.coords)
                        for i in range(len(coords) - 1):
                            p1, p2 = coords[i], coords[i + 1]
                            noded.append({
                                "x_start": round(p1[0], 2),
                                "y_start": round(p1[1], 2),
                                "x_end": round(p2[0], 2),
                                "y_end": round(p2[1], 2),
                                "length_m": round(math.hypot(p2[0] - p1[0], p2[1] - p1[1]) / 100.0, 3),
                                "arc_extent": 0.0,
                                "is_curved": False,
                                "level_id": lvl,
                            })
            
            # Include unique curved walls with analytic circular interval union
            _, curved_segments = _compute_unique_curved_runs(lvl_walls)
            noded.extend(curved_segments)

        return noded

    @property
    def total_noded_wall_length_m(self):
        """Total run of unique physical walls in linear meters without double-counting overlaps (PAC-1 T01-T03, T05, T09).
        
        Separates physical wall runs by level and computes exact analytic arc lengths for unique curved walls.
        """
        try:
            from shapely.geometry import LineString
            from shapely.ops import unary_union
            
            target_levels = [lvl.id for lvl in self.levels]
            if not target_levels:
                target_levels = list(set(w.level_id for w in self.walls.values())) or ["level1"]

            total_cm = 0.0
            for lvl in target_levels:
                lvl_walls = [w for w in self.walls.values() if w.level_id == lvl]
                straight_lines = [
                    LineString([(w.x_start, w.y_start), (w.x_end, w.y_end)])
                    for w in lvl_walls
                    if abs(getattr(w, "arc_extent", 0.0)) < 1e-4 and w.chord_length > 1e-4
                ]
                if straight_lines:
                    total_cm += unary_union(straight_lines).length
                
                # Add exact analytic arc length for unique curved walls via interval union
                curved_cm, _ = _compute_unique_curved_runs(lvl_walls)
                total_cm += curved_cm

            return round(total_cm / 100.0, 2)
        except Exception:
            return self.total_wall_length_m

    def build_room_face_topology(self):
        """Calculates atomic straight wall edges and two-sided room face ownership (PAC-1 T01-T06, R25)."""
        from cli_anything.sweethome3d.core.topology import build_room_face_topology
        return build_room_face_topology(self)

    def node_walls(self, level_id=None):
        """Replaces collinear overlapping and intersecting walls with their noded physical atoms (PAC-1 T01-T04, T06).
        
        Preserves wall attributes and children, detects conflicting overlapping walls and face intents,
        remaps hosted openings atomically with signed station transforms, preserves exact physical opening geometry,
        and guarantees atomic rollback if any validation or conversion error occurs.
        """
        try:
            from shapely.geometry import LineString
            from shapely.ops import unary_union
        except ImportError:
            return 0

        target_levels = [level_id] if level_id else [lvl.id for lvl in self.levels]
        if not target_levels:
            target_levels = list(set(w.level_id for w in self.walls.values())) or ["level1"]

        # Preserve snapshot for full atomicity across direct model API calls
        orig_walls = dict(self.walls)
        orig_openings_dict = dict(self.openings)
        orig_openings_state = {
            op_id: {
                "wall_id": op.wall_id,
                "offset_cm": op.offset_cm,
                "x_cm": getattr(op, "x_cm", None),
                "y_cm": getattr(op, "y_cm", None),
                "angle_deg": getattr(op, "angle_deg", None),
                "width_cm": op.width_cm,
                "height_cm": op.height_cm,
                "opening_type": op.opening_type,
                "swing": op.swing,
                "elevation_cm": op.elevation_cm,
                "depth_cm": op.depth_cm,
                "level_id": op.level_id,
                "extra_attribs": dict(op.extra_attribs),
                "extra_children": list(op.extra_children),
            }
            for op_id, op in self.openings.items()
        }

        try:
            total_noded = 0
            # Reserve all entity IDs across all levels and curves
            used_ids = set(self.walls.keys())

            for lvl in target_levels:
                lvl_walls = [
                    w for w in self.walls.values()
                    if w.level_id == lvl and abs(getattr(w, "arc_extent", 0.0)) < 1e-4 and w.chord_length > 1e-4
                ]
                if not lvl_walls:
                    continue

                lines = [LineString([(w.x_start, w.y_start), (w.x_end, w.y_end)]) for w in lvl_walls]
                u = unary_union(lines)
                geoms = list(u.geoms) if hasattr(u, "geoms") else [u]

                raw_atoms = []
                for g in geoms:
                    if isinstance(g, LineString) and g.length > 1e-4:
                        coords = list(g.coords)
                        for i in range(len(coords) - 1):
                            p1, p2 = coords[i], coords[i + 1]
                            raw_atoms.append((p1, p2))

                if not raw_atoms:
                    continue

                # Identify source walls for each atom and assert attribute compatibility
                atom_sources = []
                for p1, p2 in raw_atoms:
                    atom_line = LineString([p1, p2])
                    sources = []
                    for w in lvl_walls:
                        w_line = LineString([(w.x_start, w.y_start), (w.x_end, w.y_end)])
                        if w_line.distance(atom_line) < 1e-4 and w_line.intersection(atom_line).length > 1e-4:
                            sources.append(w)
                    if not sources:
                        continue
                    
                    # Check attribute and face intent compatibility across overlapping sources
                    base_w = sources[0]
                    dx1, dy1 = base_w.x_end - base_w.x_start, base_w.y_end - base_w.y_start

                    merged_attribs = dict(base_w.extra_attribs)
                    merged_children = list(base_w.extra_children)

                    for other in sources[1:]:
                        if abs(other.thickness - base_w.thickness) > 1e-3:
                            raise ValueError(f"Conflicting wall thickness on overlapping segment between {base_w.id} ({base_w.thickness}cm) and {other.id} ({other.thickness}cm)")
                        if abs(other.height - base_w.height) > 1e-3:
                            raise ValueError(f"Conflicting wall height on overlapping segment between {base_w.id} ({base_w.height}cm) and {other.id} ({other.height}cm)")

                        # Compare and merge face intent in normalized physical orientation
                        dx2, dy2 = other.x_end - other.x_start, other.y_end - other.y_start
                        aligned = (dx1 * dx2 + dy1 * dy2) > 0

                        SIDE_PAIRS = [
                            ('leftSideColor', 'rightSideColor'),
                            ('leftSideShininess', 'rightSideShininess'),
                            ('leftSideTexture', 'rightSideTexture'),
                            ('leftSideBaseboard', 'rightSideBaseboard'),
                        ]
                        side_keys = set()
                        for left_k, right_k in SIDE_PAIRS:
                            side_keys.add(left_k)
                            side_keys.add(right_k)
                            if aligned:
                                other_l = other.extra_attribs.get(left_k)
                                other_r = other.extra_attribs.get(right_k)
                            else:
                                other_l = other.extra_attribs.get(right_k)
                                other_r = other.extra_attribs.get(left_k)

                            curr_l = merged_attribs.get(left_k)
                            curr_r = merged_attribs.get(right_k)

                            if curr_l and other_l and curr_l != other_l:
                                raise ValueError(f"Conflicting wall face color/material intent ('{left_k}') on overlapping segment between {base_w.id} and {other.id}")
                            if curr_r and other_r and curr_r != other_r:
                                raise ValueError(f"Conflicting wall face color/material intent ('{right_k}') on overlapping segment between {base_w.id} and {other.id}")

                            if other_l:
                                merged_attribs[left_k] = other_l
                            if other_r:
                                merged_attribs[right_k] = other_r

                        # Merge other non-side-specific attributes
                        for k, v in other.extra_attribs.items():
                            if k not in side_keys:
                                if k in merged_attribs and merged_attribs[k] != v:
                                    raise ValueError(f"Conflicting attribute '{k}' on overlapping segment between {base_w.id} and {other.id}")
                                merged_attribs[k] = v

                        # PAC-1 T04, T06, N09, R16-P1-01, R17-P1-01: Merge extra children with semantic property validation and side-role normalization
                        def _normalize_child_xml(child_str, is_aligned):
                            if is_aligned:
                                return child_str
                            try:
                                elem = ET.fromstring(child_str) if isinstance(child_str, str) else child_str
                                attr_val = elem.attrib.get("attribute")
                                if attr_val:
                                    role_map = {
                                        "leftSideTexture": "rightSideTexture",
                                        "rightSideTexture": "leftSideTexture",
                                        "leftSideBaseboard": "rightSideBaseboard",
                                        "rightSideBaseboard": "leftSideBaseboard",
                                        "leftSideShininess": "rightSideShininess",
                                        "rightSideShininess": "leftSideShininess",
                                        "leftSideColor": "rightSideColor",
                                        "rightSideColor": "leftSideColor",
                                    }
                                    if attr_val in role_map:
                                        elem.attrib["attribute"] = role_map[attr_val]
                                        return ET.tostring(elem, encoding="unicode")
                            except Exception:
                                pass
                            return child_str

                        def _extract_prop_name_val(child_str):
                            try:
                                elem = ET.fromstring(child_str) if isinstance(child_str, str) else child_str
                                if elem.tag == "property" and "name" in elem.attrib:
                                    return elem.attrib["name"], elem.attrib.get("value")
                            except Exception:
                                pass
                            return None

                        prop_map = {}
                        for ch in merged_children:
                            info = _extract_prop_name_val(ch)
                            if info:
                                prop_map[info[0]] = info[1]

                        for child in other.extra_children:
                            norm_child = _normalize_child_xml(child, aligned)
                            info = _extract_prop_name_val(norm_child)
                            if info:
                                p_name, p_val = info
                                if p_name in prop_map:
                                    if prop_map[p_name] != p_val:
                                        raise ValueError(f"Conflicting wall property '{p_name}' ('{prop_map[p_name]}' vs '{p_val}') on overlapping segment between {base_w.id} and {other.id}")
                                    # identical value coalesces, do not duplicate
                                else:
                                    prop_map[p_name] = p_val
                                    merged_children.append(norm_child)
                            else:
                                if norm_child not in merged_children:
                                    merged_children.append(norm_child)

                    # Orient atom to match base source wall direction
                    atom_dx, atom_dy = p2[0] - p1[0], p2[1] - p1[1]
                    if (atom_dx * dx1 + atom_dy * dy1) < 0:
                        p1, p2 = p2, p1
                    
                    atom_sources.append((p1, p2, sources, merged_attribs, merged_children))

                # Build wall_id -> list of (atom_idx, s_start, s_end, is_aligned)
                wall_to_atoms = {w.id: [] for w in lvl_walls}
                for a_idx, (p1, p2, sources, merged_attribs, merged_children) in enumerate(atom_sources):
                    for w in sources:
                        dx, dy = w.x_end - w.x_start, w.y_end - w.y_start
                        L = w.chord_length
                        ux, uy = dx / L, dy / L
                        proj1 = (p1[0] - w.x_start) * ux + (p1[1] - w.y_start) * uy
                        proj2 = (p2[0] - w.x_start) * ux + (p2[1] - w.y_start) * uy
                        s_start, s_end = min(proj1, proj2), max(proj1, proj2)
                        atom_dx, atom_dy = p2[0] - p1[0], p2[1] - p1[1]
                        is_aligned = (dx * atom_dx + dy * atom_dy) > 0
                        wall_to_atoms[w.id].append((a_idx, s_start, s_end, is_aligned))

                # Remap openings and check if any opening spans a split point
                opening_remaps = {}
                for op in self.openings.values():
                    if op.wall_id in wall_to_atoms:
                        w = self.walls[op.wall_id]
                        geom = op.get_effective_geometry(w)
                        op_start = op.offset_cm
                        op_end = op.offset_cm + op.width_cm
                        matched_atom = None
                        for a_idx, s_start, s_end, is_aligned in wall_to_atoms[w.id]:
                            if op_start >= s_start - 1e-3 and op_end <= s_end + 1e-3:
                                matched_atom = (a_idx, s_start, s_end, is_aligned, geom)
                                break
                        if matched_atom is None:
                            raise ValueError(f"Cannot node walls: opening '{op.id}' ({op.name}) on wall '{w.id}' spans across split point or extends beyond wall boundaries")
                        opening_remaps[op.id] = matched_atom

                # Allocate unique atom IDs, prioritizing preservation of unchanged walls
                atom_ids = []
                # Remove source walls on this level from used_ids so unchanged IDs can be preserved
                for w in lvl_walls:
                    used_ids.discard(w.id)

                for a_idx, (p1, p2, sources, merged_attribs, merged_children) in enumerate(atom_sources):
                    if len(sources) == 1 and len(wall_to_atoms[sources[0].id]) == 1:
                        cand_id = sources[0].id
                    elif len(sources) == 1:
                        cand_id = f"{sources[0].id}_{a_idx + 1}"
                    else:
                        cand_id = f"w_{lvl}_node_{a_idx + 1}"
                    
                    suffix_idx = 1
                    final_id = cand_id
                    while final_id in used_ids:
                        final_id = f"{cand_id}_{suffix_idx}"
                        suffix_idx += 1
                    used_ids.add(final_id)
                    atom_ids.append(final_id)

                # Delete old walls on this level
                for w in lvl_walls:
                    del self.walls[w.id]

                # Add new noded walls with preserved source attributes and children
                for a_idx, (p1, p2, sources, merged_attribs, merged_children) in enumerate(atom_sources):
                    base_w = sources[0]
                    new_id = atom_ids[a_idx]
                    self.add_wall(
                        new_id, round(p1[0], 2), round(p1[1], 2), round(p2[0], 2), round(p2[1], 2),
                        thickness=base_w.thickness, height=base_w.height, level_id=lvl,
                        extra_attribs=merged_attribs,
                        extra_children=merged_children
                    )
                    total_noded += 1

                # Remap openings with signed station transform and physical pose preservation
                for op_id, (a_idx, s_start, s_end, is_aligned, geom) in opening_remaps.items():
                    op = self.openings[op_id]
                    op.wall_id = atom_ids[a_idx]
                    if is_aligned:
                        op.offset_cm = round(op.offset_cm - s_start, 2)
                    else:
                        # Antiparallel source wall: reverse station interval
                        op.offset_cm = round(s_end - (op.offset_cm + op.width_cm), 2)
                    if geom:
                        op.x_cm = round(geom["center"][0], 4)
                        op.y_cm = round(geom["center"][1], 4)
                        op.angle_deg = geom["angle_deg"]

            # Enforce model validity before committing
            val = self.validate()
            if not val["valid"]:
                raise ValueError(f"Wall noding produced invalid model: {val['errors']}")

            return total_noded

        except Exception:
            # Atomic rollback on any failure: restore walls dictionary and restore opening instances in-place
            self.walls = orig_walls
            for op_id, state in orig_openings_state.items():
                if op_id in self.openings:
                    op = self.openings[op_id]
                    op.wall_id = state["wall_id"]
                    op.offset_cm = state["offset_cm"]
                    op.x_cm = state["x_cm"]
                    op.y_cm = state["y_cm"]
                    op.angle_deg = state["angle_deg"]
                    op.width_cm = state["width_cm"]
                    op.height_cm = state["height_cm"]
                    op.opening_type = state["opening_type"]
                    op.swing = state["swing"]
                    op.elevation_cm = state["elevation_cm"]
                    op.depth_cm = state["depth_cm"]
                    op.level_id = state["level_id"]
                    op.extra_attribs = dict(state["extra_attribs"])
                    op.extra_children = list(state["extra_children"])
            self.openings = orig_openings_dict
            raise

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

        # Gate T08 / Section 6.4: Door deduction for skirting accounts for faces (interior shared wall = 2 faces, exterior = 1 face)
        # Elevated openings with sill >= 10.0 cm do NOT break the baseboard profile (deduction = 0)
        def _get_door_faces(op):
            if op.opening_type != "door":
                return 0
            if op.elevation_cm >= 10.0:
                return 0
            if not op.wall_id or op.wall_id not in self.walls:
                return 1
            wall = self.walls[op.wall_id]
            faces = 0
            for r in self.rooms.values():
                if r.level_id != wall.level_id:
                    continue
                pts = r.points
                n = len(pts)
                for i in range(n):
                    p1 = pts[i]
                    p2 = pts[(i + 1) % n]
                    d_fwd = math.hypot(wall.x_start - p1[0], wall.y_start - p1[1]) + math.hypot(wall.x_end - p2[0], wall.y_end - p2[1])
                    d_rev = math.hypot(wall.x_start - p2[0], wall.y_start - p2[1]) + math.hypot(wall.x_end - p1[0], wall.y_end - p1[1])
                    if d_fwd < 5.0 or d_rev < 5.0:
                        faces += 1
                        break
            return max(1, faces)

        doors_deduction_m = sum(
            (op.width_cm / 100.0) * _get_door_faces(op)
            for op in self.openings.values()
            if op.opening_type == "door" and op.elevation_cm < 10.0
        )
        skirting_net_m = round(max(0.0, total_skirting_m - doors_deduction_m), 2)

        # Clear core area & clear face skirting (mitred inward clear core polygon - PAC-1 T07, R10-P1-01)
        clear_core_area_m2 = 0.0
        clear_face_skirting_gross_m = 0.0

        for r in self.rooms.values():
            clear_poly = r.get_clear_core_polygon(self.walls.values())
            if clear_poly is not None:
                if not clear_poly.is_empty:
                    clear_core_area_m2 += clear_poly.area / 10000.0
                    clear_face_skirting_gross_m += clear_poly.length / 100.0
                else:
                    # Infeasible clear core: 0.0 m² usable area (PAC-1 R11-P1-01)
                    pass
            else:
                clear_core_area_m2 += r.area_m2
                clear_face_skirting_gross_m += r.perimeter_cm / 100.0

        clear_face_skirting_net_m = round(max(0.0, clear_face_skirting_gross_m - doors_deduction_m), 2)

        # Net physical masonry volume via CSG vertical slab integration in world Z (PAC-1 T08, T09, R10-P1-02, R11-P1-02)
        try:
            from shapely.geometry import Polygon, LineString
            from shapely.ops import unary_union

            levels_dict = {lvl.id: lvl.elevation_cm for lvl in self.levels}
            z_breaks = set()
            wall_data = []
            wall_polys = {}
            for w in self.walls.values():
                lvl_elev = levels_dict.get(w.level_id, 0.0)
                w_z0 = lvl_elev
                w_z1 = lvl_elev + w.height
                z_breaks.add(w_z0)
                z_breaks.add(w_z1)
                t = w.thickness / 2.0
                if abs(w.arc_extent) > 1e-4 and w.length > 0:
                    steps = w.get_curve_sampling_steps()
                    pts = [w.point_at_offset(i * w.length / steps) for i in range(steps + 1)]
                    poly = LineString(pts).buffer(t, cap_style="square")
                else:
                    if w.chord_length < 1e-4:
                        continue
                    poly = LineString([(w.x_start, w.y_start), (w.x_end, w.y_end)]).buffer(t, cap_style="square")
                wall_data.append((w, w_z0, w_z1, poly))
                wall_polys[w.id] = poly

            opening_data = []
            for op in self.openings.values():
                w = self.walls.get(op.wall_id)
                if not w or w.id not in wall_polys:
                    continue
                lvl_elev = levels_dict.get(op.level_id or w.level_id, 0.0)
                op_z0 = lvl_elev + op.elevation_cm
                op_z1 = op_z0 + op.height_cm
                z_breaks.add(op_z0)
                z_breaks.add(op_z1)
                geom = op.get_effective_geometry(w)
                if geom:
                    p1 = geom["p1"]
                    p2 = geom["p2"]
                    t_cut = max(w.thickness * 1.5, 30.0)
                    op_poly_raw = LineString([p1, p2]).buffer(t_cut / 2.0, cap_style="flat")
                    # R11-P1-02: Clip opening cutout strictly to host wall solid footprint
                    op_poly = op_poly_raw.intersection(wall_polys[w.id])
                    opening_data.append((op, op_z0, op_z1, op_poly))

            sorted_z = sorted(z_breaks)
            total_net_vol = 0.0
            total_gross_vol = 0.0

            for i in range(len(sorted_z) - 1):
                z_a = sorted_z[i]
                z_b = sorted_z[i + 1]
                dh = z_b - z_a
                if dh < 1e-4:
                    continue
                mid_z = (z_a + z_b) / 2.0
                active_walls = [p for (w, z0, z1, p) in wall_data if z0 <= mid_z <= z1]
                if not active_walls:
                    continue
                u_wall = unary_union(active_walls)
                gross_area = u_wall.area / 10000.0
                total_gross_vol += gross_area * (dh / 100.0)

                active_openings = [p for (op, z0, z1, p) in opening_data if z0 <= mid_z <= z1]
                if active_openings:
                    u_cut = unary_union(active_openings)
                    net_geom = u_wall.difference(u_cut)
                    net_area = net_geom.area / 10000.0
                else:
                    net_area = gross_area
                total_net_vol += net_area * (dh / 100.0)

            gross_volume_m3 = round(total_gross_vol, 3)
            net_masonry_volume_m3 = round(total_net_vol, 3)
        except Exception:
            gross_volume_m3 = sum((w.length / 100.0) * (w.thickness / 100.0) * (w.height / 100.0) for w in self.walls.values())
            net_masonry_volume_m3 = gross_volume_m3

        summary = {
            "wall_perimeter_m": self.total_wall_length_m,
            "physical_wall_run_m": self.total_noded_wall_length_m,
            "wall_gross_area_m2": gross_wall_m2,
            "wall_net_area_m2": net_wall_masonry_m2,
            "net_masonry_volume_m3": net_masonry_volume_m3,
            "wall_paint_area_m2": interior_paint_m2,
            "floor_area_m2": total_floor_m2,
            "clear_core_area_m2": round(clear_core_area_m2, 3),
            "skirting_length_m": skirting_net_m,
            "reference_skirting_length_m": skirting_net_m,
            "clear_face_skirting_length_m": round(clear_face_skirting_net_m, 2),
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
                "net_masonry_volume_m3": net_masonry_volume_m3,
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
            if getattr(r, "holes", None):
                for h_idx, hole in enumerate(r.holes):
                    for idx, pt in enumerate(hole):
                        for val, name in [(pt[0], "x"), (pt[1], "y")]:
                            if not math.isfinite(val):
                                msg = f"Room '{r.id}' hole {h_idx} vertex {idx} has non-finite coordinate '{name}' ({val})."
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
            e_start = getattr(dim, "elevation_start_cm", dim.elevation_cm)
            e_end = getattr(dim, "elevation_end_cm", dim.elevation_cm)
            for val, name in [(dim.x_start, "x_start"), (dim.y_start, "y_start"), (dim.x_end, "x_end"), (dim.y_end, "y_end"),
                              (dim.offset_cm, "offset_cm"), (dim.elevation_cm, "elevation_cm"),
                              (e_start, "elevation_start_cm"), (e_end, "elevation_end_cm")]:
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
                if r.area_m2 <= 0.0:
                    msg2 = f"Room '{r.id}' ({r.name}) has zero or degenerate area ({r.area_m2} m²)."
                    errors.append(msg2)
                    findings.append({"code": "ZERO_AREA_ROOM", "severity": "error", "entity_ids": [r.id], "message": msg2})
                continue
            
            if r.level_id not in level_ids:
                msg = f"Room '{r.id}' references nonexistent level '{r.level_id}'."
                errors.append(msg)
                findings.append({"code": "MISSING_LEVEL_REFERENCE", "severity": "error", "entity_ids": [r.id], "message": msg})

            # PAC-1 C01, T07, A07, R20-P1-02: Validate complete room region topology before core geometry
            try:
                room_region = r.get_region_polygon()
            except ValueError as exc:
                exc_str = str(exc)
                msg = f"Room '{r.id}' ({r.name}) has invalid geometry: {exc_str}"
                errors.append(msg)
                err_code = "ROOM_SELF_INTERSECTION" if "self-intersection" in exc_str.lower() else "INVALID_ROOM_GEOMETRY"
                findings.append({"code": err_code, "severity": "error", "entity_ids": [r.id], "message": msg})
                if r.area_m2 <= 0.0:
                    msg2 = f"Room '{r.id}' ({r.name}) has zero or degenerate area ({r.area_m2} m²)."
                    errors.append(msg2)
                    findings.append({"code": "ZERO_AREA_ROOM", "severity": "error", "entity_ids": [r.id], "message": msg2})
                continue

            clear_poly = r.get_clear_core_polygon(self.walls.values())
            if clear_poly is not None and clear_poly.is_empty:
                msg = f"Room '{r.name}' ({r.id}) has an infeasible clear core (incident walls consume entire room area)."
                warnings.append(msg)
                findings.append({"code": "INFEASIBLE_ROOM_CORE", "severity": "warning", "entity_ids": [r.id], "message": msg})

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

            if len(r.points) >= 3 and getattr(r, "holes", None):
                holes_finite = True
                for h in r.holes:
                    if not all(math.isfinite(p[0]) and math.isfinite(p[1]) for p in h):
                        holes_finite = False
                        break
                if holes_finite and all(math.isfinite(p[0]) and math.isfinite(p[1]) for p in r.points):
                    try:
                        from shapely.geometry import Polygon
                        shell_poly = Polygon(r.points)
                        hole_polys = []
                        for h_idx, hole in enumerate(r.holes):
                            if len(hole) < 3:
                                msg = f"Room '{r.id}' hole {h_idx} has fewer than 3 vertices ({len(hole)})."
                                errors.append(msg)
                                findings.append({"code": "DEGENERATE_ROOM_HOLE", "severity": "error", "entity_ids": [r.id], "message": msg})
                                continue
                            h_poly = Polygon(hole)
                            if not h_poly.is_valid:
                                h_poly = h_poly.buffer(0)
                            hole_polys.append((h_idx, h_poly))

                            # PAC-1 T07, R19-P1-04: Hole must be strictly inside room shell
                            if not shell_poly.contains(h_poly):
                                msg = f"Room '{r.id}' hole {h_idx} is not strictly contained within room shell boundary."
                                errors.append(msg)
                                findings.append({"code": "ROOM_HOLE_OUTSIDE_SHELL", "severity": "error", "entity_ids": [r.id], "message": msg})

                        # PAC-1 T07, R19-P1-04: Check for overlapping holes
                        for i in range(len(hole_polys)):
                            for j in range(i + 1, len(hole_polys)):
                                idx_i, poly_i = hole_polys[i]
                                idx_j, poly_j = hole_polys[j]
                                if poly_i.intersects(poly_j):
                                    inter = poly_i.intersection(poly_j)
                                    if inter.area > 1e-4:
                                        msg = f"Room '{r.id}' has overlapping holes ({idx_i} and {idx_j})."
                                        errors.append(msg)
                                        findings.append({"code": "ROOM_HOLES_OVERLAP", "severity": "error", "entity_ids": [r.id], "message": msg})
                    except Exception:
                        pass

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

        def _is_finite_wall(w):
            return all(math.isfinite(v) for v in (w.x_start, w.y_start, w.x_end, w.y_end, w.height, w.thickness, w.arc_extent))

        def _is_finite_room(r):
            if len(r.points) < 3 or not all(math.isfinite(p[0]) and math.isfinite(p[1]) for p in r.points):
                return False
            if getattr(r, "holes", None):
                for h in r.holes:
                    if len(h) < 3 or not all(math.isfinite(p[0]) and math.isfinite(p[1]) for p in h):
                        return False
            return True

        def _is_finite_opening(op):
            vals = [op.offset_cm, op.width_cm, op.height_cm, op.elevation_cm]
            if op.depth_cm is not None: vals.append(op.depth_cm)
            if op.x_cm is not None: vals.append(op.x_cm)
            if op.y_cm is not None: vals.append(op.y_cm)
            if op.angle_deg is not None: vals.append(op.angle_deg)
            return all(math.isfinite(v) for v in vals)

        def _is_finite_furniture(f):
            return all(math.isfinite(v) for v in (f.x_cm, f.y_cm, f.elevation_cm, f.width_cm, f.depth_cm, f.height_cm, f.angle_deg))

        furn_by_level = {}
        for f in self.furniture.values():
            if not _is_finite_furniture(f):
                continue
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
            if not _is_finite_opening(op):
                continue
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

                # PAC-1 C08, R17-P1-06, R18-P1-03: Check door swing sector against obstacle wall material
                op_lvl = self.get_level(lvl)
                op_world_z0 = (op_lvl.elevation_cm if op_lvl else 0.0) + op.elevation_cm
                op_world_z1 = op_world_z0 + op.height_cm
                door_sector_poly = _make_door_sector_poly(hinge, p_latch, p_open)

                for other_w in self.walls.values():
                    if other_w.id == op.wall_id:
                        continue  # Host wall is exempted
                    if not _is_finite_wall(other_w):
                        continue

                    has_wall_collision = False
                    collision_witness = None
                    if door_sector_poly is not None:
                        slices = resolve_wall_material(self, other_w, z0_cm=op_world_z0, z1_cm=op_world_z1)
                        for s in slices:
                            inter = door_sector_poly.intersection(s.polygon)
                            if not inter.is_empty and inter.area > 0.05:
                                has_wall_collision = True
                                collision_witness = {
                                    "penetration_area_cm2": round(inter.area, 2),
                                    "vertical_slab_cm": [round(s.z0_cm, 1), round(s.z1_cm, 1)]
                                }
                                break
                    else:
                        other_lvl = other_w.level_id or "level1"
                        w_lvl_obj = self.get_level(other_lvl)
                        w_elev = w_lvl_obj.elevation_cm if w_lvl_obj else 0.0
                        if max(op_world_z0, w_elev) < min(op_world_z1, w_elev + other_w.height) - 1e-4:
                            L = other_w.length
                            if L >= 1e-4:
                                nx = -(other_w.y_end - other_w.y_start) / L * (other_w.thickness / 2.0)
                                ny = (other_w.x_end - other_w.x_start) / L * (other_w.thickness / 2.0)
                                w_corners = [
                                    (other_w.x_start + nx, other_w.y_start + ny),
                                    (other_w.x_end + nx, other_w.y_end + ny),
                                    (other_w.x_end - nx, other_w.y_end - ny),
                                    (other_w.x_start - nx, other_w.y_start - ny)
                                ]
                                if _door_sector_intersects_polygon(hinge, p_latch, p_open, w_corners, tol=0.1):
                                    has_wall_collision = True

                    if has_wall_collision:
                        msg = f"Door '{op.id}' swing sector collides with wall '{other_w.id}'."
                        warnings.append(msg)
                        finding_data = {
                            "code": "DOOR_WALL_COLLISION",
                            "severity": "warning",
                            "entity_ids": [op.id, other_w.id],
                            "message": msg
                        }
                        if collision_witness:
                            finding_data["witness"] = collision_witness
                        findings.append(finding_data)

        # Gate C03 & C04: Check furniture vs physical wall material (accounting for opening cutouts)
        try:
            from shapely.geometry import Polygon
            has_shapely = True
        except ImportError:
            has_shapely = False

        if has_shapely:
            for f in self.furniture.values():
                if not _is_finite_furniture(f):
                    continue
                f_lvl = self.get_level(f.level_id)
                f_elev = f_lvl.elevation_cm if f_lvl else 0.0
                f_world_z0 = f_elev + f.elevation_cm
                f_world_z1 = f_world_z0 + f.height_cm
                f_corners = _get_furn_corners(f)
                furn_poly = Polygon(f_corners)

                for w in self.walls.values():
                    if not _is_finite_wall(w):
                        continue
                    slices = resolve_wall_material(self, w, z0_cm=f_world_z0, z1_cm=f_world_z1)
                    for s in slices:
                        inter = furn_poly.intersection(s.polygon)
                        if not inter.is_empty and inter.area > 0.05:
                            msg = f"Furniture '{f.id}' ({f.name}) collides with wall material of '{w.id}'."
                            warnings.append(msg)
                            findings.append({
                                "code": "FURNITURE_WALL_COLLISION",
                                "severity": "warning",
                                "entity_ids": [f.id, w.id],
                                "message": msg,
                                "witness": {
                                    "penetration_area_cm2": round(inter.area, 2),
                                    "vertical_slab_cm": [round(s.z0_cm, 1), round(s.z1_cm, 1)]
                                }
                            })
                            break

            # Gate C05: Check furniture containment in assigned room
            for f in self.furniture.values():
                if not _is_finite_furniture(f):
                    continue
                room_id = f.room_id
                if room_id is not None:
                    if room_id not in self.rooms:
                        msg = f"Furniture '{f.id}' ({f.name}) references nonexistent room '{room_id}'."
                        errors.append(msg)
                        findings.append({
                            "code": "MISSING_ROOM_REFERENCE",
                            "severity": "error",
                            "entity_ids": [f.id],
                            "message": msg
                        })
                    else:
                        r = self.rooms[room_id]
                        if f.level_id != r.level_id:
                            msg = f"Furniture '{f.id}' on level '{f.level_id}' assigned to room '{r.id}' on level '{r.level_id}'."
                            warnings.append(msg)
                            findings.append({
                                "code": "CROSS_LEVEL_ROOM_ASSIGNMENT",
                                "severity": "warning",
                                "entity_ids": [f.id, r.id],
                                "message": msg
                            })
                        if _is_finite_room(r):
                            clear_poly = r.get_clear_core_polygon(self.walls.values())
                            if clear_poly is not None:
                                room_poly = clear_poly
                            else:
                                room_poly = Polygon(r.points, holes=r.holes or [])
                            furn_poly = Polygon(_get_furn_corners(f))
                            if not room_poly.buffer(0.01).covers(furn_poly):
                                outside = furn_poly.difference(room_poly.buffer(0.01))
                                if not outside.is_empty and outside.area > 0.05:
                                    msg = f"Furniture '{f.id}' ({f.name}) extends outside assigned room '{r.id}' ({r.name})."
                                    warnings.append(msg)
                                    findings.append({
                                        "code": "FURNITURE_OUTSIDE_ROOM",
                                        "severity": "warning",
                                        "entity_ids": [f.id, r.id],
                                        "message": msg,
                                        "witness": {"outside_area_cm2": round(outside.area, 2)}
                                    })
        else:
            msg = "Mandatory geometry engine (Shapely) is unavailable; spatial validation could not be executed."
            errors.append(msg)
            findings.append({
                "code": "GEOMETRY_ENGINE_UNAVAILABLE",
                "severity": "error",
                "message": msg,
                "coverage": "not_checked"
            })

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

    def to_xml(self, *, validate=True):
        """Generates valid Sweet Home 3D Home.xml string preserving extra nodes and attributes."""
        assert_native_model_domain(self)

        def _format_native_color(col_str):
            if not col_str:
                return col_str
            s = str(col_str).strip().lstrip("#")
            if len(s) == 6:
                return f"00{s.upper()}"
            elif len(s) == 8:
                return s.upper()
            return col_str

        # PAC-1 T07, E03, N09, R18-P1-01: Native room holes require a supported decomposition; export refused
        if any(r.holes for r in self.rooms.values()):
            raise ValueError("Native room holes require a supported decomposition; export refused")

        # Gate N01: Sweet Home 3D native wire version is integer 7400 (not marketing string "7.5")
        native_ver = "7400" if (self.version == "7.5" or not self.version) else str(self.version)
        root_attribs = {"version": native_ver, "name": self.name, "wallHeight": str(self.wall_height)}
        root_attribs.update(self.extra_root_attribs)
        root = ET.Element("home", **root_attribs)
        
        # PAC-1 N02, R17-P1-03, R18-P1-02: Rank and order native root categories
        ROOT_CATEGORIES = [
            ('property',), ('furnitureVisibleProperty',), ('environment',),
            ('backgroundImage',), ('print',), ('compass',),
            ('camera', 'observerCamera'), ('level',),
            ('pieceOfFurniture', 'doorOrWindow', 'furnitureGroup', 'light'),
            ('wall',), ('room',), ('polyline',), ('dimensionLine',), ('label',)
        ]
        ROOT_RANK = {tag: rank for rank, tags in enumerate(ROOT_CATEGORIES) for tag in tags}
        level_rank = ROOT_RANK['level']

        pre_elements = []
        post_elements = []
        for raw_elem_str in self.extra_elements:
            try:
                el = ET.fromstring(raw_elem_str)
                r = ROOT_RANK.get(el.tag, 999)
                if r < level_rank:
                    pre_elements.append((r, el))
                else:
                    post_elements.append((r, el))
            except Exception:
                post_elements.append((999, raw_elem_str))

        pre_elements.sort(key=lambda item: item[0])
        post_elements.sort(key=lambda item: item[0])

        for _, el in pre_elements:
            root.append(el)

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

        # PAC-1 N02: Official Sweet Home 3D wire sequence mandates furniture/openings BEFORE walls/rooms
        # Openings (doors and windows, emitting native spatial coordinates + wall references)
        for op in self.openings.values():
            wall = self.walls.get(op.wall_id) if op.wall_id else None
            op_attrib = {
                "id": op.id, "name": op.name,
                "width": str(op.width_cm), "height": str(op.height_cm),
                "elevation": str(op.elevation_cm),
            }
            if wall:
                p_center = wall.point_at_offset(op.offset_cm + op.width_cm / 2.0)
                depth_val = op.depth_cm if op.depth_cm is not None else wall.thickness
                out_x = op.x_cm if op.x_cm is not None else p_center[0]
                out_y = op.y_cm if op.y_cm is not None else p_center[1]
                out_angle = math.radians(op.angle_deg) if op.angle_deg is not None else math.radians(wall.angle_deg)
                op_attrib.update({
                    "x": str(round(out_x, 2)),
                    "y": str(round(out_y, 2)),
                    "depth": str(depth_val),
                    "angle": str(out_angle),
                    "level": op.level_id or wall.level_id,
                })
            else:
                if op.x_cm is not None:
                    op_attrib["x"] = str(round(op.x_cm, 2))
                if op.y_cm is not None:
                    op_attrib["y"] = str(round(op.y_cm, 2))
                if op.depth_cm is not None:
                    op_attrib["depth"] = str(op.depth_cm)
                if op.angle_deg is not None:
                    op_attrib["angle"] = str(math.radians(op.angle_deg))
                if op.level_id:
                    op_attrib["level"] = op.level_id

            # PAC-1 N02, R17-P1-03: Strip non-native wire attributes ('wall', 'offset', 'type', 'swing')
            for k, v in op.extra_attribs.items():
                if k not in ("wall", "offset", "type", "swing"):
                    op_attrib[k] = v
            op_elem = ET.SubElement(root, "doorOrWindow", **op_attrib)

            # PAC-1 N02, N09, R19-P1-02, R19-P1-03: Order door child elements according to Sweet Home 3D DTD
            # Native sash child for door swing only if opening_type == 'door' and swing in ('left', 'right')
            if op.opening_type == "door" and op.swing in ("left", "right"):
                if op.swing == "right":
                    ET.SubElement(op_elem, "sash", xAxis="0.0", yAxis="0.5", width="1.0", startAngle="0.0", endAngle=str(-math.pi / 2))
                elif op.swing == "left":
                    ET.SubElement(op_elem, "sash", xAxis="1.0", yAxis="0.5", width="1.0", startAngle=str(math.pi), endAngle=str(3 * math.pi / 2))

            # PAC-1 N02, R18-P1-02, R19-P1-02: Write reversible opening metadata
            OPENING_META = "cli-anything.opening.v1"
            meta_dict = {
                "version": 1,
                "kind": op.opening_type,
                "operation": op.swing,
                "host_id": op.wall_id,
                "offset_cm": op.offset_cm
            }
            ET.SubElement(op_elem, "property", name=OPENING_META,
                          value=json.dumps(meta_dict, separators=(',', ':'), allow_nan=False))

            for raw_child in op.extra_children:
                try:
                    child_el = ET.fromstring(raw_child)
                    if child_el.tag == "property" and child_el.attrib.get("name") == OPENING_META:
                        continue
                    if child_el.tag == "sash":
                        continue
                    op_elem.append(child_el)
                except Exception:
                    pass

            # Stable sort children of doorOrWindow by DTD rank
            DOOR_RANK = {'sash': 0, 'property': 1, 'textStyle': 2, 'texture': 3, 'material': 4, 'transformation': 5}
            op_elem[:] = sorted(list(op_elem), key=lambda e: DOOR_RANK.get(e.tag, 999))

        # Furniture (angle in radians per native Sweet Home 3D API)
        for f in self.furniture.values():
            f_attrib = {
                "id": f.id, "level": f.level_id,
                "name": f.name, "catalogId": f.catalog_id,
                "x": str(f.x_cm), "y": str(f.y_cm), "elevation": str(f.elevation_cm),
                "width": str(f.width_cm), "depth": str(f.depth_cm), "height": str(f.height_cm),
                "angle": str(math.radians(f.angle_deg))
            }
            f_attrib.update(f.extra_attribs)
            f_elem = ET.SubElement(root, "pieceOfFurniture", **f_attrib)
            for raw_child in f.extra_children:
                try:
                    f_elem.append(ET.fromstring(raw_child))
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
                # Gate N03: Upstream Sweet Home 3D Java 7.5 models arcExtent in radians with inverted sign relative to +Y bulge
                native_arc = -math.radians(w.arc_extent)
                wall_attrib["arcExtent"] = str(native_arc)
            wall_attrib.update(w.extra_attribs)
            w_elem = ET.SubElement(root, "wall", **wall_attrib)
            for raw_child in w.extra_children:
                try:
                    w_elem.append(ET.fromstring(raw_child))
                except Exception:
                    pass

        # Rooms
        for r in self.rooms.values():
            # Gate N03: Format RGB color as 8 hex digits without `#`; omit custom 'area' attribute
            r_attrib = {"id": r.id, "level": r.level_id, "name": r.name,
                        "floorColor": _format_native_color(r.floor_color)}
            r_attrib.update(r.extra_attribs)
            room_elem = ET.SubElement(root, "room", **r_attrib)
            for p in r.points:
                ET.SubElement(room_elem, "point", x=str(p[0]), y=str(p[1]))
            for raw_child in r.extra_children:
                try:
                    room_elem.append(ET.fromstring(raw_child))
                except Exception:
                    pass
            # PAC-1 N02, N09, R25: Stable sort room children so properties precede points
            room_rank = {"property": 0, "textStyle": 1, "texture": 2, "point": 3}
            room_elem[:] = sorted(room_elem, key=lambda child: room_rank.get(child.tag, 99))

        # Dimensions (PAC-1 N02, R18-P1-02: elevationStart and elevationEnd)
        for dim in self.dimensions.values():
            e_start = getattr(dim, "elevation_start_cm", dim.elevation_cm)
            e_end = getattr(dim, "elevation_end_cm", dim.elevation_cm)
            # PAC-1 C01, N03, E07, R20-P1-03: Dimension endpoint elevations must be finite before native XML emission
            if not math.isfinite(e_start) or not math.isfinite(e_end):
                raise ValueError(f"Dimension '{dim.id}' endpoint elevations must be finite: start={e_start}, end={e_end}")
            d_attrib = {
                "id": dim.id, "level": dim.level_id,
                "xStart": str(dim.x_start), "yStart": str(dim.y_start),
                "xEnd": str(dim.x_end), "yEnd": str(dim.y_end),
                "offset": str(dim.offset_cm),
                "elevationStart": str(e_start),
                "elevationEnd": str(e_end),
            }
            d_attrib.update(dim.extra_attribs)
            d_elem = ET.SubElement(root, "dimensionLine", **d_attrib)
            for raw_child in dim.extra_children:
                try:
                    d_elem.append(ET.fromstring(raw_child))
                except Exception:
                    pass

        # Preserve unmodeled trailing extra elements
        for _, el in post_elements:
            try:
                if isinstance(el, str):
                    root.append(ET.fromstring(el))
                else:
                    root.append(el)
            except Exception:
                pass

        # PAC-1 N03, N09, R19-P1-03: Stable sort all root child elements according to DTD sequence
        root[:] = sorted(list(root), key=lambda e: ROOT_RANK.get(e.tag, 999))

        rough_string = ET.tostring(root, "utf-8")
        reparsed = minidom.parseString(rough_string)
        text = reparsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
        assert_native_wire(text)
        return text

    @classmethod
    def from_xml(cls, xml_text, *, dialect="native-v7400", validate=True):
        """Parses Sweet Home 3D Home.xml into a HomeModel instance preserving unmodeled elements."""
        if dialect != "native-v7400":
            raise ValueError("Use an explicit legacy conversion before native import")
        assert_native_wire(xml_text)
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

        def _parse_native_color(col_str):
            if not col_str:
                return col_str
            s = str(col_str).strip().lstrip("#")
            if len(s) == 8 and s.startswith("00"):
                return f"#{s[2:]}"
            elif len(s) == 6:
                return f"#{s}"
            return col_str

        # Walls
        for elem in root.findall("wall"):
            known_wall_keys = {"id", "xStart", "yStart", "xEnd", "yEnd", "thickness", "height", "level", "arcExtent"}
            extra_attribs = {k: v for k, v in elem.attrib.items() if k not in known_wall_keys}
            extra_children = [ET.tostring(c, encoding="unicode") for c in elem]
            raw_arc = elem.attrib.get("arcExtent")
            arc_val = 0.0
            if raw_arc is not None:
                arc_val = decode_angle(raw_arc, dialect=dialect, wall_arc=True)

            model.add_wall(
                wall_id=elem.attrib.get("id"),
                x_start=float(elem.attrib.get("xStart", 0.0)),
                y_start=float(elem.attrib.get("yStart", 0.0)),
                x_end=float(elem.attrib.get("xEnd", 0.0)),
                y_end=float(elem.attrib.get("yEnd", 0.0)),
                thickness=float(elem.attrib.get("thickness", 15.0)),
                height=float(elem.attrib.get("height", 280.0)),
                level_id=elem.attrib.get("level", "level1"),
                arc_extent=arc_val,
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
            raw_col = elem.attrib.get("floorColor", "#EAEAEA")
            parsed_col = _parse_native_color(raw_col)
            model.add_room(
                room_id=elem.attrib.get("id"),
                name=elem.attrib.get("name", "Room"),
                points=points,
                level_id=elem.attrib.get("level", "level1"),
                floor_color=parsed_col,
                extra_attribs=extra_attribs,
                extra_children=extra_children,
            )

        # Openings
        for elem in root.findall("doorOrWindow"):
            known_op_keys = {"id", "name", "wall", "offset", "width", "height", "type", "swing", "x", "y", "depth", "angle", "level", "elevation"}
            extra_attribs = {k: v for k, v in elem.attrib.items() if k not in known_op_keys}
            # PAC-1 N02, N09, R19-P1-02: Filter out opening metadata property and native sashes from extra_children
            extra_children = [
                ET.tostring(c, encoding="unicode") for c in elem
                if not (c.tag == "property" and c.attrib.get("name") == "cli-anything.opening.v1")
                and c.tag != "sash"
            ]

            op_id = elem.attrib.get("id")
            name = elem.attrib.get("name", "Door/Window")
            wall_id = elem.attrib.get("wall")
            width_cm = float(elem.attrib.get("width", 90.0))
            height_cm = float(elem.attrib.get("height", 210.0))
            op_type = elem.attrib.get("type", "door")
            swing = elem.attrib.get("swing", "right")

            offset_cm = float(elem.attrib["offset"]) if "offset" in elem.attrib else None

            # PAC-1 N02, N03, N07, N09, R19-P1-02, R20-P1-01, R21-P1-01:
            # 1. Unconditionally validate physical native sashes
            sashes = elem.findall("sash")
            native_swing = None
            if sashes:
                op_type = "door"
                if len(sashes) == 1:
                    first_s = sashes[0]
                    x_axis = float(first_s.attrib.get("xAxis", "0.0"))
                    y_axis = float(first_s.attrib.get("yAxis", "0.5"))
                    width_ratio = float(first_s.attrib.get("width", "1.0"))
                    start_angle = float(first_s.attrib.get("startAngle", "0.0"))
                    end_angle = float(first_s.attrib.get("endAngle", "0.0"))

                    depth_val = float(elem.attrib.get("depth", 20.0))

                    def _angle_diff(a, b):
                        return (a - b + math.pi) % (2 * math.pi) - math.pi

                    # Right preset: (xp=0.0, yp=0.5, wp=1.0, ap0=0.0, ap1=-pi/2)
                    err_h_r = math.hypot(width_cm * (x_axis - 0.0), depth_val * (y_axis - 0.5))
                    err_w_r = width_cm * abs(width_ratio - 1.0)
                    rmax_r = width_cm * max(abs(width_ratio), 1.0)
                    err_a_r = max(
                        2 * rmax_r * abs(math.sin(_angle_diff(start_angle, 0.0) / 2)),
                        2 * rmax_r * abs(math.sin(_angle_diff(end_angle, -math.pi / 2) / 2))
                    )
                    tot_err_r = err_h_r + err_w_r + err_a_r

                    # Left preset: (xp=1.0, yp=0.5, wp=1.0, ap0=pi, ap1=3*pi/2 or -pi/2)
                    err_h_l = math.hypot(width_cm * (x_axis - 1.0), depth_val * (y_axis - 0.5))
                    err_w_l = width_cm * abs(width_ratio - 1.0)
                    rmax_l = width_cm * max(abs(width_ratio), 1.0)
                    err_a_l = max(
                        2 * rmax_l * abs(math.sin(_angle_diff(start_angle, math.pi) / 2)),
                        2 * rmax_l * abs(math.sin(_angle_diff(end_angle, 3 * math.pi / 2) / 2))
                    )
                    tot_err_l = err_h_l + err_w_l + err_a_l

                    if tot_err_r <= 0.01:
                        native_swing = "right"
                    elif tot_err_l <= 0.01:
                        native_swing = "left"
                    else:
                        raise ValueError(f"Unsupported native sash operation on door '{op_id}'; import refused")
                else:
                    raise ValueError(f"Unsupported native multileaf sash operation ({len(sashes)} sashes) on door '{op_id}'; import refused")

            # 2. Check and decode cli-anything.opening.v1 metadata properties (PAC-1 N07, N09, R23-P1-01)
            resolved_meta = decode_opening_record(elem, native_swing=native_swing)
            if resolved_meta is not None:
                op_type = resolved_meta["kind"]
                swing = resolved_meta["operation"]
                if resolved_meta["host_id"]:
                    wall_id = resolved_meta["host_id"]
                offset_cm = resolved_meta["offset_cm"]
            elif native_swing is not None:
                swing = native_swing
            else:
                elem_name = elem.attrib.get("name")
                if elem.attrib.get("type") == "window" or (elem_name and "window" in elem_name.lower()):
                    op_type = "window"
                    swing = "none"
                else:
                    op_type = elem.attrib.get("type", "door")
                    swing = elem.attrib.get("swing", "right")

            elevation_cm = float(elem.attrib.get("elevation", 0.0))
            depth_cm = float(elem.attrib["depth"]) if "depth" in elem.attrib else None
            x_cm = float(elem.attrib["x"]) if "x" in elem.attrib else None
            y_cm = float(elem.attrib["y"]) if "y" in elem.attrib else None
            angle_deg = None
            if "angle" in elem.attrib:
                angle_deg = decode_angle(elem.attrib["angle"], dialect=dialect) % 360.0
            level_id = elem.attrib.get("level", "level1")

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

            angle_deg = decode_angle(elem.attrib.get("angle", "0"), dialect=dialect) % 360.0

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

        # Dimensions (PAC-1 N02, R17-P1-03, R18-P1-02: support elevationStart / elevationEnd)
        for elem in root.findall("dimensionLine"):
            known_dim_keys = {"id", "level", "xStart", "yStart", "xEnd", "yEnd", "offset", "elevation", "elevationStart", "elevationEnd"}
            extra_attribs = {k: v for k, v in elem.attrib.items() if k not in known_dim_keys}
            extra_children = [ET.tostring(c, encoding="unicode") for c in elem]
            e_start = float(elem.attrib.get("elevationStart", elem.attrib.get("elevation", 0.0)))
            e_end = float(elem.attrib.get("elevationEnd", e_start))
            model.add_dimension(
                dimension_id=elem.attrib.get("id"),
                x_start=float(elem.attrib.get("xStart", 0.0)),
                y_start=float(elem.attrib.get("yStart", 0.0)),
                x_end=float(elem.attrib.get("xEnd", 0.0)),
                y_end=float(elem.attrib.get("yEnd", 0.0)),
                offset_cm=float(elem.attrib.get("offset", 30.0)),
                level_id=elem.attrib.get("level", "level1"),
                elevation_start_cm=e_start,
                elevation_end_cm=e_end,
                extra_attribs=extra_attribs,
                extra_children=extra_children,
            )

        assert_native_model_domain(model)

        return model

    def save_sh3d(self, filepath):
        """Saves project as a native .sh3d compressed ZIP archive atomically preserving all resources (PAC-1 N08)."""
        xml_content = self.to_xml()
        xml_bytes = xml_content.encode("utf-8")

        # Aggregate inventory validation including Home.xml before touching filesystem
        filtered_entries = {
            fname: data_bytes
            for fname, data_bytes in self.extra_zip_entries.items()
            if fname not in ("Home.xml", "Home")
        }
        total_entries = len(filtered_entries) + 1  # +1 for Home.xml
        if total_entries > MAX_ZIP_ENTRIES:
            raise ValueError(f"Generated ZIP archive exceeds max entry limit: {total_entries} > {MAX_ZIP_ENTRIES}")

        total_uncompressed = len(xml_bytes)
        for fname, data_bytes in filtered_entries.items():
            validate_resource_name(fname)
            entry_size = len(data_bytes)
            if entry_size > MAX_ZIP_SINGLE_ENTRY_SIZE:
                raise ValueError(f"ZIP entry exceeds single-file size limit: {fname}")
            total_uncompressed += entry_size
            if total_uncompressed > MAX_ZIP_UNCOMPRESSED_SIZE:
                raise ValueError(f"Generated ZIP archive exceeds total size limit: {total_uncompressed} > {MAX_ZIP_UNCOMPRESSED_SIZE}")

        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        tmp_target = filepath + ".tmp"
        with zipfile.ZipFile(tmp_target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Home.xml", xml_content)
            for fname, data_bytes in filtered_entries.items():
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
            validate_sh3d_zip_archive(zf)
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

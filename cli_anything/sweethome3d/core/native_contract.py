"""Offline native XML contract, DTD validation, and typed adapters (PAC-1 N01-N09)."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import html
from importlib.resources import as_file, files
import io
import math
import os
from pathlib import Path
import re
import struct

from .exceptions import ValidationRejectedError

HANDLER_SHA256 = "44110c53b45af40e62755d3b99a55a27f85d12f2b4334b8f79830feff79a5db0"
PINNED_GRAMMAR_SHA256 = "2cd177039a941be968bcbe9035d14322107108c4d3b15ca521ed329b3a38b446"


def derive_documented_dtd(handler_bytes: bytes) -> bytes:
    """Build-time ONLY: normalize the pinned 7.5 comment's documented defects.

    This is explicitly a DERIVED documented grammar, not an upstream .dtd
    release artifact. Keep source/license, this transform and both hashes.
    Runtime never fetches source or constructs its grammar from user XML.
    """
    if hashlib.sha256(handler_bytes).hexdigest() != HANDLER_SHA256:
        raise ValueError("Unpinned HomeXMLHandler.java")
    source = handler_bytes.decode("utf-8")
    raw = source.split("following DTD:<pre>", 1)[1].split("</pre>", 1)[0]
    raw = html.unescape("\n".join(re.sub(r"^\s*\* ?", "", line)
                                  for line in raw.splitlines()))
    changes = [("renderer CADATA", "renderer CDATA"),
               ("name #REQUIRED>", "name CDATA #REQUIRED>"),
               ('endMarkSize CDATA "10";', 'endMarkSize CDATA "10"'),
               ("<!ATTLIST printedLevel level ID #REQUIRED>",
                "<!ATTLIST printedLevel level IDREF #REQUIRED>")]
    for old, new in changes:
        if raw.count(old) != 1:
            raise ValueError("Unexpected documented grammar; review transform")
        raw = raw.replace(old, new)
    # A?,A? has the intended 0..2 cardinality but is nondeterministic as a
    # DTD content model. Preserve the language with (A,A?)? instead.
    for tag, count in [("texture", 3), ("textStyle", 1), ("baseboard", 1)]:
        old = f"{tag}?, {tag}?"
        if raw.count(old) != count:
            raise ValueError("Unexpected repeated optional content model")
        raw = raw.replace(old, f"({tag}, {tag}?)?")
    return raw.strip().encode("utf-8")


def finite_number(value) -> float:
    if isinstance(value, bool):
        raise ValueError("Boolean is not a physical number")
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Invalid physical number") from exc
    if not math.isfinite(number):
        raise ValueError("Nonfinite physical number")
    return number


def decode_angle(value, *, dialect, wall_arc=False) -> float:
    number = finite_number(value)
    if dialect == "native-v7400":
        return (-1.0 if wall_arc else 1.0) * math.degrees(number)
    if dialect == "cli-legacy-degrees-v1":
        return number
    raise ValueError("Unknown angle dialect; explicit migration required")


def encode_angle(degrees, *, wall_arc=False) -> float:
    return (-1.0 if wall_arc else 1.0) * math.radians(finite_number(degrees))


def checked_native_float(value, *, budget=0.01, coordinate=False) -> float:
    value = finite_number(value)
    if coordinate and abs(value) > 100000.0:
        raise ValueError("Coordinate outside qualified native float profile")
    try:
        native = struct.unpack("!f", struct.pack("!f", value))[0]
    except (OverflowError, struct.error) as exc:
        raise ValueError("Native float overflow") from exc
    if not math.isfinite(native) or abs(native - value) > budget:
        raise ValueError("Native float precision budget exceeded")
    return native


def rgb_to_native(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError("Expected canonical #RRGGBB")
    return "00" + value[1:].upper()


def native_to_rgb(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"00[0-9A-Fa-f]{6}", value):
        raise ValueError("Unsupported native color dialect; do not drop high bits")
    return "#" + value[2:].upper()


def validate_native_xml(xml_text, *, dtd_path, dtd_sha256, dialect="native-v7400"):
    """Offline DTD + typed-reference + bounded core-wire validation.

    lxml is an explicit dependency of this validator; never silently skip it.
    This validates structure and core values, NOT resources/native execution.
    """
    from lxml import etree
    if dialect != "native-v7400":
        raise ValueError("Migrate legacy units before native structural validation")
    raw = xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError("Native XML exceeds 10 MiB metadata profile")
    if b"\x00" in raw or re.search(br"<!\s*(DOCTYPE|ENTITY)\b", raw, re.I):
        raise ValueError("Input DTD/entity declarations are forbidden")
    grammar = Path(dtd_path).read_bytes()
    if hashlib.sha256(grammar).hexdigest() != dtd_sha256:
        raise ValueError("Native grammar hash mismatch")
    if re.search(br"\b(SYSTEM|PUBLIC)\b", grammar):
        raise ValueError("External references forbidden in offline grammar")
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False,
                             no_network=True, huge_tree=False)
    try:
        root = etree.fromstring(raw, parser)
        dtd = etree.DTD(io.BytesIO(grammar))
        dtd.assertValid(root)
        # libxml2 may return success while logging a nondeterministic-model
        # error. Never treat validation-engine errors as coverage success.
        if dtd.error_log.filter_from_errors():
            raise ValueError("Native grammar validation error: " + str(dtd.error_log))
    except (etree.XMLSyntaxError, etree.DTDParseError, etree.DocumentInvalid) as exc:
        raise ValueError("NATIVE_STRUCTURE_INVALID: " + str(exc)) from exc
    if root.tag != "home" or root.get("version") != "7400":
        raise ValueError("Expected native home version 7400")
    by_id = {}
    for elem in root.iter():
        entity_id = elem.get("id")
        if entity_id is not None:
            if entity_id in by_id:
                raise ValueError("Duplicate native ID: " + entity_id)
            by_id[entity_id] = elem
    # DTD IDREF alone does not verify referenced entity TYPE. selectedLevel
    # is CDATA, and the documented printedLevel.level is even declared ID.
    for elem in root.iter():
        for attr, tag in [("level", "level"), ("selectedLevel", "level"),
                          ("wallAtStart", "wall"), ("wallAtEnd", "wall")]:
            ref = elem.get(attr)
            if ref is not None and (ref not in by_id or by_id[ref].tag != tag):
                raise ValueError(f"Invalid typed reference {elem.tag}.{attr}={ref}")
        for attr in ("x", "y", "z", "xStart", "yStart", "xEnd", "yEnd",
                     "elevation", "elevationStart", "elevationEnd", "heightAtEnd",
                     "width", "depth", "height", "thickness", "floorThickness",
                     "offset", "angle", "arcExtent", "xAxis", "yAxis",
                     "startAngle", "endAngle", "wallHeight"):
            if elem.get(attr) is not None:
                checked_native_float(
                    elem.get(attr),
                    coordinate=attr in {"x", "y", "xStart", "yStart", "xEnd", "yEnd"},
                )
        for attr in ("width", "depth", "height", "thickness"):
            if elem.get(attr) is not None and finite_number(elem.get(attr)) <= 0:
                raise ValueError("Nonpositive physical size: " + attr)
        for attr in ("color", "floorColor", "ceilingColor", "leftSideColor", "rightSideColor"):
            if elem.get(attr) is not None:
                native_to_rgb(elem.get(attr))
    return {"valid": True, "profile": "native-v7400", "coverage": "structure-and-core-values",
            "grammar_sha256": dtd_sha256, "target_execution": "not_checked"}


@contextmanager
def native_profile_path():
    resource = files("cli_anything.sweethome3d").joinpath(
        "resources/sh3d-v7400-derived.dtd"
    )
    with as_file(resource) as path:
        yield path


def assert_native_wire(text: str | bytes):
    with native_profile_path() as path:
        return validate_native_xml(
            text, dtd_path=path, dtd_sha256=PINNED_GRAMMAR_SHA256
        )


def assert_native_model_domain(model):
    def check(value, *, xy=False, angle=False, optional=False):
        if value is None and optional:
            return
        number = finite_number(value)
        if not angle:
            checked_native_float(number, coordinate=xy)

    check(model.wall_height)
    for level in model.levels:
        for attr in ("elevation_cm", "height_cm", "floor_thickness_cm"):
            check(getattr(level, attr))
    for wall in model.walls.values():
        for attr in ("x_start", "y_start", "x_end", "y_end"):
            check(getattr(wall, attr), xy=True)
        check(wall.thickness)
        check(wall.height)
        check(wall.arc_extent, angle=True)
        if abs(wall.arc_extent) >= 1e-4:
            assert_curved_wall_error_budget(wall)
    for room in model.rooms.values():
        for ring in [room.points] + list(room.holes or []):
            for point in ring:
                if len(point) != 2:
                    raise ValueError("Native room point must have two coordinates")
                check(point[0], xy=True)
                check(point[1], xy=True)
    for opening in model.openings.values():
        for attr in ("offset_cm", "width_cm", "height_cm", "elevation_cm"):
            check(getattr(opening, attr))
        check(opening.depth_cm, optional=True)
        check(opening.x_cm, xy=True, optional=True)
        check(opening.y_cm, xy=True, optional=True)
        check(opening.angle_deg, angle=True, optional=True)
    for furniture in model.furniture.values():
        check(furniture.x_cm, xy=True)
        check(furniture.y_cm, xy=True)
        for attr in ("width_cm", "depth_cm", "height_cm", "elevation_cm"):
            check(getattr(furniture, attr))
        check(furniture.angle_deg, angle=True)
    for dimension in model.dimensions.values():
        for attr in ("x_start", "y_start", "x_end", "y_end"):
            check(getattr(dimension, attr), xy=True)
        for attr in ("offset_cm", "elevation_cm",
                     "elevation_start_cm", "elevation_end_cm"):
            check(getattr(dimension, attr))

def compute_curved_wall_error_budget(wall, steps=None) -> dict[str, float]:
    """Computes the combined physical native-float/geometry error budget (PAC-1 N03).

    Total physical error = center error + radius error + (radius * native-angle error) + representation error.
    Enforces PAC-1 tolerance <= 0.01 cm.
    """
    if not math.isfinite(wall.arc_extent):
        raise ValidationRejectedError("wall sweep must be finite")
    if abs(wall.arc_extent) < 1e-4:
        return {
            "center_error": 0.0,
            "radius_error": 0.0,
            "angle_error": 0.0,
            "rep_error": 0.0,
            "total_error": 0.0,
            "valid": True,
        }
    c = wall.chord_length
    if not math.isfinite(c) or c <= 1e-6:
        raise ValidationRejectedError("curved wall has zero source chord")
    theta_rad = abs(math.radians(wall.arc_extent))
    alpha = theta_rad / 2.0
    sin_half = math.sin(alpha)
    if abs(sin_half) < 1e-6:
        return {
            "center_error": 0.0,
            "radius_error": 0.0,
            "angle_error": 0.0,
            "rep_error": 0.0,
            "total_error": 0.0,
            "valid": True,
        }
    R = c / (2.0 * sin_half)
    h = R * math.cos(alpha)
    mx = (wall.x_start + wall.x_end) / 2.0
    my = (wall.y_start + wall.y_end) / 2.0
    nx = -(wall.y_end - wall.y_start) / c
    ny = (wall.x_end - wall.x_start) / c
    sign = 1.0 if wall.arc_extent > 0 else -1.0
    cx = mx - sign * h * nx
    cy = my - sign * h * ny

    # PAC-1 N03 / R29: Continuous wire-based curve error budget assessment
    # Wire coordinates check (endpoints and sweep angle)
    x1p = checked_native_float(wall.x_start, coordinate=True)
    y1p = checked_native_float(wall.y_start, coordinate=True)
    x2p = checked_native_float(wall.x_end, coordinate=True)
    y2p = checked_native_float(wall.y_end, coordinate=True)
    f32_theta_rad = checked_native_float(theta_rad)

    # Reconstruct circle from float32 encoded endpoints and sweep (continuous path bound)
    cp = math.hypot(x2p - x1p, y2p - y1p)
    if not math.isfinite(cp) or cp <= 1e-6:
        raise ValidationRejectedError("curved wall collapses in native float profile")
    sin_half_f32 = math.sin(f32_theta_rad / 2.0)
    R_prime = cp / (2.0 * sin_half_f32) if abs(sin_half_f32) > 1e-6 else cp
    h_prime = R_prime * math.cos(f32_theta_rad / 2.0)
    mpx, mpy = (x1p + x2p) / 2.0, (y1p + y2p) / 2.0
    npx, npy = -(y2p - y1p) / cp, (x2p - x1p) / cp
    cx_prime = mpx - sign * h_prime * npx
    cy_prime = mpy - sign * h_prime * npy
    phi0 = math.atan2(wall.y_start - cy, wall.x_start - cx)
    phi0_prime = math.atan2(y1p - cy_prime, x1p - cx_prime)

    e_center = math.hypot(cx_prime - cx, cy_prime - cy)
    e_radius = abs(R_prime - R)
    e_phi0 = abs((phi0 - phi0_prime + math.pi) % (2.0 * math.pi) - math.pi)
    e_angle = abs(f32_theta_rad - theta_rad)
    e_native = e_center + e_radius + R_prime * (e_phi0 + e_angle)

    sampling_steps = steps or (wall.get_curve_sampling_steps(error_tol=0.008) if hasattr(wall, "get_curve_sampling_steps") else 32)
    d_theta = theta_rad / max(1, sampling_steps)
    e_rep = R * (1.0 - math.cos(d_theta / 2.0))

    total = e_native + e_rep
    return {
        "center_error": e_center,
        "radius_error": e_radius,
        "angle_error": e_angle,
        "rep_error": e_rep,
        "total_error": total,
        "valid": total <= 0.01,
    }


def assert_curved_wall_error_budget(wall, budget=0.01):
    result = compute_curved_wall_error_budget(wall)
    if not result["valid"] or result["total_error"] > budget:
        raise ValueError(
            f"Curved wall {wall.id} exceeds physical error budget {budget} cm: "
            f"total={result['total_error']:.6f} cm (center={result['center_error']:.6f}, "
            f"radius={result['radius_error']:.6f}, rep={result['rep_error']:.6f})"
        )
    return result


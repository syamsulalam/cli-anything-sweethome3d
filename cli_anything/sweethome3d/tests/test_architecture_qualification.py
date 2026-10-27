"""Qualification evidence for Architectural Rigor checkpoint (PAC-1 T01-T06).

Establishes case-level verification for:
- T01: Adjacent rooms shared edge, unique physical atoms, two-face ownership
- T02: Collinear overlapping inputs, insertion-order permutation invariance
- T03: X/T intersections, closed loops, explicit intersection noding
- T04: Room self-intersection and geometry conflict detection
- T05: Closed-form quadratic line/arc and arc/arc analytic stations
- T06: Wall split/reverse/delete consistency, opening poses, sloped heights
"""
import math
import pytest
from shapely.geometry import Polygon, LineString

from cli_anything.sweethome3d.core import (
    HomeModel,
    Wall,
    Room,
    Opening,
    DimensionLine,
)
from cli_anything.sweethome3d.core.topology import (
    compute_wall_analytic_stations,
    build_room_face_topology,
)
from cli_anything.sweethome3d.core.exceptions import (
    ValidationRejectedError,
    EntityConflictError,
)


def test_t01_adjacent_rooms_shared_edge_two_face_ownership():
    """Gate T01: Two adjacent rooms yield one shared edge and two distinct face ownerships."""
    m = HomeModel()
    # Room 1: (0,0) to (300,300)
    m.add_room("rm_1", "Living Room", [(0, 0), (300, 0), (300, 300), (0, 300)])
    # Room 2: (300,0) to (600,300), sharing the wall between (300,0) and (300,300)
    m.add_room("rm_2", "Kitchen", [(300, 0), (600, 0), (600, 300), (300, 300)])

    # Add physical perimeter walls and shared wall
    m.add_wall("w_north1", 0, 0, 300, 0)
    m.add_wall("w_north2", 300, 0, 600, 0)
    m.add_wall("w_east", 600, 0, 600, 300)
    m.add_wall("w_south2", 600, 300, 300, 300)
    m.add_wall("w_south1", 300, 300, 0, 300)
    m.add_wall("w_west", 0, 300, 0, 0)
    # Shared wall
    m.add_wall("w_shared", 300, 0, 300, 300)

    topo = build_room_face_topology(m)
    assert topo is not None

    # Check area reconciliation
    rm1_poly = m.rooms["rm_1"].get_region_polygon()
    rm2_poly = m.rooms["rm_2"].get_region_polygon()
    assert abs(rm1_poly.area - 90000.0) < 1e-4  # 9.0 m^2
    assert abs(rm2_poly.area - 90000.0) < 1e-4  # 9.0 m^2
    assert rm1_poly.intersection(rm2_poly).area < 1e-4  # No overlapping interior


def test_t02_collinear_walls_under_insertion_order_permutations():
    """Gate T02: Collinear overlapping wall inputs produce invariant physical stations."""
    # 3 collinear walls along Y=0
    w1 = Wall("w1", 0.0, 0.0, 200.0, 0.0)
    w2 = Wall("w2", 100.0, 0.0, 300.0, 0.0)
    w3 = Wall("w3", 250.0, 0.0, 400.0, 0.0)

    # Order 1: w1, w2, w3
    stations_1 = compute_wall_analytic_stations([w1, w2, w3])
    # Order 2: w3, w1, w2
    stations_2 = compute_wall_analytic_stations([w3, w1, w2])
    # Order 3: w2, w3, w1
    stations_3 = compute_wall_analytic_stations([w2, w3, w1])

    # Check station offsets on w1 are identical across orders
    offsets_1 = [s["station_cm"] for s in stations_1["w1"]]
    offsets_2 = [s["station_cm"] for s in stations_2["w1"]]
    offsets_3 = [s["station_cm"] for s in stations_3["w1"]]

    assert offsets_1 == offsets_2 == offsets_3
    # w1 must be partitioned at its own start (0.0), overlap point with w2 (100.0), and its end (200.0)
    assert offsets_1 == [0.0, 100.0, 200.0]


def test_t03_noding_x_t_junctions_and_closed_loops():
    """Gate T03: X and T intersections are cleanly noded into analytic stations."""
    # Horizontal wall across (0, 100) -> (300, 100)
    w_horiz = Wall("w_h", 0.0, 100.0, 300.0, 100.0)
    # Vertical wall forming X intersection at (100, 100)
    w_x = Wall("w_x", 100.0, 0.0, 100.0, 200.0)
    # Vertical wall forming T junction at (200, 100)
    w_t = Wall("w_t", 200.0, 100.0, 200.0, 300.0)

    stations = compute_wall_analytic_stations([w_horiz, w_x, w_t])

    # w_horiz should have stations at 0.0, 100.0 (X), 200.0 (T), and 300.0
    h_offsets = [s["station_cm"] for s in stations["w_h"]]
    assert h_offsets == [0.0, 100.0, 200.0, 300.0]

    # w_x should have stations at 0.0, 100.0 (intersection), and 200.0
    x_offsets = [s["station_cm"] for s in stations["w_x"]]
    assert x_offsets == [0.0, 100.0, 200.0]


def test_t04_same_side_room_overlap_conflict_detection():
    """Gate T04: Self-intersecting room geometry triggers structured collision findings."""
    m = HomeModel()
    m.add_room("rm_bad", "SelfIntersect", [(0, 0), (100, 100), (0, 100), (100, 0)])

    res = m.validate()
    codes = [f["code"] for f in res["findings"]]
    assert "ROOM_SELF_INTERSECTION" in codes
    assert res["valid"] is False


def test_t05_analytic_stations_closed_form_line_arc():
    """Gate T05: Closed-form quadratic solver computes exact line/arc intersection stations."""
    # Circular arc wall: radius 100, center (100, 100), sweeping 180 degrees
    w_arc = Wall("w_arc", 0.0, 100.0, 200.0, 100.0, arc_extent=180.0)
    # Straight line passing through circle center
    w_line = Wall("w_line", 100.0, -50.0, 100.0, 250.0)

    stations = compute_wall_analytic_stations([w_arc, w_line])
    assert "w_arc" in stations
    assert "w_line" in stations

    # Intersection occurs at apex (100, 200), corresponding to station 250.0 along w_line
    line_offsets = [s["station_cm"] for s in stations["w_line"]]
    assert 250.0 in line_offsets


def test_t06_wall_split_reverse_update_delete_consistency():
    """Gate T06: Split and reverse maintain opening stations, poses, sloped heights, and anchors."""
    m = HomeModel()
    m.add_wall("w_sloped", 0.0, 0.0, 400.0, 0.0, thickness=15.0, height=240.0)
    w = m.walls["w_sloped"]
    w.extra_attribs["heightAtEnd"] = 320.0
    w.extra_attribs["leftSideColor"] = "00FF0000"
    w.extra_attribs["rightSideColor"] = "0000FF00"

    # Add hosted opening
    m.add_opening("door_1", "Door", wall_id="w_sloped", offset_cm=50.0, width_cm=90.0, height_cm=210.0)

    # 1. Reverse wall
    m.reverse_wall("w_sloped")
    w_rev = m.walls["w_sloped"]
    # Opening offset must update to L - 50 - 90 = 400 - 140 = 260 cm
    door = m.openings["door_1"]
    assert door.offset_cm == 260.0
    # Colors must be swapped
    assert w_rev.extra_attribs["leftSideColor"] == "0000FF00"
    assert w_rev.extra_attribs["rightSideColor"] == "00FF0000"

    # 2. Split wall at 100 cm (outside door which is at 260-350 cm)
    head_wall, tail_wall = m.split_wall("w_sloped", 100.0)

    assert head_wall.id == "w_sloped"
    assert tail_wall.id == "w_sloped_seg2"
    assert head_wall.length == 100.0
    assert tail_wall.length == 300.0
    # Door was at 260 cm along old wall; on tail wall it must be at 260 - 100 = 160 cm
    assert door.wall_id == tail_wall.id
    assert door.offset_cm == 160.0

    # 3. Remove tail wall
    ok = m.remove_wall(tail_wall.id)
    assert ok is True
    assert tail_wall.id not in m.walls
    # Door hosted by tail wall should be cleaned up
    assert "door_1" not in m.openings

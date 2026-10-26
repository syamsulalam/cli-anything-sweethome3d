import os
import math
import tempfile
import zipfile
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
import pytest

from cli_anything.sweethome3d.core.wall import Wall
from cli_anything.sweethome3d.core.room import Room
from cli_anything.sweethome3d.core.opening import Opening
from cli_anything.sweethome3d.core.furniture import Furniture, FURNITURE_CATALOG
from cli_anything.sweethome3d.core.dimension import DimensionLine
from cli_anything.sweethome3d.core.project import HomeModel
from cli_anything.sweethome3d.core.session import SessionManager, _locked_save_json, _calc_sha256, ConcurrencyConflictError, RecoveryRequiredError
from cli_anything.sweethome3d.core.export import export_html, export_svg, calculate_bounds

def test_wall_properties():
    # 6m wall horizontal
    w = Wall("w1", 0.0, 0.0, 600.0, 0.0, thickness=15.0, height=280.0)
    assert w.length == 600.0
    assert w.length_m == 6.0
    assert w.angle_deg == 0.0
    assert w.point_at_offset(150.0) == (150.0, 0.0)

    # 4m vertical wall
    w2 = Wall("w2", 0.0, 0.0, 0.0, 400.0)
    assert w2.length == 400.0
    assert w2.length_m == 4.0
    assert w2.angle_deg == 90.0

def test_room_shoelace_area():
    # 6m x 4m rectangle = 24.0 m²
    points = [(0.0, 0.0), (600.0, 0.0), (600.0, 400.0), (0.0, 400.0)]
    r = Room("r1", "Living Room", points)
    assert r.area_cm2 == 240000.0
    assert r.area_m2 == 24.0
    assert r.centroid == (300.0, 200.0)

    # L-shaped room (6m x 6m minus 3m x 3m = 36 - 9 = 27 m²)
    l_points = [
        (0.0, 0.0), (600.0, 0.0), (600.0, 300.0),
        (300.0, 300.0), (300.0, 600.0), (0.0, 600.0)
    ]
    r_l = Room("r2", "L-Room", l_points)
    assert r_l.area_m2 == 27.0

def test_opening_properties():
    op = Opening("d1", "Front Door", "w1", offset_cm=100.0, width_cm=90.0, opening_type="door", swing="right")
    assert op.id == "d1"
    assert op.wall_id == "w1"
    assert op.width_cm == 90.0
    d = op.to_dict()
    assert d["opening_type"] == "door"
    assert d["swing"] == "right"

def test_furniture_catalog():
    assert "bed_king" in FURNITURE_CATALOG
    assert "sofa_3seat" in FURNITURE_CATALOG
    sofa = FURNITURE_CATALOG["sofa_3seat"]
    assert sofa["width"] == 220.0
    assert sofa["depth"] == 90.0

    f = Furniture("f1", "Living Sofa", catalog_id="sofa_3seat", x_cm=200.0, y_cm=150.0, angle_deg=45.0)
    assert f.angle_deg == 45.0
    assert f.to_dict()["catalog_id"] == "sofa_3seat"

def test_home_xml_roundtrip():
    model = HomeModel("Test House", wall_height=300.0)
    model.add_wall("w1", 0.0, 0.0, 500.0, 0.0)
    model.add_wall("w2", 500.0, 0.0, 500.0, 500.0)
    model.add_room("r1", "Main Room", [(0.0, 0.0), (500.0, 0.0), (500.0, 500.0), (0.0, 500.0)])
    model.add_opening("d1", "Door", "w1", offset_cm=50.0, width_cm=90.0)
    model.add_furniture("f1", "Bed", catalog_id="bed_king", x_cm=250.0, y_cm=250.0)

    xml_text = model.to_xml()
    assert "<home" in xml_text
    assert 'name="Test House"' in xml_text
    assert '<wall id="w1"' in xml_text
    assert '<room id="r1"' in xml_text

    # Deserialization test
    parsed_model = HomeModel.from_xml(xml_text)
    assert parsed_model.name == "Test House"
    assert parsed_model.wall_height == 300.0
    assert "w1" in parsed_model.walls
    assert "r1" in parsed_model.rooms
    assert parsed_model.rooms["r1"].area_m2 == 25.0
    assert "d1" in parsed_model.openings
    assert "f1" in parsed_model.furniture

def test_sh3d_zip_packaging():
    model = HomeModel("Zip Test")
    model.add_wall("w1", 0.0, 0.0, 400.0, 0.0)
    model.add_room("r1", "Room 1", [(0.0, 0.0), (400.0, 0.0), (400.0, 300.0), (0.0, 300.0)])

    with tempfile.NamedTemporaryFile(suffix=".sh3d", delete=False) as tmp:
        sh3d_path = tmp.name

    try:
        model.save_sh3d(sh3d_path)
        assert os.path.exists(sh3d_path)
        # Verify ZIP contents
        with zipfile.ZipFile(sh3d_path, "r") as zf:
            assert "Home.xml" in zf.namelist()
            content = zf.read("Home.xml").decode("utf-8")
            assert "Zip Test" in content

        # Verify load_sh3d
        loaded = HomeModel.load_sh3d(sh3d_path)
        assert loaded.name == "Zip Test"
        assert len(loaded.walls) == 1
        assert len(loaded.rooms) == 1
        assert loaded.rooms["r1"].area_m2 == 12.0
    finally:
        if os.path.exists(sh3d_path):
            os.remove(sh3d_path)

def test_session_undo_redo():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        session_file = tmp.name

    try:
        mgr = SessionManager(session_path=session_file)
        mgr.new_project("Session House")
        assert len(mgr.model.walls) == 0

        # Mutation 1: Add wall
        mgr.push_state()
        mgr.model.add_wall("w1", 0.0, 0.0, 500.0, 0.0)
        assert len(mgr.model.walls) == 1

        # Mutation 2: Add second wall
        mgr.push_state()
        mgr.model.add_wall("w2", 500.0, 0.0, 500.0, 500.0)
        assert len(mgr.model.walls) == 2

        # Undo mutation 2
        ok, _ = mgr.undo()
        assert ok
        assert len(mgr.model.walls) == 1
        assert "w2" not in mgr.model.walls

        # Undo mutation 1
        ok, _ = mgr.undo()
        assert ok
        assert len(mgr.model.walls) == 0

        # Redo mutation 1
        ok, _ = mgr.redo()
        assert ok
        assert len(mgr.model.walls) == 1
        assert "w1" in mgr.model.walls
    finally:
        if os.path.exists(session_file):
            os.remove(session_file)

def test_locked_save_json():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        test_file = tmp.name

    try:
        test_data = {"key": "value", "count": 42}
        _locked_save_json(test_file, test_data)
        assert os.path.exists(test_file)
        import json
        with open(test_file, "r", encoding="utf-8") as f:
            read_data = json.load(f)
        assert read_data == test_data
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)

def test_dimension_line():
    # 5m horizontal line from (0,0) to (500,0) with offset 30cm
    dim = DimensionLine("d1", 0.0, 0.0, 500.0, 0.0, offset_cm=30.0)
    assert dim.length == 500.0
    assert dim.length_m == 5.0
    p1_off, p2_off = dim.offset_endpoints()
    # perpendicular vector for (500, 0) is (0, 1) * 30 => (0, 30)
    assert p1_off == (0.0, 30.0)
    assert p2_off == (500.0, 30.0)
    assert dim.label_pos() == (250.0, 30.0)
    assert dim.formatted_label() == "5.00 m"

    d_dict = dim.to_dict()
    assert d_dict["id"] == "d1"
    dim_loaded = DimensionLine.from_dict(d_dict)
    assert dim_loaded.id == "d1"
    assert dim_loaded.length == 500.0

def test_curved_wall_arc():
    import math
    # 90-degree arc wall
    w = Wall("w_arc", 0.0, 0.0, 400.0, 0.0, arc_extent=90.0)
    assert w.arc_extent == 90.0
    # Arc length should be greater than chord length (400)
    assert w.arc_length > 400.0
    assert w.arc_length_m > 4.0
    d = w.to_dict()
    assert "arc_extent" in d
    w_loaded = Wall.from_dict(d)
    assert w_loaded.arc_extent == 90.0

def test_add_wall_loop_and_with_walls():
    model = HomeModel("Loop Test")
    pts = [(0.0, 0.0), (600.0, 0.0), (600.0, 400.0), (0.0, 400.0)]
    walls = model.add_wall_loop(pts, prefix="outer")
    assert len(walls) == 4
    assert "outer_1" in model.walls
    assert "outer_4" in model.walls
    assert model.walls["outer_1"].x2 == 600.0
    assert model.walls["outer_4"].x2 == 0.0

    # Test add_room with with_walls=True
    r = model.add_room("room_auto", "Auto Room", [(1000.0, 0.0), (1400.0, 0.0), (1400.0, 300.0), (1000.0, 300.0)], with_walls=True)
    assert r.id == "room_auto"
    assert "room_auto_w_1" in model.walls
    assert "room_auto_w_4" in model.walls

def test_takeoff_boq():
    model = HomeModel("Takeoff House", wall_height=300.0)
    pts = [(0.0, 0.0), (600.0, 0.0), (600.0, 400.0), (0.0, 400.0)]
    model.add_wall_loop(pts, prefix="w")
    model.add_room("r1", "Living", pts)
    # Add door (1m x 2.1m = 2.1 m²) on w1
    model.add_opening("d1", "Door", "w_1", offset_cm=100.0, width_cm=100.0, height_cm=210.0)
    # Add dimension
    model.add_dimension("dim1", 0.0, 0.0, 600.0, 0.0, offset_cm=50.0)

    takeoff = model.takeoff()
    summary = takeoff["summary"]
    # Perimeter: 2*(6+4) = 20m. Gross wall area = 20m * 3m = 60 m².
    assert summary["wall_perimeter_m"] == 20.0
    assert summary["wall_gross_area_m2"] == 60.0
    # Net wall area = 60.0 - 2.1 = 57.9 m²
    assert summary["wall_net_area_m2"] == 57.9
    # Paint interior + exterior = 2 * net = 115.8 m²
    assert summary["wall_paint_area_m2"] == 115.8
    # Floor area = 24.0 m²
    assert summary["floor_area_m2"] == 24.0
    assert summary["skirting_length_m"] == 19.0  # 20 - 1m door
    assert summary["dimensions_count"] == 1

def test_html_export():
    model = HomeModel("HTML Suite")
    pts = [(0.0, 0.0), (500.0, 0.0), (500.0, 400.0), (0.0, 400.0)]
    model.add_wall_loop(pts, prefix="w")
    model.add_room("r1", "Suite", pts)
    model.add_dimension("dim1", 0.0, 0.0, 500.0, 0.0)

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
        html_path = tmp.name

    try:
        export_html(model, html_path)
        assert os.path.exists(html_path)
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "<!DOCTYPE html>" in content
        assert "HTML Suite" in content
        assert "Architectural Floor Plan Viewer" in content
        assert "takeoff-panel" in content
    finally:
        if os.path.exists(html_path):
            os.remove(html_path)

def test_arc_exact_sagitta_point_at_offset():
    model = HomeModel()
    arc = model.add_wall("arc", 0, 0, 400, 0, arc_extent=90)
    p = arc.point_at_offset(arc.length / 2)
    expected_sagitta = 200 / math.sin(math.pi / 4) * (1 - math.cos(math.pi / 4))
    assert math.isclose(p[1], expected_sagitta, abs_tol=0.01)

def test_invalid_model_validation_catches_errors():
    invalid = HomeModel()
    invalid.add_wall("w_bad", 0, 0, 100, 0, thickness=-10, height=-20, level_id="missing")
    invalid.add_opening("bad_op", "Bad", "w_bad", -50, -30, -10)
    v = invalid.validate()
    assert v["valid"] is False
    assert len(v["errors"]) >= 4

def test_history_persists_across_restart():
    with tempfile.TemporaryDirectory() as tmp:
        history = SessionManager(os.path.join(tmp, "history.json"))
        history.push_state()
        history.model.add_wall("w_test", 0, 0, 100, 0)
        history.save_session()
        
        reloaded = SessionManager(history.session_path)
        ok, msg = reloaded.undo()
        assert ok is True

def test_door_swing_svg_distinct():
    m = HomeModel(name="Swing Test")
    m.add_wall("w1", 0, 0, 400, 0)
    m.add_opening("d", "Door", "w1", offset_cm=50, width_cm=80, swing="right")
    
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        p_right = f.name
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        p_left = f.name
    try:
        export_svg(m, p_right)
        svg_right = open(p_right, "r", encoding="utf-8").read()
        m.openings["d"].swing = "left"
        export_svg(m, p_left)
        svg_left = open(p_left, "r", encoding="utf-8").read()
        assert svg_right != svg_left
    finally:
        if os.path.exists(p_right):
            os.remove(p_right)
        if os.path.exists(p_left):
            os.remove(p_left)

def test_roundtrip_preservation_of_unmodeled_xml_and_zip_entries():
    xml_src = """<?xml version="1.0" encoding="utf-8"?>
<home version="7.5" name="Test Extra" customRootProp="preserved123">
  <level id="level1" name="Ground Floor" elevation="0.0" height="280.0" floorThickness="12.0"/>
  <wall id="w1" level="level1" xStart="0.0" yStart="0.0" xEnd="400.0" yEnd="0.0" thickness="15.0" height="280.0"/>
  <camera x="100.0" y="200.0" z="170.0" fieldOfView="1.0"/>
  <observer camera="cam1"/>
</home>
"""
    archive_path = os.path.join(tempfile.gettempdir(), "test_extra_unit.sh3d")
    out_path = os.path.join(tempfile.gettempdir(), "saved_roundtrip_unit.sh3d")
    try:
        import zipfile
        with zipfile.ZipFile(archive_path, "w") as z:
            z.writestr("Home.xml", xml_src)
            z.writestr("textures/wood.png", b"fake-png-bytes")
            z.writestr("Plugin.properties", b"key=value")

        m = HomeModel.load_sh3d(archive_path)
        assert "customRootProp" in m.extra_root_attribs
        assert len(m.extra_elements) == 2
        assert "textures/wood.png" in m.extra_zip_entries

        m.save_sh3d(out_path)
        with zipfile.ZipFile(out_path, "r") as z:
            names = z.namelist()
            assert "Home.xml" in names
            assert "textures/wood.png" in names
            assert "Plugin.properties" in names
            assert z.read("textures/wood.png") == b"fake-png-bytes"
            xml_out = z.read("Home.xml").decode("utf-8")
            assert 'customRootProp="preserved123"' in xml_out
            assert "<camera" in xml_out
            assert "<observer" in xml_out
    finally:
        if os.path.exists(archive_path):
            os.remove(archive_path)
        if os.path.exists(out_path):
            os.remove(out_path)

def test_major_arc_270_traces_full_path():
    model = HomeModel()
    arc = model.add_wall("arc", 0, 0, 400, 0, arc_extent=270)
    points = [arc.point_at_offset(arc.length * i / 200) for i in range(201)]
    sampled = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    assert arc.length > 1300
    assert sampled > 1300
    assert abs(sampled - arc.length) < 0.1

def test_door_swing_arc_centers_match_hinges():
    import re
    def svg_arc_center(x1, y1, x2, y2, r, large_arc, sweep):
        dx2 = (x1 - x2) / 2.0
        dy2 = (y1 - y2) / 2.0
        d = math.hypot(dx2, dy2)
        sq = max(0.0, (r*r - d*d) / (d*d))
        factor = math.sqrt(sq)
        if large_arc == sweep:
            factor = -factor
        cx_p = factor * dy2
        cy_p = -factor * dx2
        cx = cx_p + (x1 + x2)/2.0
        cy = cy_p + (y1 + y2)/2.0
        return round(cx, 2), round(cy, 2)

    model = HomeModel()
    model.add_wall("w1", 0, 0, 500, 0)
    model.add_opening("d_r", "Door Right", "w1", offset_cm=50, width_cm=100, swing="right")
    model.add_opening("d_l", "Door Left", "w1", offset_cm=250, width_cm=100, swing="left")

    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
        p = tmp.name
    try:
        export_svg(model, p)
        svg = open(p, "r", encoding="utf-8").read()
        pattern = r'd="M ([-0-9.]+) ([-0-9.]+) A ([-0-9.]+) ([-0-9.]+) 0 ([01]) ([01]) ([-0-9.]+) ([-0-9.]+)"'
        arcs = re.findall(pattern, svg)
        assert len(arcs) == 2
        for arc in arcs:
            x1, y1, r1, r2, la, sw, x2, y2 = [float(v) for v in arc]
            c = svg_arc_center(x1, y1, x2, y2, r1, int(la), int(sw))
            if (x1, y1) == (150.0, 0.0):
                assert c == (50.0, 0.0)  # Hinge at (50, 0)
            elif (x1, y1) == (250.0, 0.0):
                assert c == (350.0, 0.0)  # Hinge at (350, 0)
    finally:
        if os.path.exists(p):
            os.remove(p)

def test_non_finite_coordinates_rejected():
    model = HomeModel()
    model.add_wall("w_nan", float("nan"), 0, 100, 0)
    val = model.validate()
    assert val["valid"] is False
    assert any(f["code"] == "NON_FINITE_COORDINATE" for f in val["findings"])

def test_self_crossing_and_zero_area_rooms_rejected():
    model = HomeModel()
    model.add_room("r_cross", "Cross", [(0, 0), (100, 100), (0, 100), (100, 0)])
    val = model.validate()
    assert val["valid"] is False
    assert any(f["code"] == "ROOM_SELF_INTERSECTION" for f in val["findings"])

def test_rotated_furniture_obb_collision():
    model = HomeModel()
    model.add_furniture("a", "A", width_cm=200, depth_cm=20, angle_deg=90)
    model.add_furniture("b", "B", y_cm=80, width_cm=20, depth_cm=20)
    val = model.validate()
    assert any(f["code"] == "FURNITURE_COLLISION" for f in val["findings"])

def test_door_sweep_sector_furniture_collision():
    model = HomeModel()
    model.add_wall("w", 0, 0, 500, 0)
    model.add_opening("d", "Door", "w", offset_cm=0, width_cm=100, swing="right")
    model.add_furniture("f", "Desk", x_cm=160, y_cm=50, width_cm=140, depth_cm=20)
    val = model.validate()
    assert any(f["code"] == "DOOR_SWEEP_COLLISION" for f in val["findings"])

def test_entity_unmodeled_attributes_and_nested_properties_preserved():
    xml_src = """<?xml version="1.0" encoding="utf-8"?>
<home version="7.5" name="Custom Home">
  <level id="level1" name="Ground Floor" elevation="0.0" height="280.0" floorThickness="25.0" customLevelAttr="yes"/>
  <wall id="w1" level="level1" xStart="0.0" yStart="0.0" xEnd="400.0" yEnd="0.0" thickness="15.0" height="280.0" leftSideColor="#112233">
    <property name="material" value="brick"/>
  </wall>
</home>
"""
    m = HomeModel.from_xml(xml_src)
    assert m.levels[0].floor_thickness_cm == 25.0
    assert m.levels[0].extra_attribs.get("customLevelAttr") == "yes"
    assert m.walls["w1"].extra_attribs.get("leftSideColor") == "#112233"
    assert len(m.walls["w1"].extra_children) == 1

    xml_out = m.to_xml()
    assert 'floorThickness="25.0"' in xml_out
    assert 'customLevelAttr="yes"' in xml_out
    assert 'leftSideColor="#112233"' in xml_out
    assert 'name="material"' in xml_out

def test_level_filtered_json_excludes_dangling_openings():
    from cli_anything.sweethome3d.core.export import export_json
    model = HomeModel()
    model.add_level("lvl2", "Second Floor", elevation_cm=300.0, height_cm=280.0)
    model.add_wall("w1", 0, 0, 400, 0, level_id="level1")
    model.add_wall("w2", 0, 0, 400, 0, level_id="lvl2")
    model.add_opening("op1", "Door 1", "w1", offset_cm=50)
    model.add_opening("op2", "Door 2", "w2", offset_cm=50)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        p = tmp.name
    try:
        export_json(model, p, level_id="level1")
        import json
        data = json.load(open(p, "r", encoding="utf-8"))
        assert len(data["walls"]) == 1
        assert data["walls"][0]["id"] == "w1"
        assert len(data["openings"]) == 1
        assert data["openings"][0]["id"] == "op1"
    finally:
        if os.path.exists(p):
            os.remove(p)

def test_default_concurrency_protection_and_rollback():
    from cli_anything.sweethome3d.core.session import SessionManager, ConcurrencyConflictError
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "concurrency_test.json")
        first, second = SessionManager(path), SessionManager(path)
        first.model.add_wall("first", 0, 0, 100, 0)
        first.save_session()  # Defaults to strict_concurrency=True

        second.push_state()
        second.model.add_wall("second", 0, 0, 100, 0)
        import pytest
        with pytest.raises(ConcurrencyConflictError):
            second.save_session()  # Default strict_concurrency=True detects revision conflict

        # Verify rollback restored second.model
        assert "second" not in second.model.walls

def test_corrupt_session_fails_closed():
    from cli_anything.sweethome3d.core.session import SessionManager
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "corrupt.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{invalid json content")
        import pytest
        with pytest.raises(RuntimeError):
            SessionManager(path)

def test_door_sweep_arc_boundary_crossing_collision():
    model = HomeModel()
    model.add_wall("w", 0, 0, 500, 0)
    model.add_opening("d", "Door", "w", 0, 100, swing="right")
    model.add_furniture("f", "Arc crossing", x_cm=100 / math.sqrt(2),
                        y_cm=100 / math.sqrt(2), width_cm=80,
                        depth_cm=4, angle_deg=135)
    val = model.validate()
    assert any(f["code"] == "DOOR_SWEEP_COLLISION" for f in val["findings"])

def test_native_opening_pose_and_elevation_preserved():
    import xml.etree.ElementTree as ET
    xml_src = """<home version="7500">
  <level id="level1" name="Ground" elevation="0" height="280" floorThickness="25"/>
  <doorOrWindow id="win" name="Window" level="level1" x="250" y="0"
      elevation="90" width="120" depth="20" height="120" angle="0"/>
  <wall id="w" level="level1" xStart="0" yStart="0" xEnd="500" yEnd="0"
      thickness="15" height="280"/>
</home>"""
    m = HomeModel.from_xml(xml_src)
    assert m.openings["win"].elevation_cm == 90.0
    assert m.openings["win"].depth_cm == 20.0
    
    xml_out = m.to_xml()
    elem = ET.fromstring(xml_out).find("doorOrWindow")
    assert float(elem.get("elevation")) == 90.0
    assert float(elem.get("depth")) == 20.0

def test_all_four_templates_pass_strict_validation():
    from cli_anything.sweethome3d.core.templates import apply_template
    for typ in ["type36", "type45", "studio", "pavilion"]:
        m = apply_template(HomeModel(), typ)
        val = m.validate()
        assert val["valid"] is True, f"Template '{typ}' failed validation: {val['errors']}"
        assert len(val["warnings"]) == 0, f"Template '{typ}' has warnings: {val['warnings']}"

def test_curved_wall_bounds_contain_major_arcs():
    from cli_anything.sweethome3d.core.export import calculate_bounds
    model = HomeModel()
    model.add_wall("curved", 0, 0, 400, 0, arc_extent=270.0)
    min_x, min_y, width, height = calculate_bounds(model, margin_cm=0.0)
    # The apex of the 270 deg arc reaches y=482.84
    assert min_y + height >= 480.0

def test_invalid_arc_extent_and_domain_safety():
    model = HomeModel()
    model.add_wall("w_over", 0, 0, 400, 0, arc_extent=400.0)
    val = model.validate()
    assert val["valid"] is False
    assert any(f["code"] == "INVALID_ARC_EXTENT" for f in val["findings"])

    # Non-finite arc extent does not crash length or offset
    w_nan = model.add_wall("w_nan", 0, 0, 400, 0, arc_extent=float("nan"))
    assert w_nan.length >= 0.0
    assert w_nan.point_at_offset(50.0) == (50.0, 0.0)

def test_pending_push_prevents_spurious_rollback():
    import json
    import pytest
    from unittest.mock import patch
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mgr = SessionManager(session_path=path)
        mgr.new_project("Initial")
        mgr.push_state()
        mgr.model.add_wall("w1", 0, 0, 100, 0)
        mgr.save_session()
        assert len(mgr.undo_stack) == 2

        # Induce revision conflict on disk
        orig_stack_len = len(mgr.undo_stack)
        with open(path, "w", encoding="utf-8") as fp:
            json.dump({"revision": 9999}, fp)
        
        with pytest.raises(ConcurrencyConflictError):
            mgr.save_session(strict_concurrency=True)
        assert len(mgr.undo_stack) == orig_stack_len

        # Test transactional undo rollback on persistence error
        with patch("cli_anything.sweethome3d.core.session._locked_save_json", side_effect=OSError("disk error")):
            with pytest.raises(RuntimeError):
                mgr.undo()
            # Model and stacks must be restored completely
            assert "w1" in mgr.model.walls
            assert len(mgr.undo_stack) == orig_stack_len
    finally:
        if os.path.exists(path):
            os.remove(path)

def test_door_sweep_vertical_interval_filtering():
    model = HomeModel()
    model.add_wall("w1", 0, 0, 500, 0)
    # Opening at sill 100cm, height 120cm -> head 220cm
    model.add_opening("d1", "Window/Door", "w1", offset_cm=50, width_cm=90, height_cm=120, elevation_cm=100.0)
    # Furniture at z=0..80 (below opening sill)
    model.add_furniture("f_low", "Low Table", catalog_id="custom", x_cm=95, y_cm=40, elevation_cm=0.0, height_cm=80.0, width_cm=50, depth_cm=50)
    # Furniture at z=230..280 (above opening head)
    model.add_furniture("f_high", "Ceiling Fan", catalog_id="custom", x_cm=95, y_cm=40, elevation_cm=230.0, height_cm=50.0, width_cm=50, depth_cm=50)
    val = model.validate()
    assert not any(f["code"] == "DOOR_SWEEP_COLLISION" for f in val["findings"])

def test_sequential_template_application_updates_name():
    from cli_anything.sweethome3d.core.templates import apply_template
    model = HomeModel()
    apply_template(model, "type36")
    assert model.name == "Rumah Tipe 36"
    apply_template(model, "type45")
    assert model.name == "Rumah Tipe 45"
    apply_template(model, "studio")
    assert model.name == "Modern Studio 30m²"

def test_extra_zip_entries_string_and_bytes_roundtrip():
    model = HomeModel()
    model.extra_zip_entries["text.txt"] = b"plain text content"
    model.extra_zip_entries["binary.bin"] = b"\x00\x01\x02\xff"
    d = model.to_dict()
    model2 = HomeModel.from_dict(d)
    assert model2.extra_zip_entries["text.txt"] == b"plain text content"
    assert model2.extra_zip_entries["binary.bin"] == b"\x00\x01\x02\xff"

def test_stale_save_file_does_not_overwrite_destination():
    with tempfile.TemporaryDirectory() as td:
        state = os.path.join(td, "session.json")
        artifact = os.path.join(td, "home.json")
        a = SessionManager(state)
        a.new_project("base")
        a.save_file(artifact)
        b = SessionManager(state)
        a.new_project("newer")
        a.save_file(artifact)
        with pytest.raises(ConcurrencyConflictError):
            b.save_file(artifact)
        with open(artifact, "r", encoding="utf-8") as fp:
            assert json.load(fp)["name"] == "newer"

def test_native_reversed_opening_pose_preserved_in_xml():
    xml = """<home>
      <wall id="w" xStart="0" yStart="0" xEnd="500" yEnd="0"/>
      <doorOrWindow id="d" x="250" y="0" angle="3.141592653589793"
        width="90" depth="20" height="210"/>
    </home>"""
    m = HomeModel.from_xml(xml)
    assert m.openings["d"].angle_deg == 180.0
    xml_out = m.to_xml()
    elem = ET.fromstring(xml_out).find("doorOrWindow")
    assert round(float(elem.get("angle")), 4) == round(math.pi, 4)

def test_zero_chord_curved_wall_rejected_by_validator():
    m = HomeModel()
    m.add_wall("zeroarc", 0, 0, 0, 0, arc_extent=90)
    val = m.validate()
    assert val["valid"] is False
    assert any(f["code"] == "ZERO_LENGTH_WALL" for f in val["findings"])

def test_analytic_arc_extrema_bounding_scale_invariant():
    from cli_anything.sweethome3d.core.export import calculate_bounds
    m_huge = HomeModel()
    m_huge.add_wall("huge", 0, 0, 40000, 0, arc_extent=270.0)
    min_x, min_y, width, height = calculate_bounds(m_huge, margin_cm=100.0)
    # Exact left extremum is cx - r = -8284.27.
    assert min_x <= -8384.27

def test_overlapping_openings_flagged_by_validator():
    m = HomeModel()
    m.add_wall("w1", 0, 0, 500, 0)
    m.add_opening("d1", "Door 1", "w1", offset_cm=100, width_cm=90)
    m.add_opening("d2", "Door 2", "w1", offset_cm=150, width_cm=90)
    val = m.validate()
    assert any(f["code"] == "OPENING_OVERLAP" for f in val["findings"])

def test_repeated_persistence_failure_does_not_leak_flag_or_corrupt_history():
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        state = os.path.join(td, "session.json")
        c = SessionManager(state)
        c.push_state()
        c.model.add_wall("w", 0, 0, 100, 0)
        c.save_session()
        c.undo()
        before = c._create_snapshot()
        c.push_state()
        c.model.add_wall("rejected", 0, 0, 200, 0)
        with patch("cli_anything.sweethome3d.core.session._locked_save_json", side_effect=OSError("probe")):
            for attempt in range(2):
                with pytest.raises(RuntimeError):
                    c.save_session()
                assert c._pending_push is False
                assert c._create_snapshot() == before
        assert c._create_snapshot() == before
        assert len(c.undo_stack) == len(before["undo_stack"])
        assert len(c.redo_stack) == len(before["redo_stack"])

def test_unhosted_and_authoritative_opening_sweep_collision():
    m = HomeModel()
    # Unhosted door at (250, 0) rotated 180 degrees
    m.add_opening("d_rev", "Reversed Door", wall_id=None, width_cm=90, opening_type="door", swing="right", x_cm=250.0, y_cm=0.0, angle_deg=180.0)
    # Furniture placed in its swing path at y = -40
    m.add_furniture("f1", "Colliding Chair", catalog_id="chair", x_cm=250.0, y_cm=-40.0, width_cm=30, depth_cm=30)
    val = m.validate()
    assert any(f["code"] == "DOOR_SWEEP_COLLISION" for f in val["findings"])

def test_two_phase_commit_destination_failure_reconciles_cleanly():
    with tempfile.TemporaryDirectory() as td:
        state = str(Path(td) / "session.json")
        target = str(Path(td) / "never-created.json")
        m = SessionManager(state)
        m.new_project("base")
        revision = m.revision
        replace = os.replace

        def reject_only_destination(src, dst):
            if str(dst) == target:
                raise PermissionError("probe: destination unavailable")
            return replace(src, dst)

        with patch("cli_anything.sweethome3d.core.session.os.replace", side_effect=reject_only_destination):
            with pytest.raises(RuntimeError):
                m.save_file(target)

        disk = json.loads(Path(state).read_text())
        assert not Path(target).exists()
        assert disk["active_file"] != target
        assert disk["revision"] == revision
        assert m.revision == revision
        m.save_session()

def test_hosted_opening_svg_matches_native_pose_and_validation():
    with tempfile.TemporaryDirectory() as td:
        home = HomeModel.from_xml('''<home>
          <wall id="w" xStart="0" yStart="0" xEnd="500" yEnd="0"/>
          <doorOrWindow id="d" x="250" y="0" angle="3.141592653589793"
            width="90" depth="20" height="210"/>
        </home>''')
        home.add_furniture("f", "Negative-side object", x_cm=250, y_cm=-40, width_cm=20, depth_cm=20)
        assert any(f["code"] == "DOOR_SWEEP_COLLISION" for f in home.validate()["findings"])
        svg = str(Path(td) / "home.svg")
        export_svg(home, svg)
        ns = {"s": "http://www.w3.org/2000/svg"}
        leaf = ET.parse(svg).getroot().find(".//s:line[@class='door']", ns)
        assert float(leaf.get("y2")) == -90.0

def test_mutation_exception_transaction_leaves_history_untouched():
    with tempfile.TemporaryDirectory() as td:
        state = str(Path(td) / "session.json")
        m = SessionManager(state)
        m.new_project("base")
        undo_len = len(m.undo_stack)
        with pytest.raises(ValueError):
            m.new_project("bad", wall_height="invalid")
        assert len(m.undo_stack) == undo_len
        assert getattr(m, "_tx_snapshot", None) is None
        assert m._pending_push is False

def test_operation_extents_included_in_bounding_box():
    unhosted_model = HomeModel("Unhosted")
    unhosted_model.add_opening("d_unhosted", "Front Door", x_cm=0, y_cm=0, angle_deg=0, width_cm=200, opening_type="door")
    bx, by, bw, bh = calculate_bounds(unhosted_model, margin_cm=100.0)
    assert by <= -100
    assert by + bh >= 300

def test_incomplete_pose_warning_and_validation():
    incomp_model = HomeModel("Incomp")
    incomp_model.add_opening("d_incomp", "No Angle Door", x_cm=100, y_cm=100, width_cm=90, opening_type="door")
    incomp_model.add_furniture("f_coll", "Colliding", x_cm=100, y_cm=150, width_cm=30, depth_cm=30)
    v = incomp_model.validate()
    codes = [f["code"] for f in v["findings"]]
    assert "INCOMPLETE_OPENING_POSE" in codes
    assert "DOOR_SWEEP_COLLISION" in codes

def test_duplicate_id_rejection():
    m = HomeModel("DupCheck")
    m.add_wall("w1", 0, 0, 100, 0)
    with pytest.raises(ValueError, match="already exists"):
        m.add_wall("w1", 0, 0, 200, 0)

def test_live_writer_pending_journal_not_consumed_by_observer_and_occ_enforced():
    with tempfile.TemporaryDirectory() as td:
        state = str(Path(td) / "session.json")
        target = str(Path(td) / "home.json")
        a = SessionManager(state)
        a.new_project("base")
        a.save_file(target)
        a.push_state()
        a.model.name = "candidate"
        real_replace = os.replace
        observers = []

        def load_during_publication(src, dst):
            if str(dst) == target:
                observer = SessionManager(state)
                observers.append(observer)
                assert observer.model.name == "base"
                # The pending journal must NOT be deleted while writer holds lock!
                assert Path(state + ".pending").exists()
            return real_replace(src, dst)

        with patch("cli_anything.sweethome3d.core.session.os.replace", side_effect=load_during_publication):
            a.save_file(target)

        disk = json.loads(Path(state).read_text())
        assert disk["model"]["name"] == "candidate"
        assert observers[0].revision < disk["revision"]
        # Observer must be rejected due to OCC revision mismatch
        with pytest.raises(RuntimeError, match="Session revision conflict|Concurrency conflict|modified"):
            observers[0].save_session()
        # Disk state is untouched and preserves candidate model
        assert json.loads(Path(state).read_text())["model"]["name"] == "candidate"
        assert json.loads(Path(target).read_text())["name"] == "candidate"

def test_abrupt_exit_crash_recovery_rolls_forward_candidate_model():
    with tempfile.TemporaryDirectory() as td:
        state = str(Path(td) / "session.json")
        target = str(Path(td) / "home.json")
        a = SessionManager(state)
        a.new_project("base")
        a.save_file(target)
        a.push_state()
        a.model.name = "candidate_post_crash"
        
        real_replace = os.replace
        def crash_after_target_publish(src, dst):
            res = real_replace(src, dst)
            if str(dst) == target:
                # Simulate crash right after target published, before session.json was updated
                raise KeyboardInterrupt("Simulated sudden power loss / kill")
            return res

        with patch("cli_anything.sweethome3d.core.session.os.replace", side_effect=crash_after_target_publish):
            with pytest.raises(KeyboardInterrupt):
                a.save_file(target)

        # Journal is still on disk
        assert Path(state + ".pending").exists()
        # Fresh process boots up and recovers the session
        recovery_mgr = SessionManager(state)
        assert recovery_mgr.model.name == "candidate_post_crash"
        assert not Path(state + ".pending").exists()
        disk_session = json.loads(Path(state).read_text())
        assert disk_session["model"]["name"] == "candidate_post_crash"
        assert json.loads(Path(target).read_text())["name"] == "candidate_post_crash"

def test_integer_id_normalization_duplicate_rejection():
    m = HomeModel("NormalizeTest")
    m.add_wall(1, 0, 0, 100, 0)
    assert "1" in m.walls
    with pytest.raises(ValueError, match="already exists"):
        m.add_wall(1, 0, 0, 200, 0)
    with pytest.raises(ValueError, match="already exists"):
        m.add_wall("1", 0, 0, 200, 0)

    m.add_room(10, "Living Room", [(0, 0), (100, 0), (100, 100), (0, 100)])
    assert "10" in m.rooms
    with pytest.raises(ValueError, match="already exists"):
        m.add_room(10, "Living Room 2", [(0, 0), (50, 0), (50, 50), (0, 50)])

    m.add_opening(20, "Door", wall_id="1", width_cm=90)
    assert "20" in m.openings
    with pytest.raises(ValueError, match="already exists"):
        m.add_opening(20, "Door 2", wall_id="1", width_cm=90)

    m.add_furniture(30, "Table", catalog_id="table", x_cm=50, y_cm=50)
    assert "30" in m.furniture
    with pytest.raises(ValueError, match="already exists"):
        m.add_furniture(30, "Table 2", catalog_id="table", x_cm=50, y_cm=50)

    m.add_dimension(40, 0, 0, 100, 0)
    assert "40" in m.dimensions
    with pytest.raises(ValueError, match="already exists"):
        m.add_dimension(40, 0, 0, 100, 0)

    m.add_level(2, "Upper Floor", elevation_cm=300.0)
    assert m.get_level("2") is not None
    with pytest.raises(ValueError, match="already exists"):
        m.add_level(2, "Duplicate Level")
    with pytest.raises(ValueError, match="already exists"):
        m.add_level("2", "Duplicate Level String")

def test_json_import_duplicate_id_rejection():
    bad_wall_json = {
        "name": "DupWall",
        "walls": [
            {"id": "w1", "x_start": 0, "y_start": 0, "x_end": 100, "y_end": 0},
            {"id": "w1", "x_start": 0, "y_start": 0, "x_end": 200, "y_end": 0}
        ]
    }
    with pytest.raises(ValueError, match="Duplicate wall ID 'w1'"):
        HomeModel.from_dict(bad_wall_json)

    bad_room_json = {
        "name": "DupRoom",
        "rooms": [
            {"id": "r1", "name": "Room A", "points": [[0, 0], [100, 0], [100, 100], [0, 100]]},
            {"id": "r1", "name": "Room B", "points": [[0, 0], [50, 0], [50, 50], [0, 50]]}
        ]
    }
    with pytest.raises(ValueError, match="Duplicate room ID 'r1'"):
        HomeModel.from_dict(bad_room_json)

    bad_level_json = {
        "name": "DupLevel",
        "levels": [
            {"id": "lvl1", "name": "Ground", "elevation_cm": 0},
            {"id": "lvl1", "name": "Ground Duplicate", "elevation_cm": 0}
        ]
    }
    with pytest.raises(ValueError, match="Duplicate level ID 'lvl1'"):
        HomeModel.from_dict(bad_level_json)

def test_cli_wall_add_duplicate_rejection_preserves_undo_redo_history():
    from click.testing import CliRunner
    from cli_anything.sweethome3d.sweethome3d_cli import cli
    import cli_anything.sweethome3d.sweethome3d_cli as cli_mod

    with tempfile.TemporaryDirectory() as td:
        state_file = os.path.join(td, "cli_session.json")
        orig_session_mgr = cli_mod.session_mgr
        runner = CliRunner()
        try:
            cli_mod.session_mgr = SessionManager(state_file)
            cli_mod.session_mgr.new_project("CLITest")
            
            # Step 1: Add initial wall w1
            res = runner.invoke(cli, ["wall", "add", "--id", "w1", "--x1", "0", "--y1", "0", "--x2", "100", "--y2", "0"])
            assert res.exit_code == 0
            
            # Step 2: Add wall w2
            res = runner.invoke(cli, ["wall", "add", "--id", "w2", "--x1", "100", "--y1", "0", "--x2", "100", "--y2", "100"])
            assert res.exit_code == 0
            
            # Step 3: Undo w2
            res = runner.invoke(cli, ["session", "undo"])
            assert res.exit_code == 0
            
            # Now undo stack has 2 items (initial, w1), redo stack has 1 item (w2)
            undo_count_before = len(cli_mod.session_mgr.undo_stack)
            redo_count_before = len(cli_mod.session_mgr.redo_stack)
            assert undo_count_before == 2
            assert redo_count_before == 1
            
            # Step 4: Attempt to add duplicate wall w1 -> must fail
            res = runner.invoke(cli, ["wall", "add", "--id", "w1", "--x1", "0", "--y1", "0", "--x2", "200", "--y2", "0"])
            assert res.exit_code != 0 or "Error" in res.output or "already exists" in res.output
            
            # Step 5: Verify undo and redo stacks are strictly preserved!
            assert len(cli_mod.session_mgr.undo_stack) == undo_count_before
            assert len(cli_mod.session_mgr.redo_stack) == redo_count_before
            
            # Step 6: Verify redo still works cleanly
            res = runner.invoke(cli, ["session", "redo"])
            assert res.exit_code == 0
            assert "w2" in cli_mod.session_mgr.model.walls
        finally:
            cli_mod.session_mgr = orig_session_mgr

def test_pre_publication_crash_with_existing_target():
    import subprocess
    import sys
    child = r'''
import os, sys
sys.path.insert(0, r"C:\Users\THINKPAD\Documents\GitHub\cli-anything-sweethome3d")
from cli_anything.sweethome3d.core.session import SessionManager
state, target = sys.argv[1:]
m = SessionManager(state)
m.push_state()
m.model.name = "candidate"
replace = os.replace
def exit_before_artifact(src, dst):
    if str(dst) == target:
        os._exit(71)
    return replace(src, dst)
os.replace = exit_before_artifact
m.save_file(target)
'''
    with tempfile.TemporaryDirectory() as td:
        state = str(Path(td) / "session.json")
        target = str(Path(td) / "home.json")
        m = SessionManager(state)
        m.new_project("base")
        m.save_file(target)
        env = os.environ.copy()
        env.update(PYTHONPATH=r"C:\Users\THINKPAD\Documents\GitHub\cli-anything-sweethome3d",
                   PYTHONDONTWRITEBYTECODE="1", USERPROFILE=td, HOME=td)
        result = subprocess.run(
            [sys.executable, "-B", "-c", child, state, target],
            env=env, capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 71, f"Child stderr: {result.stderr}"
        assert Path(state + ".pending").exists()
        recovered = SessionManager(state)
        assert recovered.model.name == "candidate"
        assert json.loads(Path(target).read_text())["name"] == "candidate"
        assert not Path(state + ".pending").exists()

def test_pre_publication_crash_with_absent_target():
    import subprocess
    import sys
    child = r'''
import os, sys
sys.path.insert(0, r"C:\Users\THINKPAD\Documents\GitHub\cli-anything-sweethome3d")
from cli_anything.sweethome3d.core.session import SessionManager
state, target = sys.argv[1:]
m = SessionManager(state)
m.push_state()
m.model.name = "candidate"
replace = os.replace
def exit_before_artifact(src, dst):
    if str(dst) == target:
        os._exit(71)
    return replace(src, dst)
os.replace = exit_before_artifact
m.save_file(target)
'''
    with tempfile.TemporaryDirectory() as td:
        state = str(Path(td) / "session.json")
        target = str(Path(td) / "home_absent.json")
        m = SessionManager(state)
        m.new_project("base")
        env = os.environ.copy()
        env.update(PYTHONPATH=r"C:\Users\THINKPAD\Documents\GitHub\cli-anything-sweethome3d",
                   PYTHONDONTWRITEBYTECODE="1", USERPROFILE=td, HOME=td)
        result = subprocess.run(
            [sys.executable, "-B", "-c", child, state, target],
            env=env, capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 71, f"Child stderr: {result.stderr}"
        assert Path(state + ".pending").exists()
        recovered = SessionManager(state)
        assert recovered.model.name == "candidate"
        assert Path(target).exists()
        assert json.loads(Path(target).read_text())["name"] == "candidate"
        assert not Path(state + ".pending").exists()

def test_unresolved_recovery_blocks_subsequent_writes():
    with tempfile.TemporaryDirectory() as td:
        state = str(Path(td) / "session.json")
        m = SessionManager(state)
        m.new_project("base")
        m.save_session()
        
        # Plant corrupt journal
        pending_file = state + ".pending"
        with open(pending_file, "w", encoding="utf-8") as f:
            f.write("{corrupt: not_json_data")
            
        m2 = SessionManager(state)
        # Attempting to save new state while recovery is unresolved must raise RecoveryRequiredError!
        with pytest.raises(RecoveryRequiredError):
            m2.new_project("unrelated")
            m2.save_session()
            
        # Verify pending journal is still preserved
        assert os.path.exists(pending_file)
        # Verify session state was not overwritten
        disk_data = json.loads(Path(state).read_text())
        assert disk_data["model"]["name"] == "base"

def test_empty_journal_schema_rejection():
    """Gate S01: Empty `{}` or invalid schema journals must fail closed and preserve evidence."""
    with tempfile.TemporaryDirectory() as td:
        state = str(Path(td) / "session.json")
        m = SessionManager(state)
        m.new_project("base")
        m.save_session()
        
        pending_file = state + ".pending"
        with open(pending_file, "w", encoding="utf-8") as f:
            f.write("{}")
            
        m2 = SessionManager(state)
        with pytest.raises(RecoveryRequiredError):
            m2.new_project("should_fail")
            
        # Verify journal `{}` was NOT deleted and was preserved!
        assert os.path.exists(pending_file)
        # Verify session state was not overwritten
        disk_data = json.loads(Path(state).read_text())
        assert disk_data["model"]["name"] == "base"

def test_corrupted_backup_fails_closed():
    """Gate S05: If candidate stage is absent and backup is corrupted, recovery must fail closed."""
    with tempfile.TemporaryDirectory() as td:
        state = str(Path(td) / "session.json")
        target = str(Path(td) / "home.json")
        m = SessionManager(state)
        m.new_project("base")
        m.save_file(target)
        
        base_sha = _calc_sha256(target)
        
        # Corrupt target and create a corrupted backup
        backup_file = target + ".bak_corrupt"
        with open(backup_file, "w", encoding="utf-8") as f:
            f.write('{"corrupted": "backup"}')
            
        # Write journal pointing to corrupt backup and base_sha
        pending_file = state + ".pending"
        with open(pending_file, "w", encoding="utf-8") as f:
            json.dump({
                "schema_version": 1,
                "tx_id": "tx_test",
                "target": target,
                "tmp_target": target + ".tmp_none",
                "candidate_artifact_sha256": "fake_candidate_hash",
                "base_artifact_sha256": base_sha,
                "backup": backup_file,
                "had_target": True,
                "staged_session": state + ".tmp_none",
                "expected_revision": 2,
                "target_revision": 3
            }, f)
            
        # Modify target so it does NOT match base_sha
        with open(target, "w", encoding="utf-8") as f:
            f.write('{"modified": true}')
            
        # Recovery cannot verify candidate OR backup: must fail closed without deleting journal
        m2 = SessionManager(state)
        with pytest.raises(RecoveryRequiredError):
            m2.new_project("blocked")
            
        assert os.path.exists(pending_file)
        assert os.path.exists(backup_file)

def test_adjacent_rooms_shared_topology_and_golden_quantities():
    """Gate T01 & T08: Two adjacent 3x3m rooms yield 7 unique physical walls (21m) and golden quantities."""
    model = HomeModel("PAC1 Golden Fixture", wall_height=280.0)
    # Room 1: [0, 300] x [0, 300] cm
    r1 = model.add_room("room1", "Living", [(0, 0), (300, 0), (300, 300), (0, 300)], with_walls=True, wall_thickness=15.0)
    # Room 2: [300, 600] x [0, 300] cm (shares boundary x=300 from y=0 to 300)
    r2 = model.add_room("room2", "Dining", [(300, 0), (600, 0), (600, 300), (300, 300)], with_walls=True, wall_thickness=15.0)
    
    # Gate T01 check: exactly 7 physical wall atoms!
    assert len(model.walls) == 7, f"Expected 7 walls, got {len(model.walls)}: {list(model.walls.keys())}"
    assert model.total_wall_length_m == 21.0, f"Expected 21m total wall length, got {model.total_wall_length_m}"

    # Add shared interior door (1m x 2.1m = 100cm x 210cm) on the shared wall
    shared_wall = None
    for wid, w in model.walls.items():
        if abs(w.x_start - 300.0) < 1.0 and abs(w.x_end - 300.0) < 1.0:
            shared_wall = wid
            break
    assert shared_wall is not None
    model.add_opening("d_shared", "Shared Door", wall_id=shared_wall, offset_cm=100.0, width_cm=100.0, height_cm=210.0)

    # Gate T08 Golden Quantities Check:
    takeoff = model.takeoff()
    summary = takeoff["summary"]
    # Reference skirting before door deduction: 24m. After 2-face door deduction (2x1m): 22m!
    assert summary["reference_skirting_length_m"] == 22.0
    assert summary["skirting_length_m"] == 22.0
    # Inside clear-face skirting after door deduction: 20.8m!
    assert summary["clear_face_skirting_length_m"] == 20.8
    # Clear usable room cores: 2 x 2.85^2 = 16.245 m2!
    assert summary["clear_core_area_m2"] == 16.245

def test_furniture_wall_collision_and_door_cutout():
    """Gate C03 & C04: Furniture penetrating wall material triggers error; doorway cutout allows passage."""
    model = HomeModel("Wall Collision Test", wall_height=280.0)
    w = model.add_wall("w1", 0, 0, 500, 0, thickness=20.0, height=280.0)
    # Furniture 100x100cm centered at (250, 0) directly overlapping the wall
    f1 = model.add_furniture("f1", "Table Overlap", x_cm=250.0, y_cm=0.0, width_cm=100.0, depth_cm=100.0, height_cm=75.0)
    val = model.validate()
    assert any(f["code"] == "FURNITURE_WALL_COLLISION" for f in val["findings"])

    # Now add a door at offset 200 (width 100cm covers x=200 to 300) from elevation 0 to 210cm
    model.add_opening("d1", "Passage Door", wall_id="w1", offset_cm=200.0, width_cm=100.0, height_cm=210.0)
    val_after_door = model.validate()
    # In the doorway cutout, wall material is removed, so furniture in doorway is permitted!
    assert not any(f["code"] == "FURNITURE_WALL_COLLISION" for f in val_after_door["findings"])

def test_furniture_outside_room_containment():
    """Gate C05: Furniture extending outside assigned room triggers FURNITURE_OUTSIDE_ROOM."""
    model = HomeModel("Room Containment Test")
    model.add_room("r1", "Bedroom", [(0, 0), (400, 0), (400, 400), (0, 400)])
    # Furniture centered at (380, 200) with width 100cm extends outside x=400
    f = model.add_furniture("bed", "Bed", x_cm=380.0, y_cm=200.0, width_cm=100.0, depth_cm=100.0, height_cm=80.0, room_id="r1")
    val = model.validate()
    assert any(f["code"] == "FURNITURE_OUTSIDE_ROOM" for f in val["findings"])

def test_native_wire_v7400_colors_and_arc_radians():
    """Gate N01 & N03: XML exports version 7400, 8-hex colors without #, and signed radian wall arcExtent."""
    model = HomeModel("Wire Format Test", version="7.5")
    model.add_wall("w_arc", 0, 0, 400, 0, arc_extent=90.0)
    model.add_room("r1", "Living", [(0, 0), (400, 0), (400, 400), (0, 400)], floor_color="#EAEAEA")
    
    xml = model.to_xml()
    root = ET.fromstring(xml)
    # Gate N01: integer version 7400
    assert root.attrib.get("version") == "7400"
    
    # Gate N03: Room color formatted as 8 hex digits without `#` (00EAEAEA)
    room_elem = root.find("room")
    assert room_elem.attrib.get("floorColor") == "00EAEAEA"
    
    # Gate N03: Wall arcExtent exported as signed radians: -radians(90) = -pi/2 ~= -1.570796
    wall_elem = root.find("wall")
    assert float(wall_elem.attrib.get("arcExtent")) < 0.0
    assert round(float(wall_elem.attrib.get("arcExtent")), 4) == round(-math.radians(90.0), 4)

    # Roundtrip check: parsing XML restores internal values
    m_parsed = HomeModel.from_xml(xml)
    assert m_parsed.rooms["r1"].floor_color == "#EAEAEA"
    assert round(m_parsed.walls["w_arc"].arc_extent, 1) == 90.0

def test_journal_schema_rejects_boolean_revisions():
    """PAC-1 S01 & P0-01: Rejection of booleans as revisions."""
    from cli_anything.sweethome3d.core.session import _validate_journal_schema
    # isinstance(True, int) is True in Python; validator must strictly reject booleans
    bad_rec = {
        "schema_version": 1,
        "tx_id": "test_tx",
        "target": "target.sh3d",
        "staged_session": "staged.json",
        "expected_revision": False,
        "target_revision": True,
    }
    assert _validate_journal_schema(bad_rec) is False
    # Schema version missing or wrong must also be rejected
    bad_schema = dict(bad_rec)
    bad_schema["expected_revision"] = 0
    bad_schema["target_revision"] = 1
    bad_schema["schema_version"] = 2
    assert _validate_journal_schema(bad_schema) is False

def test_staged_session_tampering_rollback():
    """PAC-1 S01, S05 & P0-01: Tampered staged session triggers rollback to base."""
    from cli_anything.sweethome3d.core.session import _reconcile_pending, _calc_sha256
    with tempfile.TemporaryDirectory() as td:
        sess_file = os.path.join(td, "sess.json")
        target_file = os.path.join(td, "home.sh3d")
        stage_file = os.path.join(td, "stage.json")
        bak_file = os.path.join(td, "base.bak")

        # Initial base session at revision 1
        with open(sess_file, "w", encoding="utf-8") as f:
            json.dump({"revision": 1, "name": "Base"}, f)
        with open(target_file, "w", encoding="utf-8") as f:
            f.write("BASE_ARTIFACT")
        shutil.copy2(target_file, bak_file)

        # Stage file at revision 2
        with open(stage_file, "w", encoding="utf-8") as f:
            json.dump({"revision": 2, "name": "Candidate"}, f)
        original_stage_sha = _calc_sha256(stage_file)

        # Journal records candidate hashes
        journal_file = sess_file + ".pending"
        journal_rec = {
            "schema_version": 1,
            "tx_id": "tx_tamper",
            "target": target_file,
            "tmp_target": target_file + ".tmp",
            "candidate_artifact_sha256": "fake_sha",
            "candidate_session_sha256": original_stage_sha,
            "base_artifact_sha256": _calc_sha256(bak_file),
            "backup": bak_file,
            "had_target": True,
            "staged_session": stage_file,
            "expected_revision": 1,
            "target_revision": 2,
        }
        with open(journal_file, "w", encoding="utf-8") as f:
            json.dump(journal_rec, f)

        # TAMPER: alter staged session content
        with open(stage_file, "w", encoding="utf-8") as f:
            json.dump({"revision": 2, "name": "TAMPERED_INJECTION"}, f)

        # Reconcile should detect tampered stage hash mismatch and rollback to base!
        _reconcile_pending(sess_file)

        with open(sess_file, "r", encoding="utf-8") as f:
            d = json.load(f)
            assert d["name"] == "Base"
            assert d["revision"] == 1

def test_stale_journal_fails_closed():
    """PAC-1 S04 & P0-01: Journal targeting revision 4 does not rewind disk revision 10."""
    from cli_anything.sweethome3d.core.session import _reconcile_pending
    with tempfile.TemporaryDirectory() as td:
        sess_file = os.path.join(td, "sess.json")
        journal_file = sess_file + ".pending"
        stage_file = os.path.join(td, "stage.json")

        # Disk session is already at revision 10
        with open(sess_file, "w", encoding="utf-8") as f:
            json.dump({"revision": 10, "name": "AuthoritativeNewer"}, f)

        with open(stage_file, "w", encoding="utf-8") as f:
            json.dump({"revision": 4, "name": "StaleCandidate"}, f)

        journal_rec = {
            "schema_version": 1,
            "tx_id": "tx_stale",
            "target": os.path.join(td, "home.sh3d"),
            "tmp_target": os.path.join(td, "home.sh3d.tmp"),
            "candidate_artifact_sha256": "art_sha",
            "candidate_session_sha256": "sess_sha",
            "base_artifact_sha256": "base_sha",
            "backup": None,
            "had_target": False,
            "staged_session": stage_file,
            "expected_revision": 3,
            "target_revision": 4,
        }
        with open(journal_file, "w", encoding="utf-8") as f:
            json.dump(journal_rec, f)

        _reconcile_pending(sess_file)

        # Authoritative newer session must NOT be rewound
        with open(sess_file, "r", encoding="utf-8") as f:
            d = json.load(f)
            assert d["revision"] == 10
            assert d["name"] == "AuthoritativeNewer"
        # Pending journal must be preserved for inspection
        assert os.path.exists(journal_file)

def test_rollback_preserves_evidence_when_base_unprovable():
    """PAC-1 S05 & P0-02: Stage and backup evidence are preserved if rollback fails closed."""
    from cli_anything.sweethome3d.core.session import _rollback_to_base
    with tempfile.TemporaryDirectory() as td:
        target = os.path.join(td, "target.sh3d")
        backup = os.path.join(td, "target.bak")
        stage = os.path.join(td, "stage.json")
        tmp_target = os.path.join(td, "target.tmp")

        with open(target, "w") as f: f.write("DAMAGED_TARGET")
        with open(backup, "w") as f: f.write("CORRUPTED_BACKUP")
        with open(stage, "w") as f: f.write("CANDIDATE_STAGE")
        with open(tmp_target, "w") as f: f.write("TMP_TARGET")

        rec = {
            "schema_version": 1,
            "tx_id": "tx_fail",
            "target": target,
            "tmp_target": tmp_target,
            "staged_session": stage,
            "backup": backup,
            "had_target": True,
            "base_artifact_sha256": "expected_valid_base_sha",
            "expected_revision": 1,
            "target_revision": 2,
        }
        # Rollback must return False because base cannot be proven
        res = _rollback_to_base(rec)
        assert res is False
        # ALL evidence files must be preserved byte-for-byte!
        assert os.path.exists(stage)
        assert os.path.exists(tmp_target)
        assert os.path.exists(backup)
        assert os.path.exists(target)

def test_assert_safe_export_target_protects_journal_and_session():
    """PAC-1 S06 & P0-03: Export cannot overwrite session, lock, pending journal, or recovery targets."""
    from cli_anything.sweethome3d.sweethome3d_cli import assert_safe_export_target, get_session_mgr
    mgr = get_session_mgr()
    sess_path = mgr.session_path

    import pytest
    with pytest.raises(RuntimeError, match="guarded by active transaction journal"):
        assert_safe_export_target(sess_path)
    with pytest.raises(RuntimeError, match="guarded by active transaction journal"):
        assert_safe_export_target(sess_path + ".pending")
    with pytest.raises(RuntimeError, match="guarded by active transaction journal"):
        assert_safe_export_target(sess_path + ".lock")

def test_non_finite_parameter_in_validate_does_not_crash_geos():
    """PAC-1 C01 & P1-04: Non-finite parameters emit errors and do not crash GEOS with unclosed linestrings."""
    model = HomeModel("Non-finite test")
    model.add_furniture("f_nan", "Table", x_cm=float("nan"), y_cm=200.0, angle_deg=float("inf"))
    model.add_wall("w_inf", 0, 0, float("nan"), 0)
    val = model.validate()
    assert val["valid"] is False
    codes = [f["code"] for f in val["findings"]]
    assert "NON_FINITE_COORDINATE" in codes

def test_window_sill_height_material_collision():
    """PAC-1 C03 & P1-03: Furniture below window sill collides with solid wall material underneath."""
    model = HomeModel("Window Sill Test")
    # Straight 20cm wall along X axis from (0,0) to (500,0)
    model.add_wall("w1", 0, 0, 500, 0, thickness=20.0, height=280.0)
    # Window at sill 50cm, height 50cm (Z: 50..100cm)
    model.add_opening("win1", "Window", wall_id="w1", offset_cm=200.0, width_cm=100.0, height_cm=50.0,
                      opening_type="window", elevation_cm=50.0)
    # Low furniture centered at wall, height 75cm (Z: 0..75cm)
    # Intersects solid wall in Z: 0..50cm (below sill)!
    model.add_furniture("f_low", "Low Unit", x_cm=250.0, y_cm=0.0, width_cm=80.0, depth_cm=80.0, height_cm=75.0, elevation_cm=0.0)
    val = model.validate()
    assert any(f["code"] == "FURNITURE_WALL_COLLISION" for f in val["findings"])

def test_curved_wall_furniture_collision():
    """PAC-1 C03 & P1-03: Curved wall polygon collides with furniture centered along arc."""
    model = HomeModel("Curved Collision Test")
    # 90° curved wall (0,0) -> (400,0) thickness 20cm; arc midpoint is at (200, ~82.8)
    model.add_wall("w_curve", 0, 0, 400, 0, thickness=20.0, arc_extent=90.0)
    # Furniture placed at arc midpoint
    model.add_furniture("f_mid", "Table", x_cm=200.0, y_cm=82.84, width_cm=80.0, depth_cm=80.0, height_cm=75.0)
    val = model.validate()
    assert any(f["code"] == "FURNITURE_WALL_COLLISION" for f in val["findings"])

def test_dynamic_wall_thickness_and_net_masonry_volume():
    """PAC-1 T07, T08 & P1-02: Dynamic wall thickness determines clear core; net masonry volume is computed."""
    # 3x3m room with 10cm walls -> clear core = (3 - 0.10)^2 = 8.410 m²
    m10 = HomeModel("10cm walls")
    m10.add_room("r1", "Room", [(0,0), (300,0), (300,300), (0,300)], with_walls=True, wall_thickness=10.0)
    t10 = m10.takeoff()
    assert round(t10["summary"]["clear_core_area_m2"], 3) == 8.410

    # 3x3m room with 30cm walls -> clear core = (3 - 0.30)^2 = 7.290 m²
    m30 = HomeModel("30cm walls")
    m30.add_room("r1", "Room", [(0,0), (300,0), (300,300), (0,300)], with_walls=True, wall_thickness=30.0)
    t30 = m30.takeoff()
    assert round(t30["summary"]["clear_core_area_m2"], 3) == 7.290

    # 3x3m room with no walls -> clear core = reference area = 9.000 m²
    m0 = HomeModel("no walls")
    m0.add_room("r1", "Room", [(0,0), (300,0), (300,300), (0,300)])
    t0 = m0.takeoff()
    assert round(t0["summary"]["clear_core_area_m2"], 3) == 9.000

    # PAC-1 Section 6.4 Golden Fixture: Two adjacent 3x3m rooms, 7 walls of 0.15m, 1x2.1m door
    golden = HomeModel("Golden Fixture")
    golden.add_room("r1", "Room 1", [(0, 0), (300, 0), (300, 300), (0, 300)], with_walls=True, wall_thickness=15.0)
    golden.add_room("r2", "Room 2", [(300, 0), (600, 0), (600, 300), (300, 300)], with_walls=True, wall_thickness=15.0)
    # Find shared wall at x=300
    shared_w = None
    for w in golden.walls.values():
        if abs(w.x_start - 300.0) < 1.0 and abs(w.x_end - 300.0) < 1.0:
            shared_w = w
            break
    assert shared_w is not None
    golden.add_opening("d1", "Door", wall_id=shared_w.id, offset_cm=50.0, width_cm=100.0, height_cm=210.0, opening_type="door", elevation_cm=0.0)
    t_gold = golden.takeoff()
    assert t_gold["summary"]["net_masonry_volume_m3"] == 8.442
    assert t_gold["summary"]["clear_core_area_m2"] == 16.245
    assert t_gold["summary"]["reference_skirting_length_m"] == 22.0
    assert t_gold["summary"]["clear_face_skirting_length_m"] == 20.8

def test_elevated_door_leaves_skirting_intact():
    """PAC-1 T08 & P1-02: Opening elevated above baseboard zone (>= 10cm) leaves skirting fully intact."""
    golden = HomeModel("Elevated Opening")
    golden.add_room("r1", "Room 1", [(0, 0), (300, 0), (300, 300), (0, 300)], with_walls=True, wall_thickness=15.0)
    golden.add_room("r2", "Room 2", [(300, 0), (600, 0), (600, 300), (300, 300)], with_walls=True, wall_thickness=15.0)
    shared_w = None
    for w in golden.walls.values():
        if abs(w.x_start - 300.0) < 1.0 and abs(w.x_end - 300.0) < 1.0:
            shared_w = w
            break
    # Elevated door/window at sill 100cm, height 100cm
    golden.add_opening("d_elev", "Elevated Door", wall_id=shared_w.id, offset_cm=50.0, width_cm=100.0, height_cm=100.0,
                       opening_type="door", elevation_cm=100.0)
    t = golden.takeoff()
    # Reference skirting remains fully 24m (0m deduction)
    assert t["summary"]["reference_skirting_length_m"] == 24.0

def test_export_level_scoping_svg_html_dxf():
    """PAC-1 E01, E07 & P1-08: Level filtering scopes SVG title, HTML stats, and DXF drawing entities."""
    from cli_anything.sweethome3d.core import export_svg, export_html, export_dxf
    m = HomeModel("Multi-Level Project")
    lvl1 = m.get_level("level1")
    lvl2 = m.add_level("level2", "Second Floor", elevation_cm=300.0, height_cm=280.0)
    m.add_room("r_gnd", "Ground Room", [(0,0), (300,0), (300,300), (0,300)], level_id="level1") # 9 m²
    m.add_room("r_up", "Upper Room", [(0,0), (400,0), (400,400), (0,400)], level_id="level2") # 16 m²
    m.add_wall("w_curve", 0, 0, 400, 0, arc_extent=90.0, level_id="level1")
    m.add_furniture("chair", "Chair", x_cm=150, y_cm=150, level_id="level1")

    with tempfile.TemporaryDirectory() as td:
        svg_path = os.path.join(td, "gnd.svg")
        export_svg(m, svg_path, level_id="level1")
        with open(svg_path, "r", encoding="utf-8") as f:
            svg_content = f.read()
            assert "Total Area: 9.00 m²" in svg_content
            assert "Total Area: 25.00 m²" not in svg_content

        html_path = os.path.join(td, "gnd.html")
        export_html(m, html_path, level_id="level1")
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()
            assert "9.00 m²" in html_content
            assert "25.00 m²" not in html_content

        dxf_path = os.path.join(td, "gnd.dxf")
        export_dxf(m, dxf_path, level_id="level1")
        with open(dxf_path, "r", encoding="utf-8") as f:
            dxf_content = f.read()
            # DXF must contain curved wall polyline and furniture footprint polyline
            assert "WALLS" in dxf_content
            assert "FURNITURE" in dxf_content
            assert "POLYLINE" in dxf_content

def test_incomplete_journal_schema_rejects_and_preserves_target():
    """PAC-1 S01, R10-P0-01: Incomplete journal missing had_target or hashes rejects and preserves target."""
    with tempfile.TemporaryDirectory() as td:
        sess_file = os.path.join(td, "session.json")
        target_file = os.path.join(td, "home.json")
        m = SessionManager(sess_file)
        m.new_project("Base Plan")
        m.save_file(target_file)
        orig_target_content = open(target_file, "r").read()

        # Write incomplete journal missing had_target and sha256 hashes
        incomplete_rec = {
            "schema_version": 1,
            "tx_id": "tx_bad_001",
            "target": target_file,
            "expected_revision": 0,
            "target_revision": 1,
            "staged_session": os.path.join(td, "nonexistent.staged")
        }
        with open(sess_file + ".pending", "w", encoding="utf-8") as pf:
            json.dump(incomplete_rec, pf)

        # Recreating SessionManager triggers _locked_acquire_and_reconcile
        m2 = SessionManager(sess_file)
        # Target must be completely preserved!
        assert os.path.exists(target_file)
        assert open(target_file, "r").read() == orig_target_content
        # Journal must be preserved as evidence, not deleted
        assert os.path.exists(sess_file + ".pending")

def test_reconcile_tampered_current_session_fails_closed():
    """PAC-1 S01, R10-P0-01: Reconcile with altered current session fails closed and preserves journal."""
    with tempfile.TemporaryDirectory() as td:
        sess_file = os.path.join(td, "session.json")
        target_file = os.path.join(td, "home.json")
        m = SessionManager(sess_file)
        m.new_project("Base")
        m.save_file(target_file)
        
        # Simulate Scenario 1 crash: current revision reached target_rev (1), but content altered
        sess_sha = _calc_sha256(sess_file)
        art_sha = _calc_sha256(target_file)
        
        # Tamper current session without changing revision
        with open(sess_file, "r", encoding="utf-8") as f:
            sess_data = json.load(f)
        sess_data["model"]["name"] = "Tampered Model"
        with open(sess_file, "w", encoding="utf-8") as f:
            json.dump(sess_data, f)
            
        pending_rec = {
            "schema_version": 1,
            "tx_id": "tx_tamper_001",
            "session_path": os.path.normcase(os.path.abspath(sess_file)),
            "project_id": "Base",
            "target": target_file,
            "staged_session": os.path.join(td, "session.json.staged"),
            "candidate_session_sha256": sess_sha,
            "candidate_artifact_sha256": art_sha,
            "base_session_sha256": "0" * 64,
            "had_target": True,
            "base_artifact_sha256": art_sha,
            "backup": os.path.join(td, "backup.bak"),
            "expected_revision": 1,
            "target_revision": 2
        }
        with open(sess_file + ".pending", "w", encoding="utf-8") as pf:
            json.dump(pending_rec, pf)

        m2 = SessionManager(sess_file)
        # Pending journal must be preserved because session hash failed verification
        assert os.path.exists(sess_file + ".pending")

def test_export_rejects_hardlink_alias_to_protected_target():
    """PAC-1 S06, R10-P0-02: Export guards detect hard link / alias pointing to protected target."""
    import pytest
    from cli_anything.sweethome3d.sweethome3d_cli import assert_safe_export_target, session_mgr
    orig_active = session_mgr.active_file
    with tempfile.TemporaryDirectory() as td:
        target = os.path.join(td, "protected.json")
        with open(target, "w") as f:
            f.write("{}")
        try:
            session_mgr.active_file = target

            alias = os.path.join(td, "alias.json")
            try:
                os.link(target, alias)
                has_link = True
            except (OSError, NotImplementedError):
                has_link = False

            if has_link:
                with pytest.raises(RuntimeError, match="guarded|alias|hardlink|protected"):
                    assert_safe_export_target(alias)
            else:
                pytest.skip("Filesystem does not support hard links")
        finally:
            session_mgr.active_file = orig_active

def test_opening_non_finite_angle_returns_structured_invalidity():
    """PAC-1 C01, R10-P1-04: Non-finite opening angle does not crash and returns structured findings."""
    m = HomeModel("Inf Opening")
    m.add_wall("w", 0, 0, 400, 0)
    m.add_opening("d", "door", wall_id="w", x_cm=200, y_cm=0, angle_deg=float("inf"))
    res = m.validate()
    assert res["valid"] is False
    assert any(f["code"] == "NON_FINITE_COORDINATE" for f in res["findings"])

def test_large_scale_curved_wall_scale_bounded_collision():
    """PAC-1 C03, R10-P1-03: Error-bounded sampling catches objects on large-radius walls."""
    c = HomeModel("Large Arc Wall")
    w = c.add_wall("arc", 0, 0, 40000, 0, thickness=20, arc_extent=90)
    x, y = w.point_at_offset(w.length * 8.5 / 18)
    c.add_furniture("f", "probe", x_cm=x, y_cm=y, width_cm=10, depth_cm=10, height_cm=75)
    res = c.validate()
    assert any(f["code"] == "FURNITURE_WALL_COLLISION" for f in res["findings"])

def test_parallelogram_clear_core_area():
    """PAC-1 T07, R10-P1-01: Inward-mitred buffer derives exact clear core on parallelograms."""
    m = HomeModel("Parallelogram Room")
    # Vertices (0,0), (300,0), (480,240), (180,240)
    pts = [(0, 0), (300, 0), (480, 240), (180, 240)]
    m.add_room("r_para", "Parallelogram", pts, with_walls=True, wall_thickness=15.0)
    t = m.takeoff()
    assert t["summary"]["floor_area_m2"] == 7.20
    assert abs(t["summary"]["clear_core_area_m2"] - 6.328) < 0.005

def test_unwalled_room_with_unrelated_multilevel_wall():
    """PAC-1 T07, R10-P1-01: Unwalled room does not inherit thickness from unrelated levels."""
    m = HomeModel("Unwalled Multi-Level")
    m.add_level("level2", "Second Floor", elevation_cm=300.0)
    m.add_wall("w_lvl2", 0, 0, 500, 0, thickness=30.0, level_id="level2")
    m.add_room("r_unwalled", "Unwalled", [(0, 0), (300, 0), (300, 300), (0, 300)], level_id="level1")
    t = m.takeoff()
    # Unwalled room must retain exact 9.000 m² clear core
    assert t["summary"]["clear_core_area_m2"] == 9.000

def test_csg_vertical_slab_masonry_volume_multilevel_and_mixed_height():
    """PAC-1 T08, T09, R10-P1-02: CSG vertical slab integration in world Z handles stacked and mixed walls."""
    # Stacked identical walls on Z=0 and Z=3m
    m1 = HomeModel("Stacked")
    m1.add_level("l2", "Level 2", elevation_cm=300.0)
    m1.add_wall("w1", 0, 0, 300, 0, thickness=20, height=280, level_id="level1")
    m1.add_wall("w2", 0, 0, 300, 0, thickness=20, height=280, level_id="l2")
    t1 = m1.takeoff()
    assert t1["summary"]["net_masonry_volume_m3"] == 3.584

    # Disjoint 1m (4m high) + 3m (2m high) 20cm walls
    m2 = HomeModel("Mixed Height")
    m2.add_wall("w1", 0, 0, 100, 0, thickness=20, height=400)
    m2.add_wall("w2", 200, 0, 500, 0, thickness=20, height=200)
    t2 = m2.takeoff()
    assert t2["summary"]["net_masonry_volume_m3"] == 2.240

def test_dxf_opening_semantics_none_and_sliding():
    """PAC-1 E02, E06, R10-P1-06: DXF distinguishes hinged, sliding, and cased openings."""
    from cli_anything.sweethome3d.core import export_dxf
    m = HomeModel("Openings DXF")
    m.add_wall("w", 0, 0, 500, 0, thickness=20)
    m.add_opening("d_hinged", "Hinged Door", wall_id="w", offset_cm=50, width_cm=90, swing="right")
    m.add_opening("d_sliding", "Sliding Door", wall_id="w", offset_cm=200, width_cm=90, swing="sliding")
    m.add_opening("d_cased", "Cased Opening", wall_id="w", offset_cm=350, width_cm=90, swing="none")

    with tempfile.TemporaryDirectory() as td:
        dxf_path = os.path.join(td, "test_doors.dxf")
        export_dxf(m, dxf_path)
        with open(dxf_path, "r", encoding="utf-8") as f:
            content = f.read()
            # Hinged door has leaf line (50,0)->(50,90)
            assert "50.00\n21\n90.00" in content or "50.00\r\n21\r\n90.00" in content
            # Sliding door at (200,0) does NOT have (200,90) hinged leaf line!
            assert "200.00\n21\n90.00" not in content and "200.00\r\n21\r\n90.00" not in content
            # Cased opening at (350,0) does NOT have (350,90) hinged leaf line!
            assert "350.00\n21\n90.00" not in content and "350.00\r\n21\r\n90.00" not in content

def test_reconcile_foreign_project_journal_rejected():
    """PAC-1 S01, R11-P0-01: Replay of a valid journal from project A to project B is rejected."""
    with tempfile.TemporaryDirectory() as td:
        sess_a = os.path.join(td, "session_a.json")
        target_a = os.path.join(td, "home_a.json")
        ma = SessionManager(sess_a)
        ma.new_project("Project A")
        ma.save_file(target_a)

        sess_b = os.path.join(td, "session_b.json")
        target_b = os.path.join(td, "home_b.json")
        mb = SessionManager(sess_b)
        mb.new_project("VICTIM_PROJECT")
        mb.save_file(target_b)

        # Valid journal targeting A copied to B.pending
        valid_a_journal = {
            "schema_version": 1,
            "tx_id": "tx_a_001",
            "session_path": os.path.normcase(os.path.abspath(sess_a)),
            "project_id": "Project A",
            "target": target_a,
            "tmp_target": target_a + ".tmp",
            "candidate_session_sha256": _calc_sha256(sess_a),
            "candidate_artifact_sha256": _calc_sha256(target_a),
            "base_session_sha256": "1" * 64,
            "base_artifact_sha256": _calc_sha256(target_a),
            "had_target": True,
            "backup": target_a + ".bak",
            "staged_session": sess_a + ".staged",
            "expected_revision": 1,
            "target_revision": 2
        }
        b_pending = sess_b + ".pending"
        with open(b_pending, "w", encoding="utf-8") as f:
            json.dump(valid_a_journal, f)

        # Opening session B must reject foreign journal without altering B's model or deleting journal
        mb2 = SessionManager(sess_b)
        assert mb2.model.name == "VICTIM_PROJECT"
        assert os.path.exists(b_pending)

def test_reconcile_tampered_predecessor_scenario_2_fails_closed():
    """PAC-1 S01, S04, R11-P0-01: In Scenario 2, altered predecessor fails closed and preserves journal."""
    with tempfile.TemporaryDirectory() as td:
        sess_file = os.path.join(td, "session.json")
        target_file = os.path.join(td, "home.json")
        m = SessionManager(sess_file)
        m.new_project("Original")
        m.save_file(target_file)
        
        orig_sess_sha = _calc_sha256(sess_file)
        staged_sess = sess_file + ".staged"
        with open(staged_sess, "w", encoding="utf-8") as f:
            f.write(json.dumps({"revision": 3, "model": {"name": "Candidate"}}))
        staged_sha = _calc_sha256(staged_sess)

        # Tamper disk session file while keeping revision 2
        with open(sess_file, "r", encoding="utf-8") as f:
            d = json.load(f)
        d["model"]["name"] = "Altered Predecessor"
        with open(sess_file, "w", encoding="utf-8") as f:
            json.dump(d, f)

        pending_rec = {
            "schema_version": 1,
            "tx_id": "tx_scen2_001",
            "session_path": os.path.normcase(os.path.abspath(sess_file)),
            "project_id": "Original",
            "target": target_file,
            "tmp_target": target_file + ".tmp",
            "candidate_session_sha256": staged_sha,
            "candidate_artifact_sha256": _calc_sha256(target_file),
            "base_session_sha256": orig_sess_sha,
            "base_artifact_sha256": _calc_sha256(target_file),
            "had_target": True,
            "backup": target_file + ".bak",
            "staged_session": staged_sess,
            "expected_revision": 2,
            "target_revision": 3
        }
        with open(sess_file + ".pending", "w", encoding="utf-8") as f:
            json.dump(pending_rec, f)

        # Recovery must fail closed: predecessor was altered!
        m2 = SessionManager(sess_file)
        assert os.path.exists(sess_file + ".pending")
        assert os.path.exists(staged_sess)

def test_export_writer_race_blocked_by_lock():
    """PAC-1 S04, S06, S07, A05, R11-P0-02: Export publication respects writer lock and pending journal."""
    import cli_anything.sweethome3d.sweethome3d_cli as cli_mod
    from cli_anything.sweethome3d.sweethome3d_cli import safe_publish_export
    import pytest
    orig_internal = cli_mod._session_mgr
    with tempfile.TemporaryDirectory() as td:
        sess_file = os.path.join(td, "sess.json")
        out_file = os.path.join(td, "export.svg")
        try:
            m = SessionManager(sess_file)
            m.new_project("Export Lock Test")
            cli_mod._session_mgr = m

            # Simulate writer holding lock and active journal
            pending_file = sess_file + ".pending"
            with open(pending_file, "w") as f:
                f.write("{}")

            with pytest.raises(RuntimeError, match="unresolved recovery journal is active"):
                safe_publish_export(out_file, lambda tmp: open(tmp, "w").write("<svg></svg>"))
        finally:
            cli_mod._session_mgr = orig_internal

def test_per_side_wall_thickness_clear_core():
    """PAC-1 T07, R11-P1-01: Unequal wall thicknesses (10cm top/bottom, 30cm left/right) yield exact 10.730 m²."""
    m = HomeModel("Unequal Walls")
    m.add_wall("w_b", 0, 0, 400, 0, thickness=10.0)
    m.add_wall("w_t", 0, 300, 400, 300, thickness=10.0)
    m.add_wall("w_l", 0, 0, 0, 300, thickness=30.0)
    m.add_wall("w_r", 400, 0, 400, 300, thickness=30.0)
    r = m.add_room("r1", "Living", [(0, 0), (400, 0), (400, 300), (0, 300)])
    t = m.takeoff()
    assert t["summary"]["clear_core_area_m2"] == 10.730

def test_unrelated_wall_with_gap_not_incident():
    """PAC-1 T07, R11-P1-01: Separated wall (10cm gap) does not deduct from room area (12.000 m²)."""
    m = HomeModel("Unrelated Wall")
    m.add_wall("w_unrel", 0, -15, 400, -15, thickness=10.0)
    r = m.add_room("r1", "Room", [(0, 0), (400, 0), (400, 300), (0, 300)])
    t = m.takeoff()
    assert t["summary"]["clear_core_area_m2"] == 12.000

def test_infeasible_room_core_returns_empty():
    """PAC-1 T07, C05, R11-P1-01: 20x20cm room with 30cm walls yields 0.0 m² clear core and structured warning."""
    m = HomeModel("Infeasible Room")
    m.add_wall("w1", 0, 0, 20, 0, thickness=30.0)
    m.add_wall("w2", 0, 20, 20, 20, thickness=30.0)
    m.add_wall("w3", 0, 0, 0, 20, thickness=30.0)
    m.add_wall("w4", 20, 0, 20, 20, thickness=30.0)
    r = m.add_room("r_tiny", "Tiny", [(0, 0), (20, 0), (20, 20), (0, 20)])
    t = m.takeoff()
    assert t["summary"]["clear_core_area_m2"] == 0.0

    res = m.validate()
    assert any(f["code"] == "INFEASIBLE_ROOM_CORE" for f in res["findings"])

def test_opening_cutout_does_not_penetrate_neighbor_wall():
    """PAC-1 T08, T09, R11-P1-02: Clipped cutout avoids cutting parallel neighbor wall (exact 1.712 m³)."""
    m = HomeModel("Clipped Cutout")
    m.add_wall("w_host", 0, 0, 300, 0, thickness=20.0, height=280.0)
    m.add_wall("w_neighbor", 0, 15, 300, 15, thickness=4.0, height=280.0)
    m.add_opening("d1", "door", "w_host", offset_cm=50.0, width_cm=100.0, height_cm=210.0, elevation_cm=0.0)
    t = m.takeoff()
    assert t["masonry_and_walls"]["net_masonry_volume_m3"] == 1.712

def test_curve_sampling_maintains_0_01_error_on_large_chord():
    """PAC-1 T05, C03, R11-P1-03: Sampler maintains <=0.01cm error budget on 1.8km chord wall."""
    import math
    from cli_anything.sweethome3d.core.wall import Wall
    w = Wall("w_large", -90000, 0, 90000, 0, thickness=20.0, arc_extent=90.0)
    steps = w.get_curve_sampling_steps(error_tol=0.01)
    assert steps > 1024
    
    # Calculate analytic sagitta: R * (1 - cos(theta / 2))
    theta_rad = math.radians(90.0)
    c = 180000.0
    R = c / (2.0 * math.sin(theta_rad / 2.0))
    d_theta = theta_rad / steps
    measured_sagitta = R * (1.0 - math.cos(d_theta / 2.0))
    assert measured_sagitta <= 0.01

def test_two_vertex_room_returns_structured_error_without_crash():
    """PAC-1 C01, A04, A07, R11-P1-04: Two-vertex room with furniture returns structured findings without crash."""
    m = HomeModel("Two Point Room")
    r = m.add_room("r_2pt", "Two Point Room", [(0, 0), (100, 0)])
    f = m.add_furniture("f1", "Bed", "bed_king", 50, 50, 0, 100, 100, 50, 0, room_id="r_2pt")
    res = m.validate()
    assert res["valid"] is False
    codes = [f["code"] for f in res["findings"]]
    assert "DEGENERATE_ROOM" in codes
    assert "ZERO_AREA_ROOM" in codes

def test_reconcile_missing_staged_session_with_altered_predecessor_fails_closed():
    """PAC-1 S01, S03, S05, R12-P0-02: Missing staged_session with altered predecessor fails closed and preserves journal."""
    with tempfile.TemporaryDirectory() as td:
        sess_file = os.path.join(td, "sess.json")
        target_file = os.path.join(td, "home.json")
        m = SessionManager(sess_file)
        m.new_project("Base")
        m.save_file(target_file)

        orig_sess_sha = _calc_sha256(sess_file)
        cand_art = os.path.join(td, "candidate_artifact.json")
        with open(cand_art, "w") as f:
            f.write(json.dumps({"model": {"name": "Candidate"}}))
        cand_art_sha = _calc_sha256(cand_art)

        # Alter current predecessor model on disk while leaving expected_revision intact
        with open(sess_file, "r", encoding="utf-8") as f:
            sess_data = json.load(f)
        sess_data["model"]["name"] = "ALTERED_PREDECESSOR"
        with open(sess_file, "w", encoding="utf-8") as f:
            json.dump(sess_data, f)

        pending_rec = {
            "schema_version": 1,
            "tx_id": "tx_missing_stage_001",
            "session_path": os.path.normcase(os.path.abspath(sess_file)),
            "project_id": "Base",
            "target": target_file,
            "tmp_target": cand_art,
            "candidate_session_sha256": "f" * 64,
            "candidate_artifact_sha256": cand_art_sha,
            "base_session_sha256": orig_sess_sha,
            "base_artifact_sha256": _calc_sha256(target_file),
            "had_target": True,
            "backup": target_file + ".bak",
            "staged_session": os.path.join(td, "nonexistent_stage.json"),
            "expected_revision": 2,
            "target_revision": 3
        }
        pending_file = sess_file + ".pending"
        with open(pending_file, "w", encoding="utf-8") as pf:
            json.dump(pending_rec, pf)

        # Recovery must fail closed: predecessor was altered!
        m2 = SessionManager(sess_file)
        assert os.path.exists(pending_file), "Pending journal must NOT be deleted when predecessor is altered!"
        assert os.path.exists(cand_art), "Candidate artifact must NOT be deleted!"

def test_export_staged_overwritten_by_competing_writer_rejected():
    """PAC-1 S04, S06, S07, A05, R12-P0-01: Staged export rejected if competing writer claims destination under lock."""
    import cli_anything.sweethome3d.sweethome3d_cli as cli_mod
    from cli_anything.sweethome3d.sweethome3d_cli import safe_publish_export
    import pytest
    orig_internal = cli_mod._session_mgr
    with tempfile.TemporaryDirectory() as td:
        sess_file = os.path.join(td, "sess.json")
        out_file = os.path.join(td, "active_art.json")
        try:
            m = SessionManager(sess_file)
            m.new_project("EXPORTER_MODEL")
            cli_mod._session_mgr = m

            # Competing writer function that saves to out_file during export staging
            def staging_generator(tmp_path):
                # Write exporter's stage
                with open(tmp_path, "w") as f:
                    f.write(json.dumps({"name": "EXPORTER_MODEL"}))
                
                # Competing writer runs and commits to out_file
                writer_m = SessionManager(sess_file)
                writer_m.new_project("WRITER_MODEL")
                writer_m.save_file(out_file)

            # Export publication must detect competing writer claiming out_file and reject overwrite
            with pytest.raises(RuntimeError, match="concurrent writer committed revision.*active artifact"):
                safe_publish_export(out_file, staging_generator)

            # Assert writer's artifact was NOT overwritten
            with open(out_file, "r") as f:
                content = f.read()
            assert "WRITER_MODEL" in content
            assert "EXPORTER_MODEL" not in content
        finally:
            cli_mod._session_mgr = orig_internal

def test_clear_core_intruding_wall_material_subtracted():
    """PAC-1 T07, R12-P1-01: Wall material intruding into room is subtracted (exact 11.720 m²)."""
    m = HomeModel("Intruding Wall")
    # Room [0, 400] x [0, 300] cm
    r = m.add_room("r1", "Room", [(0, 0), (400, 0), (400, 300), (0, 300)])
    # 20cm wall from (0, -3) to (400, -3), occupies Y in [-13, +7] cm (7cm strip inside room)
    m.add_wall("w_int", 0, -3, 400, -3, thickness=20.0)
    t = m.takeoff()
    assert t["summary"]["clear_core_area_m2"] == 11.720

def test_curve_polyline_actual_segments_stay_within_0_01_error():
    """PAC-1 T05, C03, E02, R12-P1-02: True arc midpoint distance to actual polyline segments <= 0.01cm."""
    import math
    from shapely.geometry import LineString, Point
    from cli_anything.sweethome3d.core.wall import Wall

    w = Wall("w_curve", -90000, 0, 90000, 0, thickness=20.0, arc_extent=90.0)
    steps = w.get_curve_sampling_steps()
    
    # Generate unrounded polyline coordinates
    pts = [w.point_at_offset(i * w.length / steps, round_coords=False) for i in range(steps + 1)]
    
    # Independent circle geometry
    c = 180000.0
    theta_rad = math.radians(90.0)
    sin_half = math.sin(theta_rad / 2.0)
    R = c / (2.0 * sin_half)
    h = R * math.cos(theta_rad / 2.0)
    cx, cy = 0.0, -h

    max_dist = 0.0
    for i in range(steps):
        t_mid = (i + 0.5) / steps
        phi_mid = math.atan2(0 - cy, -90000 - cx) - t_mid * theta_rad
        mid_pt = Point(cx + R * math.cos(phi_mid), cy + R * math.sin(phi_mid))
        seg = LineString([pts[i], pts[i + 1]])
        d = seg.distance(mid_pt)
        if d > max_dist:
            max_dist = d

    assert max_dist <= 0.01, f"Max arc segment deviation {max_dist:.6f} cm exceeds 0.01 cm budget!"

def test_collinear_overlapping_walls_noded_into_three_atoms():
    """PAC-1 T01-T03, R12-P1-03: (0,0)->(300,0) + (150,0)->(450,0) nodes into 3 atoms totaling 4.5m union run."""
    m = HomeModel("Noded Walls")
    m.add_wall("w1", 0, 0, 300, 0, thickness=20.0)
    m.add_wall("w2", 150, 0, 450, 0, thickness=20.0)
    
    atoms = m.get_noded_wall_segments()
    assert len(atoms) == 3
    assert m.total_noded_wall_length_m == 4.50

    m.node_walls()
    assert len(m.walls) == 3
    assert m.total_wall_length_m == 4.50

def test_semantic_wall_noding_preserves_attributes_and_remaps_openings():
    """PAC-1 T01-T04, T06, R13-P1-01: Wall noding preserves source attributes and atomically remaps openings."""
    m = HomeModel("Semantic Noding")
    m.add_wall("host", 0, 0, 300, 0, thickness=15.0, height=280.0,
               extra_attribs={"leftSideColor": "00FFFFFF"})
    m.add_wall("thick", 150, 0, 150, 150, thickness=30.0, height=400.0)
    m.add_opening("door", "Door", wall_id="host", offset_cm=200.0, width_cm=90.0)

    noded_count = m.node_walls()
    assert noded_count == 3

    door = m.openings["door"]
    assert door.wall_id in m.walls, "Door wall_id must point to an existing noded wall!"
    assert door.offset_cm == 50.0, f"Door offset should be remapped to 50.0 cm, got {door.offset_cm}"

    door_host = m.walls[door.wall_id]
    assert door_host.thickness == 15.0
    assert door_host.height == 280.0
    assert door_host.extra_attribs.get("leftSideColor") == "00FFFFFF"

    # Wall 'thick' was not split, should preserve its identity and attributes
    assert "thick" in m.walls
    assert m.walls["thick"].thickness == 30.0
    assert m.walls["thick"].height == 400.0

    val = m.validate()
    assert val["valid"] is True, f"Validation failed after noding: {val['errors']}"

def test_semantic_wall_noding_rejects_conflicting_overlapping_thickness():
    """PAC-1 T04, R13-P1-01: Reject noding if overlapping collinear walls have conflicting thickness."""
    m = HomeModel("Conflicting Overlap")
    m.add_wall("w1", 0, 0, 300, 0, thickness=15.0, height=280.0)
    m.add_wall("w2", 150, 0, 450, 0, thickness=30.0, height=280.0)

    with pytest.raises(ValueError, match="Conflicting wall thickness"):
        m.node_walls()

    # Original model intact
    assert len(m.walls) == 2
    assert "w1" in m.walls and "w2" in m.walls

def test_semantic_wall_noding_rejects_opening_spanning_split_point():
    """PAC-1 T06, R13-P1-01: Refuse noding if an opening would be bisected by a wall split point."""
    m = HomeModel("Opening On Split")
    m.add_wall("host", 0, 0, 300, 0, thickness=15.0, height=280.0)
    m.add_wall("cutter", 150, 0, 150, 150, thickness=15.0, height=280.0)
    # Opening spans from 120 cm to 180 cm across split station 150 cm
    m.add_opening("spanning_door", "Door", wall_id="host", offset_cm=120.0, width_cm=60.0)

    with pytest.raises(ValueError, match="spans across split point"):
        m.node_walls()

    assert len(m.walls) == 2
    assert m.openings["spanning_door"].wall_id == "host"

def test_multilevel_and_curved_physical_wall_run_semantics():
    """PAC-1 T01-T05, T09, R13-P1-02: Level separation and analytic arc lengths in physical wall run."""
    m = HomeModel("Multilevel & Curved Run")
    # level1 exists by default; add level2
    m.add_level("level2", "Second", elevation_cm=300.0, height_cm=280.0)

    # Fixture 1: Two 3m walls at identical XY on floors Z=0 and Z=3m
    m.add_wall("w_fl1", 0, 0, 300, 0, level_id="level1")
    m.add_wall("w_fl2", 0, 0, 300, 0, level_id="level2")
    assert m.total_noded_wall_length_m == 6.00, f"Expected 6.00 m across 2 levels, got {m.total_noded_wall_length_m}"

    # Fixture 2: One 90-deg curved wall (0,0)->(400,0) cm
    m_curved = HomeModel("Curved Run")
    m_curved.add_wall("arc_w", 0, 0, 400, 0, arc_extent=90.0)
    # Analytic arc length = 100 * sqrt(2) * pi cm = 444.288... cm = 4.44 m
    assert m_curved.total_noded_wall_length_m == 4.44, f"Expected 4.44 m analytic arc length, got {m_curved.total_noded_wall_length_m}"

    segments = m_curved.get_noded_wall_segments()
    assert len(segments) == 1
    assert segments[0]["is_curved"] is True
    assert segments[0]["arc_extent"] == 90.0
    assert segments[0]["length_m"] == 4.443

def test_dxf_curve_polyline_vertices_satisfy_0_01_error_budget():
    """PAC-1 E02, E06, R13-P1-03: Serialized DXF VERTEX coordinates strictly satisfy <= 0.01 cm budget."""
    from cli_anything.sweethome3d.core import export_dxf
    m = HomeModel("DXF Precision")
    m.add_wall("big_curve", -90000, 0, 90000, 0, thickness=20.0, height=280.0, arc_extent=90.0)

    with tempfile.TemporaryDirectory() as td:
        dxf_path = os.path.join(td, "precision.dxf")
        export_dxf(m, dxf_path)

        with open(dxf_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f]

        # Parse WALLS / VERTEX group codes (code 10: X, code 20: Y)
        dxf_pts = []
        i = 0
        while i < len(lines):
            if lines[i] == "0" and i + 1 < len(lines) and lines[i + 1] == "VERTEX":
                vx, vy = None, None
                j = i + 2
                while j < len(lines) and lines[j] != "0":
                    if lines[j] == "10":
                        vx = float(lines[j + 1])
                    elif lines[j] == "20":
                        vy = float(lines[j + 1])
                    j += 2
                if vx is not None and vy is not None:
                    dxf_pts.append((vx, vy))
                i = j
            else:
                i += 1

        assert len(dxf_pts) > 100, f"Expected curve polyline vertices, found {len(dxf_pts)}"

        # Compute max distance from true circular arc
        c = 180000.0
        r = c / (2.0 * math.sin(math.pi / 4.0))
        center_x = 0.0
        center_y = -r * math.cos(math.pi / 4.0)

        max_dev = 0.0
        for k in range(len(dxf_pts) - 1):
            p1, p2 = dxf_pts[k], dxf_pts[k + 1]
            mid_x = (p1[0] + p2[0]) / 2.0
            mid_y = (p1[1] + p2[1]) / 2.0
            dev = abs(math.hypot(mid_x - center_x, mid_y - center_y) - r)
            if dev > max_dev:
                max_dev = dev

        assert max_dev <= 0.01, f"Serialized DXF vertex deviation {max_dev:.6f} cm exceeds 0.01 cm budget!"

def test_semantic_noding_antiparallel_remapping_preserves_door_geometry():
    """PAC-1 T02, T06, E02, R14-P1-01: Antiparallel wall remapping preserves door position, center, hinge, and sweep."""
    m = HomeModel("Antiparallel Door Remapping")
    m.add_wall("a", 0, 0, 300, 0)
    m.add_wall("b", 300, 0, 0, 0)
    m.add_opening("d", "Door", wall_id="b", offset_cm=50.0, width_cm=90.0)

    before = m.openings["d"].get_effective_geometry(m.walls["b"])
    m.node_walls()
    d = m.openings["d"]
    after = d.get_effective_geometry(m.walls[d.wall_id])

    assert before["center"] == after["center"], f"Center moved from {before['center']} to {after['center']}"
    assert before["hinge"] == after["hinge"], f"Hinge moved from {before['hinge']} to {after['hinge']}"
    assert before["open_endpoint"] == after["open_endpoint"], f"Open leaf endpoint changed from {before['open_endpoint']} to {after['open_endpoint']}"
    assert m.validate()["valid"] is True

def test_semantic_noding_preserves_native_children_and_rejects_face_conflict():
    """PAC-1 T04, T06, N09, R14-P1-02: Retains wall native children and rejects conflicting face color/material intent."""
    # 1. Unchanged wall preserves extra_children
    m = HomeModel("Children Preservation")
    m.add_wall("keep", 0, 0, 200, 0, extra_children=['<property name="keep" value="v"/>'])
    m.node_walls()
    assert "keep" in m.walls
    assert m.walls["keep"].extra_children == ['<property name="keep" value="v"/>']

    # 2. Conflicting face colors on overlapping walls must be rejected
    m_conflict = HomeModel("Face Conflict")
    m_conflict.add_wall("w1", 0, 0, 300, 0, extra_attribs={"leftSideColor": "00FFFFFF"})
    m_conflict.add_wall("w2", 0, 0, 300, 0, extra_attribs={"leftSideColor": "00000000"})
    with pytest.raises(ValueError, match="Conflicting wall face color/material intent"):
        m_conflict.node_walls()

def test_semantic_noding_direct_model_atomic_and_id_collision_safe():
    """PAC-1 T04, T06, R14-P1-03: ID generation does not collide with retained curves; direct model API is atomic."""
    m = HomeModel("Curve Collision & Atomicity")
    m.add_wall("w", 0, 0, 300, 0)
    m.add_wall("cross", 150, 0, 150, 150)
    m.add_wall("w_1", 600, 0, 1000, 0, arc_extent=90.0)
    
    before_dict = m.to_dict()
    # Should safely node without colliding with 'w_1'
    m.node_walls()
    assert "w_1" in m.walls, "Existing curved wall 'w_1' must be preserved!"
    assert m.walls["w_1"].arc_extent == 90.0
    assert m.validate()["valid"] is True

    # Atomicity check on error: direct API must restore complete model if any error occurs
    m_err = HomeModel("Atomicity Error Check")
    m_err.add_wall("w_a", 0, 0, 300, 0, thickness=15.0)
    m_err.add_wall("w_b", 150, 0, 450, 0, thickness=30.0)  # Conflicting thickness
    before_err = m_err.to_dict()
    try:
        m_err.node_walls()
    except ValueError:
        pass
    assert m_err.to_dict() == before_err, "Direct model node_walls must remain 100% atomic on failure!"

def test_curved_physical_runs_unique_deduplication():
    """PAC-1 T05, T09, E08, R14-P1-04: Coincident curved walls on same level deduplicate to unique physical run."""
    m = HomeModel("Unique Curved Runs")
    m.add_wall("c1", 0, 0, 400, 0, arc_extent=90.0)
    m.add_wall("c2", 0, 0, 400, 0, arc_extent=90.0)

    # 400cm chord with 90 deg arc has analytic length 4.44288... m
    assert m.total_noded_wall_length_m == 4.44, f"Expected 4.44m unique run, got {m.total_noded_wall_length_m}"

    segments = m.get_noded_wall_segments()
    assert len(segments) == 1, f"Expected 1 unique curved segment atom, got {len(segments)}"
    assert segments[0]["length_m"] == 4.443


def test_multilevel_noding_late_failure_restores_direct_openings_atomically():
    """PAC-1 T04, T06, R15-P1-01: Late multilevel noding failure restores mutated openings and preserves to_dict equality."""
    m = HomeModel()
    m.add_wall('host', 0, 0, 300, 0)
    m.add_wall('cross', 150, 0, 150, 150)
    m.add_opening('d', 'Door', wall_id='host', offset_cm=200, width_cm=90)
    m.add_level('level2', 'Second', elevation_cm=300)
    m.add_wall('a2', 0, 0, 300, 0, thickness=15, level_id='level2')
    m.add_wall('b2', 150, 0, 450, 0, thickness=30, level_id='level2')
    assert m.validate()['valid'] is True
    before = m.to_dict()
    with pytest.raises(ValueError, match="Conflicting wall thickness"):
        m.node_walls()
    assert m.to_dict() == before
    d = m.openings['d']
    assert d.wall_id == 'host'
    assert d.offset_cm == 200.0
    assert d.x_cm is None
    assert d.y_cm is None
    assert d.angle_deg is None
    assert m.validate()['valid'] is True


def test_coincident_wall_merge_preserves_all_sources_metadata_and_children():
    """PAC-1 T04, T06, N09, R15-P1-02: Merged coincident walls preserve face metadata and native children across all sources."""
    m = HomeModel()
    m.add_wall('a', 0, 0, 300, 0,
               extra_attribs={'leftSideColor': '00FFFFFF'})
    m.add_wall('b', 0, 0, 300, 0,
               extra_attribs={'rightSideColor': '00112233'},
               extra_children=['<property name="must_survive" value="native"/>'])
    m.node_walls()
    assert len(m.walls) == 1
    w = next(iter(m.walls.values()))
    assert w.extra_attribs.get('leftSideColor') == '00FFFFFF'
    assert w.extra_attribs.get('rightSideColor') == '00112233'
    assert w.extra_children == ['<property name="must_survive" value="native"/>']


def test_coincident_wall_merge_antiparallel_normalizes_and_preserves_face_colors():
    """PAC-1 T04, T06, N09, R15-P1-02: Antiparallel sources correctly normalize side colors to physical world orientation."""
    m = HomeModel()
    # Wall a: 0 -> 300 (dx > 0), left side is physically Y < 0
    m.add_wall('a', 0, 0, 300, 0,
               extra_attribs={'leftSideColor': '00FFFFFF'})
    # Wall b: 300 -> 0 (dx < 0), right side is physically Y < 0 (left of wall a)
    # And b's left side is physically Y > 0 (right of wall a)
    m.add_wall('b', 300, 0, 0, 0,
               extra_attribs={'leftSideColor': '00AABBCC'})
    m.node_walls()
    assert len(m.walls) == 1
    w = next(iter(m.walls.values()))
    assert w.extra_attribs.get('leftSideColor') == '00FFFFFF'
    assert w.extra_attribs.get('rightSideColor') == '00AABBCC'


def test_partial_curved_overlap_interval_union():
    """PAC-1 T05, T09, E08, R15-P1-03: Partial coincident arcs union analytically; contained 45 deg half reports exact 4.44m."""
    m = HomeModel()
    m.add_wall('full', 0, 0, 400, 0, arc_extent=90)
    full = m.walls['full']
    midpoint = full.point_at_offset(full.length / 2)
    m.add_wall('half', 0, 0, *midpoint, arc_extent=45)
    # The 45° half is wholly contained within the 90° arc; unique physical run is 4.44m (not 6.66m)
    assert m.total_noded_wall_length_m == 4.44
    noded_atoms = m.get_noded_wall_segments()
    curved_atoms = [a for a in noded_atoms if a.get('is_curved')]
    assert len(curved_atoms) == 1
    assert curved_atoms[0]['length_m'] == 4.443
    assert abs(curved_atoms[0]['arc_extent'] - 90.0) < 1e-3


def test_curved_wall_different_sweeps_not_collapsed():
    """PAC-1 T05, T09, E08, R15-P1-03: Large chord walls with 90 deg vs 90.004 deg sweeps midpoints differ by >0.01cm."""
    m = HomeModel()
    m.add_wall('w1', 0, 0, 40000, 0, arc_extent=90)
    m.add_wall('w2', 0, 0, 40000, 0, arc_extent=90.004)
    # Midpoints differ by ~0.409 cm (> 0.01 cm budget); must not collapse into single curve
    assert m.total_noded_wall_length_m == 888.58
    noded_atoms = m.get_noded_wall_segments()
    curved_atoms = [a for a in noded_atoms if a.get('is_curved')]
    assert len(curved_atoms) == 2


def test_semantic_noding_property_coalescence_and_conflict_rejection():
    """PAC-1 T04, T06, N09, R16-P1-01: Conflicting properties refuse atomically; duplicate identical coalesce."""
    # 1. Identical duplicate coalesces
    m_same = HomeModel("Identical Property Coalesce")
    m_same.add_wall("w1", 0, 0, 300, 0, extra_children=['<property name="finish-spec" value="oak"/>'])
    m_same.add_wall("w2", 0, 0, 300, 0, extra_children=['<property name="finish-spec" value="oak"/>'])
    m_same.node_walls()
    assert len(m_same.walls) == 1
    w = next(iter(m_same.walls.values()))
    prop_children = [c for c in w.extra_children if 'name="finish-spec"' in c]
    assert len(prop_children) == 1, f"Expected 1 coalesced property, got {len(prop_children)}"

    # 2. Conflicting property values must raise ValueError and roll back
    m_diff = HomeModel("Conflicting Property Refusal")
    m_diff.add_wall("w1", 0, 0, 300, 0, extra_children=['<property name="finish-spec" value="oak"/>'])
    m_diff.add_wall("w2", 0, 0, 300, 0, extra_children=['<property name="finish-spec" value="tile"/>'])
    before_diff = m_diff.to_dict()
    with pytest.raises(ValueError, match="Conflicting wall property 'finish-spec'"):
        m_diff.node_walls()
    assert m_diff.to_dict() == before_diff, "Conflicting property failure must roll back model completely!"


def test_semantic_noding_antiparallel_normalizes_shininess_texture_baseboard():
    """PAC-1 T04, T06, N09, R16-P1-01: Antiparallel sources swap leftSideShininess/texture/baseboard to physical face."""
    m = HomeModel("Antiparallel Shininess & Side Roles")
    m.add_wall("a", 0, 0, 300, 0, extra_attribs={"leftSideColor": "00FFFFFF"})
    # Reversed wall with leftSideShininess="0.9" (which is physically on the right side of wall a)
    m.add_wall("b", 300, 0, 0, 0, extra_attribs={
        "leftSideShininess": "0.9",
        "leftSideTexture": "tex_wood",
        "leftSideBaseboard": "bb_moulding"
    })
    m.node_walls()
    assert len(m.walls) == 1
    w = next(iter(m.walls.values()))
    assert w.extra_attribs.get("leftSideColor") == "00FFFFFF"
    assert w.extra_attribs.get("rightSideShininess") == "0.9"
    assert w.extra_attribs.get("rightSideTexture") == "tex_wood"
    assert w.extra_attribs.get("rightSideBaseboard") == "bb_moulding"
    assert "leftSideShininess" not in w.extra_attribs


def test_circular_union_closed_360_circle_splits_into_supported_arcs():
    """PAC-1 T05, T09, E08, R16-P1-02: Two semicircles unioning to 360 deg split into 2 nondegenerate supported 180 deg arcs."""
    m = HomeModel("Closed Circle Semicircles")
    # Semicircle 1: (100, 0) -> (-100, 0) extent 180
    m.add_wall("semi1", 100, 0, -100, 0, arc_extent=180.0)
    # Semicircle 2: (-100, 0) -> (100, 0) extent 180
    m.add_wall("semi2", -100, 0, 100, 0, arc_extent=180.0)

    # Circumference = 2 * pi * 100 = 628.3185 cm = 6.28 m
    assert m.total_noded_wall_length_m == 6.28
    segments = m.get_noded_wall_segments()
    curved_segs = [s for s in segments if s.get("is_curved")]
    assert len(curved_segs) == 2, f"Expected full circle to split into 2 valid supported 180 deg arcs, got {len(curved_segs)}"
    for s in curved_segs:
        assert abs(s["arc_extent"]) == 180.0
        # Assert nondegenerate chord length (not zero chord)
        chord = math.hypot(s["x_end"] - s["x_start"], s["y_end"] - s["y_start"])
        assert chord > 10.0, f"Arc chord must be nondegenerate, got {chord}"


def test_circular_union_total_representation_budget_bounds_centers_and_radii():
    """PAC-1 T05, T09, E08, R16-P1-02: Total error budget distance(C1, C2) + abs(R1 - R2) <= 0.01cm."""
    from cli_anything.sweethome3d.core.project import _compute_unique_curved_runs
    w1 = Wall("c1", 100, 0, 0, 100, arc_extent=90.0)
    # Wall 2: center (0.009, 0), radius 100.009 -> distance(C1,C2)=0.009, diff_R=0.009 -> sum=0.018 > 0.01
    w2 = Wall("c2", 100.009 + 0.009, 0, 0.009, 100.009, arc_extent=90.0)
    total_len, segs = _compute_unique_curved_runs([w1, w2], epsilon=0.01)
    # Since total displacement 0.018 > 0.01, they must not collapse into a single circle
    assert len(segs) == 2, f"Expected 2 distinct curves under combined 0.01cm budget, got {len(segs)}"


def test_native_xml_wire_sequence_and_clean_attributes():
    """PAC-1 N02, N03, R16-P1-03: Official wire sequence (furniture/openings before walls/rooms) and native attributes."""
    m = HomeModel("Wire Sequence")
    m.add_level("lvl1", "Floor", 0.0, 280.0)
    m.add_wall("w1", 0, 0, 500, 0, level_id="lvl1")
    m.add_opening("d1", "Door", wall_id="w1", offset_cm=50.0, width_cm=90.0, level_id="lvl1")
    m.add_furniture("f1", "Chair", x_cm=100.0, y_cm=100.0, level_id="lvl1")
    m.add_room("r1", "Living Room", [(0, 0), (500, 0), (500, 400), (0, 400)], level_id="lvl1")
    m.add_dimension("dim1", 0, 0, 500, 0, level_id="lvl1", elevation_cm=27.0)

    xml_out = m.to_xml()
    root = ET.fromstring(xml_out)

    # 1. Wire child sequence verification
    child_tags = [c.tag for c in root if c.tag in ("level", "pieceOfFurniture", "doorOrWindow", "wall", "room", "dimensionLine")]
    # pieceOfFurniture and doorOrWindow must precede wall and room
    wall_idx = child_tags.index("wall")
    room_idx = child_tags.index("room")
    door_idx = child_tags.index("doorOrWindow")
    furn_idx = child_tags.index("pieceOfFurniture")
    dim_idx = child_tags.index("dimensionLine")

    assert door_idx < wall_idx, "doorOrWindow must precede wall in native Sweet Home 3D wire sequence"
    assert furn_idx < wall_idx, "pieceOfFurniture must precede wall in native Sweet Home 3D wire sequence"
    assert wall_idx < room_idx, "wall must precede room in native Sweet Home 3D wire sequence"
    assert room_idx < dim_idx, "room must precede dimensionLine in native Sweet Home 3D wire sequence"

    # 2. Room must not contain custom non-native 'area' attribute
    r_elem = root.find("room")
    assert "area" not in r_elem.attrib, "Native <room> must not emit custom 'area' attribute"

    # 3. Dimension must emit elevationStart and elevationEnd
    dim_elem = root.find("dimensionLine")
    assert dim_elem.get("elevationStart") == "27.0"
    assert dim_elem.get("elevationEnd") == "27.0"


def test_native_furniture_high_precision_angle_preservation():
    """PAC-1 N03, R16-P1-03: Native furniture import preserves full float precision (12.3456 deg not truncated to 12.3 deg)."""
    rad = math.radians(12.3456)
    xml_src = f"""<home version="7400">
  <level id="level1" name="Ground" elevation="0" height="280" floorThickness="25"/>
  <pieceOfFurniture id="f" name="Precision Chair" level="level1" x="100" y="100"
      width="50" depth="50" height="80" angle="{rad}"/>
</home>"""
    m = HomeModel.from_xml(xml_src)
    assert round(m.furniture["f"].angle_deg, 4) == 12.3456, f"Expected 12.3456 deg, got {m.furniture['f'].angle_deg}"


def test_sh3d_save_stale_alternate_binary_home_excluded(tmp_path):
    """PAC-1 N08, R16-P1-03: Mutated archive save does not write stale binary Home entry alongside new Home.xml."""
    import zipfile
    archive_path = str(tmp_path / "test_stale_guard.sh3d")
    m = HomeModel("Stale Guard")
    m.add_wall("w1", 0, 0, 500, 0)
    # Simulate an archive that had an old binary 'Home' and an asset texture
    m.extra_zip_entries["Home"] = b"FAKE_OLD_SERIALIZED_HOME_BINARY"
    m.extra_zip_entries["textures/wood.png"] = b"PNG_DATA"

    m.save_sh3d(archive_path)

    with zipfile.ZipFile(archive_path, "r") as zf:
        names = zf.namelist()
        assert "Home.xml" in names, "Home.xml must be present"
        assert "textures/wood.png" in names, "Valid resource textures/wood.png must be preserved"
        assert "Home" not in names, "Stale binary 'Home' entry must NOT be written when saving updated Home.xml"

















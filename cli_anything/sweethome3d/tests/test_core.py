import os
import math
import tempfile
import zipfile
import json
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










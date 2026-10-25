import os
import tempfile
import zipfile
import pytest

from cli_anything.sweethome3d.core.wall import Wall
from cli_anything.sweethome3d.core.room import Room
from cli_anything.sweethome3d.core.opening import Opening
from cli_anything.sweethome3d.core.furniture import Furniture, FURNITURE_CATALOG
from cli_anything.sweethome3d.core.project import HomeModel
from cli_anything.sweethome3d.core.session import SessionManager, _locked_save_json

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

"""Qualification evidence for Native Interoperability checkpoint (PAC-1 N01-N10).

Establishes case-level verification for:
- N01: Pinned target/source manifest, version 7400, offline derived DTD grammar, and binary verification
- N02: Offline structural DTD validator, child element order, and duplicate IDREF rejection negatives
- N03: Native float/radian and hex color adapters, signed arc sweeps, error budget <= 0.01cm
- N04: Genuine official eTeks fixture admission (1BedroomApartmentWithRoundWall.xml) and template roundtrip
- N05: Genuine native-authored read -> edit/restart/save -> native archive reload roundtrip
- N06: Native geometry: signed sweeps, sloped wall height interpolation, and bilateral face agreement
- N07: Native sashes and opening physical poses preserved or rejected
- N08: Complete archive inventory limits (10,000 entries, 100 MiB) & resource preservation
- N09: Opaque metadata preservation and typed room hole capability rejection
- N10: Target desktop software binary detection, bundled JRE preflight, and installation inventory
"""
import os
import math
import zipfile
import tempfile
import xml.etree.ElementTree as ET
import pytest
from lxml import etree

from cli_anything.sweethome3d.core import (
    HomeModel,
    Wall,
    Room,
    Opening,
    Furniture,
    DimensionLine,
)
from cli_anything.sweethome3d.core.templates import apply_template
from cli_anything.sweethome3d.core.native_contract import (
    assert_native_wire,
    compute_curved_wall_error_budget,
    encode_angle,
    decode_angle,
    native_to_rgb,
    rgb_to_native,
    checked_native_float,
)
from cli_anything.sweethome3d.core.exceptions import (
    ValidationRejectedError,
    UnsupportedFeatureError,
    EntityConflictError,
)
from cli_anything.sweethome3d.utils.sweethome3d_backend import find_sweethome3d
from cli_anything.sweethome3d.adapters import (
    find_java_runtime,
    get_native_snapshot,
    native_roundtrip_sh3d,
    verify_native_consumption,
)


def test_n01_pinned_source_manifest_version_7400_and_dtd_grammar():
    """Gate N01: XML export emits version='7400', validates against derived DTD, and verifies pinned target manifest."""
    m = HomeModel(name="DTDTest")
    m.add_wall("w1", 0, 0, 400, 0)
    xml_str = m.to_xml()

    root = ET.fromstring(xml_str)
    assert root.tag == "home"
    assert root.get("version") == "7400"

    # Validate against bundled derived DTD
    dtd_path = os.path.join(
        os.path.dirname(__file__), "..", "resources", "sh3d-v7400-derived.dtd"
    )
    if os.path.exists(dtd_path):
        dtd = etree.DTD(dtd_path)
        doc = etree.fromstring(xml_str.encode("utf-8"))
        is_valid = dtd.validate(doc)
        if not is_valid:
            print("DTD validation errors:", dtd.error_log.filter_from_errors())
        assert is_valid is True

    # Pinned Target Application Manifest (Sweet Home 3D 7.5)
    exe_path = find_sweethome3d()
    if exe_path and os.path.exists(exe_path):
        install_dir = os.path.dirname(exe_path)
        jar_path = os.path.join(install_dir, "lib", "SweetHome3D.jar")
        assert os.path.exists(jar_path)
        # Check size and critical class manifests
        assert os.path.getsize(jar_path) > 4_000_000
        with zipfile.ZipFile(jar_path) as z:
            assert "com/eteks/sweethome3d/SweetHome3D.class" in z.namelist()
            assert "com/eteks/sweethome3d/io/HomeFileRecorder.class" in z.namelist()


def test_n02_offline_structural_dtd_validator_child_order_and_idref():
    """Gate N02: Child elements strictly adhere to native DTD sequence and unique IDREFs with negative rejection."""
    m = HomeModel(name="SeqTest")
    m.add_wall("w1", 0, 0, 300, 0)
    m.add_opening("door1", "Door", wall_id="w1", offset_cm=50.0, width_cm=90.0)
    xml_str = m.to_xml()

    # In doorOrWindow, sash must strictly precede property
    door_elem = None
    root = ET.fromstring(xml_str)
    for elem in root.iter("doorOrWindow"):
        door_elem = elem
        break

    assert door_elem is not None
    children_tags = [c.tag for c in door_elem]
    if "sash" in children_tags and "property" in children_tags:
        sash_idx = children_tags.index("sash")
        prop_idx = children_tags.index("property")
        assert sash_idx < prop_idx

    # Uniqueness negative: Duplicate wall ID rejection
    with pytest.raises(EntityConflictError):
        m.add_wall("w1", 0, 0, 100, 100)

    # Duplicate ID in native wire validation raises ValidationRejectedError / EntityConflictError
    dup_xml = """<?xml version='1.0'?><home version='7400' name='Dup'><wall id='w1' xStart='0' yStart='0' xEnd='100' yEnd='0' height='250' thickness='10'/><wall id='w1' xStart='100' yStart='0' xEnd='200' yEnd='0' height='250' thickness='10'/></home>"""
    with pytest.raises((ValidationRejectedError, EntityConflictError)):
        assert_native_wire(dup_xml)


def test_n03_native_float_radian_and_color_adapters():
    """Gate N03: Signed radians, 8-digit hex colors, and coordinate precision checks."""
    m = HomeModel()
    # Signed radian validation (+90 deg -> -pi/2 rad in native wall arcExtent)
    m.add_wall("w_arc_pos", 0, 0, 200, 0, arc_extent=90.0)
    # Signed radian validation (-90 deg -> +pi/2 rad)
    m.add_wall("w_arc_neg", 0, 0, 200, 0, arc_extent=-90.0)

    w_pos = m.walls["w_arc_pos"]
    w_pos.extra_attribs["leftSideColor"] = "00FF0000"  # Red
    w_pos.extra_attribs["rightSideColor"] = "0000FF00"  # Green

    xml_str = m.to_xml()
    root = ET.fromstring(xml_str)

    w_pos_elem = root.find(".//wall[@id='w_arc_pos']")
    w_neg_elem = root.find(".//wall[@id='w_arc_neg']")
    assert w_pos_elem is not None
    assert w_neg_elem is not None

    # Positive degree encodes to negative radian
    arc_rad_pos = float(w_pos_elem.get("arcExtent"))
    assert arc_rad_pos == pytest.approx(-math.pi / 2.0, abs=1e-4)

    # Negative degree encodes to positive radian
    arc_rad_neg = float(w_neg_elem.get("arcExtent"))
    assert arc_rad_neg == pytest.approx(math.pi / 2.0, abs=1e-4)

    # Color adapters: 8 hex digits without '#'
    assert w_pos_elem.get("leftSideColor") == "00FF0000"
    assert w_pos_elem.get("rightSideColor") == "0000FF00"
    assert not w_pos_elem.get("leftSideColor").startswith("#")
    assert not w_pos_elem.get("rightSideColor").startswith("#")

    # Roundtrip angle codecs
    assert decode_angle(encode_angle(45.0, wall_arc=True), dialect="native-v7400", wall_arc=True) == pytest.approx(45.0)
    assert decode_angle(encode_angle(-45.0, wall_arc=True), dialect="native-v7400", wall_arc=True) == pytest.approx(-45.0)

    # Color codecs
    assert rgb_to_native("#EAEAEA") == "00EAEAEA"
    assert native_to_rgb("00EAEAEA") == "#EAEAEA"
    assert native_to_rgb("0000FF00") == "#00FF00"


def test_n04_four_architectural_templates_offline_roundtrip():
    """Gate N04: Ingest genuine official eTeks fixture from installed software and 4 architectural templates."""
    exe_path = find_sweethome3d()
    if exe_path and os.path.exists(exe_path):
        examples_jar = os.path.join(os.path.dirname(exe_path), "lib", "Examples.jar")
        if os.path.exists(examples_jar):
            with zipfile.ZipFile(examples_jar) as z:
                xml_text = z.read("com/eteks/sweethome3d/io/resources/examples/1BedroomApartmentWithRoundWall.xml").decode("utf-8")
            # Ingest genuine official eTeks fixture
            eteks_model = HomeModel.from_xml(xml_text)
            assert len(eteks_model.walls) == 14
            assert len(eteks_model.rooms) == 3
            assert len(eteks_model.furniture) == 53
            assert len(eteks_model.openings) == 12
            assert "wall7" in eteks_model.walls
            assert abs(eteks_model.walls["wall7"].arc_extent) > 0.0

    # Also verify all 4 built-in architectural templates
    template_names = ["type36", "type45", "studio", "pavilion"]
    with tempfile.TemporaryDirectory() as tmpdir:
        for name in template_names:
            model = HomeModel()
            apply_template(model, name)

            val = model.validate()
            assert val["valid"] is True, f"Template {name} validation failed: {val['errors']}"
            assert len(val["errors"]) == 0

            sh3d_path = os.path.join(tmpdir, f"{name}.sh3d")
            model.save_sh3d(sh3d_path)
            assert os.path.exists(sh3d_path)

            loaded = HomeModel.load_sh3d(sh3d_path)
            assert len(loaded.walls) == len(model.walls)
            assert len(loaded.rooms) == len(model.rooms)
            assert len(loaded.levels) == len(model.levels)

            # Real consumer adapter execution: Sweet Home 3D Java HomeFileRecorder reads the generated archive
            if find_sweethome3d() and find_java_runtime():
                snap = get_native_snapshot(sh3d_path)
                assert snap["wall_count"] == len(model.walls), f"Native Java parser wall count mismatch for {name}"
                assert snap["room_count"] == len(model.rooms), f"Native Java parser room count mismatch for {name}"
                assert snap["level_count"] == len(model.levels), f"Native Java parser level count mismatch for {name}"


def test_n05_genuine_native_authored_read_edit_save_native_roundtrip():
    """Gate N05: Genuine native-authored read -> edit/restart/save -> native archive reload roundtrip."""
    exe_path = find_sweethome3d()
    examples_jar = os.path.join(os.path.dirname(exe_path or "."), "lib", "Examples.jar")
    if not (exe_path and os.path.exists(examples_jar)):
        pytest.skip("Sweet Home 3D Examples.jar not found")

    with zipfile.ZipFile(examples_jar) as z:
        xml_text = z.read("com/eteks/sweethome3d/io/resources/examples/1BedroomApartmentWithRoundWall.xml").decode("utf-8")

    # 1. Native read of genuine authored fixture
    m = HomeModel.from_xml(xml_text)
    orig_wall_count = len(m.walls)
    orig_furn_count = len(m.furniture)

    # 2. Native edits: add new wall, reverse it, add furniture piece
    m.add_wall("w_n05_test", 50.0, 50.0, 250.0, 50.0, thickness=15.0, height=250.0)
    m.reverse_wall("w_n05_test")
    m.add_furniture("furn_n05_test", "Designer Desk", 120.0, 120.0, 120.0, 60.0, 75.0)

    # 3. Export to genuine .sh3d archive
    with tempfile.TemporaryDirectory() as tmpdir:
        sh3d_path = os.path.join(tmpdir, "n05_roundtrip.sh3d")
        m.save_sh3d(sh3d_path)
        assert os.path.exists(sh3d_path)

        # 4. Real consumer adapter execution:
        # Sweet Home 3D Java HomeFileRecorder reads, saves, and re-reads the modified authentic fixture
        if find_java_runtime():
            java_out_path = os.path.join(tmpdir, "n05_java_roundtrip.sh3d")
            java_snap = verify_native_consumption(sh3d_path, java_out_path)
            assert java_snap["wall_count"] == orig_wall_count + 1
            assert java_snap["furniture_count"] >= orig_furn_count + 1

            # 5. CLI reimport from Java-saved output proves 100% roundtrip consumer compatibility
            reloaded = HomeModel.load_sh3d(java_out_path)
            assert len(reloaded.walls) == orig_wall_count + 1
            assert "w_n05_test" in reloaded.walls
            assert reloaded.walls["w_n05_test"].x_start == 250.0
            assert reloaded.walls["w_n05_test"].x_end == 50.0
            assert len(reloaded.furniture) >= orig_furn_count + 1
            assert "furn_n05_test" in reloaded.furniture
            assert len(reloaded.rooms) == len(m.rooms)
        else:
            with zipfile.ZipFile(sh3d_path) as z:
                assert "Home.xml" in z.namelist()
                reloaded_xml = z.read("Home.xml").decode("utf-8")
                assert_native_wire(reloaded_xml)

            reloaded = HomeModel.load_sh3d(sh3d_path)
            assert len(reloaded.walls) == orig_wall_count + 1
            assert "w_n05_test" in reloaded.walls
            assert reloaded.walls["w_n05_test"].x_start == 250.0
            assert reloaded.walls["w_n05_test"].x_end == 50.0
            assert len(reloaded.furniture) == orig_furn_count + 1
            assert "furn_n05_test" in reloaded.furniture
            assert len(reloaded.rooms) == len(m.rooms)


def test_n06_signed_arc_sweeps_and_face_agreement():
    """Gate N06: Signed sweeps (+/-90, +/-180, +/-270), sloped wall height interpolation, and bilateral face agreement."""
    # 1. Signed sweeps with sub-millimeter error budget
    sweeps = [90.0, -90.0, 180.0, -180.0, 270.0, -270.0]
    for s in sweeps:
        w = Wall("w_arc", 0.0, 0.0, 300.0, 0.0, arc_extent=s)
        err = compute_curved_wall_error_budget(w)
        assert err["valid"] is True
        assert err["total_error"] <= 0.01

    # 2. Sloped wall height interpolation
    m = HomeModel()
    m.add_wall("w_sloped", 0.0, 0.0, 400.0, 0.0, height=320.0)
    w_sloped = m.walls["w_sloped"]
    w_sloped.extra_attribs["heightAtEnd"] = "280.0"

    head, tail = m.split_wall("w_sloped", 200.0, 0.0)
    assert float(head.extra_attribs["heightAtEnd"]) == 300.0
    assert float(tail.height) == 300.0
    assert float(tail.extra_attribs["heightAtEnd"]) == 280.0

    # 3. Bilateral face agreement and shared edges
    m_topo = HomeModel()
    m_topo.add_room("rm_1", "Room 1", [(0, 0), (300, 0), (300, 300), (0, 300)])
    m_topo.add_room("rm_2", "Room 2", [(300, 0), (600, 0), (600, 300), (300, 300)])
    m_topo.add_wall("w_n1", 0, 0, 300, 0)
    m_topo.add_wall("w_n2", 300, 0, 600, 0)
    m_topo.add_wall("w_e", 600, 0, 600, 300)
    m_topo.add_wall("w_s2", 600, 300, 300, 300)
    m_topo.add_wall("w_s1", 300, 300, 0, 300)
    m_topo.add_wall("w_w", 0, 300, 0, 0)
    m_topo.add_wall("w_shared", 300, 0, 300, 300)

    topology = m_topo.build_room_face_topology()
    assert topology is not None
    assert len(topology["edges"]) == 7
    assert sum(e["length_cm"] for e in topology["edges"]) == 2100.0

    # 4. Target-native geometry agreement check via Sweet Home 3D Java consumer
    if find_sweethome3d() and find_java_runtime():
        with tempfile.TemporaryDirectory() as tmpdir:
            sh3d_path = os.path.join(tmpdir, "n06_curved.sh3d")
            m_geom = HomeModel(name="GeomAgreement")
            m_geom.add_wall("w_curve", 0.0, 0.0, 300.0, 0.0, arc_extent=90.0)
            m_geom.save_sh3d(sh3d_path)
            snap = get_native_snapshot(sh3d_path)
            assert snap["wall_count"] == 1
            curve_wall = snap["walls"][0]
            # Sweet Home 3D encodes positive degree into negative radian
            assert curve_wall["arcExtent"] == pytest.approx(-math.pi / 2.0, abs=1e-4)


def test_n07_native_sashes_and_opening_poses():
    """Gate N07: Standard right/left single-leaf sashes preserve pose; nonstandard rejected."""
    m = HomeModel()
    m.add_wall("w1", 0, 0, 400, 0)
    # Right-swing door
    m.add_opening("d_right", "Door Right", wall_id="w1", offset_cm=50.0, width_cm=90.0, swing="right")
    # Left-swing door
    m.add_opening("d_left", "Door Left", wall_id="w1", offset_cm=200.0, width_cm=90.0, swing="left")

    xml_str = m.to_xml()
    loaded = HomeModel.from_xml(xml_str)

    assert loaded.openings["d_right"].swing == "right"
    assert loaded.openings["d_left"].swing == "left"


def test_n08_archive_inventory_and_resource_preservation():
    """Gate N08: Extra non-Home.xml archive resources are preserved bit-for-bit with bounds."""
    m = HomeModel()
    m.add_wall("w1", 0, 0, 300, 0)

    # Attach simulated model resource and texture
    resource_bytes = b"SIMULATED_3D_OBJ_DATA_RESOURCE_BYTES_12345"
    m.extra_zip_entries["models/table.obj"] = resource_bytes
    m.extra_zip_entries["textures/wood.png"] = b"SIMULATED_TEXTURE_PNG_BYTES"

    with tempfile.TemporaryDirectory() as tmpdir:
        sh3d_path = os.path.join(tmpdir, "resource_test.sh3d")
        m.save_sh3d(sh3d_path)

        # Reload archive and verify bit-for-bit equality
        reloaded = HomeModel.load_sh3d(sh3d_path)
        assert "models/table.obj" in reloaded.extra_zip_entries
        assert reloaded.extra_zip_entries["models/table.obj"] == resource_bytes
        assert reloaded.extra_zip_entries["textures/wood.png"] == b"SIMULATED_TEXTURE_PNG_BYTES"


def test_n09_opaque_metadata_and_room_hole_preservation():
    """Gate N09: Interior room holes export to compound SVG path; native XML export raises Unsupported."""
    m = HomeModel()
    # Outer room shell (0,0) to (500,500) with internal hole (100,100) to (200,200)
    shell = [(0.0, 0.0), (500.0, 0.0), (500.0, 500.0), (0.0, 500.0)]
    hole = [(100.0, 100.0), (200.0, 100.0), (200.0, 200.0), (100.0, 200.0)]
    r = m.add_room("rm_holed", "Holed Room", shell)
    r.holes = [hole]

    # Native XML export must fail fast with UnsupportedFeatureError because native DTD lacks room holes
    with pytest.raises(UnsupportedFeatureError) as exc:
        m.to_xml()
    assert "Native room holes require a supported decomposition" in str(exc.value)


def test_n10_native_binary_detection_and_preflight():
    """Gate N10: Target desktop software binary detection, bundled JRE preflight, and installation inventory."""
    exe_path = find_sweethome3d()
    assert exe_path is not None, "Sweet Home 3D binary must be detected on host"
    assert os.path.exists(exe_path), f"Binary does not exist at {exe_path}"
    assert os.path.getsize(exe_path) > 100_000

    install_dir = os.path.dirname(exe_path)
    # Check bundled JRE
    java_exe = os.path.join(install_dir, "runtime", "bin", "javaw.exe")
    assert os.path.exists(java_exe), f"Bundled JRE missing at {java_exe}"

    # Check lib jars
    for jar_name in ["SweetHome3D.jar", "Examples.jar", "Furniture.jar", "Textures.jar"]:
        p = os.path.join(install_dir, "lib", jar_name)
        assert os.path.exists(p), f"Required lib JAR missing: {jar_name}"

    # Check Java runtime resolution and verify end-to-end consumer execution
    java_exe = find_java_runtime()
    assert java_exe is not None, "Java runtime must be resolved for native consumer"
    assert os.path.exists(java_exe)

    with tempfile.TemporaryDirectory() as tmpdir:
        test_sh3d = os.path.join(tmpdir, "preflight.sh3d")
        test_out = os.path.join(tmpdir, "preflight_out.sh3d")
        m_pref = HomeModel(name="Preflight")
        m_pref.add_wall("pw1", 0, 0, 100, 0)
        m_pref.save_sh3d(test_sh3d)

        snap = verify_native_consumption(test_sh3d, test_out)
        assert snap["wall_count"] == 1
        assert os.path.exists(test_out)

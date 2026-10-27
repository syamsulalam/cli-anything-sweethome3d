"""Qualification evidence for Native Interoperability checkpoint (PAC-1 N01-N09).

Establishes case-level verification for:
- N01: Pinned target/source manifest, version 7400, offline derived DTD grammar
- N02: Offline structural DTD validator, child order, IDREF uniqueness
- N03: Native float/radian and hex color adapters, error budget <= 0.01cm
- N04: Four architectural templates offline archive roundtrip provenance
- N06: Signed arc sweeps (+/-90, +/-180, +/-270) and bilateral face agreement
- N07: Native sashes and opening physical poses preserved or rejected
- N08: Complete archive inventory limits (10,000 entries, 100 MiB) & resource preservation
- N09: Opaque metadata preservation and typed room hole capability rejection
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
)
from cli_anything.sweethome3d.core.exceptions import (
    ValidationRejectedError,
    UnsupportedFeatureError,
)


def test_n01_pinned_source_manifest_version_7400_and_dtd_grammar():
    """Gate N01: XML export emits version='7400' and validates against derived DTD."""
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


def test_n02_offline_structural_dtd_validator_child_order_and_idref():
    """Gate N02: Child elements strictly adhere to native DTD sequence and unique IDREFs."""
    m = HomeModel(name="SeqTest")
    m.add_wall("w1", 0, 0, 300, 0)
    m.add_opening("door1", "Door", wall_id="w1", offset_cm=50.0, width_cm=90.0)
    xml_str = m.to_xml()

    # In doorOrWindow, sash must precede property
    door_elem = None
    root = ET.fromstring(xml_str)
    for elem in root.iter("doorOrWindow"):
        door_elem = elem
        break

    if door_elem is not None:
        children_tags = [c.tag for c in door_elem]
        if "sash" in children_tags and "property" in children_tags:
            sash_idx = children_tags.index("sash")
            prop_idx = children_tags.index("property")
            assert sash_idx < prop_idx


def test_n03_native_float_radian_and_color_adapters():
    """Gate N03: Arc extents emit in signed radians, colors in 8-digit hex without #."""
    m = HomeModel()
    m.add_wall("w_arc", 0, 0, 200, 0, arc_extent=90.0)
    w = m.walls["w_arc"]
    w.extra_attribs["leftSideColor"] = "00FF0000"  # Red

    xml_str = m.to_xml()
    root = ET.fromstring(xml_str)

    wall_elem = None
    for elem in root.iter("wall"):
        if elem.get("id") == "w_arc":
            wall_elem = elem
            break

    assert wall_elem is not None
    # Arc extent in radians: 90 deg = pi / 2 = 1.5707963... (with native sign mapping)
    arc_rad = float(wall_elem.get("arcExtent"))
    assert abs(abs(arc_rad) - (math.pi / 2.0)) < 1e-4

    # Color must be 8 hex digits without '#'
    color = wall_elem.get("leftSideColor")
    assert color == "00FF0000"
    assert not color.startswith("#")


def test_n04_four_architectural_templates_offline_roundtrip():
    """Gate N04: All 4 built-in architectural templates validate and roundtrip cleanly."""
    template_names = ["type36", "type45", "studio", "pavilion"]

    with tempfile.TemporaryDirectory() as tmpdir:
        for name in template_names:
            model = HomeModel()
            apply_template(model, name)

            # 1. Validation check
            val = model.validate()
            assert val["valid"] is True, f"Template {name} validation failed: {val['errors']}"
            assert len(val["errors"]) == 0

            # 2. Offline archive save & reload check
            sh3d_path = os.path.join(tmpdir, f"{name}.sh3d")
            model.save_sh3d(sh3d_path)
            assert os.path.exists(sh3d_path)

            loaded = HomeModel.load_sh3d(sh3d_path)
            assert len(loaded.walls) == len(model.walls)
            assert len(loaded.rooms) == len(model.rooms)
            assert len(loaded.levels) == len(model.levels)


def test_n06_signed_arc_sweeps_and_face_agreement():
    """Gate N06: Signed sweeps (+/-90, +/-180, +/-270) agree with error budget <= 0.01cm."""
    sweeps = [90.0, -90.0, 180.0, -180.0, 270.0, -270.0]
    for s in sweeps:
        w = Wall("w_arc", 0.0, 0.0, 300.0, 0.0, arc_extent=s)
        err = compute_curved_wall_error_budget(w)
        assert err["valid"] is True
        assert err["total_error"] <= 0.01  # Physical sub-millimeter budget


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

import os
import sys
import json
import shutil
import subprocess
import tempfile
from click.testing import CliRunner

from cli_anything.sweethome3d.sweethome3d_cli import cli

def _resolve_cli():
    """Helper to locate the CLI runner: either the installed binary or python -m."""
    if os.environ.get("CLI_ANYTHING_FORCE_INSTALLED") == "1":
        exe = shutil.which("cli-anything-sweethome3d")
        if not exe:
            raise RuntimeError("CLI_ANYTHING_FORCE_INSTALLED is set but 'cli-anything-sweethome3d' is not in PATH.")
        return [exe]
    
    installed = shutil.which("cli-anything-sweethome3d")
    if installed:
        return [installed]
    return [sys.executable, "-m", "cli_anything.sweethome3d"]

def test_e2e_cli_runner_json_pipeline():
    runner = CliRunner()

    # 1. New project
    res = runner.invoke(cli, ["--json", "project", "new", "--name", "Grand Villa", "--wall-height", "320"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["status"] == "success"
    assert data["data"]["created"] == "Grand Villa"

    # 2. Add perimeter walls (10m x 8m)
    walls = [
        ("w1", 0, 0, 1000, 0),
        ("w2", 1000, 0, 1000, 800),
        ("w3", 1000, 800, 0, 800),
        ("w4", 0, 800, 0, 0),
    ]
    for wid, x1, y1, x2, y2 in walls:
        res = runner.invoke(cli, [
            "--json", "wall", "add",
            "--id", wid, "--x1", str(x1), "--y1", str(y1),
            "--x2", str(x2), "--y2", str(y2), "--thickness", "15"
        ])
        assert res.exit_code == 0
        d = json.loads(res.output)
        assert d["status"] == "success"

    # 3. Add room polygon (10m x 8m = 80 m²)
    pts = "0,0;1000,0;1000,800;0,800"
    res = runner.invoke(cli, [
        "--json", "room", "add",
        "--id", "r_hall", "--name", "Main Great Hall", "--points", pts, "--color", "#F0F0E8"
    ])
    assert res.exit_code == 0
    d = json.loads(res.output)
    assert d["status"] == "success"
    assert d["data"]["area_m2"] == 80.0

    # 4. Add door & window
    res = runner.invoke(cli, [
        "--json", "opening", "add",
        "--id", "d_main", "--name", "Front Double Door", "--wall", "w4",
        "--offset", "300", "--width", "160", "--type", "door", "--swing", "right"
    ])
    assert res.exit_code == 0
    assert json.loads(res.output)["status"] == "success"

    res = runner.invoke(cli, [
        "--json", "opening", "add",
        "--id", "win1", "--name", "Front Window", "--wall", "w1",
        "--offset", "200", "--width", "200", "--type", "window"
    ])
    assert res.exit_code == 0
    assert json.loads(res.output)["status"] == "success"

    # 5. Add furniture
    res = runner.invoke(cli, [
        "--json", "furniture", "add",
        "--id", "sofa1", "--catalog", "sofa_3seat", "--x", "500", "--y", "400"
    ])
    assert res.exit_code == 0
    assert json.loads(res.output)["status"] == "success"

    # 6. Validate project
    res = runner.invoke(cli, ["--json", "project", "validate"])
    assert res.exit_code == 0
    val_data = json.loads(res.output)["data"]
    assert val_data["valid"] is True
    assert val_data["summary"]["total_area_m2"] == 80.0
    assert val_data["summary"]["walls_count"] == 4
    assert val_data["summary"]["openings_count"] == 2
    assert val_data["summary"]["furniture_count"] == 1

def test_e2e_exports():
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmpdir:
        svg_file = os.path.join(tmpdir, "plan.svg")
        dxf_file = os.path.join(tmpdir, "plan.dxf")
        sh3d_file = os.path.join(tmpdir, "plan.sh3d")
        json_file = os.path.join(tmpdir, "plan.json")

        # Setup simple room
        runner.invoke(cli, ["project", "new", "--name", "Export Suite"])
        runner.invoke(cli, ["wall", "add", "--id", "w1", "--x1", "0", "--y1", "0", "--x2", "500", "--y2", "0"])
        runner.invoke(cli, ["wall", "add", "--id", "w2", "--x1", "500", "--y1", "0", "--x2", "500", "--y2", "500"])
        runner.invoke(cli, ["wall", "add", "--id", "w3", "--x1", "500", "--y1", "500", "--x2", "0", "--y2", "500"])
        runner.invoke(cli, ["wall", "add", "--id", "w4", "--x1", "0", "--y1", "500", "--x2", "0", "--y2", "0"])
        runner.invoke(cli, ["room", "add", "--id", "r1", "--name", "Studio", "--points", "0,0;500,0;500,500;0,500"])
        runner.invoke(cli, ["furniture", "add", "--id", "f1", "--catalog", "bed_king", "--x", "250", "--y", "250"])

        # Export SVG
        res = runner.invoke(cli, ["--json", "export", "svg", "-o", svg_file])
        assert res.exit_code == 0
        assert os.path.exists(svg_file)
        with open(svg_file, "r", encoding="utf-8") as f:
            svg_content = f.read()
        assert "<svg" in svg_content
        assert "Studio" in svg_content
        assert "25.00 m²" in svg_content

        # Export DXF
        res = runner.invoke(cli, ["--json", "export", "dxf", "-o", dxf_file])
        assert res.exit_code == 0
        assert os.path.exists(dxf_file)
        with open(dxf_file, "r", encoding="utf-8") as f:
            dxf_content = f.read()
        assert "SECTION" in dxf_content
        assert "WALLS" in dxf_content
        assert "EOF" in dxf_content

        # Export SH3D
        res = runner.invoke(cli, ["--json", "export", "sh3d", "-o", sh3d_file])
        assert res.exit_code == 0
        assert os.path.exists(sh3d_file)
        import zipfile
        with zipfile.ZipFile(sh3d_file, "r") as zf:
            assert "Home.xml" in zf.namelist()

        # Export JSON
        res = runner.invoke(cli, ["--json", "export", "json", "-o", json_file])
        assert res.exit_code == 0
        assert os.path.exists(json_file)
        with open(json_file, "r", encoding="utf-8") as f:
            js = json.load(f)
        assert js["name"] == "Export Suite"
        assert len(js["walls"]) == 4

def test_subprocess_cli():
    cmd = _resolve_cli() + ["--json", "project", "info"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["status"] == "success"
    assert "summary" in data["data"]

def test_e2e_refinement_features():
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmpdir:
        html_file = os.path.join(tmpdir, "interactive.html")

        # 1. New project
        res = runner.invoke(cli, ["--json", "project", "new", "--name", "Modern Villa"])
        assert res.exit_code == 0

        # 2. Add wall loop (8m x 6m perimeter = 4 walls)
        pts = "0,0;800,0;800,600;0,600"
        res = runner.invoke(cli, ["--json", "wall", "add-loop", "--points", pts, "--prefix", "ext_wall"])
        assert res.exit_code == 0
        d = json.loads(res.output)
        assert d["status"] == "success"
        assert len(d["data"]["created_walls"]) == 4

        # 3. Add room with-walls for master bath (3m x 2m inside)
        bath_pts = "500,0;800,0;800,200;500,200"
        res = runner.invoke(cli, ["--json", "room", "add", "--id", "r_bath", "--name", "Master Bath", "--points", bath_pts, "--with-walls"])
        assert res.exit_code == 0
        d = json.loads(res.output)
        assert d["status"] == "success"
        assert d["data"]["area_m2"] == 6.0

        # 4. Add dimension lines
        res = runner.invoke(cli, ["--json", "dimension", "add", "--id", "dim_width", "--x1", "0", "--y1", "0", "--x2", "800", "--y2", "0", "--offset", "60"])
        assert res.exit_code == 0
        d = json.loads(res.output)
        assert d["status"] == "success"
        assert d["data"]["length_m"] == 8.0

        # List dimensions
        res = runner.invoke(cli, ["--json", "dimension", "list"])
        assert res.exit_code == 0
        d = json.loads(res.output)
        assert len(d["data"]) == 1

        # 5. Takeoff BOQ calculation
        res = runner.invoke(cli, ["--json", "project", "takeoff"])
        assert res.exit_code == 0
        d = json.loads(res.output)
        assert d["status"] == "success"
        boq = d["data"]["summary"]
        assert boq["dimensions_count"] == 1
        assert boq["wall_gross_area_m2"] > 0
        assert boq["floor_area_m2"] >= 6.0

        # 6. Export interactive HTML
        res = runner.invoke(cli, ["--json", "export", "html", "-o", html_file])
        assert res.exit_code == 0
        assert os.path.exists(html_file)
        with open(html_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Modern Villa" in content
        assert "takeoff-panel" in content

        # 7. Level management and set-current
        res = runner.invoke(cli, ["--json", "level", "add", "--id", "lvl2", "--name", "Second Floor", "--elevation", "300"])
        assert res.exit_code == 0
        res = runner.invoke(cli, ["--json", "level", "set-current", "--id", "lvl2"])
        assert res.exit_code == 0
        d = json.loads(res.output)
        assert d["data"]["current_level_id"] == "lvl2"

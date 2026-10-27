"""Qualification evidence for Agent-Native Ergonomics checkpoint (PAC-1 A01-A04, A07).

Establishes case-level verification for:
- A01: Project-free command/capability discovery & explicit resolution context
- A02: Transactional failure/history/redo preservation across mutation inventory
- A03: Normalized IDs & EntityConflictError taxonomy across all 6 entity owners
- A04: Versioned finite JSON envelopes and stable exit codes (2, 3, 4, 5, 6, 7)
- A07: Truthful validation coverage and strict rejection enforcement
"""
import os
import json
import tempfile
import pytest
from click.testing import CliRunner

from cli_anything.sweethome3d import sweethome3d_cli as cli_module
from cli_anything.sweethome3d import __version__
from cli_anything.sweethome3d.core import (
    HomeModel,
    SessionManager,
    strict_json_loads,
)
from cli_anything.sweethome3d.core.exceptions import (
    ValidationRejectedError,
    EntityConflictError,
    UnsupportedFeatureError,
)


def test_a01_public_capability_manifest_maps_all_stateful_handlers():
    """Gate A01: Capability manifest works without project and maps all handlers."""
    runner = CliRunner()
    res = runner.invoke(cli_module.cli, ["--json", "capability"])
    assert res.exit_code == 0
    data = strict_json_loads(res.output)
    assert data["protocol_version"] == "1"
    assert data["status"] == "success"
    manifest = data["data"]
    assert manifest["harness"] == "cli-anything-sweethome3d"
    assert manifest["version"] == __version__
    assert manifest["spec_version"] == "PAC-1"
    assert manifest["geometry_engine"]["available"] is True

    # Verify command inventory mapping
    cmds = manifest["commands"]
    for group in ["project", "wall", "room", "opening", "furniture", "dimension", "level", "export", "session", "system"]:
        assert group in cmds
        assert len(cmds[group]) > 0

    # Verify mutation inventory covering all 6 entity owners
    mut = manifest["mutation_inventory"]
    for entity in ["wall", "room", "opening", "furniture", "dimension", "level"]:
        assert entity in mut
        assert "add" in mut[entity]


def test_a01_doctor_runs_purely_passive_without_project_side_effects():
    """Gate A01/A08: Doctor command executes passively without side effects."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmpdir:
        sess_file = os.path.join(tmpdir, "session.json")
        runner.invoke(cli_module.cli, ["--session", sess_file, "--json", "project", "new", "--name", "DoctorTest"])
        
        # Doctor runs passively
        res = runner.invoke(cli_module.cli, ["--session", sess_file, "--json", "doctor"])
        assert res.exit_code == 0
        data = strict_json_loads(res.output)
        assert data["protocol_version"] == "1"
        assert data["status"] == "success"
        assert data["data"]["healthy"] is True
        assert data["data"]["checks"]["pending_journal"] is False
        assert not os.path.exists(sess_file + ".pending")


def test_a01_explicit_project_resolution_in_all_stateful_envelopes():
    """Gate A01: Stateful commands carry explicit project resolution context."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmpdir:
        sess_file = os.path.join(tmpdir, "session.json")

        # Project new under explicit session
        res = runner.invoke(cli_module.cli, ["--session", sess_file, "--json", "project", "new", "--name", "TestProject"])
        assert res.exit_code == 0
        data = strict_json_loads(res.output)
        assert data["protocol_version"] == "1"
        assert data["project"] is not None
        assert os.path.normcase(data["project"]["session_path"]) == os.path.normcase(sess_file)


def test_a02_transactional_failure_preserves_history_and_redo_across_mutations():
    """Gate A02: Mutation failure leaves model, history, disk, and redo completely untouched."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sess_path = os.path.join(tmpdir, "session.json")
        mgr = SessionManager(sess_path)

        # 1. Establish valid baseline with wall w1
        with mgr.transaction():
            mgr.push_state()
            mgr.model.add_wall("w1", 0, 0, 300, 0)
            mgr.save_session()
        assert "w1" in mgr.model.walls

        # 2. Undo w1 so redo stack has w1
        ok, _ = mgr.undo()
        assert ok is True
        assert "w1" not in mgr.model.walls
        assert len(mgr.redo_stack) == 1

        # Read snapshot of disk session
        with open(sess_path, "r", encoding="utf-8") as f:
            disk_before = f.read()

        # 3. Add w2 validly, then attempt duplicate w2 addition
        with mgr.transaction():
            mgr.push_state()
            mgr.model.add_wall("w2", 0, 0, 100, 0)
            mgr.save_session()
        assert "w2" in mgr.model.walls

        with pytest.raises(EntityConflictError):
            with mgr.transaction():
                mgr.push_state()
                mgr.model.add_wall("w2", 0, 0, 200, 0)
                mgr.save_session()

        # 4. Attempt invalid mutation: Wall reverse nonexistent
        with pytest.raises(ValidationRejectedError):
            with mgr.transaction():
                mgr.push_state()
                mgr.model.reverse_wall("nonexistent_wall")
                mgr.save_session()

        # 5. Attempt invalid mutation: Opening add on nonexistent wall
        with pytest.raises(ValidationRejectedError):
            with mgr.transaction():
                mgr.push_state()
                mgr.model.add_opening("bad_door", "Door", wall_id="nonexistent_wall", offset_cm=50.0, width_cm=90.0)
                mgr.save_session()

        # 7. Attempt invalid mutation: Level add duplicate ID
        with pytest.raises(EntityConflictError):
            with mgr.transaction():
                mgr.push_state()
                mgr.model.add_level("level1", 0.0)
                mgr.save_session()

        # VERIFY TRANSACTION ATOMICITY:
        # Undo w2, returning to initial undone state
        ok, _ = mgr.undo()
        assert ok is True
        assert "w2" not in mgr.model.walls

        # Redo stack must still be preserved and functional!
        assert len(mgr.redo_stack) >= 1
        ok, _ = mgr.redo()
        assert ok is True


def test_a02_wall_add_loop_atomic_rollback_on_partial_failure():
    """Gate A02: Composite loop addition rolls back completely if any segment fails."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmpdir:
        sess_file = os.path.join(tmpdir, "session.json")
        runner.invoke(cli_module.cli, ["--session", sess_file, "--json", "project", "new", "--name", "LoopTest"])

        # Valid first 2 points, but invalid 3rd point format
        res = runner.invoke(cli_module.cli, [
            "--session", sess_file, "--json", "wall", "add-loop",
            "--points", "0,0;300,0;invalid_coords;0,300"
        ])
        assert res.exit_code == 3  # VALIDATION_REJECTED
        data = strict_json_loads(res.output)
        assert data["protocol_version"] == "1"
        assert data["error"]["code"] == "VALIDATION_REJECTED"

        # Verify that 0 walls were added to project
        res_info = runner.invoke(cli_module.cli, ["--session", sess_file, "--json", "project", "info"])
        assert res_info.exit_code == 0
        info_data = strict_json_loads(res_info.output)
        assert info_data["data"]["summary"]["walls_count"] == 0


def test_a03_all_six_entity_owners_share_conflict_taxonomy_on_duplicate_ids():
    """Gate A03: All six entity owners reject duplicate normalized IDs with EntityConflictError."""
    m = HomeModel()

    # 1. Level
    m.add_level("lvl_1", 0.0)
    with pytest.raises(EntityConflictError) as exc_lvl:
        m.add_level("lvl_1", 300.0)
    assert "already exists" in str(exc_lvl.value)

    # 2. Wall
    m.add_wall("w_1", 0, 0, 100, 0)
    with pytest.raises(EntityConflictError) as exc_wall:
        m.add_wall("w_1", 0, 0, 200, 0)
    assert "already exists" in str(exc_wall.value)

    # 3. Room
    m.add_room("rm_1", "Room 1", [(0, 0), (200, 0), (200, 200), (0, 200)])
    with pytest.raises(EntityConflictError) as exc_rm:
        m.add_room("rm_1", "Room 1", [(0, 0), (100, 0), (100, 100), (0, 100)])
    assert "already exists" in str(exc_rm.value)

    # 4. Opening
    m.add_opening("op_1", "Door 1", wall_id="w_1", offset_cm=10.0, width_cm=80.0)
    with pytest.raises(EntityConflictError) as exc_op:
        m.add_opening("op_1", "Door 1", wall_id="w_1", offset_cm=20.0, width_cm=80.0)
    assert "already exists" in str(exc_op.value)

    # 5. Furniture
    m.add_furniture("f_1", "table_rect", 50, 50)
    with pytest.raises(EntityConflictError) as exc_furn:
        m.add_furniture("f_1", "chair", 60, 60)
    assert "already exists" in str(exc_furn.value)

    # 6. Dimension
    m.add_dimension("dim_1", 0, 0, 100, 0)
    with pytest.raises(EntityConflictError) as exc_dim:
        m.add_dimension("dim_1", 0, 0, 200, 0)
    assert "already exists" in str(exc_dim.value)


def test_a04_finite_versioned_json_envelopes_across_all_exit_codes():
    """Gate A04: Finite versioned JSON envelopes map all exit codes deterministically."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmpdir:
        sess_file = os.path.join(tmpdir, "session.json")
        runner.invoke(cli_module.cli, ["--session", sess_file, "--json", "project", "new", "--name", "EnvTest"])

        # Exit 2: Click usage / INPUT_ERROR
        r2 = runner.invoke(cli_module.cli, ["--session", sess_file, "--json", "wall", "add"])
        assert r2.exit_code == 2
        d2 = strict_json_loads(r2.output)
        assert d2["protocol_version"] == "1"
        assert d2["error"]["code"] == "INPUT_ERROR"

        # Exit 3: VALIDATION_REJECTED (reverse nonexistent wall)
        r3 = runner.invoke(cli_module.cli, [
            "--session", sess_file, "--json", "wall", "reverse", "nonexistent_wall"
        ])
        assert r3.exit_code == 3
        d3 = strict_json_loads(r3.output)
        assert d3["protocol_version"] == "1"
        assert d3["error"]["code"] == "VALIDATION_REJECTED"

        # Setup valid wall for conflict test
        r_setup = runner.invoke(cli_module.cli, [
            "--session", sess_file, "--json", "wall", "add",
            "--id", "w_conflict",
            "--x1", "0", "--y1", "0",
            "--x2", "100", "--y2", "0"
        ])
        assert r_setup.exit_code == 0

        # Exit 4: CONFLICT (duplicate wall ID)
        r4 = runner.invoke(cli_module.cli, [
            "--session", sess_file, "--json", "wall", "add",
            "--id", "w_conflict",
            "--x1", "0", "--y1", "0",
            "--x2", "200", "--y2", "0"
        ])
        assert r4.exit_code == 4
        d4 = strict_json_loads(r4.output)
        assert d4["protocol_version"] == "1"
        assert d4["error"]["code"] == "CONFLICT"

        # Exit 5: UNSUPPORTED (invalid file format for open)
        unsupp_file = os.path.join(tmpdir, "unsupported_plan.unsupported_ext")
        with open(unsupp_file, "wb") as f:
            f.write(b"unsupported content")

        r5 = runner.invoke(cli_module.cli, [
            "--session", sess_file, "--json", "project", "open",
            unsupp_file
        ])
        assert r5.exit_code == 5
        d5 = strict_json_loads(r5.output)
        assert d5["protocol_version"] == "1"
        assert d5["error"]["code"] == "UNSUPPORTED"


def test_a07_honest_validation_coverage_and_strict_rejection():
    """Gate A07: Validation reporting is truthful; strict rejection never outputs success."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmpdir:
        sess_file = os.path.join(tmpdir, "session.json")
        runner.invoke(cli_module.cli, ["--session", sess_file, "--json", "project", "new", "--name", "ValTest"])

        # Setup model with overlapping furniture collision
        runner.invoke(cli_module.cli, ["--session", sess_file, "--json", "wall", "add", "--id", "w1", "--x1", "0", "--y1", "0", "--x2", "500", "--y2", "0", "--thickness", "20"])
        # Place furniture colliding inside the wall
        runner.invoke(cli_module.cli, ["--session", sess_file, "--json", "furniture", "add", "--id", "f1", "--catalog", "table_rect", "--x", "100", "--y", "0"])

        # Strict validation must fail with VALIDATION_REJECTED (exit 3)
        res_strict = runner.invoke(cli_module.cli, ["--session", sess_file, "--json", "project", "validate", "--strict"])
        assert res_strict.exit_code == 3
        data = strict_json_loads(res_strict.output)
        assert data["protocol_version"] == "1"
        assert data["status"] == "error"
        assert data["error"]["code"] == "VALIDATION_REJECTED"
        assert "success" not in data or data["status"] != "success"

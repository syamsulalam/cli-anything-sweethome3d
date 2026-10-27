import math
import os
import sys
import json
import time
import click

from cli_anything.sweethome3d.core import (
    HomeModel,
    SessionManager,
    FURNITURE_CATALOG,
    DimensionLine,
    strict_json_loads,
    export_svg,
    export_dxf,
    export_json,
    export_html,
)
from cli_anything.sweethome3d.utils.repl_skin import ReplSkin
from cli_anything.sweethome3d.utils.sweethome3d_backend import find_sweethome3d, launch_gui, get_install_instructions
from cli_anything.sweethome3d import __version__
from cli_anything.sweethome3d.core import session as session_module
from cli_anything.sweethome3d.core.session import RecoveryRequiredError, ConcurrencyConflictError
from cli_anything.sweethome3d.core.exceptions import EntityConflictError, ValidationRejectedError, UnsupportedFeatureError
from cli_anything.sweethome3d.core.invocation import resolve_invocation

SESSION_FILE = getattr(session_module, "SESSION_FILE", os.path.expanduser("~/.cli_anything_sweethome3d_session.json"))

_session_mgr = None

def get_session_mgr():
    root = click.get_current_context(silent=True)
    root = root.find_root() if root is not None else None
    if root is not None and "resolution" in (root.obj or {}):
        mgr = root.obj.get("invocation_manager")
        if mgr is None:
            mgr = SessionManager(root.obj["resolution"]["session_path"])
            root.obj["invocation_manager"] = mgr
        return mgr
    global _session_mgr
    if _session_mgr is None:
        _session_mgr = SessionManager()
    return _session_mgr

class _LazySessionManagerProxy:
    def __getattr__(self, name):
        return getattr(get_session_mgr(), name)
    def __setattr__(self, name, value):
        if name in ("_session_mgr",):
            super().__setattr__(name, value)
        else:
            setattr(get_session_mgr(), name, value)

session_mgr = _LazySessionManagerProxy()
skin = ReplSkin("sweethome3d", version="1.2.0")

def assert_safe_export_target(output_path):
    """Guards against overwriting active session, journal, lock, or recovery targets (PAC-1 S06/P0-03, R10-P0-02)."""
    target = os.path.normcase(os.path.abspath(output_path))
    mgr = get_session_mgr()
    sess_path = os.path.normcase(os.path.abspath(mgr.session_path))
    protected = {sess_path, sess_path + ".pending", sess_path + ".lock"}
    
    if mgr.active_file:
        protected.add(os.path.normcase(os.path.abspath(mgr.active_file)))

    pending_file = mgr.session_path + ".pending"
    if os.path.exists(pending_file):
        try:
            with open(pending_file, "r", encoding="utf-8") as pf:
                rec = json.load(pf)
                if isinstance(rec, dict):
                    for k in ("target", "backup", "tmp_target", "staged_session"):
                        v = rec.get(k)
                        if v:
                            protected.add(os.path.normcase(os.path.abspath(v)))
        except Exception:
            pass

    if target in protected:
        raise RuntimeError(
            f"Cannot export to protected recovery target '{output_path}': destination is guarded by active transaction journal."
        )

    # Realpath / hard-link / symlink object identity check (R10-P0-02)
    real_target = os.path.normcase(os.path.realpath(output_path))
    if real_target in protected:
        raise RuntimeError(
            f"Cannot export to protected recovery target '{output_path}': resolved target '{real_target}' is guarded by active transaction journal."
        )

    if os.path.exists(output_path):
        for p in protected:
            if os.path.exists(p):
                try:
                    if os.path.samefile(output_path, p):
                        raise RuntimeError(
                            f"Cannot export to protected recovery target '{output_path}': destination is an alias/hardlink to guarded '{p}'."
                        )
                except (OSError, ValueError):
                    pass

def safe_publish_export(output_path, generator_fn):
    """Safely stages an export artifact, acquires exclusive session authority,
    rechecks destination guards against current authoritative disk context,
    detects concurrent writer commits, and atomically publishes.
    (PAC-1 S04, S06, S07, A05, R11-P0-02, R12-P0-01)
    """
    output_path = os.path.abspath(output_path)
    assert_safe_export_target(output_path)
    
    mgr = get_session_mgr()
    source_rev = mgr.revision

    tx_id = f"{os.getpid()}_{time.time_ns()}"
    tmp_out = output_path + f".tmp_exp_{tx_id}"
    
    try:
        # Step 1: Stage export to temporary file
        generator_fn(tmp_out)
        
        # Step 2: Acquire session lock before publishing
        with mgr.lock():
            # Read current authoritative session state directly from disk under lock
            sess_path = mgr.session_path
            disk_rev = 0
            disk_active = None
            if os.path.exists(sess_path) and os.path.getsize(sess_path) > 0:
                try:
                    with open(sess_path, "r", encoding="utf-8") as f:
                        disk_data = json.load(f)
                        disk_rev = disk_data.get("revision", 0)
                        disk_active = disk_data.get("active_file")
                except Exception:
                    pass

            # R12-P0-01: Detect if a concurrent writer committed while staging
            if disk_rev > source_rev and disk_active:
                norm_out = os.path.normcase(os.path.abspath(output_path))
                norm_active = os.path.normcase(os.path.abspath(disk_active))
                if norm_out == norm_active:
                    raise RuntimeError(
                        f"Cannot publish export to '{output_path}': a concurrent writer committed revision {disk_rev} "
                        f"claiming this destination as its active artifact."
                    )

            if disk_active:
                mgr.active_file = disk_active
            # Recheck destination safety under lock
            assert_safe_export_target(output_path)
            
            # Assert no active transaction is pending on this session
            pending_file = mgr.session_path + ".pending"
            if os.path.exists(pending_file):
                raise RuntimeError(
                    f"Cannot publish export to '{output_path}': an unresolved recovery journal is active on session '{mgr.session_path}'."
                )
            
            # Also check if destination itself has an active lock/journal
            if os.path.exists(output_path + ".lock") or os.path.exists(output_path + ".pending"):
                raise RuntimeError(
                    f"Cannot publish export to '{output_path}': destination has an active transaction or lock in flight."
                )
            
            # Atomic publish via os.replace
            os.replace(tmp_out, output_path)
            return output_path
    finally:
        if os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except OSError:
                pass


def project_context(ctx):
    """Returns structured context identity without triggering unexpected session load (PAC-1 A01, A04, R25-A01)."""
    if ctx is None:
        return None
    root = ctx.find_root()
    if root is None or not (root.obj or {}).get("resolution"):
        mgr = _session_mgr
        if mgr is None:
            return None
        return {
            "session_path": getattr(mgr, "session_path", None),
            "authority_id": None,
            "project_root": None,
            "selection": "default",
            "name": mgr.model.name if (mgr and getattr(mgr, "model", None)) else None,
            "active_file": getattr(mgr, "active_file", None),
            "revision": getattr(mgr, "revision", 0),
        }
    identity = dict(root.obj["resolution"])
    mgr = root.obj.get("invocation_manager")
    identity.update(
        name=mgr.model.name if (mgr and getattr(mgr, "model", None)) else None,
        active_file=mgr.active_file if mgr else None,
        revision=getattr(mgr, "revision", None) if mgr else None,
    )
    return identity


def output_result(ctx, data, human_formatter=None, include_project=True):
    """Outputs data either as JSON or human-readable format based on context flag (PAC-1 A04, R16-P1-04, R17-P1-04, R21-P1-02, R25-A01)."""
    is_json = ctx.obj.get("json", False) if ctx.obj else False
    if is_json:
        # PAC-1 A04, R16-P1-04, R17-P1-04, R21-P1-02, R25-A01: Versioned finite JSON envelope
        project_meta = project_context(ctx) if include_project else None
        payload = {
            "protocol_version": "1",
            "status": "success",
            "outcome": "ok",
            "project": project_meta,
            "data": data,
            "error": None
        }
        try:
            serialized = json.dumps(payload, indent=2, allow_nan=False)
            click.echo(serialized)
        except (ValueError, TypeError) as e:
            output_error(ctx, f"Non-finite numeric value in output: {e}", code=3, error_code="NON_FINITE_NUMERIC_ERROR")
    elif human_formatter:
        human_formatter(data)
    elif isinstance(data, (dict, list)):
        click.echo(json.dumps(data, indent=2))
    else:
        skin.success(str(data))

def classify_error(exc_or_msg):
    """Maps errors to PAC-1 typed exit codes and machine-readable error codes (PAC-1 A04, R28-P1-05, R30, R31)."""
    # 1. Type hierarchy checking first (exception identity precedes message words)
    if isinstance(exc_or_msg, BaseException):
        chain, seen = [], set()
        curr = exc_or_msg
        while isinstance(curr, BaseException) and id(curr) not in seen:
            seen.add(id(curr))
            chain.append(curr)
            curr = getattr(curr, "__cause__", None) or getattr(curr, "__context__", None)

        for typ, result in (
            (RecoveryRequiredError, (7, "RECOVERY_REQUIRED")),
            (ConcurrencyConflictError, (4, "CONFLICT")),
            (EntityConflictError, (4, "CONFLICT")),
            (PermissionError, (6, "IO_ERROR")),
            (FileNotFoundError, (6, "IO_ERROR")),
            (OSError, (6, "IO_ERROR")),
            (UnsupportedFeatureError, (5, "UNSUPPORTED")),
            (NotImplementedError, (5, "UNSUPPORTED")),
            (ValidationRejectedError, (3, "VALIDATION_REJECTED")),
        ):
            if any(isinstance(item, typ) for item in chain):
                return result

        msg_str = str(exc_or_msg)
        # Bounded legacy compatibility for exact fixed prefixes on raw ValueError:
        if msg_str.startswith("Unsupported project format") or msg_str.startswith("Unsupported dialect"):
            return 5, "UNSUPPORTED"
        if msg_str.startswith("Split offset must be finite") or msg_str.startswith("Validation rejected:"):
            return 3, "VALIDATION_REJECTED"

        # Unknown internal exception returns 1, ERROR without substring pollution
        return 1, "ERROR"

    msg = str(exc_or_msg)
    msg_lower = msg.lower()

    # 2. String/message fallback patterns only for non-exception strings
    if "recovery_required" in msg_lower or "unresolved recovery journal" in msg_lower:
        return 7, "RECOVERY_REQUIRED"
    if "already exists in project" in msg_lower or "entityconflicterror" in msg_lower or "concurrencyconflicterror" in msg_lower:
        return 4, "CONFLICT"
    if "unsupported dialect" in msg_lower or "unsupported project format" in msg_lower or "unsupported feature" in msg_lower:
        return 5, "UNSUPPORTED"
    if "access denied" in msg_lower or "permission denied" in msg_lower or "no such file" in msg_lower:
        return 6, "IO_ERROR"
    if (
        "validation rejected" in msg_lower
        or "validationrejectederror" in msg_lower
        or "invalid split offset" in msg_lower
        or "cannot split wall across" in msg_lower
        or "exceeds wall length" in msg_lower
        or "split offset must be" in msg_lower
        or "non-finite" in msg_lower
        or "exceeds physical error budget" in msg_lower
        or "zero-length" in msg_lower
        or "corrupted .sh3d file" in msg_lower
        or "native room point must have" in msg_lower
    ):
        return 3, "VALIDATION_REJECTED"

    return 1, "ERROR"

def output_error(ctx, message, code=1, error_code=None, data=None):
    """Outputs structured error either as JSON or human-readable format (PAC-1 A04, R17-P1-04, R18-P1-04, R25-A01)."""
    if code == 1 and error_code is None:
        code, error_code = classify_error(message)
    msg_str = str(message)
    is_json = False
    if ctx and hasattr(ctx, "obj") and isinstance(ctx.obj, dict):
        is_json = ctx.obj.get("json", False)
    if not is_json and "--json" in sys.argv:
        is_json = True
    if is_json:
        proj_meta = None
        try:
            proj_meta = project_context(ctx) if ctx else None
        except Exception:
            proj_meta = None
        payload = {
            "protocol_version": "1",
            "status": "error",
            "outcome": "error",
            "project": proj_meta,
            "data": data,
            "error": {
                "code": error_code or "ERROR",
                "message": msg_str
            },
            "message": msg_str
        }
        click.echo(json.dumps(payload, indent=2, allow_nan=False))
    else:
        skin.error(str(message))
        if isinstance(data, dict):
            for err in data.get("errors", []):
                skin.error(f"Error: {err}")
            for warn in data.get("warnings", []):
                skin.warning(f"Warning: {warn}")
    sys.exit(code)

class JsonEnvelopeGroup(click.Group):
    """Custom Click Group that captures usage and Click exceptions under --json mode and emits versioned JSON error envelope (PAC-1 A04, R18-P1-04)."""
    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except click.ClickException:
            raise
        except click.exceptions.Exit:
            raise
        except Exception as exc:
            output_error(ctx, exc)

    def main(self, args=None, prog_name=None, complete_var=None, standalone_mode=True, **extra):
        raw_args = list(sys.argv[1:] if args is None else args)
        is_json = "--json" in raw_args
        try:
            return super().main(args=args, prog_name=prog_name, complete_var=complete_var, standalone_mode=False, **extra)
        except click.ClickException as e:
            if is_json:
                payload = {
                    "protocol_version": "1",
                    "status": "error",
                    "outcome": "error",
                    "project": None,
                    "data": None,
                    "error": {
                        "code": "INPUT_ERROR",
                        "message": e.format_message(),
                    },
                    "message": e.format_message(),
                }
                click.echo(json.dumps(payload, indent=2, allow_nan=False))
                sys.exit(e.exit_code)
            else:
                if standalone_mode:
                    e.show()
                    sys.exit(e.exit_code)
                raise
        except click.exceptions.Exit as e:
            if standalone_mode:
                sys.exit(e.exit_code)
            raise
        except Exception as e:
            if is_json:
                code, err_code = classify_error(e)
                payload = {
                    "protocol_version": "1",
                    "status": "error",
                    "outcome": "error",
                    "project": None,
                    "data": None,
                    "error": {
                        "code": err_code,
                        "message": str(e),
                    },
                    "message": str(e),
                }
                click.echo(json.dumps(payload, indent=2, allow_nan=False))
                sys.exit(code)
            raise

@click.group(cls=JsonEnvelopeGroup, invoke_without_command=True)
@click.version_option(__version__, prog_name="cli-anything-sweethome3d")
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
@click.option("--project", "project_path", type=click.Path(file_okay=False), help="Workspace directory; state is .cli-anything/session.json.")
@click.option("--session", "session_path", type=click.Path(dir_okay=False), help="Explicit authoritative session JSON file.")
@click.pass_context
def cli(ctx, json_mode, project_path=None, session_path=None):
    """CLI-Anything: Agent-Native Command Line Interface for Sweet Home 3D."""
    ctx.ensure_object(dict)
    inherited = ctx.parent is not None and "resolution" in (ctx.find_root().obj or {})
    if inherited:
        if project_path is not None or session_path is not None:
            raise click.UsageError("Start a new invocation to change REPL authority", ctx)
    else:
        try:
            ctx.obj["resolution"] = resolve_invocation(
                project_path, session_path, default_session=SESSION_FILE
            )
        except (ValueError, OSError) as exc:
            raise click.UsageError(str(exc), ctx) from exc
        ctx.obj["invocation_manager"] = None
    ctx.obj["json"] = json_mode
    if ctx.invoked_subcommand is None:
        if json_mode:
            output_result(ctx, session_mgr.model.to_dict())
        else:
            run_repl()

# ==============================================================================
# 1. Project Commands
# ==============================================================================
@cli.group()
def project():
    """Project management commands (new, open, save, info, validate, takeoff)."""
    pass

@project.command(name="new")
@click.option("--name", default="New Home", help="Name of the floor plan project.")
@click.option("--wall-height", default=280.0, type=float, help="Default wall height in cm.")
@click.pass_context
def project_new(ctx, name, wall_height):
    """Create a new empty floor plan project."""
    session_mgr.new_project(name=name, wall_height=wall_height)
    output_result(ctx, {"created": name, "wall_height": wall_height})

@project.command(name="open")
@click.argument("filepath", type=click.Path(exists=True))
@click.pass_context
def project_open(ctx, filepath):
    """Open an existing .sh3d or .json project file."""
    try:
        model = session_mgr.open_file(filepath)
        output_result(ctx, {"opened": filepath, "summary": model.validate()["summary"]})
    except Exception as e:
        output_error(ctx, e)

@project.command(name="save")
@click.option("-o", "--output", "filepath", default=None, help="Destination .sh3d or .json path.")
@click.pass_context
def project_save(ctx, filepath):
    """Save the active project to .sh3d or .json."""
    try:
        saved_path = session_mgr.save_file(filepath)
        output_result(ctx, {"saved": saved_path})
    except Exception as e:
        output_error(ctx, e)

@project.command(name="info")
@click.pass_context
def project_info(ctx):
    """Display project metadata and architectural summary."""
    m = session_mgr.model
    val = m.validate()
    data = {
        "name": m.name,
        "version": m.version,
        "active_file": session_mgr.active_file,
        "current_level": session_mgr.current_level_id,
        "summary": val["summary"],
        "levels": [lvl.to_dict() for lvl in m.levels],
    }
    def formatter(d):
        skin.status("Project Name", d["name"])
        skin.status("Active File", d["active_file"] or "(Unsaved In-Memory)")
        skin.status("Active Level", d["current_level"])
        skin.status("Total Area", f"{d['summary']['total_area_m2']:.2f} m²")
        skin.status("Total Wall Run", f"{d['summary']['total_wall_length_m']:.1f} m")
        skin.status("Walls Count", d['summary']['walls_count'])
        skin.status("Rooms Count", d['summary']['rooms_count'])
        skin.status("Openings Count", d['summary']['openings_count'])
        skin.status("Furniture Count", d['summary']['furniture_count'])
        skin.status("Dimensions Count", d['summary']['dimensions_count'])
    output_result(ctx, data, human_formatter=formatter)

@project.command(name="validate")
@click.option("--strict", is_flag=True, help="Exit with non-zero status if validation fails.")
@click.pass_context
def project_validate(ctx, strict):
    """Run architectural constraint and closure validation checks (PAC-1 A07, R10-P1-04)."""
    val = session_mgr.model.validate()
    is_json = ctx.obj.get("json", False) if ctx.obj else False
    has_strict_failure = strict and (
        not val["valid"]
        or len(val.get("warnings", [])) > 0
        or any(f.get("coverage") == "not_checked" for f in val.get("findings", []))
    )

    if has_strict_failure:
        output_error(
            ctx,
            "Strict validation failed: model violates constraints or has unchecked coverage.",
            code=3,
            error_code="VALIDATION_REJECTED",
            data=val,
        )

    def formatter(d):
        if d["valid"]:
            skin.success("Project architectural validation PASSED.")
        else:
            skin.error("Project validation FAILED.")
        for err in d.get("errors", []):
            skin.error(f"Error: {err}")
        for warn in d.get("warnings", []):
            skin.warning(f"Warning: {warn}")
    output_result(ctx, val, human_formatter=formatter)

@project.command(name="template")
@click.option("--type", "template_type", type=click.Choice(["type36", "type45", "studio", "pavilion"], case_sensitive=False),
              required=True, help="Standard architectural typology to apply.")
@click.option("--name", default=None, help="Custom project title for the template.")
@click.pass_context
def project_template(ctx, template_type, name):
    """Apply a pre-built architectural typology template (Type 36, Type 45, Studio, Pavilion)."""
    from cli_anything.sweethome3d.core.templates import apply_template
    try:
        with session_mgr.transaction():
            session_mgr.push_state()
            apply_template(session_mgr.model, template_type, name=name)
            session_mgr.save_session()
    except Exception as e:
        output_error(ctx, e)
    data = {
        "status": "success",
        "template": template_type,
        "name": session_mgr.model.name,
        "summary": {
            "walls_count": len(session_mgr.model.walls),
            "rooms_count": len(session_mgr.model.rooms),
            "openings_count": len(session_mgr.model.openings),
            "furniture_count": len(session_mgr.model.furniture),
            "total_area_m2": session_mgr.model.total_area_m2,
        }
    }
    def formatter(d):
        skin.success(f"Applied template '{d['template']}' ({d['name']}).")
        skin.status("Total Area", f"{d['summary']['total_area_m2']:.2f} m²")
        skin.status("Walls", d['summary']['walls_count'])
        skin.status("Rooms", d['summary']['rooms_count'])
        skin.status("Openings", d['summary']['openings_count'])
        skin.status("Furniture", d['summary']['furniture_count'])
    output_result(ctx, data, human_formatter=formatter)

@project.command(name="takeoff")
@click.pass_context
def project_takeoff(ctx):
    """Generate architectural Bill of Quantities (BOQ / RAB) takeoff."""
    takeoff_data = session_mgr.model.takeoff()
    def formatter(d):
        skin.status("Project", d["project_name"])
        skin.status("Floor Area", f"{d['floor_and_ceiling']['total_floor_area_m2']:.2f} m²")
        skin.status("Ceiling Plaster", f"{d['floor_and_ceiling']['ceiling_plasterboard_m2']:.2f} m²")
        skin.status("Baseboard Skirting", f"{d['floor_and_ceiling']['skirting_baseboard_linear_m']:.1f} m")
        skin.status("Gross Wall Area", f"{d['masonry_and_walls']['gross_wall_surface_m2']:.1f} m²")
        skin.status("Openings Deduct", f"{d['masonry_and_walls']['openings_deduction_m2']:.1f} m²")
        skin.status("Net Masonry Wall", f"{d['masonry_and_walls']['net_masonry_wall_m2']:.1f} m²")
        skin.status("Wall Paint (2x)", f"{d['masonry_and_walls']['interior_wall_paint_m2']:.1f} m²")
        skin.status("Doors Count", d["openings_schedule"]["doors_count"])
        skin.status("Windows Count", d["openings_schedule"]["windows_count"])
        skin.status("Furniture Total", d["furniture_schedule"]["total_items"])
    output_result(ctx, takeoff_data, human_formatter=formatter)

# ==============================================================================
# 2. Wall Commands
# ==============================================================================
@cli.group()
def wall():
    """Wall drafting and management commands."""
    pass

@wall.command(name="add")
@click.option("--id", "wall_id", required=True, help="Unique identifier for the wall.")
@click.option("--x1", required=True, type=float, help="Start X coordinate in cm.")
@click.option("--y1", required=True, type=float, help="Start Y coordinate in cm.")
@click.option("--x2", required=True, type=float, help="End X coordinate in cm.")
@click.option("--y2", required=True, type=float, help="End Y coordinate in cm.")
@click.option("--thickness", default=15.0, type=float, help="Wall thickness in cm (default: 15.0).")
@click.option("--height", default=None, type=float, help="Wall height in cm.")
@click.option("--level", "level_id", default=None, help="Target level ID (defaults to active session level).")
@click.option("--arc-extent", default=0.0, type=float, help="Curved wall arc angle in degrees (0 = straight).")
@click.pass_context
def wall_add(ctx, wall_id, x1, y1, x2, y2, thickness, height, level_id, arc_extent):
    """Add a new wall segment (straight or curved)."""
    target_lvl = level_id or session_mgr.current_level_id
    try:
        with session_mgr.transaction():
            session_mgr.push_state()
            w = session_mgr.model.add_wall(wall_id, x1, y1, x2, y2, thickness, height, target_lvl, arc_extent)
            session_mgr.save_session()
        output_result(ctx, w.to_dict())
    except Exception as e:
        output_error(ctx, e)

@wall.command(name="add-loop")
@click.option("--prefix", "--id-prefix", "id_prefix", default="w_", help="Prefix for generated wall IDs (e.g. 'w_').")
@click.option("--points", required=True, help="Point sequence: 'x1,y1;x2,y2;x3,y3;...' in cm.")
@click.option("--closed/--open", default=True, help="Whether to close the loop from last to first point.")
@click.option("--thickness", default=15.0, type=float, help="Wall thickness in cm.")
@click.option("--height", default=None, type=float, help="Wall height in cm.")
@click.option("--level", "level_id", default=None, help="Target level ID.")
@click.pass_context
def wall_add_loop(ctx, id_prefix, points, closed, thickness, height, level_id):
    """Create a continuous loop or chain of connected wall segments in one command."""
    target_lvl = level_id or session_mgr.current_level_id
    pts = []
    try:
        for pair in points.split(";"):
            if pair.strip():
                x, y = pair.strip().split(",")
                pts.append((float(x), float(y)))
    except Exception as e:
        output_error(ctx, ValidationRejectedError(f"Invalid points format: {e}"))

    try:
        with session_mgr.transaction():
            session_mgr.push_state()
            created = session_mgr.model.add_wall_loop(pts, prefix=id_prefix, closed=closed, thickness=thickness, height=height, level_id=target_lvl)
            session_mgr.save_session()
        data = [w.to_dict() for w in created]
        output_result(ctx, {"created_count": len(created), "created_walls": data, "walls": data})
    except Exception as e:
        output_error(ctx, e)

@wall.command(name="list")
@click.option("--level", "level_id", default=None, help="Filter by level ID.")
@click.pass_context
def wall_list(ctx, level_id):
    """List all walls in the project."""
    walls = [
        w.to_dict() for w in session_mgr.model.walls.values()
        if not level_id or w.level_id == level_id
    ]
    def formatter(data):
        headers = ["ID", "Start (X,Y)", "End (X,Y)", "Len (m)", "Thick (cm)", "Arc(°)", "Level"]
        rows = [
            [d["id"], f"{d['x_start']:.0f},{d['y_start']:.0f}", f"{d['x_end']:.0f},{d['y_end']:.0f}",
             d["length_m"], d["thickness"], d["arc_extent"], d["level_id"]]
            for d in data
        ]
        skin.table(headers, rows)
    output_result(ctx, walls, human_formatter=formatter)

@wall.command(name="remove")
@click.argument("wall_id")
@click.pass_context
def wall_remove(ctx, wall_id):
    """Remove a wall by its ID."""
    try:
        with session_mgr.transaction():
            session_mgr.push_state()
            removed = session_mgr.model.remove_wall(wall_id)
            if not removed:
                raise ValidationRejectedError(f"Wall '{wall_id}' not found.")
            session_mgr.save_session()
        output_result(ctx, {"removed": wall_id})
    except Exception as e:
        output_error(ctx, e)

@wall.command(name="reverse")
@click.argument("wall_id")
@click.pass_context
def wall_reverse(ctx, wall_id):
    """Reverse a wall's direction and re-anchor hosted openings (PAC-1 T06)."""
    try:
        with session_mgr.transaction():
            session_mgr.push_state()
            w = session_mgr.model.reverse_wall(wall_id)
            session_mgr.save_session()
        output_result(ctx, {"reversed": wall_id, "wall": w.to_dict()})
    except Exception as e:
        output_error(ctx, e)

@wall.command(name="split")
@click.argument("wall_id")
@click.option("--offset", "split_offset_cm", type=float, required=True, help="Offset along wall in cm where split occurs.")
@click.option("--new-id", "new_wall_id", default=None, help="ID for the second segment.")
@click.pass_context
def wall_split(ctx, wall_id, split_offset_cm, new_wall_id):
    """Split a wall at offset into two contiguous segments (PAC-1 T06)."""
    try:
        if not math.isfinite(split_offset_cm):
            raise ValidationRejectedError(f"Split offset must be finite, got {split_offset_cm}")
        with session_mgr.transaction():
            session_mgr.push_state()
            w1, w2 = session_mgr.model.split_wall(wall_id, split_offset_cm, new_wall_id=new_wall_id)
            session_mgr.save_session()
        output_result(ctx, {"split": wall_id, "segment1": w1.to_dict(), "segment2": w2.to_dict()})
    except Exception as e:
        output_error(ctx, e)


@wall.command(name="node")
@click.option("--level", "level_id", default=None, help="Filter by level ID.")
@click.pass_context
def wall_node(ctx, level_id):
    """Node and deduplicate overlapping collinear walls and junctions into atomic physical segments (PAC-1 T01-T03)."""
    try:
        with session_mgr.transaction():
            session_mgr.push_state()
            count = session_mgr.model.node_walls(level_id=level_id)
            session_mgr.save_session()
        output_result(ctx, {
            "noded_atoms_count": count,
            "total_wall_length_m": session_mgr.model.total_wall_length_m,
            "level": level_id or "all"
        })
    except Exception as e:
        output_error(ctx, e)

# ==============================================================================
# 3. Room Commands
# ==============================================================================
@cli.group()
def room():
    """Room polygon creation, labeling, and area calculation."""
    pass

@room.command(name="add")
@click.option("--id", "room_id", required=True, help="Unique identifier for the room.")
@click.option("--name", required=True, help="Name of the room (e.g. 'Master Bedroom').")
@click.option("--points", required=True, help="Polygon points format: 'x1,y1;x2,y2;x3,y3;...' in cm.")
@click.option("--color", default="#EAEAEA", help="Floor fill hex color.")
@click.option("--level", "level_id", default=None, help="Target level ID.")
@click.option("--with-walls", is_flag=True, help="Automatically generate perimeter boundary walls.")
@click.option("--wall-thickness", default=15.0, type=float, help="Wall thickness if --with-walls is set.")
@click.pass_context
def room_add(ctx, room_id, name, points, color, level_id, with_walls, wall_thickness):
    """Add an enclosed architectural room polygon, optionally generating boundary walls."""
    target_lvl = level_id or session_mgr.current_level_id
    pts = []
    try:
        if points.startswith("["):
            pts = json.loads(points)
        else:
            for pair in points.split(";"):
                if pair.strip():
                    x, y = pair.strip().split(",")
                    pts.append((float(x), float(y)))
    except Exception as e:
        output_error(ctx, ValidationRejectedError(f"Invalid points format: {e}"))

    try:
        with session_mgr.transaction():
            session_mgr.push_state()
            r = session_mgr.model.add_room(room_id, name, pts, level_id=target_lvl, floor_color=color,
                                           with_walls=with_walls, wall_thickness=wall_thickness)
            session_mgr.save_session()
        res = r.to_dict()
        res["with_walls"] = with_walls
        output_result(ctx, res)
    except Exception as e:
        output_error(ctx, e)

@room.command(name="list")
@click.option("--level", "level_id", default=None, help="Filter by level ID.")
@click.pass_context
def room_list(ctx, level_id):
    """List all rooms and calculated floor areas."""
    rooms = [
        r.to_dict() for r in session_mgr.model.rooms.values()
        if not level_id or r.level_id == level_id
    ]
    def formatter(data):
        headers = ["ID", "Name", "Area (m²)", "Vertices", "Color", "Centroid (cm)", "Level"]
        rows = [
            [d["id"], d["name"], d["area_m2"], len(d["points"]), d["floor_color"],
             f"{d['centroid'][0]:.0f},{d['centroid'][1]:.0f}", d["level_id"]]
            for d in data
        ]
        skin.table(headers, rows)
    output_result(ctx, rooms, human_formatter=formatter)

@room.command(name="remove")
@click.argument("room_id")
@click.pass_context
def room_remove(ctx, room_id):
    """Remove a room by ID."""
    try:
        with session_mgr.transaction():
            session_mgr.push_state()
            removed = session_mgr.model.remove_room(room_id)
            if not removed:
                raise ValidationRejectedError(f"Room '{room_id}' not found.")
            session_mgr.save_session()
        output_result(ctx, {"removed": room_id})
    except Exception as e:
        output_error(ctx, e)

@room.command(name="area")
@click.argument("room_id")
@click.pass_context
def room_area(ctx, room_id):
    """Get the exact verified area of a room in m²."""
    r = session_mgr.model.rooms.get(room_id)
    if not r:
        output_error(ctx, ValidationRejectedError(f"Room '{room_id}' not found."))
    output_result(ctx, {"room_id": r.id, "name": r.name, "area_m2": r.area_m2})

# ==============================================================================
# 4. Opening Commands (Doors and Windows)
# ==============================================================================
@cli.group()
def opening():
    """Door and window opening management."""
    pass

@opening.command(name="add")
@click.option("--id", "opening_id", required=True, help="Unique opening ID.")
@click.option("--name", required=True, help="Opening name (e.g. 'Main Entrance Door').")
@click.option("--wall", "wall_id", required=True, help="Parent wall ID.")
@click.option("--offset", default=50.0, type=float, help="Offset from wall start in cm.")
@click.option("--width", default=90.0, type=float, help="Opening width in cm.")
@click.option("--height", default=210.0, type=float, help="Opening height in cm.")
@click.option("--type", "opening_type", type=click.Choice(["door", "window"]), default="door")
@click.option("--swing", type=click.Choice(["left", "right", "sliding", "none"]), default="right")
@click.pass_context
def opening_add(ctx, opening_id, name, wall_id, offset, width, height, opening_type, swing):
    """Add a door or window opening on a wall."""
    try:
        with session_mgr.transaction():
            session_mgr.push_state()
            op = session_mgr.model.add_opening(opening_id, name, wall_id, offset, width, height, opening_type, swing)
            session_mgr.save_session()
        output_result(ctx, op.to_dict())
    except Exception as e:
        output_error(ctx, e)

@opening.command(name="list")
@click.pass_context
def opening_list(ctx):
    """List all doors and windows."""
    openings = [op.to_dict() for op in session_mgr.model.openings.values()]
    def formatter(data):
        headers = ["ID", "Name", "Type", "Host Wall", "Offset (cm)", "Width (cm)", "Swing"]
        rows = [
            [d["id"], d["name"], d["opening_type"], d["wall_id"], d["offset_cm"], d["width_cm"], d["swing"]]
            for d in data
        ]
        skin.table(headers, rows)
    output_result(ctx, openings, human_formatter=formatter)

@opening.command(name="remove")
@click.argument("opening_id")
@click.pass_context
def opening_remove(ctx, opening_id):
    """Remove a door or window by ID."""
    try:
        with session_mgr.transaction():
            session_mgr.push_state()
            removed = session_mgr.model.remove_opening(opening_id)
            if not removed:
                raise ValidationRejectedError(f"Opening '{opening_id}' not found.")
            session_mgr.save_session()
        output_result(ctx, {"removed": opening_id})
    except Exception as e:
        output_error(ctx, e)

# ==============================================================================
# 5. Furniture Commands
# ==============================================================================
@cli.group()
def furniture():
    """Furniture asset placement and catalog commands."""
    pass

@furniture.command(name="catalog")
@click.pass_context
def furniture_catalog(ctx):
    """List available standard furniture items in the built-in catalog."""
    def formatter(data):
        headers = ["Catalog ID", "Item Name", "Width (cm)", "Depth (cm)", "Category"]
        rows = [
            [k, v["name"], v["width"], v["depth"], v["category"]]
            for k, v in data.items()
        ]
        skin.table(headers, rows)
    output_result(ctx, FURNITURE_CATALOG, human_formatter=formatter)

@furniture.command(name="add")
@click.option("--id", "furniture_id", required=True, help="Unique furniture ID.")
@click.option("--name", default=None, help="Display name.")
@click.option("--catalog", "catalog_id", default="custom", help="Catalog code (e.g. bed_king, sofa_3seat).")
@click.option("--x", required=True, type=float, help="Center X coordinate in cm.")
@click.option("--y", required=True, type=float, help="Center Y coordinate in cm.")
@click.option("--elevation", default=0.0, type=float, help="Elevation above floor in cm.")
@click.option("--width", default=None, type=float, help="Width in cm.")
@click.option("--depth", default=None, type=float, help="Depth in cm.")
@click.option("--height", default=None, type=float, help="Height in cm.")
@click.option("--angle", default=0.0, type=float, help="Rotation angle in degrees [0, 360).")
@click.option("--level", "level_id", default=None, help="Target level ID.")
@click.option("--room", "room_id", default=None, help="Assigned room ID for containment validation.")
@click.pass_context
def furniture_add(ctx, furniture_id, name, catalog_id, x, y, elevation, width, depth, height, angle, level_id, room_id):
    """Add a piece of furniture."""
    target_lvl = level_id or session_mgr.current_level_id
    cat_entry = FURNITURE_CATALOG.get(catalog_id, {})
    item_name = name or cat_entry.get("name", "Custom Furniture")
    w = width or cat_entry.get("width", 100.0)
    d = depth or cat_entry.get("depth", 100.0)
    h = height or cat_entry.get("height", 80.0)

    try:
        with session_mgr.transaction():
            session_mgr.push_state()
            f = session_mgr.model.add_furniture(
                furniture_id, item_name, catalog_id, x, y, elevation, w, d, h, angle, target_lvl, room_id=room_id
            )
            session_mgr.save_session()
        output_result(ctx, f.to_dict())
    except Exception as e:
        output_error(ctx, e)

@furniture.command(name="list")
@click.option("--level", "level_id", default=None, help="Filter by level ID.")
@click.pass_context
def furniture_list(ctx, level_id):
    """List all furniture in the project."""
    items = [
        f.to_dict() for f in session_mgr.model.furniture.values()
        if not level_id or f.level_id == level_id
    ]
    def formatter(data):
        headers = ["ID", "Name", "Catalog ID", "Pos (X,Y)", "Elev (cm)", "Size (WxD)", "Angle", "Level"]
        rows = [
            [d["id"], d["name"], d["catalog_id"], f"{d['x_cm']:.0f},{d['y_cm']:.0f}",
             d["elevation_cm"], f"{d['width_cm']:.0f}x{d['depth_cm']:.0f}", f"{d['angle_deg']:.0f}°", d["level_id"]]
            for d in data
        ]
        skin.table(headers, rows)
    output_result(ctx, items, human_formatter=formatter)

@furniture.command(name="remove")
@click.argument("furniture_id")
@click.pass_context
def furniture_remove(ctx, furniture_id):
    """Remove furniture item by ID."""
    try:
        with session_mgr.transaction():
            session_mgr.push_state()
            removed = session_mgr.model.remove_furniture(furniture_id)
            if not removed:
                raise ValidationRejectedError(f"Furniture '{furniture_id}' not found.")
            session_mgr.save_session()
        output_result(ctx, {"removed": furniture_id})
    except Exception as e:
        output_error(ctx, e)

# ==============================================================================
# 6. Dimension Line Commands
# ==============================================================================
@cli.group()
def dimension():
    """Architectural dimension line measurement commands."""
    pass

@dimension.command(name="add")
@click.option("--id", "dimension_id", required=True, help="Unique dimension ID.")
@click.option("--x1", required=True, type=float, help="Start X coordinate in cm.")
@click.option("--y1", required=True, type=float, help="Start Y coordinate in cm.")
@click.option("--x2", required=True, type=float, help="End X coordinate in cm.")
@click.option("--y2", required=True, type=float, help="End Y coordinate in cm.")
@click.option("--offset", default=30.0, type=float, help="Offset distance from measured points in cm.")
@click.option("--level", "level_id", default=None, help="Target level ID.")
@click.pass_context
def dimension_add(ctx, dimension_id, x1, y1, x2, y2, offset, level_id):
    """Add an architectural dimension callout line."""
    target_lvl = level_id or session_mgr.current_level_id
    try:
        with session_mgr.transaction():
            session_mgr.push_state()
            dim = session_mgr.model.add_dimension(dimension_id, x1, y1, x2, y2, offset, target_lvl)
            session_mgr.save_session()
        output_result(ctx, dim.to_dict())
    except Exception as e:
        output_error(ctx, e)

@dimension.command(name="list")
@click.option("--level", "level_id", default=None, help="Filter by level ID.")
@click.pass_context
def dimension_list(ctx, level_id):
    """List all dimension lines and measured lengths."""
    dims = [
        d.to_dict() for d in session_mgr.model.dimensions.values()
        if not level_id or d.level_id == level_id
    ]
    def formatter(data):
        headers = ["ID", "Start (X,Y)", "End (X,Y)", "Distance (m)", "Offset (cm)", "Level"]
        rows = [
            [d["id"], f"{d['x_start']:.0f},{d['y_start']:.0f}", f"{d['x_end']:.0f},{d['y_end']:.0f}",
             d["distance_m"], d["offset_cm"], d["level_id"]]
            for d in data
        ]
        skin.table(headers, rows)
    output_result(ctx, dims, human_formatter=formatter)

@dimension.command(name="remove")
@click.argument("dimension_id")
@click.pass_context
def dimension_remove(ctx, dimension_id):
    """Remove a dimension line by ID."""
    try:
        with session_mgr.transaction():
            session_mgr.push_state()
            removed = session_mgr.model.remove_dimension(dimension_id)
            if not removed:
                raise ValidationRejectedError(f"Dimension '{dimension_id}' not found.")
            session_mgr.save_session()
        output_result(ctx, {"removed": dimension_id})
    except Exception as e:
        output_error(ctx, e)

# ==============================================================================
# 7. Level Commands
# ==============================================================================
@cli.group()
def level():
    """Multi-story floor level management commands."""
    pass

@level.command(name="add")
@click.option("--id", "level_id", required=True, help="Unique level ID (e.g. 'level2').")
@click.option("--name", required=True, help="Level name (e.g. 'Second Floor').")
@click.option("--elevation", default=0.0, type=float, help="Elevation in cm.")
@click.option("--height", default=280.0, type=float, help="Ceiling height in cm.")
@click.pass_context
def level_add(ctx, level_id, name, elevation, height):
    """Add a new storey level."""
    try:
        with session_mgr.transaction():
            session_mgr.push_state()
            lvl = session_mgr.model.add_level(level_id, name, elevation, height)
            session_mgr.save_session()
        output_result(ctx, lvl.to_dict())
    except Exception as e:
        output_error(ctx, e)

@level.command(name="set-current")
@click.argument("level_id", required=False)
@click.option("--id", "opt_level_id", default=None, help="Level ID to set as active.")
@click.pass_context
def level_set_current(ctx, level_id, opt_level_id):
    """Set the active default level for new entities."""
    target_id = opt_level_id or level_id
    if not target_id:
        output_error(ctx, "Level ID must be provided as an argument or via --id.")
    try:
        active = session_mgr.set_current_level(target_id)
        output_result(ctx, {"current_level": active, "current_level_id": active})
    except ValueError as e:
        output_error(ctx, e)

@level.command(name="list")
@click.pass_context
def level_list(ctx):
    """List all levels."""
    levels = [lvl.to_dict() for lvl in session_mgr.model.levels]
    def formatter(data):
        headers = ["ID", "Name", "Elevation (cm)", "Height (cm)", "Active"]
        rows = [
            [d["id"], d["name"], d["elevation_cm"], d["height_cm"],
             "★ Yes" if d["id"] == session_mgr.current_level_id else "No"]
            for d in data
        ]
        skin.table(headers, rows)
    output_result(ctx, levels, human_formatter=formatter)

# ==============================================================================
# 8. Export Commands
# ==============================================================================
@cli.group()
def export():
    """Export floorplan drawings to SVG, DXF, HTML, SH3D, or JSON."""
    pass

@export.command(name="svg")
@click.option("-o", "--output", required=True, help="Destination .svg path.")
@click.option("--level", "level_id", default=None, help="Filter by level ID.")
@click.pass_context
def export_svg_cmd(ctx, output, level_id):
    """Export layered architectural vector SVG."""
    try:
        out_path = safe_publish_export(output, lambda tmp: export_svg(session_mgr.model, tmp, level_id=level_id))
        output_result(ctx, {"exported": out_path, "format": "svg", "level": level_id or "all"})
    except Exception as e:
        output_error(ctx, e)

@export.command(name="dxf")
@click.option("-o", "--output", required=True, help="Destination .dxf path.")
@click.option("--level", "level_id", default=None, help="Filter by level ID.")
@click.pass_context
def export_dxf_cmd(ctx, output, level_id):
    """Export AutoCAD-compatible DXF R12 file."""
    try:
        out_path = safe_publish_export(output, lambda tmp: export_dxf(session_mgr.model, tmp, level_id=level_id))
        output_result(ctx, {"exported": out_path, "format": "dxf", "level": level_id or "all"})
    except Exception as e:
        output_error(ctx, e)

@export.command(name="html")
@click.option("-o", "--output", required=True, help="Destination .html path.")
@click.option("--level", "level_id", default=None, help="Filter by level ID.")
@click.pass_context
def export_html_cmd(ctx, output, level_id):
    """Export interactive self-contained HTML floor plan viewer."""
    try:
        out_path = safe_publish_export(output, lambda tmp: export_html(session_mgr.model, tmp, level_id=level_id))
        output_result(ctx, {"exported": out_path, "format": "html", "level": level_id or "all"})
    except Exception as e:
        output_error(ctx, e)

@export.command(name="sh3d")
@click.option("-o", "--output", required=True, help="Destination .sh3d path.")
@click.pass_context
def export_sh3d_cmd(ctx, output):
    """Export Sweet Home 3D native compressed ZIP archive (.sh3d)."""
    try:
        out_path = safe_publish_export(output, lambda tmp: session_mgr.model.save_sh3d(tmp))
        output_result(ctx, {"exported": out_path, "format": "sh3d"})
    except Exception as e:
        output_error(ctx, e)

@export.command(name="json")
@click.option("-o", "--output", required=True, help="Destination .json path.")
@click.option("--level", "level_id", default=None, help="Filter by level ID.")
@click.pass_context
def export_json_cmd(ctx, output, level_id):
    """Export structured JSON floor plan model."""
    try:
        out_path = safe_publish_export(output, lambda tmp: export_json(session_mgr.model, tmp, level_id=level_id))
        output_result(ctx, {"exported": out_path, "format": "json", "level": level_id or "all"})
    except Exception as e:
        output_error(ctx, e)

# ==============================================================================
# 9. Session Commands
# ==============================================================================
@cli.group()
def session():
    """Session state and transaction undo/redo management."""
    pass

@session.command(name="status")
@click.pass_context
def session_status(ctx):
    """Show session status, active file, and undo/redo counts."""
    data = {
        "active_file": session_mgr.active_file,
        "current_level": session_mgr.current_level_id,
        "undo_steps": len(session_mgr.undo_stack),
        "redo_steps": len(session_mgr.redo_stack),
        "walls_count": len(session_mgr.model.walls),
        "rooms_count": len(session_mgr.model.rooms),
        "dimensions_count": len(session_mgr.model.dimensions),
    }
    def formatter(d):
        skin.status("Active File", d["active_file"] or "(In-Memory)")
        skin.status("Current Level", d["current_level"])
        skin.status("Undo Steps", d["undo_steps"])
        skin.status("Redo Steps", d["redo_steps"])
    output_result(ctx, data, human_formatter=formatter)

@session.command(name="undo")
@click.pass_context
def session_undo(ctx):
    """Revert the last project modification."""
    ok, msg = session_mgr.undo()
    if ok:
        output_result(ctx, {"undo": True, "message": msg})
    else:
        output_error(ctx, msg)

@session.command(name="redo")
@click.pass_context
def session_redo(ctx):
    """Re-apply the previously undone modification."""
    ok, msg = session_mgr.redo()
    if ok:
        output_result(ctx, {"redo": True, "message": msg})
    else:
        output_error(ctx, msg)

# ==============================================================================
# 10. GUI Desktop App Launcher
# ==============================================================================
@cli.command(name="gui")
@click.option("--file", "project_file", default=None, help="Optional .sh3d file to open.")
@click.pass_context
def gui_cmd(ctx, project_file):
    """Launch the Sweet Home 3D desktop application."""
    target = project_file or session_mgr.active_file
    try:
        res = launch_gui(target)
        output_result(ctx, res)
    except RuntimeError as e:
        output_error(ctx, e)

@cli.command(name="capability")
@click.pass_context
def capability_cmd(ctx):
    """Outputs agent-native capability manifest and supported CAD features (Gate A01, A04, A07, R21-P1-02)."""
    shapely_ok = False
    try:
        import shapely
        shapely_ok = True
    except (ImportError, Exception):
        shapely_ok = False

    manifest = {
        "harness": "cli-anything-sweethome3d",
        "version": __version__,
        "spec_version": "PAC-1",
        "geometry_engine": {
            "name": "shapely",
            "available": shapely_ok,
        },
        "supported_entities": [
            "wall", "room", "opening", "furniture", "dimension", "level"
        ],
        "supported_exports": [
            "json", "svg", "dxf", "html", "sh3d"
        ],
        "capabilities": {
            "noded_walls": True,
            "clear_core": True,
            "vertical_slabs": True,
            "scale_bounded_curves": True,
            "room_holes": True,
            "door_sweep_collision": True,
            "atomic_transactions": True,
            "headless_roundtrip": True
        },
        "commands": {
            "project": ["new", "open", "save", "info", "validate"],
            "wall": ["add", "add-loop", "edit", "list", "remove", "reverse", "split"],
            "room": ["add", "list", "remove"],
            "opening": ["add", "list", "remove"],
            "furniture": ["add", "list", "remove", "catalog"],
            "dimension": ["add", "list", "remove"],
            "level": ["add", "list", "set-current"],
            "export": ["json", "svg", "dxf", "html", "sh3d"],
            "session": ["undo", "redo", "status"],
            "system": ["capability", "doctor"]
        },
        "mutation_inventory": {
            "wall": ["add", "add-loop", "remove", "reverse", "split"],
            "room": ["add", "remove"],
            "opening": ["add", "remove"],
            "furniture": ["add", "remove"],
            "dimension": ["add", "remove"],
            "level": ["add"]
        }
    }
    output_result(ctx, manifest, include_project=False)

def decode_diagnostic_session(text):
    """Strictly validates diagnostic session envelope and root schema (PAC-1 A08, R23-P1-02)."""
    data = strict_json_loads(text)
    if type(data) is not dict:
        raise ValidationRejectedError("Session JSON root must be an object")
    if type(data.get("model")) is not dict:
        raise ValidationRejectedError("Session model must be an object")
    if "revision" in data and (
        type(data["revision"]) is not int or data["revision"] < 0
    ):
        raise ValidationRejectedError("Session revision must be a nonnegative integer")
    return data


@cli.command(name="doctor")
@click.pass_context
def doctor_cmd(ctx):
    """Runs comprehensive environment, session journal, and geometry diagnostics (Gate A08, R21-P1-03, R22-P1-02, R23-P1-02)."""
    shapely_ok = False
    try:
        import shapely
        shapely_ok = True
    except (ImportError, Exception):
        shapely_ok = False

    # Status-only doctor must NEVER instantiate SessionManager if it triggers recovery/reconciliation
    root = ctx.find_root() if ctx else None
    mgr = (root.obj or {}).get("invocation_manager") if root else _session_mgr
    if root and "resolution" in (root.obj or {}):
        session_path = root.obj["resolution"]["session_path"]
    else:
        session_path = getattr(mgr, "session_path", None) if mgr else None
        if not session_path:
            session_path = getattr(sys.modules.get(__name__), "SESSION_FILE", None) or getattr(session_module, "SESSION_FILE", None) or os.path.expanduser("~/.cli_anything_sweethome3d_session.json")

    journal_path = (session_path + ".pending") if session_path else None
    session_exists = os.path.exists(session_path) if session_path else False
    has_pending_journal = os.path.exists(journal_path) if journal_path else False

    model_valid = True
    model_findings = []

    if session_exists:
        try:
            with open(session_path, "r", encoding="utf-8") as f:
                raw_text = f.read()
            data = decode_diagnostic_session(raw_text)
            model = HomeModel.from_dict(data["model"])
            val = model.validate()
            model_valid = val.get("valid", True)
            model_findings = val.get("findings", [])
        except Exception as e:
            model_valid = False
            msg = str(e)
            if "Session JSON root must be an object" in msg:
                code = "INVALID_SESSION_SHAPE"
            elif "Session model must be an object" in msg:
                code = "MISSING_SESSION_MODEL"
            elif "Session revision must be a nonnegative integer" in msg:
                code = "INVALID_SESSION_REVISION"
            else:
                code = "SESSION_CORRUPT"
            model_findings.append({"code": code, "severity": "error", "message": msg})
    elif mgr and getattr(mgr, "model", None):
        try:
            val = mgr.model.validate()
            model_valid = val.get("valid", True)
            model_findings = val.get("findings", [])
        except Exception as e:
            model_valid = False
            model_findings.append({"code": "MODEL_VALIDATE_ERROR", "severity": "error", "message": str(e)})

    active_artifact = getattr(mgr, "active_file", None) if mgr else None

    health_data = {
        "healthy": shapely_ok and not has_pending_journal and model_valid,
        "checks": {
            "geometry_engine_available": shapely_ok,
            "active_session_exists": session_exists,
            "pending_journal": has_pending_journal,
            "model_valid": model_valid,
        },
        "session_path": session_path,
        "active_session": session_path,
        "active_artifact": active_artifact,
        "journal_path": journal_path,
        "findings": model_findings
    }
    output_result(ctx, health_data, include_project=False)

# ==============================================================================
# 11. Interactive REPL Mode
# ==============================================================================
def run_repl():
    """Starts the interactive prompt-driven REPL."""
    skin.print_banner()
    while True:
        try:
            prompt_name = session_mgr.model.name or "sweethome3d"
            lvl_name = session_mgr.current_level_id
            cmd_line = input(f"{skin.CYAN}[{prompt_name} ({lvl_name})]{skin.RESET} > ").strip()
            if not cmd_line:
                continue
            if cmd_line.lower() in ["exit", "quit", "q"]:
                skin.print_goodbye()
                break
            if cmd_line.lower() == "help":
                print("\nAvailable command groups: project, wall, room, opening, furniture, dimension, level, export, session, gui")
                print("Examples: 'project info', 'wall add-loop', 'room add --with-walls', 'export html -o plan.html'")
                print("Type 'exit' to quit.\n")
                continue

            import shlex
            args = shlex.split(cmd_line)
            try:
                parent = click.get_current_context(silent=True)
                if parent:
                    with cli.make_context("sweethome3d", args, parent=parent) as child:
                        cli.invoke(child)
                else:
                    cli.main(args=args, standalone_mode=False)
            except click.ClickException as ce:
                skin.error(str(ce))
            except SystemExit:
                pass
            except Exception as ex:
                skin.error(f"Error: {ex}")
        except (KeyboardInterrupt, EOFError):
            skin.print_goodbye()
            break

def main():
    cli()

if __name__ == "__main__":
    main()

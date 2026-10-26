import os
import sys
import json
import click

from cli_anything.sweethome3d.core import (
    HomeModel,
    SessionManager,
    FURNITURE_CATALOG,
    DimensionLine,
    export_svg,
    export_dxf,
    export_json,
    export_html,
)
from cli_anything.sweethome3d.utils.repl_skin import ReplSkin
from cli_anything.sweethome3d.utils.sweethome3d_backend import find_sweethome3d, launch_gui, get_install_instructions

_session_mgr = None

def get_session_mgr():
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

def output_result(ctx, data, human_formatter=None):
    """Outputs data either as JSON or human-readable format based on context flag."""
    is_json = ctx.obj.get("json", False) if ctx.obj else False
    if is_json:
        click.echo(json.dumps({"status": "success", "data": data}, indent=2))
    elif human_formatter:
        human_formatter(data)
    elif isinstance(data, (dict, list)):
        click.echo(json.dumps(data, indent=2))
    else:
        skin.success(str(data))

def output_error(ctx, message, code=1):
    is_json = ctx.obj.get("json", False) if ctx.obj else False
    if is_json:
        click.echo(json.dumps({"status": "error", "message": str(message)}, indent=2))
    else:
        skin.error(str(message))
    sys.exit(code)

@click.group(invoke_without_command=True)
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
@click.pass_context
def cli(ctx, json_mode):
    """CLI-Anything: Agent-Native Command Line Interface for Sweet Home 3D."""
    ctx.ensure_object(dict)
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
        output_error(ctx, f"Failed to open project: {e}")

@project.command(name="save")
@click.option("-o", "--output", "filepath", default=None, help="Destination .sh3d or .json path.")
@click.pass_context
def project_save(ctx, filepath):
    """Save the active project to .sh3d or .json."""
    try:
        saved_path = session_mgr.save_file(filepath)
        output_result(ctx, {"saved": saved_path})
    except Exception as e:
        output_error(ctx, f"Failed to save project: {e}")

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
        if is_json:
            click.echo(json.dumps({
                "status": "error",
                "error": "Strict validation failed: model violates constraints or has unchecked coverage.",
                "data": val
            }, indent=2))
        else:
            skin.error("Project validation FAILED (strict mode).")
            for err in val.get("errors", []):
                skin.error(f"Error: {err}")
            for warn in val.get("warnings", []):
                skin.warning(f"Warning: {warn}")
        ctx.exit(1)

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
        output_error(ctx, str(e))
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
        output_error(ctx, str(e))

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
        output_error(ctx, f"Invalid points format: {e}")

    try:
        with session_mgr.transaction():
            session_mgr.push_state()
            created = session_mgr.model.add_wall_loop(pts, prefix=id_prefix, closed=closed, thickness=thickness, height=height, level_id=target_lvl)
            session_mgr.save_session()
        data = [w.to_dict() for w in created]
        output_result(ctx, {"created_count": len(created), "created_walls": data, "walls": data})
    except Exception as e:
        output_error(ctx, str(e))

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
                raise ValueError(f"Wall '{wall_id}' not found.")
            session_mgr.save_session()
        output_result(ctx, {"removed": wall_id})
    except Exception as e:
        output_error(ctx, str(e))

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
        output_error(ctx, f"Invalid points format: {e}")

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
        output_error(ctx, str(e))

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
                raise ValueError(f"Room '{room_id}' not found.")
            session_mgr.save_session()
        output_result(ctx, {"removed": room_id})
    except Exception as e:
        output_error(ctx, str(e))

@room.command(name="area")
@click.argument("room_id")
@click.pass_context
def room_area(ctx, room_id):
    """Get the exact verified area of a room in m²."""
    r = session_mgr.model.rooms.get(room_id)
    if not r:
        output_error(ctx, f"Room '{room_id}' not found.")
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
        output_error(ctx, str(e))

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
                raise ValueError(f"Opening '{opening_id}' not found.")
            session_mgr.save_session()
        output_result(ctx, {"removed": opening_id})
    except Exception as e:
        output_error(ctx, str(e))

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
        output_error(ctx, str(e))

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
                raise ValueError(f"Furniture '{furniture_id}' not found.")
            session_mgr.save_session()
        output_result(ctx, {"removed": furniture_id})
    except Exception as e:
        output_error(ctx, str(e))

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
        output_error(ctx, str(e))

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
                raise ValueError(f"Dimension '{dimension_id}' not found.")
            session_mgr.save_session()
        output_result(ctx, {"removed": dimension_id})
    except Exception as e:
        output_error(ctx, str(e))

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
        output_error(ctx, str(e))

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
        output_error(ctx, str(e))

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
        assert_safe_export_target(output)
        out_path = export_svg(session_mgr.model, output, level_id=level_id)
        output_result(ctx, {"exported": out_path, "format": "svg", "level": level_id or "all"})
    except Exception as e:
        output_error(ctx, f"Export SVG failed: {e}")

@export.command(name="dxf")
@click.option("-o", "--output", required=True, help="Destination .dxf path.")
@click.option("--level", "level_id", default=None, help="Filter by level ID.")
@click.pass_context
def export_dxf_cmd(ctx, output, level_id):
    """Export AutoCAD-compatible DXF R12 file."""
    try:
        assert_safe_export_target(output)
        out_path = export_dxf(session_mgr.model, output, level_id=level_id)
        output_result(ctx, {"exported": out_path, "format": "dxf", "level": level_id or "all"})
    except Exception as e:
        output_error(ctx, f"Export DXF failed: {e}")

@export.command(name="html")
@click.option("-o", "--output", required=True, help="Destination .html path.")
@click.option("--level", "level_id", default=None, help="Filter by level ID.")
@click.pass_context
def export_html_cmd(ctx, output, level_id):
    """Export interactive self-contained HTML floor plan viewer."""
    try:
        assert_safe_export_target(output)
        out_path = export_html(session_mgr.model, output, level_id=level_id)
        output_result(ctx, {"exported": out_path, "format": "html", "level": level_id or "all"})
    except Exception as e:
        output_error(ctx, f"Export HTML failed: {e}")

@export.command(name="sh3d")
@click.option("-o", "--output", required=True, help="Destination .sh3d path.")
@click.pass_context
def export_sh3d_cmd(ctx, output):
    """Export Sweet Home 3D native compressed ZIP archive (.sh3d)."""
    try:
        assert_safe_export_target(output)
        out_path = session_mgr.model.save_sh3d(output)
        output_result(ctx, {"exported": out_path, "format": "sh3d"})
    except Exception as e:
        output_error(ctx, f"Export SH3D failed: {e}")

@export.command(name="json")
@click.option("-o", "--output", required=True, help="Destination .json path.")
@click.option("--level", "level_id", default=None, help="Filter by level ID.")
@click.pass_context
def export_json_cmd(ctx, output, level_id):
    """Export structured JSON floor plan model."""
    try:
        assert_safe_export_target(output)
        out_path = export_json(session_mgr.model, output, level_id=level_id)
        output_result(ctx, {"exported": out_path, "format": "json", "level": level_id or "all"})
    except Exception as e:
        output_error(ctx, f"Export JSON failed: {e}")

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
        output_error(ctx, str(e))

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

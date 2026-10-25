import os
import sys
import json
import click

from cli_anything.sweethome3d.core import (
    HomeModel,
    SessionManager,
    FURNITURE_CATALOG,
    export_svg,
    export_dxf,
    export_json,
)
from cli_anything.sweethome3d.utils.repl_skin import ReplSkin
from cli_anything.sweethome3d.utils.sweethome3d_backend import find_sweethome3d, launch_gui, get_install_instructions

session_mgr = SessionManager()
skin = ReplSkin("sweethome3d", version="1.0.0")

def output_result(ctx, data, human_formatter=None):
    """Outputs data either as JSON or human-readable format based on context flag."""
    is_json = ctx.obj.get("json", False) if ctx.obj else False
    if is_json:
        click.echo(json.dumps({"status": "success", "data": data}, indent=2))
    elif human_formatter:
        human_formatter(data)
    elif isinstance(data, dict) or isinstance(data, list):
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
    """Project management commands (new, open, save, info, validate)."""
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
        "summary": val["summary"],
        "levels": [lvl.to_dict() for lvl in m.levels],
    }
    def formatter(d):
        skin.status("Project Name", d["name"])
        skin.status("Active File", d["active_file"] or "(Unsaved In-Memory)")
        skin.status("Total Area", f"{d['summary']['total_area_m2']:.2f} m²")
        skin.status("Total Wall Run", f"{d['summary']['total_wall_length_m']:.1f} m")
        skin.status("Walls Count", d['summary']['walls_count'])
        skin.status("Rooms Count", d['summary']['rooms_count'])
        skin.status("Openings Count", d['summary']['openings_count'])
        skin.status("Furniture Count", d['summary']['furniture_count'])
    output_result(ctx, data, human_formatter=formatter)

@project.command(name="validate")
@click.pass_context
def project_validate(ctx):
    """Run architectural constraint and closure validation checks."""
    val = session_mgr.model.validate()
    def formatter(d):
        if d["valid"]:
            skin.success("Project architectural validation PASSED.")
        else:
            skin.error("Project validation FAILED.")
        for err in d["errors"]:
            skin.error(f"Error: {err}")
        for warn in d["warnings"]:
            skin.warning(f"Warning: {warn}")
    output_result(ctx, val, human_formatter=formatter)

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
@click.option("--level", "level_id", default="level1", help="Target level ID.")
@click.pass_context
def wall_add(ctx, wall_id, x1, y1, x2, y2, thickness, height, level_id):
    """Add a new wall segment."""
    session_mgr.push_state()
    w = session_mgr.model.add_wall(wall_id, x1, y1, x2, y2, thickness, height, level_id)
    session_mgr.save_session()
    output_result(ctx, w.to_dict())

@wall.command(name="list")
@click.pass_context
def wall_list(ctx):
    """List all walls in the project."""
    walls = [w.to_dict() for w in session_mgr.model.walls.values()]
    def formatter(data):
        headers = ["ID", "Start (X,Y)", "End (X,Y)", "Len (m)", "Thick (cm)", "Level"]
        rows = [
            [d["id"], f"{d['x_start']:.0f},{d['y_start']:.0f}", f"{d['x_end']:.0f},{d['y_end']:.0f}",
             d["length_m"], d["thickness"], d["level_id"]]
            for d in data
        ]
        skin.table(headers, rows)
    output_result(ctx, walls, human_formatter=formatter)

@wall.command(name="remove")
@click.argument("wall_id")
@click.pass_context
def wall_remove(ctx, wall_id):
    """Remove a wall by its ID."""
    session_mgr.push_state()
    removed = session_mgr.model.remove_wall(wall_id)
    session_mgr.save_session()
    if removed:
        output_result(ctx, {"removed": wall_id})
    else:
        output_error(ctx, f"Wall '{wall_id}' not found.")

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
@click.option("--level", "level_id", default="level1", help="Target level ID.")
@click.pass_context
def room_add(ctx, room_id, name, points, color, level_id):
    """Add an enclosed architectural room polygon."""
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

    session_mgr.push_state()
    r = session_mgr.model.add_room(room_id, name, pts, level_id=level_id, floor_color=color)
    session_mgr.save_session()
    output_result(ctx, r.to_dict())

@room.command(name="list")
@click.pass_context
def room_list(ctx):
    """List all rooms and calculated floor areas."""
    rooms = [r.to_dict() for r in session_mgr.model.rooms.values()]
    def formatter(data):
        headers = ["ID", "Name", "Area (m²)", "Vertices", "Color", "Centroid (cm)"]
        rows = [
            [d["id"], d["name"], d["area_m2"], len(d["points"]), d["floor_color"],
             f"{d['centroid'][0]:.0f},{d['centroid'][1]:.0f}"]
            for d in data
        ]
        skin.table(headers, rows)
    output_result(ctx, rooms, human_formatter=formatter)

@room.command(name="remove")
@click.argument("room_id")
@click.pass_context
def room_remove(ctx, room_id):
    """Remove a room by ID."""
    session_mgr.push_state()
    removed = session_mgr.model.remove_room(room_id)
    session_mgr.save_session()
    if removed:
        output_result(ctx, {"removed": room_id})
    else:
        output_error(ctx, f"Room '{room_id}' not found.")

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
    session_mgr.push_state()
    try:
        op = session_mgr.model.add_opening(opening_id, name, wall_id, offset, width, height, opening_type, swing)
        session_mgr.save_session()
        output_result(ctx, op.to_dict())
    except ValueError as e:
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
    session_mgr.push_state()
    removed = session_mgr.model.remove_opening(opening_id)
    session_mgr.save_session()
    if removed:
        output_result(ctx, {"removed": opening_id})
    else:
        output_error(ctx, f"Opening '{opening_id}' not found.")

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
@click.option("--width", default=None, type=float, help="Width in cm.")
@click.option("--depth", default=None, type=float, help="Depth in cm.")
@click.option("--height", default=None, type=float, help="Height in cm.")
@click.option("--angle", default=0.0, type=float, help="Rotation angle in degrees [0, 360).")
@click.option("--level", "level_id", default="level1", help="Target level ID.")
@click.pass_context
def furniture_add(ctx, furniture_id, name, catalog_id, x, y, width, depth, height, angle, level_id):
    """Add a piece of furniture."""
    cat_entry = FURNITURE_CATALOG.get(catalog_id, {})
    item_name = name or cat_entry.get("name", "Custom Furniture")
    w = width or cat_entry.get("width", 100.0)
    d = depth or cat_entry.get("depth", 100.0)
    h = height or cat_entry.get("height", 80.0)

    session_mgr.push_state()
    f = session_mgr.model.add_furniture(furniture_id, item_name, catalog_id, x, y, 0.0, w, d, h, angle, level_id)
    session_mgr.save_session()
    output_result(ctx, f.to_dict())

@furniture.command(name="list")
@click.pass_context
def furniture_list(ctx):
    """List all furniture in the project."""
    items = [f.to_dict() for f in session_mgr.model.furniture.values()]
    def formatter(data):
        headers = ["ID", "Name", "Catalog ID", "Pos (X,Y)", "Size (WxD)", "Angle"]
        rows = [
            [d["id"], d["name"], d["catalog_id"], f"{d['x_cm']:.0f},{d['y_cm']:.0f}",
             f"{d['width_cm']:.0f}x{d['depth_cm']:.0f}", f"{d['angle_deg']:.0f}°"]
            for d in data
        ]
        skin.table(headers, rows)
    output_result(ctx, items, human_formatter=formatter)

@furniture.command(name="remove")
@click.argument("furniture_id")
@click.pass_context
def furniture_remove(ctx, furniture_id):
    """Remove furniture item by ID."""
    session_mgr.push_state()
    removed = session_mgr.model.remove_furniture(furniture_id)
    session_mgr.save_session()
    if removed:
        output_result(ctx, {"removed": furniture_id})
    else:
        output_error(ctx, f"Furniture '{furniture_id}' not found.")

# ==============================================================================
# 6. Export Commands
# ==============================================================================
@cli.group()
def export():
    """Export floorplan drawings to SVG, DXF, SH3D, or JSON."""
    pass

@export.command(name="svg")
@click.option("-o", "--output", required=True, help="Destination .svg path.")
@click.pass_context
def export_svg_cmd(ctx, output):
    """Export layered architectural vector SVG."""
    try:
        out_path = export_svg(session_mgr.model, output)
        output_result(ctx, {"exported": out_path, "format": "svg"})
    except Exception as e:
        output_error(ctx, f"Export SVG failed: {e}")

@export.command(name="dxf")
@click.option("-o", "--output", required=True, help="Destination .dxf path.")
@click.pass_context
def export_dxf_cmd(ctx, output):
    """Export AutoCAD-compatible DXF R12 file."""
    try:
        out_path = export_dxf(session_mgr.model, output)
        output_result(ctx, {"exported": out_path, "format": "dxf"})
    except Exception as e:
        output_error(ctx, f"Export DXF failed: {e}")

@export.command(name="sh3d")
@click.option("-o", "--output", required=True, help="Destination .sh3d path.")
@click.pass_context
def export_sh3d_cmd(ctx, output):
    """Export Sweet Home 3D native compressed ZIP archive (.sh3d)."""
    try:
        out_path = session_mgr.model.save_sh3d(output)
        output_result(ctx, {"exported": out_path, "format": "sh3d"})
    except Exception as e:
        output_error(ctx, f"Export SH3D failed: {e}")

@export.command(name="json")
@click.option("-o", "--output", required=True, help="Destination .json path.")
@click.pass_context
def export_json_cmd(ctx, output):
    """Export structured JSON floor plan model."""
    try:
        out_path = export_json(session_mgr.model, output)
        output_result(ctx, {"exported": out_path, "format": "json"})
    except Exception as e:
        output_error(ctx, f"Export JSON failed: {e}")

# ==============================================================================
# 7. Session Commands
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
        "undo_steps": len(session_mgr.undo_stack),
        "redo_steps": len(session_mgr.redo_stack),
        "walls_count": len(session_mgr.model.walls),
        "rooms_count": len(session_mgr.model.rooms),
    }
    def formatter(d):
        skin.status("Active File", d["active_file"] or "(In-Memory)")
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
# 8. GUI Desktop App Launcher
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
# 9. Interactive REPL Mode
# ==============================================================================
def run_repl():
    """Starts the interactive prompt-driven REPL."""
    skin.print_banner()
    while True:
        try:
            prompt_name = session_mgr.model.name or "sweethome3d"
            cmd_line = input(f"{skin.CYAN}[{prompt_name}]{skin.RESET} > ").strip()
            if not cmd_line:
                continue
            if cmd_line.lower() in ["exit", "quit", "q"]:
                skin.print_goodbye()
                break
            if cmd_line.lower() == "help":
                print("\nAvailable command groups: project, wall, room, opening, furniture, export, session, gui")
                print("Examples: 'project info', 'wall list', 'room list', 'export svg -o out.svg'")
                print("Type 'exit' to quit.\n")
                continue

            # Split command line respecting quotes
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

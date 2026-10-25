import os
import zipfile
import xml.etree.ElementTree as ET
from xml.dom import minidom

from cli_anything.sweethome3d.core.wall import Wall
from cli_anything.sweethome3d.core.room import Room
from cli_anything.sweethome3d.core.opening import Opening
from cli_anything.sweethome3d.core.furniture import Furniture

class Level:
    def __init__(self, level_id="level1", name="Ground Floor", elevation_cm=0.0, height_cm=280.0):
        self.id = str(level_id)
        self.name = str(name)
        self.elevation_cm = float(elevation_cm)
        self.height_cm = float(height_cm)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "elevation_cm": self.elevation_cm,
            "height_cm": self.height_cm,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            level_id=d.get("id", "level1"),
            name=d.get("name", "Ground Floor"),
            elevation_cm=d.get("elevation_cm", 0.0),
            height_cm=d.get("height_cm", 280.0),
        )

class HomeModel:
    """Core in-memory representation of a Sweet Home 3D project."""

    def __init__(self, name="New Home", wall_height=280.0, version="7.5"):
        self.name = str(name)
        self.wall_height = float(wall_height)
        self.version = str(version)
        self.levels = [Level("level1", "Ground Floor", 0.0, wall_height)]
        self.walls = {}
        self.rooms = {}
        self.openings = {}
        self.furniture = {}

    # Level methods
    def add_level(self, level_id, name, elevation_cm=0.0, height_cm=280.0):
        lvl = Level(level_id, name, elevation_cm, height_cm)
        self.levels.append(lvl)
        return lvl

    def get_level(self, level_id):
        for lvl in self.levels:
            if lvl.id == level_id:
                return lvl
        return None

    # Wall methods
    def add_wall(self, wall_id, x_start, y_start, x_end, y_end, thickness=15.0, height=None, level_id="level1"):
        h = height if height is not None else self.wall_height
        w = Wall(wall_id, x_start, y_start, x_end, y_end, thickness, h, level_id)
        self.walls[w.id] = w
        return w

    def remove_wall(self, wall_id):
        if wall_id in self.walls:
            # Also remove openings hosted on this wall
            to_remove = [oid for oid, op in self.openings.items() if op.wall_id == wall_id]
            for oid in to_remove:
                del self.openings[oid]
            del self.walls[wall_id]
            return True
        return False

    # Room methods
    def add_room(self, room_id, name, points, level_id="level1", floor_color="#EAEAEA"):
        r = Room(room_id, name, points, level_id, floor_color)
        self.rooms[r.id] = r
        return r

    def remove_room(self, room_id):
        if room_id in self.rooms:
            del self.rooms[room_id]
            return True
        return False

    # Opening methods
    def add_opening(self, opening_id, name, wall_id, offset_cm=50.0, width_cm=90.0, height_cm=210.0,
                    opening_type="door", swing="right"):
        if wall_id not in self.walls:
            raise ValueError(f"Wall '{wall_id}' does not exist.")
        op = Opening(opening_id, name, wall_id, offset_cm, width_cm, height_cm, opening_type, swing)
        self.openings[op.id] = op
        return op

    def remove_opening(self, opening_id):
        if opening_id in self.openings:
            del self.openings[opening_id]
            return True
        return False

    # Furniture methods
    def add_furniture(self, furniture_id, name, catalog_id="custom", x_cm=0.0, y_cm=0.0,
                      elevation_cm=0.0, width_cm=100.0, depth_cm=100.0, height_cm=80.0,
                      angle_deg=0.0, level_id="level1"):
        f = Furniture(furniture_id, name, catalog_id, x_cm, y_cm, elevation_cm,
                      width_cm, depth_cm, height_cm, angle_deg, level_id)
        self.furniture[f.id] = f
        return f

    def remove_furniture(self, furniture_id):
        if furniture_id in self.furniture:
            del self.furniture[furniture_id]
            return True
        return False

    @property
    def total_area_m2(self):
        """Sum of all room floor areas in square meters."""
        return round(sum(r.area_m2 for r in self.rooms.values()), 2)

    @property
    def total_wall_length_m(self):
        """Total run of walls in linear meters."""
        return round(sum(w.length_m for w in self.walls.values()), 2)

    def validate(self):
        """Runs architectural validation checks.
        
        Returns a dictionary with status and list of warnings/errors.
        """
        errors = []
        warnings = []

        # 1. Check opening bounds on walls
        for op in self.openings.values():
            wall = self.walls.get(op.wall_id)
            if not wall:
                errors.append(f"Opening '{op.id}' references missing wall '{op.wall_id}'.")
            elif op.offset_cm + op.width_cm > wall.length:
                warnings.append(
                    f"Opening '{op.id}' ({op.width_cm}cm) exceeds length of wall '{wall.id}' ({wall.length:.1f}cm)."
                )

        # 2. Check room point counts
        for r in self.rooms.values():
            if len(r.points) < 3:
                errors.append(f"Room '{r.id}' has fewer than 3 vertices ({len(r.points)}).")
            elif r.area_m2 <= 0.0:
                warnings.append(f"Room '{r.id}' ({r.name}) has zero or degenerate area.")

        # 3. Check for empty structure
        if not self.walls and not self.rooms:
            warnings.append("Project contains no walls or rooms.")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "summary": {
                "walls_count": len(self.walls),
                "rooms_count": len(self.rooms),
                "openings_count": len(self.openings),
                "furniture_count": len(self.furniture),
                "total_area_m2": self.total_area_m2,
                "total_wall_length_m": self.total_wall_length_m,
            }
        }

    def to_xml(self):
        """Generates valid Sweet Home 3D Home.xml string."""
        root = ET.Element("home", version=self.version, name=self.name, wallHeight=str(self.wall_height))
        
        # Levels
        for lvl in self.levels:
            ET.SubElement(root, "level", id=lvl.id, name=lvl.name,
                          elevation=str(lvl.elevation_cm), height=str(lvl.height_cm))

        # Walls
        for w in self.walls.values():
            ET.SubElement(root, "wall", id=w.id, level=w.level_id,
                          xStart=str(w.x_start), yStart=str(w.y_start),
                          xEnd=str(w.x_end), yEnd=str(w.y_end),
                          thickness=str(w.thickness), height=str(w.height))

        # Rooms
        for r in self.rooms.values():
            room_elem = ET.SubElement(root, "room", id=r.id, level=r.level_id,
                                      name=r.name, area=str(r.area_m2), floorColor=r.floor_color)
            for p in r.points:
                ET.SubElement(room_elem, "point", x=str(p[0]), y=str(p[1]))

        # Openings (doors and windows)
        for op in self.openings.values():
            ET.SubElement(root, "doorOrWindow", id=op.id, wall=op.wall_id,
                          name=op.name, offset=str(op.offset_cm), width=str(op.width_cm),
                          height=str(op.height_cm), type=op.opening_type, swing=op.swing)

        # Furniture
        for f in self.furniture.values():
            ET.SubElement(root, "pieceOfFurniture", id=f.id, level=f.level_id,
                          name=f.name, catalogId=f.catalog_id,
                          x=str(f.x_cm), y=str(f.y_cm), elevation=str(f.elevation_cm),
                          width=str(f.width_cm), depth=str(f.depth_cm), height=str(f.height_cm),
                          angle=str(f.angle_deg))

        # Format pretty XML
        rough_string = ET.tostring(root, "utf-8")
        reparsed = minidom.parseString(rough_string)
        return reparsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")

    @classmethod
    def from_xml(cls, xml_text):
        """Parses Sweet Home 3D Home.xml into a HomeModel instance."""
        root = ET.fromstring(xml_text)
        model = cls(
            name=root.attrib.get("name", "Imported Home"),
            wall_height=float(root.attrib.get("wallHeight", 280.0)),
            version=root.attrib.get("version", "7.5"),
        )
        model.levels = []

        # Levels
        for elem in root.findall("level"):
            model.add_level(
                level_id=elem.attrib.get("id"),
                name=elem.attrib.get("name", "Level"),
                elevation_cm=float(elem.attrib.get("elevation", 0.0)),
                height_cm=float(elem.attrib.get("height", 280.0)),
            )
        if not model.levels:
            model.levels = [Level("level1", "Ground Floor", 0.0, 280.0)]

        # Walls
        for elem in root.findall("wall"):
            model.add_wall(
                wall_id=elem.attrib.get("id"),
                x_start=float(elem.attrib.get("xStart", 0.0)),
                y_start=float(elem.attrib.get("yStart", 0.0)),
                x_end=float(elem.attrib.get("xEnd", 0.0)),
                y_end=float(elem.attrib.get("yEnd", 0.0)),
                thickness=float(elem.attrib.get("thickness", 15.0)),
                height=float(elem.attrib.get("height", 280.0)),
                level_id=elem.attrib.get("level", "level1"),
            )

        # Rooms
        for elem in root.findall("room"):
            points = []
            for p in elem.findall("point"):
                points.append((float(p.attrib.get("x", 0.0)), float(p.attrib.get("y", 0.0))))
            model.add_room(
                room_id=elem.attrib.get("id"),
                name=elem.attrib.get("name", "Room"),
                points=points,
                level_id=elem.attrib.get("level", "level1"),
                floor_color=elem.attrib.get("floorColor", "#EAEAEA"),
            )

        # Openings
        for elem in root.findall("doorOrWindow"):
            model.add_opening(
                opening_id=elem.attrib.get("id"),
                name=elem.attrib.get("name", "Door/Window"),
                wall_id=elem.attrib.get("wall"),
                offset_cm=float(elem.attrib.get("offset", 50.0)),
                width_cm=float(elem.attrib.get("width", 90.0)),
                height_cm=float(elem.attrib.get("height", 210.0)),
                opening_type=elem.attrib.get("type", "door"),
                swing=elem.attrib.get("swing", "right"),
            )

        # Furniture
        for elem in root.findall("pieceOfFurniture"):
            model.add_furniture(
                furniture_id=elem.attrib.get("id"),
                name=elem.attrib.get("name", "Furniture"),
                catalog_id=elem.attrib.get("catalogId", "custom"),
                x_cm=float(elem.attrib.get("x", 0.0)),
                y_cm=float(elem.attrib.get("y", 0.0)),
                elevation_cm=float(elem.attrib.get("elevation", 0.0)),
                width_cm=float(elem.attrib.get("width", 100.0)),
                depth_cm=float(elem.attrib.get("depth", 100.0)),
                height_cm=float(elem.attrib.get("height", 80.0)),
                angle_deg=float(elem.attrib.get("angle", 0.0)),
                level_id=elem.attrib.get("level", "level1"),
            )

        return model

    def save_sh3d(self, filepath):
        """Saves project as a native .sh3d compressed ZIP archive containing Home.xml."""
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        xml_content = self.to_xml()
        with zipfile.ZipFile(filepath, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Home.xml", xml_content)
        return filepath

    @classmethod
    def load_sh3d(cls, filepath):
        """Loads project from a .sh3d compressed ZIP archive."""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Project file '{filepath}' not found.")
        with zipfile.ZipFile(filepath, "r") as zf:
            if "Home.xml" not in zf.namelist():
                raise ValueError("Corrupted .sh3d file: missing 'Home.xml' entry.")
            xml_content = zf.read("Home.xml").decode("utf-8")
        return cls.from_xml(xml_content)

    def to_dict(self):
        return {
            "name": self.name,
            "version": self.version,
            "wall_height": self.wall_height,
            "levels": [lvl.to_dict() for lvl in self.levels],
            "walls": [w.to_dict() for w in self.walls.values()],
            "rooms": [r.to_dict() for r in self.rooms.values()],
            "openings": [op.to_dict() for op in self.openings.values()],
            "furniture": [f.to_dict() for f in self.furniture.values()],
            "summary": {
                "total_area_m2": self.total_area_m2,
                "total_wall_length_m": self.total_wall_length_m,
            }
        }

    @classmethod
    def from_dict(cls, d):
        model = cls(
            name=d.get("name", "New Home"),
            wall_height=d.get("wall_height", 280.0),
            version=d.get("version", "7.5"),
        )
        model.levels = [Level.from_dict(lvl) for lvl in d.get("levels", [])]
        if not model.levels:
            model.levels = [Level("level1", "Ground Floor", 0.0, 280.0)]
        for w in d.get("walls", []):
            wall_obj = Wall.from_dict(w)
            model.walls[wall_obj.id] = wall_obj
        for r in d.get("rooms", []):
            room_obj = Room.from_dict(r)
            model.rooms[room_obj.id] = room_obj
        for op in d.get("openings", []):
            op_obj = Opening.from_dict(op)
            model.openings[op_obj.id] = op_obj
        for f in d.get("furniture", []):
            f_obj = Furniture.from_dict(f)
            model.furniture[f_obj.id] = f_obj
        return model

import math

class Opening:
    """Represents a door or window opening hosted on a wall in Sweet Home 3D."""

    def __init__(self, opening_id, name, wall_id=None, offset_cm=50.0, width_cm=90.0, height_cm=210.0,
                 opening_type="door", swing="right", elevation_cm=0.0, depth_cm=None,
                 x_cm=None, y_cm=None, angle_deg=None, level_id="level1",
                 extra_attribs=None, extra_children=None):
        self.id = str(opening_id)
        self.name = str(name)
        self.wall_id = str(wall_id) if wall_id is not None else None
        self.offset_cm = float(offset_cm) if offset_cm is not None else 0.0
        self.width_cm = float(width_cm)
        self.height_cm = float(height_cm)
        self.opening_type = str(opening_type).lower()  # 'door' or 'window'
        self.swing = str(swing).lower()  # 'left', 'right', 'sliding', 'none'
        self.elevation_cm = float(elevation_cm) if elevation_cm is not None else 0.0
        self.depth_cm = float(depth_cm) if depth_cm is not None else None
        self.x_cm = float(x_cm) if x_cm is not None else None
        self.y_cm = float(y_cm) if y_cm is not None else None
        self.angle_deg = float(angle_deg) if angle_deg is not None else None
        self.level_id = str(level_id) if level_id is not None else "level1"
        self.extra_attribs = dict(extra_attribs) if extra_attribs else {}
        self.extra_children = list(extra_children) if extra_children else []

    def get_effective_geometry(self, wall=None):
        """Calculates authoritative 2D geometric orientation, leaf endpoints, and operation parameters.
        
        Returns a dict:
            p1: (x, y) - start of opening along width
            p2: (x, y) - end of opening along width
            center: (x, y) - midpoint (x, y)
            angle_deg: float - effective orientation angle in degrees
            ux: float, uy: float - unit vector along opening direction
            nx: float, ny: float - unit normal vector (open leaf direction)
            width: float - opening width in cm
            hinge: (x, y) - door hinge position
            latch: (x, y) - door latch position
            open_endpoint: (x, y) - open leaf tip position when swung 90 deg
        """
        w = self.width_cm
        if w is None or not isinstance(w, (int, float)) or not math.isfinite(w) or w <= 0:
            return None

        if self.x_cm is not None and self.y_cm is not None:
            if not isinstance(self.x_cm, (int, float)) or not math.isfinite(self.x_cm):
                return None
            if not isinstance(self.y_cm, (int, float)) or not math.isfinite(self.y_cm):
                return None
            center_x = float(self.x_cm)
            center_y = float(self.y_cm)
            if self.angle_deg is not None:
                if not isinstance(self.angle_deg, (int, float)) or not math.isfinite(self.angle_deg):
                    return None
                eff_angle = float(self.angle_deg)
            elif wall is not None:
                dx = wall.x_end - wall.x_start
                dy = wall.y_end - wall.y_start
                if not math.isfinite(dx) or not math.isfinite(dy):
                    return None
                eff_angle = math.degrees(math.atan2(dy, dx))
            else:
                eff_angle = 0.0
            if not math.isfinite(eff_angle):
                return None
            rad = math.radians(eff_angle)
            ux, uy = math.cos(rad), math.sin(rad)
            if abs(uy) < 1e-9:
                uy = 0.0
            if abs(ux) < 1e-9:
                ux = 0.0
            nx, ny = -uy, ux
            p1 = (round(center_x - (w / 2.0) * ux, 4), round(center_y - (w / 2.0) * uy, 4))
            p2 = (round(center_x + (w / 2.0) * ux, 4), round(center_y + (w / 2.0) * uy, 4))
        elif wall is not None:
            if self.offset_cm is None or not isinstance(self.offset_cm, (int, float)) or not math.isfinite(self.offset_cm):
                return None
            p1 = wall.point_at_offset(self.offset_cm)
            p2 = wall.point_at_offset(self.offset_cm + w)
            if not math.isfinite(p1[0]) or not math.isfinite(p1[1]) or not math.isfinite(p2[0]) or not math.isfinite(p2[1]):
                return None
            dx = p2[0] - p1[0]
            dy = p2[1] - p1[1]
            L = math.hypot(dx, dy)
            if L < 1e-4:
                L = w
            ux, uy = dx / L, dy / L
            nx, ny = -uy, ux
            center_x = (p1[0] + p2[0]) / 2.0
            center_y = (p1[1] + p2[1]) / 2.0
            eff_angle = math.degrees(math.atan2(dy, dx))
        else:
            return None

        if self.swing == "left":
            hinge = p2
            latch = p1
            open_endpoint = (p2[0] + w * nx, p2[1] + w * ny)
        else:
            hinge = p1
            latch = p2
            open_endpoint = (p1[0] + w * nx, p1[1] + w * ny)

        return {
            "p1": p1,
            "p2": p2,
            "center": (center_x, center_y),
            "angle_deg": eff_angle,
            "ux": ux,
            "uy": uy,
            "nx": nx,
            "ny": ny,
            "width": w,
            "hinge": hinge,
            "latch": latch,
            "open_endpoint": open_endpoint,
        }

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "wall_id": self.wall_id,
            "offset_cm": self.offset_cm,
            "width_cm": self.width_cm,
            "height_cm": self.height_cm,
            "opening_type": self.opening_type,
            "swing": self.swing,
            "elevation_cm": self.elevation_cm,
            "depth_cm": self.depth_cm,
            "x_cm": self.x_cm,
            "y_cm": self.y_cm,
            "angle_deg": self.angle_deg,
            "level_id": self.level_id,
            "extra_attribs": self.extra_attribs,
            "extra_children": self.extra_children,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            opening_id=d.get("id"),
            name=d.get("name", "Opening"),
            wall_id=d.get("wall_id"),
            offset_cm=d.get("offset_cm", 50.0),
            width_cm=d.get("width_cm", 90.0),
            height_cm=d.get("height_cm", 210.0),
            opening_type=d.get("opening_type", "door"),
            swing=d.get("swing", "right"),
            elevation_cm=d.get("elevation_cm", 0.0),
            depth_cm=d.get("depth_cm"),
            x_cm=d.get("x_cm"),
            y_cm=d.get("y_cm"),
            angle_deg=d.get("angle_deg"),
            level_id=d.get("level_id", "level1"),
            extra_attribs=d.get("extra_attribs"),
            extra_children=d.get("extra_children"),
        )

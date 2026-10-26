import math

class _CallableEndpoints(tuple):
    def __call__(self):
        return self

class DimensionLine:
    """Represents an architectural dimension line in Sweet Home 3D."""

    def __init__(self, dimension_id, x_start, y_start, x_end, y_end, offset_cm=30.0,
                 level_id="level1", elevation_cm=0.0, extra_attribs=None, extra_children=None):
        self.id = str(dimension_id)
        self.x_start = float(x_start)
        self.y_start = float(y_start)
        self.x_end = float(x_end)
        self.y_end = float(y_end)
        self.offset_cm = float(offset_cm)
        self.level_id = str(level_id)
        self.elevation_cm = float(elevation_cm)
        self.extra_attribs = dict(extra_attribs) if extra_attribs else {}
        self.extra_children = list(extra_children) if extra_children else []

    @property
    def distance_cm(self):
        """Distance between the two measured points in centimeters."""
        return math.hypot(self.x_end - self.x_start, self.y_end - self.y_start)

    @property
    def distance_m(self):
        """Distance in meters."""
        return round(self.distance_cm / 100.0, 2)

    @property
    def length(self):
        """Alias for distance_cm."""
        return self.distance_cm

    @property
    def length_m(self):
        """Alias for distance_m."""
        return self.distance_m

    @property
    def angle_rad(self):
        """Angle of dimension line."""
        return math.atan2(self.y_end - self.y_start, self.x_end - self.x_start)

    @property
    def offset_endpoints(self):
        """Calculates the offset coordinates (p1_offset, p2_offset) based on offset_cm."""
        theta = self.angle_rad
        # Normal vector perpendicular to line
        nx = -math.sin(theta) * self.offset_cm
        ny = math.cos(theta) * self.offset_cm
        p1 = (round(self.x_start + nx, 2), round(self.y_start + ny, 2))
        p2 = (round(self.x_end + nx, 2), round(self.y_end + ny, 2))
        return _CallableEndpoints((p1, p2))

    def label_pos(self):
        """Calculates label midpoint coordinates."""
        p1, p2 = self.offset_endpoints
        return (round((p1[0] + p2[0]) / 2.0, 2), round((p1[1] + p2[1]) / 2.0, 2))

    def formatted_label(self):
        """Human-readable metric label string."""
        return f"{self.distance_m:.2f} m"

    def to_dict(self):
        return {
            "id": self.id,
            "x_start": self.x_start,
            "y_start": self.y_start,
            "x_end": self.x_end,
            "y_end": self.y_end,
            "offset_cm": self.offset_cm,
            "level_id": self.level_id,
            "elevation_cm": self.elevation_cm,
            "distance_m": self.distance_m,
            "length_m": self.distance_m,
            "length_cm": self.distance_cm,
            "extra_attribs": self.extra_attribs,
            "extra_children": self.extra_children,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            dimension_id=d.get("id"),
            x_start=d.get("x_start", 0.0),
            y_start=d.get("y_start", 0.0),
            x_end=d.get("x_end", 0.0),
            y_end=d.get("y_end", 0.0),
            offset_cm=d.get("offset_cm", 30.0),
            level_id=d.get("level_id", "level1"),
            elevation_cm=d.get("elevation_cm", 0.0),
            extra_attribs=d.get("extra_attribs"),
            extra_children=d.get("extra_children"),
        )

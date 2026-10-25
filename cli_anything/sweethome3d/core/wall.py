import math

class Wall:
    """Represents an architectural wall in Sweet Home 3D (coordinates in centimeters)."""

    def __init__(self, wall_id, x_start, y_start, x_end, y_end, thickness=15.0, height=280.0, level_id="level1"):
        self.id = str(wall_id)
        self.x_start = float(x_start)
        self.y_start = float(y_start)
        self.x_end = float(x_end)
        self.y_end = float(y_end)
        self.thickness = float(thickness)
        self.height = float(height)
        self.level_id = str(level_id)

    @property
    def length(self):
        """Length of wall in centimeters."""
        return math.hypot(self.x_end - self.x_start, self.y_end - self.y_start)

    @property
    def length_m(self):
        """Length of wall in meters."""
        return round(self.length / 100.0, 2)

    @property
    def angle_rad(self):
        """Angle in radians relative to horizontal X axis."""
        return math.atan2(self.y_end - self.y_start, self.x_end - self.x_start)

    @property
    def angle_deg(self):
        """Angle in degrees [0, 360)."""
        deg = math.degrees(self.angle_rad) % 360.0
        return round(deg, 1)

    def point_at_offset(self, offset_cm):
        """Returns (x, y) along the wall center line at a given offset from start."""
        l = self.length
        if l == 0:
            return (self.x_start, self.y_start)
        t = max(0.0, min(1.0, offset_cm / l))
        x = self.x_start + t * (self.x_end - self.x_start)
        y = self.y_start + t * (self.y_end - self.y_start)
        return (round(x, 2), round(y, 2))

    def to_dict(self):
        return {
            "id": self.id,
            "x_start": self.x_start,
            "y_start": self.y_start,
            "x_end": self.x_end,
            "y_end": self.y_end,
            "thickness": self.thickness,
            "height": self.height,
            "level_id": self.level_id,
            "length_m": self.length_m,
            "angle_deg": self.angle_deg,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            wall_id=d.get("id"),
            x_start=d.get("x_start", 0.0),
            y_start=d.get("y_start", 0.0),
            x_end=d.get("x_end", 0.0),
            y_end=d.get("y_end", 0.0),
            thickness=d.get("thickness", 15.0),
            height=d.get("height", 280.0),
            level_id=d.get("level_id", "level1"),
        )

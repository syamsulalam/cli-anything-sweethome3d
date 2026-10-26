class Room:
    """Represents an enclosed architectural room polygon in Sweet Home 3D."""

    def __init__(self, room_id, name, points, level_id="level1", floor_color="#E8E8E8", ceiling_color="#FFFFFF",
                 extra_attribs=None, extra_children=None):
        self.id = str(room_id)
        self.name = str(name)
        # points must be a list of (x, y) tuples/lists in cm
        self.points = [(float(p[0]), float(p[1])) for p in points]
        self.level_id = str(level_id)
        self.floor_color = str(floor_color)
        self.ceiling_color = str(ceiling_color)
        self.extra_attribs = dict(extra_attribs) if extra_attribs else {}
        self.extra_children = list(extra_children) if extra_children else []

    @property
    def area_cm2(self):
        """Calculates polygon area in square centimeters using the Shoelace formula."""
        n = len(self.points)
        if n < 3:
            return 0.0
        area = 0.0
        for i in range(n):
            j = (i + 1) % n
            area += self.points[i][0] * self.points[j][1]
            area -= self.points[j][0] * self.points[i][1]
        return abs(area) / 2.0

    @property
    def area_m2(self):
        """Area in square meters (cm^2 / 10,000)."""
        return round(self.area_cm2 / 10000.0, 2)

    @property
    def perimeter_cm(self):
        """Perimeter in centimeters."""
        import math
        n = len(self.points)
        if n < 2:
            return 0.0
        p = 0.0
        for i in range(n):
            j = (i + 1) % n
            p += math.hypot(self.points[j][0] - self.points[i][0], self.points[j][1] - self.points[i][1])
        return round(p, 1)

    @property
    def centroid(self):
        """Calculates polygon center of mass for label placement."""
        n = len(self.points)
        if n == 0:
            return (0.0, 0.0)
        if n < 3:
            avg_x = sum(p[0] for p in self.points) / n
            avg_y = sum(p[1] for p in self.points) / n
            return (round(avg_x, 2), round(avg_y, 2))
        
        cx, cy, signed_area = 0.0, 0.0, 0.0
        for i in range(n):
            j = (i + 1) % n
            factor = (self.points[i][0] * self.points[j][1] - self.points[j][0] * self.points[i][1])
            signed_area += factor
            cx += (self.points[i][0] + self.points[j][0]) * factor
            cy += (self.points[i][1] + self.points[j][1]) * factor
        
        signed_area *= 0.5
        if abs(signed_area) < 1e-6:
            avg_x = sum(p[0] for p in self.points) / n
            avg_y = sum(p[1] for p in self.points) / n
            return (round(avg_x, 2), round(avg_y, 2))
        
        cx /= (6.0 * signed_area)
        cy /= (6.0 * signed_area)
        return (round(cx, 2), round(cy, 2))

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "points": self.points,
            "level_id": self.level_id,
            "area_m2": self.area_m2,
            "floor_color": self.floor_color,
            "ceiling_color": self.ceiling_color,
            "centroid": self.centroid,
            "extra_attribs": self.extra_attribs,
            "extra_children": self.extra_children,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            room_id=d.get("id"),
            name=d.get("name", "Unnamed Room"),
            points=d.get("points", []),
            level_id=d.get("level_id", "level1"),
            floor_color=d.get("floor_color", "#E8E8E8"),
            ceiling_color=d.get("ceiling_color", "#FFFFFF"),
            extra_attribs=d.get("extra_attribs"),
            extra_children=d.get("extra_children"),
        )

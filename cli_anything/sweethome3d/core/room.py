from cli_anything.sweethome3d.core.exceptions import ValidationRejectedError
class Room:
    """Represents an enclosed architectural room polygon in Sweet Home 3D."""

    def __init__(self, room_id, name, points, level_id="level1", floor_color="#E8E8E8", ceiling_color="#FFFFFF",
                 extra_attribs=None, extra_children=None, holes=None):
        self.id = str(room_id)
        self.name = str(name)
        # points must be a list of (x, y) tuples/lists in cm
        self.points = [(float(p[0]), float(p[1])) for p in points]
        self.holes = [
            [(float(p[0]), float(p[1])) for p in h]
            for h in holes
        ] if holes else None
        self.level_id = str(level_id)
        self.floor_color = str(floor_color)
        self.ceiling_color = str(ceiling_color)
        self.extra_attribs = dict(extra_attribs) if extra_attribs else {}
        self.extra_children = list(extra_children) if extra_children else []

    @property
    def area_cm2(self):
        """Calculates polygon area in square centimeters using the Shoelace formula minus holes (PAC-1 T07, R18-P1-01)."""
        def _poly_area(pts):
            n = len(pts)
            if n < 3:
                return 0.0
            area = 0.0
            for i in range(n):
                j = (i + 1) % n
                area += pts[i][0] * pts[j][1] - pts[j][0] * pts[i][1]
            return abs(area) / 2.0

        shell_area = _poly_area(self.points)
        hole_area = sum(_poly_area(h) for h in self.holes) if self.holes else 0.0
        return max(0.0, shell_area - hole_area)

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

    def get_region_polygon(self):
        """Constructs and strictly validates the 2D planar polygon region of the room (PAC-1 C01, T07, A07, R20-P1-02)."""
        import math
        from shapely.geometry import Polygon
        from shapely.validation import explain_validity

        rings = [self.points, *(self.holes or [])]
        for index, ring in enumerate(rings):
            if len(ring) < 3 or len(set(map(tuple, ring))) < 3:
                raise ValidationRejectedError(f"Room {self.id} ring {index} requires three distinct vertices")
            if not all(math.isfinite(x) and math.isfinite(y) for x, y in ring):
                raise ValidationRejectedError(f"Room {self.id} ring {index} has nonfinite coordinates")
        region = Polygon(self.points, self.holes or [])
        if region.is_empty or not region.is_valid:
            raise ValidationRejectedError(f"Invalid room {self.id} polygon: {explain_validity(region)}")
        return region

    def get_clear_core_polygon(self, walls=None):
        """Derives the clear core polygon bounded by actual incident walls (PAC-1 T07, R10-P1-01, R11-P1-01, R20-P1-02)."""
        try:
            from shapely.geometry import Polygon, LineString
            from shapely.ops import unary_union
        except ImportError:
            return None
        
        try:
            poly = self.get_region_polygon()
        except ValueError:
            return None

        # Find incident walls on the exact same level
        incident_wall_polys = []
        if walls:
            wall_list = walls.values() if isinstance(walls, dict) else walls
            for w in wall_list:
                if getattr(w, "level_id", None) != self.level_id:
                    continue
                t = w.thickness / 2.0
                if abs(getattr(w, "arc_extent", 0.0)) > 1e-4 and w.length > 0:
                    steps = w.get_curve_sampling_steps()
                    pts = [w.point_at_offset(i * w.length / steps) for i in range(steps + 1)]
                    w_poly = LineString(pts).buffer(t, cap_style="square")
                else:
                    if w.chord_length < 1e-4:
                        continue
                    w_poly = LineString([(w.x_start, w.y_start), (w.x_end, w.y_end)]).buffer(t, cap_style="square")

                # R12-P1-01: True physical material intersection (subtraction of actual intruding solids)
                if poly.intersects(w_poly):
                    incident_wall_polys.append(w_poly)

        if not incident_wall_polys:
            return poly

        u_incident = unary_union(incident_wall_polys)
        clear_poly = poly.difference(u_incident)
        if clear_poly.is_empty or clear_poly.area < 1e-4:
            # Infeasible clear core (walls consume entire room area)
            return Polygon()
        return clear_poly

    def to_dict(self):
        d = {
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
        if self.holes:
            d["holes"] = self.holes
        return d

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
            holes=d.get("holes"),
        )

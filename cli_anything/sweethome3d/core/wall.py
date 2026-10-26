import math

class Wall:
    """Represents an architectural wall in Sweet Home 3D (coordinates in centimeters).
    
    Supports straight walls and curved/arc walls via `arc_extent` (in degrees).
    """

    def __init__(self, wall_id, x_start, y_start, x_end, y_end, thickness=15.0, height=280.0,
                 level_id="level1", arc_extent=0.0, extra_attribs=None, extra_children=None):
        self.id = str(wall_id)
        self.x_start = float(x_start)
        self.y_start = float(y_start)
        self.x_end = float(x_end)
        self.y_end = float(y_end)
        self.thickness = float(thickness)
        self.height = float(height)
        self.level_id = str(level_id)
        self.arc_extent = float(arc_extent)  # degrees; 0.0 = straight wall
        self.extra_attribs = dict(extra_attribs) if extra_attribs else {}
        self.extra_children = list(extra_children) if extra_children else []

    @property
    def x1(self):
        return self.x_start

    @property
    def y1(self):
        return self.y_start

    @property
    def x2(self):
        return self.x_end

    @property
    def y2(self):
        return self.y_end

    @property
    def chord_length(self):
        """Direct line distance between wall endpoints in centimeters."""
        if not math.isfinite(self.x_start) or not math.isfinite(self.y_start) or not math.isfinite(self.x_end) or not math.isfinite(self.y_end):
            return 0.0
        return math.hypot(self.x_end - self.x_start, self.y_end - self.y_start)

    @property
    def length(self):
        """Actual length of wall along its path in centimeters."""
        c = self.chord_length
        if not math.isfinite(self.arc_extent) or abs(self.arc_extent) < 1e-4:
            return c
        # Arc length: L = r * theta_rad, where chord c = 2 * r * sin(theta_rad / 2)
        theta_rad = abs(math.radians(self.arc_extent))
        if theta_rad >= 2.0 * math.pi:
            return c
        sin_half = math.sin(theta_rad / 2.0)
        if abs(sin_half) < 1e-6:
            return c
        r = c / (2.0 * sin_half)
        return r * theta_rad

    @property
    def length_m(self):
        """Length of wall in meters."""
        return round(self.length / 100.0, 2)

    @property
    def arc_length(self):
        """Alias for actual wall length along path (arc length for curved walls)."""
        return self.length

    @property
    def arc_length_m(self):
        """Alias for actual wall length in meters."""
        return self.length_m

    @property
    def angle_rad(self):
        """Chord angle in radians relative to horizontal X axis."""
        return math.atan2(self.y_end - self.y_start, self.x_end - self.x_start)

    @property
    def angle_deg(self):
        """Chord angle in degrees [0, 360)."""
        deg = math.degrees(self.angle_rad) % 360.0
        return round(deg, 1)

    def point_at_offset(self, offset_cm):
        """Returns (x, y) along the wall center line at a given offset from start.
        
        For curved walls with `arc_extent != 0`, computes exact points along circular arc.
        """
        l = self.length
        if l == 0:
            return (self.x_start, self.y_start)
        t = max(0.0, min(1.0, offset_cm / l))
        
        if not math.isfinite(self.arc_extent) or abs(self.arc_extent) < 1e-4 or abs(self.arc_extent) >= 360.0:
            x = self.x_start + t * (self.x_end - self.x_start)
            y = self.y_start + t * (self.y_end - self.y_start)
            return (round(x, 2), round(y, 2))

        # Circular arc interpolation
        c = self.chord_length
        if c < 1e-6:
            return (self.x_start, self.y_start)
        
        theta_rad = math.radians(self.arc_extent)
        alpha = abs(theta_rad) / 2.0
        sin_alpha = math.sin(alpha)
        if abs(sin_alpha) < 1e-6:
            x = self.x_start + t * (self.x_end - self.x_start)
            y = self.y_start + t * (self.y_end - self.y_start)
            return (round(x, 2), round(y, 2))

        r = c / (2.0 * sin_alpha)
        h = r * math.cos(alpha)

        # Chord midpoint and unit normal vector
        mx = (self.x_start + self.x_end) / 2.0
        my = (self.y_start + self.y_end) / 2.0
        dx = self.x_end - self.x_start
        dy = self.y_end - self.y_start
        nx = -dy / c
        ny = dx / c

        # Center of curvature: C = M - sign(arc_extent) * h * N
        sign = 1.0 if self.arc_extent > 0 else -1.0
        cx = mx - sign * h * nx
        cy = my - sign * h * ny

        phi1 = math.atan2(self.y_start - cy, self.x_start - cx)
        # Direct signed angular sweep across full arc extent without minor-interval collapse
        phi_t = phi1 - t * theta_rad
        x = cx + r * math.cos(phi_t)
        y = cy + r * math.sin(phi_t)
        return (round(x, 2), round(y, 2))

    def get_arc_extrema(self):
        """Returns list of exact (x, y) coordinates for all cardinal extrema reached along curved arc."""
        if not math.isfinite(self.arc_extent) or abs(self.arc_extent) < 1e-4 or abs(self.arc_extent) >= 360.0:
            return []
        c = self.chord_length
        if c < 1e-6:
            return []
        theta_rad = math.radians(self.arc_extent)
        alpha = abs(theta_rad) / 2.0
        sin_alpha = math.sin(alpha)
        if abs(sin_alpha) < 1e-6:
            return []
        r = c / (2.0 * sin_alpha)
        h = r * math.cos(alpha)
        mx = (self.x_start + self.x_end) / 2.0
        my = (self.y_start + self.y_end) / 2.0
        nx = -(self.y_end - self.y_start) / c
        ny = (self.x_end - self.x_start) / c
        sign = 1.0 if self.arc_extent > 0 else -1.0
        cx = mx - sign * h * nx
        cy = my - sign * h * ny
        phi1 = math.atan2(self.y_start - cy, self.x_start - cx)
        phi2 = phi1 - theta_rad
        min_phi = min(phi1, phi2)
        max_phi = max(phi1, phi2)

        extrema = []
        k_min = math.ceil(min_phi / (math.pi / 2.0) - 1e-9)
        k_max = math.floor(max_phi / (math.pi / 2.0) + 1e-9)
        for k in range(k_min, k_max + 1):
            angle = k * (math.pi / 2.0)
            ex = cx + r * math.cos(angle)
            ey = cy + r * math.sin(angle)
            extrema.append((ex, ey))
        return extrema

    def get_curve_sampling_steps(self, error_tol=None):
        """Computes scale-bounded curve sampling steps based on chord error (PAC-1 R10-P1-03).
        
        Guarantees that chord sagitta deviation remains bounded by error_tol or half-thickness,
        preventing scale-dependent collision misses.
        """
        if not math.isfinite(self.arc_extent) or abs(self.arc_extent) < 1e-4 or self.length <= 0:
            return 1
        theta_rad = abs(math.radians(self.arc_extent))
        sin_half = math.sin(theta_rad / 2.0)
        c = self.chord_length
        R = c / (2.0 * sin_half) if abs(sin_half) > 1e-6 else c
        if not math.isfinite(R) or R < 1e-4:
            return 16
        if error_tol is None:
            e = min(1.0, max(0.1, self.thickness / 4.0))
        else:
            e = max(0.01, float(error_tol))
        ratio = max(-1.0, min(1.0, 1.0 - (e / R)))
        d_theta = 2.0 * math.acos(ratio)
        if d_theta < 1e-6:
            return 16
        steps = int(math.ceil(theta_rad / d_theta))
        return min(1024, max(16, steps))

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
            "arc_extent": self.arc_extent,
            "length_m": self.length_m,
            "angle_deg": self.angle_deg,
            "extra_attribs": self.extra_attribs,
            "extra_children": self.extra_children,
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
            arc_extent=d.get("arc_extent", 0.0),
            extra_attribs=d.get("extra_attribs"),
            extra_children=d.get("extra_children"),
        )

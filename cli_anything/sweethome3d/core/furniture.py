FURNITURE_CATALOG = {
    "bed_king": {"name": "King Bed", "width": 200.0, "depth": 200.0, "height": 90.0, "category": "bedroom"},
    "bed_queen": {"name": "Queen Bed", "width": 160.0, "depth": 200.0, "height": 90.0, "category": "bedroom"},
    "bed_single": {"name": "Single Bed", "width": 100.0, "depth": 200.0, "height": 80.0, "category": "bedroom"},
    "sofa_3seat": {"name": "3-Seat Sofa", "width": 220.0, "depth": 90.0, "height": 85.0, "category": "living"},
    "sofa_2seat": {"name": "2-Seat Sofa", "width": 160.0, "depth": 90.0, "height": 85.0, "category": "living"},
    "armchair": {"name": "Armchair", "width": 90.0, "depth": 85.0, "height": 85.0, "category": "living"},
    "coffee_table": {"name": "Coffee Table", "width": 120.0, "depth": 60.0, "height": 45.0, "category": "living"},
    "dining_table_6": {"name": "6-Person Dining Table", "width": 180.0, "depth": 90.0, "height": 75.0, "category": "dining"},
    "dining_table_4": {"name": "4-Person Dining Table", "width": 120.0, "depth": 80.0, "height": 75.0, "category": "dining"},
    "kitchen_counter": {"name": "Kitchen Base Counter", "width": 240.0, "depth": 60.0, "height": 90.0, "category": "kitchen"},
    "kitchen_counter_sink": {"name": "Kitchen Sink Counter", "width": 120.0, "depth": 60.0, "height": 85.0, "category": "kitchen"},
    "kitchen_counter_stove": {"name": "Kitchen Stove Counter", "width": 90.0, "depth": 60.0, "height": 85.0, "category": "kitchen"},
    "refrigerator": {"name": "Refrigerator", "width": 80.0, "depth": 75.0, "height": 185.0, "category": "kitchen"},
    "kitchen_sink": {"name": "Kitchen Double Sink", "width": 90.0, "depth": 50.0, "height": 85.0, "category": "kitchen"},
    "toilet": {"name": "Water Closet Toilet", "width": 45.0, "depth": 70.0, "height": 80.0, "category": "bathroom"},
    "toilet_wc": {"name": "Water Closet Toilet", "width": 45.0, "depth": 70.0, "height": 80.0, "category": "bathroom"},
    "shower_tray": {"name": "Shower Tray Enclosure", "width": 100.0, "depth": 100.0, "height": 200.0, "category": "bathroom"},
    "shower_cabin": {"name": "Shower Cabin", "width": 90.0, "depth": 90.0, "height": 200.0, "category": "bathroom"},
    "bathroom_vanity": {"name": "Bathroom Vanity Sink", "width": 90.0, "depth": 50.0, "height": 85.0, "category": "bathroom"},
    "office_desk": {"name": "Executive Office Desk", "width": 160.0, "depth": 80.0, "height": 75.0, "category": "office"},
    "desk_office": {"name": "Executive Office Desk", "width": 140.0, "depth": 70.0, "height": 75.0, "category": "office"},
    "wardrobe": {"name": "Built-in Wardrobe", "width": 200.0, "depth": 60.0, "height": 240.0, "category": "bedroom"},
    "wardrobe_2door": {"name": "2-Door Wardrobe", "width": 120.0, "depth": 60.0, "height": 210.0, "category": "bedroom"},
    "wardrobe_3door": {"name": "3-Door Wardrobe", "width": 180.0, "depth": 60.0, "height": 210.0, "category": "bedroom"},
    "car_sedan": {"name": "Sedan Car", "width": 180.0, "depth": 450.0, "height": 145.0, "category": "garage"},
}

class Furniture:
    """Represents a piece of furniture in Sweet Home 3D."""

    def __init__(self, furniture_id, name, catalog_id="custom", x_cm=0.0, y_cm=0.0, elevation_cm=0.0,
                 width_cm=None, depth_cm=None, height_cm=None, angle_deg=0.0, level_id="level1",
                 extra_attribs=None, extra_children=None):
        self.id = str(furniture_id)
        self.catalog_id = str(catalog_id)
        self.extra_attribs = dict(extra_attribs) if extra_attribs else {}
        self.extra_children = list(extra_children) if extra_children else []
        
        # Check catalog presets for default dimensions
        preset = FURNITURE_CATALOG.get(self.catalog_id)
        self.name = str(name) if name else (preset["name"] if preset else "Furniture")

        if width_cm is not None:
            self.width_cm = float(width_cm)
        elif preset:
            self.width_cm = float(preset["width"])
        else:
            self.width_cm = 100.0

        if depth_cm is not None:
            self.depth_cm = float(depth_cm)
        elif preset:
            self.depth_cm = float(preset["depth"])
        else:
            self.depth_cm = 100.0

        if height_cm is not None:
            self.height_cm = float(height_cm)
        elif preset:
            self.height_cm = float(preset["height"])
        else:
            self.height_cm = 80.0

        self.x_cm = float(x_cm)
        self.y_cm = float(y_cm)
        self.elevation_cm = float(elevation_cm)
        self.angle_deg = float(angle_deg) % 360.0
        self.level_id = str(level_id)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "catalog_id": self.catalog_id,
            "x_cm": self.x_cm,
            "y_cm": self.y_cm,
            "elevation_cm": self.elevation_cm,
            "width_cm": self.width_cm,
            "depth_cm": self.depth_cm,
            "height_cm": self.height_cm,
            "angle_deg": self.angle_deg,
            "level_id": self.level_id,
            "extra_attribs": self.extra_attribs,
            "extra_children": self.extra_children,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            furniture_id=d.get("id"),
            name=d.get("name"),
            catalog_id=d.get("catalog_id", "custom"),
            x_cm=d.get("x_cm", 0.0),
            y_cm=d.get("y_cm", 0.0),
            elevation_cm=d.get("elevation_cm", 0.0),
            width_cm=d.get("width_cm"),
            depth_cm=d.get("depth_cm"),
            height_cm=d.get("height_cm"),
            angle_deg=d.get("angle_deg", 0.0),
            level_id=d.get("level_id", "level1"),
            extra_attribs=d.get("extra_attribs"),
            extra_children=d.get("extra_children"),
        )

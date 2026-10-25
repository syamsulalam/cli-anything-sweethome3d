class Opening:
    """Represents a door or window opening hosted on a wall in Sweet Home 3D."""

    def __init__(self, opening_id, name, wall_id, offset_cm=50.0, width_cm=90.0, height_cm=210.0,
                 opening_type="door", swing="right"):
        self.id = str(opening_id)
        self.name = str(name)
        self.wall_id = str(wall_id)
        self.offset_cm = float(offset_cm)
        self.width_cm = float(width_cm)
        self.height_cm = float(height_cm)
        self.opening_type = str(opening_type).lower()  # 'door' or 'window'
        self.swing = str(swing).lower()  # 'left', 'right', 'sliding', 'none'

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
        )

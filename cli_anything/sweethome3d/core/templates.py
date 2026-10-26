"""Architectural floor plan templates and preset typologies for Sweet Home 3D."""

def apply_template(model, template_type, name=None):
    """Populates a HomeModel with an architectural typology template.
    
    Supported templates:
      - 'type36': Indonesian Rumah Tipe 36 (6x6m = 36m², 2 Bed, 1 Bath, Living, Kitchen)
      - 'type45': Indonesian Rumah Tipe 45 (6x7.5m = 45m², 2 Bed, 1 Bath, Living/Dining, Kitchen)
      - 'studio': Compact Studio (5x6m = 30m², Bed/Living, Kitchenette, Bath)
      - 'pavilion': Executive Suite Pavilion (8x10m = 80m², Great Hall, Master Suite, Bath, Powder)
    """
    typ = template_type.lower().strip()
    if typ not in ["type36", "type45", "studio", "pavilion"]:
        raise ValueError(f"Unknown template type '{template_type}'. Available: type36, type45, studio, pavilion")

    # Reset existing entities
    model.walls.clear()
    model.rooms.clear()
    model.openings.clear()
    model.furniture.clear()
    model.dimensions.clear()

    default_names = {
        "type36": "Rumah Tipe 36",
        "type45": "Rumah Tipe 45",
        "studio": "Modern Studio 30m²",
        "pavilion": "Executive Pavilion Suite",
    }
    model.name = str(name) if name is not None else default_names.get(typ, "Standard Typology")

    if typ == "type36":
        _build_type36(model)
    elif typ == "type45":
        _build_type45(model)
    elif typ == "studio":
        _build_studio(model)
    elif typ == "pavilion":
        _build_pavilion(model)

    return model

def _build_type36(model):
    """Builds standard 6m x 6m = 36m² residential home."""
    # 1. Exterior Perimeter Walls (600 x 600 cm)
    ext_pts = [(0.0, 0.0), (600.0, 0.0), (600.0, 600.0), (0.0, 600.0)]
    model.add_wall_loop(ext_pts, prefix="w_ext")

    # 2. Internal Partition Walls
    # Central spine dividing left (living/service) from right (bedrooms) at x=300
    model.add_wall("w_int_spine", 300.0, 0.0, 300.0, 600.0, thickness=10.0)
    # Bedroom divider at y=300 from x=300 to x=600
    model.add_wall("w_int_bed", 300.0, 300.0, 600.0, 300.0, thickness=10.0)
    # Bathroom/Kitchen divider at y=450 from x=0 to x=300
    model.add_wall("w_int_bath", 0.0, 450.0, 300.0, 450.0, thickness=10.0)
    # Bath/Kitchen split wall at x=150 from y=450 to y=600
    model.add_wall("w_int_split", 150.0, 450.0, 150.0, 600.0, thickness=10.0)

    # 3. Rooms
    # Living & Dining: 300 x 450 cm = 13.5 m²
    model.add_room("r_living", "Ruang Tamu & Keluarga",
                   [(0.0, 0.0), (300.0, 0.0), (300.0, 450.0), (0.0, 450.0)],
                   floor_color="#F8F8F5")
    # Master Bedroom: 300 x 300 cm = 9.0 m²
    model.add_room("r_bed_master", "Kamar Tidur Utama",
                   [(300.0, 0.0), (600.0, 0.0), (600.0, 300.0), (300.0, 300.0)],
                   floor_color="#F2EFE9")
    # Kids Bedroom: 300 x 300 cm = 9.0 m²
    model.add_room("r_bed_kids", "Kamar Tidur Anak",
                   [(300.0, 300.0), (600.0, 300.0), (600.0, 600.0), (300.0, 600.0)],
                   floor_color="#EBF3F5")
    # Bathroom: 150 x 150 cm = 2.25 m²
    model.add_room("r_bath", "Kamar Mandi",
                   [(0.0, 450.0), (150.0, 450.0), (150.0, 600.0), (0.0, 600.0)],
                   floor_color="#D8E2DC")
    # Kitchen: 150 x 150 cm = 2.25 m²
    model.add_room("r_kitchen", "Dapur",
                   [(150.0, 450.0), (300.0, 450.0), (300.0, 600.0), (150.0, 600.0)],
                   floor_color="#ECE4DB")

    # 4. Doors & Windows
    # Front entrance door on w_ext_1 (0,0 -> 600,0) at x=100
    model.add_opening("d_main", "Pintu Utama", "w_ext_1", offset_cm=80.0, width_cm=90.0, opening_type="door")
    # Master bedroom door on spine wall
    model.add_opening("d_bed_master", "Pintu Kamar Utama", "w_int_spine", offset_cm=200.0, width_cm=80.0, opening_type="door")
    # Kids bedroom door on spine wall
    model.add_opening("d_bed_kids", "Pintu Kamar Anak", "w_int_spine", offset_cm=350.0, width_cm=80.0, opening_type="door")
    # Bathroom door on bath wall
    model.add_opening("d_bath", "Pintu KM", "w_int_bath", offset_cm=40.0, width_cm=65.0, opening_type="door")
    # Kitchen entrance door/opening on bath wall
    model.add_opening("d_kitchen", "Pintu Dapur", "w_int_bath", offset_cm=180.0, width_cm=80.0, opening_type="door")
    # Windows
    model.add_opening("win_living", "Jendela Ruang Tamu", "w_ext_1", offset_cm=200.0, width_cm=80.0, opening_type="window")
    model.add_opening("win_master", "Jendela Kamar Utama", "w_ext_1", offset_cm=400.0, width_cm=120.0, opening_type="window")
    model.add_opening("win_kids", "Jendela Kamar Anak", "w_ext_3", offset_cm=150.0, width_cm=120.0, opening_type="window")

    # 5. Core Furniture
    model.add_furniture("f_sofa", "Sofa Tamu", catalog_id="sofa_2seat", x_cm=150.0, y_cm=150.0)
    model.add_furniture("f_bed_master", "Kasur Utama", catalog_id="bed_queen", x_cm=450.0, y_cm=160.0)
    model.add_furniture("f_bed_kids", "Kasur Anak", catalog_id="bed_single", x_cm=450.0, y_cm=450.0)
    model.add_furniture("f_dining", "Meja Makan", catalog_id="dining_table_4", x_cm=150.0, y_cm=350.0)
    model.add_furniture("f_wc", "Kloset Duduk", catalog_id="toilet", x_cm=50.0, y_cm=557.5)

def _build_type45(model):
    """Builds standard 6m x 7.5m = 45m² residential home."""
    ext_pts = [(0.0, 0.0), (600.0, 0.0), (600.0, 750.0), (0.0, 750.0)]
    model.add_wall_loop(ext_pts, prefix="w_ext")

    model.add_wall("w_int_spine", 300.0, 0.0, 300.0, 750.0, thickness=10.0)
    model.add_wall("w_int_bed1", 300.0, 350.0, 600.0, 350.0, thickness=10.0)
    model.add_wall("w_int_bath", 0.0, 550.0, 300.0, 550.0, thickness=10.0)
    model.add_wall("w_int_split", 160.0, 550.0, 160.0, 750.0, thickness=10.0)

    model.add_room("r_living", "Ruang Tamu & Makan",
                   [(0.0, 0.0), (300.0, 0.0), (300.0, 550.0), (0.0, 550.0)], floor_color="#FAF8F5")
    model.add_room("r_bed1", "Kamar Tidur Utama",
                   [(300.0, 0.0), (600.0, 0.0), (600.0, 350.0), (300.0, 350.0)], floor_color="#F2ECE4")
    model.add_room("r_bed2", "Kamar Tidur 2",
                   [(300.0, 350.0), (600.0, 350.0), (600.0, 750.0), (300.0, 750.0)], floor_color="#EBF1F5")
    model.add_room("r_bath", "Kamar Mandi",
                   [(0.0, 550.0), (160.0, 550.0), (160.0, 750.0), (0.0, 750.0)], floor_color="#D8E2DC")
    model.add_room("r_kitchen", "Dapur",
                   [(160.0, 550.0), (300.0, 550.0), (300.0, 750.0), (160.0, 750.0)], floor_color="#EDE6DD")

    model.add_opening("d_main", "Pintu Utama", "w_ext_1", offset_cm=50.0, width_cm=90.0, opening_type="door")
    model.add_opening("d_bed1", "Pintu Kamar 1", "w_int_spine", offset_cm=200.0, width_cm=80.0, opening_type="door")
    model.add_opening("d_bed2", "Pintu Kamar 2", "w_int_spine", offset_cm=450.0, width_cm=80.0, opening_type="door")
    model.add_opening("d_bath", "Pintu KM", "w_int_bath", offset_cm=40.0, width_cm=70.0, opening_type="door")
    model.add_opening("d_kitchen", "Pintu Dapur", "w_int_bath", offset_cm=190.0, width_cm=80.0, opening_type="door")

    # Windows (pier between main door at 140cm and living window at 170cm)
    model.add_opening("win_living", "Jendela Ruang Tamu", "w_ext_1", offset_cm=170.0, width_cm=100.0, opening_type="window")
    model.add_opening("win_bed1", "Jendela Kamar 1", "w_ext_1", offset_cm=450.0, width_cm=120.0, opening_type="window")
    model.add_opening("win_bed2", "Jendela Kamar 2", "w_ext_3", offset_cm=150.0, width_cm=120.0, opening_type="window")

    model.add_furniture("f_sofa", "Sofa 3 Dudukan", catalog_id="sofa_3seat", x_cm=150.0, y_cm=150.0)
    model.add_furniture("f_bed1", "Kasur King", catalog_id="bed_king", x_cm=450.0, y_cm=180.0)
    model.add_furniture("f_bed2", "Kasur Queen", catalog_id="bed_queen", x_cm=450.0, y_cm=550.0)
    model.add_furniture("f_dining", "Meja Makan 4", catalog_id="dining_table_4", x_cm=150.0, y_cm=420.0)
    model.add_furniture("f_wc", "Kloset Duduk", catalog_id="toilet", x_cm=50.0, y_cm=700.0)

def _build_studio(model):
    """Builds 5m x 6m = 30m² open concept studio."""
    ext_pts = [(0.0, 0.0), (500.0, 0.0), (500.0, 600.0), (0.0, 600.0)]
    model.add_wall_loop(ext_pts, prefix="w_ext")

    # Bathroom corner (200 x 180 cm) at bottom right
    model.add_wall("w_bath_h", 300.0, 420.0, 500.0, 420.0, thickness=10.0)
    model.add_wall("w_bath_v", 300.0, 420.0, 300.0, 600.0, thickness=10.0)

    model.add_room("r_studio", "Studio Main Living & Bed",
                   [(0.0, 0.0), (500.0, 0.0), (500.0, 420.0), (300.0, 420.0), (300.0, 600.0), (0.0, 600.0)],
                   floor_color="#F4F4F0")
    model.add_room("r_bath", "Ensuite Bathroom",
                   [(300.0, 420.0), (500.0, 420.0), (500.0, 600.0), (300.0, 600.0)],
                   floor_color="#D8E2DC")

    model.add_opening("d_main", "Entrance Door", "w_ext_4", offset_cm=100.0, width_cm=90.0, opening_type="door")
    model.add_opening("d_bath", "Bathroom Door", "w_bath_v", offset_cm=50.0, width_cm=75.0, opening_type="door")
    model.add_opening("win_front", "Large Balcony Window", "w_ext_1", offset_cm=150.0, width_cm=200.0, opening_type="window")

    model.add_furniture("f_bed", "Queen Studio Bed", catalog_id="bed_queen", x_cm=380.0, y_cm=180.0)
    model.add_furniture("f_sofa", "Compact Sofa", catalog_id="sofa_2seat", x_cm=150.0, y_cm=200.0)
    model.add_furniture("f_desk", "Work Desk", catalog_id="desk_office", x_cm=80.0, y_cm=330.0)
    model.add_furniture("f_toilet", "Toilet Fixture", catalog_id="toilet", x_cm=420.0, y_cm=520.0)

def _build_pavilion(model):
    """Builds luxury 8m x 10m = 80m² Pavilion Suite."""
    ext_pts = [(0.0, 0.0), (800.0, 0.0), (800.0, 1000.0), (0.0, 1000.0)]
    model.add_wall_loop(ext_pts, prefix="w_ext")

    # Divider between Great Living Hall (left, 450cm) and Private Suite (right, 350cm)
    model.add_wall("w_div_main", 450.0, 0.0, 450.0, 1000.0, thickness=15.0)
    # Master Bedroom & Ensuite Bath divider at y=600
    model.add_wall("w_div_bath", 450.0, 600.0, 800.0, 600.0, thickness=12.0)
    # Powder room in living hall at bottom left (200 x 200 cm)
    model.add_wall("w_pwd_h", 0.0, 800.0, 200.0, 800.0, thickness=10.0)
    model.add_wall("w_pwd_v", 200.0, 800.0, 200.0, 1000.0, thickness=10.0)

    model.add_room("r_great_hall", "Great Living & Lounge",
                   [(0.0, 0.0), (450.0, 0.0), (450.0, 1000.0), (200.0, 1000.0), (200.0, 800.0), (0.0, 800.0)],
                   floor_color="#F7F5F0")
    model.add_room("r_master", "Master Bedroom Suite",
                   [(450.0, 0.0), (800.0, 0.0), (800.0, 600.0), (450.0, 600.0)],
                   floor_color="#EFEBE4")
    model.add_room("r_bath_ensuite", "Master Ensuite Bath",
                   [(450.0, 600.0), (800.0, 600.0), (800.0, 1000.0), (450.0, 1000.0)],
                   floor_color="#D5DFDC")
    model.add_room("r_powder", "Powder Room",
                   [(0.0, 800.0), (200.0, 800.0), (200.0, 1000.0), (0.0, 1000.0)],
                   floor_color="#DEE2E6")

    model.add_opening("d_entrance", "Grand Double Door", "w_ext_4", offset_cm=300.0, width_cm=160.0, opening_type="door")
    model.add_opening("d_suite", "Master Suite Entrance", "w_div_main", offset_cm=250.0, width_cm=90.0, opening_type="door")
    model.add_opening("d_ensuite", "Ensuite Bath Door", "w_div_bath", offset_cm=120.0, width_cm=85.0, opening_type="door")
    model.add_opening("d_powder", "Powder Room Door", "w_pwd_h", offset_cm=60.0, width_cm=75.0, opening_type="door")
    model.add_opening("win_great", "Great Room Window", "w_ext_1", offset_cm=150.0, width_cm=200.0, opening_type="window")
    model.add_opening("win_master", "Master Suite Window", "w_ext_1", offset_cm=550.0, width_cm=180.0, opening_type="window")

    model.add_furniture("f_sofa_main", "Lounge Sectional", catalog_id="sofa_3seat", x_cm=220.0, y_cm=350.0)
    model.add_furniture("f_bed_king", "Emperor Bed", catalog_id="bed_king", x_cm=625.0, y_cm=300.0)
    model.add_furniture("f_dining", "Dining Set 6", catalog_id="dining_table_6", x_cm=280.0, y_cm=650.0)
    model.add_furniture("f_wardrobe", "Walk-in Wardrobe", catalog_id="wardrobe_3door", x_cm=700.0, y_cm=120.0)

"""Turn a spec into Blender geometry. Everything is real-world metres, Z up,
+Y = north. Each object gets custom props: role, level (used by drawings/exports)."""
import math
import bpy
from mathutils import Vector

from . import materials as M
from . import spec as S

# Workbench drawing colours (plans/elevations) per role
DRAW = {
    "wall_ext": (0.08, 0.08, 0.08, 1), "wall_int": (0.15, 0.15, 0.15, 1),
    "slab": (0.80, 0.80, 0.80, 1), "floor": (0.96, 0.96, 0.94, 1),
    "frame": (0.35, 0.35, 0.35, 1), "glass": (0.70, 0.85, 0.95, 1),
    "door": (0.65, 0.50, 0.35, 1), "stair": (0.75, 0.75, 0.75, 1),
    "roof": (0.55, 0.55, 0.55, 1), "ground": (1, 1, 1, 1),
    "element": (0.85, 0.85, 0.85, 1), "label": (0, 0, 0, 1),
}


# ---------------------------------------------------------------- scene setup
def reset_scene():
    if bpy.app.background:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    else:  # inside the GUI a factory reset would kill the running script: wipe data instead
        for data in (bpy.data.objects, bpy.data.meshes, bpy.data.curves, bpy.data.materials,
                     bpy.data.lights, bpy.data.cameras, bpy.data.collections, bpy.data.worlds):
            for item in list(data):
                data.remove(item)
    sc = bpy.context.scene
    us = sc.unit_settings
    us.system, us.scale_length, us.length_unit = "METRIC", 1.0, "METERS"
    return sc


def coll(name, parent=None):
    c = bpy.data.collections.get(name)
    if c is None:
        c = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(c)
    return c


def _tag(ob, role, level):
    ob["role"] = role
    ob["level"] = level
    ob.color = DRAW.get(role, (0.8, 0.8, 0.8, 1))
    return ob


def mesh_obj(name, verts, faces, collection, mat=None, role="element", level="SITE"):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], faces)
    me.validate()
    me.update()
    ob = bpy.data.objects.new(name, me)
    collection.objects.link(ob)
    if mat:
        me.materials.append(mat)
    return _tag(ob, role, level)


BOX_FACES = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def box(name, size, center, rot_z, collection, mat=None, role="element", level="SITE"):
    sx, sy, sz = (s / 2 for s in size)
    v = [(-sx, -sy, -sz), (sx, -sy, -sz), (sx, sy, -sz), (-sx, sy, -sz),
         (-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz)]
    ob = mesh_obj(name, v, BOX_FACES, collection, mat, role, level)
    ob.location = center
    ob.rotation_euler = (0, 0, rot_z)
    return ob


def prism(name, poly, z0, z1, collection, mat=None, role="element", level="SITE"):
    """Vertical extrusion of a 2D polygon between z0 and z1."""
    if S.signed_area(poly) < 0:
        poly = list(reversed(poly))
    n = len(poly)
    verts = [(x, y, z0) for x, y in poly] + [(x, y, z1) for x, y in poly]
    faces = [tuple(range(n - 1, -1, -1)), tuple(range(n, 2 * n))]
    faces += [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    return mesh_obj(name, verts, faces, collection, mat, role, level)


def boolean_cut(ob, cutter_coll, name="Cut"):
    m = ob.modifiers.new(name, "BOOLEAN")
    m.operation = "DIFFERENCE"
    m.operand_type = "COLLECTION"
    m.collection = cutter_coll
    m.solver = "EXACT"
    return m


def hide_cutter(ob):
    ob.hide_render = True
    ob.display_type = "WIRE"
    ob["role"] = "cutter"


# ---------------------------------------------------------------- walls & openings
def _seg_frame(wall, seg_index):
    a, b = S.segments(wall)[seg_index]
    a, b = Vector((*a, 0)), Vector((*b, 0))
    d = (b - a).normalized()
    left = Vector((-d.y, d.x, 0))
    t = wall["thickness"]
    side = {"left": t / 2, "right": -t / 2, "center": 0.0}[wall["align"]]
    return a, d, left, side, math.atan2(d.y, d.x)


def build_wall(spec, lv, wall, colls, quality):
    p = wall["path"]
    n = len(p)
    z0 = lv["elevation"]
    z1 = z0 + wall["height"]
    verts = [(x, y, z0) for x, y in p] + [(x, y, z1) for x, y in p]
    rng = range(n) if wall["closed"] else range(n - 1)
    faces = [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in rng]
    mat = M.by_name(wall["material"]) if wall.get("material") in M.LIBRARY else M.for_role(spec, wall["role"])
    ob = mesh_obj(f"{lv['name']}_{wall['id']}", verts, faces, colls["walls"], mat, wall["role"], lv["name"])
    sol = ob.modifiers.new("Thickness", "SOLIDIFY")
    sol.thickness = wall["thickness"]
    # normal of the ribbon points to the RIGHT of travel; solidify offset -1 grows left
    sol.offset = {"left": -1.0, "right": 1.0, "center": 0.0}[wall["align"]]
    sol.use_even_offset = True
    sol.use_quality_normals = True
    return ob


def build_openings(spec, lv, walls_by_id, colls, quality):
    cutter_colls = {}
    for k, o in enumerate(lv["openings"]):
        wall = walls_by_id.get(o["wall"])
        if wall is None:
            continue
        ob_wall = wall["_ob"]
        if o["wall"] not in cutter_colls:
            c = coll(f"cut_{lv['name']}_{o['wall']}", colls["cutters"])
            cutter_colls[o["wall"]] = c
            boolean_cut(ob_wall, c, "Openings")
        a, d, left, side, rot = _seg_frame(wall, o["segment"])
        t = wall["thickness"]
        w, h, sill = o["width"], o["height"], o["sill"]
        cxy = a + d * (o["offset"] + w / 2) + left * side
        zc = lv["elevation"] + sill + h / 2
        center = Vector((cxy.x, cxy.y, zc))
        name = f"{lv['name']}_{o['kind']}{k}"
        cut = box(name + "_cut", (w, t + 0.4, h), center, rot, cutter_colls[o["wall"]],
                  role="cutter", level=lv["name"])
        hide_cutter(cut)
        if o["kind"] == "opening":
            continue
        _frame(spec, lv, o, name, center, d, left, rot, t, colls, wall["closed"])


def _frame(spec, lv, o, name, center, d, left, rot, t, colls, exterior=False):
    w, h = o["width"], o["height"]
    fp = o.get("frame", 0.06)                 # frame profile width
    fd = min(o.get("frame_depth", 0.10), t * 0.8)
    inset = o.get("inset", 0.0)               # +ve pushes toward the outside face
    base = center - left * inset
    fm = M.for_role(spec, "frame")

    def place(local_x, local_z, size, mat, role, suffix):
        loc = base + d * local_x + Vector((0, 0, local_z))
        return box(f"{name}_{suffix}", size, loc, rot, colls["openings"], mat, role, lv["name"])

    place(-w / 2 + fp / 2, 0, (fp, fd, h), fm, "frame", "jambL")
    place(w / 2 - fp / 2, 0, (fp, fd, h), fm, "frame", "jambR")
    place(0, h / 2 - fp / 2, (w - 2 * fp, fd, fp), fm, "frame", "head")
    if o["kind"] == "window":
        place(0, -h / 2 + fp / 2, (w - 2 * fp, fd, fp), fm, "frame", "sill")
        # optional vertical mullions: "mullions": 2 splits into 3 panes
        nm = int(o.get("mullions", 0 if w < 2.0 else round(w / 1.6) - 1))
        for i in range(nm):
            x = -w / 2 + fp + (i + 1) * (w - 2 * fp) / (nm + 1)
            place(x, 0, (fp * 0.8, fd * 0.9, h - 2 * fp), fm, "frame", f"mull{i}")
        g = place(0, 0, (w - 2 * fp, 0.012, h - 2 * fp), M.for_role(spec, "glass"), "glass", "glass")
        g["exterior"] = exterior
    else:  # door
        leaf_h = h - fp
        g = o.get("glazed", False)
        mat = M.for_role(spec, "glass" if g else "door")
        leaf = place(0, -fp / 2, (w - 2 * fp, 0.045, leaf_h), mat, "glass" if g else "door", "leaf")
        leaf["exterior"] = exterior and g


# ---------------------------------------------------------------- slabs, rooms, stairs
def build_slab(spec, lv, colls, top_z=None, role="slab", name=None, thickness=None, poly=None):
    poly = poly or S.outline(lv)
    if not poly:
        return None
    if role == "slab":
        ext = next((w for w in lv["walls"] if w["closed"]), None)
        if ext:
            poly = S.inset(poly, ext["thickness"] / 2)
    top = lv["elevation"] if top_z is None else top_z
    th = lv["slab"] if thickness is None else thickness
    ob = prism(name or f"{lv['name']}_slab", poly, top - th, top, colls["slabs"],
               M.for_role(spec, role), role, lv["name"])
    if lv["slab_openings"] and role == "slab":
        c = coll(f"cut_{lv['name']}_slab", colls["cutters"])
        for i, hole in enumerate(lv["slab_openings"]):
            cut = prism(f"{lv['name']}_void{i}", hole, top - th - 0.2, top + 0.2, c,
                        role="cutter", level=lv["name"])
            hide_cutter(cut)
        boolean_cut(ob, c, "Voids")
        lv["_void_cut"] = c
    return ob


def build_rooms(spec, lv, colls):
    """Thin floor finish per room + a plan label (only visible in plan drawings)."""
    for r in lv["rooms"]:
        role = "floor"
        mat = M.for_role(spec, r.get("material_role", "floor")) if not r.get("material") \
            else M.by_name(r["material"])
        z = lv["elevation"]
        fl = prism(f"{lv['name']}_floor_{r['name']}", r["polygon"], z, z + 0.015,
                   colls["slabs"], mat, role, lv["name"])
        if lv.get("_void_cut"):
            boolean_cut(fl, lv["_void_cut"], "Voids")
        # plaster ceiling under the slab / flat roof above
        i = spec["levels"].index(lv)
        above = spec["levels"][i + 1] if i + 1 < len(spec["levels"]) else None
        zc = above["elevation"] - above["slab"] if above else \
            (lv["elevation"] + lv["height"] if spec["roof"].get("type", "flat") == "flat" else None)
        if zc is not None and r.get("ceiling", True):
            ce = prism(f"{lv['name']}_ceiling_{r['name']}", r["polygon"], zc - 0.012, zc - 0.001,
                       colls["slabs"], M.for_role(spec, "ceiling"), "ceiling", lv["name"])
            if above and above["slab_openings"]:      # keep the stair void open
                c = bpy.data.collections.get(f"cut_{above['name']}_ceiling")
                if c is None:
                    c = coll(f"cut_{above['name']}_ceiling", colls["cutters"])
                    for k, hole in enumerate(above["slab_openings"]):
                        hide_cutter(prism(f"{above['name']}_cvoid{k}", hole, zc - 0.2, zc + 0.2, c,
                                          role="cutter", level=above["name"]))
                boolean_cut(ce, c, "Voids")
        xs = [p[0] for p in r["polygon"]]
        ys = [p[1] for p in r["polygon"]]
        a = S.area(r["polygon"])
        cu = bpy.data.curves.new(f"label_{r['name']}", "FONT")
        cu.body = f"{r['name']}\n{a:.1f} m²"
        cu.align_x, cu.align_y = "CENTER", "CENTER"
        cu.size = r.get("label_size", 0.32)
        ob = bpy.data.objects.new(f"{lv['name']}_label_{r['name']}", cu)
        ob.location = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, z + 0.05)
        colls["labels"].objects.link(ob)
        _tag(ob, "label", lv["name"])
        ob.hide_render = True


def build_stairs(spec, lv, colls):
    for k, s in enumerate(lv["stairs"]):
        rise = s.get("rise", lv["height"])
        n = max(1, round(rise / s.get("riser", 0.175)))
        r = rise / n
        t = s.get("tread", 0.28)
        wdt = s.get("width", 1.0)
        ang = math.radians(s.get("direction", 0.0))  # 0 = +X, 90 = +Y
        d = Vector((math.cos(ang), math.sin(ang), 0))
        left = Vector((-d.y, d.x, 0))
        start = Vector((*s["start"], 0))  # start = corner on the RIGHT edge of the first step
        mat = M.for_role(spec, "stair")
        # one solid flight: stepped side profile (along, up) extruded across the width
        prof = [(0.0, 0.0)]
        for i in range(n):
            prof += [(i * t, (i + 1) * r), ((i + 1) * t, (i + 1) * r)]
        prof.append((n * t, 0.0))
        z0 = lv["elevation"]
        P = lambda a, y, z: tuple(start + d * a + left * y)[:2] + (z0 + z,)
        m = len(prof)
        verts = [P(a, 0.0, z) for a, z in prof] + [P(a, wdt, z) for a, z in prof]
        faces = [tuple(range(m)), tuple(range(2 * m - 1, m - 1, -1))]
        faces += [(k, (k + 1) % m, m + (k + 1) % m, m + k) for k in range(m)]
        ob = mesh_obj(f"{lv['name']}_stair{k}", verts, faces, colls["stairs"], mat, "stair", lv["name"])
        import bmesh
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(ob.data)
        bm.free()


# ---------------------------------------------------------------- roof
def build_roof(spec, colls):
    r = spec["roof"]
    lv = spec["levels"][-1]
    top = lv["elevation"] + lv["height"]
    fp = S.outline(lv)
    if not fp:
        return
    kind = r.get("type", "flat")
    th = r.get("thickness", lv["slab"])
    if kind == "flat":
        ov = r.get("overhang", 0.0)
        poly = fp
        if ov:
            xs, ys = [p[0] for p in fp], [p[1] for p in fp]
            poly = [[min(xs) - ov, min(ys) - ov], [max(xs) + ov, min(ys) - ov],
                    [max(xs) + ov, max(ys) + ov], [min(xs) - ov, max(ys) + ov]]
        par = r.get("parapet", 0.0)
        if par and not ov:
            ext0 = next(w for w in lv["walls"] if w["closed"])
            poly = S.inset(poly, ext0["thickness"] / 2)
        prism("roof_slab", poly, top, top + th, colls["roof"], M.for_role(spec, "roof"), "roof", "ROOF")
        if par:
            ext = next(w for w in lv["walls"] if w["closed"])
            pw = dict(ext, id="parapet", height=th + par)
            plv = dict(lv, elevation=top, name="ROOF")
            ob = build_wall(spec, plv, pw, colls, None)
            ob.users_collection[0].objects.unlink(ob)
            colls["roof"].objects.link(ob)
            cap = r.get("coping", True)
            if cap:  # thin metal coping on parapet
                ob2 = build_wall(spec, dict(plv, elevation=top + th + par),
                                 dict(pw, id="coping", height=0.04, thickness=ext["thickness"] + 0.04,
                                      path=ext["path"]), colls, None)
                ob2.users_collection[0].objects.unlink(ob2)
                colls["roof"].objects.link(ob2)
                ob2.data.materials.clear()
                ob2.data.materials.append(M.for_role(spec, "frame"))
                ob2["role"] = "roof"
        return
    if kind in ("gable", "mono"):
        ov = r.get("overhang", 0.4)
        pitch = math.radians(r.get("pitch", 30))
        xs, ys = [p[0] for p in fp], [p[1] for p in fp]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        along_x = r.get("ridge", "x" if (x1 - x0) >= (y1 - y0) else "y") == "x"
        # work in (u = along ridge, v = across) then map back
        u0, u1, v0, v1 = (x0, x1, y0, y1) if along_x else (y0, y1, x0, x1)
        tan = math.tan(pitch)
        mp = lambda u, v, z: (u, v, z) if along_x else (v, u, z)
        mat = M.for_role(spec, "roof")
        if kind == "gable":
            vm = (v0 + v1) / 2
            zr = top + (vm - v0) * tan
            ze = top - ov * tan
            planes = [
                [mp(u0 - ov, v0 - ov, ze), mp(u1 + ov, v0 - ov, ze), mp(u1 + ov, vm, zr), mp(u0 - ov, vm, zr)],
                [mp(u1 + ov, v1 + ov, ze), mp(u0 - ov, v1 + ov, ze), mp(u0 - ov, vm, zr), mp(u1 + ov, vm, zr)],
            ]
            dz = th / math.cos(pitch)   # keep the gable infill just under the roof surface
            tri = [(v0, top - dz), (v1, top - dz), (vm, zr - dz)]
        else:  # mono-pitch rising toward v1
            zr = top + (v1 - v0) * tan
            ze = top - ov * tan
            planes = [[mp(u0 - ov, v0 - ov, ze), mp(u1 + ov, v0 - ov, ze),
                       mp(u1 + ov, v1 + ov, zr + ov * tan), mp(u0 - ov, v1 + ov, zr + ov * tan)]]
            dz = th / math.cos(pitch)
            tri = [(v0, top - dz), (v1, top - dz), (v1, zr - dz)]
        for i, pl in enumerate(planes):
            if not along_x:
                pl = list(reversed(pl))  # keep normals pointing up after the axis swap
            ob = mesh_obj(f"roof_plane{i}", pl, [(0, 1, 2, 3)], colls["roof"], mat, "roof", "ROOF")
            s = ob.modifiers.new("Thickness", "SOLIDIFY")
            s.thickness = th
            s.offset = -1.0   # grow downward so the two top surfaces meet cleanly at the ridge
        # gable-end infill walls
        ext = next(w for w in lv["walls"] if w["closed"])
        t = ext["thickness"]
        wm = M.for_role(spec, ext["role"])
        import bmesh
        for uu, sgn in ((u0, 1), (u1, -1)):
            verts = []
            for ua in (uu, uu + sgn * t):
                verts += [mp(ua, vv, zz) for vv, zz in tri]
            faces = [(0, 1, 2), (5, 4, 3), (0, 3, 4, 1), (1, 4, 5, 2), (2, 5, 3, 0)]
            ob = mesh_obj(f"gable_{'a' if sgn > 0 else 'b'}", verts, faces, colls["roof"], wm, ext["role"], "ROOF")
            # fix inconsistent winding after mirroring
            bm = bmesh.new()
            bm.from_mesh(ob.data)
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
            bm.to_mesh(ob.data)
            bm.free()


# ---------------------------------------------------------------- site & elements
def build_site(spec, colls):
    st = spec["site"]
    b = S.bounds(spec)
    m = st["margin"]
    z = st["ground_level"]
    poly = st.get("polygon") or [[b[0] - m, b[1] - m], [b[3] + m, b[1] - m],
                                  [b[3] + m, b[4] + m], [b[0] - m, b[4] + m]]
    prism("ground", poly, z - 0.3, z, colls["site"], M.for_role(spec, "ground"), "ground", "SITE")


def build_elements(spec, colls):
    for i, e in enumerate(spec["elements"]):
        role = e.get("role", "element")
        mat = M.by_name(e["material"]) if e.get("material") in M.LIBRARY else M.for_role(spec, role)
        name = e.get("name", f"element{i}")
        lvl = e.get("level", "SITE")
        if e["type"] == "box":
            box(name, e["size"], e["center"], math.radians(e.get("rotation", 0)),
                colls["elements"], mat, role, lvl)
        elif e["type"] == "prism":
            prism(name, e["polygon"], e["z"][0], e["z"][1], colls["elements"], mat, role, lvl)


# ---------------------------------------------------------------- context (trees)
def _tree(name, x, y, z, height, rng, colls, crown=None):
    """Simple procedural tree: tapered trunk + a crown of displaced leaf clusters."""
    import bmesh
    crown = crown or height * 0.36
    bark, leaf = M.by_name("bark"), M.by_name("foliage")
    trunk_h = height * 0.55
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=10, radius1=height * 0.022, radius2=height * 0.012,
                          depth=trunk_h)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, trunk_h / 2))
    me = bpy.data.meshes.new(name + "_trunk")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(bark)
    ob = bpy.data.objects.new(name + "_trunk", me)
    ob.location = (x, y, z)
    colls["context"].objects.link(ob)
    _tag(ob, "tree", "SITE")
    bm = bmesh.new()
    cz = height - crown * 0.95
    n = 9
    for i in range(n):
        a = rng.uniform(0, 6.283)
        rr = rng.uniform(0.0, crown * 0.55)
        r = crown * rng.uniform(0.42, 0.62)
        c = (rr * math.cos(a), rr * math.sin(a), cz + rng.uniform(-0.35, 0.45) * crown)
        res = bmesh.ops.create_icosphere(bm, subdivisions=3, radius=r)
        bmesh.ops.translate(bm, verts=res["verts"], vec=c)
    me = bpy.data.meshes.new(name + "_crown")
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(leaf)
    ob = bpy.data.objects.new(name + "_crown", me)
    ob.location = (x, y, z)
    colls["context"].objects.link(ob)
    tex = bpy.data.textures.get("crown_noise") or bpy.data.textures.new("crown_noise", "CLOUDS")
    tex.noise_scale = 0.55
    tex.noise_depth = 2
    dm = ob.modifiers.new("Leaves", "DISPLACE")
    dm.texture = tex
    dm.strength = crown * 0.35
    dm.mid_level = 0.5
    _tag(ob, "tree", "SITE")


def build_context(spec, colls):
    """Trees around the building: spec["context"] = {"trees": "auto" | [[x, y, height], ...]}
    or false to switch off. "auto" places a few behind and beside the hero view."""
    import random
    ctx = spec.get("context", {"trees": "auto"})
    if not ctx:
        return
    trees = ctx.get("trees", "auto")
    rng = random.Random(ctx.get("seed", 7))
    z = spec["site"]["ground_level"]
    if trees == "auto":
        b = S.bounds(spec, include_elements=True)
        cx, cy = (b[0] + b[3]) / 2, (b[1] + b[4]) / 2
        rad = math.hypot(b[3] - b[0], b[4] - b[1]) / 2
        hero = spec.get("hero_azimuth", 210)
        trees = []
        for k, off in enumerate((150, 180, 205, 120, 245, 95, 270)):   # behind/beside the hero camera
            az = math.radians(hero + off + rng.uniform(-8, 8))
            d = rad + rng.uniform(4.0, 9.0) + (k % 2) * 3.0
            trees.append([cx + math.sin(az) * d, cy + math.cos(az) * d, rng.uniform(7.0, 11.0)])
    for i, t in enumerate(trees):
        _tree(f"tree{i}", t[0], t[1], z, t[2] if len(t) > 2 else 8.0, rng, colls)


# ---------------------------------------------------------------- entry point
def build(spec, quality="draft", context=True):
    reset_scene()
    root = coll("Building")
    colls = {
        "walls": coll("Walls", root), "openings": coll("Openings", root),
        "slabs": coll("Slabs", root), "stairs": coll("Stairs", root),
        "roof": coll("Roof", root), "elements": coll("Elements", root),
        "site": coll("Site"), "labels": coll("Labels"), "cutters": coll("Cutters"),
        "context": coll("Context"),
    }
    for lv in spec["levels"]:
        by_id = {}
        for w in lv["walls"]:
            w["_ob"] = build_wall(spec, lv, w, colls, quality)
            by_id[w["id"]] = w
        build_openings(spec, lv, by_id, colls, quality)
        build_slab(spec, lv, colls)
        build_rooms(spec, lv, colls)
        build_stairs(spec, lv, colls)
    build_roof(spec, colls)
    build_elements(spec, colls)
    build_site(spec, colls)
    if context:
        build_context(spec, colls)
    if quality != "draft":  # soft edges catch light, cost little at this point
        for w in [w for lv in spec["levels"] for w in lv["walls"]]:
            bv = w["_ob"].modifiers.new("Bevel", "BEVEL")
            bv.width = 0.006
            bv.segments = 2
            bv.limit_method = "ANGLE"
    for lv in spec["levels"]:
        lv.pop("_void_cut", None)
        for w in lv["walls"]:
            w.pop("_ob", None)
    return colls

"""Exports: .blend, glTF (web / clients), FBX & OBJ (other DCC/CAD tools),
and STL scale models for 3D printing (mm, watertight via voxel remesh)."""
import os
import struct
import bpy
from mathutils import Vector

SKIP_ROLES = {"cutter", "label"}


def building_objects(include_site=False):
    out = []
    for ob in bpy.data.objects:
        role = ob.get("role")
        if ob.type != "MESH" or role in SKIP_ROLES or role is None:
            continue
        if role == "ground" and not include_site:
            continue
        out.append(ob)
    return out


def _select(objs):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    if objs:
        bpy.context.view_layer.objects.active = objs[0]


def save_blend(path):
    bpy.ops.wm.save_as_mainfile(filepath=path)
    return path


def gltf(path, include_site=True):
    _select(building_objects(include_site))
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_apply=True)
    return path


def fbx(path, include_site=False):
    _select(building_objects(include_site))
    bpy.ops.export_scene.fbx(filepath=path, use_selection=True, use_mesh_modifiers=True,
                             apply_unit_scale=True, bake_space_transform=True)
    return path


def obj(path, include_site=False):
    _select(building_objects(include_site))
    if hasattr(bpy.ops.wm, "obj_export"):
        bpy.ops.wm.obj_export(filepath=path, export_selected_objects=True, apply_modifiers=True)
    else:
        bpy.ops.export_scene.obj(filepath=path, use_selection=True, use_mesh_modifiers=True)
    return path


# ---------------------------------------------------------------- STL maquette
def _triangles(objs):
    dg = bpy.context.evaluated_depsgraph_get()
    tris = []
    for ob in objs:
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
        me.calc_loop_triangles()
        mw = ob.matrix_world
        vs = [mw @ v.co for v in me.vertices]
        tris += [tuple(vs[i] for i in t.vertices) for t in me.loop_triangles]
        ev.to_mesh_clear()
    return tris


def _mesh_from_tris(name, tris):
    verts, faces = [], []
    for t in tris:
        base = len(verts)
        verts += [tuple(v) for v in t]
        faces.append((base, base + 1, base + 2))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _write_stl(path, tris, scale):
    with open(path, "wb") as f:
        f.write(b"archkit scale model".ljust(80, b" "))
        f.write(struct.pack("<I", len(tris)))
        for a, b, c in tris:
            a, b, c = a * scale, b * scale, c * scale
            n = (b - a).cross(c - a)
            n = n.normalized() if n.length > 0 else Vector((0, 0, 1))
            f.write(struct.pack("<12fH", *n, *a, *b, *c, 0))


# For printing, glass / frames / door leaves go: openings print as clean holes.
PRINT_SKIP = {"glass", "frame", "door", "floor"}


def stl_model(out_dir, name, ratio=100, voxel=None, split_levels=False, base=False, max_tris=200_000,
              keep=("wall_ext", "wall_int", "slab", "stair", "roof", "element")):
    """Write STL(s) at 1:ratio in millimetres. Returns list of paths.

    voxel: remesh size in REAL metres (None = auto, 0 = no remesh). Remeshing merges
    overlapping parts into one watertight shell, which is what slicers want."""
    if voxel is None:
        voxel = max(0.025, 0.3 * ratio / 1000)   # ~0.3 mm at print scale
    scale = 1000.0 / ratio                        # metres -> mm at 1:ratio
    objs = [o for o in building_objects(include_site=base)
            if (o.get("role") in keep or (base and o.get("role") == "ground"))
            and o.get("role") not in PRINT_SKIP]
    groups = {}
    for o in objs:
        key = o.get("level", "SITE") if split_levels else "all"
        groups.setdefault(key, []).append(o)
    paths = []
    for key, group in groups.items():
        tris = _triangles(group)
        if not tris:
            continue
        if voxel:
            tmp = _mesh_from_tris(f"_print_{key}", tris)
            rm = tmp.modifiers.new("Remesh", "REMESH")
            rm.mode = "VOXEL"
            rm.voxel_size = voxel
            rm.adaptivity = 0.0
            tris = _triangles([tmp])
            if len(tris) > max_tris:   # voxel meshes are dense; flat walls need far fewer triangles
                dec = tmp.modifiers.new("Simplify", "DECIMATE")
                dec.decimate_type = "COLLAPSE"
                dec.ratio = max_tris / len(tris)
                tris = _triangles([tmp])
            bpy.data.objects.remove(tmp)
        suffix = "" if key == "all" else f"_{key}"
        p = os.path.join(out_dir, f"{name}_1-{ratio}{suffix}.stl")
        _write_stl(p, tris, scale)
        mn = Vector((min(v.x for t in tris for v in t), min(v.y for t in tris for v in t), min(v.z for t in tris for v in t)))
        mx = Vector((max(v.x for t in tris for v in t), max(v.y for t in tris for v in t), max(v.z for t in tris for v in t)))
        size = (mx - mn) * scale
        print(f"[archkit] STL {p}: {len(tris)} tris, {size.x:.1f} x {size.y:.1f} x {size.z:.1f} mm")
        paths.append(p)
    return paths

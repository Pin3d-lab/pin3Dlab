"""Exports: .blend, glTF (web / clients), FBX & OBJ (other DCC/CAD tools)."""
import bpy

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

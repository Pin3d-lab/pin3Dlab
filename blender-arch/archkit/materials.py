"""Material library. Base colours stay unlinked so glTF/FBX exports keep them;
procedural detail goes into roughness and bump only (render-time realism)."""
import os
import bpy

# name: (base colour, roughness, metallic, transmission, bump strength, noise scale)
LIBRARY = {
    "plaster_white": ((0.82, 0.81, 0.78), 0.85, 0.0, 0.0, 0.08, 18.0),
    "plaster_warm":  ((0.78, 0.70, 0.60), 0.85, 0.0, 0.0, 0.08, 18.0),
    "concrete":      ((0.50, 0.49, 0.47), 0.75, 0.0, 0.0, 0.15, 6.0),
    "concrete_dark": ((0.22, 0.22, 0.22), 0.70, 0.0, 0.0, 0.15, 6.0),
    "brick_red":     ((0.42, 0.16, 0.10), 0.90, 0.0, 0.0, 0.30, 4.0),
    "timber":        ((0.45, 0.28, 0.15), 0.60, 0.0, 0.0, 0.10, 3.0),
    "oak_floor":     ((0.55, 0.38, 0.22), 0.45, 0.0, 0.0, 0.03, 3.0),
    "tile_light":    ((0.75, 0.74, 0.72), 0.30, 0.0, 0.0, 0.02, 2.0),
    "aluminium_dark": ((0.05, 0.05, 0.055), 0.35, 1.0, 0.0, 0.0, 1.0),
    "steel":         ((0.55, 0.56, 0.58), 0.30, 1.0, 0.0, 0.0, 1.0),
    "glass":         ((0.90, 0.95, 0.95), 0.02, 0.0, 1.0, 0.0, 1.0),
    "roof_tile":     ((0.16, 0.15, 0.15), 0.70, 0.0, 0.0, 0.20, 5.0),
    "roof_membrane": ((0.35, 0.35, 0.34), 0.80, 0.0, 0.0, 0.05, 8.0),
    "gravel":        ((0.45, 0.43, 0.40), 0.95, 0.0, 0.0, 0.40, 40.0),
    "grass":         ((0.10, 0.20, 0.05), 0.95, 0.0, 0.0, 0.30, 60.0),
    "paving":        ((0.55, 0.53, 0.50), 0.80, 0.0, 0.0, 0.10, 10.0),
    "door_wood":     ((0.30, 0.18, 0.10), 0.50, 0.0, 0.0, 0.05, 3.0),
}

# role -> library name (override per project with spec["materials"])
ROLES = {
    "wall_ext": "plaster_white", "wall_int": "plaster_white", "slab": "concrete",
    "floor": "oak_floor", "roof": "roof_membrane", "frame": "aluminium_dark",
    "glass": "glass", "door": "door_wood", "stair": "concrete", "ground": "grass",
    "element": "concrete", "label": "concrete_dark",
}


def _set(node, names, value):
    for n in names:
        if n in node.inputs:
            node.inputs[n].default_value = value
            return True
    return False


def make(name, color, rough, metal=0.0, trans=0.0, bump=0.0, scale=10.0, image=None, image_scale=1.0):
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = (*color, 1.0)   # viewport / Workbench drawing colour
    mat.roughness = rough
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    _set(bsdf, ["Base Color"], (*color, 1.0))
    _set(bsdf, ["Roughness"], rough)
    _set(bsdf, ["Metallic"], metal)
    if trans:
        _set(bsdf, ["Transmission Weight", "Transmission"], trans)
        _set(bsdf, ["IOR"], 1.5)
        mat.diffuse_color = (0.55, 0.68, 0.75, 1.0)
        # EEVEE Legacy (<4.2) needs screen-space refraction or glass renders black
        for attr, val in (("use_screen_refraction", True), ("blend_method", "HASHED")):
            if hasattr(mat, attr):
                try:
                    setattr(mat, attr, val)
                except Exception:
                    pass
    coord = None
    if image or bump:
        coord = nt.nodes.new("ShaderNodeTexCoord")
    if image:
        path = bpy.path.abspath(image)
        if os.path.exists(path):
            mapping = nt.nodes.new("ShaderNodeMapping")
            mapping.inputs["Scale"].default_value = (1 / image_scale,) * 3
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = bpy.data.images.load(path, check_existing=True)
            tex.projection = "BOX"
            tex.projection_blend = 0.2
            nt.links.new(coord.outputs["Object"], mapping.inputs["Vector"])
            nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        else:
            print(f"[archkit] texture not found: {path}")
    if bump:
        noise = nt.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = scale
        _set(noise, ["Detail"], 8.0)
        nt.links.new(coord.outputs["Object"], noise.inputs["Vector"])
        bmp = nt.nodes.new("ShaderNodeBump")
        bmp.inputs["Strength"].default_value = bump
        _set(bmp, ["Distance"], 0.01)
        nt.links.new(noise.outputs["Fac"], bmp.inputs["Height"])
        nt.links.new(bmp.outputs["Normal"], bsdf.inputs["Normal"])
        # subtle roughness variation breaks up flat CG look
        ramp = nt.nodes.new("ShaderNodeMapRange")
        ramp.inputs["To Min"].default_value = max(0.0, rough - 0.1)
        ramp.inputs["To Max"].default_value = min(1.0, rough + 0.1)
        nt.links.new(noise.outputs["Fac"], ramp.inputs["Value"])
        nt.links.new(ramp.outputs["Result"], bsdf.inputs["Roughness"])
    return mat


def for_role(spec, role):
    """Material for a role, honouring spec['materials'] overrides.

    Override forms: "wall_ext": "brick_red"            (library name)
                    "wall_ext": {"color": [r,g,b], "roughness": 0.8,
                                 "image": "//textures/brick.jpg", "image_scale": 1.0}
    """
    ov = spec.get("materials", {}).get(role, ROLES.get(role, "concrete"))
    if isinstance(ov, str):
        lib = LIBRARY.get(ov) or LIBRARY["concrete"]
        return make(ov, *lib)
    base = LIBRARY.get(ov.get("base", ""), LIBRARY["concrete"])
    img = ov.get("image")
    if img and not img.startswith("//") and not os.path.isabs(img):
        img = os.path.join(spec["_dir"], img)
    return make(
        f"{role}_custom",
        tuple(ov.get("color", base[0])), ov.get("roughness", base[1]), ov.get("metallic", base[2]),
        ov.get("transmission", base[3]), ov.get("bump", base[4]), ov.get("noise_scale", base[5]),
        image=img, image_scale=ov.get("image_scale", 1.0),
    )

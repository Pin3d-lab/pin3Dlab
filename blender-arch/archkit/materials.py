"""Material library. If assets/ holds Poly Haven textures (python3 fetch_assets.py), the
matching library materials use them: colour, roughness and a bump from the displacement
map, all box-projected at real-world size (so no UVs are needed). Otherwise a procedural
fallback (noise roughness + bump) is used. mat.diffuse_color always carries a flat
colour for the viewport and for glTF/FBX exports."""
import json
import os

import bpy

# name: (base colour, roughness, metallic, transmission, bump strength, noise scale)
LIBRARY = {
    "plaster_white": ((0.82, 0.81, 0.78), 0.85, 0.0, 0.0, 0.08, 18.0),
    "plaster_warm":  ((0.78, 0.70, 0.60), 0.85, 0.0, 0.0, 0.08, 18.0),
    "concrete":      ((0.50, 0.49, 0.47), 0.75, 0.0, 0.0, 0.15, 6.0),
    "concrete_dark": ((0.22, 0.22, 0.22), 0.70, 0.0, 0.0, 0.15, 6.0),
    "brick_red":     ((0.42, 0.16, 0.10), 0.90, 0.0, 0.0, 0.30, 4.0),
    "timber":        ((0.36, 0.25, 0.16), 0.60, 0.0, 0.0, 0.10, 3.0),
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
    "foliage":       ((0.06, 0.13, 0.035), 0.65, 0.0, 0.0, 0.0, 1.0),
    "bark":          ((0.12, 0.09, 0.07), 0.90, 0.0, 0.0, 0.30, 8.0),
}

# role -> library name (override per project with spec["materials"])
ROLES = {
    "wall_ext": "plaster_white", "wall_int": "plaster_white", "slab": "concrete", "ceiling": "plaster_white",
    "floor": "oak_floor", "roof": "roof_membrane", "frame": "aluminium_dark",
    "glass": "glass", "door": "door_wood", "stair": "concrete", "ground": "grass",
    "element": "concrete", "label": "concrete_dark",
}

# how much of the photo texture's own variation to keep (0 = flat library colour) and
# bump strength from its displacement map
TEX_OPTS = {
    "plaster_white": {"detail": 0.35, "bump": 0.15},
    "concrete": {"detail": 0.85, "bump": 0.35},
    "grass": {"detail": 1.0, "bump": 0.8, "hue": 0.25},
    "brick_red": {"hue": 0.5},
    "paving": {"hue": 0.4},
    "gravel": {"hue": 0.4},
    "tile_light": {"detail": 0.8, "bump": 0.3},
    "oak_floor": {"detail": 1.0, "bump": 0.2},
}

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
_MANIFEST = None
USE_TEXTURES = True          # run.py sets this from --no-textures


def textures():
    global _MANIFEST
    if _MANIFEST is None:
        try:
            with open(os.path.join(ASSETS, "manifest.json")) as f:
                _MANIFEST = json.load(f).get("textures", {})
        except (OSError, ValueError):
            _MANIFEST = {}
    return _MANIFEST if USE_TEXTURES else {}


def _set(node, names, value):
    for n in names:
        if n in node.inputs:
            node.inputs[n].default_value = value
            return True
    return False


def _glass(mat, nt, bsdf, color):
    """Architectural glass: refractive for camera rays, transparent for shadow rays, so
    sunlight and sky light reach interiors (a plain glass BSDF blocks them without caustics)."""
    _set(bsdf, ["Transmission Weight", "Transmission"], 1.0)
    _set(bsdf, ["IOR"], 1.45)
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    lp = nt.nodes.new("ShaderNodeLightPath")
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    tr.inputs["Color"].default_value = (*color, 1.0)
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(lp.outputs["Is Shadow Ray"], mix.inputs["Fac"])
    nt.links.new(bsdf.outputs["BSDF"], mix.inputs[1])
    nt.links.new(tr.outputs["BSDF"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    mat.diffuse_color = (0.55, 0.68, 0.75, 1.0)
    for attr, val in (("use_screen_refraction", True), ("use_raytrace_refraction", True),
                      ("blend_method", "HASHED"), ("surface_render_method", "DITHERED")):
        if hasattr(mat, attr):
            try:
                setattr(mat, attr, val)
            except Exception:
                pass


def _image(nt, path, coord, scale, non_color=False):
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(path, check_existing=True)
    if non_color:
        tex.image.colorspace_settings.name = "Non-Color"
    tex.projection = "BOX"
    tex.projection_blend = 0.25
    tex.interpolation = "Cubic" if non_color else "Linear"
    nt.links.new(coord, tex.inputs["Vector"])
    return tex


def _mean_linear(img):
    """Average colour of an image in linear RGB (used to calibrate photo textures)."""
    try:
        import numpy as np
        w, h = img.size
        px = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        m = px.reshape(-1, 4)[::7, :3].mean(0)
    except Exception:
        return None
    if img.is_float or img.colorspace_settings.name != "sRGB":
        return tuple(float(c) for c in m)
    return tuple(float(c) ** 2.2 for c in m)


def _textured(nt, bsdf, tx, color, key):
    """Poly Haven set, box projected at real size. The colour map is calibrated so its
    average equals the library/spec colour: the photo gives detail, the spec gives tone."""
    opts = TEX_OPTS.get(key, {})
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    size = tx.get("size_m", 1.0)
    mp.inputs["Scale"].default_value = (1 / size,) * 3
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    vec = mp.outputs["Vector"]
    if "diff" in tx:
        tex = _image(nt, os.path.join(ASSETS, tx["diff"]), vec, size)
        col = tex.outputs["Color"]
        mean = _mean_linear(tex.image)
        if mean:
            # luminance variation of the photo, coloured with the target colour
            mlum = max(0.2126 * mean[0] + 0.7152 * mean[1] + 0.0722 * mean[2], 1e-3)
            bw = nt.nodes.new("ShaderNodeRGBToBW")
            nt.links.new(col, bw.inputs["Color"])
            ratio = nt.nodes.new("ShaderNodeMath")
            ratio.operation = "MULTIPLY"
            ratio.inputs[1].default_value = 1.0 / mlum
            nt.links.new(bw.outputs["Val"], ratio.inputs[0])
            tinted = nt.nodes.new("ShaderNodeMix")
            tinted.data_type, tinted.blend_type = "RGBA", "MULTIPLY"
            tinted.inputs["Factor"].default_value = 1.0
            tinted.inputs[6].default_value = (*color, 1.0)
            nt.links.new(ratio.outputs[0], tinted.inputs[7])
            out_col = tinted.outputs[2]
            hue = opts.get("hue", 0.0)
            if hue > 0:     # keep some of the photo's own colour variation (leaves in grass...)
                tl = 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]
                gain = nt.nodes.new("ShaderNodeMix")
                gain.data_type, gain.blend_type = "RGBA", "MULTIPLY"
                gain.inputs["Factor"].default_value = 1.0
                gain.inputs[7].default_value = (tl / mlum,) * 3 + (1.0,)
                nt.links.new(col, gain.inputs[6])
                mixh = nt.nodes.new("ShaderNodeMix")
                mixh.data_type = "RGBA"
                mixh.inputs["Factor"].default_value = hue
                nt.links.new(out_col, mixh.inputs[6])
                nt.links.new(gain.outputs[2], mixh.inputs[7])
                out_col = mixh.outputs[2]
            col = out_col
            detail = opts.get("detail", 1.0)
            if detail < 1.0:
                mix = nt.nodes.new("ShaderNodeMix")
                mix.data_type = "RGBA"
                mix.inputs["Factor"].default_value = detail
                mix.inputs[6].default_value = (*color, 1.0)
                nt.links.new(col, mix.inputs[7])
                col = mix.outputs[2]
        nt.links.new(col, bsdf.inputs["Base Color"])
    if "rough" in tx:
        r = _image(nt, os.path.join(ASSETS, tx["rough"]), vec, size, True)
        nt.links.new(r.outputs["Color"], bsdf.inputs["Roughness"])
    if "disp" in tx:
        d = _image(nt, os.path.join(ASSETS, tx["disp"]), vec, size, True)
        bmp = nt.nodes.new("ShaderNodeBump")
        bmp.inputs["Strength"].default_value = opts.get("bump", 0.5)
        _set(bmp, ["Distance"], 0.004 * size)
        nt.links.new(d.outputs["Color"], bmp.inputs["Height"])
        nt.links.new(bmp.outputs["Normal"], bsdf.inputs["Normal"])


def make(name, color, rough, metal=0.0, trans=0.0, bump=0.0, scale=10.0, image=None, image_scale=1.0,
         tex_key=None):
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = (*color, 1.0)   # viewport / export colour
    mat.roughness = rough
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    _set(bsdf, ["Base Color"], (*color, 1.0))
    _set(bsdf, ["Roughness"], rough)
    _set(bsdf, ["Metallic"], metal)
    if trans:
        _glass(mat, nt, bsdf, color)
        return mat
    if (tex_key or name) == "foliage":
        _set(bsdf, ["Subsurface Weight", "Subsurface"], 0.25)
        _set(bsdf, ["Subsurface Radius"], (0.2, 0.3, 0.1))
        _set(bsdf, ["Subsurface Scale"], 0.05)
    tx = textures().get(tex_key or name)
    if tx and not image:
        _textured(nt, bsdf, tx, color, tex_key or name)
        return mat
    coord = None
    if image or bump:
        coord = nt.nodes.new("ShaderNodeTexCoord")
    if image:
        path = bpy.path.abspath(image)
        if os.path.exists(path):
            mapping = nt.nodes.new("ShaderNodeMapping")
            mapping.inputs["Scale"].default_value = (1 / image_scale,) * 3
            nt.links.new(coord.outputs["Object"], mapping.inputs["Vector"])
            tex = _image(nt, path, mapping.outputs["Vector"], image_scale)
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


def by_name(name):
    lib = LIBRARY.get(name) or LIBRARY["concrete"]
    return make(name, *lib)


def for_role(spec, role):
    """Material for a role, honouring spec['materials'] overrides.

    Override forms: "wall_ext": "brick_red"            (library name)
                    "wall_ext": {"base": "plaster_white", "color": [r,g,b], "roughness": 0.8,
                                 "image": "textures/brick.jpg", "image_scale": 1.0}
    """
    ov = spec.get("materials", {}).get(role, ROLES.get(role, "concrete"))
    if isinstance(ov, str):
        return by_name(ov)
    base_name = ov.get("base", "")
    base = LIBRARY.get(base_name, LIBRARY["concrete"])
    img = ov.get("image")
    if img and not img.startswith("//") and not os.path.isabs(img):
        img = os.path.join(spec["_dir"], img)
    return make(
        f"{role}_custom",
        tuple(ov.get("color", base[0])), ov.get("roughness", base[1]), ov.get("metallic", base[2]),
        ov.get("transmission", base[3]), ov.get("bump", base[4]), ov.get("noise_scale", base[5]),
        image=img, image_scale=ov.get("image_scale", 1.0), tex_key=base_name or None,
    )

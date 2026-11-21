"""Cameras, lighting, render presets and the view loop."""
import math
import os
import bpy
from mathutils import Matrix, Vector

from . import spec as S
from .build import coll

PRESETS = {
    # fast iteration: look at composition, not quality
    "draft":   {"engine": "EEVEE",  "res": (1280, 720),  "samples": 16,  "noise": 0.1,  "bounces": 4,  "depth": "8"},
    # client check-ins: correct light, light noise
    "preview": {"engine": "CYCLES", "res": (1920, 1080), "samples": 96,  "noise": 0.05, "bounces": 6,  "depth": "8"},
    # deliverables
    "final":   {"engine": "CYCLES", "res": (3840, 2160), "samples": 768, "noise": 0.01, "bounces": 12, "depth": "16"},
}


# ---------------------------------------------------------------- engine helpers
def _set_engine(sc, kind):
    cands = {"EEVEE": ["BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"], "CYCLES": ["CYCLES"],
             "WORKBENCH": ["BLENDER_WORKBENCH"]}[kind]
    for c in cands:
        try:
            sc.render.engine = c
            return c
        except TypeError:
            continue
    raise RuntimeError(f"no render engine for {kind}")


def enable_gpu():
    """Use the best GPU backend available; returns the backend name or 'CPU'."""
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except KeyError:
        return "CPU"
    for backend in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
        try:
            prefs.compute_device_type = backend
        except TypeError:
            continue
        (getattr(prefs, "refresh_devices", None) or prefs.get_devices)()
        devs = [d for d in prefs.devices if d.type == backend]
        if devs:
            for d in prefs.devices:
                d.use = d.type == backend
            bpy.context.scene.cycles.device = "GPU"
            return backend
    return "CPU"


def configure(sc, preset, scale_pct=100):
    p = PRESETS[preset]
    r = sc.render
    r.resolution_x, r.resolution_y = p["res"]
    r.resolution_percentage = scale_pct
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGB"
    r.image_settings.color_depth = p["depth"]
    r.use_persistent_data = True  # re-use BVH/textures between views
    for vt in ("AgX", "Filmic"):
        try:
            sc.view_settings.view_transform = vt
            break
        except TypeError:
            pass
    for look in ("AgX - Medium High Contrast", "Medium High Contrast"):
        try:
            sc.view_settings.look = look
            break
        except TypeError:
            pass
    # Cycles (used for preview/final, and as draft fallback)
    cy = sc.cycles
    cy.samples = p["samples"]
    cy.use_adaptive_sampling = True
    cy.adaptive_threshold = p["noise"]
    cy.use_denoising = True
    try:
        cy.denoiser = "OPENIMAGEDENOISE"
    except TypeError:
        pass
    cy.max_bounces = p["bounces"]
    cy.diffuse_bounces = min(4, p["bounces"])
    cy.glossy_bounces = min(4, p["bounces"])
    cy.transmission_bounces = max(8, p["bounces"])  # glass needs these
    cy.transparent_max_bounces = 8
    cy.caustics_reflective = cy.caustics_refractive = False
    cy.blur_glossy = 1.0
    cy.sample_clamp_indirect = 10.0
    ee = sc.eevee
    ee.taa_render_samples = max(16, p["samples"] // 4)
    for attr, val in (("use_raytracing", True), ("use_gtao", True), ("use_ssr", True),
                      ("use_shadows", True), ("use_soft_shadows", True),
                      ("use_ssr_refraction", True)):
        if hasattr(ee, attr):
            setattr(ee, attr, val)
    return p


# ---------------------------------------------------------------- lighting
def setup_lighting(spec):
    sc = bpy.context.scene
    sun = spec["sun"]
    az, el = math.radians(sun["azimuth"]), math.radians(sun["elevation"])
    north = math.radians(spec.get("north", 0.0))      # true north rotation of the plan
    az_world = az - north
    to_sun = Vector((math.sin(az_world) * math.cos(el), math.cos(az_world) * math.cos(el), math.sin(el)))

    world = bpy.data.worlds.new("World")
    sc.world = world
    world.color = (1, 1, 1)                 # Workbench drawing background
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes["Background"]
    sky = nt.nodes.new("ShaderNodeTexSky")
    for t in ("NISHITA", "MULTIPLE_SCATTERING", "SINGLE_SCATTERING", "HOSEK_WILKIE"):
        try:
            sky.sky_type = t
            break
        except TypeError:
            pass
    if hasattr(sky, "sun_elevation"):
        sky.sun_elevation = el
        sky.sun_rotation = (math.pi / 2 - az_world) % (2 * math.pi)
        if hasattr(sky, "sun_disc"):
            sky.sun_disc = False            # the sun lamp below does the direct light
    nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = sun.get("sky_strength", 0.25)

    if hdri := spec.get("hdri"):            # optional HDRI replaces the sky
        path = hdri if os.path.isabs(hdri) else os.path.join(spec["_dir"], hdri)
        if os.path.exists(path):
            env = nt.nodes.new("ShaderNodeTexEnvironment")
            env.image = bpy.data.images.load(path)
            nt.links.new(env.outputs["Color"], bg.inputs["Color"])
            bg.inputs["Strength"].default_value = sun.get("hdri_strength", 1.0)

    lc = coll("Lights")
    ld = bpy.data.lights.new("Sun", "SUN")
    ld.energy = sun.get("strength", 4.0)
    ld.angle = math.radians(sun.get("softness", 0.6))
    ob = bpy.data.objects.new("Sun", ld)
    lc.objects.link(ob)
    ob.rotation_euler = (-to_sun).to_track_quat("-Z", "Y").to_euler()

    # interior fill: soft area light per room ceiling + Cycles portals in windows
    if spec.get("interior_lights"):
        for lv in spec["levels"]:
            for r in lv["rooms"]:
                xs = [p[0] for p in r["polygon"]]
                ys = [p[1] for p in r["polygon"]]
                a = S.area(r["polygon"])
                l = bpy.data.lights.new(f"ceil_{r['name']}", "AREA")
                l.shape = "RECTANGLE"
                l.size, l.size_y = (max(xs) - min(xs)) * 0.6, (max(ys) - min(ys)) * 0.6
                l.energy = a * spec.get("interior_watts_per_m2", 12)
                l.color = (1.0, 0.92, 0.82)
                o = bpy.data.objects.new(l.name, l)
                o.location = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                              lv["elevation"] + lv["height"] - lv["slab"] - 0.05)
                lc.objects.link(o)


def add_portals(spec):
    """Area-light portals in every window: big noise reduction for interior Cycles renders."""
    lc = coll("Lights")
    for ob in [o for o in bpy.data.objects if o.get("role") == "glass"]:
        l = bpy.data.lights.new(ob.name + "_portal", "AREA")
        l.shape = "RECTANGLE"
        dims = ob.dimensions
        l.size, l.size_y = dims.x, dims.z
        try:
            l.cycles.is_portal = True
        except AttributeError:
            continue
        p = bpy.data.objects.new(l.name, l)
        p.matrix_world = ob.matrix_world.copy()
        p.rotation_euler = (math.radians(-90), 0, ob.rotation_euler.z)  # face inward-ish
        lc.objects.link(p)


# ---------------------------------------------------------------- cameras
def _corners(b):
    return [Vector((x, y, z)) for x in (b[0], b[3]) for y in (b[1], b[4]) for z in (b[2], b[5])]


def _aspect(sc):
    return sc.render.resolution_x / sc.render.resolution_y


def new_camera(name, ortho=False):
    cd = bpy.data.cameras.new(name)
    cd.type = "ORTHO" if ortho else "PERSP"
    cd.sensor_fit = "HORIZONTAL"
    cd.sensor_width = 36
    cd.clip_end = 2000
    ob = bpy.data.objects.new(name, cd)
    coll("Cameras").objects.link(ob)
    return ob


def _mw(ob):
    """matrix_world computed from loc/rot directly (ob.matrix_world is stale until a depsgraph update)."""
    return Matrix.Translation(ob.location) @ ob.rotation_euler.to_matrix().to_4x4()


def look_at(ob, target):
    ob.rotation_euler = (Vector(target) - ob.location).to_track_quat("-Z", "Y").to_euler()


def fit_perspective(cam, corners, direction, margin=0.88, aspect=16 / 9, shift=True):
    """Move the camera back along -direction until all corners fit; use lens shift
    (not tilt) to frame them, which keeps verticals vertical in 2-point views."""
    cd = cam.data
    k = cd.lens / cd.sensor_width
    center = sum(corners, Vector()) / len(corners)
    direction = Vector(direction).normalized()
    eye = cam.location.copy()
    for dist in [x * 0.5 for x in range(4, 2000)]:
        cam.location = Vector((center.x, center.y, eye.z)) - Vector((direction.x, direction.y, 0)).normalized() * dist \
            if abs(direction.z) < 1e-6 else center - direction * dist
        look_at(cam, cam.location + direction)
        mw = _mw(cam).inverted()
        us, vs = [], []
        ok = True
        for c in corners:
            p = mw @ c
            if -p.z < 0.5:
                ok = False
                break
            us.append(p.x / -p.z * k)
            vs.append(p.y / -p.z * k)
        if not ok:
            continue
        uc, vc = (max(us) + min(us)) / 2, (max(vs) + min(vs)) / 2
        if not shift:
            uc = vc = 0.0
        if (max(us) - min(us)) <= margin and (max(vs) - min(vs)) <= margin / aspect \
                and max(abs(u - uc) for u in us) <= margin / 2 and max(abs(v - vc) for v in vs) <= margin / aspect / 2:
            cd.shift_x, cd.shift_y = uc, vc
            return cam
    return cam


def fit_ortho(cam, corners, margin=0.9, aspect=16 / 9):
    mw = _mw(cam).inverted()
    pts = [mw @ c for c in corners]
    us, vs = [p.x for p in pts], [p.y for p in pts]
    w, h = max(us) - min(us), max(vs) - min(vs)
    cam.data.ortho_scale = max(w, h * aspect) / margin
    uc, vc = (max(us) + min(us)) / 2, (max(vs) + min(vs)) / 2
    rot = cam.rotation_euler.to_matrix()
    right, up = rot @ Vector((1, 0, 0)), rot @ Vector((0, 1, 0))
    cam.location += right * uc + up * vc
    return cam


def make_views(spec):
    """Return {name: (camera, kind, level_name)}. kind: photo | plan | elevation."""
    sc = bpy.context.scene
    asp = _aspect(sc)
    b = S.bounds(spec, include_elements=False)   # frame the building, not the whole site
    bld = b
    corners = _corners((bld[0], bld[1], 0.0, bld[3], bld[4], bld[5]))
    cx, cy = (b[0] + b[3]) / 2, (b[1] + b[4]) / 2
    views = {}

    def az_dir(az_deg, el_deg=0.0):
        a, e = math.radians(az_deg), math.radians(el_deg)
        # direction the camera LOOKS (from az position toward the building)
        return Vector((-math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), -math.sin(e)))

    hero_az = spec.get("hero_azimuth", 210)       # camera stands SSW, looks NNE
    cam = new_camera("hero")
    cam.data.lens = spec.get("hero_lens", 28)
    cam.location = Vector((cx, cy, spec.get("eye_height", 1.6)))
    fit_perspective(cam, corners, az_dir(hero_az), aspect=asp)
    views["hero"] = (cam, "photo", None)

    cam = new_camera("hero_alt")
    cam.data.lens = spec.get("hero_lens", 28)
    cam.location = Vector((cx, cy, spec.get("eye_height", 1.6)))
    fit_perspective(cam, corners, az_dir(hero_az + 90), aspect=asp)
    views["hero_alt"] = (cam, "photo", None)

    cam = new_camera("aerial")
    cam.data.lens = 50
    fit_perspective(cam, corners, az_dir(hero_az - 15, 32), aspect=asp, shift=False)
    views["aerial"] = (cam, "photo", None)

    cam = new_camera("axo", ortho=True)
    cam.location = Vector((cx, cy, 0)) - az_dir(hero_az, 35.264) * 100
    look_at(cam, (cx, cy, 0))
    fit_ortho(cam, corners, aspect=asp)
    views["axo"] = (cam, "photo", None)

    for lv in spec["levels"]:
        cam = new_camera(f"plan_{lv['name']}", ortho=True)
        cut = spec.get("plan_cut_height", 1.2)
        cam.location = Vector((cx, cy, lv["elevation"] + cut))   # looks straight down, +Y up
        cam.data.clip_start = 0.001       # the camera plane IS the section cut
        cam.data.clip_end = cut + lv["slab"] + 0.5
        fit_ortho(cam, [Vector((x, y, lv["elevation"])) for x in (b[0], b[3]) for y in (b[1], b[4])],
                  margin=0.92, aspect=asp)
        cam.location.z = lv["elevation"] + cut
        views[f"plan_{lv['name']}"] = (cam, "plan", lv["name"])

    for name, az in (("south", 180), ("east", 90), ("north", 0), ("west", 270)):
        cam = new_camera(f"elev_{name}", ortho=True)
        d = az_dir(az)
        cam.location = Vector((cx, cy, (b[2] + b[5]) / 2)) - d * 200
        look_at(cam, cam.location + d)
        fit_ortho(cam, corners, margin=0.85, aspect=asp)
        views[f"elev_{name}"] = (cam, "elevation", None)

    for c in spec["cameras"]:   # custom / interior cameras from the spec
        cam = new_camera(c["name"])
        cam.data.lens = c.get("lens", 24)
        cam.location = c["location"]
        look_at(cam, c["look_at"])
        if c.get("two_point", True):   # level the camera; frame with shift instead of tilt
            t = Vector(c["look_at"])
            cam.data.shift_y = (t.z - cam.location.z) / ((t - cam.location).xy.length or 1) * cam.data.lens / 36
            look_at(cam, (t.x, t.y, cam.location.z))
        views[c["name"]] = (cam, "interior" if c.get("interior") else "photo", None)
    return views


# ---------------------------------------------------------------- drawing style
def drawing_style(sc, kind):
    _set_engine(sc, "WORKBENCH")
    sh = sc.display.shading
    sh.show_object_outline = True
    sh.object_outline_color = (0, 0, 0)
    sh.show_backface_culling = False
    sh.show_xray = False
    if kind == "plan":
        sh.light = "FLAT"
        sh.color_type = "OBJECT"
        sh.show_cavity = False
        sh.show_shadows = False
    else:  # elevation: flat colour fills + outlines, like a coloured line drawing
        sh.light = "FLAT"
        sh.color_type = "MATERIAL"
        sh.show_cavity = False
        sh.show_shadows = False
    sc.display.render_aa = "16"
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"


def _set_label_visibility(level):
    for ob in bpy.data.objects:
        if ob.get("role") == "label":
            ob.hide_render = level is None or ob.get("level") != level
        if ob.get("role") == "ground" and level is not None:
            ob.hide_render = True        # cleaner plans
        elif ob.get("role") == "ground":
            ob.hide_render = False


_NO_OIDN = None


def _probe_denoiser(sc):
    """Some distro Blender builds lack OpenImageDenoise and a failed render leaves an
    empty image behind. Probe once with a tiny render before the real ones."""
    global _NO_OIDN
    if _NO_OIDN is not None:
        return
    r = sc.render
    keep = (r.resolution_x, r.resolution_y, r.resolution_percentage, sc.cycles.samples, r.engine)
    r.resolution_x = r.resolution_y = 16
    r.resolution_percentage = 100
    sc.cycles.samples = 1
    r.engine = "CYCLES"
    sc.cycles.use_denoising = True
    try:
        bpy.ops.render.render()
        _NO_OIDN = False
    except RuntimeError:
        _NO_OIDN = True
        print("[archkit] this Blender build has no OpenImageDenoise: rendering without denoising. "
              "Use the official build from blender.org for clean low-sample renders.")
    r.resolution_x, r.resolution_y, r.resolution_percentage, sc.cycles.samples, r.engine = keep


def _render(path):
    sc = bpy.context.scene
    sc.render.filepath = path
    if _NO_OIDN:
        sc.cycles.use_denoising = False
    bpy.ops.render.render(write_still=True)


def render_views(spec, views, wanted, preset, out_dir, scale_pct=100):
    sc = bpy.context.scene
    os.makedirs(out_dir, exist_ok=True)
    results = []
    photo_engine = PRESETS[preset]["engine"]
    if photo_engine == "CYCLES" or any(views[v][1] == "interior" for v in wanted if v in views):
        backend = enable_gpu()
        print(f"[archkit] Cycles device: {backend}")
        _probe_denoiser(sc)
    if any(views[v][1] == "interior" for v in wanted if v in views) and photo_engine == "CYCLES":
        add_portals(spec)
    for name in wanted:
        if name not in views:
            print(f"[archkit] unknown view '{name}', available: {', '.join(views)}")
            continue
        cam, kind, level = views[name]
        configure(sc, preset, scale_pct)
        sc.camera = cam
        _set_label_visibility(level if kind == "plan" else None)
        if kind in ("plan", "elevation"):
            drawing_style(sc, kind)
        else:
            _set_engine(sc, "CYCLES" if kind == "interior" else photo_engine)
        path = os.path.join(out_dir, f"{name}.png")
        try:
            _render(path)
        except RuntimeError as e:   # e.g. EEVEE/Workbench without a GPU context
            if sc.render.engine == "CYCLES":
                raise
            print(f"[archkit] {sc.render.engine} failed ({e}); falling back to Cycles")
            _set_engine(sc, "CYCLES")
            sc.cycles.samples = min(sc.cycles.samples, 32)
            _render(path)
        results.append(path)
        print(f"[archkit] rendered {path}")
    return results

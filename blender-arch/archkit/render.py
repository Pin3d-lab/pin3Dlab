"""Cameras, lighting presets, render presets, finishing (AgX, glare, DOF) and the view loop.

Photo views only: plans/sections/elevations are vector drawings (drawings.py)."""
import json
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
    "preview": {"engine": "CYCLES", "res": (1920, 1080), "samples": 128, "noise": 0.04, "bounces": 8,  "depth": "8"},
    # deliverables
    "final":   {"engine": "CYCLES", "res": (3840, 2160), "samples": 768, "noise": 0.01, "bounces": 12, "depth": "16"},
}

# Lighting presets. Sun elevation/azimuth come from spec["sun"] for "day";
# the others override the elevation. "hdri" names an entry in assets/manifest.json.
LIGHTING = {
    "day":      {"sun": 4.0, "sun_color": (1.0, 0.96, 0.90), "elevation": None, "hdri": "day",
                 "hdri_strength": 1.0, "sky_strength": 0.25, "exposure": 0.0, "interior": False},
    "golden":   {"sun": 5.5, "sun_color": (1.0, 0.52, 0.24), "elevation": 9.0, "hdri": "golden",
                 "hdri_strength": 0.45, "sky_strength": 0.3, "exposure": 0.3, "interior": False},
    "overcast": {"sun": 0.0, "sun_color": (1, 1, 1), "elevation": 40.0, "hdri": "overcast",
                 "hdri_strength": 1.6, "sky_strength": 1.0, "exposure": 0.3, "interior": False},
    "night":    {"sun": 0.0, "sun_color": (1, 1, 1), "elevation": -4.0, "hdri": "night",
                 "hdri_strength": 0.06, "sky_strength": 0.02, "exposure": 0.6, "interior": True},
}

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


def manifest():
    try:
        with open(os.path.join(ASSETS, "manifest.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"textures": {}, "hdris": {}}


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


def configure(sc, preset, scale_pct=100, interior=False):
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
    cy = sc.cycles
    cy.samples = p["samples"] * (2 if interior and preset != "draft" else 1)
    cy.use_adaptive_sampling = True
    cy.adaptive_threshold = p["noise"]
    cy.use_denoising = True
    try:
        cy.denoiser = "OPENIMAGEDENOISE"
        cy.denoising_input_passes = "RGB_ALBEDO_NORMAL"
        cy.denoising_prefilter = "ACCURATE"
    except (TypeError, AttributeError):
        pass
    # interiors are lit almost entirely by bounced light: give them more diffuse bounces
    b = p["bounces"] + (4 if interior else 0)
    cy.max_bounces = b
    cy.diffuse_bounces = min(b, 8 if interior else 4)
    cy.glossy_bounces = min(b, 4)
    cy.transmission_bounces = max(8, b)       # glass needs these
    cy.transparent_max_bounces = 16
    cy.caustics_reflective = cy.caustics_refractive = False
    cy.blur_glossy = 1.0
    cy.sample_clamp_direct = 0.0
    cy.sample_clamp_indirect = 10.0
    if hasattr(cy, "use_light_tree"):
        cy.use_light_tree = True
    ee = sc.eevee
    ee.taa_render_samples = max(32, p["samples"] // 2)
    for attr, val in (("use_raytracing", True), ("use_gtao", True), ("use_ssr", True),
                      ("use_shadows", True), ("use_soft_shadows", True), ("use_ssr_refraction", True),
                      ("use_fast_gi", True), ("shadow_ray_count", 2), ("shadow_step_count", 8)):
        if hasattr(ee, attr):
            try:
                setattr(ee, attr, val)
            except (TypeError, AttributeError):
                pass
    return p


# ---------------------------------------------------------------- lighting
def _hdri_sun(img):
    """(azimuth deg, elevation deg, clamp value) of the brightest spot of an equirect HDRI."""
    try:
        import numpy as np
    except ImportError:
        return None
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    lum = (px.reshape(-1, 4)[:, :3] @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32))
    i = int(lum.argmax())
    row, col = divmod(i, w)
    u, v = (col + 0.5) / w, (row + 0.5) / h
    phi = (u - 0.5) * 2 * math.pi             # Blender equirect: phi = atan2(y, -x)
    x, y = -math.cos(phi), math.sin(phi)
    az = math.degrees(math.atan2(x, y)) % 360
    el = (v - 0.5) * 180.0
    clamp = float(np.percentile(lum, 99.5)) * 3.0
    return az, el, max(clamp, 1.0), float(lum.max())


def setup_lighting(spec, preset="day", use_hdri=True):
    """World (HDRI or physical sky), sun lamp and, for night/interior use, room lights."""
    sc = bpy.context.scene
    L = dict(LIGHTING[preset])
    sun = spec["sun"]
    az = sun["azimuth"]
    el = sun["elevation"] if L["elevation"] is None or preset == "day" else L["elevation"]
    if preset == "day":
        el = sun["elevation"]
    north = spec.get("north", 0.0)          # true north rotation of the plan
    az_world = math.radians(az - north)
    elr = math.radians(el)
    to_sun = Vector((math.sin(az_world) * math.cos(elr), math.cos(az_world) * math.cos(elr), math.sin(elr)))

    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    sc.world = world
    world.color = (1, 1, 1)
    world.use_nodes = True
    nt = world.node_tree
    for n in list(nt.nodes):
        if n.type not in ("OUTPUT_WORLD",):
            nt.nodes.remove(n)
    out = next(n for n in nt.nodes if n.type == "OUTPUT_WORLD")
    bg = nt.nodes.new("ShaderNodeBackground")
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    hdri_path = None
    if spec.get("hdri"):                     # explicit project HDRI wins
        hp = spec["hdri"]
        hdri_path = hp if os.path.isabs(hp) else os.path.join(spec["_dir"], hp)
    elif use_hdri:
        h = manifest().get("hdris", {}).get(L["hdri"])
        if h:
            hdri_path = os.path.join(ASSETS, h["file"])
    if hdri_path and os.path.exists(hdri_path):
        env = nt.nodes.new("ShaderNodeTexEnvironment")
        env.image = bpy.data.images.load(hdri_path, check_existing=True)
        info = _hdri_sun(env.image)
        tc = nt.nodes.new("ShaderNodeTexCoord")
        mp = nt.nodes.new("ShaderNodeMapping")
        nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
        nt.links.new(mp.outputs["Vector"], env.inputs["Vector"])
        col = env.outputs["Color"]
        if info:
            h_az, h_el, clamp, peak = info
            # rotate the HDRI so its sun glow sits where our sun lamp is
            mp.inputs["Rotation"].default_value[2] = math.radians((az - north) - h_az)
            if L["sun"] > 0 and peak > clamp:
                # remove the HDRI's own sun disc: the lamp gives crisper, controllable shadows
                mn = nt.nodes.new("ShaderNodeVectorMath")
                mn.operation = "MINIMUM"
                mn.inputs[1].default_value = (clamp, clamp, clamp)
                nt.links.new(col, mn.inputs[0])
                col = mn.outputs["Vector"]
        nt.links.new(col, bg.inputs["Color"])
        bg.inputs["Strength"].default_value = sun.get("hdri_strength", L["hdri_strength"])
        print(f"[archkit] lighting '{preset}': HDRI {os.path.basename(hdri_path)}")
    else:
        if preset == "overcast":
            bg.inputs["Color"].default_value = (0.78, 0.80, 0.84, 1)
            bg.inputs["Strength"].default_value = L["sky_strength"]
        else:
            sky = nt.nodes.new("ShaderNodeTexSky")
            for t in ("NISHITA", "MULTIPLE_SCATTERING", "SINGLE_SCATTERING", "HOSEK_WILKIE"):
                try:
                    sky.sky_type = t
                    break
                except TypeError:
                    pass
            if hasattr(sky, "sun_elevation"):
                sky.sun_elevation = max(elr, math.radians(-2))
                sky.sun_rotation = (math.pi / 2 - az_world) % (2 * math.pi)
                if hasattr(sky, "sun_disc"):
                    sky.sun_disc = False        # the sun lamp below does the direct light
            nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
            bg.inputs["Strength"].default_value = sun.get("sky_strength", L["sky_strength"])
        print(f"[archkit] lighting '{preset}': procedural sky (run fetch_assets.py for HDRIs)")

    lc = coll("Lights")
    for ob in list(lc.objects):
        bpy.data.objects.remove(ob)
    if L["sun"] > 0:
        ld = bpy.data.lights.new("Sun", "SUN")
        ld.energy = sun.get("strength", L["sun"]) if preset == "day" else L["sun"]
        ld.color = L["sun_color"]
        ld.angle = math.radians(sun.get("softness", 0.6 if preset != "golden" else 1.2))
        ob = bpy.data.objects.new("Sun", ld)
        lc.objects.link(ob)
        ob.rotation_euler = (-to_sun).to_track_quat("-Z", "Y").to_euler()
    if L["interior"] or spec.get("interior_lights"):
        interior_lights(spec, night=preset == "night")
    sc["archkit_exposure"] = L["exposure"]


def interior_lights(spec, night=False):
    """Warm ceiling panel per room (plus a soft fill near the floor at night)."""
    lc = coll("Lights")
    wpm = spec.get("interior_watts_per_m2", 7 if night else 10)
    for lv in spec["levels"]:
        for r in lv["rooms"]:
            xs = [p[0] for p in r["polygon"]]
            ys = [p[1] for p in r["polygon"]]
            a = S.area(r["polygon"])
            l = bpy.data.lights.new(f"ceil_{lv['name']}_{r['name']}", "AREA")
            l.shape = "RECTANGLE"
            l.size, l.size_y = (max(xs) - min(xs)) * 0.5, (max(ys) - min(ys)) * 0.5
            l.energy = a * wpm
            l.color = (1.0, 0.86, 0.70)
            o = bpy.data.objects.new(l.name, l)
            o.location = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                          lv["elevation"] + lv["height"] - lv["slab"] - 0.03)
            o.visible_camera = False            # light the room, don't show a glowing panel
            o.visible_glossy = False
            o.visible_transmission = False     # ...nor through the glass
            lc.objects.link(o)


def add_portals(spec):
    """Area-light portals in every exterior window/glazed door: Cycles then samples the sky
    through the openings, which removes most of the noise in interiors."""
    lc = coll("Lights")
    for ob in [o for o in bpy.data.objects if o.get("role") == "glass" and o.get("exterior")]:
        l = bpy.data.lights.new(ob.name + "_portal", "AREA")
        l.shape = "RECTANGLE"
        sx, sy, sz = ob.dimensions
        l.size, l.size_y = sx + 0.05, sz + 0.05
        try:
            l.cycles.is_portal = True
        except AttributeError:
            continue
        p = bpy.data.objects.new(l.name, l)
        # light X along the wall, Y up, emitting along -Z = towards the room (wall's left normal)
        p.location = ob.location
        p.rotation_euler = (math.radians(90), 0, ob.rotation_euler.z)
        lc.objects.link(p)


# ---------------------------------------------------------------- finishing
def _comp_tree(sc):
    if hasattr(sc, "compositing_node_group"):          # Blender 5.x
        ng = sc.compositing_node_group
        if ng is None:
            ng = bpy.data.node_groups.new("Compositing", "CompositorNodeTree")
            sc.compositing_node_group = ng
        return ng
    sc.use_nodes = True
    return sc.node_tree


def setup_compositor(sc, glare=True):
    """Subtle bloom on highlights (sun on glass, lamps): reads as 'photographic'."""
    try:
        nt = _comp_tree(sc)
    except Exception as e:      # compositor API differs between versions: never block a render
        print(f"[archkit] compositor not set up ({e})")
        return
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    rl = nt.nodes.new("CompositorNodeRLayers")
    try:
        outn = nt.nodes.new("CompositorNodeComposite")
        out_sock = outn.inputs["Image"]
    except RuntimeError:
        outn = nt.nodes.new("NodeGroupOutput")
        nt.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        out_sock = outn.inputs[0]
    src = rl.outputs["Image"]
    if glare:
        g = nt.nodes.new("CompositorNodeGlare")
        for attr, val in (("glare_type", "BLOOM"), ("quality", "HIGH")):
            try:
                setattr(g, attr, val)
            except (TypeError, AttributeError):
                if attr == "glare_type":
                    g.glare_type = "FOG_GLOW"
        settings = {"Threshold": 1.2, "Strength": 0.12, "Size": 0.6, "Saturation": 1.0}
        if "Strength" in g.inputs:
            for k, v in settings.items():
                if k in g.inputs:
                    g.inputs[k].default_value = v
        else:
            g.mix = -0.88
            g.threshold = 1.2
            g.size = 8
        nt.links.new(src, g.inputs["Image"])
        src = g.outputs["Image"]
    nt.links.new(src, out_sock)


def apply_dof(cam, target, fstop=5.6):
    cam.data.dof.use_dof = True
    cam.data.dof.focus_distance = (Vector(target) - cam.location).length
    cam.data.dof.aperture_fstop = fstop


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
    """Photo views: {name: (camera, kind, target)}. kind: photo | interior."""
    sc = bpy.context.scene
    asp = _aspect(sc)
    b = S.bounds(spec, include_elements=False)   # frame the building, not the whole site
    corners = _corners((b[0], b[1], 0.0, b[3], b[4], b[5]))
    cx, cy = (b[0] + b[3]) / 2, (b[1] + b[4]) / 2
    center = Vector((cx, cy, b[5] * 0.4))
    views = {}

    def az_dir(az_deg, el_deg=0.0):
        a, e = math.radians(az_deg), math.radians(el_deg)
        # direction the camera LOOKS (from az position toward the building)
        return Vector((-math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), -math.sin(e)))

    hero_az = spec.get("hero_azimuth", 210)       # camera stands SSW, looks NNE
    for name, az in (("hero", hero_az), ("hero_alt", hero_az + 90)):
        cam = new_camera(name)
        cam.data.lens = spec.get("hero_lens", 28)
        cam.location = Vector((cx, cy, spec.get("eye_height", 1.6)))
        fit_perspective(cam, corners, az_dir(az), aspect=asp)
        views[name] = (cam, "photo", center)

    cam = new_camera("aerial")
    cam.data.lens = 50
    fit_perspective(cam, corners, az_dir(hero_az - 15, 32), aspect=asp, shift=False)
    views["aerial"] = (cam, "photo", center)

    cam = new_camera("axo", ortho=True)
    cam.location = Vector((cx, cy, 0)) - az_dir(hero_az, 35.264) * 100
    look_at(cam, (cx, cy, 0))
    fit_ortho(cam, corners, aspect=asp)
    views["axo"] = (cam, "photo", center)

    for c in spec["cameras"]:   # custom / interior cameras from the spec
        cam = new_camera(c["name"])
        cam.data.lens = c.get("lens", 24)
        cam.location = c["location"]
        cam.data.clip_start = 0.05
        look_at(cam, c["look_at"])
        if c.get("two_point", True):   # level the camera; frame with shift instead of tilt
            t = Vector(c["look_at"])
            cam.data.shift_y = (t.z - cam.location.z) / ((t - cam.location).xy.length or 1) * cam.data.lens / 36
            look_at(cam, (t.x, t.y, cam.location.z))
        if c.get("dof"):
            d = c["dof"] if isinstance(c["dof"], dict) else {}
            apply_dof(cam, d.get("focus", c["look_at"]), d.get("fstop", 4.0))
        cam["exposure"] = c.get("exposure", 2.2 if c.get("interior") else 0.0)
        views[c["name"]] = (cam, "interior" if c.get("interior") else "photo", Vector(c["look_at"]))
    return views


# ---------------------------------------------------------------- render loop
_OIDN = None


def _probe_denoiser(sc):
    """Distro Blender builds may lack OpenImageDenoise, and a failed render leaves an empty
    image behind. Probe once with a tiny render before the real ones."""
    global _OIDN
    if _OIDN is not None:
        return _OIDN
    r = sc.render
    keep = (r.resolution_x, r.resolution_y, r.resolution_percentage, sc.cycles.samples, r.engine,
            sc.camera, r.filepath)
    tmp = None
    if sc.camera is None:
        tmp = new_camera("_probe")
        sc.camera = tmp
    r.resolution_x = r.resolution_y = 16
    r.resolution_percentage = 100
    sc.cycles.samples = 1
    r.engine = "CYCLES"
    sc.cycles.use_denoising = True
    try:
        bpy.ops.render.render()
        _OIDN = True
    except RuntimeError as e:
        _OIDN = False
        print(f"[archkit] denoiser unavailable ({e}): rendering without denoising. "
              "Use the official build from blender.org for clean low-sample renders.")
    r.resolution_x, r.resolution_y, r.resolution_percentage, sc.cycles.samples, r.engine, sc.camera, r.filepath = keep
    if tmp:
        bpy.data.objects.remove(tmp)
    return _OIDN


def _render(path):
    sc = bpy.context.scene
    sc.render.filepath = path
    if _OIDN is False:
        sc.cycles.use_denoising = False
    bpy.ops.render.render(write_still=True)


def render_views(spec, views, wanted, preset, out_dir, scale_pct=100, dof=False, glare=True, suffix=""):
    sc = bpy.context.scene
    os.makedirs(out_dir, exist_ok=True)
    results = []
    photo_engine = PRESETS[preset]["engine"]
    wanted = [v for v in wanted if v in views]
    if photo_engine == "CYCLES" or any(views[v][1] == "interior" for v in wanted):
        print(f"[archkit] Cycles device: {enable_gpu()}")
        _probe_denoiser(sc)
    if any(views[v][1] == "interior" for v in wanted):
        add_portals(spec)
    setup_compositor(sc, glare)
    for name in wanted:
        cam, kind, target = views[name]
        interior = kind == "interior"
        configure(sc, preset, scale_pct, interior)
        sc.view_settings.exposure = sc.get("archkit_exposure", 0.0) + cam.get("exposure", 0.0)
        sc.camera = cam
        if dof and not cam.data.dof.use_dof and cam.type == "CAMERA" and cam.data.type == "PERSP":
            apply_dof(cam, target, 5.6 if not interior else 4.0)
        _set_engine(sc, "CYCLES" if interior else photo_engine)
        path = os.path.join(out_dir, f"{name}{suffix}.png")
        try:
            _render(path)
        except RuntimeError as e:   # e.g. EEVEE without a GPU context
            if sc.render.engine == "CYCLES":
                raise
            print(f"[archkit] {sc.render.engine} failed ({e}); falling back to Cycles")
            _set_engine(sc, "CYCLES")
            sc.cycles.samples = min(sc.cycles.samples, 32)
            _render(path)
        results.append(path)
        print(f"[archkit] rendered {path}")
    return results

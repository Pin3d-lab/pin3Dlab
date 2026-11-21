"""archkit runner. Usage (Blender's own Python):

  blender -b -P run.py -- projects/example_house --preset draft --views hero,plan_GF
  blender -b -P run.py -- projects/example_house --preset final --views photos --light golden
  blender -b -P run.py -- projects/example_house --views drawings --sheet A3 --scale 100
  blender    -P run.py -- projects/example_house --no-render     # build, then keep the GUI open

Photo views : hero hero_alt aerial axo <spec cameras>      -> out/<preset>/<view>.png
Drawings    : plan_<LEVEL> section_<NAME> elev_south|east|north|west
                                                           -> out/drawings/<view>.svg/.pdf
                                                              + <project>_drawings.pdf (set)
Groups      : all | photos | drawings | plans | sections | elevations | default
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
from archkit import spec as S, build as B, render as R, export as X, drawings as D  # noqa: E402
from archkit import materials as M  # noqa: E402


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="run.py")
    ap.add_argument("project", help="project folder (with spec.json) or a .json file")
    ap.add_argument("--preset", choices=list(R.PRESETS), default="draft")
    ap.add_argument("--views", default="default")
    ap.add_argument("--light", choices=list(R.LIGHTING), default=None,
                    help="lighting preset (default: spec 'lighting' or day)")
    ap.add_argument("--dof", action="store_true", help="depth of field on perspective views")
    ap.add_argument("--no-glare", action="store_true", help="switch off the subtle bloom")
    ap.add_argument("--no-context", action="store_true", help="no trees around the building")
    ap.add_argument("--no-textures", action="store_true", help="procedural materials only")
    ap.add_argument("--no-hdri", action="store_true", help="physical sky instead of HDRI")
    ap.add_argument("--sheet", default=None, choices=["A4", "A3", "A2", "A1", "A0"],
                    help="drawing sheet size (default: spec 'sheet' or A3)")
    ap.add_argument("--scale", type=int, default=None,
                    help="drawing scale 1:N, e.g. 50 or 100 (default: spec 'scale' or 100)")
    ap.add_argument("--export", default="", help="comma list: blend,glb,fbx,obj")
    ap.add_argument("--resolution", type=int, default=100, help="resolution percentage")
    ap.add_argument("--out", default=None, help="output folder (default: <project>/out)")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--force", action="store_true", help="build even if lint reports errors")
    return ap.parse_args(argv)


def expand_views(arg, photos, drawings, spec):
    plans = [v for v in drawings if v.startswith("plan_")]
    secs = [v for v in drawings if v.startswith("section_")]
    elevs = [v for v in drawings if v.startswith("elev_")]
    custom = [c["name"] for c in spec["cameras"]]
    groups = {
        "all": list(photos) + list(drawings),
        "photos": ["hero", "hero_alt", "aerial", "axo"] + custom,
        "drawings": plans + secs + elevs,
        "plans": plans, "sections": secs, "elevations": elevs,
        "default": ["hero", "aerial"] + plans[:1],
    }
    out = []
    for item in arg.split(","):
        out += groups.get(item.strip(), [item.strip()])
    return list(dict.fromkeys(v for v in out if v))


def main():
    a = parse()
    t0 = time.time()
    spec = S.load(os.path.abspath(a.project))
    out = os.path.abspath(a.out or os.path.join(spec["_dir"], "out"))
    os.makedirs(out, exist_ok=True)

    issues = S.lint(spec)
    for sev, msg in issues:
        print(f"[archkit] {sev.upper()}: {msg}")
    rep = S.report(spec)
    with open(os.path.join(out, "report.md"), "w") as f:
        f.write(S.report_markdown(rep, issues))
    with open(os.path.join(out, "report.json"), "w") as f:
        json.dump({"report": rep, "lint": issues}, f, indent=2)
    if any(s == "error" for s, _ in issues) and not a.force:
        print("[archkit] lint errors: fix spec.json or pass --force")
        sys.exit(2)

    M.USE_TEXTURES = not a.no_textures
    light = a.light or spec.get("lighting", "day")
    B.build(spec, quality=a.preset, context=not a.no_context)
    R.configure(bpy.context.scene, a.preset, a.resolution)
    R.setup_lighting(spec, light, use_hdri=not a.no_hdri)
    photos = R.make_views(spec)
    drawings = D.views(spec)
    print(f"[archkit] built '{spec['name']}' in {time.time() - t0:.1f}s")
    print(f"[archkit] photo views: {', '.join(photos)}")
    print(f"[archkit] drawings: {', '.join(drawings)}")

    if not a.no_render:
        wanted = expand_views(a.views, photos, drawings, spec)
        unknown = [v for v in wanted if v not in photos and v not in drawings]
        for v in unknown:
            print(f"[archkit] unknown view '{v}'")
        dw = [v for v in wanted if v in drawings]
        if dw:
            D.make_sheets(spec, dw, os.path.join(out, "drawings"),
                          sheet=a.sheet or spec.get("sheet", "A3"), scale=a.scale or spec.get("scale", 100))
        pw = [v for v in wanted if v in photos]
        if pw:
            suffix = "" if light == "day" else f"_{light}"
            R.render_views(spec, photos, pw, a.preset, os.path.join(out, a.preset), a.resolution,
                           dof=a.dof, glare=not a.no_glare, suffix=suffix)

    name = spec["name"].replace(" ", "_")
    for fmt in [f.strip() for f in a.export.split(",") if f.strip()]:
        if fmt == "glb":
            X.gltf(os.path.join(out, f"{name}.glb"))
        elif fmt == "fbx":
            X.fbx(os.path.join(out, f"{name}.fbx"))
        elif fmt == "obj":
            X.obj(os.path.join(out, f"{name}.obj"))
        elif fmt == "blend":
            pass  # saved below
        else:
            print(f"[archkit] unknown export '{fmt}' (use blend, glb, fbx, obj)")
    if "blend" in a.export or a.no_render:
        X.save_blend(os.path.join(out, f"{name}.blend"))
    print(f"[archkit] done in {time.time() - t0:.1f}s -> {out}")


if __name__ == "__main__":
    main()

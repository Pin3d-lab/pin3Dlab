"""archkit runner. Usage (Blender's own Python):

  blender -b -P run.py -- projects/example_house --preset draft --views hero,plan_GF
  blender -b -P run.py -- projects/example_house --preset final --views all --export glb,stl
  blender    -P run.py -- projects/example_house --no-render     # build, then keep the GUI open

Views: hero hero_alt aerial axo plan_<LEVEL> elev_south|east|north|west <custom cameras>
Groups: all | photos | drawings | default
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
from archkit import spec as S, build as B, render as R, export as X  # noqa: E402


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="run.py")
    ap.add_argument("project", help="project folder (with spec.json) or a .json file")
    ap.add_argument("--preset", choices=list(R.PRESETS), default="draft")
    ap.add_argument("--views", default="default")
    ap.add_argument("--export", default="", help="comma list: blend,glb,fbx,obj,stl")
    ap.add_argument("--scale", type=int, default=100, help="STL model scale 1:N (default 100)")
    ap.add_argument("--split-levels", action="store_true", help="one STL per level (lift-off floors)")
    ap.add_argument("--stl-base", action="store_true", help="include the site as a base plate")
    ap.add_argument("--voxel", type=float, default=None, help="STL remesh size in real metres, 0 = off")
    ap.add_argument("--resolution", type=int, default=100, help="resolution percentage")
    ap.add_argument("--out", default=None, help="output folder (default: <project>/out)")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--force", action="store_true", help="build even if lint reports errors")
    return ap.parse_args(argv)


def expand_views(arg, views, spec):
    plans = [v for v in views if v.startswith("plan_")]
    elevs = [v for v in views if v.startswith("elev_")]
    custom = [c["name"] for c in spec["cameras"]]
    groups = {
        "all": list(views),
        "photos": ["hero", "hero_alt", "aerial", "axo"] + custom,
        "drawings": plans + elevs,
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

    B.build(spec, quality=a.preset)
    R.configure(bpy.context.scene, a.preset, a.resolution)
    R.setup_lighting(spec)
    views = R.make_views(spec)
    print(f"[archkit] built '{spec['name']}' in {time.time() - t0:.1f}s; views: {', '.join(views)}")

    if not a.no_render:
        R.render_views(spec, views, expand_views(a.views, views, spec), a.preset,
                       os.path.join(out, a.preset), a.resolution)

    name = spec["name"].replace(" ", "_")
    for fmt in [f.strip() for f in a.export.split(",") if f.strip()]:
        if fmt == "glb":
            X.gltf(os.path.join(out, f"{name}.glb"))
        elif fmt == "fbx":
            X.fbx(os.path.join(out, f"{name}.fbx"))
        elif fmt == "obj":
            X.obj(os.path.join(out, f"{name}.obj"))
        elif fmt == "stl":
            X.stl_model(out, name, a.scale, a.voxel, a.split_levels, a.stl_base)
        elif fmt == "blend":
            pass  # saved below
        else:
            print(f"[archkit] unknown export '{fmt}'")
    if "blend" in a.export or a.no_render:
        X.save_blend(os.path.join(out, f"{name}.blend"))
    print(f"[archkit] done in {time.time() - t0:.1f}s -> {out}")


if __name__ == "__main__":
    main()

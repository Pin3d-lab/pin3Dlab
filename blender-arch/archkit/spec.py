"""Spec loading, validation (lint) and architectural reporting.

Pure Python: no bpy import, so it can be tested and run outside Blender
(`python3 -m archkit.spec projects/example_house`).
"""
import json
import math
import os

DEFAULTS = {
    "wall_thickness": 0.25,
    "slab_thickness": 0.25,
    "level_height": 3.0,
    "opening": {"window": {"width": 1.2, "height": 1.4, "sill": 0.9},
                "door": {"width": 0.9, "height": 2.1, "sill": 0.0},
                "opening": {"width": 1.0, "height": 2.1, "sill": 0.0}},
}


# ---------------------------------------------------------------- geometry
def signed_area(poly):
    a = 0.0
    for i, (x1, y1) in enumerate(poly):
        x2, y2 = poly[(i + 1) % len(poly)]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def area(poly):
    return abs(signed_area(poly))


def inset(poly, d):
    """Offset a polygon inward by d (miter joins). Works for CW or CCW input."""
    if signed_area(poly) < 0:
        poly = list(reversed(poly))
    n = len(poly)
    out = []
    for i in range(n):
        p0, p1, p2 = poly[i - 1], poly[i], poly[(i + 1) % n]
        e1 = (p1[0] - p0[0], p1[1] - p0[1])
        e2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1, l2 = math.hypot(*e1) or 1, math.hypot(*e2) or 1
        n1 = (-e1[1] / l1, e1[0] / l1)          # left normal = inside for CCW
        n2 = (-e2[1] / l2, e2[0] / l2)
        bx, by = n1[0] + n2[0], n1[1] + n2[1]
        bl = math.hypot(bx, by) or 1
        bx, by = bx / bl, by / bl
        cos = bx * n1[0] + by * n1[1] or 1
        out.append([p1[0] + bx * d / cos, p1[1] + by * d / cos])
    return out


def seg_len(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def segments(wall):
    p = wall["path"]
    n = len(p)
    rng = range(n) if wall.get("closed") else range(n - 1)
    return [(p[i], p[(i + 1) % n]) for i in rng]


def compass(dx, dy):
    """Name of the compass direction of vector (dx, dy); +Y is north."""
    ang = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return names[int((ang + 22.5) // 45) % 8]


# ---------------------------------------------------------------- loading
def load(path):
    """Load a spec from a project dir or a .json file and fill defaults."""
    if os.path.isdir(path):
        path = os.path.join(path, "spec.json")
    with open(path) as f:
        spec = json.load(f)
    spec["_dir"] = os.path.dirname(os.path.abspath(path))
    spec.setdefault("name", os.path.basename(spec["_dir"]))
    levels = spec.setdefault("levels", [])
    for i, lv in enumerate(levels):
        lv.setdefault("name", f"L{i}")
        lv.setdefault("elevation", 0.0 if i == 0 else
                      levels[i - 1]["elevation"] + levels[i - 1]["height"])
        lv.setdefault("height", spec.get("level_height", DEFAULTS["level_height"]))
        lv.setdefault("slab", spec.get("slab_thickness", DEFAULTS["slab_thickness"]))
        for j, w in enumerate(lv.setdefault("walls", [])):
            w.setdefault("id", f"{lv['name']}_w{j}")
            w.setdefault("closed", False)
            w.setdefault("thickness", spec.get("wall_thickness", DEFAULTS["wall_thickness"]))
            w.setdefault("height", lv["height"])
            w.setdefault("role", "wall_ext" if w["closed"] else "wall_int")
            if w["closed"]:
                # Closed outlines are always CCW and describe the OUTSIDE face.
                if signed_area(w["path"]) < 0:
                    w["path"] = list(reversed(w["path"]))
                w.setdefault("align", "left")      # left of a CCW path = inside
            else:
                w.setdefault("align", "center")
        for o in lv.setdefault("openings", []):
            kind = o.setdefault("kind", "window")
            for k, v in DEFAULTS["opening"].get(kind, DEFAULTS["opening"]["window"]).items():
                o.setdefault(k, v)
            o.setdefault("segment", 0)
        lv.setdefault("rooms", [])
        lv.setdefault("slab_openings", [])
        lv.setdefault("stairs", [])
    spec.setdefault("roof", {"type": "flat"})
    spec.setdefault("elements", [])
    spec.setdefault("cameras", [])
    spec.setdefault("site", {})
    spec["site"].setdefault("ground_level", -0.15)
    spec["site"].setdefault("margin", 150.0)   # big enough to reach the horizon
    spec.setdefault("sun", {"azimuth": 210, "elevation": 38})
    spec.setdefault("materials", {})
    return spec


def outline(level):
    """The level's footprint: first closed wall path, or an explicit `slab` polygon."""
    if "footprint" in level:
        return level["footprint"]
    for w in level["walls"]:
        if w["closed"]:
            return w["path"]
    return None


def find_wall(spec, level, wall_id):
    for w in level["walls"]:
        if w["id"] == wall_id:
            return w
    return None


def top_of_walls(spec):
    lv = spec["levels"][-1]
    return lv["elevation"] + lv["height"]


def roof_height(spec):
    r = spec["roof"]
    fp = outline(spec["levels"][-1]) or [[0, 0]]
    xs = [p[0] for p in fp]
    ys = [p[1] for p in fp]
    if r.get("type") == "gable":
        span = min(max(xs) - min(xs), max(ys) - min(ys))
        return span / 2 * math.tan(math.radians(r.get("pitch", 30))) + r.get("thickness", 0.25)
    return r.get("thickness", spec["levels"][-1]["slab"]) + r.get("parapet", 0.0)


def bounds(spec, include_elements=True):
    """Axis-aligned bounds of the building: (minx, miny, minz, maxx, maxy, maxz)."""
    xs, ys = [], []
    for lv in spec["levels"]:
        for w in lv["walls"]:
            for x, y in w["path"]:
                xs.append(x)
                ys.append(y)
    if include_elements:
        for e in spec["elements"]:
            if e.get("type") == "box":
                cx, cy, _ = e["center"]
                sx, sy, _ = e["size"]
                xs += [cx - sx / 2, cx + sx / 2]
                ys += [cy - sy / 2, cy + sy / 2]
            elif e.get("type") == "prism":
                for x, y in e["polygon"]:
                    xs.append(x)
                    ys.append(y)
    ov = spec["roof"].get("overhang", 0.0)
    zmin = spec["site"]["ground_level"]
    zmax = top_of_walls(spec) + roof_height(spec)
    return (min(xs) - ov, min(ys) - ov, zmin, max(xs) + ov, max(ys) + ov, zmax)


# ---------------------------------------------------------------- lint
def lint(spec):
    """Return a list of (severity, message). Severity: 'error' | 'warn'."""
    out = []
    E = lambda m: out.append(("error", m))
    W = lambda m: out.append(("warn", m))
    if not spec["levels"]:
        E("spec has no levels")
    ids = set()
    for lv in spec["levels"]:
        name = lv["name"]
        for w in lv["walls"]:
            if w["id"] in ids:
                E(f"{name}: duplicate wall id '{w['id']}'")
            ids.add(w["id"])
            if len(w["path"]) < 2:
                E(f"{name}/{w['id']}: path needs at least 2 points")
            if w["closed"] and area(w["path"]) < 0.01:
                E(f"{name}/{w['id']}: closed outline has ~zero area")
            if w["thickness"] < 0.05:
                W(f"{name}/{w['id']}: wall thinner than 5 cm")
        if outline(lv) is None:
            W(f"{name}: no closed exterior wall, so no floor slab will be built")

        per_seg = {}
        for k, o in enumerate(lv["openings"]):
            tag = f"{name}/opening[{k}] ({o['kind']} on {o.get('wall')})"
            w = find_wall(spec, lv, o.get("wall"))
            if w is None:
                E(f"{tag}: wall id not found")
                continue
            segs = segments(w)
            if not 0 <= o["segment"] < len(segs):
                E(f"{tag}: segment {o['segment']} out of range (wall has {len(segs)})")
                continue
            L = seg_len(*segs[o["segment"]])
            corner = w["thickness"] if w["closed"] else 0.0
            if o["offset"] < corner - 1e-6 or o["offset"] + o["width"] > L - corner + 1e-6:
                E(f"{tag}: runs past the wall ends/corners (segment length {L:.2f} m, "
                  f"usable {corner:.2f}..{L - corner:.2f})")
            top = o["sill"] + o["height"]
            limit = w["height"]
            if top > limit - 0.05:
                E(f"{tag}: top at {top:.2f} m reaches the wall top ({limit:.2f} m)")
            elif top > limit - lv.get("slab", 0.25) - 0.1:
                W(f"{tag}: head at {top:.2f} m leaves under 10 cm below the slab above")
            if o["kind"] == "door" and o["sill"] > 0.02:
                W(f"{tag}: door with a sill of {o['sill']} m")
            if o["kind"] == "door" and o["height"] < 2.0:
                W(f"{tag}: door lower than 2.0 m")
            per_seg.setdefault((w["id"], o["segment"]), []).append((o["offset"], o["offset"] + o["width"], tag))
        for spans in per_seg.values():
            spans.sort()
            for (a0, a1, ta), (b0, b1, tb) in zip(spans, spans[1:]):
                if b0 < a1 + 0.1:
                    E(f"overlap / less than 10 cm pier: {ta} and {tb}")

        for s in lv["stairs"]:
            rise = s.get("rise", lv["height"])
            n = max(1, round(rise / s.get("riser", 0.175)))
            r = rise / n
            t = s.get("tread", 0.28)
            blondel = 2 * r + t
            if r > 0.19:
                W(f"{name}: stair riser {r*100:.1f} cm is steep (>19 cm)")
            if not 0.60 <= blondel <= 0.65:
                W(f"{name}: stair 2R+T = {blondel:.3f} m (comfortable range 0.60-0.65)")
            if s.get("width", 1.0) < 0.9:
                W(f"{name}: stair narrower than 90 cm")
    return out


# ---------------------------------------------------------------- report
def report(spec):
    """Areas, facade glazing ratios and counts. Returns a dict."""
    rep = {"name": spec["name"], "levels": [], "facades": {}, "totals": {}}
    gfa_total = 0.0
    n_win = n_door = 0
    for lv in spec["levels"]:
        fp = outline(lv)
        gfa = area(fp) if fp else 0.0
        gfa_total += gfa
        rooms = [{"name": r["name"], "area": round(area(r["polygon"]), 2)} for r in lv["rooms"]]
        rep["levels"].append({
            "name": lv["name"], "elevation": lv["elevation"], "height": lv["height"],
            "gross_area": round(gfa, 2), "net_room_area": round(sum(r["area"] for r in rooms), 2),
            "rooms": rooms,
        })
        for w in lv["walls"]:
            if not w["closed"]:
                continue
            for i, (a, b) in enumerate(segments(w)):
                L = seg_len(a, b)
                dx, dy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
                face = compass(dy, -dx)           # outward normal of a CCW outline
                f = rep["facades"].setdefault(face, {"wall_area": 0.0, "glazed_area": 0.0})
                f["wall_area"] += L * w["height"]
                for o in lv["openings"]:
                    if o.get("wall") == w["id"] and o["segment"] == i and o["kind"] != "door":
                        f["glazed_area"] += o["width"] * o["height"]
        for o in lv["openings"]:
            n_win += o["kind"] == "window"
            n_door += o["kind"] == "door"
    for f in rep["facades"].values():
        f["wwr_percent"] = round(100 * f["glazed_area"] / f["wall_area"], 1) if f["wall_area"] else 0
        f["wall_area"] = round(f["wall_area"], 2)
        f["glazed_area"] = round(f["glazed_area"], 2)
    b = bounds(spec, include_elements=False)
    rep["totals"] = {
        "gross_floor_area": round(gfa_total, 2),
        "windows": n_win, "doors": n_door,
        "building_height": round(b[5], 2),
        "footprint_bbox": [round(b[3] - b[0], 2), round(b[4] - b[1], 2)],
    }
    return rep


def report_markdown(rep, issues=()):
    L = [f"# {rep['name']}: model report", ""]
    t = rep["totals"]
    L += [f"- Gross floor area: **{t['gross_floor_area']} m²**",
          f"- Height: {t['building_height']} m; footprint box {t['footprint_bbox'][0]} × {t['footprint_bbox'][1]} m",
          f"- Windows: {t['windows']}, doors: {t['doors']}", ""]
    L += ["| Level | Elev. (m) | Gross m² | Net rooms m² |", "|---|---|---|---|"]
    for lv in rep["levels"]:
        L.append(f"| {lv['name']} | {lv['elevation']} | {lv['gross_area']} | {lv['net_room_area']} |")
    L += ["", "| Room | Level | m² |", "|---|---|---|"]
    for lv in rep["levels"]:
        for r in lv["rooms"]:
            L.append(f"| {r['name']} | {lv['name']} | {r['area']} |")
    L += ["", "| Facade | Wall m² | Glazed m² | WWR % |", "|---|---|---|---|"]
    for k in sorted(rep["facades"]):
        f = rep["facades"][k]
        L.append(f"| {k} | {f['wall_area']} | {f['glazed_area']} | {f['wwr_percent']} |")
    if issues:
        L += ["", "## Lint", ""] + [f"- **{s}**: {m}" for s, m in issues]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    import sys
    s = load(sys.argv[1])
    issues = lint(s)
    print(report_markdown(report(s), issues))
    sys.exit(1 if any(sv == "error" for sv, _ in issues) else 0)

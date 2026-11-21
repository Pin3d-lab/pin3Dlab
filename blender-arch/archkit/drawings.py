"""Vector drawings from the built model: plans, sections and elevations.

For every view we take the evaluated meshes (booleans applied, bevels off) and
  1. cut them with the section plane  -> closed loops, drawn as filled poché + thick outline
  2. collect feature edges beyond the cut, test their visibility by ray casting
     (hidden-line removal) and grade them: profile (depth jump) vs. plain edge
Spec-driven symbols (door swings, window lines, stairs, dimensions, labels, level
markers) are added by symbols.py; vector.py lays everything out on a sheet at true
scale and writes SVG + PDF.
"""
import math
import os

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import spec as S
from . import symbols as Y
from . import vector as V

SKIP = {"cutter", "label", "ground", "tree", None}
PLAN_SKIP = {"frame", "glass", "door", "stair", "floor"}     # replaced by plan symbols
LEVEL_TITLES = {"GF": "Ground floor", "FF": "First floor", "SF": "Second floor", "TF": "Third floor",
                "B1": "Basement", "L0": "Level 0", "L1": "Level 1", "L2": "Level 2", "RF": "Roof"}


# ---------------------------------------------------------------- view definitions
def _look(look, p0=None, p1=None):
    if isinstance(look, (list, tuple)):
        v = Vector((look[0], look[1], 0))
    else:
        v = {"north": Vector((0, 1, 0)), "south": Vector((0, -1, 0)), "east": Vector((1, 0, 0)),
             "west": Vector((-1, 0, 0))}.get(str(look).lower())
    if v is None:       # default: left of the from->to direction
        t = Vector((p1[0] - p0[0], p1[1] - p0[1], 0)).normalized()
        v = Vector((-t.y, t.x, 0))
    return v.normalized()


def sections(spec):
    """Spec sections, or one longitudinal + one transverse through the centre."""
    b = S.bounds(spec, include_elements=False)
    out = []
    secs = spec.get("sections")
    if secs is None:
        cx, cy = (b[0] + b[3]) / 2, (b[1] + b[4]) / 2
        walls = [w for lv in spec["levels"] for w in lv["walls"] if not w["closed"]]

        def clear(val, axis):   # keep the default cut off interior walls lying in the plane
            for w in walls:
                for p, q in S.segments(w):
                    if abs(p[axis] - q[axis]) < 1e-6 and abs(p[axis] - val) < w["thickness"] / 2 + 0.2:
                        return False
            return True
        while not clear(cy, 1):
            cy += 0.45
        while not clear(cx, 0):
            cx += 0.45
        long_x = (b[3] - b[0]) >= (b[4] - b[1])
        A = {"name": "AA", "from": [b[0] - 1.5, cy], "to": [b[3] + 1.5, cy], "look": "north"}
        B = {"name": "BB", "from": [cx, b[1] - 1.5], "to": [cx, b[4] + 1.5], "look": "west"}
        secs = [A, B] if long_x else [B, A]
    for s in secs:
        s = dict(s)
        D = _look(s.get("look"), s["from"], s["to"])
        s["dir2"] = (D.x, D.y)
        out.append(s)
    return out


def views(spec):
    """{name: view dict}. Frame: u = (P-O).R, v = (P-O).U, depth = (P-O).D."""
    out = {}
    cut_h = spec.get("plan_cut_height", 1.2)
    for lv in spec["levels"]:
        z = lv["elevation"] + cut_h + 1.37e-4          # nudge off exact sill/head heights
        title = lv.get("title") or f"{LEVEL_TITLES.get(lv['name'], lv['name'])} plan"
        out[f"plan_{lv['name']}"] = {
            "kind": "plan", "title": title, "level": lv, "O": Vector((0, 0, z)),
            "R": Vector((1, 0, 0)), "U": Vector((0, 1, 0)), "D": Vector((0, 0, -1)),
            "cut": 0.0, "depth": cut_h + lv["slab"] + 0.3}
    for s in sections(spec):
        D = Vector((s["dir2"][0], s["dir2"][1], 0))
        R = D.cross(Vector((0, 0, 1)))
        out[f"section_{s['name']}"] = {
            "kind": "section", "title": s.get("title", f"Section {s['name'][0]}-{s['name'][-1]}"),
            "O": Vector((0, 0, 0)), "R": R, "U": Vector((0, 0, 1)), "D": D,
            "cut": Vector((*s["from"], 0)).dot(D) + 1.37e-4, "depth": s.get("depth", 200.0), "sec": s}
    for name, d in (("south", (0, 1)), ("east", (-1, 0)), ("north", (0, -1)), ("west", (1, 0))):
        D = Vector((*d, 0))
        out[f"elev_{name}"] = {
            "kind": "elevation", "title": f"{name.capitalize()} elevation", "O": Vector((0, 0, 0)),
            "R": D.cross(Vector((0, 0, 1))), "U": Vector((0, 0, 1)), "D": D, "cut": None, "depth": 500.0}
    return out


# ---------------------------------------------------------------- mesh access
def _objects(kind):
    obs = []
    for ob in bpy.data.objects:
        if ob.type != "MESH" or ob.get("role") in SKIP or ob.hide_render:
            continue
        if kind == "plan" and ob.get("role") in PLAN_SKIP:
            continue
        obs.append(ob)
    return obs


def _mesh_arrays(ob, dg):
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    mw = np.array(ob.matrix_world, dtype=np.float64)
    nv, ne, npl, nl = len(me.vertices), len(me.edges), len(me.polygons), len(me.loops)
    co = np.empty(nv * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
    me.calc_loop_triangles()
    tris = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", tris)
    edges = np.empty(ne * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", edges)
    pn = np.empty(npl * 3)
    me.polygons.foreach_get("normal", pn)
    lt = np.empty(npl, dtype=np.int64)
    me.polygons.foreach_get("loop_total", lt)
    le = np.empty(nl, dtype=np.int64)
    me.loops.foreach_get("edge_index", le)
    ev.to_mesh_clear()
    pn = pn.reshape(-1, 3) @ np.linalg.inv(mw[:3, :3])     # normals: inverse-transpose
    pn /= np.linalg.norm(pn, axis=1, keepdims=True) + 1e-12
    return co, tris.reshape(-1, 3), edges.reshape(-1, 2), pn, le, np.repeat(np.arange(npl), lt)


def _feature_edges(edges, pn, le, pol, angle=8.0):
    if len(edges) == 0:
        return edges
    order = np.argsort(le, kind="stable")
    pl_s = pol[order]
    counts = np.bincount(le, minlength=len(edges))
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    feat = (counts == 1) | (counts > 2)
    two = np.where(counts == 2)[0]
    if len(two):
        i0 = starts[two]
        dots = (pn[pl_s[i0]] * pn[pl_s[i0 + 1]]).sum(1)
        feat[two[dots < math.cos(math.radians(angle))]] = True
    return edges[feat]


def _cut_loops(co, tris, sd, proj):
    """Intersect triangles with the plane sd == 0. Returns closed (u, v) loops.
    Crossing points are keyed by their mesh edge, so loops chain exactly."""
    st = sd[tris]
    m = (st.min(1) < 0) & (st.max(1) > 0)
    if not m.any():
        return []
    T = tris[m]
    adj = {}
    pts = {}
    for tri in T:
        nodes = []
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            if (sd[a] < 0) != (sd[b] < 0):
                a, b = (a, b) if a < b else (b, a)
                key = (int(a), int(b))
                if key not in pts:
                    t = sd[a] / (sd[a] - sd[b])
                    pts[key] = proj(co[a] + (co[b] - co[a]) * t)
                nodes.append(key)
        if len(nodes) == 2 and nodes[0] != nodes[1]:
            adj.setdefault(nodes[0], []).append(nodes[1])
            adj.setdefault(nodes[1], []).append(nodes[0])
    loops, seen = [], set()
    for start in adj:
        if start in seen:
            continue
        loop, prev, cur = [start], None, start
        seen.add(start)
        while True:
            nxt = [n for n in adj[cur] if n != prev and (n not in seen or (n == start and len(loop) > 2))]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            if cur == start:
                break
            seen.add(cur)
            loop.append(cur)
        if len(loop) >= 3:
            loops.append([pts[k] for k in loop])
    return loops


# ---------------------------------------------------------------- extraction
def extract(view, step=0.025):
    """Return {"poche": [loops per object], "lines": [(p0, p1, pen)], "umax", "umin"}."""
    kind = view["kind"]
    O, R, U, D = view["O"], view["R"], view["U"], view["D"]
    cut, depth = view["cut"], view["depth"]
    bevels = []
    for ob in bpy.data.objects:
        for m in ob.modifiers:
            if m.type == "BEVEL" and m.show_viewport:
                m.show_viewport = False
                bevels.append(m)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    On, Rn, Un, Dn = (np.array(v[:]) for v in (O, R, U, D))

    data = []
    for ob in _objects(kind):
        co, tris, edges, pn, le, pol = _mesh_arrays(ob, dg)
        if len(co) == 0:
            continue
        dep = (co - On) @ Dn
        lo = cut if cut is not None else -1e9
        if dep.max() < lo or (cut is not None and dep.min() > cut + depth):
            continue
        data.append((ob, co, tris, edges, pn, le, pol, dep))
    for m in bevels:
        m.show_viewport = True
    if not data:
        return {"poche": [], "lines": [], "glass": [], "umin": 0, "umax": 1}

    front = cut if cut is not None else min(d[7].min() for d in data) - 1.0
    back = (cut if cut is not None else front) + depth
    # BVH of everything that can hide an edge
    allv, allt, base = [], [], 0
    for _, co, tris, *_rest in data:
        allv.append(co)
        allt.append(tris + base)
        base += len(co)
    V_all = np.concatenate(allv)
    T_all = np.concatenate(allt)
    bvh = BVHTree.FromPolygons([tuple(v) for v in V_all], [tuple(t) for t in T_all], epsilon=0.0)

    def uv(p):
        q = p - On
        return (float(q @ Rn), float(q @ Un))

    # 1. cut loops
    poche = []
    if cut is not None:
        for ob, co, tris, edges, pn, le, pol, dep in data:
            loops = _cut_loops(co, tris, dep - cut, uv)
            if loops:
                poche.append(loops)

    # 2. visible feature edges beyond the cut
    tol = 0.012
    Dv = Vector(D[:])

    def hidden(P, dP, perp):
        dist = dP - front - tol
        if dist <= 0:
            return False
        for off in (0.0, 0.004, -0.004):
            o = Vector(P - Dn * (dP - front)) + perp * off
            if bvh.ray_cast(o, Dv, dist)[0] is None:
                return False
        return True

    def hit(P, dP, perp, off):
        o = Vector(P - Dn * (dP - front)) + perp * off
        h = bvh.ray_cast(o, Dv, back - front)
        return (h[3], h[1]) if h[0] is not None else (None, None)

    lines = []
    for ob, co, tris, edges, pn, le, pol, dep in data:
        for a, b in _feature_edges(edges, pn, le, pol):
            pa, pb, da, db = co[a], co[b], dep[a], dep[b]
            t0, t1 = 0.0, 1.0
            for lim, sign in ((front, 1), (back, -1)):     # clip to front <= depth <= back
                fa, fb = (da - lim) * sign, (db - lim) * sign
                if fa < 0 and fb < 0:
                    t0, t1 = 1, 0
                    break
                if fa < 0:
                    t0 = max(t0, fa / (fa - fb))
                elif fb < 0:
                    t1 = min(t1, fa / (fa - fb))
            if t1 - t0 <= 1e-6:
                continue
            ua, ub = uv(pa), uv(pb)
            du, dv = ub[0] - ua[0], ub[1] - ua[1]
            L = math.hypot(du, dv) * (t1 - t0)
            if L < 1e-4:
                continue
            perp = (Vector(R[:]) * (-dv) + Vector(U[:]) * du).normalized()
            n = max(1, int(L / step) + 1)
            vis = []
            for i in range(n):
                t = t0 + (t1 - t0) * (i + 0.5) / n
                vis.append(not hidden(pa + (pb - pa) * t, da + (db - da) * t, perp))
            i = 0
            while i < n:
                if not vis[i]:
                    i += 1
                    continue
                j = i
                while j + 1 < n and vis[j + 1]:
                    j += 1
                s0 = t0 + (t1 - t0) * i / n
                s1 = t0 + (t1 - t0) * (j + 1) / n
                tm = (s0 + s1) / 2
                Pm, dm = pa + (pb - pa) * tm, da + (db - da) * tm
                (h1, n1), (h2, n2) = hit(Pm, dm, perp, 0.03), hit(Pm, dm, perp, -0.03)
                i = j + 1
                if h1 is not None and h2 is not None and abs(h1 - h2) < 0.004 and abs(n1.dot(n2)) > 0.9999:
                    continue        # seam between coplanar faces (e.g. storey joints in a render)
                prof = (h1 is None) or (h2 is None) or abs(h1 - h2) > 0.5
                p0 = (ua[0] + du * s0, ua[1] + dv * s0)
                p1 = (ua[0] + du * s1, ua[1] + dv * s1)
                lines.append((p0, p1, "profile" if prof else ("visible" if kind != "section" else "beyond")))
    glass = []
    if kind != "plan":
        for ob, co, tris, edges, pn, le, pol, dep in data:
            if ob.get("role") != "glass":
                continue
            q = co - On
            uu, vv = q @ Rn, q @ Un
            if np.ptp(uu) < 0.05 or np.ptp(vv) < 0.05:
                continue            # glass seen edge-on
            dmin = dep.min()
            lo3, hi3 = co.min(0), co.max(0)
            probes = [lo3 + (hi3 - lo3) * f for f in (0.5, 0.27, 0.73)] + \
                     [lo3 + (hi3 - lo3) * np.array(f) for f in ((0.27, 0.27, 0.73), (0.73, 0.73, 0.27))]
            if all(hidden(c - Dn * (c @ Dn - dmin), dmin, Vector((0, 0, 0))) for c in probes):
                continue
            glass.append([(uu.min(), vv.min()), (uu.max(), vv.min()), (uu.max(), vv.max()), (uu.min(), vv.max())])
    us = [p[0] for p0, p1, _ in lines for p in (p0, p1)] + [p[0] for lps in poche for lp in lps for p in lp]
    return {"poche": poche, "lines": lines, "glass": glass, "umin": min(us) if us else 0, "umax": max(us) if us else 1}


def _clip_rect(p0, p1, r):
    """Liang-Barsky clip of a segment to rect (u0, v0, u1, v1)."""
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - r[0]), (dx, r[2] - x0), (-dy, y0 - r[1]), (dy, r[3] - y0)):
        if abs(p) < 1e-12:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
    if t0 > t1:
        return None
    return (x0 + dx * t0, y0 + dy * t0), (x0 + dx * t1, y0 + dy * t1)


def _clip_poly(poly, r):
    """Sutherland-Hodgman clip of a polygon to rect (u0, v0, u1, v1)."""
    for axis, lim, keep_gt in ((0, r[0], True), (0, r[2], False), (1, r[1], True), (1, r[3], False)):
        out = []
        for k, cur in enumerate(poly):
            prev = poly[k - 1]
            inside = lambda p: (p[axis] >= lim) if keep_gt else (p[axis] <= lim)
            if inside(cur):
                if not inside(prev):
                    t = (lim - prev[axis]) / (cur[axis] - prev[axis])
                    out.append(tuple(prev[i] + (cur[i] - prev[i]) * t for i in (0, 1)))
                out.append(cur)
            elif inside(prev):
                t = (lim - prev[axis]) / (cur[axis] - prev[axis])
                out.append(tuple(prev[i] + (cur[i] - prev[i]) * t for i in (0, 1)))
        poly = out
        if not poly:
            break
    return poly


def crop_rect(spec, view, margin=None):
    """Drawing extent: the building plus a margin of site (default 3 m), ground and up for
    sections/elevations."""
    m = spec.get("drawing_margin", 3.0) if margin is None else margin
    b = S.bounds(spec, include_elements=False)
    # grow to include elements lying close to the building (terraces, canopies), not long paths
    lim = 6.0
    for e in spec["elements"]:
        eb = S.bounds(dict(spec, elements=[e], levels=spec["levels"]), include_elements=True)
        if eb[0] > b[0] - lim and eb[1] > b[1] - lim and eb[3] < b[3] + lim and eb[4] < b[4] + lim:
            b = (min(b[0], eb[0]), min(b[1], eb[1]), b[2], max(b[3], eb[3]), max(b[4], eb[4]), b[5])
    O, R, U = (np.array(v[:]) for v in (view["O"], view["R"], view["U"]))
    pts = [np.array((x, y, z)) for x in (b[0], b[3]) for y in (b[1], b[4]) for z in (b[2], b[5])]
    us = [(p - O) @ R for p in pts]
    vs = [(p - O) @ U for p in pts]
    if view["kind"] == "plan":
        return (min(us) - m, min(vs) - m, max(us) + m, max(vs) + m)
    return (min(us) - m, spec["site"]["ground_level"], max(us) + m, max(vs) + 50)


# ---------------------------------------------------------------- drawing assembly
def build_drawing(spec, name, view, geo, scale, secs):
    kind = view["kind"]
    dr = V.Drawing(name, view["title"], kind)
    g = spec["site"]["ground_level"]
    crop = crop_rect(spec, view)
    for poly in geo.get("glass", []):
        poly = _clip_poly(poly, crop)
        if len(poly) >= 3:
            dr.poly_fill(poly, "glass")
    for p0, p1, pen in geo["lines"]:
        seg = _clip_rect(p0, p1, crop)
        if seg is None:
            continue
        dr.line(seg, pen)
    for loops in geo["poche"]:
        loops = [_clip_poly(lp, crop) for lp in loops]
        dr.poche([lp for lp in loops if len(lp) >= 3])
    if kind == "plan":
        lv = view["level"]
        dr.north = spec.get("north", 0.0)
        Y.plan(dr, spec, lv, view["O"].z, scale, secs)
    else:
        u0 = max(geo["umin"], crop[0])
        u1 = min(geo["umax"], crop[2])
        ext = Y.mm(scale, 12)
        dr.line([(u0 - ext, g), (u1 + ext, g)], "ground")
        Y.level_markers(dr, spec, u1 + ext, scale)
    return dr


def make_sheets(spec, names, out_dir, sheet="A3", scale=100, formats=("svg", "pdf")):
    """Render the named drawing views to sheets. Returns list of written files."""
    os.makedirs(out_dir, exist_ok=True)
    allv = views(spec)
    secs = sections(spec)
    meta = dict(spec.get("project", {}))
    meta.setdefault("project", spec.get("title", spec["name"].replace("_", " ").title()))
    meta.setdefault("description", spec.get("description", ""))
    pages, files = [], []
    numbers = {"plan": 100, "section": 200, "elevation": 300}
    for name in names:
        view = allv.get(name)
        if view is None:
            print(f"[archkit] unknown drawing '{name}', available: {', '.join(allv)}")
            continue
        geo = extract(view)
        sc = V.pick_scale(lambda s: build_drawing(spec, name, view, geo, s, secs).bounds(), sheet, scale)
        if sc != scale:
            print(f"[archkit] {name}: does not fit {sheet} at 1:{scale}, using 1:{sc}")
        dr = build_drawing(spec, name, view, geo, sc, secs)
        numbers[view["kind"]] += 1
        page = V.compose(dr, sheet, sc, meta, number=f"A-{numbers[view['kind']]}")
        pages.append(page)
        base = os.path.join(out_dir, name)
        if "svg" in formats:
            files.append(V.write_svg(page, base + ".svg"))
        if "pdf" in formats:
            files.append(V.write_pdf([page], base + ".pdf", page.title))
        print(f"[archkit] drawing {name}: 1:{sc} on {sheet}, {len(geo['lines'])} lines, "
              f"{sum(len(p) for p in geo['poche'])} cut loops")
    if len(pages) > 1 and "pdf" in formats:
        p = os.path.join(out_dir, f"{spec['name']}_drawings.pdf")
        files.append(V.write_pdf(pages, p, f"{meta['project']} drawings"))
        print(f"[archkit] drawing set: {p} ({len(pages)} sheets)")
    return files

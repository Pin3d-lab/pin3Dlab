"""Drawing annotations computed from the spec (pure Python, no bpy):
door swings, window lines, stair symbols, dimension chains, room labels,
overhead outlines, section marks (plans) and level markers (sections/elevations).

Sizes given in paper millimetres are converted to metres with mm(scale, x)."""
import math

from . import spec as S


def mm(scale, x):
    return x * scale / 1000.0


def _add(a, b, k=1.0):
    return (a[0] + b[0] * k, a[1] + b[1] * k)


def _arc(c, r, a0, a1, n=24):
    return [(c[0] + r * math.cos(a0 + (a1 - a0) * i / n), c[1] + r * math.sin(a0 + (a1 - a0) * i / n))
            for i in range(n + 1)]


def _seg(wall, i):
    """Segment frame: start a, unit dir d, left normal n, faces (lo, hi) as offsets along n."""
    a, b = S.segments(wall)[i]
    L = S.seg_len(a, b)
    d = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
    n = (-d[1], d[0])
    t = wall["thickness"]
    lo, hi = {"left": (0.0, t), "right": (-t, 0.0), "center": (-t / 2, t / 2)}[wall["align"]]
    return a, d, n, L, lo, hi


def _P(a, d, n, s, o):
    return (a[0] + d[0] * s + n[0] * o, a[1] + d[1] * s + n[1] * o)


def readable_angle(dx, dy):
    ang = math.degrees(math.atan2(dy, dx))
    if ang > 90.001 or ang <= -89.999:
        ang += 180.0 if ang <= -89.999 else -180.0
    return ang


# ---------------------------------------------------------------- plan symbols
def openings(dr, spec, lv, cut_z):
    walls = {w["id"]: w for w in lv["walls"]}
    zc = cut_z - lv["elevation"]
    for o in lv["openings"]:
        w = walls.get(o.get("wall"))
        if w is None:
            continue
        a, d, n, L, lo, hi = _seg(w, o["segment"])
        s0, s1 = o["offset"], o["offset"] + o["width"]
        cut_through = o["sill"] < zc < o["sill"] + o["height"]
        if not cut_through:
            continue
        if o["kind"] == "window":
            for off in (lo, hi):
                dr.line([_P(a, d, n, s0, off), _P(a, d, n, s1, off)], "thin")
            mid = (lo + hi) / 2 + (o.get("inset", 0.0) if w["closed"] else 0.0) * (-1)
            for off in (mid - 0.025, mid + 0.025):
                dr.line([_P(a, d, n, s0, off), _P(a, d, n, s1, off)], "symbol")
        elif o["kind"] == "door":
            swing = o.get("swing", "in" if w["closed"] else "left")
            side = +1 if swing in ("in", "left") else -1   # +1: towards the wall's left normal
            if w["closed"] and swing == "out":
                side = -1
            face = hi if side > 0 else lo
            hinge_start = o.get("hinge", "start") == "start"
            sh, so = (s0, s1) if hinge_start else (s1, s0)
            fp = 0.05                                       # frame/stop allowance
            sh += fp if hinge_start else -fp
            so -= fp if hinge_start else -fp
            r = abs(so - sh)
            H = _P(a, d, n, sh, face)
            dd = (d[0] * (1 if hinge_start else -1), d[1] * (1 if hinge_start else -1))
            nn = (n[0] * side, n[1] * side)
            T = _add(H, nn, r)
            lt = 0.04
            dr.line([H, T, _add(T, dd, lt), _add(H, dd, lt)], "leaf", closed=True)
            a_open = math.atan2(nn[1], nn[0])
            a_closed = math.atan2(dd[1], dd[0])
            da = (a_closed - a_open + math.pi) % (2 * math.pi) - math.pi
            dr.line(_arc(H, r, a_open, a_open + da), "thin")
        else:  # plain opening: show the head above as an overhead line
            for off in (lo, hi):
                dr.line([_P(a, d, n, s0, off), _P(a, d, n, s1, off)], "hidden")


def stair_symbol(dr, spec, lv, s, cut_rel, scale, full=False):
    rise = s.get("rise", lv["height"])
    nst = max(1, round(rise / s.get("riser", 0.175)))
    r = rise / nst
    t = s.get("tread", 0.28)
    wdt = s.get("width", 1.0)
    ang = math.radians(s.get("direction", 0.0))
    d = (math.cos(ang), math.sin(ang))
    n = (-d[1], d[0])
    a = tuple(s["start"])
    P = lambda x, y: _P(a, d, n, x, y)
    length = nst * t
    if full:
        xc = length
    else:
        k = sum(1 for i in range(nst) if (i + 1) * r <= cut_rel)    # steps fully below the cut
        xc = min(length, k * t + 0.5 * t)
    for i in range(nst + 1):
        x = i * t
        dr.line([P(x, 0), P(x, wdt)], "symbol" if x <= xc + 1e-6 else "hidden")
    dr.line([P(0, 0), P(xc, 0)], "symbol")
    dr.line([P(0, wdt), P(xc, wdt)], "symbol")
    if xc < length:
        dr.line([P(xc, 0), P(length, 0)], "hidden")
        dr.line([P(xc, wdt), P(length, wdt)], "hidden")
        # break line across the flight where the plan cut passes
        dr.line([P(xc - 0.15, -0.05), P(xc + 0.05, wdt * 0.45), P(xc - 0.05, wdt * 0.55),
                 P(xc + 0.15, wdt + 0.05)], "visible")
    # walking line with start dot and arrow towards the top
    ym = wdt / 2
    end = (xc - 0.1) if not full else length - 0.05
    dr.line([P(0.12, ym), P(end, ym)], "thin")
    dr.line([P(0.12 + 0.06 * math.cos(q * math.pi / 8), ym + 0.06 * math.sin(q * math.pi / 8))
             for q in range(17)], "thin", closed=True)
    ah, aw = mm(scale, 2.5), mm(scale, 1.0)
    dr.line([P(end, ym), P(end - ah, ym + aw), P(end - ah, ym - aw)], "solid", closed=True, fill=0.0)
    lab = "UP" if not full else "DN"
    tp = P(0.15, ym - mm(scale, 2.6)) if not full else P(length - 0.3, ym - mm(scale, 2.6))
    dr.text(tp, lab, 2.0, "start" if not full else "end", rot=readable_angle(*d), halo=True)
    dr.text(P((xc if not full else length) / 2 + 0.1, ym + mm(scale, 2.4)),
            f"{nst} x {r * 100:.1f} / {t * 100:.0f}", 1.6, "middle", rot=readable_angle(*d), halo=True)


def stairs(dr, spec, lv, cut_z, scale):
    for s in lv["stairs"]:
        stair_symbol(dr, spec, lv, s, cut_z - lv["elevation"], scale)
    # flights arriving from the level below: seen in full through the stair void
    i = spec["levels"].index(lv)
    if i > 0:
        below = spec["levels"][i - 1]
        for s in below["stairs"]:
            if below["elevation"] + s.get("rise", below["height"]) >= lv["elevation"] - 0.05:
                stair_symbol(dr, spec, below, s, 99, scale, full=True)


def room_labels(dr, spec, lv):
    for r in lv["rooms"]:
        poly = r["polygon"]
        A = S.signed_area(poly)
        if abs(A) < 1e-6:
            continue
        cx = cy = 0.0
        for k, (x1, y1) in enumerate(poly):
            x2, y2 = poly[(k + 1) % len(poly)]
            c = x1 * y2 - x2 * y1
            cx += (x1 + x2) * c
            cy += (y1 + y2) * c
        cx, cy = cx / (6 * A), cy / (6 * A)
        cx, cy = r.get("label_at", (cx, cy))
        dr.text((cx, cy), f"{r['name']}\n{abs(A):.1f} m²", 2.5 if len(r["name"]) < 18 else 2.0,
                "middle", halo=True)


def dimensions(dr, spec, lv, scale):
    """Two chains outside every exterior wall segment: openings, then overall."""
    for w in [w for w in lv["walls"] if w["closed"]]:
        for i, (pa, pb) in enumerate(S.segments(w)):
            a, d, n, L, lo, hi = _seg(w, i)
            if L < 0.5:
                continue
            outer = lo if w["align"] == "left" else hi
            out = (-n[0], -n[1]) if w["align"] == "left" else n
            ticks = {0.0, L}
            for o in lv["openings"]:
                if o.get("wall") == w["id"] and o["segment"] == i:
                    ticks |= {o["offset"], o["offset"] + o["width"]}
            chains = [sorted(ticks), [0.0, L]]
            if len(chains[0]) == 2:
                chains = chains[1:]
            for c_i, chain in enumerate(chains):
                off = mm(scale, 9 + 7 * c_i)
                base = _add(_P(a, d, n, 0, outer), out, off)
                Q = lambda s: _add(base, d, s)
                dr.line([Q(chain[0]), Q(chain[-1])], "dim")
                for s in chain:
                    p_wall = _add(_P(a, d, n, s, outer), out, mm(scale, 2.0))
                    dr.line([p_wall, _add(Q(s), out, mm(scale, 1.5))], "dim")
                    tk = mm(scale, 1.3)
                    dd = ((d[0] + out[0]) * tk, (d[1] + out[1]) * tk)
                    dr.line([_add(Q(s), dd, -1), _add(Q(s), dd, 1)], "tick")
                rot = readable_angle(*d)
                ra = math.radians(rot)
                up = (-math.sin(ra), math.cos(ra))
                for s0, s1 in zip(chain, chain[1:]):
                    txt = f"{s1 - s0:.2f}"
                    p = _add(Q((s0 + s1) / 2), up, mm(scale, 1.0))
                    dr.text(p, txt, 2.0, "middle", rot=rot, valign="baseline")


def overhead(dr, spec, lv, cut_z):
    top = lv["elevation"] + lv["height"]
    for e in spec["elements"]:
        if e["type"] == "box":
            cx, cy, cz = e["center"]
            sx, sy, sz = e["size"]
            z0 = cz - sz / 2
            rot = math.radians(e.get("rotation", 0))
            c, s_ = math.cos(rot), math.sin(rot)
            poly = [(cx + x * c - y * s_, cy + x * s_ + y * c)
                    for x, y in ((-sx / 2, -sy / 2), (sx / 2, -sy / 2), (sx / 2, sy / 2), (-sx / 2, sy / 2))]
        elif e["type"] == "prism":
            z0 = e["z"][0]
            poly = [tuple(p) for p in e["polygon"]]
        else:
            continue
        if cut_z < z0 < top:
            dr.line(poly, "overhead", closed=True)
    if lv is spec["levels"][-1]:
        ov = spec["roof"].get("overhang", 0.0)
        fp = S.outline(lv)
        if ov and fp:
            xs, ys = [p[0] for p in fp], [p[1] for p in fp]
            dr.line([(min(xs) - ov, min(ys) - ov), (max(xs) + ov, min(ys) - ov),
                     (max(xs) + ov, max(ys) + ov), (min(xs) - ov, max(ys) + ov)], "overhead", closed=True)


def section_marks(dr, spec, sections, scale):
    """Thin dash-dot cut line with heavy ends, arrows (look direction) and letters,
    pushed outside the dimension chains."""
    b = S.bounds(spec, include_elements=False)
    corners = [(x, y) for x in (b[0], b[3]) for y in (b[1], b[4])]
    for sec in sections:
        p0, p1 = sec["from"], sec["to"]
        L = S.seg_len(p0, p1)
        if L < 1e-6:
            continue
        t = ((p1[0] - p0[0]) / L, (p1[1] - p0[1]) / L)
        proj = [(c[0] - p0[0]) * t[0] + (c[1] - p0[1]) * t[1] for c in corners]
        s0 = min(0.0, min(proj) - mm(scale, 27))
        s1 = max(L, max(proj) + mm(scale, 27))
        e0, e1 = _add(p0, t, s0), _add(p0, t, s1)
        D = sec["dir2"]
        lab = sec["name"][0] if len(sec["name"]) == 2 and sec["name"][0] == sec["name"][1] else sec["name"]
        dr.line([e0, e1], "secline")
        for p, sgn in ((e0, 1), (e1, -1)):
            dr.line([p, _add(p, t, sgn * mm(scale, 6))], "mark")
            tip = _add(p, D, mm(scale, 5))
            b0 = _add(_add(p, D, mm(scale, 1.5)), t, mm(scale, 1.4))
            b1 = _add(_add(p, D, mm(scale, 1.5)), t, -mm(scale, 1.4))
            dr.line([tip, b0, b1], "solid", closed=True, fill=0.0)
            dr.text(_add(_add(p, t, -sgn * mm(scale, 3.0)), D, mm(scale, 2.5)), lab, 3.5, "middle", bold=True)


def plan(dr, spec, lv, cut_z, scale, sections=()):
    openings(dr, spec, lv, cut_z)
    stairs(dr, spec, lv, cut_z, scale)
    overhead(dr, spec, lv, cut_z)
    room_labels(dr, spec, lv)
    dimensions(dr, spec, lv, scale)
    section_marks(dr, spec, sections, scale)


# ---------------------------------------------------------------- sections / elevations
def level_markers(dr, spec, u_max, scale, include_top=True):
    marks = [(lv["elevation"], lv["name"]) for lv in spec["levels"]]
    zt = S.top_of_walls(spec)
    if include_top:
        b = S.bounds(spec, include_elements=False)
        marks.append((b[5], "top"))
        if spec["roof"].get("type", "flat") == "flat" and abs(b[5] - zt) > 0.05:
            marks.append((zt, "roof"))
    marks.append((spec["site"]["ground_level"], "ground"))
    x0 = u_max + mm(scale, 2.0)
    xm = u_max + mm(scale, 8.0)
    x1 = xm + mm(scale, 16.0)
    h, w = mm(scale, 2.4), mm(scale, 1.6)
    last = -1e9
    shift = 0.0
    for z, name in sorted(set(marks)):
        shift = mm(scale, 18.0) if (z - last) < mm(scale, 5.0) and not shift else 0.0
        last = z
        xm = u_max + mm(scale, 8.0) + shift
        x1 = xm + mm(scale, 16.0)
        dr.line([(x0, z), (x1, z)], "thin")
        dr.line([(xm, z), (xm - w, z + h), (xm + w, z + h)], "solid", closed=True, fill=0.0)
        val = "±0.00" if abs(z) < 0.005 else f"{z:+.2f}"
        dr.text((xm + w + mm(scale, 1.0), z + mm(scale, 0.8)), val, 2.2, "start", valign="baseline")
        dr.text((xm + w + mm(scale, 1.0), z - mm(scale, 0.8)), name, 1.8, "start", valign="top")

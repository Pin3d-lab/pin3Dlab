"""2D vector drawings, sheets (title block, scale bar, north arrow) and writers for
SVG and PDF. Pure Python (no bpy, no third-party packages).

A Drawing holds items in MODEL coordinates (metres, u right / v up in the view).
`compose()` places it on a paper sheet at a true scale (1:50, 1:100, ...) and returns
a Page whose items are in PAPER millimetres; `write_svg` / `write_pdf` write pages.

Item dicts:
  {"t": "line", "pts": [(u, v), ...], "closed": False, "pen": "beyond", "fill": None}
  {"t": "poche", "loops": [[(u, v), ...], ...]}      # cut material: union-filled, outlined
  {"t": "text", "at": (u, v), "s": "Living", "size": 2.5, "anchor": "middle", "rot": 0}
"""
import datetime
import math
import zlib

# Pen table: width in mm on paper, colour (0..1 grey or rgb), dash pattern in mm, layer.
PENS = {
    "fill":      {"w": 0.0,  "c": (0.85, 0.85, 0.85), "dash": None, "z": 0},
    "glass":     {"w": 0.0,  "c": (0.86, 0.91, 0.94), "dash": None, "z": 0},
    "beyond":    {"w": 0.13, "c": 0.0, "dash": None, "z": 1},
    "visible":   {"w": 0.18, "c": 0.0, "dash": None, "z": 1},
    "profile":   {"w": 0.35, "c": 0.0, "dash": None, "z": 1},
    "ground":    {"w": 0.70, "c": 0.0, "dash": None, "z": 2},
    "cut":       {"w": 0.50, "c": 0.0, "dash": None, "z": 3},     # outline of poché
    "symbol":    {"w": 0.18, "c": 0.0, "dash": None, "z": 4},
    "leaf":      {"w": 0.25, "c": 0.0, "dash": None, "z": 4},
    "thin":      {"w": 0.10, "c": 0.0, "dash": None, "z": 4},
    "hidden":    {"w": 0.13, "c": 0.0, "dash": (1.5, 1.0), "z": 4},
    "overhead":  {"w": 0.13, "c": 0.0, "dash": (4.0, 1.2, 0.6, 1.2), "z": 4},
    "dim":       {"w": 0.13, "c": 0.0, "dash": None, "z": 5},
    "tick":      {"w": 0.35, "c": 0.0, "dash": None, "z": 5},
    "secline":   {"w": 0.18, "c": 0.0, "dash": (6.0, 1.5, 1.0, 1.5), "z": 5},
    "mark":      {"w": 0.70, "c": 0.0, "dash": None, "z": 5},
    "solid":     {"w": 0.0,  "c": 0.0, "dash": None, "z": 5},      # black fill (arrows)
    "frame":     {"w": 0.50, "c": 0.0, "dash": None, "z": 6},
    "frame_thin": {"w": 0.18, "c": 0.0, "dash": None, "z": 6},
}
POCHE = {"fill": 0.25, "z": 3}            # grey value of cut material
TEXT_Z = 7

SHEETS = {"A4": (297, 210), "A3": (420, 297), "A2": (594, 420), "A1": (841, 594), "A0": (1189, 841)}
SCALES = [20, 25, 50, 100, 200, 250, 500, 1000]

# Helvetica advance widths (1/1000 em) for ASCII 32..126, used for centring / title block layout
_HW = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278] + [556] * 10 + \
      [278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556,
       833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
       333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333,
       500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]


def text_width(s, size, bold=False):
    w = 0
    for ch in s:
        o = ord(ch)
        w += _HW[o - 32] if 32 <= o <= 126 else 556
    return w / 1000.0 * size * (1.06 if bold else 1.0)


# ---------------------------------------------------------------- drawing
class Drawing:
    def __init__(self, name, title, kind):
        self.name, self.title, self.kind = name, title, kind
        self.items = []
        self.north = None        # degrees, for plans

    def line(self, pts, pen="visible", closed=False, fill=None):
        if len(pts) >= 2:
            self.items.append({"t": "line", "pts": [tuple(p) for p in pts], "closed": closed,
                               "pen": pen, "fill": fill})

    def poly_fill(self, pts, pen="fill"):
        self.items.append({"t": "line", "pts": [tuple(p) for p in pts], "closed": True, "pen": pen,
                           "fill": PENS[pen]["c"], "nostroke": True})

    def poche(self, loops):
        loops = [[tuple(p) for p in lp] for lp in loops if len(lp) >= 3]
        if loops:
            self.items.append({"t": "poche", "loops": loops})

    def text(self, at, s, size=2.5, anchor="middle", rot=0.0, bold=False, valign="middle", halo=False):
        self.items.append({"t": "text", "at": tuple(at), "s": s, "size": size, "anchor": anchor,
                           "rot": rot, "bold": bold, "valign": valign, "halo": halo})

    def bounds(self, kinds=None):
        xs, ys = [], []
        for it in self.items:
            if kinds and it.get("role") not in kinds:
                continue
            pts = it["pts"] if it["t"] == "line" else \
                [p for lp in it["loops"] for p in lp] if it["t"] == "poche" else [it["at"]]
            xs += [p[0] for p in pts]
            ys += [p[1] for p in pts]
        if not xs:
            return (0, 0, 1, 1)
        return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------- sheet composition
class Page:
    def __init__(self, w, h, title):
        self.w, self.h, self.title = w, h, title
        self.items = []          # paper mm, y up (origin bottom-left)


def _xf(items, f, ox, oy):
    """Model -> paper transform for drawing items."""
    out = []
    for it in items:
        it = dict(it)
        if it["t"] == "line":
            it["pts"] = [(ox + u * f, oy + v * f) for u, v in it["pts"]]
        elif it["t"] == "poche":
            it["loops"] = [[(ox + u * f, oy + v * f) for u, v in lp] for lp in it["loops"]]
        else:
            it["at"] = (ox + it["at"][0] * f, oy + it["at"][1] * f)
        out.append(it)
    return out


def fits(bounds, scale, area):
    w = (bounds[2] - bounds[0]) * 1000.0 / scale
    h = (bounds[3] - bounds[1]) * 1000.0 / scale
    return w <= area[0] and h <= area[1]


def sheet_area(sheet):
    W, H = SHEETS[sheet]
    tb_h = 34 if W < 600 else 44
    return (W - 2 * 10 - 2 * 6, H - 2 * 10 - tb_h - 2 * 6 - 12)   # 12 mm for the drawing title row


def pick_scale(bounds_fn, sheet, wanted):
    """Smallest standard scale >= wanted (i.e. largest drawing) that fits the sheet.
    bounds_fn(scale) -> model bounds (annotations grow with scale)."""
    area = sheet_area(sheet)
    for s in [s for s in SCALES if s >= wanted]:
        if fits(bounds_fn(s), s, area):
            return s
    return SCALES[-1]


def compose(drawing, sheet="A3", scale=100, meta=None, number=""):
    """Place `drawing` on a sheet at 1:scale. Returns a Page."""
    meta = meta or {}
    W, H = SHEETS[sheet]
    big = W >= 600
    page = Page(W, H, drawing.title)
    m = 10.0
    tb_w, tb_h = (180, 44) if big else (150, 34)
    # frame
    page.items.append({"t": "line", "pts": [(m, m), (W - m, m), (W - m, H - m), (m, H - m)],
                       "closed": True, "pen": "frame"})
    # drawing placement: centred in the area above the title block row
    f = 1000.0 / scale
    b = drawing.bounds()
    dw, dh = (b[2] - b[0]) * f, (b[3] - b[1]) * f
    ax0, ay0 = m + 6, m + tb_h + 6 + 12
    ax1, ay1 = W - m - 6, H - m - 6
    ox = ax0 + ((ax1 - ax0) - dw) / 2 - b[0] * f
    oy = ay0 + ((ay1 - ay0) - dh) / 2 - b[1] * f
    page.items += _xf(drawing.items, f, ox, oy)
    if dw > ax1 - ax0 + 0.5 or dh > ay1 - ay0 + 0.5:
        print(f"[archkit] WARNING: {drawing.name} ({dw:.0f} x {dh:.0f} mm) is larger than the "
              f"{sheet} drawing area at 1:{scale}")

    # drawing title under the drawing, scale bar to its right
    tx = max(ax0, ox + b[0] * f)
    ty = max(ay0 - 8, oy + b[1] * f - 10)
    page.items.append(_t((tx, ty), drawing.title.upper(), 4.0, "start", bold=True))
    tw = text_width(drawing.title.upper(), 4.0, True)
    page.items.append({"t": "line", "pts": [(tx, ty - 2.2), (tx + tw, ty - 2.2)], "pen": "mark"})
    page.items.append(_t((tx + tw + 4, ty), f"1:{scale}", 3.0, "start"))
    page.items += scale_bar((tx + tw + 4 + text_width(f"1:{scale}", 3.0) + 8, ty - 1.5), scale)
    if drawing.north is not None:
        page.items += north_arrow((ax1 - 12, ay1 - 14), drawing.north)
    # title block
    page.items += title_block(W - m - tb_w, m, tb_w, tb_h, drawing.title, scale, sheet, meta, number)
    return page


def _t(at, s, size, anchor="start", bold=False, valign="baseline", rot=0.0):
    return {"t": "text", "at": at, "s": s, "size": size, "anchor": anchor, "rot": rot,
            "bold": bold, "valign": valign, "halo": False}


def scale_bar(at, scale):
    """Alternating black/white bar. Length picked so it is ~30-50 mm on paper."""
    x, y = at
    step = {20: 0.5, 25: 0.5, 50: 1, 100: 1, 200: 2, 250: 5, 500: 10, 1000: 20}.get(scale, 1)
    n = 5
    f = 1000.0 / scale
    h = 1.6
    out = []
    for i in range(n):
        x0, x1 = x + i * step * f, x + (i + 1) * step * f
        rect = [(x0, y), (x1, y), (x1, y + h), (x0, y + h)]
        out.append({"t": "line", "pts": rect, "closed": True, "pen": "thin",
                    "fill": 0.0 if i % 2 == 0 else 1.0})
    for i in (0, n):
        lab = f"{i * step:g}" if i else "0"
        out.append(_t((x + i * step * f, y + h + 1.2), lab, 2.0, "middle"))
    out.append(_t((x + n * step * f + 2, y + 0.1), "m", 2.0, "start"))
    return out


def north_arrow(at, north_deg=0.0):
    """Circle with a filled half-arrow; north_deg rotates it (plan north = +Y)."""
    cx, cy = at
    r = 7.0
    out = [{"t": "line", "pts": [(cx + r * math.cos(a * math.pi / 18), cy + r * math.sin(a * math.pi / 18))
                                 for a in range(36)], "closed": True, "pen": "symbol"}]
    a = math.radians(north_deg)
    rot = lambda x, y: (cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a))
    out.append({"t": "line", "pts": [rot(0, r + 2), rot(-3.2, -r + 1), rot(0, -r + 3)], "closed": True,
                "pen": "solid", "fill": 0.0, "nostroke": True})
    out.append({"t": "line", "pts": [rot(0, r + 2), rot(3.2, -r + 1), rot(0, -r + 3)], "closed": True,
                "pen": "symbol", "fill": 1.0})
    tx, ty = rot(0, r + 5.2)
    out.append(_t((tx, ty), "N", 3.5, "middle", bold=True, valign="middle", rot=north_deg))
    return out


def title_block(x, y, w, h, title, scale, sheet, meta, number):
    out = [{"t": "line", "pts": [(x, y), (x + w, y), (x + w, y + h), (x, y + h)], "closed": True,
            "pen": "frame", "fill": 1.0}]
    r1 = y + h * 0.58
    out.append({"t": "line", "pts": [(x, r1), (x + w, r1)], "pen": "frame_thin"})
    c1, c2 = x + w * 0.42, x + w * 0.72
    out.append({"t": "line", "pts": [(c1, y), (c1, r1)], "pen": "frame_thin"})
    out.append({"t": "line", "pts": [(c2, y), (c2, r1)], "pen": "frame_thin"})
    s = h / 34.0
    project = meta.get("project", "")
    out.append(_t((x + 3, y + h - 4.5 * s), "PROJECT", 1.8 * s))
    out.append(_t((x + 3, y + h - 9.5 * s), project, 4.0 * s, bold=True))
    if meta.get("description"):
        d = meta["description"]
        while d and text_width(d, 2.0 * s) > w - 6:
            d = d[:-2]
        out.append(_t((x + 3, r1 + 1.8 * s), d, 2.0 * s))
    out.append(_t((x + 3, r1 - 4.5 * s), "DRAWING", 1.8 * s))
    tt = title
    while tt and text_width(tt, 3.4 * s, True) > c1 - x - 5:
        tt = tt[:-1]
    out.append(_t((x + 3, y + 4.5 * s), tt, 3.4 * s, bold=True))
    out.append(_t((c1 + 3, r1 - 4.5 * s), "SCALE / SHEET", 1.8 * s))
    out.append(_t((c1 + 3, y + 4.5 * s), f"1:{scale} @ {sheet}", 3.0 * s))
    out.append(_t((c2 + 3, r1 - 4.5 * s), "DATE / No.", 1.8 * s))
    out.append(_t((c2 + 3, y + 9.5 * s), meta.get("date", datetime.date.today().isoformat()), 2.6 * s))
    out.append(_t((c2 + 3, y + 4.0 * s), number, 3.0 * s, bold=True))
    if meta.get("author"):
        out.append(_t((x + w - 3, y + h - 6 * s), meta["author"], 2.0 * s, "end"))
    return out


# ---------------------------------------------------------------- writers
def _grey(c):
    if isinstance(c, (int, float)):
        return (c, c, c)
    return tuple(c)


def _hex(c):
    r, g, b = _grey(c)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _ordered(items):
    def z(it):
        if it["t"] == "text":
            return TEXT_Z
        if it["t"] == "poche":
            return POCHE["z"]
        return PENS[it["pen"]]["z"]
    return sorted(items, key=z)


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg_page(page):
    H = page.h
    Y = lambda y: H - y
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{page.w}mm" height="{page.h}mm" '
         f'viewBox="0 0 {page.w} {page.h}">',
         f'<title>{_esc(page.title)}</title>',
         f'<rect width="{page.w}" height="{page.h}" fill="#ffffff"/>',
         '<g fill="none" stroke-linejoin="round" stroke-linecap="butt" '
         'font-family="Helvetica, Arial, sans-serif">']

    def d_of(loops, closed=True):
        parts = []
        for lp in loops:
            parts.append("M" + " L".join(f"{x:.3f},{Y(y):.3f}" for x, y in lp) + (" Z" if closed else ""))
        return " ".join(parts)

    done_poche = False
    for it in _ordered(page.items):
        if it["t"] == "poche":
            if done_poche:
                continue
            done_poche = True
            # all outlines first (double width), then all fills: the fills cover the inner half
            # of every outline, so overlapping cut parts read as one merged shape
            ds = [d_of(p["loops"]) for p in page.items if p["t"] == "poche"]
            w = PENS["cut"]["w"] * 2
            o += [f'<path d="{d}" stroke="#000" stroke-width="{w:.3f}"/>' for d in ds]
            o += [f'<path d="{d}" fill="{_hex(POCHE["fill"])}" fill-rule="evenodd" stroke="none"/>' for d in ds]
        elif it["t"] == "line":
            p = PENS[it["pen"]]
            d = d_of([it["pts"]], it.get("closed"))
            fill = _hex(it["fill"]) if it.get("fill") is not None else "none"
            if it.get("nostroke") or p["w"] == 0:
                o.append(f'<path d="{d}" fill="{fill}" stroke="none"/>')
                continue
            dash = f' stroke-dasharray="{",".join(f"{x:g}" for x in p["dash"])}"' if p["dash"] else ""
            o.append(f'<path d="{d}" fill="{fill}" stroke="{_hex(p["c"])}" '
                     f'stroke-width="{p["w"]:.3f}"{dash}/>')
        else:
            x, y = it["at"]
            sz = it["size"]
            dy = {"middle": 0.35 * sz, "top": 0.75 * sz, "baseline": 0.0}[it.get("valign", "baseline")]
            anchor = {"start": "start", "middle": "middle", "end": "end"}[it["anchor"]]
            wt = ' font-weight="bold"' if it.get("bold") else ""
            rot = f' transform="rotate({-it["rot"]:.2f} {x:.3f} {Y(y):.3f})"' if it.get("rot") else ""
            lines = it["s"].split("\n")
            for k, ln in enumerate(lines):
                yy = Y(y) + dy + (k - (len(lines) - 1) / 2) * sz * 1.25
                halo = (' stroke="#fff" stroke-width="0.8" paint-order="stroke"' if it.get("halo") else "")
                o.append(f'<text x="{x:.3f}" y="{yy:.3f}" font-size="{sz:.2f}" text-anchor="{anchor}" '
                         f'fill="#000" stroke="none"{wt}{rot}{halo}>{_esc(ln)}</text>')
    o.append("</g></svg>")
    return "\n".join(o) + "\n"


def write_svg(page, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg_page(page))
    return path


def _pdf_str(s):
    b = s.encode("cp1252", errors="replace")
    out = []
    for ch in b:
        c = chr(ch)
        if c in "()\\":
            out.append("\\" + c)
        elif 32 <= ch < 127:
            out.append(c)
        else:
            out.append("\\%03o" % ch)
    return "(" + "".join(out) + ")"


def _pdf_content(page):
    k = 72.0 / 25.4
    o = ["1 j 0 J"]
    P = lambda x, y: f"{x * k:.2f} {y * k:.2f}"

    def path(loops, closed=True):
        s = []
        for lp in loops:
            s.append(P(*lp[0]) + " m")
            s += [P(*p) + " l" for p in lp[1:]]
            if closed:
                s.append("h")
        return " ".join(s)

    done_poche = False
    for it in _ordered(page.items):
        if it["t"] == "poche":
            if done_poche:
                continue
            done_poche = True
            ps = [path(p["loops"]) for p in page.items if p["t"] == "poche"]
            g = POCHE["fill"]
            o += [f"0 G {PENS['cut']['w'] * 2 * k:.3f} w [] 0 d {p} S" for p in ps]
            o += [f"{g:.3f} {g:.3f} {g:.3f} rg {p} f*" for p in ps]
        elif it["t"] == "line":
            pen = PENS[it["pen"]]
            p = path([it["pts"]], it.get("closed"))
            fill = it.get("fill")
            if fill is not None:
                r, gg, b = _grey(fill)
                o.append(f"{r:.3f} {gg:.3f} {b:.3f} rg")
            stroke = not it.get("nostroke") and pen["w"] > 0
            if stroke:
                r, gg, b = _grey(pen["c"])
                dash = "[" + " ".join(f"{x * k:.2f}" for x in pen["dash"]) + "] 0 d" if pen["dash"] else "[] 0 d"
                o.append(f"{r:.3f} {gg:.3f} {b:.3f} RG {pen['w'] * k:.3f} w {dash}")
            op = ("B" if stroke else "f") if fill is not None else "S"
            o.append(f"{p} {op}")
        else:
            x, y = it["at"]
            sz = it["size"]
            font = "/F2" if it.get("bold") else "/F1"
            lines = it["s"].split("\n")
            a = math.radians(it.get("rot", 0.0))
            ca, sa = math.cos(a), math.sin(a)
            dy = {"middle": -0.35 * sz, "top": -0.75 * sz, "baseline": 0.0}[it.get("valign", "baseline")]
            for j, ln in enumerate(lines):
                w = text_width(ln, sz, it.get("bold"))
                dx = {"start": 0.0, "middle": -w / 2, "end": -w}[it["anchor"]]
                ly = dy - (j - (len(lines) - 1) / 2) * sz * 1.25
                px = x + dx * ca - ly * sa
                py = y + dx * sa + ly * ca
                if it.get("halo"):
                    o.append(f"BT 1 Tr 1 1 1 RG {0.8 * k:.2f} w {font} {sz * k:.2f} Tf "
                             f"{ca:.4f} {sa:.4f} {-sa:.4f} {ca:.4f} {px * k:.2f} {py * k:.2f} Tm {_pdf_str(ln)} Tj ET")
                o.append(f"BT 0 Tr 0 0 0 rg {font} {sz * k:.2f} Tf {ca:.4f} {sa:.4f} {-sa:.4f} {ca:.4f} "
                         f"{px * k:.2f} {py * k:.2f} Tm {_pdf_str(ln)} Tj ET")
    return "\n".join(o).encode("latin-1")


def write_pdf(pages, path, title="archkit drawings"):
    """Write one or more pages into a single (vector) PDF."""
    k = 72.0 / 25.4
    objs = []

    def add(b):
        objs.append(b if isinstance(b, bytes) else b.encode("latin-1"))
        return len(objs)

    cat = add("")                         # placeholders, filled below
    pages_id = add("")
    f1 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    kids = []
    for pg in pages:
        data = zlib.compress(_pdf_content(pg))
        cid = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream")
        pid = add(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {pg.w * k:.2f} {pg.h * k:.2f}] "
                  f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> >> /Contents {cid} 0 R >>")
        kids.append(pid)
    objs[cat - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
    objs[pages_id - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in kids)}] "
                          f"/Count {len(kids)} >>").encode()
    info = add(f"<< /Title {_pdf_str(title)} /Producer (archkit) >>")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, b in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + b + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += (b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, cat, info, x))
    with open(path, "wb") as f:
        f.write(out)
    return path

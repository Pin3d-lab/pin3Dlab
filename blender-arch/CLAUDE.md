# Conventions for Claude sessions working on blender-arch

The user is an architect, not a programmer. What matters most: (1) architectural drawings,
(2) render quality. Keep messages short and plain; don't add unrelated features.

## Ground rules
- `projects/<name>/spec.json` is the source of truth. Make design changes there, in metres,
  never by editing a generated `.blend`. Plan coords: +X east, +Y north, Z up.
- Run everything through `run.py` with Blender (official 4.5 LTS at `/opt/blender`,
  `blender` on PATH; code must also work on 4.2+ and 5.x: engine names, sky types and compositor
  API differ, see the fallbacks in render.py).
  `blender -b -P run.py -- projects/<name> --views drawings,hero --preset draft`
- **Verify visually.** After a change, render and look at the PNGs; rasterize PDFs/SVGs to check
  drawings (e.g. `pip install pymupdf`, then render the page to PNG). Don't claim it works unseen.
- Never commit outputs (`projects/*/out/`), downloaded assets (`assets/`) or Blender binaries.
- No 3D-print / STL features (removed on purpose).
- Branch: commit in logical steps; don't open pull requests unless asked.

## Layout
| File | Role |
|---|---|
| `run.py` | CLI: lint + report, build, drawings, photo renders, exports |
| `archkit/spec.py` | load/defaults, lint, area report. Pure Python (`python3 -m archkit.spec <project>`) |
| `archkit/build.py` | geometry. Walls = path ribbon + Solidify + EXACT boolean cutters. Every object gets custom props `role` and `level` |
| `archkit/materials.py` | role → library material; Poly Haven textures (box projection, calibrated to library colour) with procedural fallback; glass transparent to shadow rays |
| `archkit/render.py` | presets, lighting presets (day/golden/overcast/night), HDRI alignment, portals, compositor bloom, DOF, photo cameras |
| `archkit/drawings.py` | vector drawings: view frames, mesh cut (poché loops), feature edges + ray-cast hidden-line removal, crop, sheet output |
| `archkit/symbols.py` | spec-driven annotation: doors, windows, stairs, dimensions, labels, section marks, level markers. Pure Python |
| `archkit/vector.py` | Drawing/Page model, sheet composition (title block, scale bar, north arrow), SVG + PDF writers (no deps) |
| `fetch_assets.py` | downloads CC0 HDRIs/textures into `assets/` + `assets/manifest.json` |

## Roles
`wall_ext wall_int slab ceiling floor roof frame glass door stair element ground tree label cutter`.
Drawings skip `cutter label ground tree`; plans also skip `frame glass door stair floor ceiling`
(drawn as symbols instead). Glass objects carry `exterior` (used for portals).

## Drawing pipeline details
- View frame: u = (P-O)·R, v = (P-O)·U, depth = (P-O)·D. Plans look down (-Z) with the cut at
  `plan_cut_height` (+0.137 mm nudge so cuts never coincide with sill/head faces).
- Poché is drawn as "all outlines at double width, then all fills" so overlapping cut parts
  merge into one outline. Keep that order in both writers.
- Hidden lines: an edge sample is visible if no surface lies between the cut/front plane and the
  sample (tolerance 12 mm), testing ±4 mm beside it for grazing faces. Coplanar seams between
  objects (storey joints) are dropped; a depth jump > 0.5 m beside an edge makes it a profile line.
- Bevel modifiers are disabled while extracting drawing geometry.
- Pen widths and dash patterns live in `vector.PENS` (mm on paper). Annotation sizes in symbols.py
  are paper mm converted with `mm(scale, x)`, so they stay constant at 1:50 and 1:100.
- `pick_scale` moves to the next standard scale if a drawing does not fit the sheet; it prints
  a message. Crop: building + 3 m (`drawing_margin`), plus elements within 6 m of it.

## Bonsai (BlenderBIM)
Evaluated in Nov 2026: Bonsai 0.9.0 supports Blender 4.2-5.0 (py311, linux-x64 build available
on extensions.blender.org). Its drawing generation needs the model to be authored as IFC
(classes, storeys, representations) and runs through its own operators. In this cloud session the
add-on install was blocked by the session's permission policy, so headless drawing generation was
not tested. The native pipeline does not depend on it. If you try it later: install with
`blender --command extension install-file -r user_default --enable <zip>`, export the model to IFC
via Bonsai, then test `bpy.ops.bim.create_drawing` in background mode; add it as an optional
`--bonsai` path only if it works reliably headless.

## Verifying quickly
```bash
blender -b -P run.py -- projects/example_house --views drawings          # ~10 s
blender -b -P run.py -- projects/example_house --preset preview --resolution 50 --views hero,interior_living
```

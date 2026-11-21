# archkit: architecture from a spec file, in Blender

Describe a building in `projects/<name>/spec.json` (walls, openings, rooms, stairs, roof, in metres)
and one command builds the 3D model, draws **plans, sections and elevations as true-scale
vector sheets (SVG + PDF)**, and renders **photo-real views** in Blender.

```
spec.json  ──►  3D model  ──►  drawings/  plan_GF.pdf  section_AA.pdf  elev_south.pdf ... + one drawing-set PDF
                          └──►  <preset>/  hero.png  aerial.png  interior_living.png ...
```

## Quick start

Requirements: Blender 4.2 or newer (tested with **Blender 4.5 LTS**, the official build from
blender.org; distro builds often lack the OpenImageDenoise denoiser).

```bash
cd blender-arch
python3 fetch_assets.py          # once: CC0 skies + textures from Poly Haven (~110 MB, optional)

# everything at draft quality (fast)
blender -b -P run.py -- projects/example_house --views all

# drawings only: A3 sheets at 1:100 (or --sheet A1 --scale 50)
blender -b -P run.py -- projects/example_house --views drawings

# client-ready images, golden-hour light, depth of field
blender -b -P run.py -- projects/example_house --preset final --views photos --light golden --dof

# open the model in the Blender GUI to look around
blender -P run.py -- projects/example_house --no-render
```

New project: copy `projects/_template` to `projects/<your_name>` and edit `spec.json`.
Outputs go to `projects/<name>/out/` (not committed to git):

| Path | What |
|---|---|
| `out/drawings/plan_GF.pdf / .svg` | one sheet per drawing (vector, true scale, title block) |
| `out/drawings/<name>_drawings.pdf` | all drawings as one multi-page drawing set |
| `out/<preset>/hero.png` ... | renders (`hero_golden.png` etc. for other lighting presets) |
| `out/report.md / .json` | areas per room and level, glazing ratio per facade, lint warnings |
| `out/<name>.blend / .glb / .fbx / .obj` | with `--export blend,glb,fbx,obj` |

## Views and drawings

| Name | Output |
|---|---|
| `plan_<LEVEL>` | floor plan cut at 1.20 m above the floor (`plan_cut_height`): cut walls in poché, door swings, window lines, stair with cut line and arrow, two dimension chains per facade (openings + overall), room name and area, section marks, overhead elements dashed, north arrow, scale bar |
| `section_<NAME>` | vertical section from the spec's `sections` (default: one longitudinal + one transverse through the centre): cut material in poché with a heavy outline, everything beyond in thin lines with hidden lines removed, ground line, level markers (+0.00, +3.20 ...) |
| `elev_south`, `elev_east`, `elev_north`, `elev_west` | elevations: profile lines heavier than face lines, glazing tinted, ground line, level markers |
| `hero`, `hero_alt` | eye-level 2-point perspectives (vertical lines stay vertical) |
| `aerial`, `axo` | bird's-eye perspective and isometric |
| any `cameras[].name` | your own cameras (e.g. `interior_living`) |

Groups for `--views`: `all`, `photos`, `drawings`, `plans`, `sections`, `elevations`, `default`
(hero, aerial, first plan). Combine with commas: `--views drawings,hero`.

Line weights (mm on paper): cut outline 0.50, ground 0.70, profile 0.35, visible edge 0.18,
beyond (sections) 0.13, dimensions 0.13. Change them in `archkit/vector.py` (`PENS`).

## Command line

| Option | Meaning |
|---|---|
| `--preset draft / preview / final` | render quality (see below); default `draft` |
| `--views ...` | which views/drawings (names or groups, comma separated) |
| `--light day / golden / overcast / night` | lighting preset; default spec `lighting` or `day` |
| `--sheet A3 / A1 / A2 / A0 / A4` | drawing sheet; default spec `sheet` or A3 |
| `--scale 100 / 50 / 200 ...` | drawing scale 1:N; if a drawing does not fit, the next standard scale is used and a message says so |
| `--dof` | depth of field on perspective views |
| `--resolution 50` | render at 50 % size (quick checks) |
| `--no-context` | no trees around the building |
| `--no-textures`, `--no-hdri` | procedural materials / physical sky only |
| `--no-glare` | switch off the subtle bloom |
| `--export blend,glb,fbx,obj` | model exports |
| `--no-render` | build only (with the GUI: keep Blender open) |

## Presets

| Preset | Engine | Size | Samples | Use |
|---|---|---|---|---|
| `draft` | EEVEE (interiors: Cycles) | 1280×720 | 16 | composition, quick checks |
| `preview` | Cycles + denoise | 1920×1080 | 128 | client check-ins |
| `final` | Cycles + denoise | 3840×2160 | 768 | deliverables |

Lighting presets: `day` (sun from `spec.sun`, clear-sky HDRI), `golden` (low warm sun, sunset sky),
`overcast` (soft, no sun: best for showing materials and form), `night` (dusk sky, interior lights on).
With `fetch_assets.py` run, real HDRI skies are used; the HDRI is rotated so its sun glow matches
the sun lamp, and its sun disc is clamped so the lamp alone casts the (sharp) shadows.

## spec.json reference

All lengths in metres. Plan coordinates: +X east, +Y north, Z up. `segment` counts the sides of a
wall path from 0. Closed exterior outlines describe the **outside face** and are made
counter-clockwise automatically.

```jsonc
{
  "name": "example_house",
  "description": "shown in the title block",
  "project": {"project": "Example House", "author": "Office name"},   // title block
  "north": 0,                           // true-north rotation of the plan (degrees)
  "sun": {"azimuth": 240, "elevation": 32, "strength": 4.0},
  "lighting": "day",                    // default lighting preset
  "sheet": "A3", "scale": 100,          // default drawing sheet and scale
  "hero_azimuth": 205, "hero_lens": 26, // where the hero camera stands, its focal length
  "plan_cut_height": 1.2,
  "materials": {"wall_ext": "plaster_white", "roof": "roof_membrane",
                "wall_int": {"base": "plaster_white", "color": [0.9, 0.88, 0.84]}},
  "levels": [{
    "name": "GF", "title": "Ground floor plan", "elevation": 0.0, "height": 3.2, "slab": 0.3,
    "walls": [
      {"id": "ext", "closed": true, "thickness": 0.3, "path": [[0,0],[12,0],[12,9],[0,9]]},
      {"id": "int_a", "thickness": 0.12, "path": [[7,0.3],[7,8.7]], "material": "brick_red"}
    ],
    "openings": [
      {"wall": "ext", "segment": 0, "kind": "window", "offset": 0.8, "width": 5.0, "sill": 0.0, "height": 2.6},
      {"wall": "ext", "segment": 0, "kind": "door", "offset": 7.6, "width": 1.1, "height": 2.3,
       "hinge": "start", "swing": "in", "glazed": false},
      {"wall": "int_a", "kind": "opening", "offset": 5.2, "width": 1.2, "height": 2.3}
    ],
    "rooms": [{"name": "Living / Kitchen", "polygon": [[0.3,0.3],[6.94,0.3],[6.94,8.7],[0.3,8.7]],
               "material": "oak_floor"}],
    "stairs": [{"start": [7.2, 7.6], "direction": 0, "width": 1.0, "riser": 0.188, "tread": 0.26}],
    "slab_openings": []                 // voids in this level's floor slab, e.g. above a stair
  }],
  "roof": {"type": "flat", "thickness": 0.3, "parapet": 0.5},   // or gable / mono with pitch, overhang
  "elements": [                         // terraces, canopies, paths, columns ...
    {"name": "terrace", "type": "prism", "polygon": [[0,-3.5],[7,-3.5],[7,0],[0,0]], "z": [-0.15, -0.02], "material": "timber"},
    {"name": "canopy", "type": "box", "center": [8.15, -0.6, 2.7], "size": [2.6, 1.2, 0.14]}
  ],
  "sections": [{"name": "AA", "from": [-1.5, 8.1], "to": [13.5, 8.1], "look": "south"}],
  "cameras": [{"name": "interior_living", "interior": true, "location": [0.9, 0.8, 1.45],
               "look_at": [6.5, 8.2, 1.3], "lens": 16, "exposure": 2.2, "dof": {"fstop": 4}}],
  "context": {"trees": "auto"},         // or [[x, y, height], ...] or false
  "interior_lights": false,             // warm ceiling lights also in daylight renders
  "hdri": "path/to/own_sky.hdr"         // optional: your own HDRI instead of the presets
}
```

Door options: `hinge` `start`/`end` (which jamb, along the wall direction), `swing` `in`/`out` for
exterior walls or `left`/`right` of the wall direction for interior walls. Windows: `mullions`
(count), `inset`. Sections: `look` is `north/south/east/west` or a vector `[dx, dy]`; omitted, it
looks to the left of from→to. `depth` limits how far beyond the cut is drawn.

Materials (library names): `plaster_white`, `plaster_warm`, `concrete`, `concrete_dark`, `brick_red`,
`timber`, `oak_floor`, `tile_light`, `aluminium_dark`, `steel`, `glass`, `roof_tile`,
`roof_membrane`, `gravel`, `grass`, `paving`, `door_wood`. Roles you can override in `materials`:
`wall_ext`, `wall_int`, `slab`, `ceiling`, `floor`, `roof`, `frame`, `glass`, `door`, `stair`,
`ground`, `element`. A colour override keeps the photo texture's detail but sets its tone.

Check a spec without Blender: `python3 -m archkit.spec projects/example_house` (prints the area
report and lint errors such as windows overlapping corners or steep stairs).

## How to optimize outputs

### Speed
- **Iterate on `draft`**, switch to `preview` to check light, and only render `final` for
  delivery. Use `--resolution 50` for quick framing checks at any preset.
- **GPU**: Cycles automatically uses OptiX / CUDA / HIP / Metal if present (the log says
  `Cycles device: ...`). A mid-range GPU is 5-20× faster than a laptop CPU; `final` on CPU takes long.
- **Adaptive sampling + denoising** are always on: Cycles stops sampling pixels that are already
  clean, and OpenImageDenoise removes the remaining noise. To go faster, lower `samples` in
  `PRESETS` (render.py) rather than turning denoising off. Raise `noise` (adaptive threshold) for
  speed, lower it for quality.
- **Persistent data** is on: several views of one model reuse the scene data, so render all views
  in one command (`--views photos`) instead of one command per view.
- Drawings are fast (seconds): they do not render pixels, they compute vectors.

### Quality
- **Model at real-world scale** (metres): light falloff, textures, depth of field and the
  physical sky all assume it. Real wall thicknesses and window reveals give real shadows.
- **2-point perspective**: the hero views and custom cameras keep the camera level and frame with
  lens shift, so verticals stay vertical. Keep `two_point` on (default) for architecture.
- **Focal lengths**: 24-35 mm for exteriors (`hero_lens`), 16-20 mm for interiors. Wider than
  16 mm distorts furniture and rooms; longer lenses flatten facades.
- **Sun angle**: a low sun (25-40°) grazing the main facade shows depth; sun from straight in
  front looks flat. Set `sun.azimuth` 30-60° off the facade normal, or try `--light golden`.
  `--light overcast` is the honest choice for material studies.
- **HDRI + real textures**: run `fetch_assets.py` once. Textures are projected at their real size,
  so a 1 m plaster sample stays 1 m on the wall.
- **Bevels**: `preview` and `final` add 6 mm bevels to walls so edges catch light (drafts and
  drawings skip them).
- **Interiors**: mark the camera `"interior": true`. That adds window portals (Cycles samples the
  sky through the windows: far less noise), more light bounces, twice the samples and +2.2 EV
  exposure. Too dark or too bright: set the camera's `exposure` (in EV stops). For evening shots
  use `--light night` or `"interior_lights": true`.
- **Glass** lets sun and sky light through to interiors (shadow rays pass it); keep windows as
  openings in the spec, not as separate objects.

### Working with Claude
- **Keep `spec.json` the source of truth.** Ask for changes in plan terms and metres: "move the
  kitchen window 0.5 m east", "make the stair 1.1 m wide", "add a 1.2 m deep balcony on the south
  side of FF". Avoid editing the generated `.blend` by hand: the next build overwrites it.
- **Iterate on drafts**: ask for `--preset draft` renders and the drawing PDFs, look, then ask for
  the next change. Ask for `preview`/`final` once the design is settled.
- **Read the report**: `out/report.md` gives areas and glazing ratios after every build: quick
  checks against the brief.
- **Commit versions** of `spec.json` (git) at each design step, with a message like
  "option B: stair moved north"; then you can compare or go back at any time.

## Notes and limits
- Bonsai (BlenderBIM) is not used: see `CLAUDE.md` for why; drawings are generated natively.
- Box-projected textures use the displacement map as bump (no UVs needed); glTF/FBX exports
  carry flat material colours, not the textures.
- Context trees are simple procedural shapes (trunk + leafy crown): enough to place the building,
  not botanical models. Switch off with `--no-context` or `"context": false`.

"""Download the CC0 HDRIs and PBR textures archkit uses, from Poly Haven.

    python3 fetch_assets.py            # 2k (default)
    python3 fetch_assets.py --res 4k   # sharper close-ups, 4x the disk space
    python3 fetch_assets.py --list     # show what would be fetched

Files go to assets/ (git-ignored) and assets/manifest.json tells materials.py and
render.py what is there. Everything works without the assets (procedural fallback);
with them, materials get real image textures and lighting presets get real skies.
Re-running only downloads what is missing.
"""
import argparse
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
API = "https://api.polyhaven.com"
UA = {"User-Agent": "archkit-asset-fetch/1.0"}

# archkit material name -> Poly Haven texture id
TEXTURES = {
    "plaster_white": "white_plaster_02",
    "concrete": "concrete_wall_004",
    "brick_red": "red_brick",
    "oak_floor": "wood_floor",
    "timber": "japanese_cedar_planks",      # vertical timber cladding
    "paving": "rectangular_paving",
    "grass": "leafy_grass",
    "tile_light": "large_floor_tiles_02",
    "gravel": "gravel_floor_02",
}
# lighting preset -> Poly Haven HDRI id (sky-only "puresky" maps: no ground to fight the model)
HDRIS = {
    "day": "kloofendal_48d_partly_cloudy_puresky",
    "golden": "kloppenheim_06_puresky",
    "overcast": "kloofendal_overcast_puresky",
    "night": "qwantani_dusk_2_puresky",
}
# Poly Haven map name -> our key. Displacement is used as a bump map: it needs no UVs,
# so it works with the box projection archkit uses on walls and floors.
MAPS = {"Diffuse": "diff", "Rough": "rough", "Displacement": "disp", "nor_gl": "nor"}


def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.load(r)


def download(url, path):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    os.replace(tmp, path)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--res", default="2k", choices=["1k", "2k", "4k"])
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        print("textures:", json.dumps(TEXTURES, indent=1))
        print("hdris:", json.dumps(HDRIS, indent=1))
        return
    manifest = {"resolution": a.res, "textures": {}, "hdris": {}, "license": "CC0 (polyhaven.com)"}
    for mat, pid in TEXTURES.items():
        try:
            info = get_json(f"{API}/info/{pid}")
            files = get_json(f"{API}/files/{pid}")
        except Exception as e:
            print(f"[fetch] {pid}: {e}")
            continue
        entry = {"id": pid, "size_m": (info.get("dimensions") or [1000])[0] / 1000.0}
        for ph, key in MAPS.items():
            f = files.get(ph, {}).get(a.res, {}).get("jpg") or files.get(ph, {}).get(a.res, {}).get("png")
            if not f:
                continue
            rel = os.path.join("textures", pid, os.path.basename(f["url"]))
            new = download(f["url"], os.path.join(ASSETS, rel))
            entry[key] = rel
            print(f"[fetch] {'got ' if new else 'have'} {rel}")
        manifest["textures"][mat] = entry
    for preset, pid in HDRIS.items():
        try:
            f = get_json(f"{API}/files/{pid}")["hdri"][a.res]["hdr"]
        except Exception as e:
            print(f"[fetch] {pid}: {e}")
            continue
        rel = os.path.join("hdris", os.path.basename(f["url"]))
        new = download(f["url"], os.path.join(ASSETS, rel))
        manifest["hdris"][preset] = {"id": pid, "file": rel}
        print(f"[fetch] {'got ' if new else 'have'} {rel}")
    os.makedirs(ASSETS, exist_ok=True)
    with open(os.path.join(ASSETS, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"[fetch] manifest: {os.path.join(ASSETS, 'manifest.json')}")


if __name__ == "__main__":
    sys.exit(main())

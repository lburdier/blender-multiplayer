"""Genere index.json (depot d'extensions Blender, format "v1") a partir des
zips d'un dossier. Equivalent a `blender --command extension server-generate`,
sans avoir besoin de Blender (utilise par GitHub Actions).

Usage : python tools/make_index.py <dossier contenant les .zip>
"""

import hashlib
import json
import os
import sys
import zipfile

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

# Ordre des champs identique a celui de Blender.
FIELDS = ("schema_version", "id", "name", "tagline", "version", "type", "maintainer", "license",
          "blender_version_min", "blender_version_max", "website", "copyright", "permissions",
          "platforms", "tags")


def manifest_of(path):
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.rsplit("/", 1)[-1] == "blender_manifest.toml"]
        if not names:
            raise ValueError("%s : pas de blender_manifest.toml" % path)
        raw = zf.read(min(names, key=len)).decode("utf-8")
    if tomllib is None:
        raise RuntimeError("Python 3.11+ requis (tomllib)")
    return tomllib.loads(raw)


def entry_for(path):
    man = manifest_of(path)
    entry = {k: man[k] for k in FIELDS if k in man}
    with open(path, "rb") as fh:
        data = fh.read()
    entry["archive_url"] = "./" + os.path.basename(path)
    entry["archive_size"] = len(data)
    entry["archive_hash"] = "sha256:" + hashlib.sha256(data).hexdigest()
    return entry


def main(folder):
    zips = sorted(f for f in os.listdir(folder) if f.endswith(".zip"))
    data = [entry_for(os.path.join(folder, f)) for f in zips]
    # Blender ne lit qu'une entree par extension (la premiere) : comme
    # extensions.blender.org, on ne publie que la derniere version de chacune.
    latest = {}
    for e in data:
        v = tuple(int(x) for x in e["version"].split(".")[:3])
        if e["id"] not in latest or v > latest[e["id"]][0]:
            latest[e["id"]] = (v, e)
    data = [latest[k][1] for k in sorted(latest)]
    index = {"version": "v1", "blocklist": [], "data": data}
    with open(os.path.join(folder, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, ensure_ascii=False)
    print("index.json : %d paquet(s) : %s" % (len(data), ", ".join(e["version"] for e in data)))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")

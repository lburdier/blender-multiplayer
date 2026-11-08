"""Publie une nouvelle version de l'addon.

Usage : python tools/release.py <blender.exe> [--notes "texte"]

1. Verifie que la version de blender_manifest.toml et celle de bl_info
   (__init__.py) sont identiques et que le tag n'existe pas encore.
2. Construit le zip avec `blender --command extension build`.
3. Pousse la branche main et cree la version GitHub avec le zip.
   GitHub Actions met ensuite a jour le depot d'extensions (index.json) :
   tous les addons installes recoivent la mise a jour automatiquement.
"""

import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "blender_multiplayer")


def run(*cmd, **kw):
    shown = " ".join(c if len(c) < 120 else c[:60] + "..." for c in cmd)
    print("$", shown.encode("ascii", "replace").decode("ascii"))
    return subprocess.run(cmd, cwd=ROOT, check=True, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("blender")
    ap.add_argument("--notes", default="")
    args = ap.parse_args()

    manifest = open(os.path.join(PKG, "blender_manifest.toml"), encoding="utf-8").read()
    version = re.search(r'(?m)^version\s*=\s*"([^"]+)"', manifest).group(1)
    init = open(os.path.join(PKG, "__init__.py"), encoding="utf-8").read()
    bl = re.search(r'"version":\s*\((\d+),\s*(\d+),\s*(\d+)\)', init).groups()
    if ".".join(bl) != version:
        sys.exit("Versions differentes : manifest %s, bl_info %s" % (version, ".".join(bl)))
    tag = "v" + version
    tags = subprocess.run(["git", "tag", "--list", tag], cwd=ROOT, capture_output=True, text=True).stdout
    if tag in tags.split():
        sys.exit("Le tag %s existe deja : augmentez la version." % tag)
    if subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                      text=True).stdout.strip():
        sys.exit("Des modifications ne sont pas commitees.")

    dist = os.path.join(ROOT, "dist")
    os.makedirs(dist, exist_ok=True)
    zip_path = os.path.join(dist, "blender_multiplayer-%s.zip" % version)
    if os.path.exists(zip_path):
        os.remove(zip_path)
    run(args.blender, "--factory-startup", "--command", "extension", "build",
        "--source-dir", PKG, "--output-dir", dist)
    run("git", "push", "origin", "main")
    notes = args.notes or "Version %s. Les addons installes se mettent a jour automatiquement." % version
    run("gh", "release", "create", tag, zip_path, "--title", "Blender Multiplayer %s" % version,
        "--notes", notes, "--latest")
    print("Publie : %s. Le depot d'extensions sera a jour dans 1 a 2 minutes." % tag)


if __name__ == "__main__":
    main()

"""Teste la mise a jour automatique AU DEMARRAGE, en interface graphique.

Usage : python tests/run_update_gui_test.py <blender.exe> <zip de la version courante>

Reproduit l'usage reel : Blender ouvert en double-cliquant sur un .blend.
1. Profil isole (BLENDER_USER_RESOURCES), version courante installee depuis le zip.
2. Un depot HTTP local publie une fausse version 9.9.9.
3. Blender est lance en interface avec un fichier .blend en argument, sans
   rien appeler a la main : la verification de demarrage doit trouver 9.9.9
   et l'installer toute seule.
"""

import functools
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import make_index  # noqa: E402
from run_update_test import fake_next  # noqa: E402

INSTALL = r'''
import sys, bpy
ZIP = sys.argv[sys.argv.index("--") + 1]
bpy.context.preferences.system.use_online_access = True
r = bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default", enable_on_install=True)
print("INSTALL", r)
bpy.ops.wm.save_userpref()
'''

# Execute APRES le chargement du .blend : n'appelle pas l'updater, observe seulement.
WATCH = r'''
import sys, json, time, bpy
OUT = sys.argv[sys.argv.index("--") + 1]
T0 = time.time()
seen = []
def _watch():
    mod = next((m for m in bpy.context.preferences.addons.keys() if m.endswith("blender_multiplayer")), None)
    up = sys.modules.get(mod + ".updater") if mod else None
    st = up.state["status"] if up else "?"
    if not seen or seen[-1] != st:
        seen.append(st)
    if st == "installed" or time.time() - T0 > 40:
        json.dump({"statuses": seen, "file": bpy.data.filepath}, open(OUT, "w"))
        bpy.ops.wm.quit_blender()
        return None
    return 0.25
bpy.app.timers.register(_watch, first_interval=0.25, persistent=True)
'''


def installed_version(profile):
    for root, _dirs, files in os.walk(os.path.join(profile, "extensions")):
        if "blender_manifest.toml" in files and root.endswith("blender_multiplayer"):
            text = open(os.path.join(root, "blender_manifest.toml"), encoding="utf-8").read()
            m = re.search(r'(?m)^version = "(.*)"', text)
            return m.group(1) if m else "?"
    return None


def main():
    blender, cur_zip = sys.argv[1], os.path.abspath(sys.argv[2])
    work = tempfile.mkdtemp(prefix="mp_update_gui_")
    site = os.path.join(work, "site")
    profile = os.path.join(work, "profile")
    os.makedirs(site)
    os.makedirs(profile)
    for name, body in (("install.py", INSTALL), ("watch.py", WATCH)):
        with open(os.path.join(work, name), "w", encoding="utf-8") as fh:
            fh.write(body)

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site)
    handler.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.json" % httpd.server_address[1]
    env = dict(os.environ, BLENDER_USER_RESOURCES=profile, MP_UPDATE_INDEX=url)
    env.pop("MP_UPDATED_FROM", None)

    shutil.copy(cur_zip, site)
    make_index.main(site)
    subprocess.run([blender, "-b", "--factory-startup", "--python", os.path.join(work, "install.py"),
                    "--", cur_zip], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                   timeout=300)
    before = installed_version(profile)

    nxt = os.path.join(work, "blender_multiplayer-9.9.9.zip")
    fake_next(cur_zip, nxt)
    shutil.copy(nxt, site)
    make_index.main(site)

    blend = os.path.join(work, "scene.blend")
    subprocess.run([blender, "-b", "--factory-startup", "--python-expr",
                    "import bpy; bpy.ops.wm.save_as_mainfile(filepath=%r)" % blend],
                   stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, timeout=120)

    out = os.path.join(work, "watch.json")
    with open(out + ".log", "w") as log:
        subprocess.run([blender, blend, "--python", os.path.join(work, "watch.py"), "--", out],
                       env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    httpd.shutdown()

    after = installed_version(profile)
    watch = json.load(open(out)) if os.path.exists(out) else {}
    print("Version avant      :", before)
    print("Etats de l'updater :", " > ".join(watch.get("statuses", ["(aucun resultat)"])))
    print("Version apres      :", after)
    ok = after == "9.9.9"
    print("Dossier :", work)
    print("RESULTAT :", "SUCCES" if ok else "ECHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

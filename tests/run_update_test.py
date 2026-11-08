"""Teste la mise a jour automatique dans un profil Blender isole.

Usage : python tests/run_update_test.py <blender.exe> <zip de la version courante>

1. Fabrique une fausse version N+1 du zip et un depot local (index.json) servi
   en HTTP sur 127.0.0.1.
2. Cas "zip" : installe la version courante depuis le zip (depot user_default),
   puis lance le module updater -> doit installer N+1.
3. Cas "depot distant" : ajoute le depot HTTP dans Blender, y installe la
   version courante, publie N+1, lance updater -> doit installer N+1.
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
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import make_index  # noqa: E402

INNER = r'''
import os, sys, time, importlib, json
import bpy
MODE, ZIP, URL, OUT = sys.argv[sys.argv.index("--") + 1:]
res = {"mode": MODE, "steps": []}
def step(name, ok, detail=""):
    res["steps"].append([name, bool(ok), str(detail)])
try:
    bpy.context.preferences.system.use_online_access = True
    exts = bpy.context.preferences.extensions
    if MODE == "zip_install":
        r = bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default", enable_on_install=True)
        step("installation depuis le zip", "FINISHED" in r, r)
    elif MODE == "remote_install":
        repo = exts.repos.new(name="MP test", module="mp_test", remote_url=URL)
        idx = list(exts.repos).index(repo)
        r1 = bpy.ops.extensions.repo_sync(repo_index=idx)
        r2 = bpy.ops.extensions.package_install(repo_index=idx, pkg_id="blender_multiplayer", enable_on_install=True)
        step("installation depuis le depot distant", "FINISHED" in r2, (r1, r2))
    else:
        mod = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("blender_multiplayer"))
        pkg = importlib.import_module(mod)
        up = importlib.import_module(mod + ".updater")
        step("version avant", True, pkg.compat.ADDON_VERSION)
        step("type d'installation", True, up.install_kind())
        up.state.update(status="idle")
        up._check_thread()
        step("nouvelle version detectee", up.state["status"] == "available", up.state)
        err = up.install()
        step("lancement de l'installation", err == "", err)
        if up.install_kind()[0] == "local":
            t0 = time.time()
            while up.state["status"] == "downloading" and time.time() - t0 < 30:
                time.sleep(0.1)
            step("telechargement + verification sha256", up.state["status"] == "downloaded", up.state)
            up._install_downloaded()
        else:
            up._install_remote(up.install_kind()[2])
        step("installation", up.state["status"] == "installed", up.state)
        mod2 = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("blender_multiplayer"))
        pkg2 = sys.modules[mod2]
        step("version apres (module recharge)", tuple(pkg2.compat.ADDON_VERSION) == (9, 9, 9), pkg2.compat.ADDON_VERSION)
        step("addon toujours active", mod2 in bpy.context.preferences.addons.keys(), mod2)
        step("marqueur de mise a jour", os.environ.get("MP_UPDATED_FROM"), os.environ.get("MP_UPDATED_FROM"))
        bpy.ops.mp.check_update() if hasattr(bpy.ops, "mp") else None
    bpy.ops.wm.save_userpref()
except Exception:
    import traceback
    step("exception", False, traceback.format_exc())
json.dump(res, open(OUT, "w"), indent=1)
'''


def fake_next(src_zip, dst_zip):
    with zipfile.ZipFile(src_zip) as zin, zipfile.ZipFile(dst_zip, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith("blender_manifest.toml"):
                data = re.sub(rb'(?m)^version = ".*"', b'version = "9.9.9"', data)
            if item.filename.endswith("__init__.py") and b"bl_info" in data:
                data = re.sub(rb'"version": \(\d+, \d+, \d+\)', b'"version": (9, 9, 9)', data)
            zout.writestr(item, data)


def main():
    blender, cur_zip = sys.argv[1], os.path.abspath(sys.argv[2])
    work = tempfile.mkdtemp(prefix="mp_update_")
    site = os.path.join(work, "site")
    os.makedirs(site)
    script = os.path.join(work, "inner.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(INNER)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site)
    handler.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.json" % httpd.server_address[1]
    ok = True

    def publish(*zips):
        for f in os.listdir(site):
            os.remove(os.path.join(site, f))
        for z in zips:
            shutil.copy(z, site)
        make_index.main(site)

    nxt = os.path.join(work, "next", "blender_multiplayer-9.9.9.zip")
    os.makedirs(os.path.dirname(nxt))
    fake_next(cur_zip, nxt)

    for case, install_mode in (("zip", "zip_install"), ("depot distant", "remote_install")):
        profile = os.path.join(work, "profile_" + install_mode)
        os.makedirs(profile)
        env = dict(os.environ, BLENDER_USER_RESOURCES=profile, MP_UPDATE_INDEX=url)
        env.pop("MP_UPDATED_FROM", None)
        publish(cur_zip)
        print("== Cas %s" % case)
        for mode in (install_mode, "update"):
            if mode == "update":
                publish(cur_zip, nxt)
            out = os.path.join(work, "%s_%s.json" % (install_mode, mode))
            log = open(out + ".log", "w")
            subprocess.run([blender, "-b", "--python", script, "--", mode, cur_zip, url, out],
                           env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
            if not os.path.exists(out):
                print("  pas de resultat, voir", out + ".log")
                ok = False
                continue
            for name, good, detail in json.load(open(out))["steps"]:
                print("  [%s] %s  %s" % ("OK" if good else "ECHEC", name, detail[:160] if not good or
                                         name.startswith(("version", "type", "marqueur")) else ""))
                ok &= good
    httpd.shutdown()
    print("Dossier :", work)
    print("RESULTAT :", "SUCCES" if ok else "ECHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

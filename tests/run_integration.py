"""Lance un hote et un client Blender en arriere-plan et compare leurs scenes.

Usage : python run_integration.py <blender_hote.exe> <blender_client.exe> [port]
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main():
    host_exe = sys.argv[1]
    client_exe = sys.argv[2] if len(sys.argv) > 2 else host_exe
    port = sys.argv[3] if len(sys.argv) > 3 else "57123"
    scen = sys.argv[4] if len(sys.argv) > 4 else ""
    out = tempfile.mkdtemp(prefix="mp_test_")
    env = dict(os.environ)
    env["HOME"] = out
    env["USERPROFILE"] = out
    common = ["-b", "--factory-startup", "--python-exit-code", "1"]
    scene = [os.environ["MP_SCENE"]] if os.environ.get("MP_SCENE") else []
    host = subprocess.Popen([host_exe] + common[:1] + scene + common[1:] + ["--python", os.path.join(HERE, "host_scenario%s.py" % scen),
                                                   "--", ROOT, port, out],
                            stdout=open(os.path.join(out, "host.log"), "w"), stderr=subprocess.STDOUT,
                            env=env)
    time.sleep(2.0)
    client = subprocess.Popen([client_exe] + common + ["--python", os.path.join(HERE, "client_scenario%s.py" % scen),
                                                       "--", ROOT, port, out],
                              stdout=open(os.path.join(out, "client.log"), "w"),
                              stderr=subprocess.STDOUT, env=env)
    try:
        host.wait(timeout=400)
        client.wait(timeout=120)
    except subprocess.TimeoutExpired:
        host.kill()
        client.kill()
        print("TIMEOUT global")
    ok = True
    states = {}
    for role in ("host", "client"):
        path = os.path.join(out, role + ".json")
        if not os.path.exists(path):
            print("== %s : pas de resultat (voir %s)" % (role, os.path.join(out, role + ".log")))
            ok = False
            continue
        with open(path, encoding="utf-8") as fh:
            res = json.load(fh)
        states[role] = res.get("state")
        print("== %s" % role)
        for c in res["checks"]:
            print("  [%s] %s %s" % ("OK" if c["ok"] else "ECHEC", c["name"],
                                    "" if c["ok"] else c["detail"]))
            ok &= c["ok"]
        for e in res["errors"]:
            print("  ERREUR:", e)
            ok = False
        bad_logs = [l for l in res.get("logs", []) if "erreur" in l.lower() or "Traceback" in l]
        for l in bad_logs:
            print("  LOG:", l[:600])
    if "host" in states and "client" in states and not os.environ.get("MP_SCENE"):
        a, b = states["host"], states["client"]
        same = a == b
        print("== Convergence des scenes :", "IDENTIQUES" if same else "DIFFERENTES")
        if not same:
            ok = False
            for key in a:
                for name in sorted(set(a[key]) | set(b.get(key, {}))):
                    va, vb = a[key].get(name), b.get(key, {}).get(name)
                    if va != vb:
                        print("   %s/%s\n     hote  : %s\n     client: %s" % (key, name, va, vb))
    print("Dossier de test :", out)
    print("RESULTAT :", "SUCCES" if ok else "ECHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

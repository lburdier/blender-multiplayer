"""Port deja utilise et sessions orphelines (restes d une ancienne version).

Usage : BLENDER_USER_RESOURCES=<profil vide> blender -b --factory-startup --python tests/run_port_test.py -- <zip>
"""
import sys, socket, time, importlib, bpy
ZIP = sys.argv[sys.argv.index("--")+1]
res = []
def chk(name, ok, detail=""): res.append(ok); print("CHECK [%s] %s %s" % ("OK" if ok else "ECHEC", name, detail))
def free(port):
    s = socket.socket()
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1); s.bind(("", port)); return True
    except OSError: return False
    finally: s.close()
def mod():
    m = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("blender_multiplayer"))
    return importlib.import_module(m + ".session")
bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default", enable_on_install=True)
S = mod()
# 1. session orpheline (detachee sans arret) puis nouvel hebergement
a = S.host(5599, "x", "T"); S.current = None
chk("orphelin cree : port occupe", not free(5599))
b = S.host(5599, "x", "T")
chk("re-heberger apres orphelin : meme port", b.port == 5599, b.port)
chk("orphelin arrete", a.state == "closed" and not a.server.running)
S.leave(); time.sleep(0.2)
# 2. port pris par un autre programme
blocker = socket.socket(); blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
blocker.bind(("", 5599)); blocker.listen(1)
c = S.host(5599, "x", "T")
chk("port occupe par un autre programme : port suivant", c.port == 5600, c.port)
chk("toast port change", any("deja utilise" in t[0] if isinstance(t,(list,tuple)) else "deja utilise" in str(t) for t in c.toasts), list(c.toasts)[-1:])
S.leave(); blocker.close(); time.sleep(0.2)
# 3. orphelin d'une ancienne version puis reinstallation
d = S.host(5599, "x", "T"); S.current = None
bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default", enable_on_install=True, overwrite=True)
time.sleep(0.3)
chk("reinstallation : orphelin arrete au demarrage de l'addon", d.state == "closed" and free(5599))
S2 = mod()
e = S2.host(5599, "x", "T")
chk("hebergement apres reinstallation", e.port == 5599, e.port)
S2.leave()
# 4. erreur claire si tous les ports sont pris
blockers = []
for p in range(5599, 5609):
    k = socket.socket(); k.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1); k.bind(("", p)); blockers.append(k)
try:
    S2.host(5599, "x", "T"); chk("tous les ports pris -> erreur", False)
except OSError as exc:
    chk("tous les ports pris -> erreur claire", "deja utilises" in str(exc), str(exc)[:120])
chk("pas de session laissee", S2.current is None)
print("RESULTAT", "SUCCES" if all(res) else "ECHEC")

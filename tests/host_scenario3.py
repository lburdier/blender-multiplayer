"""Scenario 3 (hote) : textures (fichiers, UDIM, peinture), lumieres, reglages de
rendu independants, deplacements fluides, expiration de l'historique."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402

import bpy  # noqa: E402

T.ROLE = "host"
session = T.session
T.mp.prefs._defaults.history_limit = 30


def save_png(path, rgba, size=8):
    img = bpy.data.images.new("tmp_save", size, size)
    img.pixels.foreach_set(list(rgba) * (size * size))
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def px(img):
    return [round(x, 2) for x in img.pixels[:4]]


try:
    texdir = os.path.join(T.OUT, "textures_hote")
    os.makedirs(texdir, exist_ok=True)
    ext_path = os.path.join(texdir, "bois.png")
    save_png(ext_path, (1, 0, 0, 1))
    tex = bpy.data.images.load(ext_path)
    tex.name = "TexExterne"
    mat = bpy.data.materials.new("MatTex")
    mat.use_nodes = True
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = tex
    mat.node_tree.links.new(node.outputs["Color"], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    cube = bpy.data.objects["Cube"]
    cube.data.materials.append(mat)
    # UDIM : 2 tuiles
    save_png(os.path.join(texdir, "udim.1001.png"), (1, 1, 0, 1))
    save_png(os.path.join(texdir, "udim.1002.png"), (0, 1, 1, 1))
    udim = bpy.data.images.load(os.path.join(texdir, "udim.1001.png"))
    udim.source = "TILED"
    udim.name = "TexUdim"
    node2 = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node2.image = udim
    # Texture peinte non enregistree
    peinte = bpy.data.images.new("Peinte", 16, 16)
    peinte.pixels.foreach_set([0, 1, 0, 1] * 256)
    peinte.update()
    node3 = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node3.image = peinte
    # Lumiere
    bpy.ops.object.light_add(type="POINT", location=(0, 0, 4))
    lum = bpy.context.active_object
    lum.name = "Lampe"
    # Reglages de rendu de l'hote
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = 77
    sc.render.resolution_x = 1111
    T.pump(0.3)

    s = session.host(T.PORT, "", "Test3", username="Hote")
    T.wait_for(lambda: len(s.users) == 2, 60, "arrivee du client")
    T.barrier("CLIENT_READY", 90)

    # H1 : modifications de l'hote
    save_png(ext_path, (0, 0, 1, 1))                      # fichier modifie sur le disque
    peinte.pixels.foreach_set([1, 0, 1, 1] * 256)         # "peinture"
    peinte.update()
    L = lum.data
    L.color = (0.1, 0.5, 0.9)
    L.energy = 321.0
    L.shadow_soft_size = 0.42
    if hasattr(getattr(L, "cycles", None), "max_bounces"):
        L.cycles.max_bounces = 3
    if hasattr(L, "use_temperature"):
        L.use_temperature = True
        L.temperature = 3300.0
    if getattr(L, "node_tree", None) is None and hasattr(L, "use_nodes"):
        L.use_nodes = True
    if getattr(L, "node_tree", None) is not None:
        L.node_tree.nodes.new("ShaderNodeBlackbody").name = "CorpsNoir"
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.cycles.samples = 99
    sc.render.resolution_x = 1234
    T.pump(1.0)
    T.say("H1")

    # H2 : deplacement continu du cube
    T.barrier("C1", 60)
    before = sum(1 for e in s.history.entries if e["user"] != s.me)
    for i in range(31):
        cube.location.x = i * 0.1
        T.pump(0.033)
    T.pump(1.5)
    after = sum(1 for e in s.history.entries if e["user"] != s.me)
    T.check("aucun echo du client pendant le glissement", after == before, (before, after))
    T.say("H2")
    T.barrier("C2", 60)
    T.wait_for(lambda: px(peinte) == [1.0, 1.0, 0.0, 1.0], 15, "peinture du client")
    T.check("peinture du client recue", px(peinte) == [1.0, 1.0, 0.0, 1.0], px(peinte))
    T.check("energie modifiee par le client", round(L.energy) == 55, L.energy)
    T.check("reglages de rendu de l'hote intacts", sc.render.engine == "BLENDER_WORKBENCH"
            and sc.cycles.samples == 99, (sc.render.engine, sc.cycles.samples))

    # H3 : expiration de l'historique
    for i in range(50):
        o = bpy.data.objects.new("Vide%02d" % i, None)
        sc.collection.objects.link(o)
        T.pump(0.07)
    T.pump(8.0)
    n = len(s.history.entries)
    archives = os.path.join(s.session_dir, "archives")
    files = os.listdir(archives) if os.path.isdir(archives) else []
    T.check("actions anciennes expirees", n <= 40, n)
    T.check("archive du projet creee", any(f.endswith(".blend") for f in files), files)
    T.say("H3")
    T.barrier("END", 60)
    T.pump(1.0)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
    traceback.print_exc()
T.finish()
if T.session.current is not None:
    T.session.current.stop()

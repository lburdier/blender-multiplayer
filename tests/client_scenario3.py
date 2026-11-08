import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402

import bpy  # noqa: E402

T.ROLE = "client"
session = T.session


def px(img):
    return [round(x, 2) for x in img.pixels[:4]]


def eevee():
    for name in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            bpy.context.scene.render.engine = name
            return name
        except TypeError:
            continue
    return bpy.context.scene.render.engine


try:
    # Reglages de rendu propres au client, avant de rejoindre
    my_engine = eevee()
    bpy.context.scene.cycles.samples = 33
    bpy.context.scene.cycles.device = "GPU"          # propre a ce PC
    bpy.context.scene.cycles.preview_samples = 7     # qualite de la vue 3D : propre a ce PC
    s = None
    for _ in range(40):
        try:
            s = session.join("127.0.0.1", T.PORT, "", username="Client")
            break
        except OSError:
            time.sleep(0.5)
    T.wait_for(lambda: s.active, 90, "synchronisation initiale")
    T.pump(1.0)
    sc = bpy.context.scene
    tex = bpy.data.images.get("TexExterne")
    T.check("texture externe recue (fichier copie)", tex is not None and os.path.isfile(bpy.path.abspath(tex.filepath))
            and ("assets" in tex.filepath or "cache" in tex.filepath), tex.filepath if tex else None)
    T.check("texture externe affichee (pixels)", tex is not None and px(tex) == [1.0, 0.0, 0.0, 1.0],
            px(tex) if tex else None)
    udim = bpy.data.images.get("TexUdim")
    T.check("image UDIM presente", udim is not None and udim.source == "TILED")
    if udim is not None:
        folder = os.path.dirname(bpy.path.abspath(udim.filepath))
        tiles = sorted(f for f in os.listdir(folder)) if os.path.isdir(folder) else []
        T.check("tuiles UDIM recues", len([t for t in tiles if t.startswith("udim.")]) == 2, tiles)
    peinte = bpy.data.images.get("Peinte")
    T.check("texture peinte non enregistree recue", peinte is not None and px(peinte) == [0.0, 1.0, 0.0, 1.0],
            px(peinte) if peinte else None)
    T.check("moteur de rendu de l'hote recu", sc.render.engine == "CYCLES", sc.render.engine)
    T.check("echantillons de l'hote recus", sc.cycles.samples == 77, sc.cycles.samples)
    T.check("peripherique du client conserve", sc.cycles.device == "GPU", sc.cycles.device)
    T.check("echantillons de la vue 3D du client conserves", sc.cycles.preview_samples == 7,
            sc.cycles.preview_samples)
    T.check("resolution de sortie partagee", sc.render.resolution_x == 1111, sc.render.resolution_x)
    T.say("CLIENT_READY")

    T.barrier("H1", 60)
    T.wait_for(lambda: px(bpy.data.images["TexExterne"]) == [0.0, 0.0, 1.0, 1.0], 20, "texture mise a jour")
    T.wait_for(lambda: px(bpy.data.images["Peinte"]) == [1.0, 0.0, 1.0, 1.0], 20, "peinture de l'hote")
    T.pump(0.5)
    tex = bpy.data.images["TexExterne"]
    peinte = bpy.data.images["Peinte"]
    T.check("fichier de texture modifie : mis a jour", px(tex) == [0.0, 0.0, 1.0, 1.0], px(tex))
    T.check("peinture de l'hote recue", px(peinte) == [1.0, 0.0, 1.0, 1.0], px(peinte))
    L = bpy.data.objects["Lampe"].data
    T.check("lumiere : couleur, puissance, rayon", [round(x, 2) for x in L.color] == [0.1, 0.5, 0.9]
            and round(L.energy) == 321 and round(L.shadow_soft_size, 2) == 0.42,
            ([round(x, 2) for x in L.color], L.energy, L.shadow_soft_size))
    if hasattr(getattr(L, "cycles", None), "max_bounces"):
        T.check("lumiere : reglage Cycles (rebonds)", L.cycles.max_bounces == 3, L.cycles.max_bounces)
    if hasattr(L, "use_temperature"):
        T.check("lumiere : temperature", L.use_temperature and round(L.temperature) == 3300,
                (L.use_temperature, L.temperature))
    if getattr(L, "node_tree", None) is not None:
        T.wait_for(lambda: bpy.data.objects["Lampe"].data.node_tree.nodes.get("CorpsNoir") is not None, 10,
                   "noeud de lumiere")
        L = bpy.data.objects["Lampe"].data
        T.check("lumiere : noeuds", L.node_tree.nodes.get("CorpsNoir") is not None,
                [n.name for n in L.node_tree.nodes])
    T.check("changement de moteur de l'hote recu", sc.render.engine == "BLENDER_WORKBENCH", sc.render.engine)
    T.check("changement d'echantillons de l'hote recu", sc.cycles.samples == 99, sc.cycles.samples)
    T.check("peripherique du client toujours conserve", sc.cycles.device == "GPU", sc.cycles.device)
    T.check("vue 3D du client toujours conservee", sc.cycles.preview_samples == 7, sc.cycles.preview_samples)
    T.check("resolution mise a jour", sc.render.resolution_x == 1234, sc.render.resolution_x)
    T.say("C1")

    # Deplacement continu : on echantillonne la position du cube a chaque image
    T.barrier("H2", 60)
    cube = bpy.data.objects["Cube"]
    T.check("deplacement : position finale exacte", round(cube.location.x, 4) == 3.0, cube.location.x)
    T.say("C2")
    # Le client peint et modifie la lumiere
    peinte.pixels.foreach_set([1, 1, 0, 1] * 256)
    peinte.update()
    bpy.data.objects["Lampe"].data.energy = 55.0
    T.pump(2.5)

    T.barrier("H3", 90)
    T.pump(1.0)
    T.check("historique allege chez le client", len(s.hist) <= 45, len(s.hist))
    T.say("END")
    T.pump(1.0)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
    traceback.print_exc()
T.finish()
if T.session.current is not None:
    T.session.current.stop()

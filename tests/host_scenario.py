import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402

import bpy  # noqa: E402

T.ROLE = "host"
session = T.session
try:
    cube = bpy.data.objects["Cube"]
    mat = bpy.data.materials.new("Rouge")
    mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (1, 0, 0, 1)
    cube.data.materials.append(mat)
    bpy.ops.mesh.primitive_uv_sphere_add(location=(4, 0, 0))
    sphere = bpy.context.active_object
    sphere.name = "HostSphere"

    s = session.host(T.PORT, "secret", "Test", username="Hote", online=T.ONLINE)
    if T.ONLINE:
        T.wait_for(lambda: s.public_address(), 30, "adresse Internet")
        T.check("adresse Internet ouverte", bool(s.public_address()), s.tunnel.error)
        if T.VIA in ("web", "blocked"):
            t0 = time.time()
            T.wait_for(lambda: s.web_address(), 240, "voie HTTPS")
            T.check("voie HTTPS ouverte", bool(s.web_address()), s.web.error if s.web else "absente")
            T.log("voie HTTPS prete en %.1f s : %s" % (time.time() - t0, s.web_address()))
        with open(T.INVITE_FILE + ".tmp", "w", encoding="utf-8") as fh:
            fh.write(s.invite_link())
        os.replace(T.INVITE_FILE + ".tmp", T.INVITE_FILE)
        T.log("lien:", s.invite_link())
    T.wait_for(lambda: len(s.users) == 2, 40, "arrivee du client")
    T.barrier("CLIENT_READY", 60)

    # 1. L'hote deplace le cube
    cube.location = (1, 2, 3)
    T.pump(0.5)
    seq_after_s1 = s.history.seq
    T.say("S1")

    # 2. Le client cree un cone et change la couleur du materiau
    T.barrier("C1")
    T.wait_for(lambda: "ClientCone" in bpy.data.objects, 10, "cone du client")
    cone = bpy.data.objects.get("ClientCone")
    T.check("creation distante (cone)", cone is not None)
    T.check("position du cone", cone is not None and tuple(round(x, 3) for x in cone.location) == (0, 4, 0),
            cone.location if cone else None)
    T.wait_for(lambda: round(bpy.data.materials["Rouge"].node_tree.nodes["Principled BSDF"]
                             .inputs["Base Color"].default_value[1], 3) == 1.0, 10, "materiau vert")
    col = tuple(bpy.data.materials["Rouge"].node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value)
    T.check("materiau modifie a distance (vert)", round(col[1], 3) == 1.0 and round(col[0], 3) == 0.0, col)

    # 3. Ctrl+Z du client : sa couleur revient au rouge, le cone reste
    T.say("S2")
    T.barrier("C2")
    T.wait_for(lambda: round(bpy.data.materials["Rouge"].node_tree.nodes["Principled BSDF"]
                             .inputs["Base Color"].default_value[0], 3) == 1.0, 10, "annulation couleur")
    col = tuple(bpy.data.materials["Rouge"].node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value)
    T.check("Ctrl+Z distant restaure la couleur", round(col[0], 3) == 1.0 and round(col[1], 3) == 0.0, col)
    T.check("Ctrl+Z distant ne touche pas au reste", "ClientCone" in bpy.data.objects)

    # 4. Modifications simultanees du meme champ
    T.say("S3")
    cube.location = (5, 5, 5)
    T.pump(0.2)
    T.barrier("C3")
    T.pump(1.0)

    # 5. Verrou : le client edite le cone (mode Edition puis Sculpt)
    T.barrier("C4_EDITING")
    cone = bpy.data.objects["ClientCone"]
    T.wait_for(lambda: s.locks.get(T.mp.ids.get_uid(cone)) is not None, 10, "verrou")
    T.check("verrou pose par le mode Sculpt distant", s.locks.get(T.mp.ids.get_uid(cone)) is not None)
    T.wait_for(lambda: min(v.co.z for v in cone.data.vertices) > 0.5, 10, "geometrie en direct")
    T.check("edition de maillage en direct (mode Edition)", min(v.co.z for v in cone.data.vertices) > 0.5,
            min(v.co.z for v in cone.data.vertices))
    cone.location.x = 99
    T.pump(0.6)
    T.check("modif refusee sur objet verrouille", round(cone.location.x, 3) != 99, cone.location.x)
    T.say("S4")
    T.barrier("C4_DONE")
    T.pump(0.5)

    # 6. Suppression puis Ctrl+Z de l'hote
    bpy.data.objects.remove(bpy.data.objects["HostSphere"])
    T.pump(0.6)
    T.say("S5")
    T.barrier("C5")
    s.undo()
    T.pump(1.0)
    T.check("Ctrl+Z recree l'objet supprime", "HostSphere" in bpy.data.objects)
    hs = bpy.data.objects.get("HostSphere")
    T.check("objet recree au bon endroit", hs is not None and round(hs.location.x, 3) == 4.0)
    T.check("objet recree dans la scene", hs is not None and hs.name in bpy.context.scene.objects)
    T.say("S6")
    T.barrier("C6")

    # 7. Le client ajoute modificateur / collection / parent
    T.barrier("C7")
    T.pump(0.5)
    cube = bpy.data.objects["Cube"]
    cone = bpy.data.objects["ClientCone"]
    T.check("modificateur distant", any(m.type == "SUBSURF" for m in cube.modifiers),
            [m.type for m in cube.modifiers])
    T.check("collection distante", "Groupe" in bpy.data.collections and
            cone.name in bpy.data.collections["Groupe"].objects)
    T.check("parent distant", cone.parent == cube)

    # 8. Retour a un point de l'historique
    s.hist_revert(seq_after_s1)
    T.pump(1.0)
    T.check("retour au point : cone absent", "ClientCone" not in bpy.data.objects)
    T.check("retour au point : cube a (1,2,3)",
            tuple(round(x, 3) for x in bpy.data.objects["Cube"].location) == (1, 2, 3),
            bpy.data.objects["Cube"].location)
    T.check("retour au point : plus de modificateur", len(bpy.data.objects["Cube"].modifiers) == 0)
    T.check("retour au point : collection absente", "Groupe" not in bpy.data.collections)
    T.say("S8")
    T.barrier("C8")

    # 9. Annuler le retour
    s.undo()
    T.pump(1.0)
    T.check("annuler le retour : cone revenu", "ClientCone" in bpy.data.objects)
    T.check("annuler le retour : modificateur revenu",
            any(m.type == "SUBSURF" for m in bpy.data.objects["Cube"].modifiers))
    cone = bpy.data.objects.get("ClientCone")
    T.check("annuler le retour : parent revenu", cone is not None and cone.parent == bpy.data.objects["Cube"])
    T.check("annuler le retour : geometrie revenue", cone is not None and
            min(v.co.z for v in cone.data.vertices) > 0.5)
    T.say("S9")
    T.barrier("C9")

    # 10. Sauvegardes
    path = s.checkpoint("test")
    T.check("point de sauvegarde ecrit", os.path.isfile(path), path)
    ap = s.autosave()
    T.check("autosauvegarde ecrite", os.path.isfile(ap), ap)
    T.check("journal d'historique", os.path.isfile(os.path.join(s.session_dir, "history.jsonl")))

    T.pump(0.5)
    T.say("END")
    T.barrier("C_END")
    T.pump(1.5)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
    traceback.print_exc()
T.finish()
if T.session.current is not None:
    T.session.current.stop()

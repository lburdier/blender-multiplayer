import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402

import bmesh  # noqa: E402
import bpy  # noqa: E402

T.ROLE = "client"
session = T.session


def color():
    return tuple(round(x, 3) for x in bpy.data.materials["Rouge"].node_tree.nodes["Principled BSDF"]
                 .inputs["Base Color"].default_value)


try:
    import time
    s = None
    if T.ONLINE:
        T.wait_for(lambda: os.path.exists(T.INVITE_FILE), 60, "lien d'invitation")
        link = T.mp.invite.parse(open(T.INVITE_FILE, encoding="utf-8").read())
        T.check("lien d'invitation lisible", link is not None and link["password"] == "secret", link)
        # On ne garde que l'adresse Internet pour tester vraiment le relais.
        pub = [a for a in link["addresses"] if a[0] == "bore.pub"]
        T.check("lien avec adresse Internet", len(pub) == 1, link["addresses"])
        s = session.join_any(pub, link["password"], username="Client")
        T.log("connecte via", pub)
    for _ in range(40):
        if s is not None:
            break
        try:
            s = session.join("127.0.0.1", T.PORT, "secret", username="Client")
            break
        except OSError:
            time.sleep(0.5)
    T.wait_for(lambda: s.active, 60, "synchronisation initiale")
    T.check("scene recue", "Cube" in bpy.data.objects and "HostSphere" in bpy.data.objects)
    T.check("materiau recu", "Rouge" in bpy.data.materials and color()[0] == 1.0, color())
    T.say("CLIENT_READY")

    T.barrier("S1")
    cube = bpy.data.objects["Cube"]
    T.wait_for(lambda: tuple(round(x, 3) for x in cube.location) == (1, 2, 3), 10, "cube deplace")
    T.check("deplacement distant", tuple(round(x, 3) for x in cube.location) == (1, 2, 3), cube.location)

    bpy.ops.mesh.primitive_cone_add(location=(0, 4, 0))
    cone = bpy.context.active_object
    cone.name = "ClientCone"
    T.pump(0.5)
    bpy.data.materials["Rouge"].node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0, 1, 0, 1)
    T.pump(0.8)
    T.say("C1")

    T.barrier("S2")
    s.undo()
    T.wait_for(lambda: color()[0] == 1.0, 10, "annulation locale")
    T.check("Ctrl+Z multijoueur (couleur)", color()[0] == 1.0 and color()[1] == 0.0, color())
    T.check("Ctrl+Z garde le cone", "ClientCone" in bpy.data.objects)
    T.say("C2")

    T.barrier("S3")
    bpy.data.objects["Cube"].location = (-5, -5, -5)
    T.pump(1.0)
    T.say("C3")

    # Mode Edition : verrou + geometrie en direct
    cone = bpy.data.objects["ClientCone"]
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = cone
    cone.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(cone.data)
    for v in bm.verts:
        v.co.z += 2.0
    bmesh.update_edit_mesh(cone.data)
    T.pump(1.2)
    T.say("C4_EDITING")
    T.barrier("S4")
    T.check("objet verrouille non deplace par l'autre", round(cone.location.x, 3) == 0.0, cone.location.x)
    bpy.ops.object.mode_set(mode="OBJECT")
    T.pump(0.8)
    T.say("C4_DONE")

    T.barrier("S5")
    T.wait_for(lambda: "HostSphere" not in bpy.data.objects, 10, "suppression distante")
    T.check("suppression distante", "HostSphere" not in bpy.data.objects)
    T.say("C5")
    T.barrier("S6")
    T.wait_for(lambda: "HostSphere" in bpy.data.objects, 10, "objet recree")
    T.check("objet recree par le Ctrl+Z de l'hote", "HostSphere" in bpy.data.objects)
    T.check("objet recree visible", "HostSphere" in bpy.context.scene.objects)
    T.say("C6")

    cube = bpy.data.objects["Cube"]
    cone = bpy.data.objects["ClientCone"]
    cube.modifiers.new("Subdiv", "SUBSURF")
    coll = bpy.data.collections.new("Groupe")
    bpy.context.scene.collection.children.link(coll)
    coll.objects.link(cone)
    cone.parent = cube
    T.pump(1.0)
    T.say("C7")

    T.barrier("S8")
    T.wait_for(lambda: "ClientCone" not in bpy.data.objects, 10, "retour au point")
    T.check("retour au point recu", "ClientCone" not in bpy.data.objects and
            len(bpy.data.objects["Cube"].modifiers) == 0)
    T.say("C8")
    T.barrier("S9")
    T.wait_for(lambda: "ClientCone" in bpy.data.objects, 10, "annulation du retour")
    T.check("annulation du retour recue", "ClientCone" in bpy.data.objects)
    T.say("C9")

    T.barrier("END")
    T.pump(1.0)
    T.say("C_END")
    T.pump(1.0)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
    traceback.print_exc()
T.finish()
if T.session.current is not None:
    T.session.current.stop()

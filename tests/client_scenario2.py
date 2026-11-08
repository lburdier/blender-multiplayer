import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402

import bpy  # noqa: E402

T.ROLE = "client"
session = T.session

try:
    s = None
    for _ in range(40):
        try:
            s = session.join("127.0.0.1", T.PORT, "", username="Client")
            break
        except OSError:
            time.sleep(0.5)
    T.wait_for(lambda: s.active, 60, "synchronisation initiale")
    T.say("CLIENT_READY")

    T.barrier("H1", 60)
    T.wait_for(lambda: all(n in bpy.data.objects for n in
                           ("Spot", "Spot.001", "PlanGN", "Courbe", "Squelette", "Texte")), 15, "objets")
    T.pump(1.0)
    ob = bpy.data.objects
    T.check("lumiere", "Spot" in ob and ob["Spot"].data.type == "SPOT" and round(ob["Spot"].data.energy) == 1234
            and round(ob["Spot"].data.spot_size, 2) == 0.8)
    T.check("camera (focale + mise au point)", round(ob["Camera"].data.lens) == 85 and
            ob["Camera"].data.dof.focus_object == ob["Cube"])
    sc = bpy.context.scene
    sc.frame_set(20)
    T.check("animation (images cles)", round(ob["Cube"].location.z, 2) == 10.0, ob["Cube"].location)
    sc.frame_set(1)
    gn = ob["PlanGN"].modifiers.get("GN")
    ng = bpy.data.node_groups.get("MaGeo")
    ident = next(i.identifier for i in ng.interface.items_tree if i.name == "Taille") if ng else None
    T.check("geometry nodes (groupe + liens)", ng is not None and len(ng.links) == 2 and gn is not None
            and gn.node_group == ng)
    T.check("geometry nodes (valeur d'entree)", gn is not None and round(gn[ident], 2) == 2.5)
    mat = bpy.data.materials.get("AvecImage")
    T.check("materiau avec image", mat is not None and any(n.type == "TEX_IMAGE" and n.image and
                                                           n.image.name == "Tex" for n in mat.node_tree.nodes))
    T.check("image generee", "Tex" in bpy.data.images and tuple(bpy.data.images["Tex"].size) == (64, 64))
    T.check("courbe", ob["Courbe"].type == "CURVE" and len(ob["Courbe"].data.splines[0].bezier_points) == 2)
    T.check("courbe masquee", ob["Courbe"].hide_get())
    T.check("armature + pose", ob["Squelette"].type == "ARMATURE" and
            round(ob["Squelette"].pose.bones[0].rotation_quaternion[1], 2) == 0.71,
            tuple(ob["Squelette"].pose.bones[0].rotation_quaternion))
    T.check("texte", ob["Texte"].data.body == "Bonjour" and ob["Texte"].hide_render)
    T.check("doublon distinct", T.mp.ids.get_uid(ob["Spot"]) != T.mp.ids.get_uid(ob["Spot.001"]))

    gn[ident] = 4.0
    ob["PlanGN"].update_tag()
    ob["Spot"].data.energy = 500
    ob["Camera"].name = "CamPrincipale"
    cube = ob["Cube"]
    sc.frame_set(40)
    cube.location = (5, 5, 5)
    cube.keyframe_insert("location", frame=40)
    sc.frame_set(1)
    ob["Texte"].data.body = "Salut"
    ob["Squelette"].pose.bones[0].rotation_quaternion = (0.5, 0.5, 0.5, 0.5)
    T.pump(1.0)
    # Ajout d'un os en mode Edition d'armature
    arm = ob["Squelette"]
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.data.edit_bones.new("OsClient")
    eb.head = (0, 0, 1)
    eb.tail = (0, 0, 2)
    T.pump(1.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    T.pump(1.5)
    T.say("K1")

    T.barrier("H2")
    jum = bpy.data.objects.new("Jumeau", None)
    bpy.context.scene.collection.objects.link(jum)
    T.pump(0.3)
    T.say("K2")
    T.pump(1.5)
    names = sorted(o.name for o in bpy.data.objects if o.name.startswith("Jumeau"))
    T.check("conflit de noms resolu (client)", names == ["Jumeau", "Jumeau.001"], names)

    T.barrier("END")
    T.pump(1.0)
    T.say("K_END")
    T.pump(1.0)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
    traceback.print_exc()
T.finish()
if T.session.current is not None:
    T.session.current.stop()

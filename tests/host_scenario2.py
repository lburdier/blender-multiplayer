import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402

import bpy  # noqa: E402

T.ROLE = "host"
session = T.session


def active():
    return bpy.context.view_layer.objects.active


def select_only(o):
    for x in bpy.context.view_layer.objects:
        x.select_set(False)
    o.select_set(True)
    bpy.context.view_layer.objects.active = o


try:
    s = session.host(T.PORT, "", "Test2", username="Hote")
    T.wait_for(lambda: len(s.users) == 2, 40, "arrivee du client")
    T.barrier("CLIENT_READY", 60)

    bpy.ops.object.light_add(type="SPOT", location=(0, 0, 5))
    spot = active()
    spot.name = "Spot"
    spot.data.energy = 1234
    spot.data.spot_size = 0.8
    spot.data.color = (1, 0.5, 0.2)
    cam = bpy.data.objects["Camera"]
    cam.data.lens = 85
    cam.data.dof.use_dof = True
    cam.data.dof.focus_object = bpy.data.objects["Cube"]

    cube = bpy.data.objects["Cube"]
    cube.location = (0, 0, 0)
    cube.keyframe_insert("location", frame=1)
    cube.location = (0, 0, 10)
    cube.keyframe_insert("location", frame=20)

    ng = bpy.data.node_groups.new("MaGeo", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Taille", in_out="INPUT", socket_type="NodeSocketFloat")
    gi = ng.nodes.new("NodeGroupInput")
    go = ng.nodes.new("NodeGroupOutput")
    tr = ng.nodes.new("GeometryNodeTransform")
    ng.links.new(gi.outputs[0], tr.inputs[0])
    ng.links.new(tr.outputs[0], go.inputs[0])
    bpy.ops.mesh.primitive_plane_add(location=(-4, 0, 0))
    plane = active()
    plane.name = "PlanGN"
    mod = plane.modifiers.new("GN", "NODES")
    mod.node_group = ng
    ident = next(i.identifier for i in ng.interface.items_tree if i.name == "Taille")
    mod[ident] = 2.5

    img = bpy.data.images.new("Tex", 64, 64)
    mat = bpy.data.materials.new("AvecImage")
    mat.use_nodes = True
    tn = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tn.image = img
    mat.node_tree.links.new(tn.outputs[0], mat.node_tree.nodes["Principled BSDF"].inputs[0])
    plane.data.materials.append(mat)

    bpy.ops.curve.primitive_bezier_curve_add(location=(0, -4, 0))
    active().name = "Courbe"
    bpy.ops.object.armature_add(location=(4, 4, 0))
    arm = active()
    arm.name = "Squelette"
    arm.pose.bones[0].rotation_quaternion = (0.707, 0.707, 0, 0)
    bpy.ops.object.text_add(location=(0, 0, 3))
    txt = active()
    txt.name = "Texte"
    txt.data.body = "Bonjour"
    txt.hide_render = True

    select_only(spot)
    bpy.ops.object.duplicate()
    dup = active()
    dup.location.x += 3
    bpy.data.objects["Courbe"].hide_set(True)
    T.pump(2.0)
    T.check("doublon (Maj+D) a un UID distinct",
            T.mp.ids.get_uid(dup) != T.mp.ids.get_uid(spot), (dup.name, T.mp.ids.get_uid(dup)))
    T.say("H1")

    T.barrier("K1", 40)
    T.pump(1.0)
    plane = bpy.data.objects["PlanGN"]
    T.check("entree Geometry Nodes modifiee a distance", round(plane.modifiers["GN"][ident], 3) == 4.0,
            plane.modifiers["GN"][ident])
    T.check("energie de lumiere modifiee a distance",
            round(bpy.data.objects["Spot"].data.energy, 1) == 500.0, bpy.data.objects["Spot"].data.energy)
    T.check("renommage distant", "CamPrincipale" in bpy.data.objects and "Camera" not in bpy.data.objects)
    sc = bpy.context.scene
    sc.frame_set(40)
    T.check("image cle ajoutee a distance",
            tuple(round(x, 2) for x in bpy.data.objects["Cube"].location) == (5, 5, 5),
            bpy.data.objects["Cube"].location)
    sc.frame_set(1)
    T.check("texte modifie a distance", bpy.data.objects["Texte"].data.body == "Salut",
            bpy.data.objects["Texte"].data.body)
    T.check("pose modifiee a distance",
            tuple(round(x, 2) for x in bpy.data.objects["Squelette"].pose.bones[0].rotation_quaternion)
            == (0.5, 0.5, 0.5, 0.5))
    T.check("nouvel os cree a distance (mode Edition armature)",
            "OsClient" in bpy.data.objects["Squelette"].data.bones,
            [b.name for b in bpy.data.objects["Squelette"].data.bones])

    # Conflit de noms : les deux joueurs creent "Jumeau" en meme temps
    T.say("H2")
    jum = bpy.data.objects.new("Jumeau", None)
    bpy.context.scene.collection.objects.link(jum)
    T.pump(0.3)
    T.barrier("K2")
    T.pump(1.5)
    names = sorted(o.name for o in bpy.data.objects if o.name.startswith("Jumeau"))
    T.check("conflit de noms resolu", names == ["Jumeau", "Jumeau.001"], names)

    # Lecture de la timeline : aucune operation ne doit partir
    before = s.history.seq
    for f in range(1, 30):
        sc.frame_set(f)
        T.pump(0.02)
    T.pump(0.5)
    T.check("changer de frame ne cree pas d'operation", s.history.seq == before,
            [e["label"] for e in s.history.entries[-5:]])
    sc.frame_set(1)

    T.say("END")
    T.barrier("K_END")
    T.pump(1.5)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
    traceback.print_exc()
T.finish()
if T.session.current is not None:
    T.session.current.stop()

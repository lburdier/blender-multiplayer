"""Scenario L : le shading d'un objet edite doit rester identique chez l'autre joueur.

Un joueur passe en mode Edition et deplace un sommet ; l'autre compare les
attributs de shading (sharp_face, sharp_edge) et les normales de chaque coin,
pendant l'edition puis apres la sortie du mode. Les roles s'inversent ensuite.
"""
import json
import os

import bmesh
import bpy

NAMES = ("Cube", "Mixte", "Lisse", "Groupe")


def build_scene():
    """Cube plat (scene par defaut), mixte (faces plates + aretes vives) et lisse."""
    cube = bpy.data.objects["Cube"]
    bpy.ops.mesh.primitive_cube_add(location=(4, 0, 0))
    mixed = bpy.context.object
    mixed.name = "Mixte"
    mixed.data.name = "MixteMesh"
    bm = bmesh.new()
    bm.from_mesh(mixed.data)
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    for i, face in enumerate(bm.faces):
        face.smooth = i % 2 == 0
        face.material_index = i % 2
    bm.edges[0].smooth = False
    bm.to_mesh(mixed.data)
    bm.free()
    for name in ("MatA", "MatB"):
        mixed.data.materials.append(bpy.data.materials.new(name))
    col = mixed.data.color_attributes.new("Couleur", "FLOAT_COLOR", "POINT")
    for i, d in enumerate(col.data):
        d.color = (i / 8.0, 0.5, 1.0 - i / 8.0, 1.0)
    bpy.ops.mesh.primitive_uv_sphere_add(location=(-4, 0, 0), segments=16, ring_count=8)
    sphere = bpy.context.object
    sphere.name = "Lisse"
    sphere.data.name = "LisseMesh"
    sphere.data.shade_smooth()
    # un groupe de sommets fait passer le maillage par l'autre chemin d'envoi (blob .blend)
    bpy.ops.mesh.primitive_cube_add(location=(0, 4, 0))
    grouped = bpy.context.object
    grouped.name = "Groupe"
    grouped.data.name = "GroupeMesh"
    grouped.vertex_groups.new(name="G").add([0, 1, 2], 1.0, "REPLACE")
    bpy.context.view_layer.objects.active = cube
    return cube


def shading(name):
    obj = bpy.data.objects[name]
    if obj.mode == "EDIT":
        obj.update_from_editmode()
        # en mode Edition, me.attributes ne decrit que le BMesh (tableaux vides)
        me = obj.to_mesh()
    else:
        me = obj.data
    out = {}
    for attr, size in (("sharp_face", len(me.polygons)), ("sharp_edge", len(me.edges))):
        a = me.attributes.get(attr)
        out[attr] = [bool(d.value) for d in a.data] if a is not None else [False] * size
    out["smooth"] = [bool(p.use_smooth) for p in me.polygons]
    out["mat"] = [p.material_index for p in me.polygons]
    # 3 decimales : assez pour voir un cube lisse (normales diagonales) contre un cube plat
    out["normals"] = [[round(x, 3) for x in n.vector] for n in me.corner_normals]
    out["sum"] = [round(sum(v.co[i] for v in me.vertices), 3) for i in range(3)]
    uv = me.uv_layers.get("UVMap")
    out["uv"] = [[round(x, 3) for x in d.vector] for d in uv.uv] if uv is not None else None
    col = me.attributes.get("Couleur")
    out["col"] = [[round(x, 3) for x in d.color] for d in col.data] if col is not None else None
    if obj.mode == "EDIT":
        obj.to_mesh_clear()
    return out


def publish(T, name):
    with open(os.path.join(T.OUT, name + ".json"), "w") as fh:
        json.dump({n: shading(n) for n in NAMES}, fh)


def compare(T, name):
    with open(os.path.join(T.OUT, name + ".json")) as fh:
        want = json.load(fh)
    for n in NAMES:
        got = shading(n)
        for key in ("sum", "smooth", "sharp_face", "sharp_edge", "mat", "normals", "uv", "col"):
            T.check("%s / %s : %s" % (name, n, key), want[n][key] == got[key],
                    "attendu %s, recu %s" % (str(want[n][key])[:300], str(got[key])[:300]))


def edit_move(T, obj_name, delta):
    """Passe en Edition, deplace un sommet, et laisse la lecture periodique travailler."""
    obj = bpy.data.objects[obj_name]
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in list(bpy.context.selected_objects):
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[0].co.z += delta
    bmesh.update_edit_mesh(obj.data)
    obj.data.update_tag()
    T.pump(6.0)


def leave_edit(T):
    bpy.ops.object.mode_set(mode="OBJECT")
    T.pump(2.0)


def editor(T, tag):
    for n, d in zip(NAMES, (0.4, 0.3, 0.2, 0.25)):
        edit_move(T, n, d)
        publish(T, tag + "_edit_" + n)
        T.say(tag + "_EDIT_" + n)
        T.barrier(tag + "_EDIT_OK_" + n, 60)
        leave_edit(T)
        publish(T, tag + "_done_" + n)
        T.say(tag + "_DONE_" + n)
        T.barrier(tag + "_DONE_OK_" + n, 60)


def viewer(T, tag):
    for n in NAMES:
        T.barrier(tag + "_EDIT_" + n, 90)
        T.pump(1.0)
        compare(T, tag + "_edit_" + n)
        T.say(tag + "_EDIT_OK_" + n)
        T.barrier(tag + "_DONE_" + n, 60)
        T.pump(1.5)
        compare(T, tag + "_done_" + n)
        T.say(tag + "_DONE_OK_" + n)

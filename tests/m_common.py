"""Scenario M : latence des modifications, mesuree de bout en bout.

Le joueur MP_M_DRIVER (hote par defaut) fait glisser une valeur pendant 3 s, comme
une souris : sommet en mode Edition (petit, moyen, gros maillage, cles de forme),
puis couleur de materiau et reglage de modificateur. L'autre joueur note a quel
instant il voit chaque valeur. Les deux Blender etant sur la meme machine, ils
partagent la meme horloge.
"""
import json
import os
import statistics
import time

import bmesh
import bpy

DRIVER = os.environ.get("MP_M_DRIVER", "host")
DRAG_S = float(os.environ.get("MP_M_SECONDS", "3"))
FPS = 30.0
MESHES = ("Cube8", "Sphere20k", "Grille200k", "FormeCle", "Topo")
CASES = tuple(c for c in os.environ.get("MP_M_CASES", "").split(",") if c) or (
    ("Objet",) + MESHES + ("Materiau", "Modificateur"))


def build_scene():
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    bpy.context.object.name = "Cube8"
    bpy.ops.mesh.primitive_uv_sphere_add(location=(5, 0, 0), segments=200, ring_count=100)
    bpy.context.object.name = "Sphere20k"
    bpy.ops.mesh.primitive_grid_add(location=(0, 5, 0), x_subdivisions=447, y_subdivisions=447)
    bpy.context.object.name = "Grille200k"
    bpy.ops.mesh.primitive_cube_add(location=(0, -5, 0))
    obj = bpy.context.object
    obj.name = "FormeCle"
    obj.shape_key_add(name="Basis")
    key = obj.shape_key_add(name="Forme")
    key.value = 1.0
    obj.active_shape_key_index = 1
    bpy.ops.mesh.primitive_cube_add(location=(-5, 0, 0))
    bpy.context.object.name = "Topo"
    bpy.ops.mesh.primitive_cube_add(location=(5, 5, 0))
    obj = bpy.context.object
    obj.name = "Materiel"
    mat = bpy.data.materials.new("Mat")
    mat.use_nodes = True
    obj.data.materials.append(mat)
    obj.modifiers.new("Bevel", "BEVEL")
    bpy.ops.mesh.primitive_cube_add(location=(-5, -5, 0))
    bpy.context.object.name = "Objet"
    bpy.ops.object.select_all(action="DESELECT")


def read(case):
    dg = bpy.context.evaluated_depsgraph_get()
    if case == "Objet":
        return round(bpy.data.objects["Objet"].location.z, 4)     # valeur affichee (lissage compris)
    if case in MESHES:
        ev = bpy.data.objects[case].evaluated_get(dg)
        return round(ev.data.vertices[0].co.z, 4)
    obj = bpy.data.objects["Materiel"]
    if case == "Materiau":
        node = obj.material_slots[0].material.node_tree.nodes["Principled BSDF"]
        return round(node.inputs["Base Color"].default_value[0], 4)
    return round(obj.modifiers["Bevel"].width, 4)


def _write(case, value, obj):
    if case == "Objet":
        obj.location.z = value
    elif case in MESHES:
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.verts[0].co.z = value
        bmesh.update_edit_mesh(obj.data)
    elif case == "Materiau":
        node = obj.material_slots[0].material.node_tree.nodes["Principled BSDF"]
        node.inputs["Base Color"].default_value[0] = value
        obj.material_slots[0].material.node_tree.update_tag()
    else:
        obj.modifiers["Bevel"].width = value
        obj.update_tag()


def drag(T, case, base):
    """Une valeur differente toutes les 1/30 s. Retourne [(heure, valeur)]."""
    obj = bpy.data.objects[case if case in MESHES or case == "Objet" else "Materiel"]
    if case in MESHES:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        # Comme un vrai geste : seuls quelques sommets selectionnes bougent
        # (MP_M_SELECT_ALL=1 : tout est selectionne, relecture complete du maillage).
        if os.environ.get("MP_M_SELECT_ALL") != "1":
            bm = bmesh.from_edit_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            for v in bm.verts:
                v.select = False
            bm.verts[0].select = True
            bm.select_flush(False)
            bmesh.update_edit_mesh(obj.data)
        T.pump(1.5)     # le verrou arrive chez l'autre joueur
        if case == "Topo":      # la topologie change juste avant le geste
            bm = bmesh.from_edit_mesh(obj.data)
            bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1, use_grid_fill=True)
            bmesh.update_edit_mesh(obj.data)
            obj.data.update_tag()
            T.pump(1.5)
    log = []
    end = time.time() + DRAG_S
    i = 0
    while time.time() < end:
        i += 1
        value = round(base + i * 0.002, 4)
        _write(case, value, obj)
        log.append((time.time(), value))
        T.pump(1.0 / FPS)
    return log


def vertex_count(case):
    return len(bpy.data.objects[case].data.vertices) if case in MESHES else 0


def stale_normals(case):
    """Faces dont la normale ne correspond pas aux sommets evalues (cache perime, shading lisse)."""
    ev = bpy.data.objects[case].evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.data
    bad = 0
    corners = me.corner_normals
    for p in me.polygons:
        pts = [me.vertices[v].co for v in p.vertices]
        n = sum(((a.cross(b)) for a, b in zip(pts, pts[1:] + pts[:1])), pts[0] * 0).normalized()
        if (n - p.normal).length > 1e-3 or any((corners[i].vector - n).length > 1e-3 for i in p.loop_indices):
            bad += 1
    return bad


def driver(T, sent, phase):
    for n, case in enumerate(CASES):
        t0 = time.time()
        phase[0] = case
        log = drag(T, case, 0.1 + n * 0.05)
        with open(os.path.join(T.OUT, "tx_%s.json" % case), "w") as fh:
            json.dump(log, fh)
        T.say("TX_" + case)
        T.barrier("RX_" + case, 60)
        if case in MESHES:
            bpy.ops.object.mode_set(mode="OBJECT")
            T.pump(2.5)
        final = read(case)
        heavy = [x for x in sent if x[0] == case and set(x[2] or ()) & {"geom", "blob", "fp"}]
        limit = 3 if case == "Topo" else 1
        T.check("%s : une seule operation complete pour tout le geste" % case, len(heavy) <= limit, heavy)
        phase[0] = "run"
        T.say("FIN_%s %s %d" % (case, final, vertex_count(case)))
        T.barrier("FIN_OK_" + case, 60)
        T.log("%s : %.1f s" % (case, time.time() - t0))


def viewer(T, sent):
    s = T.session.current
    for case in CASES:
        seen = []
        stale = None
        last = read(case)
        marker = "TX_" + case
        while not T.seen(marker):
            T.pump(0.0)
            value = read(case)
            if value != last:
                seen.append((time.time(), value))
                last = value
                if case == "Cube8" and len(seen) == 30:
                    stale = stale_normals(case)     # en plein geste, avant l'etat complet
        T.pump(1.0)
        with open(os.path.join(T.OUT, "tx_%s.json" % case)) as fh:
            tx = json.load(fh)
        when = {round(v, 4): t for t, v in tx}
        lat = [(t - when[v]) * 1000 for t, v in seen if v in when]
        ok = len(lat) >= 2
        res = {"recus": len(lat), "envoyes": len(tx)}
        if lat:
            lat.sort()
            res.update(mediane=round(statistics.median(lat)),
                       p90=round(lat[int(len(lat) * 0.9)]), max=round(lat[-1]))
            gaps = [(b[0] - a[0]) * 1000 for a, b in zip(seen, seen[1:])]
            res["trou_max"] = round(max(gaps)) if gaps else None
        T.RESULTS.setdefault("latence", {})[case] = res
        T.log("LATENCE", case, res)
        T.check("%s : mises a jour recues pendant le glissement" % case, ok, res)
        if case == "Cube8":
            T.check("normales recalculees pendant le geste (cube toujours plat)", stale == 0, stale)
        T.say("RX_" + case)
        marker = "FIN_%s " % case
        T.wait_for(lambda: any(str(c.get("text", "")).startswith(marker) for c in s.chat), 60, marker)
        T.pump(1.0)
        parts = next(c["text"].split() for c in s.chat if str(c.get("text", "")).startswith(marker))
        want = float(parts[1])
        T.check("%s : meme nombre de sommets" % case, int(parts[2]) == vertex_count(case),
                "%s / %s" % (parts[2], vertex_count(case)))
        got = read(case)
        T.check("%s : valeur finale identique" % case, abs(got - want) < 1e-3, "%s / %s" % (want, got))
        T.say("FIN_OK_" + case)
    # "Material" (materiau d'usine, jamais touche ici) part une fois chez l'hote, deja en 1.6.1.
    echo = [x for x in sent if x[0] != "join" and x[1] != "Material"]
    T.log("ENVOIS", echo[:30])
    T.check("aucun renvoi (anti-echo)", not echo, echo[:10])


def watch_sends(tracker, phase):
    sent = []
    orig = tracker.collect

    def collect(*a, **k):
        ch = orig(*a, **k)
        for c in ch:
            idb = tracker.index.get(c["u"])
            sent.append((phase[0], idb.name if idb is not None else c["u"],
                         sorted(c.get("f", {}).keys()) if c.get("k") == "u" else c.get("k")))
        return ch
    tracker.collect = collect
    return sent

"""Scenario E : edition a plusieurs du meme objet, en mode Edition, en meme temps.

L'hote et l'invite entrent tous les deux en mode Edition sur le meme maillage :
- personne n'est renvoye en mode Objet, aucun verrou ;
- chacun fait glisser un sommet en meme temps : chacun voit le sommet de l'autre
  bouger en direct dans son propre mode Edition, sans perdre le sien ;
- l'hote subdivise (topologie), puis l'invite supprime un sommet : l'autre voit
  le nouveau maillage sans quitter le mode Edition, et son sommet reste en place ;
- apres la topologie, le direct marche encore ;
- en sortant, les deux maillages sont identiques et plus rien n'est renvoye ;
- meme chose sur un maillage avec groupe de sommets (envoye en bloc .blend).
"""
import json
import os
import time

import bmesh
import bpy

FPS = 30.0


def build_scene():
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=10, y_subdivisions=10, size=4, location=(0, 0, 0))
    bpy.context.object.name = "Partage"
    bpy.ops.mesh.primitive_cube_add(location=(6, 0, 0))
    obj = bpy.context.object
    obj.name = "PartageVG"
    g = obj.vertex_groups.new(name="Groupe")
    g.add(list(range(len(obj.data.vertices))), 0.5, "REPLACE")
    bpy.ops.object.select_all(action="DESELECT")


def enter(name):
    obj = bpy.data.objects[name]
    if obj.mode == "EDIT":
        return obj
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    return obj


def leave():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def bm_of(name):
    bm = bmesh.from_edit_mesh(bpy.data.objects[name].data)
    bm.verts.ensure_lookup_table()
    return bm


def z_of(name, i):
    bm = bm_of(name)
    return round(bm.verts[i].co.z, 4) if i < len(bm.verts) else None


def count(name):
    obj = bpy.data.objects[name]
    return len(bm_of(name).verts) if obj.mode == "EDIT" else len(obj.data.vertices)


def set_z(name, i, value):
    me = bpy.data.objects[name].data
    bm = bm_of(name)
    bm.verts[i].co.z = value
    bmesh.update_edit_mesh(me)


def publish(T, name, data):
    with open(os.path.join(T.OUT, "%s_%s.json" % (name, T.ROLE)), "w") as fh:
        json.dump(data, fh)


def fetch(T, name, role):
    with open(os.path.join(T.OUT, "%s_%s.json" % (name, role))) as fh:
        return json.load(fh)


def signature(name):
    """Empreinte du maillage (positions, aretes, faces, groupes)."""
    obj = bpy.data.objects[name]
    me = obj.data
    co = [tuple(round(c, 4) for c in v.co) for v in me.vertices]
    faces = [tuple(p.vertices) for p in me.polygons]
    groups = [tuple(round(g.weight, 3) for g in v.groups) for v in me.vertices]
    return [len(co), sum(map(sum, co)), len(faces), hash(tuple(faces)) % 1000003,
            len(groups), sum(map(sum, groups))]


def watch_sends(tracker, phase):
    sent = []
    orig = tracker.collect

    def collect(*a, **k):
        ch = orig(*a, **k)
        for c in ch:
            f = c.get("f", {})
            if c.get("k") == "u" and set(f) & {"geom", "blob", "fp"}:
                sent.append((phase[0], c["u"]))
        return ch
    tracker.collect = collect
    return sent


def run(T, sent, phase):
    me_role = T.ROLE
    other = "client" if me_role == "host" else "host"
    s = T.session.current
    mine, theirs = (0, 120) if me_role == "host" else (120, 0)

    # 1. Tous les deux en mode Edition sur le meme objet
    phase[0] = "enter"
    obj = enter("Partage")
    T.say("IN_" + me_role)
    T.barrier("IN_" + other, 60)
    T.pump(2.0)
    T.check("toujours en mode Edition (pas renvoye en mode Objet)", obj.mode == "EDIT", obj.mode)
    from blender_multiplayer import ids
    ouid = ids.get_uid(obj)
    T.check("aucun verrou sur l'objet edite a deux", ouid not in s.locks, s.locks)
    co_users = [u.get("name") for uid, u in s.users.items() if uid != s.me and
                ouid in (u.get("presence") or {}).get("co", [])]
    T.check("l'autre est vu en edition du meme objet", len(co_users) == 1, co_users)
    T.say("READY_" + me_role)
    T.barrier("READY_" + other, 60)

    # 2. Glissements simultanes : chacun son sommet
    phase[0] = "drag"
    seen = []
    start_theirs = z_of("Partage", theirs)
    t_end = time.time() + 2.5
    i = 0
    base = 0.0
    while time.time() < t_end:
        i += 1
        set_z("Partage", mine, round(base + i * 0.01, 4))
        T.pump(1.0 / FPS)
        z = z_of("Partage", theirs)
        if z != start_theirs and (not seen or seen[-1][1] != z):
            seen.append((time.time(), z))
    final_mine = z_of("Partage", mine)
    publish(T, "drag", {"final": final_mine})
    T.say("DRAG_" + me_role)
    T.barrier("DRAG_" + other, 60)
    T.pump(2.0)
    want = fetch(T, "drag", other)["final"]
    T.log("valeurs du sommet de l'autre vues en direct : %d" % len(seen))
    T.check("sommet de l'autre vu bouger en direct (>= 10 images)", len(seen) >= 10, len(seen))
    T.check("sommet de l'autre a sa place finale", z_of("Partage", theirs) == want,
            "%s / %s" % (z_of("Partage", theirs), want))
    T.check("mon sommet n'a pas ete ecrase", z_of("Partage", mine) == final_mine,
            "%s / %s" % (z_of("Partage", mine), final_mine))
    T.say("DRAG_OK_" + me_role)
    T.barrier("DRAG_OK_" + other, 60)

    # 3. Topologie : l'hote subdivise, puis l'invite supprime un sommet
    phase[0] = "topo"
    if me_role == "host":
        bm = bm_of("Partage")
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1, use_grid_fill=True)
        bmesh.update_edit_mesh(obj.data)
        T.pump(1.5)
        publish(T, "topo1", {"n": count("Partage")})
        T.say("TOPO1")
        T.barrier("TOPO1_OK", 60)
    else:
        T.barrier("TOPO1", 60)
        want = fetch(T, "topo1", "host")["n"]
        T.wait_for(lambda: count("Partage") == want, 10, "subdivision de l'hote")
        T.check("subdivision de l'hote recue sans quitter le mode Edition",
                obj.mode == "EDIT" and count("Partage") == want, "%s / %s" % (count("Partage"), want))
        T.check("mon sommet garde sa place apres la subdivision", z_of("Partage", mine) == final_mine,
                "%s / %s" % (z_of("Partage", mine), final_mine))
        bm = bm_of("Partage")
        bmesh.ops.delete(bm, geom=[bm.verts[60]], context="VERTS")
        bmesh.update_edit_mesh(obj.data)
        T.pump(1.5)
        publish(T, "topo2", {"n": count("Partage")})
        T.say("TOPO1_OK")
    if me_role == "host":
        T.barrier("TOPO2", 60)
        want = fetch(T, "topo2", "client")["n"]
        T.wait_for(lambda: count("Partage") == want, 10, "suppression de l'invite")
        T.check("suppression de l'invite recue sans quitter le mode Edition",
                obj.mode == "EDIT" and count("Partage") == want, "%s / %s" % (count("Partage"), want))
        T.say("TOPO2_OK")
    else:
        T.say("TOPO2")
        T.barrier("TOPO2_OK", 60)

    # 4. Le direct marche encore apres les changements de topologie
    phase[0] = "drag2"
    if me_role == "host":
        T.pump(1.0)
        t_end = time.time() + 1.5
        i = 0
        while time.time() < t_end:
            i += 1
            set_z("Partage", 0, round(2.0 + i * 0.01, 4))
            T.pump(1.0 / FPS)
        publish(T, "drag2", {"final": z_of("Partage", 0)})
        T.say("DRAG2")
        T.barrier("DRAG2_OK", 60)
    else:
        start = z_of("Partage", 0)
        values = set()
        t_end = time.time() + 6.0
        while time.time() < t_end and not T.seen("DRAG2"):
            T.pump(1.0 / FPS)
            z = z_of("Partage", 0)
            if z != start:
                values.add(z)
        T.pump(1.5)
        want = fetch(T, "drag2", "host")["final"]
        T.check("direct encore actif apres la topologie (>= 10 images)", len(values) >= 10, len(values))
        T.check("valeur finale apres la topologie", z_of("Partage", 0) == want,
                "%s / %s" % (z_of("Partage", 0), want))
        T.say("DRAG2_OK")

    # 5. Sortie du mode Edition : maillages identiques, plus rien ne circule
    phase[0] = "exit"
    leave()
    T.say("OUT_" + me_role)
    T.barrier("OUT_" + other, 60)
    T.pump(3.0)
    phase[0] = "quiet"
    T.pump(3.0)
    publish(T, "sig", signature("Partage"))
    T.say("SIG_" + me_role)
    T.barrier("SIG_" + other, 60)
    T.check("maillages identiques apres l'edition a deux", signature("Partage") == fetch(T, "sig", other),
            "%s / %s" % (signature("Partage"), fetch(T, "sig", other)))
    quiet = [x for x in sent if x[0] == "quiet"]
    T.check("plus aucun renvoi une fois sortis (pas de ping-pong)", not quiet, quiet[:5])

    # 6. Maillage avec groupe de sommets (bloc .blend)
    phase[0] = "vg"
    obj = enter("PartageVG")
    T.say("VG_IN_" + me_role)
    T.barrier("VG_IN_" + other, 60)
    T.pump(1.5)
    T.check("groupe de sommets : toujours en mode Edition", obj.mode == "EDIT", obj.mode)
    if me_role == "host":
        bm = bm_of("PartageVG")
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1, use_grid_fill=True)
        bmesh.update_edit_mesh(obj.data)
        T.pump(0.5)
        for k in range(30):
            set_z("PartageVG", 0, round(1.5 + k * 0.01, 4))
            T.pump(1.0 / FPS)
        T.pump(1.5)
        publish(T, "vg", {"n": count("PartageVG"), "z": z_of("PartageVG", 0)})
        T.say("VG_DONE")
        T.barrier("VG_OK", 60)
    else:
        T.barrier("VG_DONE", 60)
        want = fetch(T, "vg", "host")
        T.wait_for(lambda: count("PartageVG") == want["n"] and z_of("PartageVG", 0) == want["z"], 10,
                   "maillage avec groupe de l'hote")
        T.check("groupe de sommets : subdivision et sommet de l'hote recus en mode Edition",
                obj.mode == "EDIT" and count("PartageVG") == want["n"] and z_of("PartageVG", 0) == want["z"],
                "%s %s / %s" % (count("PartageVG"), z_of("PartageVG", 0), want))
        T.say("VG_OK")
    leave()
    T.say("VG_OUT_" + me_role)
    T.barrier("VG_OUT_" + other, 60)
    T.pump(3.0)
    publish(T, "sigvg", signature("PartageVG"))
    T.say("SIGVG_" + me_role)
    T.barrier("SIGVG_" + other, 60)
    T.check("groupe de sommets : maillages et poids identiques",
            signature("PartageVG") == fetch(T, "sigvg", other),
            "%s / %s" % (signature("PartageVG"), fetch(T, "sigvg", other)))
    T.say("END_" + me_role)
    T.barrier("END_" + other, 60)
    if me_role == "host":
        T.pump(2.0)     # l'invite lit END_host avant que la session ne ferme

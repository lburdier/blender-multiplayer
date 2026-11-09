"""Outils partages par les scripts de test (executes DANS Blender)."""

import json
import os
import sys
import time
import traceback

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ADDON_PARENT = ARGS[0]
PORT = int(ARGS[1])
OUT = ARGS[2]
sys.path.insert(0, ADDON_PARENT)

import blender_multiplayer as mp  # noqa: E402
from blender_multiplayer import session  # noqa: E402

mp.register()

RESULTS = {"checks": [], "errors": []}
# MP_TEST_ONLINE=1 : la session passe par le relais Internet (lien d'invitation)
ONLINE = os.environ.get("MP_TEST_ONLINE") == "1"
INVITE_FILE = os.path.join(OUT, "invite.txt")
# MP_TEST_VIA=web : l'invite n'utilise que la voie HTTPS (reseau qui bloque bore.pub)
VIA = os.environ.get("MP_TEST_VIA", "bore")
ROLE = "?"


def log(*a):
    print("[%s]" % ROLE, *a, flush=True)


_NEXT_TICK = [0.0]


def pump(seconds=0.05):
    """Fait tourner la session comme dans Blender : le tic est rappele apres le
    delai qu'il renvoie (minuteur bpy.app.timers), la scene est evaluee entre-temps."""
    end = time.time() + seconds
    while True:
        s = session.current
        if s is not None:
            try:
                bpy.context.view_layer.update()
            except Exception:
                pass
            if time.time() >= _NEXT_TICK[0]:
                delay = s.tick()
                _NEXT_TICK[0] = time.time() + (delay if delay else 0.1)
        if time.time() >= end:
            break
        time.sleep(0.003)


def wait_for(cond, timeout=20.0, what=""):
    end = time.time() + timeout
    while time.time() < end:
        pump(0.03)
        try:
            if cond():
                return True
        except Exception:
            pass
    log("TIMEOUT:", what)
    RESULTS["errors"].append("timeout: " + what)
    return False


def check(name, ok, detail=""):
    RESULTS["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)})
    log("CHECK", "OK " if ok else "FAIL", name, detail)


def say(text):
    session.current.send_chat(text)
    pump(0.05)


def seen(text):
    s = session.current
    return s is not None and any(c.get("text") == text for c in s.chat)


def barrier(text, timeout=30.0):
    return wait_for(lambda: seen(text), timeout, "message " + text)


def dump_state():
    out = {"objects": {}, "materials": {}, "collections": {}, "meshes": {}}
    for o in bpy.data.objects:
        out["objects"][o.name] = {
            "type": o.type,
            "loc": [round(x, 3) for x in o.location],
            "rot": [round(x, 3) for x in o.rotation_euler],
            "scale": [round(x, 3) for x in o.scale],
            "parent": o.parent.name if o.parent else None,
            "data": o.data.name if o.data else None,
            "mods": [(m.name, m.type) for m in o.modifiers],
            "mats": [s.material.name if s.material else None for s in o.material_slots],
            "colls": sorted(c.name for c in o.users_collection),
            "hide_render": o.hide_render,
        }
    out["extra"] = dump_extra()
    for m in bpy.data.materials:
        if m.users == 0:
            continue  # orphelin : Blender ne l'enregistre pas, il disparait au chargement
        col = None
        if m.node_tree:
            n = m.node_tree.nodes.get("Principled BSDF")
            if n:
                col = [round(x, 3) for x in n.inputs["Base Color"].default_value]
        out["materials"][m.name] = {"color": col, "users": m.users > 0}
    for c in bpy.data.collections:
        out["collections"][c.name] = {"objects": sorted(o.name for o in c.objects),
                                      "children": sorted(x.name for x in c.children)}
    for me in bpy.data.meshes:
        if me.users == 0:
            continue
        s = [0.0, 0.0, 0.0]
        for v in me.vertices:
            for i in range(3):
                s[i] += v.co[i]
        out["meshes"][me.name] = {"v": len(me.vertices), "f": len(me.polygons),
                                  "sum": [round(x, 2) for x in s]}
    return out


def dump_extra():
    ex = {}
    sc = bpy.context.scene
    keep = sc.frame_current
    for o in sorted(bpy.data.objects, key=lambda o: o.name):
        d = {"hide": o.hide_get(), "hide_render": o.hide_render}
        if o.type == "LIGHT":
            d["light"] = [o.data.type, round(o.data.energy, 2), [round(x, 3) for x in o.data.color]]
            if o.data.type == "SPOT":
                d["spot"] = round(o.data.spot_size, 3)
        if o.type == "CAMERA":
            d["cam"] = [round(o.data.lens, 2), o.data.dof.use_dof,
                        o.data.dof.focus_object.name if o.data.dof.focus_object else None]
        if o.animation_data and o.animation_data.action:
            d["action"] = o.animation_data.action.name
            locs = []
            for f in (1, 10, 20, 40):
                sc.frame_set(f)
                locs.append([round(x, 2) for x in o.location])
            d["anim"] = locs
        for m in o.modifiers:
            if m.type == "NODES":
                d["gn"] = [m.node_group.name if m.node_group else None,
                           sorted((k, round(m[k], 3) if isinstance(m[k], float) else m[k])
                                  for k in m.keys() if isinstance(m[k], (int, float)))]
        if o.type == "ARMATURE":
            d["bones"] = sorted(b.name for b in o.data.bones)
            d["pose"] = {pb.name: [round(x, 3) for x in pb.rotation_quaternion] for pb in o.pose.bones}
        if o.type == "FONT":
            d["body"] = o.data.body
        if o.type == "CURVE":
            d["curve"] = [len(o.data.splines), sum(len(s.bezier_points) + len(s.points) for s in o.data.splines)]
        ex[o.name] = d
    sc.frame_set(keep)
    ex["_images"] = sorted((i.name, tuple(i.size)) for i in bpy.data.images if i.users)
    ex["_node_groups"] = sorted((g.name, len(g.nodes), len(g.links)) for g in bpy.data.node_groups if g.users)
    return ex


def finish():
    try:
        RESULTS["state"] = dump_state()
    except Exception:
        RESULTS["errors"].append(traceback.format_exc())
    s = session.current
    if s is not None:
        RESULTS["logs"] = [t for _ts, t in list(s.logs)[-60:]]
        RESULTS["hist"] = [h.get("l") for h in s.history_items()]
    with open(os.path.join(OUT, "%s.json" % ROLE), "w", encoding="utf-8") as fh:
        json.dump(RESULTS, fh, indent=1)
    log("termine")

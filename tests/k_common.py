"""Scenario K : modifications faites par un AUTRE addon (celui du vehicule).

Un joueur (MP_K_DRIVER = host ou client, hote par defaut) utilise le vrai code
de conduite de l'addon du vehicule (dossier MP_K_ADDON) : os deplaces,
proprietes de feux, f-curves coupees pendant la conduite, driver ajoute.
L'autre joueur verifie qu'il voit tout, sans rien renvoyer.
"""
import json
import os
import time

import bpy

DRIVER = os.environ.get("MP_K_DRIVER", "host")
KEYS = ("roues_rotation", "phares", "clignotant_g", "barre_led", "frein", "bache_vitesse")


def snapshot(rig):
    pb = rig.pose.bones["CTRL_Deplacement"]
    ev = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {
        "loc": [round(x, 3) for x in pb.location],
        "rotz": round(pb.rotation_euler.z, 3),
        "dir": round(rig.pose.bones["CTRL_Direction"].rotation_euler.z, 3),
        "props": {k: round(float(pb[k]), 3) for k in KEYS if k in pb.keys()},
        "wheel": [round(x, 3) for x in ev.pose.bones["DEF_Roue_AVG"].matrix.to_euler()],
        "steer": [round(x, 3) for x in ev.pose.bones["MCH_Direction_AVG"].matrix.to_euler()],
    }


def close(a, b, tol=0.002):
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(close(a[k], b[k], tol) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    return abs(a - b) <= tol


def watch_sends(T, tracker, phase):
    """Note tout ce que ce joueur envoie, par etape."""
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


def drive(T, phase, sent):
    import sys
    sys.path.insert(0, os.environ["MP_K_ADDON"])
    from jc_commander_rig import pilotage

    def publish(name, data):
        with open(os.path.join(T.OUT, name + ".json"), "w") as fh:
            json.dump(data, fh)

    rig = bpy.data.objects["JC_Rig"]
    before = snapshot(rig)
    # --- conduite : le vrai code de l'addon, comme a chaque tic de son operateur modal
    phase[0] = "drive"
    car = pilotage.Session(rig, bpy.context.scene, False)
    t_end = time.time() + 3.0
    last = time.time()
    ticks = 0
    while time.time() < t_end:
        now = time.time()
        car.tick(1.0, 0.0, 0.6, False, now - last)
        last = now
        ticks += 1
        T.pump(1 / 60)
    T.pump(1.0)
    publish("drive", snapshot(rig))
    T.check("le vehicule a bouge", snapshot(rig)["loc"] != before["loc"])
    T.say("DRIVE")
    T.barrier("DRIVE_OK", 60)

    # --- feux : commandes de l'addon (touches H, F) + reglage direct d'une propriete
    phase[0] = "lights"
    car.cycle_lights()
    car.toggle("clignotant_g")
    rig.pose.bones["CTRL_Deplacement"]["barre_led"] = 1.0
    rig.update_tag()
    T.pump(1.5)
    publish("lights", snapshot(rig))
    T.say("LIGHTS")
    T.barrier("LIGHTS_OK", 60)

    # --- l'addon ajoute un driver (comme une mise a niveau du rig)
    phase[0] = "driver"
    obj = bpy.data.objects["JC_Bache"]
    fc = obj.driver_add("color", 0)
    fc.driver.type = "SCRIPTED"
    v = fc.driver.variables.new()
    v.name = "led"
    v.targets[0].id = rig
    v.targets[0].data_path = 'pose.bones["CTRL_Deplacement"]["barre_led"]'
    fc.driver.expression = "0.25 + led * 0.5"
    obj.update_tag()
    T.pump(1.5)
    T.say("DRIVER")
    T.barrier("DRIVER_OK", 60)

    # --- cles posees par l'addon (comme « Tout calculer » ou une conduite enregistree)
    phase[0] = "keys"
    from jc_commander_rig import trajet
    trajet.write_keys(rig, 'pose.bones["CTRL_Deplacement"]["phares"]', 0, [10, 30], [0.0, 2.0],
                      10, 30, "CONSTANT")
    rig.update_tag()
    T.pump(1.5)
    T.say("KEYS")
    T.barrier("KEYS_OK", 60)

    # --- fin de conduite : f-curves retablies
    phase[0] = "stop"
    car.unmute()
    rig.update_tag()
    T.pump(1.5)
    T.say("STOP")
    T.barrier("END", 120)
    by_phase = {}
    for ph, name, f in sent:
        by_phase.setdefault(ph, []).append([name, f])
    rig_sends = sum(1 for n, _f in by_phase.get("drive", []) if n == "JC_Rig")
    T.log("ENVOIS", json.dumps(by_phase)[:3000])
    T.log("CONDUITE : %d tics de l'addon, %d envois de la pose" % (ticks, rig_sends))
    T.check("conduite envoyee en continu (>= 15 par seconde)", rig_sends >= 45, rig_sends)
    big = [n for ph in ("drive", "lights") for n, f in by_phase.get(ph, [])
           if n and (n.startswith("M") or "blob" in (f or ()) or "fp" in (f or ()))]
    T.check("pas de materiau / maillage renvoye pour des valeurs pilotees", not big, big[:10])


def watch(T, phase, sent):
    def same(name, keys):
        with open(os.path.join(T.OUT, name + ".json")) as fh:
            want = json.load(fh)
        T.pump(0.3)
        got = snapshot(bpy.data.objects["JC_Rig"])
        for k in keys:
            T.check("%s : %s identique" % (name, k), close(want[k], got[k]),
                    "conducteur %s / ici %s" % (want[k], got[k]))

    phase[0] = "drive"
    T.barrier("DRIVE", 120)
    T.pump(1.0)
    same("drive", ("loc", "rotz", "dir", "props", "wheel", "steer"))
    T.say("DRIVE_OK")

    phase[0] = "lights"
    T.barrier("LIGHTS", 60)
    T.pump(1.0)
    same("lights", ("props",))
    T.say("LIGHTS_OK")

    phase[0] = "driver"
    T.barrier("DRIVER", 60)
    T.pump(1.0)
    obj = bpy.data.objects["JC_Bache"]
    fc = obj.animation_data.drivers.find("color", index=0) if obj.animation_data else None
    T.check("driver ajoute par l'addon recu", fc is not None and fc.driver.expression == "0.25 + led * 0.5",
            fc.driver.expression if fc else None)
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    T.check("driver recu evalue ici", abs(ev.color[0] - 0.75) < 1e-3, ev.color[0])
    T.say("DRIVER_OK")

    phase[0] = "keys"
    T.barrier("KEYS", 60)
    T.pump(1.0)
    rig = bpy.data.objects["JC_Rig"]
    import sys
    compat = sys.modules[T.session.__name__.rsplit(".", 1)[0] + ".compat"]
    fc = next((f for f in compat.anim_fcurves(rig.animation_data)
               if f.data_path == 'pose.bones["CTRL_Deplacement"]["phares"]' and f.driver is None), None)
    keys = [tuple(round(x, 2) for x in k.co) for k in fc.keyframe_points] if fc else None
    T.check("cles posees par l'addon recues", keys == [(10.0, 0.0), (30.0, 2.0)], keys)
    sc = bpy.context.scene
    keep = sc.frame_current
    sc.frame_set(30)
    T.check("cles recues evaluees", round(float(rig.pose.bones["CTRL_Deplacement"]["phares"]), 2) == 2.0,
            rig.pose.bones["CTRL_Deplacement"]["phares"])
    sc.frame_set(keep)
    T.pump(1.0)
    T.say("KEYS_OK")

    phase[0] = "stop"
    T.barrier("STOP", 60)
    T.pump(2.0)
    echo = [x for x in sent if x[0] != "join"]
    T.log("ENVOIS", echo[:40])
    T.check("aucun renvoi (anti-echo)", not echo, echo[:10])
    T.say("END")

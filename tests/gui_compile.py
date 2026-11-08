"""Temps de compilation des shaders EEVEE en vue Rendu selon la facon d'ouvrir le fichier.

blender [fichier] --factory-startup --python gui_compile.py -- <mode> <fichier> <sortie.json>
mode : startup (fichier ouvert au lancement) | timer | timer_ui | timer_scripts
"""
import json
import os
import sys
import time

import bpy

MODE, FILE, OUT = sys.argv[sys.argv.index("--") + 1:][:3]
bpy.context.preferences.view.show_splash = False
if os.environ.get("MP_ADDON"):
    sys.path.insert(0, os.environ["MP_ADDON"])
    import blender_multiplayer as _mp
    _mp.register()
T = {"t0": time.time(), "step": 0, "log": []}


def view3d():
    for w in bpy.context.window_manager.windows:
        for a in w.screen.areas:
            if a.type == "VIEW_3D":
                return w, a
    return None, None


def eevee():
    items = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
    return next(n for n in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE") if n in items)


def shot():
    w, area = view3d()
    path = OUT + ".png"
    with bpy.context.temp_override(window=w, screen=w.screen):
        bpy.ops.screen.screenshot(filepath=path, check_existing=False)
    reg = next(r for r in area.regions if r.type == "WINDOW")
    img = bpy.data.images.load(path, check_existing=False)
    W = img.size[0]
    px = img.pixels[:]
    tot = n = 0
    for y in range(reg.y + 10, reg.y + reg.height - 10, 6):
        for x in range(reg.x + 10, reg.x + reg.width - 10, 6):
            i = (y * W + x) * 4
            tot += px[i] + px[i + 1] + px[i + 2]
            n += 1
    return round(tot / max(1, 3 * n), 4)


def tick():
    now = time.time() - T["t0"]
    st = T["step"]
    if st == 0 and now > 3:
        if MODE.startswith("timer"):
            w, _a = view3d()
            kw = dict(filepath=FILE, load_ui=(MODE == "timer_ui"), use_scripts=(MODE == "timer_scripts"))
            if os.environ.get("MP_COMPAT"):
                from blender_multiplayer import compat as _c
                _c.open_file(FILE)
            else:
                with bpy.context.temp_override(window=w, screen=w.screen):
                    bpy.ops.wm.open_mainfile(**kw)
        T["step"] = 1
        return 0.5
    if st == 1:
        for sc in bpy.data.scenes:
            sc.render.engine = eevee()
        w, a = view3d()
        a.spaces.active.shading.type = "RENDERED"
        if os.environ.get("MP_CAM"):
            a.spaces.active.region_3d.view_perspective = "CAMERA"
        if os.environ.get("MP_NOOVL"):
            a.spaces.active.overlay.show_overlays = False
        a.tag_redraw()
        T["t1"] = time.time()
        T["step"] = 2
        return 0.25
    if st == 2:
        busy = bpy.app.is_job_running("SHADER_COMPILATION")
        el = round(time.time() - T["t1"], 2)
        T["log"].append((el, busy))
        if (not busy and el > 1.5) or el > 120:
            T["step"] = 3
            T["t3"] = time.time()
            T["res"] = {"mode": MODE, "compile_s": el, "busy_at_end": busy, "mats": len(bpy.data.materials)}
        return 0.25
    if st == 3 and time.time() - T["t3"] > float(os.environ.get("MP_WAIT", "3")):
            res = T["res"]
            res["brightness"] = shot()
            json.dump(res, open(OUT, "w"))
            print("RESULT", res, flush=True)
            bpy.ops.wm.quit_blender()
            return None
    return 0.25


bpy.app.timers.register(tick, first_interval=1.0, persistent=True)

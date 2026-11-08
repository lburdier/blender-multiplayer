"""Vue 3D en mode Rendu chez l'invite (mode graphique, minuteurs reels).

blender --factory-startup --python gui_render.py -- <parent> <host|guest> <port> <dossier> [CYCLES|EEVEE]

L'hote prepare une scene (texture externe, texture empaquetee, monde HDRI,
soleil), l'invite la rejoint, les deux passent la vue 3D en mode Rendu et
mesurent la luminosite de leur vue. Puis l'hote ajoute une lampe, ensuite
l'invite en ajoute une : chacun mesure a nouveau.
"""

import json
import os
import sys
import time

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, ARGS[0])
ROLE, PORT, OUT = ARGS[1], int(ARGS[2]), ARGS[3]
ENGINE = ARGS[4] if len(ARGS) > 4 else "EEVEE"

import blender_multiplayer as mp  # noqa: E402
from blender_multiplayer import session  # noqa: E402

mp.register()
if os.environ.get("MP_KEEP_PROFILE"):
    from blender_multiplayer import prefs as _prefs_mod
    _prefs_mod._defaults.root_dir = os.path.join(OUT, ROLE)
bpy.context.preferences.view.show_splash = False
_DIS = os.environ.get("MP_DISABLE", "") if ROLE == "guest" else ""
if "restore" in _DIS:
    from blender_multiplayer import serialize as _ser
    _ser.restore_local_render = lambda *a, **k: None
if "extras" in _DIS:
    session.ClientSession._apply_snapshot_extras = lambda self, *a, **k: {}
if "minimal" in _DIS:
    from blender_multiplayer import compat as _cp, tracker as _tm

    def _mini(self, msg, binary):
        path = os.path.join(self.session_dir, "mini.blend")
        with open(path, "wb") as fh:
            fh.write(binary[:msg.get("blend_len", len(binary))])
        def _do():
            self.loading = True
            _cp.open_file(path)
            self.loading = False
            self.tracker = _tm.Tracker(self)
            if "noinit" not in _DIS:
                self.tracker.init_known(write_blobs=False)
            self.active = True
            self.state = "ready"
        if "deferopen" in _DIS:
            bpy.app.timers.register(lambda: _do(), first_interval=0.01)
        else:
            _do()
    session.ClientSession._on_snapshot = _mini
if "draw" in _DIS:
    from blender_multiplayer import presence as _pr
    _pr.unregister()
T0 = time.time()
ST = {"step": 0, "res": {}}
DEPS = {}


@bpy.app.handlers.persistent
def _count(scene, depsgraph):
    if not ST.get("count"):
        return
    for u in depsgraph.updates:
        try:
            k = "%s:%s%s%s" % (type(u.id).__name__, u.id.name, " G" if u.is_updated_geometry else "",
                               " T" if u.is_updated_transform else "")
        except Exception:
            k = "?"
        DEPS[k] = DEPS.get(k, 0) + 1
    DEPS["_total"] = DEPS.get("_total", 0) + 1


bpy.app.handlers.depsgraph_update_post.append(_count)


def idle_report(tag):
    s = session.current
    out = dict(sorted(DEPS.items(), key=lambda kv: -kv[1])[:25])
    if s is not None:
        out["_hist"] = len(s.history_items())
        out["_hist_last"] = [h.get("l") for h in s.history_items()][-6:]
    ST["res"]["idle_" + tag] = out
    DEPS.clear()


def eevee_name():
    items = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
    return next((n for n in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE") if n in items), items[0])


def win():
    return bpy.context.window_manager.windows[0]


def view3d():
    for area in win().screen.areas:
        if area.type == "VIEW_3D":
            return area
    return None


def rendered_view():
    area = view3d()
    sp = area.spaces.active
    sp.shading.type = "RENDERED"
    sp.overlay.show_overlays = bool(os.environ.get("MP_OVERLAYS"))
    sp.show_region_ui = False
    sp.show_region_toolbar = False
    r3d = sp.region_3d
    r3d.view_perspective = "CAMERA"
    area.tag_redraw()


def measure(tag):
    path = os.path.join(OUT, "%s_%s.png" % (ROLE, tag))
    with bpy.context.temp_override(window=win(), screen=win().screen):
        bpy.ops.screen.screenshot(filepath=path, check_existing=False)
    area = view3d()
    reg = next(r for r in area.regions if r.type == "WINDOW")
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = img.pixels[:]
    tot, n = 0.0, 0
    for y in range(reg.y + 10, reg.y + reg.height - 10, 6):
        for x in range(reg.x + 10, reg.x + reg.width - 10, 6):
            i = (y * w + x) * 4
            if i + 2 < len(px):
                tot += px[i] + px[i + 1] + px[i + 2]
                n += 1
    bpy.data.images.remove(img)
    val = round(tot / max(1, 3 * n), 4)
    ST["res"][tag] = val
    print("MESURE", ROLE, tag, val, flush=True)
    with open(os.path.join(OUT, "gui_render_%s.json" % ROLE), "w") as fh:
        json.dump(ST["res"], fh)
    return val


def build_scene():
    sc = bpy.context.scene
    sc.render.engine = "CYCLES" if ENGINE == "CYCLES" else eevee_name()
    if ENGINE == "CYCLES":
        sc.cycles.samples = 8
        sc.cycles.preview_samples = 4
    texdir = os.path.join(OUT, "tex")
    os.makedirs(texdir, exist_ok=True)
    img = bpy.data.images.new("damier", 64, 64)
    px = []
    for y in range(64):
        for x in range(64):
            c = 1.0 if (x // 8 + y // 8) % 2 else 0.1
            px += [c, 0.4 * c, 0.1, 1.0]
    img.pixels.foreach_set(px)
    img.filepath_raw = os.path.join(texdir, "damier.png")
    img.file_format = "PNG"
    img.save()
    ext = bpy.data.images.load(os.path.join(texdir, "damier.png"))
    packed = bpy.data.images.new("empaquetee", 32, 32)
    packed.pixels.foreach_set([0.1, 0.8, 0.2, 1.0] * 1024)
    packed.pack()
    for name, im in (("Cube", ext),):
        mat = bpy.data.materials.new("Mat_" + name)
        mat.use_nodes = True
        n = mat.node_tree.nodes.new("ShaderNodeTexImage")
        n.image = im
        mat.node_tree.links.new(n.outputs["Color"], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
        bpy.data.objects[name].data.materials.append(mat)
    bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, -1))
    plane = bpy.context.active_object
    mat = bpy.data.materials.new("Mat_Sol")
    mat.use_nodes = True
    n = mat.node_tree.nodes.new("ShaderNodeTexImage")
    n.image = packed
    mat.node_tree.links.new(n.outputs["Color"], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    plane.data.materials.append(mat)
    # Monde HDRI fourni avec Blender
    hdr_dir = os.path.join(os.path.dirname(bpy.app.binary_path), "%d.%d" % bpy.app.version[:2],
                           "datafiles", "studiolights", "world")
    hdr = next((os.path.join(hdr_dir, f) for f in sorted(os.listdir(hdr_dir)) if f.endswith(".exr")), None)
    w = sc.world or bpy.data.worlds.new("World")
    sc.world = w
    w.use_nodes = True
    nt = w.node_tree
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(hdr)
    nt.links.new(env.outputs["Color"], nt.nodes["Background"].inputs["Color"])
    nt.nodes["Background"].inputs["Strength"].default_value = 0.6
    bpy.data.objects["Light"].data.energy = 300.0


def driver():
    t = time.time() - T0
    s = session.current
    st = ST["step"]
    try:
        if ROLE == "host":
            if st == 0 and t > (6.0 if os.environ.get("MP_HOST_FILE") else 1.0):
                if not os.environ.get("MP_HOST_FILE"):
                    build_scene()
                if not os.environ.get("MP_HOST_LATE"):
                    rendered_view()
                session.host(PORT, "", "Rendu", username="Hote")
                if os.environ.get("MP_HOST_LATE"):
                    bpy.app.timers.register(lambda: rendered_view(), first_interval=1.0)
                ST["step"] = 1
            elif st == 1 and s and len(s.users) == 2:
                ST["t"] = time.time()
                ST["step"] = 2
                ST["count"] = True
            elif st == 2 and time.time() - ST["t"] > 8.0:
                idle_report("host")
                measure("0_initial")
                # L'hote ajoute une lampe rouge tres forte
                bpy.ops.object.light_add(type="POINT", location=(2.5, -2.5, 2.0))
                L = bpy.context.active_object
                L.name = "LampeHote"
                L.data.energy = 4000.0
                L.data.color = (1.0, 0.1, 0.1)
                ST["t"] = time.time()
                ST["step"] = 3
            elif st == 3 and time.time() - ST["t"] > 4.0:
                measure("1_lampe_hote")
                ST["t"] = time.time()
                ST["step"] = 4
            elif st == 4 and time.time() - ST["t"] > 8.0:
                measure("2_lampe_invite")
                ST["step"] = 5
                ST["t"] = time.time()
            elif st == 5 and time.time() - ST["t"] > 3.0:
                bpy.ops.wm.quit_blender()
                return None
        else:
            if st == 0 and t > 6.0:
                session.join("127.0.0.1", PORT, "", username="Invite")
                ST["step"] = 1
            elif st == 1 and s and s.active:
                ST["t"] = time.time()
                ST["step"] = 2
            elif st == 2 and time.time() - ST["t"] > 1.0:
                if "stop" in _DIS:
                    s.stop("test")
                rendered_view()
                ST["t"] = time.time()
                ST["step"] = 3
                ST["count"] = True
                _jobs_t0 = time.time()
                ST["res"]["jobs"] = []

                def _jobs():
                    el = round(time.time() - _jobs_t0, 1)
                    busy = bpy.app.is_job_running("SHADER_COMPILATION")
                    ST["res"]["jobs"].append((el, busy, bpy.app.is_job_running("RENDER_PREVIEW")))
                    if (not busy and el > 2) or el > 60:
                        ST["res"]["compile_done_s"] = el
                        return None
                    return 0.5
                bpy.app.timers.register(_jobs, first_interval=0.1)
            elif st == 3 and os.environ.get("MP_POLL") and time.time() - ST["t"] > 2.0 * (1 + len(ST.setdefault("poll", []))):
                ST["poll"].append(measure("poll"))
                ST["res"]["poll"] = ST["poll"]
                if ST["poll"][-1] > 0.3 or len(ST["poll"]) >= 20:
                    ST["res"]["poll_ok_s"] = 2 * len(ST["poll"]) if ST["poll"][-1] > 0.3 else None
                    ST["t"] = time.time() - 7.5
                    del os.environ["MP_POLL"]
            elif st == 3 and not os.environ.get("MP_POLL") and time.time() - ST["t"] > 7.0:
                idle_report("guest")
                measure("0_initial")
                if os.environ.get("MP_PROBE"):
                    w = win()
                    sp = view3d().spaces.active
                    ST["res"]["probe_state"] = {
                        "win_scene": w.scene.name, "win_vl": w.view_layer.name,
                        "ctx_scene": bpy.context.scene.name, "engine": bpy.context.scene.render.engine,
                        "shading": sp.shading.type, "scenes": [x.name for x in bpy.data.scenes],
                        "vl_use": [v.use for v in w.scene.view_layers],
                        "use_scripts": bpy.app.driver_namespace is not None}
                    ST["step"] = 30
                    ST["t"] = time.time()
                    return 0.05
                ST["t"] = time.time()
                ST["step"] = 4
            elif st == 30 and time.time() - ST["t"] > 0.5:
                bpy.context.view_layer.update()
                for a in win().screen.areas:
                    a.tag_redraw()
                ST["step"] = 31
                ST["t"] = time.time()
            elif st == 31 and time.time() - ST["t"] > 3.0:
                measure("p1_update")
                sp = view3d().spaces.active
                sp.shading.type = "SOLID"
                ST["step"] = 32
                ST["t"] = time.time()
            elif st == 32 and time.time() - ST["t"] > 0.5:
                view3d().spaces.active.shading.type = "RENDERED"
                ST["step"] = 33
                ST["t"] = time.time()
            elif st == 33 and time.time() - ST["t"] > 3.0:
                measure("p2_toggle")
                sc = bpy.context.scene
                e = sc.render.engine
                sc.render.engine = "BLENDER_WORKBENCH"
                sc.render.engine = e
                ST["step"] = 34
                ST["t"] = time.time()
            elif st == 34 and time.time() - ST["t"] > 3.0:
                measure("p3_engine")
                w = win()
                if os.environ.get("MP_PROBE") != "wait":
                    w.scene = w.scene
                ST["step"] = 35
                ST["t"] = time.time()
            elif st == 35 and time.time() - ST["t"] > 3.0:
                measure("p4_winscene")
                ST["step"] = 4
                ST["t"] = time.time()
            elif st == 4 and time.time() - ST["t"] > 4.0:
                measure("1_lampe_hote")
                bpy.ops.object.light_add(type="POINT", location=(-2.5, 2.5, 2.0))
                L = bpy.context.active_object
                L.name = "LampeInvite"
                L.data.energy = 4000.0
                L.data.color = (0.1, 0.3, 1.0)
                ST["t"] = time.time()
                ST["step"] = 5
            elif st == 5 and time.time() - ST["t"] > 4.0:
                measure("2_lampe_invite")
                ST["step"] = 6
                ST["t"] = time.time()
            elif st == 6 and os.environ.get("MP_GUEST_CYCLES") and "3_cycles" not in ST["res"]:
                bpy.context.scene.render.engine = "CYCLES"
                ST["res"]["3_cycles"] = None
                ST["t"] = time.time()
            elif st == 6 and time.time() - ST["t"] > (10.0 if os.environ.get("MP_GUEST_CYCLES") else 4.0):
                if os.environ.get("MP_GUEST_CYCLES"):
                    measure("3_cycles")
                ST["res"]["engine_end"] = bpy.context.scene.render.engine
                bpy.ops.wm.quit_blender()
                return None
    except Exception:
        import traceback
        traceback.print_exc()
        ST["res"]["error"] = traceback.format_exc()
        with open(os.path.join(OUT, "gui_render_%s.json" % ROLE), "w") as fh:
            json.dump(ST["res"], fh)
        bpy.ops.wm.quit_blender()
        return None
    return 0.05


bpy.app.timers.register(driver, first_interval=0.5, persistent=True)

"""Captures d'ecran pour le README (mode graphique, aucun clic necessaire).

blender <scene.blend> --python shots.py -- <parent addon> <host|guest> <port> <dossier>

L'hote ouvre une session joignable par Internet, l'invite la rejoint via le
lien d'invitation (adresse Internet uniquement), chacun agit dans la scene,
puis chaque Blender enregistre des captures de sa fenetre.
"""

import math
import os
import sys
import time

import bpy
from mathutils import Euler, Vector

ARGS = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, ARGS[0])
ROLE, PORT, SHARED = ARGS[1], int(ARGS[2]), ARGS[3]
INVITE = os.path.join(SHARED, "invite.txt")

import blender_multiplayer as mp  # noqa: E402
from blender_multiplayer import net, session, ui  # noqa: E402

mp.register()
bpy.context.preferences.system.use_online_access = True
bpy.context.preferences.view.show_splash = False
# Captures publiques : pas de vraies adresses locales.
net.local_addresses = lambda: ["192.168.1.20"]
mp.prefs._defaults.username = "Alex" if ROLE == "host" else "Sam"

T0 = time.time()
ST = {"step": 0}
NAME = "Alex" if ROLE == "host" else "Sam"
COLOR = (0.95, 0.35, 0.25) if ROLE == "host" else (0.25, 0.65, 0.98)


def win():
    return bpy.context.window_manager.windows[0]


def view3d():
    for area in win().screen.areas:
        if area.type == "VIEW_3D":
            return area
    return None


def setup_ui():
    # Seul l'onglet Multijoueur reste dans la barre laterale : il est donc affiche.
    for cls in list(bpy.types.Panel.__subclasses__()):
        if getattr(cls, "bl_space_type", "") == "VIEW_3D" and getattr(cls, "bl_region_type", "") == "UI" \
                and getattr(cls, "bl_category", "") != "Multijoueur" and cls.is_registered:
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
    area = view3d()
    sp = area.spaces.active
    sp.show_region_ui = True
    sp.shading.type = "SOLID"
    sp.shading.color_type = "MATERIAL"
    sp.shading.light = "STUDIO"
    sp.overlay.show_floor = True
    with bpy.context.temp_override(window=win(), area=area):
        for region in area.regions:
            if region.type == "UI":
                pass
    for a in win().screen.areas:
        if a.type == "PROPERTIES":
            a.spaces.active.context = "OBJECT" if hasattr(a.spaces.active, "context") else None


def set_view(loc, rot, dist):
    r3d = view3d().spaces.active.region_3d
    r3d.view_perspective = "PERSP"
    r3d.view_location = Vector(loc)
    r3d.view_rotation = Euler(rot).to_quaternion()
    r3d.view_distance = dist


def select_only(name):
    ob = bpy.data.objects.get(name)
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    if ob:
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob


def set_tab():
    area = view3d()
    if area is None:
        return
    for region in area.regions:
        if region.type == "UI":
            try:
                if region.active_panel_category != "Multijoueur":
                    region.active_panel_category = "Multijoueur"
                    area.tag_redraw()
            except Exception:
                pass


def shot(name):
    path = os.path.join(SHARED, name)
    with bpy.context.temp_override(window=win(), screen=win().screen):
        bpy.ops.screen.screenshot(filepath=path, check_existing=False)
    area = view3d()
    rects = {"win": [win().width, win().height]}
    for region in area.regions:
        if region.type in ("UI", "WINDOW"):
            rects[region.type] = [region.x, region.y, region.width, region.height]
    import json
    with open(path + ".json", "w") as fh:
        json.dump(rects, fh)
    print("CAPTURE", path, flush=True)


def driver():
    t = time.time() - T0
    st = ST
    s = session.current
    if st["step"] >= 1:
        set_tab()
    try:
        if st["step"] == 0 and t > 1.0:
            setup_ui()
            if ROLE == "host":
                set_view((0, 0, 0.6), (math.radians(62), 0, math.radians(38)), 10.5)
                select_only("Anneau")
                session.host(PORT, "", "Atelier du samedi", username=NAME, color=COLOR, online=True)
            else:
                set_view((0, 0, 0.6), (math.radians(68), 0, math.radians(-35)), 9.5)
            st["step"] = 1
        elif st["step"] == 1 and ROLE == "host" and s and s.public_address():
            with open(INVITE + ".tmp", "w", encoding="utf-8") as fh:
                fh.write(s.invite_link())
            os.replace(INVITE + ".tmp", INVITE)
            st["step"] = 2
        elif st["step"] == 1 and ROLE == "guest" and t > 3 and os.path.exists(INVITE):
            data = mp.invite.parse(open(INVITE, encoding="utf-8").read())
            pub = [a for a in data["addresses"] if a[0] == "bore.pub"]
            session.join_any(pub, data["password"], username=NAME, color=COLOR)
            st["step"] = 2
        elif st["step"] == 2 and ROLE == "guest" and s and s.active:
            setup_ui()
            set_view((0, 0, 0.6), (math.radians(66), 0, math.radians(-32)), 10)
            select_only("Suzanne")
            st["step"] = 3
            st["t3"] = time.time()
            s.send_chat("Salut ! Je m'occupe de Suzanne.")
        elif st["step"] == 3 and ROLE == "guest":
            k = time.time() - st["t3"]
            ob = bpy.data.objects.get("Suzanne")
            if ob and k < 3:
                ob.location.x = -0.4 * k / 3
                ob.rotation_euler.z = math.radians(20 + 25 * k / 3)
            if k > 3 and not st.get("cone"):
                st["cone"] = True
                bpy.ops.mesh.primitive_cone_add(location=(0.6, -2.6, 0.6), radius1=0.5, depth=1.2)
                bpy.context.active_object.name = "Cone de Sam"
                select_only("Suzanne")
            if k > 12:
                shot("02_invite.png")
                st["step"] = 4
        elif st["step"] == 2 and ROLE == "host" and s and len(s.users) == 2:
            st["step"] = 3
            st["t3"] = time.time()
        elif st["step"] == 3 and ROLE == "host":
            k = time.time() - st["t3"]
            if k > 2 and not st.get("chat"):
                st["chat"] = True
                s.send_chat("Bienvenue ! Je fais les reglages de l'anneau.")
                ob = bpy.data.objects.get("Anneau")
                ob.scale = (1.2, 1.2, 1.2)
            if k > 13:
                shot("01_hote.png")
                st["step"] = 4
                st["t4"] = time.time()
        elif st["step"] == 4 and ROLE == "host" and time.time() - st["t4"] > 6:
            session.leave()
            st["step"] = 5
        elif st["step"] == 4 and ROLE == "guest" and session.current is None:
            # Panneau avant connexion, avec un lien d'invitation detecte dans le presse-papiers.
            ui._clipboard_invite = lambda context: {"name": "Atelier du samedi",
                                                    "addresses": [("bore.pub", 41234)], "password": "x"}
            session.last_reason = ""
            mp.compat.tag_redraw()
            view3d().tag_redraw()
            st["step"] = 5
            st["t5"] = time.time()
        elif st["step"] == 5 and ROLE == "guest" and time.time() - st["t5"] < 2:
            view3d().tag_redraw()
        elif st["step"] == 5 and ROLE == "guest" and time.time() - st["t5"] > 3:
            shot("03_rejoindre.png")
            st["step"] = 6
        if st["step"] in (5, 6) and (ROLE == "host" or st["step"] == 6):
            if time.time() - T0 > 10 and st.get("quit_t") is None:
                st["quit_t"] = time.time()
            if st.get("quit_t") and time.time() - st["quit_t"] > 2:
                bpy.ops.wm.quit_blender()
                return None
    except Exception:
        import traceback
        traceback.print_exc()
    return 0.05


bpy.app.timers.register(driver, first_interval=0.5, persistent=True)

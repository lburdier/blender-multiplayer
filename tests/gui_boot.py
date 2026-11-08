"""Test en mode graphique : blender --factory-startup --python gui_boot.py -- <parent> <role> <port> [dossier]

L'hote ouvre une session joignable par Internet et ecrit son lien d'invitation
dans <dossier>/invite.txt ; l'invite rejoint avec ce lien (operateur
mp.join_link), puis fait bouger un objet et sa vue pour que l'on voie la
synchronisation et l'avatar a l'ecran.
"""

import math
import os
import sys
import time

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, ARGS[0])
ROLE = ARGS[1]
PORT = int(ARGS[2])
SHARED = ARGS[3] if len(ARGS) > 3 else os.path.join(os.path.expanduser("~"), "mp_gui_test")
os.makedirs(SHARED, exist_ok=True)
INVITE = os.path.join(SHARED, "invite.txt")

import blender_multiplayer as mp  # noqa: E402
from blender_multiplayer import session  # noqa: E402

mp.register()
bpy.context.preferences.system.use_online_access = True
T0 = time.time()
STATE = {"step": 0}


def open_sidebar():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.show_region_ui = True
                for region in area.regions:
                    if region.type == "UI":
                        try:
                            region.active_panel_category = "Multijoueur"
                        except Exception:
                            pass


def driver():
    t = time.time() - T0
    st = STATE
    try:
        if st["step"] == 0 and t > 1.5:
            open_sidebar()
            if ROLE == "host":
                if os.path.exists(INVITE):
                    os.remove(INVITE)
                session.host(PORT, "", "Demo Internet", username="Hote", color=(0.95, 0.35, 0.25),
                             online=True)
            st["step"] = 1
        elif st["step"] == 1 and ROLE == "host":
            s = session.current
            if s is not None and s.public_address():
                with open(INVITE + ".tmp", "w", encoding="utf-8") as fh:
                    fh.write(s.invite_link())
                os.replace(INVITE + ".tmp", INVITE)
                print("LIEN", s.invite_link(), flush=True)
                st["step"] = 9
        elif st["step"] == 1 and ROLE == "client" and t > 3 and os.path.exists(INVITE):
            link = open(INVITE, encoding="utf-8").read().strip()
            # Force le passage par Internet : on retire les adresses locales du lien.
            data = mp.invite.parse(link)
            pub = [a for a in data["addresses"] if a[0] == "bore.pub"]
            link = mp.invite.make(data["name"], pub, data["password"])
            print("REJOINT", pub, bpy.ops.mp.join_link(link=link), flush=True)
            st["step"] = 2
        elif st["step"] == 2 and ROLE == "client" and session.current and session.current.active:
            st["step"] = 3
            st["t3"] = time.time()
            for win in bpy.context.window_manager.windows:
                for area in win.screen.areas:
                    if area.type == "VIEW_3D":
                        r3d = area.spaces.active.region_3d
                        r3d.view_location = (0, 0, 0)
                        r3d.view_distance = 6
            cube = bpy.data.objects.get("Cube")
            if cube:
                for o in bpy.context.view_layer.objects:
                    o.select_set(False)
                cube.select_set(True)
                bpy.context.view_layer.objects.active = cube
            session.current.send_chat("Salut ! Je suis connecte par Internet.")
        elif st["step"] == 3:
            k = time.time() - st["t3"]
            cube = bpy.data.objects.get("Cube")
            if cube and k < 25:
                cube.location = (math.sin(k) * 2.0, math.cos(k) * 2.0, 0.5)
            for win in bpy.context.window_manager.windows:
                for area in win.screen.areas:
                    if area.type == "VIEW_3D":
                        r3d = area.spaces.active.region_3d
                        from mathutils import Euler
                        r3d.view_rotation = Euler((1.1, 0, k * 0.15)).to_quaternion()
            if k > 4 and not st.get("cone"):
                st["cone"] = True
                bpy.ops.mesh.primitive_cone_add(location=(3, -2, 0))
                bpy.context.active_object.name = "Cone de l'invite"
    except Exception:
        import traceback
        traceback.print_exc()
    return 0.05


bpy.app.timers.register(driver, first_interval=0.5, persistent=True)

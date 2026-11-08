"""Test en mode graphique : blender --factory-startup --python gui_boot.py -- <parent> <role> <port>

Hote et client se connectent tout seuls ; le client fait bouger un objet et
sa vue pour que l'on voie la synchronisation et l'avatar a l'ecran.
"""

import math
import sys
import time

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, ARGS[0])
ROLE = ARGS[1]
PORT = int(ARGS[2])

import blender_multiplayer as mp  # noqa: E402
from blender_multiplayer import session  # noqa: E402

mp.register()
T0 = time.time()
STATE = {"step": 0}


def open_sidebar():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.show_region_ui = True


def driver():
    t = time.time() - T0
    st = STATE
    try:
        if st["step"] == 0 and t > 1.5:
            open_sidebar()
            if ROLE == "host":
                session.host(PORT, "", "Demo GUI", username="Hote", color=(0.95, 0.35, 0.25))
            st["step"] = 1
        elif st["step"] == 1 and ROLE == "client" and t > 5:
            session.join("127.0.0.1", PORT, "", username="Invite", color=(0.25, 0.65, 0.98))
            st["step"] = 2
        elif st["step"] == 2 and ROLE == "client" and session.current and session.current.active:
            st["step"] = 3
            st["t3"] = time.time()
            # vue du client : en hauteur, de biais
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
            session.current.send_chat("Salut ! Je bouge le cube.")
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

"""Cree la scene de demonstration des captures d'ecran.
blender -b --factory-startup --python make_demo_scene.py -- <sortie.blend>"""

import math
import sys

import bpy

OUT = sys.argv[sys.argv.index("--") + 1]

for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)


def mat(name, color, rough=0.5, metal=0.0):
    m = bpy.data.materials.new(name)
    m.diffuse_color = color + (1.0,)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color + (1.0,)
        bsdf.inputs["Roughness"].default_value = rough
        bsdf.inputs["Metallic"].default_value = metal
    return m


def add(op, name, material, **kw):
    op(**kw)
    ob = bpy.context.active_object
    ob.name = name
    ob.data.materials.append(material)
    return ob


sol = add(bpy.ops.mesh.primitive_plane_add, "Sol", mat("Sol", (0.32, 0.33, 0.36), 0.9), size=14)
singe = add(bpy.ops.mesh.primitive_monkey_add, "Suzanne", mat("Orange", (0.95, 0.42, 0.15)),
            location=(0, 0, 1.0), rotation=(math.radians(-10), 0, math.radians(20)))
singe.modifiers.new("Lissage", "SUBSURF").levels = 2
bpy.ops.object.shade_smooth()
tore = add(bpy.ops.mesh.primitive_torus_add, "Anneau", mat("Bleu", (0.15, 0.45, 0.95), 0.3, 0.6),
           location=(-2.8, 1.2, 0.35))
bpy.ops.object.shade_smooth()
add(bpy.ops.mesh.primitive_cylinder_add, "Colonne", mat("Vert", (0.2, 0.75, 0.4)),
    location=(2.8, 1.6, 1.0), radius=0.5, depth=2.0)
add(bpy.ops.mesh.primitive_cube_add, "Caisse", mat("Jaune", (0.95, 0.75, 0.2)),
    location=(2.2, -1.8, 0.5), size=1.0, rotation=(0, 0, math.radians(25)))
add(bpy.ops.mesh.primitive_ico_sphere_add, "Bille", mat("Violet", (0.6, 0.3, 0.9), 0.2),
    location=(-1.6, -2.0, 0.45), radius=0.45, subdivisions=3)
bpy.ops.object.shade_smooth()

bpy.ops.object.light_add(type="SUN", location=(4, -4, 8))
bpy.context.active_object.name = "Soleil"
bpy.ops.object.camera_add(location=(9, -9, 6), rotation=(math.radians(63), 0, math.radians(45)))
bpy.context.active_object.name = "Camera"
bpy.context.scene.camera = bpy.context.active_object
for ob in bpy.context.view_layer.objects:
    ob.select_set(False)
bpy.ops.wm.save_as_mainfile(filepath=OUT)

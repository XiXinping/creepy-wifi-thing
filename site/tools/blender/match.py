"""Render the board the way the Seeed product photo shows it, to compare against the reference.

Run from site/: blender -b tools/blender/assets/xiao_scene.blend --python tools/blender/match.py -- out.png az el dist [lens]
White seamless + soft top light like the photo. Pure diagnostic; the site does not use it.
"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_scene import enable_gpu, look, orbit  # noqa: E402

a = sys.argv[sys.argv.index("--") + 1:]
out, az, el, dist = a[0], float(a[1]), float(a[2]), float(a[3])
lens = float(a[4]) if len(a) > 4 else 100

scene = bpy.context.scene
enable_gpu(scene)
scene.render.resolution_x, scene.render.resolution_y = 700, 525  # reference is 4:3
scene.cycles.samples = int(a[11]) if len(a) > 11 else 64
vt = a[10] if len(a) > 10 else "AgX"
scene.view_settings.view_transform = vt
scene.view_settings.look = "AgX - Punchy" if vt == "AgX" else "None"

floor = bpy.data.objects["floor"]
white = bpy.data.materials.new("seamless")
white.diffuse_color = (1, 1, 1, 1)
white.use_nodes = True
white.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.9, 0.9, 0.9, 1)
white.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.9
floor.data.materials[0] = white
# product-photo lighting: big soft top light, dim surround, sweep blown out by exposure
pool = bpy.data.objects["pool"]
pool.data.energy = float(a[7]) if len(a) > 7 else 300
pool.data.spot_size = math.radians(120)
pool.data.shadow_soft_size = 0.5
env = scene.world.node_tree
env.nodes["Background"].inputs["Color"].default_value = (1, 1, 1, 1)
for link in list(env.links):
    if link.to_node.name == "Background":
        env.links.remove(link)
env.nodes["Background"].inputs["Strength"].default_value = float(a[5]) if len(a) > 5 else 1.0
scene.view_settings.exposure = float(a[6]) if len(a) > 6 else 0.0

cam = scene.camera
cam.constraints.clear()
cam.data.lens = lens
tgt = Vector((float(a[8]) / 1000 if len(a) > 8 else 0, float(a[9]) / 1000 if len(a) > 9 else 0, 0.0015))
cam.matrix_world = look(orbit(tgt, az, el, dist), tgt, Vector((0, 0, 1)))
cam.data.dof.use_dof = False
scene.render.filepath = out
bpy.ops.render.render(write_still=True)

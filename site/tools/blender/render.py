"""Render the scroll sequences from assets/xiao_scene.blend (made by build_scene.py).

Run from site/:
  blender -b tools/blender/assets/xiao_scene.blend --python tools/blender/render.py -- hero [--preview]
  blender -b tools/blender/assets/xiao_scene.blend --python tools/blender/render.py -- rise [--preview]
Then: python3 tools/blender/encode.py  (PNG -> WebP into public/renders)

Frames land in tools/blender/assets/frames/<shot>/####.png. `rise` also writes
public/renders/rise.json: where the room, TX and RX sit in the last frame.
"""
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

HERE = Path(__file__).resolve().parent
SITE = HERE.parents[1]
sys.path.insert(0, str(HERE))
from build_scene import enable_gpu, look, orbit  # noqa: E402
args = sys.argv[sys.argv.index("--") + 1:]
shot = args[0]
preview = "--preview" in args
only = int(args[args.index("--frame") + 1]) if "--frame" in args else None

FRAMES = 96
RES = (640, 400) if preview else (1920, 1200)
SAMPLES = 32 if preview else 256

# Room for the physics stage, metres. Mirrors src/lib/room.ts.
ROOM_W, ROOM_H = 6.0, 3.75
TX_ROOM, RX_ROOM = (1.375, 1.625), (4.625, 2.125)  # grid-cell centres: no grid line grazes a board
FRAME_W = 7.2  # last rise frame spans the room plus margin; 1920x1200 keeps 16:10
HERO_END = dict(spin=0.0, az=-54.0, el=40.0, dist=0.13)  # az/el match the Seeed product photo

scene = bpy.context.scene
enable_gpu(scene)
scene.render.resolution_x, scene.render.resolution_y = RES
scene.render.resolution_percentage = 100
scene.cycles.samples = SAMPLES
cam = scene.camera
target = bpy.data.objects["cam_target"]
rig = bpy.data.objects["XIAO"]
board = bpy.data.objects["board"]

out_dir = HERE / "assets" / "frames" / (shot + ("-preview" if preview else ""))
out_dir.mkdir(parents=True, exist_ok=True)


def smootherstep(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * t * (t * (6 * t - 15) + 10)


def lerp(a, b, t):
    return a + (b - a) * t


def clone_rig(src, name):
    """Linked copy of a rig hierarchy (meshes share data, so it costs no memory)."""
    copies = {}

    def rec(o, parent):
        c = o.copy()
        c.name = f"{name}:{o.name}"
        scene.collection.objects.link(c)
        if o.name in bpy.data.collections["receivers"].objects:
            bpy.data.collections["receivers"].objects.link(c)
        c.parent = parent
        copies[o] = c
        for ch in o.children:
            rec(ch, c)

    rec(src, None)
    return copies[src]


def render_frames(setup):
    cam.constraints.clear()  # framing is computed explicitly per frame
    for i in range(FRAMES):
        if only is not None and i != only:
            continue
        path = out_dir / f"{i:04d}.png"
        if only is None and path.exists():  # resume: a relaunch after a crash or sleep keeps finished frames
            continue
        setup(i / (FRAMES - 1))
        bpy.context.view_layer.update()
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)


def hero(t):
    e = smootherstep(t)
    rig.location = room_to_world(TX_ROOM)  # same spot as the rise, so hero's last frame is rise's first
    board.rotation_euler.z = math.radians(lerp(HERO_END["spin"] - 180.0, HERO_END["spin"], e))
    tgt = rig.location + Vector((0, 0, 0.0015))
    target.location = tgt
    loc = orbit(tgt, HERO_END["az"], lerp(32.0, HERO_END["el"], e), lerp(0.17, HERO_END["dist"], e))
    cam.matrix_world = look(loc, tgt, Vector((0, 0, 1)))


def room_to_world(p):
    return Vector((p[0] - ROOM_W / 2, p[1] - ROOM_H / 2, 0))


def setup_rise():
    tx, rx = room_to_world(TX_ROOM), room_to_world(RX_ROOM)
    rig.location = tx
    board.rotation_euler.z = math.radians(HERO_END["spin"])  # continuous with the hero's last frame
    rx_rig = clone_rig(rig, "rx")
    rx_rig.location = rx
    for ch in rx_rig.children_recursive:
        if ch.name.endswith(":board"):
            ch.rotation_euler.z = math.radians(HERO_END["spin"] + 180)
    cam_data = cam.data
    top_h = FRAME_W / 2 / math.tan(math.atan(cam_data.sensor_width / 2 / cam_data.lens))
    near_tgt = tx + Vector((0, 0, 0.0015))

    centre = Vector((0, 0, 0))
    fov_w = cam_data.sensor_width / cam_data.lens  # view width per metre of distance

    def rise(t):
        e = smootherstep(t)
        dist = math.exp(lerp(math.log(HERO_END["dist"]), math.log(top_h), e))
        # drift toward the room centre only as fast as the widening view allows, so TX never leaves frame
        span = (centre - near_tgt).length
        tgt = near_tgt.lerp(centre, min(1.0, 0.3 * fov_w * (dist - HERO_END["dist"]) / span))
        target.location = tgt
        el = lerp(HERO_END["el"], 90.0, smootherstep((t - 0.1) / 0.9))
        az = lerp(HERO_END["az"], 0.0, e)
        # up hint swings from world Z (horizon view) to world +Y (plan view: x right, y up)
        up = Vector((0, 0, 1)).lerp(Vector((0, 1, 0)), smootherstep((el - 60) / 30)).normalized()
        cam.matrix_world = look(orbit(tgt, az, min(el, 89.999), dist), tgt, up)

    half_h = FRAME_W / 2 * RES[1] / RES[0]
    meta = {
        "frames": FRAMES,
        "room": {"w": ROOM_W, "h": ROOM_H},
        "tx": TX_ROOM,
        "rx": RX_ROOM,
        # last frame, in room metres: x0..x1 left->right, y0..y1 bottom->top
        "frame": {"x0": ROOM_W / 2 - FRAME_W / 2, "x1": ROOM_W / 2 + FRAME_W / 2,
                  "y0": ROOM_H / 2 - half_h, "y1": ROOM_H / 2 + half_h},
    }
    (SITE / "public" / "renders").mkdir(parents=True, exist_ok=True)
    (SITE / "public" / "renders" / "rise.json").write_text(json.dumps(meta, indent=2))
    return rise


if shot == "hero":
    render_frames(hero)
elif shot == "rise":
    render_frames(setup_rise())
else:
    raise SystemExit(f"unknown shot {shot!r}")

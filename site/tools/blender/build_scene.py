"""Build the XIAO ESP32-C5 product scene.

Run: blender -b --factory-startup --python build_scene.py -- [--test out.png]
Writes assets/xiao_scene.blend (gitignored). Needs assets/xiao.glb from step_to_glb.py.
"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


# ── renderer ──────────────────────────────────────────────────────────────
def enable_gpu(scene):
    """Device choice is a user preference, not saved in the .blend: call this in every render process."""
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "ONEAPI"
    prefs.refresh_devices()
    gpu = [d for d in prefs.devices if d.type == "ONEAPI"]
    for d in prefs.devices:
        d.use = d.type == "ONEAPI"
    # hardware RT only exists with Fedora's intel-level-zero-gpu-raytracing installed
    prefs.use_oneapirt = Path("/usr/lib64/libze_intel_gpu_raytracing.so").exists()
    scene.cycles.device = "GPU" if gpu else "CPU"
    print("cycles device:", scene.cycles.device, "hwrt:", prefs.use_oneapirt)


def setup_render(scene, samples=256):
    scene.render.engine = "CYCLES"
    enable_gpu(scene)
    scene.cycles.samples = samples
    scene.cycles.adaptive_threshold = 0.01
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.max_bounces = 12
    scene.cycles.glossy_bounces = 6
    scene.cycles.transmission_bounces = 8
    scene.render.film_transparent = False  # the floor is part of the picture; the page colour is sampled from it
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Punchy"
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "16"


# ── materials ─────────────────────────────────────────────────────────────
def principled(name, **inputs):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes["Principled BSDF"]
    for k, v in inputs.items():
        bsdf.inputs[k].default_value = v
    return m, bsdf


def add_noise_bump(mat, bsdf, scale, strength, distance=0.00002):
    nt = mat.node_tree
    tex = nt.nodes.new("ShaderNodeTexNoise")
    tex.inputs["Scale"].default_value = scale
    tex.inputs["Detail"].default_value = 8
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = strength
    bump.inputs["Distance"].default_value = distance
    nt.links.new(tex.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])


def make_materials():
    M = {}
    # black mask with a fairly tight sheen: in the product photo it only lights up at the mirror angle
    m, b = principled("soldermask", **{"Base Color": (0.004, 0.004, 0.0045, 1), "Roughness": 0.3,
                                       "Specular IOR Level": 0.25})
    add_noise_bump(m, b, 9000, 0.15)  # orange-peel in the mask
    M["soldermask"] = m
    # pads read saturated copper-orange in the product photo
    m, b = principled("enig", **{"Base Color": (0.95, 0.56, 0.40, 1), "Metallic": 1.0, "Roughness": 0.25})
    add_noise_bump(m, b, 20000, 0.1)
    M["enig"] = m
    M["fr4"], _ = principled("fr4_edge", **{"Base Color": (0.10, 0.085, 0.07, 1), "Roughness": 0.6})
    m, b = principled("nickel_silver", **{"Base Color": (0.80, 0.80, 0.78, 1), "Metallic": 1.0,
                                          "Roughness": 0.16, "Anisotropic": 0.6})
    add_noise_bump(m, b, 4000, 0.05)
    M["metal"] = m
    M["plastic_black"], _ = principled("plastic_black", **{"Base Color": (0.02, 0.02, 0.02, 1), "Roughness": 0.35})
    M["plastic_white"], _ = principled("plastic_white", **{"Base Color": (0.85, 0.85, 0.82, 1), "Roughness": 0.3,
                                                           "Subsurface Weight": 0.4, "Subsurface Radius": (0.0005, 0.0005, 0.0005)})
    M["gold"], _ = principled("gold", **{"Base Color": (1.0, 0.78, 0.40, 1), "Metallic": 1.0, "Roughness": 0.18})
    return M


def classify(mat):
    """Map STEP colours (via glTF base colour) onto our materials."""
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    r, g, b = bsdf.inputs["Base Color"].default_value[:3] if bsdf else (0.5, 0.5, 0.5)
    if max(r, g, b) < 0.03:
        return "soldermask"
    if max(r, g, b) < 0.1:
        return "plastic_black"
    if r > 0.85 and b < 0.5:
        return "gold" if g > 0.85 else "enig"
    if r > 0.9 and g > 0.9:
        return "plastic_white"
    if r - b > 0.08:
        return "fr4"
    return "metal"


# ── scene ─────────────────────────────────────────────────────────────────
def build():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    setup_render(scene)
    M = make_materials()

    bpy.ops.import_scene.gltf(filepath=str(ASSETS / "xiao.glb"))
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    for o in meshes:
        for slot in o.material_slots:
            if slot.material and not slot.material.name.startswith(tuple(M)):
                slot.material = M[classify(slot.material)]
        for p in o.data.polygons:
            p.use_smooth = False
        o.data.shade_auto_smooth() if hasattr(o.data, "shade_auto_smooth") else None

    # centre the board at the origin, PCB bottom on z=0
    pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    # XIAO (rig: board + its own light set) > board (turntable pivot) > offset (centres the STEP)
    rig = bpy.data.objects.new("XIAO", None)
    board = bpy.data.objects.new("board", None)
    offset = bpy.data.objects.new("offset", None)
    for o in (rig, board, offset):
        scene.collection.objects.link(o)
    for o in [o for o in bpy.data.objects if o.parent is None and o not in (rig, board, offset)]:
        o.parent = offset
    offset.location = -Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    offset.parent = board
    board.parent = rig

    add_details(offset, M)
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]

    # light linking: the softboxes light only the boards, so the floor shows just the round pool
    receivers = bpy.data.collections.new("receivers")
    for o in meshes:
        receivers.objects.link(o)

    add_floor(scene)
    add_world(scene)
    add_lights(rig, receivers)
    add_camera(scene)
    return scene



def textured(name, image, ink, paper=None, roughness=(0.55, 0.35)):
    """Material whose ink comes from an RGBA image's alpha; paper=None makes the rest transparent."""
    m, b = principled(name, **{"Base Color": ink, "Roughness": roughness[1]})
    nt = m.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(str(ASSETS / image))
    tex.interpolation = "Cubic"
    out = nt.nodes["Material Output"]
    if paper is None:
        clear = nt.nodes.new("ShaderNodeBsdfTransparent")
        mix = nt.nodes.new("ShaderNodeMixShader")
        nt.links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
        nt.links.new(clear.outputs["BSDF"], mix.inputs[1])
        nt.links.new(b.outputs["BSDF"], mix.inputs[2])
        nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    else:
        col = nt.nodes.new("ShaderNodeMix"); col.data_type = "RGBA"
        col.inputs["A"].default_value = paper
        col.inputs["B"].default_value = ink
        rough = nt.nodes.new("ShaderNodeMapRange")
        rough.inputs["To Min"].default_value, rough.inputs["To Max"].default_value = roughness
        spec = nt.nodes.new("ShaderNodeMapRange")  # ink sits under the laminate: no sheen of its own
        spec.inputs["To Min"].default_value, spec.inputs["To Max"].default_value = 0.5, 0.05
        for n in (col.inputs["Factor"], rough.inputs["Value"], spec.inputs["Value"]):
            nt.links.new(tex.outputs["Alpha"], n)
        nt.links.new(col.outputs["Result"], b.inputs["Base Color"])
        nt.links.new(rough.outputs["Result"], b.inputs["Roughness"])
        nt.links.new(spec.outputs["Result"], b.inputs["Specular IOR Level"])
    return m


def quad(name, x0, x1, y0, y1, z, uv, mat, parent):
    """Flat quad in world mm at height z; uv(x, y) -> (u, v)."""
    mesh = bpy.data.meshes.new(name)
    vs = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    mesh.from_pydata([(x / 1000, y / 1000, z / 1000) for x, y in vs], [], [(0, 1, 2, 3)])
    layer = mesh.uv_layers.new()
    for i, (x, y) in enumerate(vs):
        layer.data[i].uv = uv(x, y)
    mesh.materials.append(mat)
    o = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(o)
    o.parent = parent
    o.matrix_parent_inverse = parent.matrix_world.inverted()
    return o


def add_details(offset, M):
    """Things the STEP lacks or simplifies, placed in world mm (board centred, USB-C towards +x)."""
    bpy.context.view_layer.update()
    pcb = next(o for o in bpy.data.objects if o.name.startswith("=>[0:1:1:32]"))
    # U.FL: the STEP paints shell, insulator and pin one gold; the photo shows a tin shell, white
    # insulator and gold pin, so split that slot by distance from the connector axis
    ufl = next(o for o in bpy.data.objects if o.name.startswith("U.FL"))
    slots = [s.material.name for s in ufl.material_slots]
    for name in ("nickel_silver", "plastic_white"):
        if name not in slots:
            ufl.data.materials.append(bpy.data.materials[name])
            slots.append(name)
    gold = slots.index("gold")
    polys = [p for p in ufl.data.polygons if p.material_index == gold]
    centre = sum((p.center for p in polys), Vector()) / len(polys)
    for p in polys:
        r = (p.center - centre).xy.length * 1000 * ufl.matrix_world.to_scale().x
        p.material_index = gold if r < 0.3 else slots.index("plastic_white" if r < 0.75 else "nickel_silver")

    # drop the STEP's placeholder shield label: label + glyphs on the PCB object (above 2.5 mm) and
    # glyphs embossed on the shield sub-assembly (3.020-3.027 mm, i.e. poking through our label plane)
    import bmesh
    for o, z_cut in ((pcb, 0.0025), (next(o for o in bpy.data.objects if o.name.startswith("=>[0:1:1:33]")), 0.003005)):
        bm = bmesh.new()
        bm.from_mesh(o.data)
        mw = o.matrix_world
        doomed = [f for f in bm.faces if (mw @ f.calc_center_median()).z > z_cut]
        bmesh.ops.delete(bm, geom=doomed, context="FACES")
        bm.to_mesh(o.data)
        bm.free()

    # label: image top = USB side (+x), image left = +y; matches the photo's reading direction
    label = textured("label", "label.png", ink=(0.006, 0.006, 0.006, 1), paper=(0.86, 0.86, 0.84, 1),
                     roughness=(0.6, 0.55))
    quad("label", -7.39, 2.81, -6.1, 6.1, 3.012,
         lambda x, y: ((6.1 - y) / 12.2, (x + 7.39) / 10.2), label, offset)
    silk = textured("silkscreen", "silk.png", ink=(0.80, 0.80, 0.76, 1), roughness=(0.6, 0.6))
    quad("silkscreen", -11.23, 9.72, -8.89, 8.89, 1.004,
         lambda x, y: ((x + 11.23) / 20.95, (y + 8.89) / 17.78), silk, offset)

    # 0603 user LED inside its silkscreen outline: clear epoxy over a pale yellow die
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.00927, -0.0058, 0.00126))
    led = bpy.context.object
    led.name = "user_led"
    led.scale = (0.0016, 0.0008, 0.0005)
    bev = led.modifiers.new("bevel", "BEVEL"); bev.width = 0.00006; bev.segments = 3
    m, _ = principled("led", **{"Base Color": (0.95, 0.88, 0.55, 1), "Roughness": 0.15,
                                "Transmission Weight": 0.6, "IOR": 1.5})
    led.data.materials.append(m)
    led.parent = offset
    led.matrix_parent_inverse = offset.matrix_world.inverted()


def add_floor(scene):
    """Cyanotype-paper floor with a faint 25 cm grid aligned to the physics room (see render.py ROOM_*)."""
    bpy.ops.mesh.primitive_plane_add(size=60, location=(0, 0, 0))
    floor = bpy.context.object
    floor.name = "floor"
    m, b = principled("cyanotype_floor", **{"Roughness": 0.9, "Specular IOR Level": 0.05})
    nt = m.node_tree
    N = nt.nodes.new
    L = nt.links.new
    geo = N("ShaderNodeNewGeometry")
    # paper fibre: large blotchy tone + fine grain
    tone = N("ShaderNodeTexNoise"); tone.inputs["Scale"].default_value = 0.8; tone.inputs["Detail"].default_value = 6
    grain = N("ShaderNodeTexNoise"); grain.inputs["Scale"].default_value = 2500; grain.inputs["Detail"].default_value = 4
    L(geo.outputs["Position"], tone.inputs["Vector"])
    L(geo.outputs["Position"], grain.inputs["Vector"])
    ramp = N("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.004, 0.018, 0.060, 1)  # deep Prussian; only the pool spot lights the floor
    ramp.color_ramp.elements[1].color = (0.010, 0.036, 0.110, 1)
    L(tone.outputs["Fac"], ramp.inputs["Fac"])
    # grid: distance to the nearest 0.25 m line, room origin at (-3, -1.875)
    sep = N("ShaderNodeSeparateXYZ"); L(geo.outputs["Position"], sep.inputs["Vector"])

    def line(axis, offset):
        add = N("ShaderNodeMath"); add.operation = "ADD"; add.inputs[1].default_value = offset
        L(sep.outputs[axis], add.inputs[0])
        pp = N("ShaderNodeMath"); pp.operation = "PINGPONG"; pp.inputs[1].default_value = 0.125
        L(add.outputs[0], pp.inputs[0])
        lt = N("ShaderNodeMath"); lt.operation = "LESS_THAN"; lt.inputs[1].default_value = 0.0012
        L(pp.outputs[0], lt.inputs[0])
        return lt

    gx, gy = line("X", 3.0), line("Y", 1.875)
    grid = N("ShaderNodeMath"); grid.operation = "MAXIMUM"
    L(gx.outputs[0], grid.inputs[0]); L(gy.outputs[0], grid.inputs[1])
    mix = N("ShaderNodeMix"); mix.data_type = "RGBA"
    mix.inputs["B"].default_value = (0.016, 0.05, 0.13, 1)  # faded cyanotype line, barely above the paper
    gfac = N("ShaderNodeMath"); gfac.operation = "MULTIPLY"; gfac.inputs[1].default_value = 0.55
    L(grid.outputs[0], gfac.inputs[0])
    L(gfac.outputs[0], mix.inputs["Factor"])
    L(ramp.outputs["Color"], mix.inputs["A"])
    L(mix.outputs["Result"], b.inputs["Base Color"])
    bump = N("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.08; bump.inputs["Distance"].default_value = 0.0001
    L(grain.outputs["Fac"], bump.inputs["Height"])
    L(bump.outputs["Normal"], b.inputs["Normal"])
    floor.data.materials.append(m)


def add_world(scene):
    world = bpy.data.worlds.new("studio")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(str(ASSETS / "studio_small_09.exr"))
    nt.links.new(env.outputs["Color"], nt.nodes["Background"].inputs["Color"])
    nt.nodes["Background"].inputs["Strength"].default_value = 0.04  # dark studio: reflections come from the softboxes


def add_lights(rig, receivers):
    def area(name, az, el, dist, size, energy, color):
        a, e = math.radians(az), math.radians(el)
        loc = (dist * math.cos(e) * math.sin(a), -dist * math.cos(e) * math.cos(a), dist * math.sin(e))
        bpy.ops.object.light_add(type="AREA", location=loc)
        L = bpy.context.object
        L.name = name
        L.data.shape = "RECTANGLE"
        L.data.size, L.data.size_y = size
        L.data.energy = energy
        L.data.color = color
        L.rotation_euler = (-Vector(loc)).to_track_quat("-Z", "Y").to_euler()
        L.visible_camera = False  # softboxes would show as bright quads once the camera pulls back
        L.light_linking.receiver_collection = receivers
        L.parent = rig
        return L

    # Hero camera ends at az -54 el 40 (the product-photo view), so the board top mirrors az 126 el 40.
    # Every light stays well outside that cone; otherwise the whole soldermask reflects it and reads grey.
    area("key", 70, 55, 0.25, (0.16, 0.10), 3.0, (1.0, 0.97, 0.93))
    area("fill", -110, 25, 0.25, (0.10, 0.10), 0.5, (0.95, 0.97, 1.0))
    area("strip", 180, 78, 0.25, (0.24, 0.02), 1.2, (1.0, 1.0, 1.0))
    # cyanotype rim: cool Prussian-blue edge light that ties the render to the page
    area("rim", 115, 8, 0.22, (0.20, 0.02), 2.0, (0.45, 0.62, 0.95))

    # soft round pool of light from above: what marks each board once the camera is metres away
    bpy.ops.object.light_add(type="SPOT", location=(0, 0, 1.2))
    pool = bpy.context.object
    pool.name = "pool"
    pool.data.energy = 150
    pool.data.spot_size = math.radians(34)
    pool.data.spot_blend = 1.0
    pool.data.shadow_soft_size = 0.08
    pool.data.color = (0.78, 0.86, 1.0)
    pool.visible_camera = False
    pool.parent = rig

    # low kicker, not light-linked: throws a short soft shadow so the board sits on the paper
    a, e, d = math.radians(60), math.radians(28), 0.45
    bpy.ops.object.light_add(type="SPOT", location=(d * math.cos(e) * math.sin(a), -d * math.cos(e) * math.cos(a), d * math.sin(e)))
    kick = bpy.context.object
    kick.name = "kicker"
    kick.rotation_euler = (-kick.location).to_track_quat("-Z", "Y").to_euler()
    kick.data.energy = 25
    kick.data.spot_size = math.radians(20)
    kick.data.spot_blend = 1.0
    kick.data.shadow_soft_size = 0.03
    kick.visible_camera = False
    kick.parent = rig


def add_camera(scene):
    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = 100
    cam_data.clip_start, cam_data.clip_end = 0.002, 200  # default 0.1 m clips the board at macro distance
    cam_data.dof.use_dof = True
    cam_data.dof.aperture_fstop = 32
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    target = bpy.data.objects.new("cam_target", None)
    scene.collection.objects.link(target)
    target.location = (0, 0, 0.0015)
    con = cam.constraints.new("TRACK_TO")
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    cam_data.dof.focus_object = target
    place_camera(cam, azimuth=-54, elevation=40, dist=0.13)


def look(cam_loc, tgt, up_hint):
    """Camera matrix looking from cam_loc at tgt; explicit up so straight-down never flips."""
    f = (tgt - cam_loc).normalized()
    r = f.cross(up_hint).normalized()
    u = r.cross(f)
    m = Matrix((r, u, -f)).transposed().to_4x4()
    m.translation = cam_loc
    return m


def orbit(tgt, az, el, dist):
    a, e = math.radians(az), math.radians(el)
    return tgt + Vector((dist * math.cos(e) * math.sin(a), -dist * math.cos(e) * math.cos(a), dist * math.sin(e)))


def place_camera(cam, azimuth, elevation, dist):
    cam.location = orbit(Vector((0, 0, 0)), azimuth, elevation, dist)


if __name__ == "__main__":
    scene = build()
    bpy.ops.wm.save_as_mainfile(filepath=str(ASSETS / "xiao_scene.blend"))
    if "--test" in args:
        scene.render.resolution_x, scene.render.resolution_y = 1200, 800
        scene.cycles.samples = int(args[args.index("--samples") + 1]) if "--samples" in args else 128
        scene.render.filepath = args[args.index("--test") + 1]
        bpy.ops.render.render(write_still=True)

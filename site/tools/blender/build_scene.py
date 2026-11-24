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


def add_noise_bump(mat, bsdf, scale, strength, distance=0.00002, coords=None):
    nt = mat.node_tree
    tex = nt.nodes.new("ShaderNodeTexNoise")
    if coords:  # e.g. "Object": metres, so feature size is the same on every part
        nt.links.new(nt.nodes.new("ShaderNodeTexCoord").outputs[coords], tex.inputs["Vector"])
    tex.inputs["Scale"].default_value = scale
    tex.inputs["Detail"].default_value = 8
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = strength
    bump.inputs["Distance"].default_value = distance
    nt.links.new(tex.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])


def grain_bump(mat, bsdf, scale, strength, rough=None, wave=None):
    """Noise in object space (metres) as bump, `scale` per axis: stretch one axis for a rolled/brushed grain.
    rough=(lo, hi) lets roughness follow the same grain; wave=(scale, strength) adds a soft large undulation."""
    nt = mat.node_tree
    N, L = nt.nodes.new, nt.links.new
    co = N("ShaderNodeTexCoord")
    mp = N("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = scale
    L(co.outputs["Object"], mp.inputs["Vector"])
    g = N("ShaderNodeTexNoise")
    g.inputs["Scale"].default_value = 1.0
    g.inputs["Detail"].default_value = 6
    L(mp.outputs["Vector"], g.inputs["Vector"])
    bump = N("ShaderNodeBump")
    bump.inputs["Strength"].default_value = strength
    bump.inputs["Distance"].default_value = 0.000005
    L(g.outputs["Fac"], bump.inputs["Height"])
    out = bump
    if wave:
        w = N("ShaderNodeTexNoise")
        w.inputs["Scale"].default_value = wave[0]
        w.inputs["Detail"].default_value = 2
        L(co.outputs["Object"], w.inputs["Vector"])
        wb = N("ShaderNodeBump")
        wb.inputs["Strength"].default_value = wave[1]
        wb.inputs["Distance"].default_value = 0.00002
        L(w.outputs["Fac"], wb.inputs["Height"])
        L(bump.outputs["Normal"], wb.inputs["Normal"])
        out = wb
    L(out.outputs["Normal"], bsdf.inputs["Normal"])
    if rough:
        r = N("ShaderNodeMapRange")
        r.inputs["To Min"].default_value, r.inputs["To Max"].default_value = rough
        L(g.outputs["Fac"], r.inputs["Value"])
        L(r.outputs["Result"], bsdf.inputs["Roughness"])


def fr4_edge():
    """Routed PCB edge (z 0..1 mm, world): thin dark mask/copper skins top and bottom, and between them the
    glass-epoxy core, a pale grey-beige with horizontal routing streaks (reference photos' board edge)."""
    m, b = principled("fr4_edge", **{"Roughness": 0.65, "Specular IOR Level": 0.35})
    nt = m.node_tree
    N, L = nt.nodes.new, nt.links.new
    pos = N("ShaderNodeNewGeometry").outputs["Position"]
    mp = N("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (600, 600, 9000)  # long along the edge, fine across its height
    L(pos, mp.inputs["Vector"])
    streak = N("ShaderNodeTexNoise")
    streak.inputs["Scale"].default_value = 1.0
    streak.inputs["Detail"].default_value = 8
    L(mp.outputs["Vector"], streak.inputs["Vector"])
    blot = N("ShaderNodeTexNoise")  # smears from routing dust, ~0.3 mm
    blot.inputs["Scale"].default_value = 3000
    blot.inputs["Detail"].default_value = 4
    L(pos, blot.inputs["Vector"])
    both = N("ShaderNodeMath"); both.operation = "MULTIPLY_ADD"; both.inputs[1].default_value = 1.0
    L(blot.outputs["Fac"], both.inputs[0]); L(streak.outputs["Fac"], both.inputs[2])
    core = N("ShaderNodeValToRGB")
    core.color_ramp.elements[0].position, core.color_ramp.elements[1].position = 0.55, 1.05
    core.color_ramp.elements[0].color = (0.03, 0.028, 0.025, 1)
    core.color_ramp.elements[1].color = (0.2, 0.185, 0.165, 1)
    L(both.outputs[0], core.inputs["Fac"])
    z = N("ShaderNodeSeparateXYZ")
    L(pos, z.inputs["Vector"])
    top = N("ShaderNodeMath"); top.operation = "GREATER_THAN"; top.inputs[1].default_value = 0.00096
    bot = N("ShaderNodeMath"); bot.operation = "LESS_THAN"; bot.inputs[1].default_value = 0.00004
    L(z.outputs["Z"], top.inputs[0]); L(z.outputs["Z"], bot.inputs[0])
    skin = N("ShaderNodeMath"); skin.operation = "MAXIMUM"
    L(top.outputs[0], skin.inputs[0]); L(bot.outputs[0], skin.inputs[1])
    mix = N("ShaderNodeMix"); mix.data_type = "RGBA"
    mix.inputs["B"].default_value = (0.006, 0.006, 0.006, 1)
    L(skin.outputs[0], mix.inputs["Factor"])
    L(core.outputs["Color"], mix.inputs["A"])
    L(mix.outputs["Result"], b.inputs["Base Color"])
    bump = N("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.3; bump.inputs["Distance"].default_value = 0.00001
    L(streak.outputs["Fac"], bump.inputs["Height"])
    L(bump.outputs["Normal"], b.inputs["Normal"])
    return m


def make_materials():
    M = {}
    # black mask with a fairly tight sheen: in the product photo it only lights up at the mirror angle
    m, b = principled("soldermask", **{"Base Color": (0.004, 0.004, 0.0045, 1), "Roughness": 0.3,
                                       "Specular IOR Level": 0.25})
    add_noise_bump(m, b, 9000, 0.15)  # orange-peel in the mask
    M["soldermask"] = m
    # castellated pads: satin copper-salmon with a fine grain, not a flat pink (reference/xiao-c5-top.jpg)
    m, b = principled("enig", **{"Base Color": (0.66, 0.26, 0.12, 1), "Metallic": 1.0, "Roughness": 0.22})
    grain_bump(m, b, (40000, 40000, 40000), 0.2, rough=(0.18, 0.28))
    M["enig"] = m
    M["fr4"] = fr4_edge()
    # stamped, nickel-plated steel (USB shell, shield, switch caps). Contrast-stretched, the photo's USB top shows
    # coarse relief: short streaks ~0.3-1 mm long running along the part, over irregular ~0.5 mm dents. It lives in
    # the surface (bump), so it shows as broken-up reflections; the sheen itself stays uniform (no blotches).
    m, b = principled("nickel_silver", **{"Base Color": (0.72, 0.72, 0.70, 1), "Metallic": 1.0, "Roughness": 0.2})
    nt = m.node_tree
    N, L = nt.nodes.new, nt.links.new
    co = N("ShaderNodeTexCoord").outputs["Object"]
    mp = N("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1600, 7000, 7000)  # streaks: ~0.6 mm long, ~0.15 mm wide
    L(co, mp.inputs["Vector"])
    streak = N("ShaderNodeTexNoise")
    streak.inputs["Scale"].default_value = 1.0
    streak.inputs["Detail"].default_value = 3
    streak.inputs["Distortion"].default_value = 0.6
    L(mp.outputs["Vector"], streak.inputs["Vector"])
    dent = N("ShaderNodeTexNoise")  # irregular dents ~0.5 mm
    dent.inputs["Scale"].default_value = 2000
    dent.inputs["Detail"].default_value = 2
    L(co, dent.inputs["Vector"])
    b1 = N("ShaderNodeBump"); b1.inputs["Strength"].default_value = 0.35; b1.inputs["Distance"].default_value = 0.00002
    L(streak.outputs["Fac"], b1.inputs["Height"])
    b2 = N("ShaderNodeBump"); b2.inputs["Strength"].default_value = 0.25; b2.inputs["Distance"].default_value = 0.00002
    L(dent.outputs["Fac"], b2.inputs["Height"])
    L(b1.outputs["Normal"], b2.inputs["Normal"])
    L(b2.outputs["Normal"], b.inputs["Normal"])
    M["metal"] = m
    M["solder"], _ = principled("solder", **{"Base Color": (0.78, 0.78, 0.76, 1), "Metallic": 1.0, "Roughness": 0.1})
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
    add_antenna(offset, M)
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
    lx0, lx1, ly0, ly1 = SHIELD[0] + 0.12, SHIELD[1] - 0.12, SHIELD[2] + 0.12, SHIELD[3] - 0.12
    quad("label", lx0, lx1, ly0, ly1, SHIELD[4] + 0.012,
         lambda x, y: ((ly1 - y) / (ly1 - ly0), (x - lx0) / (lx1 - lx0)), label, offset)
    silk = textured("silkscreen", "silk.png", ink=(0.80, 0.80, 0.76, 1), roughness=(0.6, 0.6))
    quad("silkscreen", -11.23, 9.72, -8.89, 8.89, 1.004,
         lambda x, y: ((x + 11.23) / 20.95, (y + 8.89) / 17.78), silk, offset)

    # USB-C shell: the photo shows two notches in the top plate's rear edge (the STEP's edge is straight)
    # and small oval latch holes where the STEP has square ones
    # The real part is several stampings: outer shell, an inner shell whose tabs show in the notches, and
    # legs soldered into the board beside it.
    shell = bpy.data.objects["CHAMFER9:1"]
    # the shell is an open-backed tube: its top plate ends in a cut edge over the black insulator, where the
    # STEP rolls it down into a rear wall
    boolean(shell, "DIFFERENCE", block((3.7, 4.15), (-4.7, 4.7), (-0.1, 4.5)))
    for y in (-1.6, 1.6):
        boolean(shell, "UNION", block((5.55, 6.65), (y - 0.65, y + 0.65), (3.82, 4.21)))
        # latch springs are pressed-in oval dimples, not holes
        bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=1, location=(6.1 / 1000, y / 1000, 4.25 / 1000))
        dimple = bpy.context.object
        dimple.scale = (0.00026, 0.00046, 0.00013)
        boolean(shell, "DIFFERENCE", dimple)
        boolean(shell, "DIFFERENCE", block((3.7, 4.85), (y - 0.55, y + 0.55), (3.55, 4.5)))
        tab = block((3.8, 4.8), (y - 0.45, y + 0.45), (3.5, 3.7))
        tab.name = "usb_inner_tab"
        tab.modifiers.new("bevel", "BEVEL").width = 0.00004
        tab.data.materials.append(M["metal"])
        tab.parent = offset
        tab.matrix_parent_inverse = offset.matrix_world.inverted()

    # legs: the STEP has one crude bar under the shell whose ends poke out as white blocks; the photo shows
    # a bent leg each side standing in a solder fillet
    import bmesh
    bar = bpy.data.objects["=>[0:1:1:33]"]
    bm = bmesh.new()
    bm.from_mesh(bar.data)
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if abs((bar.matrix_world @ f.calc_center_median()).y) > 0.0044],
                     context="FACES")
    bm.to_mesh(bar.data)
    bm.free()
    for sy in (-1, 1):
        leg = block((7.65, 8.85), (4.45, 4.6) if sy > 0 else (-4.6, -4.45), (1.0, 2.6))
        ear = block((7.65, 8.85), (4.45, 5.3) if sy > 0 else (-5.3, -4.45), (1.05, 1.2))
        for o in (leg, ear):
            o.name = "usb_leg"
            o.modifiers.new("bevel", "BEVEL").width = 0.00004
            o.data.materials.append(M["metal"])
            o.parent = offset
            o.matrix_parent_inverse = offset.matrix_world.inverted()
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1,
                                             location=(8.25 / 1000, sy * 5.0 / 1000, 1.0 / 1000))
        fillet = bpy.context.object
        fillet.name = "usb_leg_solder"
        fillet.scale = (0.0009, 0.00065, 0.0002)  # a low fillet, not a bead
        fillet.data.materials.append(M["solder"])
        for f in fillet.data.polygons:
            f.use_smooth = True
        fillet.parent = offset
        fillet.matrix_parent_inverse = offset.matrix_world.inverted()

    def own(o, mat, bevel=0.04, segments=2):
        """Parent o to the board with material mat and a bevel of `bevel` mm (0: none)."""
        if bevel:
            b = o.modifiers.new("bevel", "BEVEL"); b.width = bevel / 1000; b.segments = segments
        o.data.materials.clear()  # booleans can leave an empty slot first, which renders default white
        o.data.materials.append(mat)
        for f in o.data.polygons:
            f.material_index = 0
        o.parent = offset
        o.matrix_parent_inverse = offset.matrix_world.inverted()
        return o

    def cyl(r, x, y, z0, z1, verts=48):
        bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r / 1000, depth=(z1 - z0) / 1000,
                                            location=(x / 1000, y / 1000, (z0 + z1) / 2000))
        return bpy.context.object

    # The STEP is the XIAO ESP32-C3's. Everything below is the C5 as measured off reference/xiao-c5-top.jpg
    # rectified onto the board top (homography from the pad holes; heights from the parallax of the 1 mm board edge).

    # tact switches: white plastic base, stamped cap with rolled beads along its long edges, dark plunger;
    # the terminal end of the base pokes out towards -y
    for o in [o for o in bpy.data.objects if o.name.startswith("Button")]:
        pts = [o.matrix_world @ Vector(c) * 1000 for c in o.bound_box]
        x0, x1 = min(q.x for q in pts), max(q.x for q in pts)
        y0, y1 = min(q.y for q in pts), max(q.y for q in pts)
        bpy.data.objects.remove(o)
        xm, by0, by1 = (x0 + x1) / 2, y0 + 0.45, y1 - 0.45
        own(block((x0, x1), (by0, by1), (1.0, 1.35)), M["plastic_white"], 0.06)
        own(block((x0 + 0.45, x1 - 0.45), (y0, by0 + 0.1), (1.0, 1.18)), M["plastic_white"], 0.04)
        own(block((xm - 0.45, xm + 0.45), (by1 - 0.1, y1), (1.0, 1.12)), M["metal"], 0.03)  # cap's solder leg
        own(block((x0 + 0.12, x1 - 0.12), (by0 + 0.1, by1 - 0.1), (1.3, 1.72)), M["metal"], 0.1, 3)
        own(block((xm - 0.95, xm + 0.95), ((by0 + by1) / 2 - 0.95, (by0 + by1) / 2 + 0.95), (1.72, 1.75)), M["metal"], 0.12, 3)
        for bx in (x0 + 0.14, x1 - 0.14):
            bead = cyl(0.1, bx, (by0 + by1) / 2, 0, by1 - by0 - 0.3, 24)
            bead.rotation_euler.x = math.pi / 2
            bead.location.z = 1.6 / 1000
            own(bead, M["metal"], 0)
        own(cyl(0.62, xm, (by0 + by1) / 2, 1.7, 1.92), M["plastic_black"], 0.12, 4)

    # castellations: per pin a 1.0 mm plated through-hole ~1.3 mm in from the edge, in a D-shaped pad that runs
    # out to the edge's plated half-hole. The C3's flat rectangular pads (and its other lands) go under the mask.
    pcb_slots = [s.material.name for s in pcb.material_slots]
    mask_i = pcb_slots.index("soldermask")
    import bmesh as _bm
    bm = _bm.new()
    bm.from_mesh(pcb.data)
    for f in bm.faces:
        c = pcb.matrix_world @ f.calc_center_median()
        # all of the C3's top copper: its pads, its module and switch lands (none of it shows on the C5)
        if pcb_slots[f.material_index] == "enig" and f.normal.z > 0.9 and c.z > 0.0009:
            f.material_index = mask_i
    bm.to_mesh(pcb.data)
    bm.free()
    for k in range(7):
        x = -8.437 + 2.54 * k
        for sy in (-1, 1):
            hy, py = sy * PIN_HOLE_Y, sy * PIN_PAD_Y
            # flush with the mask (a hair above it only so the two surfaces don't fight)
            pad = cyl(0.8, x, py, 1.0, 1.003)
            boolean(pad, "UNION", block((x - 0.8, x + 0.8), sorted((py, sy * 8.89)), (1.0, 1.003)))
            boolean(pad, "DIFFERENCE", cyl(PIN_HOLE_R, x, hy, 0.9, 1.2))
            boolean(pad, "DIFFERENCE", cyl(0.4, x, sy * 8.89, 0.9, 1.2))
            own(pad, M["enig"], 0)
            boolean(pcb, "DIFFERENCE", own(cyl(PIN_HOLE_R, x, hy, -0.2, 1.2), M["enig"], 0), transfer=True)
            # plated half-hole in the board edge: what a castellated pin solders through
            boolean(pcb, "DIFFERENCE", own(cyl(0.4, x, sy * 8.89, -0.2, 1.2), M["enig"], 0), transfer=True)
    # the drilled walls must be plated: whatever slot the boolean mapped them to, make them copper
    pcb_slots = [m.name for m in pcb.data.materials]
    for f in pcb.data.polygons:
        if pcb_slots[f.material_index] not in ("fr4_edge", "enig", "soldermask"):
            f.material_index = pcb_slots.index("enig")

    # RF shield can: the C5's is wider and lower than the C3's in the STEP; the label covers its lid
    bpy.data.objects.remove(next(o for o in bpy.data.objects if o.name.startswith("Shield")))
    x0, x1, y0, y1, top = SHIELD
    can = own(block((x0, x1), (y0, y1), (1.0, top)), M["metal"], 0.03)  # crisp sheet-metal box
    can.name = "shield_c5"

    # 0603 user LED inside its silkscreen outline: tinned end caps, clear epoxy, a small yellow die
    def part(name, loc, size, mat, bevel=0.00004):
        bpy.ops.mesh.primitive_cube_add(size=1, location=[c / 1000 for c in loc])
        o = bpy.context.object
        o.name = name
        o.scale = [c / 1000 for c in size]
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        b = o.modifiers.new("bevel", "BEVEL"); b.width = bevel; b.segments = 3
        o.data.materials.append(mat)
        o.parent = offset
        o.matrix_parent_inverse = offset.matrix_world.inverted()
        return o

    cx, cy = 8.85, -6.45  # clear of the USB shell's rear leg (to y -5.88)
    epoxy, _ = principled("led_epoxy", **{"Base Color": (0.97, 0.96, 0.92, 1), "Roughness": 0.08,
                                          "Transmission Weight": 0.85, "IOR": 1.5})
    die, _ = principled("led_die", **{"Base Color": (0.95, 0.80, 0.35, 1), "Roughness": 0.4})
    part("user_led", (cx, cy, 1.0 + 0.2), (1.1, 0.6, 0.4), epoxy)
    for dx in (-0.62, 0.62):
        part("user_led_cap", (cx + dx, cy, 1.0 + 0.18), (0.22, 0.62, 0.36), M["metal"])
    part("user_led_die", (cx, cy, 1.0 + 0.1), (0.25, 0.25, 0.1), die, 0.00001)


def block(xs, ys, zs):
    """Box in world mm, for booleans."""
    lo, hi = Vector((xs[0], ys[0], zs[0])) / 1000, Vector((xs[1], ys[1], zs[1])) / 1000
    bpy.ops.mesh.primitive_cube_add(size=1, location=(lo + hi) / 2)
    o = bpy.context.object
    o.scale = hi - lo
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)  # so bevel widths are metres
    return o


def boolean(target, op, cutter, transfer=False):
    """Apply cutter to target with an exact boolean, bake it into target's mesh, delete cutter.
    transfer: new faces take the cutter's material (e.g. a drill that leaves a plated wall)."""
    bpy.context.view_layer.update()
    mod = target.modifiers.new("bool", "BOOLEAN")
    mod.operation, mod.solver, mod.object = op, "EXACT", cutter
    if transfer:
        mod.material_mode = "TRANSFER"
    dg = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(target.evaluated_get(dg))
    target.modifiers.remove(mod)
    target.data = mesh
    bpy.data.objects.remove(cutter)


# C5 RF shield can, measured off the rectified photo: x0, x1, y0, y1, top z (mm, board at the origin)
SHIELD = (-7.05, 3.9, -6.55, 6.55, 2.8)
# castellated pins (|y|, mm): the hole sits towards the chip end of its D-shaped pad, so the copper ring is thin
# on that side and wide towards the board edge
PIN_HOLE_Y, PIN_HOLE_R = 7.47, 0.47
PIN_PAD_Y = 7.75  # centre of the pad's round end (radius 0.8)

# U.FL jack centre and top (world mm, board at the origin), from the STEP's "U.FL Connector" bounds
UFL = (-9.6, 0.0, 2.3)


def add_antenna(offset, M):
    """Seeed's FPC antenna for the XIAO (A-01), mated to the U.FL jack: brass plug, ~70 mm of 1.13 mm coax,
    40 x 20 mm black FPC on the floor with the cable soldered near its middle (root-folder reference photos).
    Everything hangs off one empty, "antenna", which render.py slides in; hero renders hide it."""
    ant = bpy.data.objects.new("antenna", None)
    bpy.context.scene.collection.objects.link(ant)
    ant.parent = offset
    ant.matrix_parent_inverse = offset.matrix_world.inverted()
    x, y, z = UFL
    brass, _ = principled("brass", **{"Base Color": (0.86, 0.66, 0.38, 1), "Metallic": 1.0, "Roughness": 0.3})
    coax_m, _ = principled("coax", **{"Base Color": (0.008, 0.008, 0.009, 1), "Roughness": 0.45})
    fpc_m, _ = principled("fpc", **{"Base Color": (0.01, 0.01, 0.011, 1), "Roughness": 0.55,
                                    "Specular IOR Level": 0.3})

    def own(o, parent=ant):
        bpy.context.view_layer.update()
        mw = o.matrix_world.copy()
        o.parent = parent
        o.matrix_world = mw

    def finish(o, name, mat, bevel=0.0001):
        o.name = name
        b = o.modifiers.new("bevel", "BEVEL"); b.width = bevel; b.segments = 2
        o.data.materials.append(mat)
        return o

    # plug: round cap over the jack (mated ~2.4 mm above the PCB), square body, ribbed crimp towards -x
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=0.00105, depth=0.00125,
                                        location=(x / 1000, y / 1000, (z + 0.35) / 1000))
    own(finish(bpy.context.object, "ufl_cap", brass))
    own(finish(block((x - 1.9, x + 0.2), (y - 1.0, y + 1.0), (z + 0.3, z + 0.95)), "ufl_body", brass))
    for i in range(3):
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.00062, depth=0.00035,
                                            location=((x - 2.2 - 0.45 * i) / 1000, y / 1000, (z + 0.6) / 1000),
                                            rotation=(0, math.pi / 2, 0))
        own(finish(bpy.context.object, "ufl_crimp", brass, 0.00005))

    # FPC lies beyond the board's -x/+y corner; the cable leaves the plug along -x and bows round to it
    R = 0.565  # mm, 1.13 mm coax
    phi = math.radians(-60)  # FPC +X (towards its cable edge) points back at the board; FPC sits far side, so the rise reveals it
    rot = Matrix.Rotation(phi, 2)
    exit_l, solder_l, crimp_l = Vector((20.0, -1.8)), Vector((0.0, 1.0)), Vector((-3.6, 1.8))  # FPC-local mm
    edge = Vector((x - 3.2, y)) + 47 * -(rot @ Vector((1, 0)))  # where the cable meets the FPC's edge
    c = edge - rot @ exit_l
    at = lambda v: c + rot @ v
    top = 0.15  # FPC thickness

    root = bpy.data.objects.new("fpc_root", None)
    bpy.context.scene.collection.objects.link(root)
    fpc = finish(block((-20, 20), (-10, 10), (0, top)), "fpc", fpc_m, 0.00003)
    fpc.parent = root
    ink = textured("fpc_ink", "fpc.png", ink=(0.85, 0.85, 0.86, 1), roughness=(0.5, 0.5))
    quad("fpc_print", -20, 20, -10, 10, top + 0.002, lambda u, v: ((u + 20) / 40, (v + 10) / 20), ink, root)
    # tinned crimp soldered to the feed
    blob = finish(block((-3.6, 3.6), (-1.2, 1.2), (0, 0.5)), "fpc_crimp", M["metal"], 0.0002)
    blob.location = (crimp_l.x / 1000, crimp_l.y / 1000, top / 1000)
    blob.rotation_euler.z = math.atan2(solder_l.y - crimp_l.y, solder_l.x - crimp_l.x)
    blob.parent = root
    root.rotation_euler.z = phi
    root.location = (c.x / 1000, c.y / 1000, 0)
    own(root)

    def lay(v, h):
        return (v.x, v.y, h)
    bow = (rot @ Vector((0, 1))) * 4  # gentle sideways bow along the free run
    mid = (Vector((x - 3.2, y)) + edge) / 2 + bow
    pts = [(x - 3.3, y, z + 0.6), (x - 6.5, y + 1.0, R + 0.3), lay(mid, R),
           lay(at(exit_l + Vector((4, -0.4))), R), lay(at(exit_l), top + R), lay(at(solder_l), top + R)]
    cu = bpy.data.curves.new("coax", "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth, cu.bevel_resolution, cu.resolution_u = R / 1000, 6, 24
    sp = cu.splines.new("BEZIER")
    sp.bezier_points.add(len(pts) - 1)
    for bp, co in zip(sp.bezier_points, pts):
        bp.handle_left_type = bp.handle_right_type = "AUTO"
        bp.co = Vector(co) / 1000
    coax = bpy.data.objects.new("coax", cu)
    bpy.context.scene.collection.objects.link(coax)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = coax
    coax.select_set(True)
    bpy.ops.object.convert(target="MESH")  # meshes only, so it joins the light-linked receivers
    coax = bpy.context.object
    coax.data.materials.append(coax_m)
    own(coax)


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

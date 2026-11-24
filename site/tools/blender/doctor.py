"""Render-tooling health check. Run: blender -b --factory-startup --python doctor.py"""
import time
import bpy

ok = True
def report(name, passed, detail=""):
    global ok
    ok &= passed
    print(f"[{'PASS' if passed else 'FAIL'}] {name} {detail}")

print("Blender", bpy.app.version_string, bpy.app.binary_path)

# Colour management: AgX only exists if the OCIO config loaded.
vs = bpy.context.scene.view_settings
try:  # enum is dynamic, so probe by assignment rather than listing items
    vs.view_transform = "AgX"
except TypeError:
    pass
report("OCIO config / AgX", vs.view_transform == "AgX", vs.view_transform)

# Devices
prefs = bpy.context.preferences.addons["cycles"].preferences
found = {}
for backend in ("ONEAPI", "CUDA", "OPTIX", "HIP", "METAL"):
    try:
        prefs.compute_device_type = backend
    except TypeError:
        continue
    prefs.refresh_devices()
    devs = [d.name for d in prefs.devices if d.type == backend]
    if devs:
        found[backend] = devs
report("GPU compute device", bool(found), str(found or "none (CPU only)"))
print("OIDN in build:", bpy.app.build_options.openimagedenoise if hasattr(bpy.app.build_options, "openimagedenoise") else "unknown")

# Test render: monkey + area light, timed per device
bpy.ops.mesh.primitive_monkey_add()
bpy.ops.object.modifier_add(type="SUBSURF")
bpy.ops.object.light_add(type="AREA", location=(2, -2, 3))
bpy.context.object.data.energy = 300
sc = bpy.context.scene
sc.render.engine = "CYCLES"
sc.render.resolution_x = sc.render.resolution_y = 256
sc.render.filepath = "/tmp/doctor_"
sc.cycles.samples = 64
sc.cycles.use_denoising = True

def timed(device):
    sc.cycles.device = device
    t = time.time()
    bpy.ops.render.render(write_still=False)
    return time.time() - t

report("CPU render", True, f"{timed('CPU'):.1f}s")
if found:
    backend, names = next(iter(found.items()))
    prefs.compute_device_type = backend
    for d in prefs.devices:
        d.use = d.type == backend
    # hardware RT needs the intel-level-zero-gpu-raytracing package; probe with it on, fall back off
    for hwrt in (True, False):
        if backend == "ONEAPI":
            prefs.use_oneapirt = hwrt
        try:
            report(f"GPU render ({backend}, hwrt={hwrt})", True, f"{timed('GPU'):.1f}s")
            break
        except Exception as e:
            report(f"GPU render ({backend}, hwrt={hwrt})", False, str(e).splitlines()[0])
print("ALL OK" if ok else "SOME CHECKS FAILED")

#!/usr/bin/env bash
# Full pipeline: scene -> hero + rise frames -> WebP. Run from site/. ~3.5 h on the Arc 140V.
set -euo pipefail
cd "$(dirname "$0")/../.."
python3 tools/blender/textures.py
(cd tools/blender && blender -b --factory-startup --python-exit-code 1 --python build_scene.py)
for shot in hero rise; do
  blender -b tools/blender/assets/xiao_scene.blend --python-exit-code 1 --python tools/blender/render.py -- "$shot"
done
python3 tools/blender/encode.py

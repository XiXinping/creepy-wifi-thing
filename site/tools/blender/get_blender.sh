#!/usr/bin/env bash
# Install the official blender.org build (bundles OCIO 2.5 + oneAPI kernels; Fedora's package has neither).
set -euo pipefail
VER="${1:-5.2.4}"
SERIES="${VER%.*}"
NAME="blender-${VER}-linux-x64"
BASE="https://download.blender.org/release/Blender${SERIES}"
DEST="$HOME/.local/opt"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

mkdir -p "$DEST" "$HOME/.local/bin"
curl -fL --progress-bar -o "$TMP/$NAME.tar.xz" "$BASE/$NAME.tar.xz"
curl -fsSL -o "$TMP/sums" "$BASE/blender-${VER}.sha256"
(cd "$TMP" && grep " $NAME.tar.xz\$" sums | sha256sum -c -)
rm -rf "${DEST:?}/$NAME"
tar -xJf "$TMP/$NAME.tar.xz" -C "$DEST"
ln -sfn "$DEST/$NAME/blender" "$HOME/.local/bin/blender"
echo "$DEST/$NAME/blender"

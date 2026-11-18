#!/usr/bin/env bash
# Build Ombro's Linux app (.deb) in Docker, from macOS or Linux.
#
# Run from gui/: scripts/build_linux.sh            (x86_64, the usual PC)
#                OMBRO_ARCH=arm64 scripts/build_linux.sh
#
# Needs Docker. On an Apple silicon Mac with Colima, an x86_64 build runs far faster under
# Rosetta (softwareupdate --install-rosetta, then
# colima start -p ombro-linux --vm-type vz --vz-rosetta --cpu 6 --memory 8 --disk 60);
# without it, Colima's QEMU emulation works but takes much longer.
#
# The package lands in gui/build/linux/out/. The sidecar is frozen in the container for the
# target, as PyInstaller can't cross-build. Caches (cargo, target, npm, uv) live in Docker volumes,
# so later builds are quicker; `docker volume rm` the ombro-linux-* ones to start clean.
set -euo pipefail

ARCH="${OMBRO_ARCH:-amd64}"
cd "$(dirname "$0")/.."
GUI=$(pwd)
REPO=$(cd .. && pwd)
WORK="$GUI/build/linux"
IMAGE="ombro-linux-build:$ARCH"

echo "== Source (tracked and new files, nothing ignored)"
rm -rf "$WORK/src" && mkdir -p "$WORK/src" "$WORK/out"
(cd "$REPO" && git ls-files -co --exclude-standard -z | rsync -a --from0 --files-from=- ./ "$WORK/src/")

echo "== Toolchain image ($ARCH)"
docker build --platform "linux/$ARCH" -t "$IMAGE" scripts/linux

echo "== Build"
docker run --rm --platform "linux/$ARCH" \
  -v "$WORK/src:/mnt/src:ro" -v "$WORK/out:/out" \
  -v "ombro-linux-cargo-$ARCH:/opt/cargo/registry" \
  -v "ombro-linux-target-$ARCH:/target" \
  -v "ombro-linux-npm-$ARCH:/root/.npm" \
  -v "ombro-linux-uv-$ARCH:/root/.cache/uv" \
  -e CARGO_TARGET_DIR=/target -e UV_PROJECT_ENVIRONMENT=/opt/venv \
  "$IMAGE" bash -euo pipefail -c '
    # Built on the container disk: a Mac disk ignores case, and the Python files include
    # names that differ only in case (terminfo hp2621A and hp2621a).
    mkdir -p /src && cp -a /mnt/src/. /src/
    uv python install 3.14
    cd /src/gui
    npm ci --no-audit --no-fund
    npm run sidecar
    npx tauri build --bundles deb
    rm -f /out/*.deb
    cp /target/release/bundle/deb/*.deb /out/
    dpkg-deb --info /out/*.deb | sed -n "/Package/,/Description/p"
    dpkg-deb --contents /out/*.deb | grep -E "usr/bin|\.desktop|lib/Ombro/python/bin/python3$"
  '
ls -la "$WORK/out"

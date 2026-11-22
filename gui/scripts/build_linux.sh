#!/usr/bin/env bash
# Build Ombro's Linux app: .deb, .rpm and AppImage natively (OMBRO_BUNDLES to choose), .deb in Docker.
#
# On Linux (the route that works), run from gui/:  scripts/build_linux.sh
#   It builds natively for this machine's architecture. Needs Node 22, Rust (rustup), uv, and
#   the -dev libraries Tauri needs (Ubuntu/Debian):
#     sudo apt install build-essential pkg-config file libwebkit2gtk-4.1-dev libxdo-dev \
#       libssl-dev libayatana-appindicator3-dev librsvg2-dev libdbus-1-dev
#   A clean build takes about 10 minutes, mostly Rust. Build on the oldest Ubuntu the package
#   should run on (24.04): a binary built against its glibc runs on later releases.
#
# In Docker (OMBRO_DOCKER=1, or any non-Linux machine): KNOWN TO FAIL from an Apple silicon
#   Mac without Rosetta. Colima falls back to QEMU emulation, and the VM crashed during the
#   Rust link (18 Nov 2026). It may work under Rosetta
#   (softwareupdate --install-rosetta, then
#   colima start -p ombro-linux --vm-type vz --vz-rosetta --cpu 6 --memory 8 --disk 60),
#   but that hasn't been proven end to end. Prefer a native build on a Linux machine.
#     OMBRO_ARCH=arm64 OMBRO_DOCKER=1 scripts/build_linux.sh
#
# The package lands in gui/build/linux/out/. The sidecar is frozen for the target, as
# PyInstaller can't cross-build. In Docker, caches (cargo, target, npm, uv) live in volumes,
# so later builds are quicker; `docker volume rm` the ombro-linux-* ones to start clean.
set -euo pipefail

ARCH="${OMBRO_ARCH:-amd64}"
cd "$(dirname "$0")/.."
GUI=$(pwd)
REPO=$(cd .. && pwd)
WORK="$GUI/build/linux"
IMAGE="ombro-linux-build:$ARCH"

if [ "$(uname -s)" = Linux ] && [ -z "${OMBRO_DOCKER:-}" ]; then
  echo "== Native build on $(uname -m)"
  export UV_PYTHON_PREFERENCE=only-managed
  mkdir -p "$WORK/out"
  uv python install 3.14
  npm ci --no-audit --no-fund
  npm run sidecar
  # linuxdeploy (the AppImage tool) is itself an AppImage: unpacked, it needs no FUSE.
  export APPIMAGE_EXTRACT_AND_RUN=1
  BUNDLES="${OMBRO_BUNDLES:-deb,rpm,appimage}"
  VERSION=$(node -p "require('./src-tauri/tauri.conf.json').version")
  if [[ $VERSION == *-* ]]; then
    # A pre-release (0.2.0-rc1) has no .rpm: RPM forbids "-" in a version, and Tauri accepts
    # only semver, so there is no version both take. The .deb and AppImage are built as usual.
    BUNDLES=$(tr , '\n' <<<"$BUNDLES" | grep -vx rpm | paste -sd, - || true)
    echo "== Pre-release $VERSION: no .rpm"
  fi
  npx tauri build --bundles "$BUNDLES"
  B=src-tauri/target/release/bundle
  rm -f "$WORK/out"/*.deb "$WORK/out"/*.rpm "$WORK/out"/*.AppImage "$WORK/out"/*.sig
  [[ $BUNDLES == *deb* ]] && cp "$B"/deb/*.deb "$WORK/out/"
  [[ $BUNDLES == *rpm* ]] && cp "$B"/rpm/*.rpm "$WORK/out/"
  [[ $BUNDLES == *appimage* ]] && cp "$B"/appimage/*.AppImage "$WORK/out/"
  # Each package's signature for the updater (an AppImage replaces itself; a .deb or .rpm is
  # installed in place), with the updater key, whose public half is in tauri.conf.json.
  if [[ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ]]; then
    for f in "$WORK/out"/*.deb "$WORK/out"/*.rpm "$WORK/out"/*.AppImage; do
      [[ -e "$f" ]] || continue
      npx tauri signer sign "$f" >/dev/null
      [[ -s "$f.sig" ]] || { echo "No signature for $f" >&2; exit 1; }
    done
  else
    echo "== No TAURI_SIGNING_PRIVATE_KEY: the packages aren't signed for the updater"
  fi
  ls -la "$WORK/out"
  exit 0
fi

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

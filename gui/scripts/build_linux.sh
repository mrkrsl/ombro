#!/usr/bin/env bash
# Build Ombro's Linux app: .deb, .rpm and AppImage (OMBRO_BUNDLES to choose), natively on Linux.
#
# Run from gui/ on a Linux machine:  scripts/build_linux.sh
#   It builds natively for this machine's architecture. Needs Node 22, Rust (rustup), uv, and
#   the -dev libraries Tauri needs (Ubuntu/Debian):
#     sudo apt install build-essential pkg-config file libwebkit2gtk-4.1-dev libxdo-dev \
#       libssl-dev libayatana-appindicator3-dev librsvg2-dev libdbus-1-dev
#   A clean build takes about 10 minutes, mostly Rust. Build on the oldest Ubuntu the package
#   should run on (24.04): a binary built against its glibc runs on later releases.
#
# The package lands in gui/build/linux/out/. The sidecar is frozen for this machine, as
# PyInstaller can't cross-build, so build each architecture on a machine of that architecture.
# (A Docker route was removed on 23 Nov 2026: under QEMU on an Apple silicon Mac it crashed.)
set -euo pipefail

cd "$(dirname "$0")/.."
GUI=$(pwd)
WORK="$GUI/build/linux"

if [ "$(uname -s)" != Linux ]; then
  echo "Build the Linux app on a Linux machine (see the top of this script)." >&2
  exit 1
fi

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

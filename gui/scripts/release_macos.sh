#!/usr/bin/env bash
# Build Ombro for macOS, signed with Developer ID and notarised by Apple.
#
# Run from gui/: scripts/release_macos.sh          a release candidate: signed and notarised
#                scripts/release_macos.sh --local  a local test build: signed, not notarised
#
# Needs APPLE_SIGNING_IDENTITY, APPLE_API_KEY, APPLE_API_ISSUER and APPLE_API_KEY_PATH: from the
# environment when they are all set there (the release workflow sets them), or else from
# ~/.private_keys/ombro-notary.env (never in the repo), which exports them.
#
# Only the sidecar (ombro-serve) has hardened-runtime exceptions (entitlements.plist: library
# validation off, which PyInstaller's one-file build needs). The app has none.
# Tauri signs every executable in the bundle with the one entitlements file it is given, so it is
# given none; this script then signs the sidecar again with entitlements.plist, seals the app again,
# and only then notarises it, makes the .dmg, and notarises and staples that too.
set -euo pipefail

LOCAL=0
[[ "${1:-}" == "--local" ]] && LOCAL=1

ENV_FILE="${OMBRO_NOTARY_ENV:-$HOME/.private_keys/ombro-notary.env}"
if [[ -z "${APPLE_SIGNING_IDENTITY:-}" || -z "${APPLE_API_KEY:-}" || -z "${APPLE_API_ISSUER:-}" \
      || -z "${APPLE_API_KEY_PATH:-}" ]]; then
  [[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE" >&2; exit 1; }
  # shellcheck source=/dev/null
  source "$ENV_FILE"
fi
: "${APPLE_SIGNING_IDENTITY:?not set}"
export APPLE_SIGNING_IDENTITY

cd "$(dirname "$0")/.."
ENTITLEMENTS=src-tauri/entitlements.plist

notarise() {
  xcrun notarytool submit "$1" \
    --key "$APPLE_API_KEY_PATH" --key-id "$APPLE_API_KEY" --issuer "$APPLE_API_ISSUER" --wait
}

echo "== Sidecar (signed)"
npm run sidecar

echo "== App (signed by Tauri, with no entitlements; notarised below, once the sidecar is signed again)"
env -u APPLE_API_KEY -u APPLE_API_ISSUER -u APPLE_API_KEY_PATH npx tauri build --bundles app

BUNDLE=src-tauri/target/release/bundle
APP="$BUNDLE/macos/Ombro.app"
SIDECAR="$APP/Contents/MacOS/ombro-serve"

echo "== Sidecar with its entitlements, then the app sealed again"
codesign --force --timestamp --options runtime --entitlements "$ENTITLEMENTS" --sign "$APPLE_SIGNING_IDENTITY" "$SIDECAR"
codesign --force --timestamp --options runtime --sign "$APPLE_SIGNING_IDENTITY" "$APP"

echo "== Entitlements: the sidecar's only"
codesign --verify --deep --strict --verbose=2 "$APP"
codesign -d --entitlements - --xml "$SIDECAR" 2>/dev/null | grep -q disable-library-validation \
  || { echo "The sidecar lost its entitlements" >&2; exit 1; }
if codesign -d --entitlements - --xml "$APP" 2>/dev/null | grep -q disable-library-validation; then
  echo "The app has disable-library-validation; it should have no entitlements" >&2
  exit 1
fi

VERSION=$(node -p 'require("./src-tauri/tauri.conf.json").version')
mkdir -p "$BUNDLE/dmg"
DMG="$BUNDLE/dmg/Ombro_${VERSION}_$(uname -m).dmg"

if [[ $LOCAL == 0 ]]; then
  echo "== Notarise and staple the app"
  ZIP="$BUNDLE/macos/Ombro.zip"
  ditto -c -k --keepParent "$APP" "$ZIP"
  notarise "$ZIP"
  rm -f "$ZIP"
  xcrun stapler staple "$APP"
fi

echo "== $DMG"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/Ombro.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname Ombro -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
codesign --force --timestamp --sign "$APPLE_SIGNING_IDENTITY" "$DMG"

if [[ $LOCAL == 1 ]]; then
  echo "Done (signed, not notarised): $APP"
  exit 0
fi

if [[ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ]]; then
  # The update the app downloads: the notarised, stapled app as it is now, and its signature
  # (with the updater key, whose public half is in tauri.conf.json). Tauri's own would be made
  # before the sidecar is signed again above, so this makes it.
  echo "== Update archive, signed"
  mkdir -p "$BUNDLE/updater"
  UPDATE="$BUNDLE/updater/Ombro_${VERSION}_$(uname -m).app.tar.gz"
  rm -f "$UPDATE" "$UPDATE.sig"
  COPYFILE_DISABLE=1 tar -czf "$UPDATE" -C "$BUNDLE/macos" Ombro.app
  npx tauri signer sign "$UPDATE" >/dev/null
  [[ -s "$UPDATE.sig" ]] || { echo "No signature for $UPDATE" >&2; exit 1; }
else
  echo "== No TAURI_SIGNING_PRIVATE_KEY: no update archive"
fi

echo "== Notarise and staple $DMG"
notarise "$DMG"
xcrun stapler staple "$DMG"

echo "== Gatekeeper"
spctl --assess --type execute --verbose=2 "$APP"
spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
xcrun stapler validate "$APP"
xcrun stapler validate "$DMG"
echo "Done: $DMG"

#!/usr/bin/env bash
# Build Ombro for macOS, signed with Developer ID and notarised by Apple.
#
# Run from gui/: scripts/release_macos.sh
#
# Needs ~/.private_keys/ombro-notary.env (never in the repo), which exports
# APPLE_SIGNING_IDENTITY, APPLE_API_KEY, APPLE_API_ISSUER and APPLE_API_KEY_PATH.
# Tauri reads those to sign and notarise the .app; this script then notarises
# and staples the .dmg too, and checks both with Gatekeeper.
set -euo pipefail

ENV_FILE="${OMBRO_NOTARY_ENV:-$HOME/.private_keys/ombro-notary.env}"
[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE" >&2; exit 1; }
# shellcheck source=/dev/null
source "$ENV_FILE"

cd "$(dirname "$0")/.."

echo "== Sidecar (signed)"
npm run sidecar

echo "== App (signed and notarised by Tauri)"
npx tauri build --bundles app,dmg

BUNDLE=src-tauri/target/release/bundle
APP="$BUNDLE/macos/Ombro.app"
DMG=$(ls "$BUNDLE"/dmg/Ombro_*.dmg | head -1)

echo "== Notarise and staple $DMG"
xcrun notarytool submit "$DMG" \
  --key "$APPLE_API_KEY_PATH" --key-id "$APPLE_API_KEY" --issuer "$APPLE_API_ISSUER" --wait
xcrun stapler staple "$DMG"

echo "== Gatekeeper"
codesign --verify --deep --strict --verbose=2 "$APP"
spctl --assess --type execute --verbose=2 "$APP"
spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
xcrun stapler validate "$APP"
xcrun stapler validate "$DMG"
echo "Done: $DMG"

#!/usr/bin/env bash
# Compile the macOS app icon from icons/AppIcon.icon (Icon Composer's format) with Xcode's actool.
#
# Run from gui/ after changing the artwork: scripts/build_icon.sh
#
# macOS 26 and later show an app's icon from its Assets.car (named by CFBundleIconName in
# src-tauri/Info.plist), masked to the rounded square, so the artwork can run off its edges.
# An app with only an .icns is shrunk onto a grey plate. actool also renders a masked
# icon.icns for older macOS. Both results are committed, so a build needs no Xcode.
#
# To change the artwork, replace icons/AppIcon.icon/Assets/art.png (1024 px, full bleed; light)
# and art-dark.png (the same, for dark mode, from ombro-meta/assets/app-icon-dark.svg),
# or open AppIcon.icon in Icon Composer.
set -euo pipefail
cd "$(dirname "$0")/../src-tauri/icons"
out=$(mktemp -d)
xcrun actool AppIcon.icon --compile "$out" --app-icon AppIcon --platform macosx \
  --minimum-deployment-target 11.0 --target-device mac --output-partial-info-plist "$out/partial.plist" >/dev/null
cp "$out/Assets.car" macos/Assets.car
cp "$out/AppIcon.icns" icon.icns
rm -rf "$out"
echo "Wrote icons/macos/Assets.car and icons/icon.icns"

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
# The PNGs (32x32, 128x128, 128x128@2x, icon.png) are the Linux app's icons and the window's.
# GNOME draws an icon as it is, with no mask, so they are rendered from the same Assets.car,
# rounded square and margin included (scripts/render_icon.swift), not from the full-bleed art.
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
# A throwaway bundle holding the icon (and a stand-in program), for macOS to draw at each size.
app="$out/Icon.app"
mkdir -p "$app/Contents/Resources" "$app/Contents/MacOS"
cp /usr/bin/true "$app/Contents/MacOS/Icon"  # with no program, macOS badges the icon "can't open"
cp "$out/Assets.car" "$out/AppIcon.icns" "$app/Contents/Resources/"
cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>app.ombro.icon-render.$$</string>
<key>CFBundleIconName</key><string>AppIcon</string>
<key>CFBundleIconFile</key><string>AppIcon</string>
<key>CFBundleExecutable</key><string>Icon</string>
<key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
PLIST
swift ../../scripts/render_icon.swift "$app" 32=32x32.png 128=128x128.png 256=128x128@2x.png 512=icon.png
rm -rf "$out"
echo "Wrote icons/macos/Assets.car, icons/icon.icns and the PNGs"

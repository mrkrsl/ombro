// Draw an app bundle's icon, as macOS shows it (masked to the rounded square), into PNGs of the
// sizes given: swift scripts/render_icon.swift <app> <size>=<out.png> ...
// build_icon.sh uses it for the Linux icons, which GNOME draws as they are, with no mask of its own.
import AppKit

let args = CommandLine.arguments
let icon = NSWorkspace.shared.icon(forFile: args[1])
for spec in args.dropFirst(2) {
  let parts = spec.split(separator: "=", maxSplits: 1)
  let size = Int(parts[0])!
  let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8,
                             samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                             bytesPerRow: 0, bitsPerPixel: 0)!
  rep.size = NSSize(width: size, height: size)
  NSGraphicsContext.saveGraphicsState()
  NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
  NSGraphicsContext.current!.imageInterpolation = .high
  icon.draw(in: NSRect(x: 0, y: 0, width: size, height: size))
  NSGraphicsContext.restoreGraphicsState()
  try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: String(parts[1])))
}

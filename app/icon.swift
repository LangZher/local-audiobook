// Draws the app icon (an open book with a sound wave, on black) into an .iconset folder. Usage: swift icon.swift <out.iconset>
import AppKit

let out = CommandLine.arguments[1]
try? FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)

func render(_ px: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = CGFloat(px), inset = s * 0.09
    let bg = NSBezierPath(roundedRect: NSRect(x: inset, y: inset, width: s - 2 * inset, height: s - 2 * inset),
                          xRadius: s * 0.2, yRadius: s * 0.2)
    NSGradient(starting: NSColor(white: 0.16, alpha: 1), ending: NSColor(white: 0.02, alpha: 1))!.draw(in: bg, angle: -90)
    let c = s / 2, w = s * 0.27, h = s * 0.3, y0 = s * 0.38
    for side in [-1.0, 1.0] as [CGFloat] {   // two pages
        let p = NSBezierPath()
        p.move(to: NSPoint(x: c, y: y0))
        p.curve(to: NSPoint(x: c + side * w, y: y0 + s * 0.02), controlPoint1: NSPoint(x: c + side * w * 0.4, y: y0 - s * 0.03),
                controlPoint2: NSPoint(x: c + side * w * 0.8, y: y0))
        p.line(to: NSPoint(x: c + side * w, y: y0 + h))
        p.curve(to: NSPoint(x: c, y: y0 + h - s * 0.02), controlPoint1: NSPoint(x: c + side * w * 0.8, y: y0 + h + s * 0.01),
                controlPoint2: NSPoint(x: c + side * w * 0.4, y: y0 + h - s * 0.04))
        p.close()
        NSColor(red: 0.96, green: 0.94, blue: 0.9, alpha: 1).setFill(); p.fill()
    }
    let gold = NSColor(red: 0.91, green: 0.77, blue: 0.42, alpha: 1)
    for (i, bh) in [0.05, 0.1, 0.16, 0.1, 0.05].enumerated() {   // sound wave under the book
        let x = c + (CGFloat(i) - 2) * s * 0.06, bhh = s * CGFloat(bh)
        let bar = NSBezierPath(roundedRect: NSRect(x: x - s * 0.018, y: s * 0.25 - bhh / 2, width: s * 0.036, height: bhh),
                               xRadius: s * 0.018, yRadius: s * 0.018)
        gold.setFill(); bar.fill()
    }
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

for size in [16, 32, 128, 256, 512] {
    try! render(size).write(to: URL(fileURLWithPath: "\(out)/icon_\(size)x\(size).png"))
    try! render(size * 2).write(to: URL(fileURLWithPath: "\(out)/icon_\(size)x\(size)@2x.png"))
}

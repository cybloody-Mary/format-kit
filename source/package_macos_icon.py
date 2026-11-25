"""Make the Finder and Dock icon from the approved square PNG artwork."""

from pathlib import Path
import subprocess
import tempfile


_RENDER_ICONSET = r"""
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

enum IconRenderError: Error {
    case expectedSquarePNG
    case cannotRender(Int)
    case cannotWrite(URL)
}

let coverURL = URL(fileURLWithPath: CommandLine.arguments[1])
let iconsetURL = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
guard let source = CGImageSourceCreateWithURL(coverURL as CFURL, nil),
      let cover = CGImageSourceCreateImageAtIndex(source, 0, nil),
      cover.width == cover.height else {
    throw IconRenderError.expectedSquarePNG
}

for points in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = points * scale
        let width = CGFloat(pixels)
        let inset = width * 0.085
        let plate = CGRect(x: inset, y: inset, width: width - 2 * inset, height: width - 2 * inset)
        let radius = plate.width * 0.22
        guard let context = CGContext(
            data: nil, width: pixels, height: pixels, bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            throw IconRenderError.cannotRender(pixels)
        }
        context.clear(CGRect(x: 0, y: 0, width: width, height: width))
        context.addPath(CGPath(roundedRect: plate, cornerWidth: radius, cornerHeight: radius, transform: nil))
        context.clip()
        context.interpolationQuality = .high
        context.draw(cover, in: plate)
        guard let raster = context.makeImage() else {
            throw IconRenderError.cannotRender(pixels)
        }
        let suffix = scale == 2 ? "@2x" : ""
        let target = iconsetURL.appendingPathComponent("icon_\(points)x\(points)\(suffix).png")
        guard let writer = CGImageDestinationCreateWithURL(target as CFURL, UTType.png.identifier as CFString, 1, nil) else {
            throw IconRenderError.cannotWrite(target)
        }
        CGImageDestinationAddImage(writer, raster, nil)
        guard CGImageDestinationFinalize(writer) else {
            throw IconRenderError.cannotWrite(target)
        }
    }
}
"""


def build_macos_icon(master_png: Path, destination: Path, *, sdk=None):
    """Render the macOS rounded silhouette and package its Retina sizes."""
    with tempfile.TemporaryDirectory(prefix='format-kit-macos-icon-') as directory:
        work = Path(directory)
        iconset = work / 'AppIcon.iconset'
        iconset.mkdir()
        renderer = work / 'render.swift'
        renderer.write_text(_RENDER_ICONSET, encoding='utf-8')
        command = ['/usr/bin/xcrun', 'swift', '-module-cache-path', str(work / 'module-cache')]
        if sdk is not None:
            command += ['-sdk', str(sdk)]
        subprocess.run([*command, str(renderer), str(master_png.resolve()), str(iconset)], check=True)
        subprocess.run(['/usr/bin/iconutil', '-c', 'icns', '-o', str(destination), str(iconset)], check=True)

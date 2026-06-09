#!/usr/bin/env python3
"""Generate the per-state menu-bar template icons for Sleepless.

Run on macOS (needs PyObjC / AppKit). Writes ``assets/icons/<key>.png`` as
40x40, 72-DPI, black+alpha PNGs suitable as template images (the app marks them
template at load, so macOS tints them for light/dark menu bars). The output is
committed, so CI and end users never run this. If an SF Symbol is unavailable,
falls back to rendering the monochrome text glyph for that state.

    python3 scripts/make_icons.py
"""
from __future__ import annotations

import os

from AppKit import (
    NSImage, NSImageSymbolConfiguration, NSBitmapImageRep, NSGraphicsContext,
    NSColor, NSFont, NSAttributedString, NSDeviceRGBColorSpace,
    NSBitmapImageFileTypePNG, NSCompositingOperationSourceOver,
    NSCompositingOperationSourceAtop, NSRectFillUsingOperation,
    NSFontAttributeName, NSForegroundColorAttributeName,
)
from Foundation import NSMakeRect, NSMakePoint

PX = 40
PAD = 0.82
SYMBOLS = {
    "off": "moon.fill",
    "ac": "bolt.fill",
    "batt": "battery.50",
    "alarm": "exclamationmark.triangle.fill",
}
FALLBACK_GLYPHS = {"off": "⏾", "ac": "☀", "batt": "▲", "alarm": "⚠"}
OUT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "icons"))


def _symbol_image(name):
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if img is None:
        return None
    cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(22.0, 0.0)
    configured = img.imageWithSymbolConfiguration_(cfg)
    return configured if configured is not None else img


def _blank_rep(px):
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, px, px, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0)
    rep.setSize_((px, px))
    return rep


def _render(key, px):
    """Draw the state's symbol (or glyph fallback) as pure black + alpha."""
    rep = _blank_rep(px)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)
    try:
        sym = _symbol_image(SYMBOLS[key])
        if sym is not None:
            size = sym.size()
            scale = min(px / size.width, px / size.height) * PAD
            w, h = size.width * scale, size.height * scale
            sym.drawInRect_fromRect_operation_fraction_(
                NSMakeRect((px - w) / 2.0, (px - h) / 2.0, w, h),
                NSMakeRect(0, 0, size.width, size.height),
                NSCompositingOperationSourceOver, 1.0)
            NSColor.blackColor().set()
            NSRectFillUsingOperation(NSMakeRect(0, 0, px, px),
                                     NSCompositingOperationSourceAtop)
        else:
            attrs = {NSFontAttributeName: NSFont.systemFontOfSize_(26.0),
                     NSForegroundColorAttributeName: NSColor.blackColor()}
            astr = NSAttributedString.alloc().initWithString_attributes_(
                FALLBACK_GLYPHS[key], attrs)
            sz = astr.size()
            astr.drawAtPoint_(NSMakePoint((px - sz.width) / 2.0, (px - sz.height) / 2.0))
    finally:
        NSGraphicsContext.restoreGraphicsState()
    return rep


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for key in SYMBOLS:
        rep = _render(key, PX)
        png = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
        path = os.path.join(OUT_DIR, f"{key}.png")
        if not png.writeToFile_atomically_(path, True):
            raise SystemExit(f"failed to write {path}")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

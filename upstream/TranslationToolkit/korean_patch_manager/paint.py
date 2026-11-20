"""Anti-aliased rounded shapes as RGBA PNG bytes, standard library only.

Tk draws canvas shapes without anti-aliasing, so cards, buttons and badges are
rendered here from a signed distance function and handed to ``tk.PhotoImage``.
Only the corner blocks are evaluated per pixel; straight runs reuse one sample,
which keeps a full-width card well under a millisecond per row.
"""
from __future__ import annotations

import math
import struct
import zlib


def rgb(value):
    """'#RRGGBB' -> (r, g, b)."""
    value = value.lstrip('#')
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def mix(a, b, t):
    """Blend two '#RRGGBB' colours; t=0 gives a, t=1 gives b."""
    ca, cb = rgb(a), rgb(b)
    return '#%02X%02X%02X' % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


def png(width, height, rows):
    """Encode rows of RGBA bytes as a PNG file."""
    def chunk(tag, data):
        body = tag + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body))
    raw = b''.join(b'\x00' + row for row in rows)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 1)) + chunk(b'IEND', b''))


def _distance(px, py, half_w, half_h, radius):
    """Signed distance from a point to a rounded rectangle centred on the origin."""
    qx, qy = abs(px) - (half_w - radius), abs(py) - (half_h - radius)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return outside + min(max(qx, qy), 0.0) - radius


def _over(layers):
    """Composite (colour, alpha) layers bottom to top into one straight-alpha pixel."""
    r = g = b = a = 0.0
    for (cr, cg, cb), alpha in layers:
        if alpha <= 0.0:
            continue
        keep = a * (1.0 - alpha)
        total = alpha + keep
        r = (cr * alpha + r * keep) / total
        g = (cg * alpha + g * keep) / total
        b = (cb * alpha + b * keep) / total
        a = total
    return bytes((round(r), round(g), round(b), round(a * 255)))


def rounded(width, height, radius, fill, border=None, border_width=1.0, shadow=None, gradient=None):
    """Return (png_bytes, pad) for a rounded rectangle.

    ``shadow`` is (colour, alpha, blur, dy). ``gradient`` is a second fill colour
    reached at the bottom edge. ``pad`` is the transparent margin added on every
    side so the shadow has room; place the image at (x - pad, y - pad).
    """
    width, height = max(2, int(width)), max(2, int(height))
    radius = max(0.0, min(float(radius), width / 2.0, height / 2.0))
    pad = 0
    if shadow:
        pad = int(math.ceil(shadow[2] + abs(shadow[3]))) + 1
    full_w, full_h = width + 2 * pad, height + 2 * pad
    half_w, half_h = width / 2.0, height / 2.0
    centre_x, centre_y = pad + half_w, pad + half_h
    top, bottom = rgb(fill), rgb(gradient) if gradient else None
    edge = rgb(border) if border else None

    def pixel(x, y, colour):
        px, py = x + 0.5 - centre_x, y + 0.5 - centre_y
        d = _distance(px, py, half_w, half_h, radius)
        layers = []
        if shadow:
            ds = _distance(px, py - shadow[3], half_w, half_h, radius)
            t = 1.0 if ds <= 0 else max(0.0, 1.0 - ds / shadow[2])
            layers.append((rgb(shadow[0]), shadow[1] * t * t))
        cover = min(1.0, max(0.0, 0.5 - d))
        if edge:
            inner = min(1.0, max(0.0, 0.5 - (d + border_width)))
            layers.append((edge, cover - inner if inner > 0 else cover))
            layers.append((colour, inner))
        else:
            layers.append((colour, cover))
        return _over(layers)

    # Columns left of `split` are sampled; the run up to the mirrored block repeats one sample.
    split = min(full_w // 2, pad + int(math.ceil(radius)) + int(math.ceil(border_width)) + 1)
    middle = full_w - 2 * split
    band = pad + int(math.ceil(radius)) + int(math.ceil(border_width)) + 1
    rows, cache = [], None
    for y in range(full_h):
        t = min(1.0, max(0.0, (y - pad) / max(1.0, height - 1.0)))
        colour = tuple(a + (b - a) * t for a, b in zip(top, bottom)) if bottom else top
        flat = band <= y < full_h - band
        if flat and cache is not None and not bottom:
            rows.append(cache)
            continue
        left = [pixel(x, y, colour) for x in range(split)]
        row = b''.join(left) + pixel(split, y, colour) * middle + b''.join(reversed(left)) if middle > 0 \
            else b''.join(left) + b''.join(reversed(left[:full_w - split]))
        if flat and not bottom:
            cache = row
        rows.append(row)
    return png(full_w, full_h, rows), pad


def compose(width, height, shapes):
    """Small free-form image: shapes are (x, y, w, h, radius, colour, alpha), drawn in order."""
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            layers = []
            for sx, sy, sw, sh, radius, colour, alpha in shapes:
                d = _distance(x + 0.5 - (sx + sw / 2.0), y + 0.5 - (sy + sh / 2.0), sw / 2.0, sh / 2.0,
                              min(radius, sw / 2.0, sh / 2.0))
                layers.append((rgb(colour), alpha * min(1.0, max(0.0, 0.5 - d))))
            row.append(_over(layers))
        rows.append(b''.join(row))
    return png(width, height, rows)


def app_icon(size, accent, accent_dark):
    """A tile with a plus sign: the 'patch' mark used for the window and header."""
    s = size / 64.0
    bar = 9.0 * s
    shapes = [(0, 0, size, size, 15.0 * s, accent, 1.0),
              (0, size * 0.5, size, size * 0.5, 15.0 * s, accent_dark, 0.55),
              (size / 2.0 - bar / 2.0, 15.0 * s, bar, 34.0 * s, bar / 2.0, '#FFFFFF', 1.0),
              (15.0 * s, size / 2.0 - bar / 2.0, 34.0 * s, bar, bar / 2.0, '#FFFFFF', 1.0)]
    return compose(size, size, shapes)

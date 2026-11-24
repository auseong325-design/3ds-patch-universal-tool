r"""Readers for the 3DS Smash lumen font: FGB glyph table + .tex atlas (PICA200 tiled).

FGB (observed on KO3DS files): 16-byte header "FGB\0", u16 LE glyph count at 8, then 32-byte
records. Record: u32 atlas index, u16 x, u16 y, then metrics (little-endian), and the
character code as a big-endian u16 at +24. Unknown fields are kept verbatim.

TEX: 128-byte header (u32 width, u32 height, u8 PICA format, u8 1, name at 0x0C),
then one tiled image. Supported formats: 0x09 LA4 (8 bpp), 0x0B A4 (4 bpp).

Run: .venv\Scripts\python.exe -B -m tools.ctr_font preview <fgb> <out.png> [text]
"""
import struct, sys
from pathlib import Path
from .grayimg import Image   # patch-time images; preview() below uses Pillow

FORMATS = {0x09: ('LA4', 8), 0x0B: ('A4', 4), 0x08: ('A8', 8), 0x07: ('L8', 8)}

class FGB:
    def __init__(self, data):
        if data[:4] != b'FGB\0': raise ValueError('FGB magic')
        self.header = data[:16]; n = struct.unpack_from('<H', data, 8)[0]
        if len(data) != 16 + 32 * n: raise ValueError('FGB size')
        self.records = [data[16 + 32 * i:48 + 32 * i] for i in range(n)]
        if self.serialize() != data: raise ValueError('FGB round trip')

    def code(self, r): return struct.unpack_from('>H', r, 24)[0]
    def atlas(self, r): return struct.unpack_from('<I', r, 0)[0]
    def xy(self, r): return struct.unpack_from('<HH', r, 4)
    def fields(self, r): return struct.unpack_from('<8h', r, 8)  # metrics, not yet named
    def by_code(self): return {self.code(r): r for r in self.records}
    def serialize(self):
        h = bytearray(self.header); struct.pack_into('<H', h, 8, len(self.records))
        return bytes(h) + b''.join(self.records)

def morton(i):
    x = y = 0
    for b in range(3):
        x |= ((i >> (2 * b)) & 1) << b; y |= ((i >> (2 * b + 1)) & 1) << b
    return x, y

class Tex:
    def __init__(self, data):
        self.header = data[:128]
        self.width, self.height = struct.unpack_from('<II', data, 0)
        self.fmt = data[8]  # data[9] is 1 on every observed file (mip count?)
        if self.fmt not in FORMATS: raise ValueError(f'unsupported tex format {self.fmt:#x}')
        self.kind, bpp = FORMATS[self.fmt]
        self.body = data[128:]
        if len(self.body) != self.width * self.height * bpp // 8: raise ValueError('tex size')

    def to_image(self):
        """Return (luminance, alpha) PIL images, top row first."""
        w, h = self.width, self.height; lum = bytearray(w * h); alp = bytearray(w * h)
        tiles_x = w // 8; pix = 0
        for t in range(w * h // 64):
            tx, ty = (t % tiles_x) * 8, (t // tiles_x) * 8
            for i in range(64):
                mx, my = morton(i); x, y = tx + mx, ty + my
                if self.kind == 'A4':
                    v = (self.body[pix // 2] >> (4 * (pix & 1))) & 15; l, a = 255, v * 17
                elif self.kind == 'LA4':
                    v = self.body[pix]; l, a = (v >> 4) * 17, (v & 15) * 17
                else:
                    v = self.body[pix]; l, a = (v, 255) if self.kind == 'L8' else (255, v)
                y = h - 1 - y  # PICA textures are stored bottom row first
                lum[y * w + x] = l; alp[y * w + x] = a; pix += 1
        return Image.frombytes('L', (w, h), bytes(lum)), Image.frombytes('L', (w, h), bytes(alp))

def encode_tex(tex, lum, alpha):
    """Inverse of Tex.to_image for A4/LA4: returns the full .tex bytes (header kept)."""
    w, h = tex.width, tex.height
    L, A = lum.tobytes(), alpha.tobytes()
    tiles_x = w // 8; out = bytearray(len(tex.body)); pix = 0
    for t in range(w * h // 64):
        tx, ty = (t % tiles_x) * 8, (t // tiles_x) * 8
        for i in range(64):
            mx, my = morton(i); x, y = tx + mx, h - 1 - (ty + my)
            a = (A[y * w + x] + 8) // 17
            if tex.kind == 'A4':
                out[pix // 2] |= a << (4 * (pix & 1))
            elif tex.kind == 'LA4':
                out[pix] = (((L[y * w + x] + 8) // 17) << 4) | a
            else:
                raise ValueError('encoder supports A4 and LA4 only')
            pix += 1
    return bytes(tex.header) + bytes(out)

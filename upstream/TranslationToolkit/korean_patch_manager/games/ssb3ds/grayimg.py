"""Minimal 8-bit grayscale image for the font recipe, so the patch pipeline needs no Pillow.

Covers exactly what patchcore/ctr_font use at patch time: Image.frombytes('L', ...), Image.new('L', ...),
paste(int or image, box), tobytes(), width/height/size. The 3DS 한글패치 매니저 runtime ships no third-party
Python packages, so this module is what runs there.
"""


class GrayImage:
    __slots__ = ('width', 'height', 'data')

    def __init__(self, width, height, data):
        if len(data) != width * height:
            raise ValueError('image data size')
        self.width, self.height, self.data = width, height, bytearray(data)

    @property
    def size(self):
        return self.width, self.height

    def tobytes(self):
        return bytes(self.data)

    def paste(self, source, box):
        if isinstance(source, int):
            x0, y0, x1, y1 = box
            x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, self.width), min(y1, self.height)
            if x1 <= x0:
                return
            row = bytes([source]) * (x1 - x0)
            for y in range(y0, y1):
                start = y * self.width + x0
                self.data[start:start + x1 - x0] = row
            return
        x, y = box[0], box[1]
        for sy in range(source.height):
            ty = y + sy
            if not 0 <= ty < self.height:
                continue
            sx0, sx1 = max(0, -x), min(source.width, self.width - x)
            if sx1 <= sx0:
                continue
            start = ty * self.width + x + sx0
            src = sy * source.width
            self.data[start:start + sx1 - sx0] = source.data[src + sx0:src + sx1]


class Image:
    """The two constructors of PIL.Image used by the pipeline (mode 'L' only)."""

    @staticmethod
    def frombytes(mode, size, data):
        if mode != 'L':
            raise ValueError('only mode L is supported')
        return GrayImage(size[0], size[1], data)

    @staticmethod
    def new(mode, size, color=0):
        if mode != 'L':
            raise ValueError('only mode L is supported')
        return GrayImage(size[0], size[1], bytes([color]) * (size[0] * size[1]))

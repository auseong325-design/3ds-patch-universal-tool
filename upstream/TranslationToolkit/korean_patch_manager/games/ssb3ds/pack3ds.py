r"""3DS packed-subtree rebuilder (the pack behind a "<partition>/<dir>/packed" storage key).

Layout model, measured on the Korean 1.1.7 data (message and font packs) and enforced strictly:
  * rows are laid out in RF offset order, the subtree root first;
  * every row starts aligned: .tex files, and directories holding a .tex anywhere below, to 0x80;
    everything else to 0x20;
  * a directory stores a 0x20-byte header; a file stores its bytes plus a 0x20-byte reserve;
    the gap up to the next row's alignment is padding;
  * padding is 0xCC; the pack ends at the end of the last row's stored bytes;
  * dir.compressed_size = max(descendant end) - dir.offset, where a descendant directory ends at
    offset + its compressed_size; dir.size = dir.compressed_size + sum(file.size - file.compressed_size);
  * a member is zlib when its payload starts with a zlib header, otherwise stored raw.
A no-edit rebuild must reproduce the input byte for byte; the constructor verifies this.
"""
import struct, zlib

PAD = 0xCC
FLAG_PACKED_DIR = 0x200
FLAG_STORAGE = 0x400

def is_dir(r): return bool(r['flags'] & FLAG_PACKED_DIR) or r['path'].endswith('/')

def is_zlib(b): return len(b) > 1 and b[0] == 0x78 and (b[0] * 256 + b[1]) % 31 == 0

def align(v, a): return (v + a - 1) // a * a

class Pack3DS:
    level = 6  # zlib level for edited members

    def __init__(self, rf_rows, prefix, pack):
        self.prefix, self.pack = prefix, bytes(pack)
        under = [r for r in rf_rows if r['path'].startswith(prefix)]
        nested = [r['path'] for r in under if r['path'] != prefix and is_dir(r) and r['flags'] & FLAG_STORAGE]
        self.rows = sorted((r for r in under if not any(r['path'].startswith(n) for n in nested)), key=lambda r: r['offset'])
        if not self.rows or self.rows[0]['path'] != prefix:
            raise ValueError(f'{prefix}: root is not the first row')
        self.base = self.rows[0]['offset']
        if self.rows[0]['compressed_size'] != len(self.pack):
            raise ValueError(f'{prefix}: pack length {len(self.pack)} != root cmp {self.rows[0]["compressed_size"]}')
        for r in self.rows:
            if not is_dir(r):
                p = self.payload(r)
                size = len(zlib.decompress(p)) if is_zlib(p) else len(p)
                if size != r['size']:
                    raise ValueError(f'{r["path"]}: size mismatch')
        rebuilt, rows = self.rebuild({})
        if rebuilt != self.pack:
            raise ValueError(f'{prefix}: no-edit rebuild differs from the original pack')
        for a, b in zip(rows, self.rows):
            if (a['offset'], a['compressed_size'], a['size']) != (b['offset'], b['compressed_size'], b['size']):
                raise ValueError(f'{b["path"]}: modelled row {a["offset"]:#x}/{a["compressed_size"]}/{a["size"]} != '
                                 f'{b["offset"]:#x}/{b["compressed_size"]}/{b["size"]}')

    def alignment(self, r):
        if is_dir(r):
            return 0x80 if any(q['path'].startswith(r['path']) and q['path'].endswith('.tex') for q in self.rows) else 0x20
        return 0x80 if r['path'].endswith('.tex') else 0x20

    def payload(self, r):
        rel = r['offset'] - self.base
        return self.pack[rel:rel + r['compressed_size']]

    def files(self): return [r for r in self.rows if not is_dir(r)]

    def members(self):
        out = {}
        for r in self.files():
            p = self.payload(r)
            out[r['path']] = zlib.decompress(p) if is_zlib(p) else p
        return out

    def rebuild(self, edits):
        unknown = set(edits) - {r['path'] for r in self.files()}
        if unknown:
            raise ValueError(f'edits for non-members: {sorted(unknown)}')
        out = bytearray(); placed = []; ends = {}
        for i, r in enumerate(self.rows):
            nxt = self.rows[i + 1] if i + 1 < len(self.rows) else None
            new = dict(r, offset=self.base + len(out))
            if is_dir(r):
                rel = r['offset'] - self.base
                header = self.pack[rel:rel + 0x20]
                start = len(out)
                out += header
                if nxt is not None:
                    out += bytes([PAD]) * (align(start + 0x20, self.alignment(nxt)) - len(out))
            else:
                if r['path'] in edits:
                    raw = edits[r['path']]
                    stored = zlib.compress(raw, self.level) if is_zlib(self.payload(r)) else raw
                    new['compressed_size'], new['size'] = len(stored), len(raw)
                else:
                    stored = self.payload(r)
                end = len(out) + len(stored)
                ends[r['path']] = new['offset'] + len(stored)
                out += stored
                if nxt is not None:
                    out += bytes([PAD]) * (align(end + 0x20, self.alignment(nxt)) - end)
            placed.append(new)
        # directory aggregates, deepest first
        for new in reversed(placed):
            if is_dir(new):
                desc = [q for q in placed if q['path'] != new['path'] and q['path'].startswith(new['path'])]
                if not desc:
                    raise ValueError(f'{new["path"]}: empty directory is outside the model')
                end = max((q['offset'] + q['compressed_size']) if is_dir(q) else ends[q['path']] for q in desc)
                cmp = end - new['offset']
                size = cmp + sum(q['size'] - q['compressed_size'] for q in desc if not is_dir(q))
                new['compressed_size'], new['size'] = cmp, size
        return bytes(out), placed


def rebuild_in_place(pack, edits, level=9):
    """Replace members without moving anything: same offsets, same RF rows.

    For packs whose rows the game reads from the base RF (fonts moved into the update layer),
    every edited member must keep its decoded size and fit its original compressed slot. The
    new zlib stream is written at the original offset and the rest of the slot is 0xCC.
    Returns the new pack bytes; raises if a member does not fit.
    """
    out = bytearray(pack.pack)
    rows = {r['path']: r for r in pack.files()}
    for path, raw in edits.items():
        r = rows[path]
        if len(raw) != r['size']:
            raise ValueError(f'{path}: decoded size {len(raw)} != {r["size"]} (must stay equal)')
        stored = zlib.compress(raw, level)
        if len(stored) > r['compressed_size']:
            raise ValueError(f'{path}: compressed {len(stored)} > slot {r["compressed_size"]}')
        rel = r['offset'] - pack.base
        out[rel:rel + r['compressed_size']] = stored + bytes([PAD]) * (r['compressed_size'] - len(stored))
    return bytes(out)

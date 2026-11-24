"""Whole-subtree RF pack rebuilder (3DS profile).

Copied from the Wii U project (tools/rfpack.py, SHA-256 10ae85ce9e9db4e3fa0047d5ac89d7d3ee2c8adaca3fe2f7d9e53df83f044e8e).
Only change: the 3DS packs use 32-byte blocks and a 32-byte reserve (Wii U: 128 and 64),
derived from the KO3DS update message pack and checked by a byte-identical no-edit rebuild.
The text below describes the Wii U numbers; read 128 as BLOCK and 64 as RESERVE.

Unlike the earlier delta patchers (``tools/font_pack.py``,
``tools/translate_confirmed.py``) which nudged offsets and occasionally left a
directory size slightly wrong, this module rebuilds an entire packed subtree
from scratch.  With no edits it reproduces the original pack bytes and RF entry
rows byte-for-byte; that identity is what the full assembler and its tests rely
on.

Only RF *reading* lives in ``tools/archive.py`` (kept read-only on purpose).
This module adds the writer half: ``encode_rf`` and ``PackedSubtree``.

Entry table layout (little-endian), rediscovered here so the writer does not
depend on archive internals:

    at = u32(dec, 12)
    at += u32(dec, at) * 8 + 4
    at += u32(dec, at) + 4
    entry i is at + i*24, packed as '<6I':
        (offset, name/ext ref, compressed_size, size, timestamp, flags)
    flags & 0xff   -> directory depth
    flags & 0x200  -> packed directory entry
    flags & 0x400  -> owns a storage key (a pack root dir, or a loose own-storage file)
    flags & 0x4000 -> external override: the member is read from a loose file in
                      the update; its compressed_size/size describe that loose file

Pack layout model.  The constructor checks it strictly (every entry must start
exactly where the model puts it), and a no-edit rebuild must then reproduce the
pack byte for byte.  Measured on the JP v304 title: 718 of the 720 update packs
and all 77 packs (update or base dump) that hold JP-text textures fit; the two
update packs that do not (data/stage/end/MarioMaker_f, data/stage/melee/XStadium)
hold external-override rows laid out differently and are refused, never guessed:
  * entries are laid out in RF offset order, the subtree root first;
  * a directory that has descendants occupies its 128-byte header (bytes
    preserved verbatim, 0xCC in practice); its descendants follow directly;
  * every other entry has an *extent* and occupies
    ``(extent + 64 + 127) // 128 * 128`` bytes, padded with 0xCC:
      - a file member: extent = compressed_size (its stored payload);
      - an empty file (compressed_size 0): extent = one 0xCC block (128);
      - an empty leaf directory: extent = its 128-byte header (cmp = size = 128);
      - an external-override row (0x4000): the stale packed copy the update left
        in the pack; extent = that copy's zlib stream length (the RF sizes now
        describe the loose file, so the stale length is read from the pack);
  * the pack ends at the end of the last entry's extent (no trailing padding);
  * ``dir.compressed_size`` = max over descendants of (offset + extent) minus
    ``dir.offset`` (a descendant directory's extent is its own compressed_size);
    the pack length equals ``root.compressed_size``;
  * ``dir.size`` = ``dir.compressed_size`` + sum over descendant file members of
    ``(size - compressed_size)`` (the stale copy's sizes for external overrides);
  * a member is zlib-compressed iff its stored payload starts with ``b'x'``;
    tiny members may be stored raw (``compressed_size == size``).
Rows of a nested pack (a descendant directory with its own storage key) live in
that other pack and are excluded.  A loose own-storage file (0x400 on a file row)
inside a pack subtree is outside the model and is refused.
"""
from __future__ import annotations

import struct
import zlib

from .archive import resource_read, u32

HEADER = 128
BLOCK = 32    # Wii U: 128
RESERVE = 32  # Wii U: 64
PAD = 0xCC
FLAG_DEPTH = 0xFF
FLAG_PACKED_DIR = 0x200
FLAG_STORAGE = 0x400
FLAG_EXTERNAL = 0x4000

# Entry kinds of the layout model.
DIR, LEAF, FILE, EMPTY, EXTERNAL = "dir", "leaf", "file", "empty", "external"


def decode_rf(raw: bytes) -> bytearray:
    """Return the 128-byte header followed by the inflated remainder.

    Mirrors ``resource_read``'s consistency checks so a corrupt RF is rejected
    here rather than producing silent garbage downstream.
    """
    if raw[:2] != b"RF" or u32(raw, 4) != HEADER:
        raise ValueError("RF header")
    dec = zlib.decompress(raw[HEADER:])
    if len(dec) != u32(raw, 28):
        raise ValueError("RF decompressed length")
    if len(raw) - HEADER != u32(raw, 24):
        raise ValueError("RF compressed length")
    return bytearray(raw[:HEADER] + dec)


def encode_rf(decoded: bytes | bytearray) -> bytes:
    """Deflate ``decoded[128:]`` and refresh only the compressed-length u32.

    All other header bytes (including the decompressed-length u32 at +28, which
    does not change when entry values are rewritten in place) are preserved.
    """
    decoded = bytearray(decoded)
    compressed = zlib.compress(bytes(decoded[HEADER:]), 6)
    struct.pack_into("<I", decoded, 24, len(compressed))
    return bytes(decoded[:HEADER]) + compressed


def entry_table_position(decoded: bytes | bytearray) -> int:
    """Byte offset of entry 0 within the decoded RF."""
    pos = u32(decoded, 12)
    pos += u32(decoded, pos) * 8 + 4
    pos += u32(decoded, pos) + 4
    return pos


def pack_key(partition: str, prefix: str) -> str:
    """Storage key of a packed subtree, e.g. ``data(jp_jp)/ui/font/packed``."""
    return f"{partition}/{prefix.rstrip('/')}/packed"


def _file_span(compressed_size: int) -> int:
    """Bytes an entry of the given extent occupies in the pack, 0xCC padding included."""
    return (compressed_size + RESERVE + BLOCK - 1) // BLOCK * BLOCK


def _is_compressed(payload: bytes) -> bool:
    """A member is zlib data iff it starts with a valid zlib header.

    Same test as ``tools.archive.Archive.read`` (0x78 plus the header checksum), so
    a raw member that merely begins with the byte 'x' is not mistaken for zlib.
    """
    return len(payload) > 1 and payload[0] == 0x78 and (payload[0] * 256 + payload[1]) % 31 == 0


def apply_rows(decoded_rf: bytearray, rows: list[dict]) -> None:
    """Write offset/compressed_size/size for each row into the entry table.

    Name/ext ref, timestamp and flags are left exactly as they were — only the
    three geometry fields move.
    """
    at = entry_table_position(decoded_rf)
    for row in rows:
        pos = at + row["entry_index"] * 24
        struct.pack_into("<I", decoded_rf, pos, row["offset"])
        struct.pack_into("<II", decoded_rf, pos + 8, row["compressed_size"], row["size"])


class PackedSubtree:
    """One packed subtree (e.g. every row under ``ui/font/``) of one RF.

    Construct it with the subtree's RF rows (from ``resource_read``), the path
    prefix, and the raw pack bytes.  The layout model is validated strictly on
    load, so a successful construction is itself evidence that it holds on the
    input and that ``rebuild({})`` reproduces the input byte-for-byte.
    """

    def __init__(self, rf_rows: list[dict], prefix: str, pack_bytes: bytes):
        self.prefix = prefix
        self.pack = bytes(pack_bytes)
        under = [r for r in rf_rows if r["path"].startswith(prefix)]
        if not under:
            raise ValueError(f"No rows under prefix {prefix!r}")
        root = next((r for r in under if r["path"] == prefix), None)
        if root is None:
            raise ValueError(f"Subtree root {prefix!r} not found")
        self.root = root
        # Descendant directories with their own storage key are separate packs.
        self.nested = sorted(
            r["path"] for r in under
            if r is not root and self._is_dir(r) and r["flags"] & FLAG_STORAGE
        )
        rows = [r for r in under if not any(r["path"].startswith(n) for n in self.nested)]
        own = [r["path"] for r in rows if not self._is_dir(r) and r["flags"] & FLAG_STORAGE]
        if own:
            raise ValueError(f"{prefix}: own-storage file rows inside the pack: {own[:3]}")
        # RF offset order is the pack layout order (stable: RF order breaks ties).
        self.rows = sorted(rows, key=lambda r: r["offset"])
        if self.rows[0] is not root:
            raise ValueError(f"{prefix}: subtree root is not the first entry of the pack")
        self._by_path = {r["path"]: r for r in self.rows}
        paths = [r["path"] for r in self.rows]
        self.kinds: dict[str, str] = {}
        for r in self.rows:
            if self._is_dir(r):
                has_desc = any(p != r["path"] and p.startswith(r["path"]) for p in paths)
                self.kinds[r["path"]] = DIR if has_desc else LEAF
            elif r["flags"] & FLAG_EXTERNAL:
                self.kinds[r["path"]] = EXTERNAL
            else:
                self.kinds[r["path"]] = EMPTY if r["compressed_size"] == 0 else FILE
        # Stale packed copies behind external-override rows: path -> (cmp, size).
        self.stale: dict[str, tuple[int, int]] = {}
        self._validate()

    # -- classification helpers ------------------------------------------
    @staticmethod
    def _is_dir(row: dict) -> bool:
        return bool(row["flags"] & FLAG_PACKED_DIR) or row["path"].endswith("/")

    def files(self) -> list[dict]:
        """Real pack members (external-override rows are read from loose files)."""
        return [r for r in self.rows if self.kinds[r["path"]] in (FILE, EMPTY)]

    def dirs(self) -> list[dict]:
        return [r for r in self.rows if self.kinds[r["path"]] in (DIR, LEAF)]

    def external_rows(self) -> list[dict]:
        return [r for r in self.rows if self.kinds[r["path"]] == EXTERNAL]

    # -- layout model ----------------------------------------------------
    def _extent(self, kind: str, row: dict) -> int:
        """Bytes an entry counts towards directory sizes and the pack end."""
        if kind == DIR:
            return row["compressed_size"]
        if kind == EXTERNAL:
            return self.stale[row["path"]][0]
        if kind in (LEAF, EMPTY):
            return BLOCK
        return row["compressed_size"]

    @staticmethod
    def _span(kind: str, extent: int) -> int:
        """Bytes from an entry's offset to the next entry's offset."""
        return BLOCK if kind == DIR else _file_span(extent)

    def _aggregates(self, rows: list[dict], kinds: dict[str, str]) -> dict[str, tuple[int, int]]:
        """Directory (compressed_size, size) under the model, rows in layout order."""
        out: dict[str, tuple[int, int]] = {}
        end: dict[str, int] = {}
        for r in reversed(rows):  # descendants always come after their directory
            path, kind = r["path"], kinds[r["path"]]
            if kind in (DIR, LEAF):
                desc = [q for q in rows if q["path"] != path and q["path"].startswith(path)]
                cmp = max(end[q["path"]] for q in desc) - r["offset"] if desc else BLOCK
                size = cmp
                for q in desc:
                    qkind = kinds[q["path"]]
                    if qkind in (FILE, EMPTY):
                        size += q["size"] - q["compressed_size"]
                    elif qkind == EXTERNAL:
                        stale_cmp, stale_size = self.stale[q["path"]]
                        size += stale_size - stale_cmp
                out[path] = (cmp, size)
                end[path] = r["offset"] + cmp
            else:
                end[path] = r["offset"] + self._extent(kind, r)
        return out

    def _check_pad(self, row: dict, start: int, stop: int) -> None:
        pad = self.pack[start:min(stop, len(self.pack))]
        if pad and any(b != PAD for b in pad):
            raise ValueError(f"{row['path']}: padding is not 0xCC")

    # -- validation ------------------------------------------------------
    def _validate(self) -> None:
        if self.root["compressed_size"] != len(self.pack):
            raise ValueError(
                f"{self.prefix}: pack length {len(self.pack)} != root cmp "
                f"{self.root['compressed_size']}"
            )
        base = self.root["offset"]
        pos = 0
        last_end = 0
        for row in self.rows:
            path, kind = row["path"], self.kinds[row["path"]]
            rel = row["offset"] - base
            if rel != pos:
                raise ValueError(f"{path}: offset {rel} is not the modelled position {pos}")
            if kind == EXTERNAL:
                # The stale packed copy: its exact zlib stream length and size.
                d = zlib.decompressobj()
                try:
                    stale = d.decompress(self.pack[rel:])
                except zlib.error as e:
                    raise ValueError(f"{path}: stale copy behind external override is not zlib ({e})")
                if not d.eof or not _is_compressed(self.pack[rel:rel + 2]):
                    raise ValueError(f"{path}: stale copy behind external override is not zlib")
                self.stale[path] = (len(self.pack) - rel - len(d.unused_data), len(stale))
            if kind in (DIR, LEAF):
                if rel + BLOCK > len(self.pack):
                    raise ValueError(f"{path}: dir header overflows pack")
                if kind == LEAF:
                    if (row["compressed_size"], row["size"]) != (BLOCK, BLOCK):
                        raise ValueError(f"{path}: empty leaf directory is not 128/128")
                    self._check_pad(row, rel + BLOCK, rel + _file_span(BLOCK))
                extent = BLOCK
            elif kind == EMPTY:
                if row["size"] != 0:
                    raise ValueError(f"{path}: empty member with size {row['size']}")
                extent = BLOCK
                self._check_pad(row, rel, rel + _file_span(extent))
            elif kind == FILE:
                payload = self.pack[rel: rel + row["compressed_size"]]
                if len(payload) != row["compressed_size"]:
                    raise ValueError(f"{path}: file payload truncated")
                if _is_compressed(payload):
                    if len(zlib.decompress(payload)) != row["size"]:
                        raise ValueError(f"{path}: decoded size mismatch")
                elif row["compressed_size"] != row["size"]:
                    raise ValueError(
                        f"{path}: raw member cmp != size "
                        f"({row['compressed_size']} != {row['size']})"
                    )
                extent = row["compressed_size"]
                self._check_pad(row, rel + extent, rel + _file_span(extent))
            else:  # EXTERNAL
                extent = self.stale[path][0]
                self._check_pad(row, rel + extent, rel + _file_span(extent))
            last_end = rel + extent
            pos += self._span(kind, extent)
        if last_end != len(self.pack):
            raise ValueError(f"{self.prefix}: pack ends at {len(self.pack)}, model says {last_end}")
        for path, (cmp, size) in self._aggregates(self.rows, self.kinds).items():
            row = self._by_path[path]
            if row["compressed_size"] != cmp:
                raise ValueError(f"{path}: dir cmp {row['compressed_size']} != {cmp}")
            if row["size"] != size:
                raise ValueError(f"{path}: dir size {row['size']} != {size}")

    # -- reading ---------------------------------------------------------
    def _payload(self, row: dict) -> bytes:
        rel = row["offset"] - self.root["offset"]
        return self.pack[rel: rel + row["compressed_size"]]

    def members(self) -> dict[str, bytes]:
        """Return {path: decoded bytes} for every file member of the subtree."""
        out: dict[str, bytes] = {}
        for row in self.files():
            payload = self._payload(row)
            out[row["path"]] = zlib.decompress(payload) if _is_compressed(payload) else payload
        return out

    def stored(self, path: str) -> bytes:
        """The raw (possibly compressed) stored bytes of one member."""
        row = next(r for r in self.files() if r["path"] == path)
        return self._payload(row)

    # -- rebuilding ------------------------------------------------------
    def rebuild(self, edits: dict[str, bytes] | None = None) -> tuple[bytes, list[dict]]:
        """Rebuild the pack, applying ``edits`` (path -> new decoded bytes).

        Unchanged entries keep their original bytes verbatim (payload, directory
        header, stale copy behind an external override).  An edited member is
        re-compressed with zlib level 6 when the original was compressed,
        otherwise stored raw.  Returns ``(new_pack, updated_rows)`` where
        ``updated_rows`` are fresh copies (originals untouched) with new
        offset / compressed_size / size for every subtree row, ready for
        ``apply_rows``.  External-override rows only move: their sizes keep
        describing the loose file.
        """
        edits = edits or {}
        unknown = set(edits) - {r["path"] for r in self.files()}
        if unknown:
            raise ValueError(f"Edit for non-member path(s): {sorted(unknown)}")

        base = self.root["offset"]
        placed: list[dict] = []
        kinds: dict[str, str] = {}
        pack = bytearray()
        last_end = 0
        for row in self.rows:
            path, kind = row["path"], self.kinds[row["path"]]
            orig_rel = row["offset"] - base
            new = dict(row, offset=base + len(pack))
            start = len(pack)
            if kind in (DIR, LEAF):
                header = self.pack[orig_rel: orig_rel + BLOCK]
                if len(header) != BLOCK:
                    raise ValueError(f"{path}: short dir header")
                content, extent = header, BLOCK
            elif kind == EXTERNAL:
                extent = self.stale[path][0]
                content = self.pack[orig_rel: orig_rel + extent]
            else:
                if path in edits:
                    decoded = edits[path]
                    # Compress when the original was compressed; raw stays raw.
                    stored = zlib.compress(decoded, 6) if _is_compressed(self._payload(row)) else decoded
                    new["compressed_size"], new["size"] = len(stored), len(decoded)
                    kind = FILE if stored else EMPTY
                else:
                    stored = self._payload(row)
                content = stored
                extent = len(stored) if kind == FILE else BLOCK
            pack += content + bytes([PAD]) * (self._span(kind, extent) - len(content))
            last_end = start + extent
            kinds[path] = kind
            placed.append(new)

        # The pack ends at the last entry's extent, exactly as in the original.
        del pack[last_end:]

        # Directory aggregates from the freshly placed entries.
        aggregates = self._aggregates(placed, kinds)
        for new in placed:
            if new["path"] in aggregates:
                new["compressed_size"], new["size"] = aggregates[new["path"]]
        return bytes(pack), placed


def load_subtree(rf_path, prefix: str, pack_bytes: bytes) -> PackedSubtree:
    """Convenience: read an RF file and build a PackedSubtree from it."""
    return PackedSubtree(resource_read(rf_path), prefix, pack_bytes)

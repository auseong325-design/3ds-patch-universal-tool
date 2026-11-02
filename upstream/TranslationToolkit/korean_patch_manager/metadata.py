"""Read public 3DS container headers without decrypting game content.

Layout references: 3dbrew NCCH, NCSD, CIA and TMD format documentation.
Header fields are not signature-verified and do not prove installed updates.
"""
from pathlib import Path


def _read(stream, offset, size, length):
    if offset < 0 or size < 0 or offset + size > length:
        raise ValueError('파일의 헤더 또는 섹션이 잘렸습니다.')
    stream.seek(offset)
    data = stream.read(size)
    if len(data) != size: raise ValueError('파일 읽기가 중단되었습니다.')
    return data


def identify(path):
    path = Path(path)
    length = path.stat().st_size
    with path.open('rb') as stream:
        header = _read(stream, 0, 0x200, length)
        magic = header[0x100:0x104]
        if magic == b'NCSD':
            offset = int.from_bytes(header[0x120:0x124], 'little') * 0x200
            part_size = int.from_bytes(header[0x124:0x128], 'little') * 0x200
            if not offset or part_size < 0x200 or offset + part_size > length:
                raise ValueError('NCSD의 첫 파티션 범위가 올바르지 않습니다.')
            ncch = _read(stream, offset, 0x200, length)
            if ncch[0x100:0x104] != b'NCCH': raise ValueError('첫 파티션에 NCCH 헤더가 없습니다.')
            return _ncch(ncch, 'NCSD/CCI')
        if magic == b'NCCH': return _ncch(header, 'NCCH')
        if int.from_bytes(header[:4], 'little') == 0x2020:
            align = lambda x: (x + 63) & ~63
            cert = int.from_bytes(header[8:12], 'little')
            ticket = int.from_bytes(header[12:16], 'little')
            tmd_size = int.from_bytes(header[16:20], 'little')
            tmd_offset = align(0x2020) + align(cert) + align(ticket)
            if tmd_size < 4 or tmd_offset + tmd_size > length: raise ValueError('CIA의 TMD 범위가 올바르지 않습니다.')
            sig = int.from_bytes(_read(stream, tmd_offset, 4, length), 'big')
            sizes = {0x10000:0x240, 0x10001:0x140, 0x10002:0x80,
                     0x10003:0x240, 0x10004:0x140, 0x10005:0x80}
            if sig not in sizes: raise ValueError('지원하지 않는 TMD 서명 형식입니다.')
            offset = sizes[sig]
            if offset + 0x9e > tmd_size: raise ValueError('CIA TMD 헤더가 잘렸습니다.')
            tmd = _read(stream, tmd_offset + offset, 0x9e, length)
            tid = int.from_bytes(tmd[0x4c:0x54], 'big')
            version = int.from_bytes(tmd[0x9c:0x9e], 'big')
            if not tid: raise ValueError('CIA Title ID가 비어 있습니다.')
            return {'format':'CIA', 'title_id':f'{tid:016X}', 'title_version':version,
                    'version_display':f'{version >> 10}.{(version >> 4) & 63}.{version & 15}', 'product_code':None}
        raise ValueError('지원되는 CIA, CCI/3DS 또는 NCCH 헤더가 아닙니다. ZCCI는 아직 지원하지 않습니다.')


def _ncch(data, fmt):
    tid = int.from_bytes(data[0x118:0x120], 'little')
    if not tid: raise ValueError('NCCH Program ID가 비어 있습니다.')
    product = data[0x150:0x160].split(b'\0', 1)[0].decode('ascii', errors='replace')
    return {'format':fmt, 'title_id':f'{tid:016X}', 'title_version':None, 'version_display':None, 'product_code':product}

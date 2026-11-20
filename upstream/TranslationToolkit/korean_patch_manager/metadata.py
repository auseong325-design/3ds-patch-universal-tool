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


SMDH_SIZE = 0x36C0
SMDH_LANGUAGES = ('ja', 'en', 'fr', 'de', 'it', 'es', 'zh-CN', 'ko', 'nl', 'pt', 'ru', 'zh-TW')


def smdh_titles(data):
    """Short title, long title and publisher for each language slot of an SMDH (ExeFS icon)."""
    if len(data) != SMDH_SIZE or data[:4] != b'SMDH':
        raise ValueError('HOME 메뉴 아이콘(SMDH) 파일이 아닙니다. 크기는 14,016바이트이고 SMDH로 시작해야 합니다.')
    rows = {}
    for index, language in enumerate(SMDH_LANGUAGES):
        base = 8 + index * 0x200
        text = lambda start, size: data[base + start:base + start + size].decode('utf-16-le', errors='replace').split('\0')[0]
        rows[language] = (text(0, 0x80), text(0x80, 0x100), text(0x180, 0x80))
    return rows


def smdh_with_title(data, title, language='ko'):
    """Copy of an SMDH whose title for one language is replaced; icons, other languages and flags stay."""
    current = smdh_titles(data)[language]
    title = title.strip()
    if not title or len(title.encode('utf-16-le')) > 0x7E or any(ord(c) < 32 for c in title):
        raise ValueError('HOME 메뉴 이름은 1~63자의 한 줄 글자여야 합니다.')
    field = lambda text, size: text.encode('utf-16-le').ljust(size, b'\0')
    base = 8 + SMDH_LANGUAGES.index(language) * 0x200
    result = bytearray(data)
    result[base:base + 0x180] = field(title, 0x80) + field(title, 0x100)
    if not current[2]:      # keep the publisher unless the slot never had one
        result[base + 0x180:base + 0x200] = field(smdh_titles(data)['en'][2] or 'Nintendo', 0x80)
    return bytes(result)


def cia_certificates(path):
    """The certificate chain stored in a CIA; it is the same public data RomForge expects as certs.bin."""
    path = Path(path)
    length = path.stat().st_size
    with path.open('rb') as stream:
        header = _read(stream, 0, 0x20, length)
        if int.from_bytes(header[:4], 'little') != 0x2020:
            raise ValueError('CIA 파일이 아닙니다.')
        size = int.from_bytes(header[8:12], 'little')
        if not 0x300 <= size <= 0x4000:
            raise ValueError('CIA의 인증서 영역 크기가 올바르지 않습니다.')
        chain = _read(stream, (0x2020 + 63) & ~63, size, length)
    if b'Root-CA' not in chain:
        raise ValueError('CIA의 인증서 영역을 읽을 수 없습니다.')
    return chain


def _ncch(data, fmt):
    tid = int.from_bytes(data[0x118:0x120], 'little')
    if not tid: raise ValueError('NCCH Program ID가 비어 있습니다.')
    product = data[0x150:0x160].split(b'\0', 1)[0].decode('ascii', errors='replace')
    return {'format':fmt, 'title_id':f'{tid:016X}', 'title_version':None, 'version_display':None, 'product_code':product}

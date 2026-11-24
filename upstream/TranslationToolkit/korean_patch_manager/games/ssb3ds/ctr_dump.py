r"""Read the few files the patch needs straight from a decrypted 3DS dump (.3ds/.cci or .cia).

No external tool and no full extraction: the NCSD/CIA container is parsed to the first NCCH,
then the RomFS (IVFC level 3) root files and the ExeFS ".code" section are located and copied.
Only decrypted ("NoCrypto") dumps are supported; an encrypted dump raises E12 with instructions.
"""
import os, struct
from pathlib import Path
from .patchcore import PatchError

MEDIA = 0x200
BASE_TID = 0x0004000000167C00
UPDATE_TID = 0x0004000E00167C00

def _u32(b, o): return struct.unpack_from('<I', b, o)[0]
def _u64(b, o): return struct.unpack_from('<Q', b, o)[0]
def _align(v, a): return (v + a - 1) // a * a

class Dump:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise PatchError('E10', f'파일을 찾을 수 없습니다: {self.path.name}', '파일 위치를 다시 선택해 주세요.')
        try:
            self.f = open(self.path, 'rb')
        except OSError as e:
            raise PatchError('E10', f'파일을 열 수 없습니다: {self.path.name} ({e.strerror})',
                             '다른 프로그램이 파일을 쓰고 있지 않은지 확인해 주세요.')
        if self.path.stat().st_size < 0x10000:
            self.f.close()
            raise PatchError('E11', f'{self.path.name}은(는) 3DS 덤프(.3ds/.cci/.cia)가 아닙니다 (파일이 너무 작음).',
                             '사용설명서의 GodMode9 덤프 방법대로 만든 파일을 선택해 주세요.')
        head = self._read(0, 0x4000)
        if head[0x100:0x104] == b'NCSD':
            self.kind = '3DS(카트리지 덤프)'
            self.ncch = _u32(head, 0x120) * MEDIA
        elif _u32(head, 0) == 0x2020:
            self.kind = 'CIA'
            cert, tik, tmd = _u32(head, 8), _u32(head, 12), _u32(head, 16)
            self.ncch = _align(0x2020, 64) + _align(cert, 64) + _align(tik, 64) + _align(tmd, 64)
        else:
            raise PatchError('E11', f'{self.path.name}은(는) 3DS 덤프(.3ds/.cci/.cia)가 아닙니다.',
                             '사용설명서의 GodMode9 덤프 방법대로 만든 파일을 선택해 주세요.')
        h = self._read(self.ncch, 0x200)
        if h[0x100:0x104] != b'NCCH':
            raise PatchError('E12', f'{self.path.name}이(가) 암호화되어 있거나 손상됐습니다.',
                             'GodMode9에서 이 파일을 선택 → "Decrypt file"(복호화)로 다시 만든 뒤 선택해 주세요.')
        self.title_id = _u64(h, 0x118)
        if not h[0x18F] & 0x04:
            raise PatchError('E12', f'{self.path.name}이(가) 암호화된 덤프입니다.',
                             'GodMode9에서 이 파일을 선택 → "Decrypt file"(복호화)로 다시 만든 뒤 선택해 주세요.')
        self.exefs = self.ncch + _u32(h, 0x1A0) * MEDIA
        self.romfs = self.ncch + _u32(h, 0x1B0) * MEDIA
        self.has_romfs = _u32(h, 0x1B4) != 0

    def _read(self, off, size):
        self.f.seek(off); b = self.f.read(size)
        if len(b) != size:
            raise PatchError('E11', f'{self.path.name}이(가) 중간에 잘려 있습니다.', '덤프를 다시 만들어 주세요.')
        return b

    def close(self):
        self.f.close()

    def root_files(self):
        """{name: (absolute offset, size)} of the RomFS root directory."""
        if not self.has_romfs:
            raise PatchError('E11', f'{self.path.name}에 게임 데이터(RomFS)가 없습니다.', '올바른 파일을 선택했는지 확인해 주세요.')
        ivfc = self._read(self.romfs, 0x60)
        if ivfc[:4] != b'IVFC':
            raise PatchError('E12', f'{self.path.name}의 게임 데이터를 읽을 수 없습니다(암호화 또는 손상).',
                             'GodMode9에서 복호화(Decrypt file)한 파일인지 확인해 주세요.')
        master = _u32(ivfc, 0x08); lv3_log = _u32(ivfc, 0x4C)
        lv3 = self.romfs + _align(0x60 + master, 1 << lv3_log)
        hdr = self._read(lv3, 0x28)
        dir_meta, file_meta_off, file_meta_size, data_off = _u32(hdr, 0x0C), _u32(hdr, 0x1C), _u32(hdr, 0x20), _u32(hdr, 0x24)
        root = self._read(lv3 + dir_meta, 0x18)
        fm = self._read(lv3 + file_meta_off, file_meta_size)
        out = {}; off = _u32(root, 0x0C)
        while off != 0xFFFFFFFF:
            sib = _u32(fm, off + 4); doff = _u64(fm, off + 8); dsize = _u64(fm, off + 16); nlen = _u32(fm, off + 28)
            out[fm[off + 32:off + 32 + nlen].decode('utf-16-le')] = (lv3 + data_off + doff, dsize)
            off = sib
        return out

    def exefs_file(self, name):
        hdr = self._read(self.exefs, 0x200)
        for i in range(10):
            n = hdr[i * 16:i * 16 + 8].rstrip(b'\0').decode('ascii', 'replace')
            if n == name:
                return self.exefs + 0x200 + _u32(hdr, i * 16 + 8), _u32(hdr, i * 16 + 12)
        raise PatchError('E11', f'{self.path.name}에 실행 코드({name})가 없습니다.', '업데이트 덤프가 맞는지 확인해 주세요.')

    def copy(self, off, size, dst, progress=None, label=''):
        dst = Path(dst); tmp = dst.with_suffix(dst.suffix + '.part'); done = 0
        self.f.seek(off)
        with open(tmp, 'wb') as w:
            while done < size:
                b = self.f.read(min(1 << 22, size - done))
                if not b:
                    raise PatchError('E11', f'{self.path.name}이(가) 중간에 잘려 있습니다.', '덤프를 다시 만들어 주세요.')
                w.write(b); done += len(b)
                if progress:
                    progress(label, done / size)
        os.replace(tmp, dst)

def classify(paths):
    """Open the given dumps and return (base_dump, update_dump), in whatever order they were given."""
    dumps = [Dump(p) for p in paths]
    by = {d.title_id: d for d in dumps}
    names = {BASE_TID: '한국판 카트리지(본편)', UPDATE_TID: '한국판 업데이트'}
    for d in dumps:
        if d.title_id not in names:
            for x in dumps: x.close()
            raise PatchError('E13', f'{d.path.name}은(는) 한국판 스매시브라더스 3DS가 아닙니다 (Title ID {d.title_id:016X}).',
                             '본편은 0004000000167C00, 업데이트는 0004000E00167C00이어야 합니다. 다른 지역판은 지원하지 않습니다.')
    missing = [names[t] for t in names if t not in by]
    if missing:
        for x in dumps: x.close()
        raise PatchError('E14', f'{" / ".join(missing)} 파일이 필요합니다.',
                         '본편(카트리지) 덤프와 업데이트 1.1.7 덤프를 하나씩 선택해 주세요.')
    return by[BASE_TID], by[UPDATE_TID]

def extract(base, update, dst, progress=None):
    """Copy base dt/ls, update dt/ls/cro.sarc and the update .code into dst/{base,update}/."""
    b, u = Path(dst) / 'base', Path(dst) / 'update'
    b.mkdir(parents=True, exist_ok=True); u.mkdir(parents=True, exist_ok=True)
    bf, uf = base.root_files(), update.root_files()
    for need, files, who in ((('dt', 'ls'), bf, '본편'), (('dt', 'ls', 'cro.sarc'), uf, '업데이트')):
        for n in need:
            if n not in files:
                raise PatchError('E31', f'{who} 덤프에 {n} 파일이 없습니다.', '한국판 스매시브라더스 3DS 덤프가 맞는지 확인해 주세요.')
    for n in ('ls', 'dt'):
        base.copy(*bf[n], b / n, progress, f'본편에서 {n} 꺼내기')
    for n in ('ls', 'dt', 'cro.sarc'):
        update.copy(*uf[n], u / n, progress, f'업데이트에서 {n} 꺼내기')
    off, size = update.exefs_file('.code')
    update.copy(off, size, u / 'code.exefs', progress, '업데이트 실행 코드 꺼내기')
    return b, u, u / 'code.exefs'

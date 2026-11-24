r"""One build pipeline for both the developer build and the public patch maker.

Inputs:  base RomFS dir (dt, ls), update RomFS dir (dt, ls, cro.sarc), the update ExeFS .code as stored,
         a recipe dir (our own work only: translations, font recipe, CRO byte patches, code.ips,
         expected hashes).
Output:  <out>/luma/titles/0004000000167C00/code.ips and <out>/ssb3ds-ko/{rom,rex}/...

Every failure is raised as PatchError(code, message, hint) with Korean text for the user.
Nothing in this module refers to the project folder, so the maker can run it anywhere.
"""
import base64, hashlib, json, os, shutil, struct, zlib
from pathlib import Path
from .grayimg import Image
from .archive import ls_read, resource_parse, crc, u32
from .rfpack import decode_rf, encode_rf, apply_rows
from .pack3ds import Pack3DS
from .msbt import MSBT
from .ctr_font import FGB, Tex, encode_tex

PART = 'data(kr_kr)'
RF_KEY = 'resource(kr_kr)'

class PatchError(Exception):
    def __init__(self, code, message, hint=''):
        super().__init__(f'[{code}] {message}')
        self.code, self.message, self.hint = code, message, hint

def sha256_file(path, progress=None, label=''):
    h = hashlib.sha256(); size = os.path.getsize(path); done = 0
    with open(path, 'rb') as f:
        while True:
            b = f.read(1 << 22)
            if not b:
                return h.hexdigest()
            h.update(b); done += len(b)
            if progress and size:
                progress(label, done / size)

def read_key(root, ls, key):
    if crc(key) not in ls:
        raise PatchError('E31', f'원본 데이터 안에 필요한 항목({key})이 없습니다.',
                         '한국판 1.1.7이 맞는지, 덤프가 완전한지 확인해 주세요.')
    idx, off, size = ls[crc(key)]
    with (root / 'dt').open('rb') as f:
        f.seek(off); data = f.read(size)
    if len(data) != size:
        raise PatchError('E31', 'dt 파일이 예상보다 짧습니다(덤프가 잘렸을 수 있음).', '덤프를 다시 만들어 주세요.')
    return data

def ls_write(original, changes, additions):
    """Rewrite an LS v2: changes {crc: (off, size)} in place, additions appended (index 0)."""
    b = bytearray(original)
    if b[:2] != b'of' or struct.unpack_from('<H', b, 2)[0] != 2:
        raise ValueError('only LS v2 is supported')
    n = u32(b, 4); seen = set()
    for i in range(n):
        p = 8 + i * 16; k = u32(b, p)
        if k in changes:
            off, size = changes[k]; struct.pack_into('<II', b, p + 4, off, size); seen.add(k)
    if set(changes) - seen:
        raise ValueError('changed key not in LS')
    tail = bytes(b[8 + (n - 1) * 16 + 12:8 + n * 16])
    for k, (off, size) in additions.items():
        if k in {u32(b, 8 + i * 16) for i in range(n)}:
            raise ValueError('added key already in LS')
        b += struct.pack('<III', k, off, size) + b'\0\0' + tail[2:]
    struct.pack_into('<I', b, 4, n + len(additions))
    return bytes(b)

def layer_patch(root, packs, mirror_rows=None):
    """Rebuild packs of one layer; returns (new_ls, appended, placed_rows)."""
    ls_raw = (root / 'ls').read_bytes(); ls = ls_read(root / 'ls')
    rf_raw = read_key(root, ls, RF_KEY)
    rf = decode_rf(rf_raw); rows = resource_parse(rf_raw)
    dt_size = (root / 'dt').stat().st_size
    appended = bytearray(); changes = {}
    def append(blob):
        appended.extend(bytes([0xCC]) * ((-(dt_size + len(appended))) % 0x80))
        off = dt_size + len(appended); appended.extend(blob); return off
    all_placed = []
    for prefix, edits in packs:
        key = f'{PART}/{prefix.rstrip("/")}/packed'
        pack = Pack3DS(rows, prefix, read_key(root, ls, key))
        new_pack, placed = pack.rebuild(edits)
        apply_rows(rf, placed); all_placed += placed
        changes[crc(key)] = (append(new_pack), len(new_pack))
    if mirror_rows:
        by_path = {r['path']: r for r in rows}
        apply_rows(rf, [dict(by_path[m['path']], offset=m['offset'], compressed_size=m['compressed_size'],
                             size=m['size']) for m in mirror_rows])
    new_rf = encode_rf(rf)
    changes[crc(RF_KEY)] = (append(new_rf), len(new_rf))
    return ls_write(ls_raw, changes, {}), bytes(appended), all_placed

def pack_members(root, rf_key, prefix, partition):
    ls = ls_read(root / 'ls')
    rows = resource_parse(read_key(root, ls, rf_key))
    return Pack3DS(rows, prefix, read_key(root, ls, f'{partition}/{prefix.rstrip("/")}/packed')).members()

# ---------------------------------------------------------------- messages
def message_edits(upd, recipe):
    messages = json.loads((recipe / 'messages.json').read_text(encoding='utf-8'))
    link = json.loads((recipe / 'wiiu_link.json').read_text(encoding='utf-8'))
    members = pack_members(upd, RF_KEY, 'ui/message/', PART)
    en_members = pack_members(upd, 'resource', 'ui/message/', 'data')
    raw_edits = {}
    for r in link:
        en = MSBT(en_members[f'ui/message/{r["file"]}.msbt'])
        ctrls = [bytes.fromhex(t['raw']) for t in en.tokens[en.labels[r['label']]] if t['type'] == 'control']
        parts = r['ko'].split('{ctrl}')
        if len(parts) - 1 != len(ctrls):
            raise PatchError('E41', f'Wii U 문구 {r["label"]}의 제어 태그 수가 맞지 않습니다.', '제작기 데이터 오류입니다. 제작자에게 알려 주세요.')
        raw = b''.join(p.encode('utf-16-le') + (ctrls[i] if i < len(ctrls) else b'') for i, p in enumerate(parts))
        raw_edits.setdefault(r['file'], {})[r['label']] = raw + b'\0\0'
    edits = {}
    for file in sorted(set(messages) | set(raw_edits)):
        path = f'ui/message/{file}.msbt'
        m = MSBT(members[path])
        for label, raw in raw_edits.get(file, {}).items():
            idx = m.labels[label]
            if any(t['type'] == 'control' for t in m.tokens[idx]) or any(t.get('value') for t in m.tokens[idx]):
                raise PatchError('E41', f'{label}: 한국판 원문이 예상과 다릅니다.', '한국판 1.1.7이 맞는지 확인해 주세요.')
            m.raw[idx] = raw
        repl = messages.get(file, {})
        missing = [k for k in repl if k not in m.labels]
        if missing:
            raise PatchError('E41', f'{file}: 번역할 항목이 원본에 없습니다({missing[0]} 등).', '한국판 1.1.7이 맞는지 확인해 주세요.')
        edits[path] = m.serialize(repl)
        MSBT(edits[path])
    return edits

# ---------------------------------------------------------------- fonts
def apply_font_recipe(members, recipe):
    """Apply fonts.json to the original font members; returns {path: new bytes}."""
    spec = json.loads((recipe / 'fonts.json').read_text(encoding='utf-8'))
    out = {}
    for f in spec['fonts']:
        fgb = FGB(members[f['fgb']])
        texs = [Tex(members[p]) for p in f['tex']]
        imgs = [list(t.to_image()) for t in texs]
        remove = set(f['remove']); have = {fgb.code(r) for r in fgb.records}
        if not remove <= have:
            raise PatchError('E42', f'{f["fgb"]}: 글꼴 구성이 예상과 다릅니다.', '한국판 1.1.7이 맞는지 확인해 주세요.')
        keep = []
        for r in fgb.records:
            if fgb.code(r) in remove:
                x, y = fgb.xy(r); w, h = fgb.fields(r)[6], fgb.fields(r)[7]
                p = fgb.atlas(r)
                imgs[p][0].paste(0, (x, y, x + w, y + h)); imgs[p][1].paste(0, (x, y, x + w, y + h))
            else:
                keep.append(r)
        new = []
        for a in f['add']:
            rec = bytes.fromhex(a['record']); page, x, y = struct.unpack_from('<IHH', rec, 0)
            w, h = struct.unpack_from('<hh', rec, 20)
            if struct.unpack_from('>H', rec, 24)[0] in {fgb.code(r) for r in keep}:
                raise PatchError('E42', '추가할 글자가 이미 글꼴에 있습니다.', '한국판 1.1.7이 맞는지 확인해 주세요.')
            imgs[page][1].paste(Image.frombytes('L', (w, h), base64.b64decode(a['alpha'])), (x, y))
            if a.get('lum'):
                imgs[page][0].paste(Image.frombytes('L', (w, h), base64.b64decode(a['lum'])), (x, y))
            new.append(rec)
        fgb.records = sorted(keep + new, key=fgb.code)
        out[f['fgb']] = fgb.serialize()
        for p, t in enumerate(texs):
            out[f['tex'][p]] = encode_tex(t, imgs[p][0], imgs[p][1])
    return out

# ---------------------------------------------------------------- CRO
def apply_cro(sarc, patches):
    data = bytearray(sarc)
    if data[:4] != b'SARC' or data[8:12] != b'CRC\0':
        raise PatchError('E43', 'cro.sarc 형식이 예상과 다릅니다.', '한국판 1.1.7 업데이트가 맞는지 확인해 주세요.')
    n = struct.unpack_from('<I', data, 4)[0]
    entries = [list(struct.unpack_from('<4I', data, 16 + 16 * i)) for i in range(n)]
    by_name = {}
    for i, (h, off, size, csz) in enumerate(entries):
        raw = zlib.decompress(bytes(data[off:off + csz]))
        no = struct.unpack_from('<I', raw, 0x84)[0]
        by_name[raw[no:raw.index(b'\0', no)].decode()] = (i, bytearray(raw))
    for p in patches:
        i, raw = by_name[p['module']]; off = int(p['offset'], 16)
        old, new = bytes.fromhex(p['original']), bytes.fromhex(p['new'])
        if raw[off:off + len(old)] != old:
            raise PatchError('E43', f'{p["module"]} 모듈의 바꿀 위치가 예상과 다릅니다.', '한국판 1.1.7 업데이트가 맞는지 확인해 주세요.')
        raw[off:off + len(new)] = new
    for module in sorted({p['module'] for p in patches}):
        i, raw = by_name[module]
        blob = zlib.compress(bytes(raw), 9)
        data += b'\0' * ((-len(data)) % 0x80)
        entries[i][1], entries[i][3] = len(data), len(blob)
        data += blob
        struct.pack_into('<4I', data, 16 + 16 * i, *entries[i])
    return bytes(data)

# ---------------------------------------------------------------- whole build
def copy_with_tail(src, dst, tail, progress=None, label=''):
    size = os.path.getsize(src); done = 0
    tmp = dst.with_suffix(dst.suffix + '.part')
    with open(src, 'rb') as r, open(tmp, 'wb') as w:
        while True:
            b = r.read(1 << 22)
            if not b:
                break
            w.write(b); done += len(b)
            if progress:
                progress(label, done / size)
        w.write(tail)
    os.replace(tmp, dst)

# Output names are the logical keys of expected.json ("ssb3ds-ko/...", the r1 SD layout); a layout maps them to
# other paths, e.g. the LayeredFS title folder used by the 3DS 한글패치 매니저 (KPM_LAYOUT).
SD_LAYOUT = {'ssb3ds-ko/rom/ls': 'ssb3ds-ko/rom/ls', 'ssb3ds-ko/rom/dt': 'ssb3ds-ko/rom/dt',
             'ssb3ds-ko/rex/ls': 'ssb3ds-ko/rex/ls', 'ssb3ds-ko/rex/dt': 'ssb3ds-ko/rex/dt',
             'ssb3ds-ko/rex/cro.sarc': 'ssb3ds-ko/rex/cro.sarc'}
KPM_LAYOUT = {'ssb3ds-ko/rom/ls': 'romfs/ls', 'ssb3ds-ko/rom/dt': 'romfs/dt',
              'ssb3ds-ko/rex/ls': 'romfs/_ko_update/ls', 'ssb3ds-ko/rex/dt': 'romfs/_ko_update/dt',
              'ssb3ds-ko/rex/cro.sarc': 'romfs/_ko_update/cro.sarc'}
CODE_PATCH = 'luma/titles/0004000000167C00/code.ips'

def build_sd(base, upd, code_bin, recipe, out, progress=None, verify_inputs=True, verify_outputs=True, include_loader=True,
             layout=None):
    base, upd, recipe, out = Path(base), Path(upd), Path(recipe), Path(out)
    layout = layout or SD_LAYOUT
    prog = progress or (lambda s, f=None: None)
    expected = json.loads((recipe / 'expected.json').read_text(encoding='utf-8')) if (recipe / 'expected.json').exists() else {}
    report = dict(inputs={}, outputs={})

    # 1) inputs are exactly the Korean 1.1.7 data
    inputs = {'update_code.exefs': Path(code_bin), 'update_ls': upd / 'ls', 'update_dt': upd / 'dt',
              'update_cro.sarc': upd / 'cro.sarc', 'base_ls': base / 'ls', 'base_dt': base / 'dt'}
    for name, p in inputs.items():
        if not p.exists():
            raise PatchError('E30', f'원본 파일({name})을 찾을 수 없습니다.', '덤프가 올바른지 확인해 주세요.')
        report['inputs'][name] = sha256_file(p, prog, f'원본 확인: {name}')
        want = expected.get('inputs', {}).get(name)
        if verify_inputs and want and report['inputs'][name] != want:
            hint = ('업데이트 1.1.7(v35088)이 아닌 다른 버전일 수 있습니다. 본체에서 최신 업데이트를 받은 뒤 다시 덤프해 주세요.'
                    if name.startswith('update') else '한국판 카트리지(CTR-P-AXCK)가 아니거나 덤프가 손상됐을 수 있습니다. 다시 덤프해 주세요.')
            raise PatchError('E32', f'원본 데이터({name})가 예상한 한국판 1.1.7과 다릅니다.', hint)

    # 2) base layer: fonts
    prog('글꼴에 한글 추가', 0.0)
    font_members = pack_members(base, RF_KEY, 'ui/font/', PART)
    fonts = apply_font_recipe(font_members, recipe)
    rom_ls, rom_app, mirror = layer_patch(base, [('ui/font/', fonts)])
    # 3) update layer: messages (+ mirrored font rows)
    prog('한국어 문장 넣기', 0.0)
    rex_ls, rex_app, _ = layer_patch(upd, [('ui/message/', message_edits(upd, recipe))], mirror)
    cro = apply_cro((upd / 'cro.sarc').read_bytes(), json.loads((recipe / 'cro.json').read_text(encoding='utf-8')))

    # 4) write
    data = {'ssb3ds-ko/rom/ls': rom_ls, 'ssb3ds-ko/rex/ls': rex_ls, 'ssb3ds-ko/rex/cro.sarc': cro}
    tails = {'ssb3ds-ko/rom/dt': (base / 'dt', rom_app, 'SD용 파일 쓰기 (본편, 약 770MB)'),
             'ssb3ds-ko/rex/dt': (upd / 'dt', rex_app, 'SD용 파일 쓰기 (업데이트, 약 120MB)')}
    outputs = {name: out / layout[name] for name in SD_LAYOUT}
    try:
        for p in outputs.values():
            p.parent.mkdir(parents=True, exist_ok=True)
        for name, blob in data.items():
            outputs[name].write_bytes(blob)
        for name, (src, tail, label) in tails.items():
            copy_with_tail(src, outputs[name], tail, prog, label)
        if include_loader:
            ips = out / CODE_PATCH; ips.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(recipe / 'code.ips', ips)
    except OSError as e:
        raise PatchError('E60', f'결과 파일을 쓰지 못했습니다: {e.strerror or e}',
                         '출력 폴더의 여유 공간(약 1GB)과 쓰기 권한을 확인해 주세요. 백신 프로그램이 막는 경우도 있습니다.')

    # 5) outputs match the tested build
    if include_loader:
        outputs[CODE_PATCH] = ips
    for name, p in outputs.items():
        report['outputs'][name] = sha256_file(p, prog, f'결과 확인: {name}')
        want = expected.get('outputs', {}).get(name)
        if verify_outputs and want and report['outputs'][name] != want:
            raise PatchError('E50', f'만든 파일({name})이 검증된 테스트판과 다릅니다.',
                             '입력은 맞았는데 결과가 다릅니다. 제작 기록(로그)을 제작자에게 보내 주세요.')
    return report

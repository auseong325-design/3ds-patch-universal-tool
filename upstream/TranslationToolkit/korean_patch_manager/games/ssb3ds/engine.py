"""SSB3DS Korean patch engine for the 3DS 한글패치 매니저 (KPM), recipe format "kpm-recipe-v1".

The public recipe holds only our own work (Korean text, Gothic A1 glyphs, byte patches, our ARM code, hashes).
Everything game-derived is made here, on the user's PC, from the user's own decrypted dumps:

  build_install  base cartridge + update 1.1.7  ->  <title>/code.ips + romfs/{dt,ls} + romfs/_ko_update/{dt,ls,cro.sarc}
                 (the LayeredFS layout Luma3DS and Azahar read; KPM installs it like any install ZIP)
  build_rom      base + update (+ optional DLC)  ->  one decrypted .3ds with update, patch and DLC inside (3dstool)

Code layout in the 1.1.7 code (all outside the area Luma3DS writes its own LayeredFS code to, 0xB6110C + ~0x114):
  IFile_Open hook 0x12A21C -> rex: loader at 0xB61F00 (identical bytes in code.ips and in the all-in-one ROM)
  DLC adapter 0xB61400..0xB61F00 (all-in-one with DLC only)

Pure Python except build_rom, which runs 3dstool 1.2.6. Errors are PatchError(code, message, hint) in Korean.
This file is developed in the SSB3DS project (tools/kpm_engine.py) and copied into KPM by tools.export_kpm_engine.
"""
import hashlib, json, os, shutil, struct, zipfile
from pathlib import Path, PurePosixPath

from .patchcore import PatchError, build_sd, sha256_file, KPM_LAYOUT
from .ctr_dump import Dump, extract, _u32, BASE_TID, UPDATE_TID
from .dlc_dump import DLC, DLC_TID
from .rom_container import romfs_files, extent_sha, verify_ncch
from .dlc_runtime import apply as apply_dlc
from .standalone_core import apply_ips, tool_run

FORMAT = 'kpm-recipe-v1'
ENGINE = 'ssb3ds'
ENGINE_VERSION = 1
TITLE_ID = '0004000000167C00'
UPDATE_DIR = '_ko_update'
RECIPE_FILES = ('messages.json', 'wiiu_link.json', 'fonts.json', 'cro.json', 'expected.json', 'code.ips',
                'allinone.json', 'dlc_adapter.bin')
ROLES = {BASE_TID: 'base', UPDATE_TID: 'update', DLC_TID: 'dlc'}
ROLE_NAMES = {'base': '본편(카트리지 덤프)', 'update': '업데이트 1.1.7', 'dlc': '추가 콘텐츠(DLC)'}
INSTALL_WORK_BYTES = 2_200_000_000      # extracted originals + the result, before KPM copies it
ROM_WORK_BYTES = 12 * 1024 ** 3


# ---------------------------------------------------------------- recipe

def read_manifest(zip_path):
    """The manifest of a recipe ZIP, or None when the ZIP is not a recipe for this engine."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            if 'manifest.json' not in z.namelist() or z.getinfo('manifest.json').file_size > 1 << 20:
                return None
            manifest = json.loads(z.read('manifest.json'))
    except (OSError, zipfile.BadZipFile, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(manifest, dict) or manifest.get('format') != FORMAT or manifest.get('engine') != ENGINE:
        return None
    return manifest


def open_recipe(zip_path, dest):
    """Extract recipe/<file> into dest after checking every SHA-256 listed in the manifest."""
    manifest = read_manifest(zip_path)
    if manifest is None:
        raise PatchError('K01', '이 프로그램이 읽을 수 있는 스매시브라더스 레시피가 아닙니다.', '패치를 다시 받아 주세요.')
    if int(manifest.get('engine_version', 0)) > ENGINE_VERSION:
        raise PatchError('K02', '더 새로운 통합 툴이 필요한 패치입니다.', '3DS 한글패치 매니저를 최신 버전으로 바꿔 주세요.')
    files = manifest.get('files') or {}
    if set(files) != set(RECIPE_FILES):
        raise PatchError('K03', '패치 파일 구성이 올바르지 않습니다.', '패치를 다시 받아 주세요.')
    dest = Path(dest); dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for name, want in files.items():
            if PurePosixPath(name).name != name:
                raise PatchError('K03', '패치 파일 구성이 올바르지 않습니다.', '패치를 다시 받아 주세요.')
            data = z.read('recipe/' + name)
            if hashlib.sha256(data).hexdigest() != want:
                raise PatchError('K04', '패치 파일이 손상됐습니다.', '패치를 다시 받아 주세요.')
            (dest / name).write_bytes(data)
    return manifest


# ---------------------------------------------------------------- inputs

def identify(paths):
    """Sort the user's dumps by Title ID: {'base': Path, 'update': Path, 'dlc': Path}. Raises PatchError."""
    found = {}
    for p in paths:
        d = Dump(p)
        try:
            role = ROLES.get(d.title_id)
        finally:
            d.close()
        if role is None:
            raise PatchError('E13', f'{Path(p).name}은(는) 한국판 스매시브라더스 3DS 파일이 아닙니다 (Title ID {d.title_id:016X}).',
                             '본편 0004000000167C00, 업데이트 0004000E00167C00, DLC 0004008C00167C00만 쓸 수 있습니다.')
        if role in found and Path(found[role]).resolve() != Path(p).resolve():
            raise PatchError('E15', f'{ROLE_NAMES[role]} 파일이 두 개입니다.', '하나만 남겨 주세요.')
        found[role] = Path(p).resolve()
    return found


def identify_one(path):
    """Role of one dump ('base'/'update'/'dlc'), or raises PatchError."""
    return next(iter(identify([path])))


def text_size(update_path):
    """.text size from the update's exheader (code set info), for the Luma LayeredFS placement check."""
    d = Dump(update_path)
    try:
        return _u32(d._read(d.ncch + 0x200, 0x40), 0x18)
    finally:
        d.close()


def _code(recipe):
    return json.loads((Path(recipe) / 'allinone.json').read_text(encoding='utf-8'))


# ---------------------------------------------------------------- SD / Azahar install

def build_install(recipe, base_path, update_path, title_dir, work, progress=None, log=None):
    """Write the LayeredFS title folder (code.ips + romfs/...) into title_dir. work is a scratch folder."""
    recipe, title_dir, work = Path(recipe).resolve(), Path(title_dir).resolve(), Path(work).resolve()
    prog = progress or (lambda *a: None); log = log or (lambda *a: None)
    if shutil.disk_usage(work if work.exists() else work.parent).free < INSTALL_WORK_BYTES:
        raise PatchError('E20', '작업 공간이 부족합니다.', '작업 드라이브에 2.2GB 이상 비워 주세요.')
    found = identify([base_path, update_path])
    if set(found) != {'base', 'update'}:
        raise PatchError('E14', '본편(카트리지)과 업데이트 1.1.7 파일이 하나씩 필요합니다.', '두 파일을 모두 넣어 주세요.')
    base, upd = Dump(found['base']), Dump(found['update'])
    try:
        log(f'본편: {base.path.name} / 업데이트: {upd.path.name}')
        b, u, code = extract(base, upd, work / 'original', prog)
    finally:
        base.close(); upd.close()
    report = build_sd(b, u, code, recipe, title_dir, prog, include_loader=False, layout=KPM_LAYOUT)
    shutil.copyfile(recipe / 'code.ips', title_dir / 'code.ips')
    report['outputs']['code.ips'] = sha256_file(title_dir / 'code.ips')
    report['text_size'] = text_size(found['update'])
    shutil.rmtree(work / 'original', ignore_errors=True)
    log('결과 파일 해시가 검증된 빌드와 같습니다.')
    return report


# ---------------------------------------------------------------- all-in-one ROM

def build_rom(recipe, base_path, update_path, dlc_path, target, tool, progress=None, log=None):
    """One decrypted .3ds (CCI): base RomFS + update RomFS under /_ko_update + patch (+ DLC under /_ko_dlc).
    target is the output file (must not exist). Returns the verification record."""
    recipe, target, tool = Path(recipe).resolve(), Path(target).resolve(), Path(tool).resolve()
    prog = progress or (lambda *a: None); log = log or (lambda *a: None)
    config = _code(recipe)
    if sha256_file(tool) != config['tool_sha256']:
        raise PatchError('R01', '게임 재구성 도구(3dstool)가 예상한 파일이 아닙니다.', '통합 툴을 다시 풀어 주세요.')
    if target.exists():
        raise PatchError('R02', '같은 이름의 결과 파일이 이미 있습니다.', '기존 파일을 옮기거나 다른 폴더를 골라 주세요.')
    out = target.parent; out.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(out).free < ROM_WORK_BYTES:
        raise PatchError('E20', '작업 공간이 부족합니다.', '결과를 만들 드라이브에 12GB 이상 비워 주세요.')
    found = identify([p for p in (base_path, update_path, dlc_path) if p])
    if not {'base', 'update'} <= set(found):
        raise PatchError('E14', '본편(카트리지)과 업데이트 1.1.7 파일이 하나씩 필요합니다.', '두 파일을 모두 넣어 주세요.')
    if target.resolve() in {Path(p).resolve() for p in found.values()}:
        raise PatchError('R03', '원본과 결과 경로가 같습니다.', '다른 폴더를 골라 주세요.')
    work = out / (target.name + '.작업중')
    if work.exists():
        raise PatchError('R04', '이전 작업 폴더가 남아 있습니다.', f'{work.name} 폴더를 지운 뒤 다시 시작해 주세요.')
    partial = out / (target.name + '.part')
    if partial.exists():
        raise PatchError('R04', '이전 미완성 파일이 남아 있습니다.', f'{partial.name}을(를) 지운 뒤 다시 시작해 주세요.')
    work.mkdir()
    base = upd = dlc = None
    try:
        base, upd = Dump(found['base']), Dump(found['update'])
        if base.kind != '3DS(카트리지 덤프)':
            raise PatchError('R05', '본편은 .3ds 또는 .cci 카트리지 덤프가 필요합니다.', '복호화된 한국판 본편 카트리지 덤프를 넣어 주세요.')
        inputs = {'base': sha256_file(base.path, prog, '본편 확인'), 'update': sha256_file(upd.path, prog, '업데이트 확인')}
        if 'dlc' in found:
            prog('DLC 확인', 0)
            try:
                dlc = DLC(found['dlc'])
            except ValueError as e:
                raise PatchError('D01', 'DLC 파일을 쓸 수 없습니다.', f'복호화된 한국판 DLC CIA인지 확인해 주세요. ({e})') from None
            inputs['dlc'] = dlc.sha256
            log(f'DLC: 콘텐츠 {len(dlc.contents)}개')
        head = base._read(0, 0x4000)
        partitions = [(i, _u32(head, 0x120 + i * 8) * 512, _u32(head, 0x124 + i * 8) * 512) for i in range(8)]
        partitions = [p for p in partitions if p[1] and p[2]]
        if not partitions or partitions[0][0] != 0:
            raise PatchError('R06', '카트리지 파티션 구조를 읽을 수 없습니다.', '원본을 다시 덤프해 주세요.')
        prog('게임 파일 분해', 0)
        ncsd = work / 'ncsd.header'
        argv = ['-x', '-t', 'cci', '-f', base.path, '--header', ncsd]
        for i, off, size in partitions:
            argv += [f'--partition{i}', work / f'partition{i}.ncch']
        tool_run(tool, argv, log, work)
        upd.copy(upd.ncch, _u32(upd._read(upd.ncch, 0x200), 0x104) * 512, work / 'update.ncch')
        for name, source in (('base', work / 'partition0.ncch'), ('update', work / 'update.ncch')):
            tool_run(tool, ['-x', '-t', 'cxi', '-f', source, '--header', work / (name + '.header'), '--exh', work / (name + '.exheader'),
                            '--exefs', work / (name + '.exefs'), '--romfs', work / (name + '.romfs'), '--plain', work / (name + '.plain'),
                            '--logo', work / (name + '.logo')], log, work)
        romfs = work / 'romfs'; nested = romfs / UPDATE_DIR
        prog('본편·업데이트 데이터 분해', 0)
        tool_run(tool, ['-x', '-t', 'romfs', '-f', work / 'base.romfs', '--romfs-dir', romfs], log, work)
        if nested.exists():
            raise PatchError('R07', '이미 변경된 본편 덤프입니다.', '수정하지 않은 한국판 원본 덤프를 넣어 주세요.')
        tool_run(tool, ['-x', '-t', 'romfs', '-f', work / 'update.romfs', '--romfs-dir', nested], log, work)
        exefs = work / 'exefs'
        tool_run(tool, ['-x', '-u', '-t', 'exefs', '-f', work / 'update.exefs', '--header', work / 'exefs.header', '--exefs-dir', exefs], log, work)
        raw_code = work / 'code.exefs'
        upd.copy(*upd.exefs_file('.code'), raw_code)
        prog('번역·글꼴·Wii U 메뉴 적용', 0)
        layers = work / 'patched'
        data_report = build_sd(romfs, nested, raw_code, recipe, layers, prog, include_loader=False, layout=KPM_LAYOUT)
        for rel in KPM_LAYOUT.values():
            shutil.copy2(layers / rel, work / rel)
        codefile = exefs / 'code.bin'
        code = codefile.read_bytes()
        if hashlib.sha256(code).hexdigest() != config['original_code_sha256']:
            raise PatchError('R08', '실행 코드가 한국판 1.1.7과 다릅니다.', '원본 본편과 업데이트 1.1.7을 넣어 주세요.')
        patched = apply_ips(code, (recipe / 'code.ips').read_bytes())
        if hashlib.sha256(patched).hexdigest() != config['embedded_code_sha256']:
            raise PatchError('R08', '코드 패치 검증에 실패했습니다.', '패치를 다시 받아 주세요.')
        if dlc:
            dlc.extract(romfs)
            patched = apply_dlc(patched, dlc, (recipe / 'dlc_adapter.bin').read_bytes(), config['dlc_adapter'])
        codefile.write_bytes(patched)
        header = bytearray((work / 'update.header').read_bytes()); base_header = (work / 'base.header').read_bytes()
        header[0x108:0x110] = base_header[0x108:0x110]
        header[0x118:0x120] = base_header[0x118:0x120]
        header[0x150:0x160] = base_header[0x150:0x160]
        header[0x18f] = (header[0x18f] | 4) & ~0x21
        (work / 'embedded.header').write_bytes(header)
        exheader = bytearray((work / 'update.exheader').read_bytes()); exheader[0x0d] &= ~1
        (work / 'embedded.exheader').write_bytes(exheader)
        prog('합본 게임 파일 조립', 0)
        tool_run(tool, ['-c', '-t', 'exefs', '-f', work / 'embedded.exefs', '--header', work / 'exefs.header', '--exefs-dir', exefs], log, work)
        tool_run(tool, ['-c', '-t', 'romfs', '-f', work / 'embedded.romfs', '--romfs-dir', romfs], log, work)
        argv = ['-c', '-t', 'cxi', '-f', work / 'embedded.ncch', '--header', work / 'embedded.header', '--exh', work / 'embedded.exheader',
                '--exefs', work / 'embedded.exefs', '--romfs', work / 'embedded.romfs', '--not-encrypt']
        for flag, source in (('--logo', work / 'update.logo'), ('--plain', work / 'update.plain')):
            if source.exists() and source.stat().st_size:
                argv += [flag, source]
        tool_run(tool, argv, log, work)
        argv = ['-c', '-t', 'cci', '-f', partial, '--header', ncsd, '--not-pad']
        for i, off, size in partitions:
            argv += [f'--partition{i}', work / 'embedded.ncch' if i == 0 else work / f'partition{i}.ncch']
        tool_run(tool, argv, log, work)
        prog('합본 게임 파일 검증', 0)
        verified = _verify_rom(partial, base, upd, romfs, nested, patched, prog, dlc)
        record = dict(result='PASS', format='KPM_RECIPE_ALLINONE_CCI', engine=ENGINE, title_id=TITLE_ID,
                      input_sha256=inputs, component_outputs=data_report['outputs'], output_file=target.name,
                      output_bytes=partial.stat().st_size, output_sha256=sha256_file(partial, prog, '결과 해시'),
                      dlc_embedded=bool(dlc), verification=verified)
        if dlc:
            record['dlc'] = dlc.report()
        os.replace(partial, target)
        prog('완료', 1)
        return record
    except (ValueError, struct.error) as exc:
        raise PatchError('R20', '합본 게임 파일 구조 검증에 실패했습니다.', str(exc)) from exc
    finally:
        for d in (base, upd, dlc):
            if d:
                d.close()
        if work.parent.resolve() != out.resolve() or not work.name.endswith('.작업중'):
            raise RuntimeError('unsafe cleanup path')
        shutil.rmtree(work, ignore_errors=True)
        if partial.exists():
            partial.unlink()


def _verify_rom(path, base, update, rom, rex, patched_code, progress, dlc):
    result = Dump(path)
    try:
        if result.kind != '3DS(카트리지 덤프)' or result.title_id != base.title_id:
            raise ValueError('Output identity mismatch')
        checks = verify_ncch(result)
        if extent_sha(result, *result.exefs_file('.code')) != hashlib.sha256(patched_code).hexdigest():
            raise ValueError('Embedded code mismatch')
        files = romfs_files(result); expected = {}; done = 0
        sources = [(base, '', rom, {'ls', 'dt'}), (update, UPDATE_DIR + '/', rex, {'ls', 'dt', 'cro.sarc'})]
        total = sum(len(romfs_files(d)) for d, *_ in sources) + (len(dlc.files) if dlc else 0)
        for source, prefix, patched, changes in sources:
            for name, (off, size) in romfs_files(source).items():
                rel = prefix + name
                if rel not in files:
                    raise ValueError('Missing rebuilt file ' + rel)
                if name in changes:
                    want, want_size = sha256_file(patched / name), (patched / name).stat().st_size
                else:
                    want, want_size = extent_sha(source, off, size), size
                if files[rel][1] != want_size or extent_sha(result, *files[rel]) != want:
                    raise ValueError('Rebuilt file differs: ' + rel)
                expected[rel] = want; done += 1; progress('합본 파일 확인', done / total)
        if dlc:
            for name, (off, size) in dlc.files.items():
                want = extent_sha(dlc, off, size)
                if name not in files or files[name][1] != size or extent_sha(result, *files[name]) != want:
                    raise ValueError('Embedded DLC file differs: ' + name)
                expected[name] = want; done += 1; progress('합본 DLC 확인', done / total)
        if set(files) != set(expected):
            raise ValueError('Unexpected rebuilt files')
        return dict(file_count=len(files), ncch_hashes=checks, code_sha256=checks['exefs/.code'])
    finally:
        result.close()

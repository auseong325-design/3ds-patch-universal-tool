"""LayeredFS authoring helpers for the Translation Toolkit fork."""
from __future__ import annotations

import ftplib
import hashlib
import json
import os
import stat
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from . import metadata, runtime


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def zip_sha256(archive, name):
    digest = hashlib.sha256()
    with archive.open(name) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def title_id(value):
    value = value.strip().upper()
    if not re.fullmatch(r'[0-9A-F]{16}', value):
        raise ValueError('Title ID는 16자리 16진수여야 합니다.')
    return value


def is_reparse(path):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def reject_reparse(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if is_reparse(part): raise ValueError(f'심볼릭 링크 또는 정션 경로는 지원하지 않습니다: {part}')


def files(root):
    root = Path(root)
    reject_reparse(root)
    if not root.is_dir():
        raise ValueError(f'폴더를 찾을 수 없습니다: {root}')
    result = {}
    for path in root.rglob('*'):
        if is_reparse(path):
            raise ValueError(f'심볼릭 링크 또는 정션은 지원하지 않습니다: {path}')
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            if any(ord(c) < 32 or c in '\\:' for c in rel):
                raise ValueError(f'지원하지 않는 파일 이름: {rel!r}')
            result[rel] = path
    return result


def compare(original, edited):
    reject_reparse(original); reject_reparse(edited)
    a, b = Path(original).resolve(), Path(edited).resolve()
    if a == b or a in b.parents or b in a.parents:
        raise ValueError('원본과 수정 RomFS 폴더는 서로 겹치지 않아야 합니다.')
    old, new = files(a), files(b)
    changed = []
    for rel, path in sorted(new.items()):
        new_hash = sha256(path)
        old_hash = sha256(old[rel]) if rel in old else None
        if old_hash != new_hash:
            changed.append({'path': rel, 'original_sha256': old_hash, 'modified_sha256': new_hash})
    removed = sorted(set(old) - set(new))
    return changed, removed


def ips_ranges(path, code_size=None):
    data = Path(path).read_bytes()
    if not data.startswith(b'PATCH'):
        raise ValueError('IPS 헤더가 올바르지 않습니다.')
    pos, spans = 5, []
    while True:
        if pos + 3 > len(data):
            raise ValueError('IPS EOF가 없습니다.')
        if data[pos:pos+3] == b'EOF':
            pos += 3
            if pos != len(data):
                raise ValueError('IPS EOF 뒤의 데이터는 지원하지 않습니다.')
            break
        start = int.from_bytes(data[pos:pos+3], 'big'); pos += 3
        if pos + 2 > len(data): raise ValueError('IPS 레코드가 잘렸습니다.')
        size = int.from_bytes(data[pos:pos+2], 'big'); pos += 2
        if not size:
            if pos + 3 > len(data): raise ValueError('IPS RLE 레코드가 잘렸습니다.')
            size = int.from_bytes(data[pos:pos+2], 'big'); pos += 3
            if not size: raise ValueError('빈 IPS RLE 레코드입니다.')
        else:
            if pos + size > len(data): raise ValueError('IPS 데이터가 잘렸습니다.')
            pos += size
        if code_size is not None and start + size > code_size:
            raise ValueError('IPS 레코드가 원본 코드의 범위를 벗어납니다.')
        spans.append((start, start + size))
    spans.sort()
    if any(b[0] < a[1] for a, b in zip(spans, spans[1:])):
        raise ValueError('IPS 내부에 겹치는 레코드가 있습니다. 충돌을 확인하세요.')
    return spans


def validate_ips(path, code_size=None):
    return len(ips_ranges(path, code_size))


def ips_conflicts(paths, code_size=None):
    if len(paths) < 2: raise ValueError('비교할 IPS 파일을 2개 이상 선택하세요.')
    parsed = [(str(path), ips_ranges(path, code_size)) for path in paths]
    conflicts = []
    for index, (name_a, ranges_a) in enumerate(parsed):
        for name_b, ranges_b in parsed[index + 1:]:
            i = j = 0
            while i < len(ranges_a) and j < len(ranges_b):
                a, b = ranges_a[i], ranges_b[j]
                start, end = max(a[0], b[0]), min(a[1], b[1])
                if start < end: conflicts.append((name_a, name_b, start, end))
                if a[1] <= b[1]: i += 1
                else: j += 1
    return conflicts


def inspect(config):
    tid = title_id(config.get('title_id', ''))
    if not str(config.get('original', '')).strip() or not str(config.get('edited', '')).strip():
        raise ValueError('원본과 수정 RomFS 폴더를 지정하세요.')
    changes, removed = compare(config['original'], config['edited'])
    ips = config.get('code_ips', '').strip()
    if ips:
        code = config.get('original_code', '').strip()
        validate_ips(ips, Path(code).stat().st_size if code else None)
    rom = config.get('original_rom', '').strip()
    if rom and not Path(rom).is_file(): raise ValueError('원본 게임 파일을 찾을 수 없습니다.')
    rom_info = None
    if rom:
        if Path(rom).suffix.lower() == '.zcci':
            with Path(rom).open('rb') as stream:
                if stream.read(4) != b'Z3DS': raise ValueError('ZCCI 헤더가 올바르지 않습니다.')
            rom_info = {'format': 'ZCCI', 'title_id': None, 'title_version': None, 'version_display': None, 'product_code': None}
        else:
            rom_info = metadata.identify(rom)
    if rom_info and rom_info['title_id'] and rom_info['title_id'] != tid:
        raise ValueError(f"입력한 Title ID와 게임 파일의 ID가 다릅니다: {tid} / {rom_info['title_id']}")
    return {'title_id': tid, 'region': config.get('region', '').strip(),
            'version': config.get('version', '').strip(), 'rom_metadata': rom_info, 'original_rom_sha256': sha256(rom) if rom else None,
            'original_code_sha256': sha256(config['original_code']) if ips and config.get('original_code', '').strip() else None,
            'code_ips_sha256': sha256(ips) if ips else None, 'changes': changes, 'removed': removed}


def build(config, output):
    report = inspect(config)
    if report['removed']:
        raise ValueError('LayeredFS는 파일 삭제를 표현하지 못합니다. 삭제 목록을 확인하세요.')
    if not report['changes'] and not report['code_ips_sha256']:
        raise ValueError('배포할 변경 파일이나 코드 패치가 없습니다.')
    output = Path(output).resolve()
    original, edited = Path(config['original']).resolve(), Path(config['edited']).resolve()
    if output in (original, edited) or original in output.parents or edited in output.parents:
        raise ValueError('출력 폴더는 입력 폴더 외부여야 합니다.')
    if output.exists() and any(output.iterdir()):
        raise ValueError('출력 폴더가 비어 있지 않습니다. 빈 폴더를 지정하세요.')
    output.mkdir(parents=True, exist_ok=True)
    tid = report['title_id']
    instruction = (f"Title ID: {tid}\n지역: {report['region'] or '미입력'}\n버전: {report['version'] or '미입력'}\n"
                   '원본 및 업데이트 버전이 일치하는지 확인하세요. 실행 검증은 별도로 필요합니다.\n'
                   'Luma3DS: SD 카드 루트에 압축을 풀고 Game Patching을 활성화하세요.\n'
                   'Azahar: 사용자 데이터 폴더에 압축을 풀고 게임을 다시 시작하세요.\n')
    # Write to temporary archives and publish only after all inputs have been verified.
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        tmp = Path(tmp)
        for kind in ('luma', 'azahar'):
            prefix = f'luma/titles/{tid}' if kind == 'luma' else f'load/mods/{tid}'
            with zipfile.ZipFile(tmp / f'{kind}.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for entry in report['changes']:
                    source = edited / entry['path']
                    if sha256(source) != entry['modified_sha256']:
                        raise ValueError(f'검사 중 파일이 변경되었습니다: {entry["path"]}')
                    archive.write(source, f'{prefix}/romfs/{entry["path"]}')
                if report['code_ips_sha256']:
                    if sha256(config['code_ips']) != report['code_ips_sha256']:
                        raise ValueError('검사 중 IPS 파일이 변경되었습니다.')
                    archive.write(config['code_ips'], f'{prefix}/' + ('code.ips' if kind == 'luma' else 'exefs/code.ips'))
                archive.writestr('설치 안내.txt', instruction)
                archive.writestr('manifest.json', json.dumps(report, ensure_ascii=False, indent=2))
            with zipfile.ZipFile(tmp / f'{kind}.zip') as archive:
                for entry in report['changes']:
                    if zip_sha256(archive, f'{prefix}/romfs/{entry["path"]}') != entry['modified_sha256']:
                        raise ValueError(f'ZIP 내용 해시 불일치: {entry["path"]}')
                if report['code_ips_sha256']:
                    code_name = f'{prefix}/' + ('code.ips' if kind == 'luma' else 'exefs/code.ips')
                    if zip_sha256(archive, code_name) != report['code_ips_sha256']: raise ValueError('ZIP IPS 해시 불일치')
        for name in ('luma.zip', 'azahar.zip'):
            shutil.move(str(tmp / name), str(output / name))
    (output / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def xdelta_bundle(config, output, executable):
    report = inspect(config)
    if report['removed']: raise ValueError('삭제된 파일은 xdelta로 표현할 수 없습니다.')
    if not report['changes'] and not report['code_ips_sha256']: raise ValueError('배포할 변경 사항이 없습니다.')
    report['format'] = 'kpm-xdelta-v2'
    output = Path(output).resolve()
    for root in (Path(config['original']).resolve(), Path(config['edited']).resolve()):
        if output == root or root in output.parents: raise ValueError('출력 파일은 입력 폴더 외부여야 합니다.')
    if output.exists(): raise ValueError('출력 파일이 이미 있습니다.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as td:
        td = Path(td)
        with zipfile.ZipFile(td / 'bundle.zip', 'w', zipfile.ZIP_DEFLATED) as z:
            for entry in report['changes']:
                rel = entry['path']; original = Path(config['original']) / rel; edited = Path(config['edited']) / rel
                if entry['original_sha256'] is None:
                    if sha256(edited) != entry['modified_sha256']: raise ValueError(f'검사 중 파일이 변경되었습니다: {rel}')
                    z.write(edited, 'added/' + rel)
                    continue
                delta, restored = td / 'file.xdelta', td / 'restored'
                for p in (delta, restored): p.unlink(missing_ok=True)
                if sha256(original) != entry['original_sha256'] or sha256(edited) != entry['modified_sha256']:
                    raise ValueError(f'검사 중 파일이 변경되었습니다: {rel}')
                subprocess.run([executable, '-e', '-s', str(original), str(edited), str(delta)], check=True, capture_output=True, creationflags=runtime.hidden_process_flags())
                subprocess.run([executable, '-d', '-s', str(original), str(delta), str(restored)], check=True, capture_output=True, creationflags=runtime.hidden_process_flags())
                if sha256(restored) != entry['modified_sha256']: raise ValueError(f'xdelta 복원 검증 실패: {rel}')
                z.write(delta, 'patches/' + rel + '.xdelta')
            if report['code_ips_sha256']:
                if sha256(config['code_ips']) != report['code_ips_sha256']: raise ValueError('검사 중 IPS 파일이 변경되었습니다.')
                z.write(config['code_ips'], 'code.ips')
            z.writestr('manifest.json', json.dumps(report, ensure_ascii=False, indent=2))
        with zipfile.ZipFile(td / 'bundle.zip') as z:
            for entry in report['changes']:
                if entry['original_sha256'] is None and zip_sha256(z, 'added/' + entry['path']) != entry['modified_sha256']:
                    raise ValueError(f'추가 파일의 ZIP 해시 불일치: {entry["path"]}')
            if report['code_ips_sha256'] and zip_sha256(z, 'code.ips') != report['code_ips_sha256']:
                raise ValueError('ZIP IPS 해시 불일치')
        shutil.move(str(td / 'bundle.zip'), str(output))
    return report


def deploy_azahar(config, user_dir):
    report = inspect(config)
    if report['removed']: raise ValueError('LayeredFS는 파일 삭제를 표현하지 못합니다.')
    reject_reparse(user_dir)
    root = Path(user_dir).resolve()
    if not root.is_dir(): raise ValueError('Azahar 사용자 폴더가 없습니다.')
    target = root / 'load' / 'mods' / report['title_id']
    for parent in (root / 'load', root / 'load' / 'mods', target):
        reject_reparse(parent)
    for source in (Path(config['original']).resolve(), Path(config['edited']).resolve()):
        if source == target or source in target.parents or target in source.parents:
            raise ValueError('설치 대상과 입력 폴더가 겹칩니다.')
    if not report['changes'] and not report['code_ips_sha256']: raise ValueError('설치할 변경 사항이 없습니다.')
    existing = files(target) if target.exists() else {}
    existing_folded = {key.casefold(): key for key in existing}
    wanted = [('romfs/' + e['path'], Path(config['edited']) / e['path'], e['modified_sha256']) for e in report['changes']]
    if report['code_ips_sha256']:
        alternatives = ('code.ips', 'code.bps', 'exefs/code.ips', 'exefs/code.bps', 'exefs/code.bin')
        for name in alternatives:
            if name not in existing_folded: continue
            current = existing[existing_folded[name]]
            if name == 'exefs/code.ips' and sha256(current) == report['code_ips_sha256']: continue
            raise ValueError('기존 실행 코드 패치가 있어 충돌할 수 있습니다. 병합을 직접 확인하세요.')
        wanted.append(('exefs/code.ips', Path(config['code_ips']), report['code_ips_sha256']))
    for rel, _, digest in wanted:
        if rel.casefold() in existing_folded and sha256(existing[existing_folded[rel.casefold()]]) != digest:
            raise ValueError(f'기존 모드 파일과 충돌합니다: {rel}')
    # Stage verified bytes first; create missing files exclusively and roll them back on error.
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as td:
        stage = Path(td)
        pending = []
        for rel, src, digest in wanted:
            if rel.casefold() in existing_folded: continue
            dest = stage / rel; dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            if sha256(dest) != digest: raise ValueError(f'복사 검증 실패: {rel}')
            pending.append((rel, dest))
        created = []
        try:
            target.mkdir(parents=True, exist_ok=True)
            for rel, staged in pending:
                dest = target / rel
                reject_reparse(dest.parent)
                dest.parent.mkdir(parents=True, exist_ok=True)
                reject_reparse(dest)
                with dest.open('xb') as out:
                    created.append(dest)
                    with staged.open('rb') as inp: shutil.copyfileobj(inp, out)
        except Exception:
            for path in reversed(created): path.unlink(missing_ok=True)
            raise
    return target


def deploy_ftp(config, host, port, user='', password=''):
    report = inspect(config)
    if report['removed']: raise ValueError('LayeredFS는 파일 삭제를 표현하지 못합니다.')
    if not report['changes'] and not report['code_ips_sha256']: raise ValueError('전송할 변경 사항이 없습니다.')
    # Refuse an existing title directory to avoid replacing an unrelated patch.
    with ftplib.FTP() as ftp:
        ftp.connect(host, int(port), timeout=15); ftp.login(user or 'anonymous', password or '')
        for directory in ('luma', 'titles'):
            try: ftp.mkd(directory)
            except ftplib.error_perm: pass
            ftp.cwd(directory)
        try:
            ftp.cwd(report['title_id'])
        except ftplib.error_perm:
            ftp.mkd(report['title_id']); ftp.cwd(report['title_id'])
        else:
            raise ValueError('실기에 해당 Title ID 폴더가 이미 있습니다. 백업 후 이동하세요.')
        def upload(src, parts, expected):
            for component in parts[:-1]:
                try: ftp.mkd(component)
                except ftplib.error_perm: pass
                ftp.cwd(component)
            if sha256(src) != expected: raise ValueError(f'검사 중 파일이 변경되었습니다: {src}')
            with Path(src).open('rb') as stream: ftp.storbinary('STOR ' + parts[-1], stream)
            for _ in parts[:-1]: ftp.cwd('..')
        for entry in report['changes']:
            upload(Path(config['edited']) / entry['path'], ['romfs'] + entry['path'].split('/'), entry['modified_sha256'])
        if report['code_ips_sha256']: upload(config['code_ips'], ['code.ips'], report['code_ips_sha256'])
    return len(report['changes'])


def apply_xdelta_bundle(bundle, original, output, executable, original_rom=None, original_code=None):
    """Apply our xdelta archive only when every source and result hash matches."""
    from pathlib import PurePosixPath
    source_root = Path(original).resolve()
    if not source_root.is_dir(): raise ValueError('원본 RomFS 폴더가 없습니다.')
    output = Path(output).resolve()
    if output == source_root or source_root in output.parents or output in source_root.parents:
        raise ValueError('출력 폴더와 원본 폴더가 겹칩니다.')
    if output.exists(): raise ValueError('출력 폴더가 이미 있습니다. 새 경로를 지정하세요.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle) as z, tempfile.TemporaryDirectory(dir=output.parent) as td:
        names = z.namelist()
        if len(names) != len(set(names)): raise ValueError('중복 ZIP 항목이 있습니다.')
        report = json.loads(z.read('manifest.json'))
        title_id(report['title_id'])
        if original_rom:
            if not report.get('original_rom_sha256'): raise ValueError('패키지에 원본 게임 해시가 없어 비교할 수 없습니다.')
            if sha256(original_rom) != report['original_rom_sha256']: raise ValueError('원본 게임 파일 해시 불일치')
        if original_code:
            if not report.get('original_code_sha256'): raise ValueError('패키지에 원본 코드 해시가 없어 비교할 수 없습니다.')
            if sha256(original_code) != report['original_code_sha256']: raise ValueError('원본 코드 파일 해시 불일치')
        changes = report['changes']
        if not isinstance(changes, list) or (not changes and not report.get('code_ips_sha256')): raise ValueError('변경 파일 목록이 비었습니다.')
        if report.get('format') not in (None, 'kpm-xdelta-v2'): raise ValueError('지원하지 않는 패키지 버전입니다.')
        stage = Path(td) / 'result'; stage.mkdir()
        seen = set()
        for entry in changes:
            rel = entry['path']
            if not isinstance(rel, str): raise ValueError('잘못된 패치 경로입니다.')
            p = PurePosixPath(rel)
            if (not rel or p.is_absolute() or any(x in ('', '.', '..') for x in rel.split('/'))
                    or any(ord(c) < 32 or c in '\\:' for c in rel) or rel.casefold() in seen):
                raise ValueError(f'잘못된 패치 경로: {rel}')
            seen.add(rel.casefold())
            source = source_root.joinpath(*p.parts)
            dest = stage / 'romfs' / Path(*p.parts); dest.parent.mkdir(parents=True, exist_ok=True)
            if entry['original_sha256'] is None:
                if source.exists(): raise ValueError(f'추가 파일이 원본에 이미 있습니다: {rel}')
                with z.open('added/' + rel) as inp, dest.open('wb') as out: shutil.copyfileobj(inp, out)
            else:
                if not source.is_file() or source.is_symlink() or source_root not in source.resolve().parents:
                    raise ValueError(f'원본 파일을 찾을 수 없거나 안전하지 않습니다: {rel}')
                if sha256(source) != entry['original_sha256']: raise ValueError(f'원본 해시 불일치: {rel}')
                patch = Path(td) / 'patch.xdelta'; patch.unlink(missing_ok=True)
                with z.open('patches/' + rel + '.xdelta') as inp, patch.open('wb') as out: shutil.copyfileobj(inp, out)
                subprocess.run([executable, '-d', '-s', str(source), str(patch), str(dest)], check=True, capture_output=True, creationflags=runtime.hidden_process_flags())
            if sha256(dest) != entry['modified_sha256']: raise ValueError(f'복원 파일 해시 불일치: {rel}')
        if report.get('code_ips_sha256'):
            code = stage / 'code.ips'
            with z.open('code.ips') as inp, code.open('wb') as out: shutil.copyfileobj(inp, out)
            if sha256(code) != report['code_ips_sha256']: raise ValueError('IPS 해시 불일치')
            validate_ips(code)
        (stage / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        stage.rename(output)
    return len(changes)


def build_romforge(config, output, components=None):
    """Create the documented RomForge 3DS LayeredFS patch layout."""
    report = inspect(config)
    components = components or {}
    if set(components) - {'code', 'exheader', 'banner'}: raise ValueError('지원하지 않는 구성 요소입니다.')
    if components and report['code_ips_sha256']: raise ValueError('통합 코드와 code.ips는 동시에 사용할 수 없습니다.')
    if report['removed']: raise ValueError('삭제된 파일은 RomForge 패키지로 표현할 수 없습니다.')
    if any(entry['original_sha256'] is None for entry in report['changes']):
        raise ValueError('새로 추가된 파일의 RomForge 리팩 지원은 검증되지 않았습니다. LayeredFS ZIP을 사용하세요.')
    if not report['changes'] and not report['code_ips_sha256'] and not components: raise ValueError('배포할 변경 사항이 없습니다.')
    suffixes = ('.xdelta', '.xdelta3', '.ips', '.ups', '.bps', '.ppf', '.aps')
    if any(entry['path'].lower().endswith(suffixes) for entry in report['changes']):
        raise ValueError('RomForge가 패치 확장자로 해석하는 원본 파일이 있습니다. 전용 xdelta 패키지를 사용하세요.')
    output = Path(output).resolve()
    for root in (Path(config['original']).resolve(), Path(config['edited']).resolve()):
        if output == root or root in output.parents: raise ValueError('출력 파일은 입력 폴더 외부여야 합니다.')
    if output.exists(): raise ValueError('출력 파일이 이미 있습니다.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as td:
        tmp = Path(td) / 'romforge.zip'
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
            for entry in report['changes']:
                source = Path(config['edited']) / entry['path']
                if sha256(source) != entry['modified_sha256']: raise ValueError(f'파일이 변경되었습니다: {entry["path"]}')
                z.write(source, 'romfs/' + entry['path'])
            if report['code_ips_sha256']:
                if sha256(config['code_ips']) != report['code_ips_sha256']: raise ValueError('IPS 파일이 변경되었습니다.')
                z.write(config['code_ips'], 'code.ips')
            for key, entry in components.items():
                name = {'code':'exefs/code.bin', 'exheader':'exheader.bin', 'banner':'exefs/banner.bin'}[key]
                if sha256(entry['path']) != entry['modified_sha256']: raise ValueError(f'구성 요소가 변경되었습니다: {key}')
                z.write(entry['path'], name)
        with zipfile.ZipFile(tmp) as z:
            for entry in report['changes']:
                with z.open('romfs/' + entry['path']) as stream:
                    digest = hashlib.sha256()
                    for part in iter(lambda: stream.read(1024 * 1024), b''): digest.update(part)
                    if digest.hexdigest() != entry['modified_sha256']: raise ValueError('ZIP 파일 검증 실패')
            if report['code_ips_sha256'] and zip_sha256(z, 'code.ips') != report['code_ips_sha256']:
                raise ValueError('ZIP IPS 해시 불일치')
            for key, entry in components.items():
                name = {'code':'exefs/code.bin', 'exheader':'exheader.bin', 'banner':'exefs/banner.bin'}[key]
                if zip_sha256(z, name) != entry['modified_sha256']: raise ValueError(f'ZIP 구성 요소 해시 불일치: {key}')
        shutil.move(str(tmp), str(output))
    return report


def restored_packages(restored, output):
    """Build installable archives from the verified result of apply_xdelta_bundle."""
    restored = Path(restored).resolve()
    output = Path(output).resolve()
    if output == restored or restored in output.parents or output in restored.parents:
        raise ValueError('출력 폴더와 복원 폴더가 겹칩니다.')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('빈 출력 폴더를 지정하세요.')
    report = json.loads((restored / 'manifest.json').read_text(encoding='utf-8'))
    tid = title_id(report['title_id'])
    expected = {e['path']: e['modified_sha256'] for e in report['changes']}
    romfs = restored / 'romfs'
    actual = files(romfs) if romfs.exists() else {}
    if set(actual) != set(expected): raise ValueError('복원된 파일 목록이 매니페스트와 다릅니다.')
    for rel, source in actual.items():
        if sha256(source) != expected[rel]: raise ValueError(f'복원된 파일 해시 불일치: {rel}')
    ips = restored / 'code.ips'
    if report.get('code_ips_sha256'):
        if not ips.is_file() or sha256(ips) != report['code_ips_sha256']: raise ValueError('복원된 IPS 해시 불일치')
        validate_ips(ips)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as td:
        td = Path(td)
        for kind in ('luma', 'azahar'):
            prefix = f'luma/titles/{tid}' if kind == 'luma' else f'load/mods/{tid}'
            with zipfile.ZipFile(td / f'{kind}.zip', 'w', zipfile.ZIP_DEFLATED) as z:
                for rel, source in actual.items(): z.write(source, f'{prefix}/romfs/{rel}')
                if report.get('code_ips_sha256'):
                    z.write(ips, prefix + ('/code.ips' if kind == 'luma' else '/exefs/code.ips'))
                z.writestr('manifest.json', json.dumps(report, ensure_ascii=False, indent=2))
                z.writestr('설치 안내.txt', f'Title ID: {tid}\n지역: {report.get("region") or "미입력"}\n버전: {report.get("version") or "미입력"}\nLuma: SD 루트에 압축 해제 후 Game Patching 활성화.\nAzahar: 사용자 데이터 폴더에 압축 해제.\n실제 실행 여부는 직접 확인하세요.\n')
        for kind in ('luma', 'azahar'): shutil.move(str(td / f'{kind}.zip'), str(output / f'{kind}.zip'))
    return report

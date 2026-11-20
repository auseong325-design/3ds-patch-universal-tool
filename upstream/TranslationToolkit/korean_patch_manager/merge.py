"""Create and apply base-relative update + translation merge packages."""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from . import core, metadata, romforge, runtime

FORMAT = 'kpm-merge-v1'


def _workspace(path):
    root = Path(path).resolve()
    project = json.loads((root / 'project.json').read_text(encoding='utf-8'))
    core.title_id(project['title_id'])
    original = Path(project['original'])
    if not original.is_dir(): raise ValueError(f'원본 RomFS 폴더가 없습니다: {original}')
    for name in ('original_code.bin', 'original_exheader.bin'):
        if not (root / name).is_file(): raise ValueError(f'작업 폴더에 {name}이 없습니다. 최신 브리지로 작업 폴더를 다시 준비하세요.')
    baseline_path = root / 'workspace-manifest.json'
    if not baseline_path.is_file(): raise ValueError('작업 폴더의 원본 해시 목록이 없습니다. 최신 버전으로 작업 폴더를 다시 준비하세요.')
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    if baseline.get('format') != 'kpm-workspace-v1' or baseline.get('title_id') != project['title_id']:
        raise ValueError('작업 폴더의 원본 해시 목록이 올바르지 않습니다.')
    source_rom = project.get('original_rom', '')
    if source_rom and Path(source_rom).is_file() and core.sha256(source_rom) != baseline.get('source_sha256'):
        raise ValueError('작업 폴더를 만든 원본 게임 파일의 해시가 변경되었습니다.')
    for name in ('code','exheader','banner','icon'):
        path = root / ('original_'+name+'.bin')
        if path.is_file() and (not baseline.get('components',{}).get(name) or core.sha256(path) != baseline['components'][name]):
            raise ValueError(f'작업 폴더 원본 {name} 해시 불일치')
    return root, project, original, baseline


def merged_exheader(base, update, base_id):
    base, update = bytes(base), bytes(update)
    if len(base) != 0x800 or len(update) != 0x800: raise ValueError('확장 헤더는 0x800바이트여야 합니다.')
    if int.from_bytes(base[0x200:0x208], 'little') != int(base_id, 16) or base[0x200:0x208] != update[0x200:0x208]:
        raise ValueError('본편·업데이트 확장 헤더의 프로그램 ID가 다릅니다.')
    # Only accept the narrow class of update headers whose memory mapping and
    # capabilities are identical. Preserve the base access descriptor and jump ID.
    mutable = set(range(0x0e, 0x10)) | set(range(0x18, 0x1c)) | set(range(0x28, 0x2c)) | set(range(0x1c8, 0x1d0)) | set(range(0x400, 0x600))
    diff = [i for i, (a, b) in enumerate(zip(base, update)) if a != b and i not in mutable]
    if diff: raise ValueError(f'확장 헤더의 메모리 배치 또는 권한이 달라 자동 병합할 수 없습니다: 0x{diff[0]:X}')
    out = bytearray(base)
    for start, end in ((0x0e,0x10), (0x18,0x1c), (0x28,0x2c)): out[start:end] = update[start:end]
    # The temporary RomForge package uses an uncompressed exefs/code.bin.
    out[0x0d] &= 0xfe
    for off in (0x10, 0x20, 0x30):
        _, pages, size = struct.unpack_from('<III', out, off)
        if size > pages * 4096: raise ValueError('업데이트 코드 크기가 메모리 할당을 초과합니다.')
    return bytes(out)


def update_title_version(update_header, update_rom=None):
    """The TMD version a merged base must carry so the HOME Menu stops asking for the update.

    The HOME Menu compares the installed version with the latest version it has heard of; with the update
    merged into the base and the official update removed, only the base's TMD version is left. Prefer the
    update CIA's own TMD version. Dumps often carry 0 there, so fall back to the update's remaster version
    (exheader 0x0E, which is the major part of its title version) with the minor and micro parts maxed.
    """
    if update_rom and Path(update_rom).suffix.lower() == '.cia' and Path(update_rom).is_file():
        try:
            version = metadata.identify(update_rom)['title_version']
            if version: return version
        except (ValueError, OSError):
            pass
    remaster = struct.unpack_from('<H', bytes(update_header), 0x0E)[0]
    return (remaster << 10) | 0x3FF if remaster else 0


def _check_code_size(code, exheader):
    size = sum(struct.unpack_from('<I', exheader, off + 4)[0] for off in (0x10,0x20,0x30)) * 4096
    if len(code) != size: raise ValueError(f'코드 길이가 확장 헤더의 메모리 페이지 수와 다릅니다: {len(code)} / {size}')


def _apply_ips(code, path):
    core.validate_ips(path, len(code))
    data = Path(path).read_bytes(); out = bytearray(code); pos = 5
    while data[pos:pos+3] != b'EOF':
        offset = int.from_bytes(data[pos:pos+3], 'big'); pos += 3
        size = int.from_bytes(data[pos:pos+2], 'big'); pos += 2
        if size:
            out[offset:offset+size] = data[pos:pos+size]; pos += size
        else:
            size = int.from_bytes(data[pos:pos+2], 'big'); value = data[pos+2]; pos += 3
            out[offset:offset+size] = bytes([value]) * size
    return bytes(out)


def _delta(exe, src, target, patch, restored):
    for p in (patch, restored): p.unlink(missing_ok=True)
    subprocess.run([str(exe), '-e', '-s', str(src), str(target), str(patch)], check=True, capture_output=True, creationflags=runtime.hidden_process_flags())
    subprocess.run([str(exe), '-d', '-s', str(src), str(patch), str(restored)], check=True, capture_output=True, creationflags=runtime.hidden_process_flags())
    if core.sha256(target) != core.sha256(restored): raise ValueError(f'xdelta 복원 검증 실패: {target}')


def build_bundle(base_workspace, update_workspace, patch_romfs, output, xdelta, ips=None, banner=None, log=None, icon=None, title=None,
                 title_version=None):
    """icon is a ready SMDH file; title instead writes a Korean HOME Menu name into the base game's own icon.
    title_version is the official update's TMD version when known; otherwise it is worked out from the update."""
    if icon and title: raise ValueError('아이콘 파일과 HOME 메뉴 이름 중 하나만 지정하세요.')
    base_root, base_project, base, base_baseline = _workspace(base_workspace)
    upd_root, upd_project, update, update_baseline = _workspace(update_workspace)
    base_id, update_id = core.title_id(base_project['title_id']), core.title_id(upd_project['title_id'])
    if not (base_id.startswith('00040000') and update_id == '0004000E' + base_id[8:]):
        raise ValueError(f'본편과 업데이트 Title ID가 일치하지 않습니다: {base_id} / {update_id}')
    base_header = (base_root/'original_exheader.bin').read_bytes(); update_header = (upd_root/'original_exheader.bin').read_bytes()
    header = merged_exheader(base_header, update_header, base_id)
    base_code = (base_root/'original_code.bin').read_bytes(); update_code = (upd_root/'original_code.bin').read_bytes()
    _check_code_size(base_code, base_header); _check_code_size(update_code, update_header)
    target_code = _apply_ips(update_code, ips) if ips else update_code
    base_files, update_files = core.files(base), core.files(update)
    patch_files = core.files(patch_romfs) if patch_romfs else {}
    if set(update_files) != set(update_baseline.get('romfs', {})):
        raise ValueError('업데이트 원본 RomFS 파일 목록이 추출 당시와 다릅니다.')
    for rel, path in update_files.items():
        if update_baseline.get('romfs', {}).get(rel) != core.sha256(path):
            raise ValueError(f'업데이트 작업 폴더 원본 해시 불일치: {rel}')
    candidates = set(update_files) | set(patch_files)
    missing = sorted(candidates - set(base_files))
    if missing: raise ValueError('본편에 없는 파일은 병합할 수 없습니다: ' + ', '.join(missing[:8]))
    if any(path.lower().endswith(('.xdelta','.xdelta3','.ips','.ups','.bps','.ppf','.aps')) for path in candidates):
        raise ValueError('RomForge 패치 확장자와 겹치는 게임 파일은 지원하지 않습니다.')
    output = Path(output).resolve()
    if output.exists(): raise ValueError('출력 파일이 이미 있습니다.')
    for source in (base, update, Path(patch_romfs) if patch_romfs else None):
        if source:
            root = source.resolve()
            if output == root or root in output.parents: raise ValueError('출력은 입력 폴더 외부여야 합니다.')
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {'format':FORMAT, 'title_id':base_id, 'update_title_id':update_id,
                'base_rom_sha256':core.sha256(base_project['original_rom']) if Path(base_project.get('original_rom','')).is_file() else None,
                'update_rom_sha256':core.sha256(upd_project['original_rom']) if Path(upd_project.get('original_rom','')).is_file() else None,
                'includes_ips':bool(ips), 'changes':[], 'components':{},
                'cia_title_version':int(title_version) if title_version not in (None, '') else update_title_version(update_header, upd_project.get('original_rom')),
                'notice':'본편에 업데이트를 통합한 패치입니다. 공식 업데이트 설치 상태와 게임 실행은 별도로 확인하세요.'}
    with tempfile.TemporaryDirectory(prefix='kpm-merge-', dir=output.parent) as td:
        td=Path(td); patch=td/'part.xdelta'; restored=td/'restored'; target=td/'target'
        target.write_bytes(target_code); header_file=td/'header'; header_file.write_bytes(header)
        components={'code':(base_root/'original_code.bin',target), 'exheader':(base_root/'original_exheader.bin',header_file)}
        if banner:
            if not (base_root/'original_banner.bin').is_file(): raise ValueError('본편 작업 폴더에 원본 배너가 없습니다.')
            components['banner']=(base_root/'original_banner.bin',Path(banner))
        if icon or title:
            original_icon = base_root/'original_icon.bin'
            if not original_icon.is_file(): raise ValueError('본편 작업 폴더에 원본 아이콘이 없습니다. 최신 버전으로 작업 폴더를 다시 준비하세요.')
            new_icon = td/'icon'
            new_icon.write_bytes(metadata.smdh_with_title(original_icon.read_bytes(), title) if title else Path(icon).read_bytes())
            metadata.smdh_titles(new_icon.read_bytes())
            components['icon']=(original_icon,new_icon)
            manifest['home_title']=metadata.smdh_titles(new_icon.read_bytes())['ko'][0]
        with zipfile.ZipFile(td/'package.zip', 'w', compression=zipfile.ZIP_DEFLATED) as z:
            for rel in sorted(candidates):
                src=base_files[rel]; dst=patch_files.get(rel, update_files.get(rel))
                source_hash, target_hash=core.sha256(src), core.sha256(dst)
                if base_baseline.get('romfs', {}).get(rel) != source_hash:
                    raise ValueError(f'본편 작업 폴더 원본 해시 불일치: {rel}')
                if source_hash == target_hash: continue
                _delta(xdelta, src, dst, patch, restored)
                if core.sha256(src) != source_hash or core.sha256(dst) != target_hash: raise ValueError(f'차분 생성 중 파일이 변경되었습니다: {rel}')
                z.write(patch,'romfs/'+rel+'.xdelta')
                manifest['changes'].append({'path':rel,'original_sha256':source_hash,'modified_sha256':target_hash})
                if log: log(f'차분 생성: {rel}')
            for key,(src,dst) in components.items():
                source_hash, target_hash=core.sha256(src), core.sha256(dst)
                _delta(xdelta, src, dst, patch, restored)
                if core.sha256(src) != source_hash or core.sha256(dst) != target_hash: raise ValueError(f'차분 생성 중 구성 요소가 변경되었습니다: {key}')
                z.write(patch,'components/'+key+'.xdelta')
                manifest['components'][key]={'original_sha256':source_hash,'modified_sha256':target_hash}
            z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
            z.writestr('사용 안내.txt', manifest['notice']+'\n원본 및 결과 해시는 통합 툴에서 검사합니다.\n')
        if output.exists(): raise ValueError('출력 파일이 이미 있습니다.')
        with output.open('xb') as dst, (td/'package.zip').open('rb') as src:
            try: shutil.copyfileobj(src,dst,1024*1024)
            except Exception:
                dst.close(); output.unlink(missing_ok=True); raise
    return manifest


def _safe_rel(value):
    if not isinstance(value,str) or not value: raise ValueError('패키지 경로가 비었습니다.')
    p=PurePosixPath(value)
    if p.is_absolute() or any(part in ('','.','..') for part in value.split('/')) or any(ord(c)<32 or c in '\\:' for c in value):
        raise ValueError(f'안전하지 않은 패키지 경로: {value}')
    return Path(*p.parts)


def repack_bundle(bundle, base_workspace, key_directory, output, fmt, bridge, xdelta, cia_version='', log=None):
    """cia_version '' lets a CIA carry the merged update's version (from the package, else from its exheader)."""
    base_root, project, original, base_baseline = _workspace(base_workspace)
    with zipfile.ZipFile(bundle) as z:
        names=z.namelist()
        if len(names)!=len(set(names)): raise ValueError('ZIP에 중복된 항목이 있습니다.')
        info=z.getinfo('manifest.json')
        if info.file_size>8*1024*1024: raise ValueError('패키지 매니페스트가 너무 큽니다.')
        manifest=json.loads(z.read(info))
        if manifest.get('format')!=FORMAT: raise ValueError('지원하지 않는 통합 패키지입니다.')
        if core.title_id(project['title_id']) != core.title_id(manifest['title_id']): raise ValueError('본편 Title ID 불일치')
        components=manifest.get('components',{})
        if not {'code','exheader'} <= set(components) <= {'code','exheader','banner','icon'}: raise ValueError('구성 요소 목록이 올바르지 않습니다.')
        changes=manifest.get('changes')
        if not isinstance(changes,list): raise ValueError('RomFS 목록이 올바르지 않습니다.')
        output=Path(output).resolve()
        if output.exists(): raise ValueError('출력 파일이 이미 있습니다.')
        for source in (original.resolve(),base_root):
            if output == source or source in output.parents: raise ValueError('출력은 입력 폴더 외부여야 합니다.')
        output.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='kpm-merge-apply-',dir=output.parent) as td:
            td=Path(td); before=td/'original'; after=td/'edited'; before.mkdir(); after.mkdir(); seen=set()
            def restore(src, name, expected, dest):
                if not src.is_file() or core.sha256(src)!=expected['original_sha256']: raise ValueError(f'원본 해시 불일치: {name}')
                patch=td/'part.xdelta'; patch.unlink(missing_ok=True)
                with z.open(name) as inp, patch.open('wb') as out: shutil.copyfileobj(inp,out,1024*1024)
                dest.parent.mkdir(parents=True,exist_ok=True)
                subprocess.run([str(xdelta),'-d','-s',str(src),str(patch),str(dest)],check=True,capture_output=True,creationflags=runtime.hidden_process_flags())
                if core.sha256(dest)!=expected['modified_sha256']: raise ValueError(f'복원 결과 해시 불일치: {name}')
            for entry in changes:
                rel=_safe_rel(entry['path']); key=str(rel).replace('\\','/').casefold()
                if key in seen: raise ValueError('중복 파일 경로입니다.')
                seen.add(key); src=original/rel
                core.reject_reparse(src)
                if original.resolve() not in src.resolve().parents: raise ValueError('원본 경로가 RomFS를 벗어났습니다.')
                restore(src,'romfs/'+entry['path']+'.xdelta',entry,after/rel)
                dest=before/rel; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(src,dest)
                if core.sha256(dest)!=entry['original_sha256']: raise ValueError(f'복사 중 원본이 바뀌었습니다: {rel}')
            out_components={}
            for key,entry in components.items():
                src=base_root/('original_'+key+'.bin'); dst=td/(key+'.bin')
                restore(src,'components/'+key+'.xdelta',entry,dst)
                out_components[key]={**entry,'path':str(dst)}
            if fmt.upper() == 'CIA' and str(cia_version).strip() == '':
                version = manifest.get('cia_title_version')
                if not isinstance(version, int) or not 0 <= version <= 65535:
                    version = update_title_version((td/'exheader.bin').read_bytes())
                cia_version = str(version)
                if log: log(f'CIA 버전 번호를 업데이트에 맞춥니다: {cia_version}')
            cfg={'title_id':manifest['title_id'],'original':str(before),'edited':str(after),
                 'original_rom':project['original_rom'],'cia_version':cia_version}
            result=romforge.repack(cfg,key_directory,output,fmt,bridge,log,components=out_components)
            result['manifest']=manifest
            return result

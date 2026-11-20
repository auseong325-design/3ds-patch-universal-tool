"""Safe process adapter for the locally built RomForge bridge."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from . import core, runtime


def repack(config, key_directory, output_file, output_format, bridge_dll, log=None, expected_code_sha256=None, expected_game_sha256=None, components=None):
    game = Path(config.get('original_rom', '')).resolve()
    if not config.get('original_rom') or not game.is_file(): raise ValueError('원본 게임 파일을 선택하세요.')
    if game.suffix.lower() not in ('.3ds', '.cci', '.cia', '.zcci'):
        raise ValueError('3DS, CCI, CIA 또는 ZCCI 게임 파일을 선택하세요.')
    key_directory = Path(key_directory).resolve()
    if not key_directory.is_dir(): raise ValueError('키 폴더를 선택하세요. 복호화가 필요 없는 게임은 빈 폴더를 사용할 수 있습니다.')
    fmt = output_format.upper()
    if fmt not in ('CCI', 'CIA', 'ZCCI'): raise ValueError('지원하지 않는 출력 형식입니다.')
    cia_version = str(config.get('cia_version', '')).strip()
    if cia_version and (not cia_version.isascii() or not cia_version.isdecimal() or int(cia_version) > 65535):
        raise ValueError('CIA TMD 버전은 0부터 65535까지의 정수여야 합니다.')
    output = Path(output_file).resolve()
    if output.suffix.lower() != '.' + fmt.lower(): raise ValueError('출력 확장자와 형식이 일치하지 않습니다.')
    if output.exists(): raise ValueError('출력 파일이 이미 있습니다.')
    if output == game: raise ValueError('원본 게임 파일을 덮어쓸 수 없습니다.')
    for key in ('original', 'edited'):
        if not config.get(key): raise ValueError('원본과 수정 RomFS 폴더를 선택하세요.')
        root = Path(config[key]).resolve()
        if output == root or root in output.parents: raise ValueError('출력 파일은 RomFS 입력 폴더 외부여야 합니다.')
    bridge_dll = Path(bridge_dll).resolve()
    if not bridge_dll.is_file(): raise ValueError(f'RomForge 브리지가 빌드되지 않았습니다: {bridge_dll}')
    output.parent.mkdir(parents=True, exist_ok=True)
    # The bridge checks Title ID and per-file hashes even for compressed ZCCI input.
    project = dict(config); project['original_rom'] = ''
    with tempfile.TemporaryDirectory(prefix='kpm-repack-', dir=output.parent) as td:
        td = Path(td); patch = td / 'patch.zip'
        report = core.build_romforge(project, patch, components=components)
        source_hash = core.sha256(game)
        if expected_game_sha256 and source_hash != expected_game_sha256: raise ValueError('원본 게임 파일 해시 불일치')
        req = {'input': str(game), 'patch': str(patch), 'outputBase': str(td / 'result'),
               'keyDirectory': str(key_directory), 'format': fmt, 'titleId': report['title_id'],
               'ciaVersion': int(cia_version) if cia_version else None,
               'codeIps': bool(report['code_ips_sha256']), 'originalCodeSha256': expected_code_sha256 or report['original_code_sha256'],
               'files': [{'path': e['path'], 'originalSha256': e['original_sha256'], 'modifiedSha256': e['modified_sha256']} for e in report['changes']]}
        for key, entry in (components or {}).items():
            if key == 'code': req['originalCodeSha256'] = entry['original_sha256']
            else: req['original' + key.capitalize() + 'Sha256'] = entry['original_sha256']
            req['expected' + key.capitalize() + 'Sha256'] = entry['modified_sha256']
        proc = subprocess.Popen(runtime.bridge_command(bridge_dll), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=runtime.hidden_process_flags())
        proc.stdin.write(json.dumps(req, ensure_ascii=False).encode('utf-8')); proc.stdin.close()
        log_lines = []
        for raw in proc.stderr:
            line = raw.decode('utf-8', errors='replace').strip()
            if line.startswith('KPM_PROGRESS:'):
                if log: log('RomForge 리팩 진행: ' + line.split(':', 1)[1] + '%')
            else:
                log_lines.append(line)
                if log: log(line)
        lines = proc.stdout.read().decode('utf-8', errors='replace').splitlines()
        proc.stdout.close(); proc.stderr.close(); proc.wait()
        stderr = '\n'.join(log_lines)
        result_lines = [line[len('KPM_RESULT:'):] for line in lines if line.startswith('KPM_RESULT:')]
        if not result_lines: raise RuntimeError(f'RomForge 브리지 응답이 없습니다. 종료 코드 {proc.returncode}\n{stderr}')
        result = json.loads(result_lines[-1])
        if proc.returncode or not result.get('ok'): raise RuntimeError(f"RomForge 리팩 실패: {result.get('error', stderr)}")
        if core.sha256(game) != source_hash: raise ValueError('작업 중 원본 게임 파일이 변경되었습니다.')
        generated = Path(result['output']).resolve()
        if generated.parent != td.resolve() or not generated.is_file(): raise ValueError('브리지 출력 경로가 올바르지 않습니다.')
        expected_hash = core.sha256(generated)
        created = False
        try:
            with output.open('xb') as dst:
                created = True
                with generated.open('rb') as src: shutil.copyfileobj(src, dst, length=1024 * 1024)
            if core.sha256(output) != expected_hash: raise ValueError('최종 출력 파일 복사 검증에 실패했습니다.')
        except Exception:
            if created: output.unlink(missing_ok=True)
            raise
        return {'output': str(output), 'sha256': expected_hash, 'source_sha256': source_hash,
                'checked_files': result['checked_files'], 'code_checked': result['code_checked'], 'log': stderr}


def repack_delta(bundle, original_romfs, game_file, key_directory, output_file, output_format, bridge_dll, xdelta, log=None, cia_version=None):
    """Apply a KPM delta package directly to a game via verified RomForge repack."""
    import zipfile
    game = Path(game_file)
    if not game.is_file(): raise ValueError('원본 게임 파일을 선택하세요.')
    if not Path(original_romfs).is_dir(): raise ValueError('원본 RomFS 폴더를 선택하세요.')
    original_root = Path(original_romfs).resolve()
    output = Path(output_file).resolve()
    if output == original_root or original_root in output.parents:
        raise ValueError('출력 파일은 원본 RomFS 폴더 외부여야 합니다.')
    with zipfile.ZipFile(bundle) as z:
        info = z.getinfo('manifest.json')
        if info.file_size > 8 * 1024 * 1024: raise ValueError('매니페스트가 너무 큽니다.')
        manifest = json.loads(z.read(info))
    if any(entry.get('original_sha256') is None for entry in manifest.get('changes', [])):
        raise ValueError('새 파일을 포함한 패키지는 RomForge 리팩에서 지원하지 않습니다.')
    expected_game = manifest.get('original_rom_sha256')
    if expected_game and core.sha256(game) != expected_game: raise ValueError('원본 게임 파일 해시 불일치')
    parent = Path(output_file).resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kpm-delta-repack-', dir=parent) as td:
        td = Path(td); restored = td / 'restored'; original = td / 'original'; original.mkdir()
        core.apply_xdelta_bundle(bundle, original_romfs, restored, xdelta)
        for entry in manifest['changes']:
            rel = Path(*entry['path'].split('/'))
            dest = original / rel; dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(original_romfs) / rel, dest)
            if core.sha256(dest) != entry['original_sha256']: raise ValueError(f'원본 파일이 변경되었습니다: {entry["path"]}')
        edited = restored / 'romfs'
        edited.mkdir(exist_ok=True)
        project = {'title_id':manifest['title_id'], 'original':str(original), 'edited':str(edited),
                   'original_rom':str(game), 'code_ips':str(restored/'code.ips') if manifest.get('code_ips_sha256') else '', 'cia_version': cia_version or ''}
        return repack(project, key_directory, output_file, output_format, bridge_dll, log,
                      expected_code_sha256=manifest.get('original_code_sha256'), expected_game_sha256=expected_game)


def setup_workspace(game_file, key_directory, output_directory, bridge_dll):
    game = Path(game_file).resolve()
    if not game.is_file() or game.suffix.lower() not in ('.3ds', '.cci', '.cia', '.zcci'):
        raise ValueError('3DS, CCI, CIA 또는 ZCCI 게임 파일을 선택하세요.')
    keys = Path(key_directory).resolve()
    if not keys.is_dir(): raise ValueError('키 폴더를 선택하세요.')
    bridge = Path(bridge_dll).resolve()
    if not bridge.is_file(): raise ValueError('RomForge 브리지가 빌드되지 않았습니다.')
    output = Path(output_directory).resolve()
    if output.exists(): raise ValueError('작업 폴더가 이미 있습니다.')
    if output == game or output in game.parents: raise ValueError('작업 폴더와 게임 파일 경로가 겹칩니다.')
    output.parent.mkdir(parents=True, exist_ok=True)
    source_hash = core.sha256(game)
    with tempfile.TemporaryDirectory(prefix='kpm-extract-', dir=output.parent) as td:
        stage = Path(td) / 'workspace'; stage.mkdir()
        req = {'operation':'workspace', 'input':str(game), 'keyDirectory':str(keys), 'outputBase':str(stage)}
        proc = subprocess.run(runtime.bridge_command(bridge), input=json.dumps(req).encode('utf-8'), capture_output=True, creationflags=runtime.hidden_process_flags())
        stderr = proc.stderr.decode('utf-8',errors='replace').strip()
        lines = [line[len('KPM_RESULT:'):] for line in proc.stdout.decode('utf-8',errors='replace').splitlines() if line.startswith('KPM_RESULT:')]
        if not lines: raise RuntimeError(f'RomForge 브리지 응답이 없습니다. {stderr}')
        result = json.loads(lines[-1])
        if proc.returncode or not result.get('ok'): raise RuntimeError(f"게임 추출 실패: {result.get('error', stderr)}")
        if core.sha256(game) != source_hash: raise ValueError('추출 중 원본 게임 파일이 변경되었습니다.')
        shutil.copytree(stage/'original', stage/'edited')
        romfs_hashes = {}
        for rel, source in core.files(stage/'original').items():
            digest = core.sha256(source)
            if digest != core.sha256(stage/'edited'/rel): raise ValueError(f'작업 복사본 검증 실패: {rel}')
            romfs_hashes[rel] = digest
        baseline = {'format':'kpm-workspace-v1', 'title_id':result['title_id'], 'source_sha256':source_hash,
                    'romfs':romfs_hashes, 'components':{name:core.sha256(stage/('original_'+name+'.bin'))
                      for name in ('code','exheader','banner','icon') if (stage/('original_'+name+'.bin')).is_file()}}
        (stage/'workspace-manifest.json').write_text(json.dumps(baseline,ensure_ascii=False,indent=2),encoding='utf-8')
        project = {'title_id':result['title_id'], 'original':str(output/'original'), 'edited':str(output/'edited'),
                   'original_rom':str(game), 'original_code':str(output/'original_code.bin') if result['code'] else '',
                   'original_exheader':str(output/'original_exheader.bin') if (stage/'original_exheader.bin').is_file() else '',
                   'original_banner':str(output/'original_banner.bin') if (stage/'original_banner.bin').is_file() else '',
                   'original_icon':str(output/'original_icon.bin') if (stage/'original_icon.bin').is_file() else '',
                   'keys':str(keys), 'repack_format':'CCI', 'port':'5000'}
        (stage/'project.json').write_text(json.dumps(project,ensure_ascii=False,indent=2),encoding='utf-8')
        if output.exists(): raise ValueError('작업 폴더가 이미 있습니다.')
        stage.rename(output)
    return {'project':project, 'project_file':str(output/'project.json'), 'files':result['files'], 'source_sha256':source_hash}

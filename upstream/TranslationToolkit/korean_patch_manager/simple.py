"""Small end-to-end operations for the beginner interface."""
import json
import tempfile
import zipfile
from pathlib import Path
import shutil
from . import core, merge, metadata, romforge


def validate_cia_keys(keys, game=None):
    """certs.bin may be missing when the game itself is a CIA: its certificate chain is used instead."""
    if not keys or not Path(keys).is_dir():
        raise ValueError('키 파일(aes_keys.txt)이 든 폴더를 골라 주세요.')
    if not (Path(keys) / 'certs.bin').is_file():
        if not game or Path(game).suffix.lower() != '.cia':
            raise ValueError('키 폴더에 certs.bin이 없습니다. 인증서 준비 방법을 확인하세요.')
        metadata.cia_certificates(game)
    path = Path(keys) / 'aes_keys.txt'
    if path.is_file() and path.stat().st_size <= 1024 * 1024:
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            name, sep, value = line.strip().partition('=')
            if sep and name.strip().lower() in ('common0', 'commonkey0'):
                value = value.strip()
                if len(value) == 32 and all(c in '0123456789abcdefABCDEF' for c in value):
                    return
    raise ValueError('CIA 출력용 aes_keys.txt의 common0 키가 필요합니다. 키 준비 방법을 확인하세요.')


def package_info(path, expected):
    with zipfile.ZipFile(path) as z:
        if len(z.namelist()) != len(set(z.namelist())):
            raise ValueError('ZIP에 중복 항목이 있습니다.')
        if 'manifest.json' not in z.namelist():
            raise ValueError('이 ZIP은 전용 패키지가 아닙니다. 설치 ZIP이라면 사용 안내를 선택하세요.')
        info = z.getinfo('manifest.json')
        if info.file_size > 8 * 1024 * 1024:
            raise ValueError('패키지 정보가 너무 큽니다.')
        manifest = json.loads(z.read(info))
    if not isinstance(manifest, dict) or manifest.get('format') not in expected:
        raise ValueError('선택한 목적과 패키지 종류가 다릅니다. 통합 패키지인지 일반 xdelta인지 확인하세요.')
    return manifest


def apply_merged_game(game, package, output, fmt, keys, bridge, xdelta, log=None):
    """Prepare a temporary baseline automatically; retain only the verified result."""
    package_info(package, {'kpm-merge-v1'})
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('결과 파일이 이미 있습니다. 새 파일 이름을 선택하세요.')
    if fmt == 'CIA': validate_cia_keys(keys, game)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kpm-simple-', dir=output.parent) as td:
        temp = Path(td)
        if not keys:
            (temp / 'keys').mkdir()
        key_dir = Path(keys) if keys else temp / 'keys'
        if fmt == 'CIA' and not (key_dir / 'certs.bin').is_file():
            # Work on a private copy of the key folder so the user's folder is never written to.
            shutil.copytree(key_dir, temp / 'keys-with-certs')
            key_dir = temp / 'keys-with-certs'
            (key_dir / 'certs.bin').write_bytes(metadata.cia_certificates(game))
            if log: log('게임 CIA에 들어 있는 인증서를 사용합니다.')
        if log: log('1/3  원본 게임에서 임시 작업 폴더를 준비합니다.')
        romforge.setup_workspace(game, key_dir, temp / 'base', bridge)
        if log: log('2/3  통합 패키지 적용 및 리팩을 시작합니다.')
        # A CIA takes the merged update's version (see merge.repack_bundle); a CCI has no TMD version.
        version = '' if fmt == 'CIA' else '0'
        result = merge.repack_bundle(package, temp / 'base', key_dir, output, fmt, bridge, xdelta,
                                     cia_version=version, log=log)
        if log: log('3/3  결과 파일 해시 검사를 마쳤습니다.')
        return result


def restore_delta(original, package, parent, xdelta):
    package_info(package, {None, 'kpm-xdelta-v2'})
    parent = Path(parent).resolve()
    patch, archives = parent / 'restored_patch', parent / 'restored_packages'
    if patch.exists() or archives.exists():
        raise ValueError('복원 결과 폴더가 이미 있습니다. 다른 저장 폴더를 선택하세요.')
    count = core.apply_xdelta_bundle(package, original, patch, xdelta)
    core.restored_packages(patch, archives)
    return {'count': count, 'output': str(archives)}

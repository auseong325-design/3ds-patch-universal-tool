"""Recipe patches (manifest format "kpm-recipe-v1").

A recipe is a small public ZIP that holds no game data: text, glyphs drawn by the patch author, byte patches and
hashes. A game-specific engine (korean_patch_manager/games/<engine>/engine.py) builds the real patch on this PC from
the user's own decrypted dumps, either as a LayeredFS title folder (installed like any install ZIP) or as one
all-in-one game file (.3ds for emulators, .cia for a 3DS, the CIA made by the RomForge bridge).

Engine interface (see games/ssb3ds/engine.py):
  FORMAT, ENGINE, ENGINE_VERSION, ROLE_NAMES, read_manifest(zip), open_recipe(zip, dest), identify(paths) -> {role: Path},
  build_install(recipe, base, update, title_dir, work, progress, log) -> report (with 'text_size'),
  build_rom(recipe, base, update, dlc, target, tool, progress, log) -> record.
Engines raise exceptions with .code/.message/.hint (Korean text for the user).
"""
from __future__ import annotations

import hashlib
import importlib
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from . import core, metadata, runtime

FORMAT = 'kpm-recipe-v1'
# Luma3DS (v13.x, sysmodules/loader/source/patcher.c) applies code.ips first and then, when the title folder has a
# romfs/ folder, writes its own LayeredFS code (romfsRedirPatch, about 0x114 bytes) right at the end of .text.
# A code.ips that puts code there is overwritten and the game crashes on a real console (emulators do not do this).
LUMA_PAYLOAD = 0x200
TOOL_3DSTOOL = ('3dstool-1.2.6', '3dstool.exe')
TOOL_3DSTOOL_SHA256 = '967fd5ec6476df1fa6a01da0df5a1fea339aa488c10be218d38e07f4b8143b7e'   # 3dstool 1.2.6 (MIT)
TOOL_IGNORE_SHA256 = '6123f0d8a410d64a51f74e01eaa1e9f2ad96effd63c1996580985b83f38c875c'     # empty exclusion policy


class RecipeError(ValueError):
    """Carries the engine's Korean message and hint."""

    def __init__(self, title, body='', code=''):
        super().__init__(title + ('\n' + body if body else ''))
        self.title, self.body, self.code = title, body, code


def engine(name):
    if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9_]{1,32}', name):
        raise RecipeError('알 수 없는 패치 형식입니다', '3DS 한글패치 매니저를 최신 버전으로 바꿔 주세요.')
    try:
        return importlib.import_module(f'.games.{name}.engine', __package__)
    except ModuleNotFoundError:
        raise RecipeError('이 버전이 모르는 게임의 패치입니다', '3DS 한글패치 매니저를 최신 버전으로 바꿔 주세요.') from None


def manifest_of(source_names, read):
    """The recipe manifest when the ZIP is a recipe, else None. read(name) returns bytes."""
    if 'manifest.json' not in source_names:
        return None
    try:
        manifest = json.loads(read('manifest.json'))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(manifest, dict) or manifest.get('format') != FORMAT:
        return None
    return manifest


def _wrap(error):
    if hasattr(error, 'message') and hasattr(error, 'code'):
        return RecipeError(error.message, getattr(error, 'hint', ''), error.code)
    return error


def luma_conflicts(ips_path, text_size):
    """IPS records that write into the area Luma3DS overwrites at the end of .text when LayeredFS is active."""
    lo, hi = text_size, text_size + LUMA_PAYLOAD
    return [(start, end) for start, end in core.ips_ranges(ips_path) if start < hi and end > lo]


def tool_path(app_dir):
    return Path(app_dir).resolve() / 'tools' / TOOL_3DSTOOL[0] / TOOL_3DSTOOL[1]


def required_space(mode):
    return 2_400_000_000 if mode == 'install' else 13 * 1024 ** 3


# ---------------------------------------------------------------- building

def build_install(package_path, manifest, inputs, stage_parent, progress=None, log=None):
    """Build <stage_parent>/<TID>/ (code.ips + romfs/...) from the user's dumps. Returns (title_dir, report)."""
    eng = engine(manifest.get('engine'))
    stage_parent = Path(stage_parent)
    title_dir = stage_parent / manifest['title_id']
    recipe_dir, work = stage_parent / 'recipe', stage_parent / 'work'
    try:
        eng.open_recipe(package_path, recipe_dir)
        report = eng.build_install(recipe_dir, inputs['base'], inputs['update'], title_dir, work, progress, log)
    except Exception as error:   # engine errors carry Korean text
        raise _wrap(error) from error
    finally:
        shutil.rmtree(work, ignore_errors=True)
    code = title_dir / 'code.ips'
    if code.is_file() and (title_dir / 'romfs').is_dir():
        bad = luma_conflicts(code, report['text_size'])
        if bad:
            raise RecipeError('이 패치의 코드가 Luma와 겹칩니다',
                              '실기(Luma3DS)에서 게임이 멈출 수 있는 패치라 설치하지 않았습니다. 패치 제작자에게 알려 주세요.',
                              'L01 ' + ', '.join(f'{s:#x}-{e:#x}' for s, e in bad))
    shutil.rmtree(recipe_dir, ignore_errors=True)
    return title_dir, report


def build_rom(package_path, manifest, inputs, output, fmt, app_dir, keys=None, bridge=None, progress=None, log=None):
    """Build the all-in-one game file. fmt 'CCI' writes a decrypted .3ds; 'CIA' converts that with the RomForge bridge."""
    eng = engine(manifest.get('engine'))
    output = Path(output).resolve()
    if output.exists():
        raise RecipeError('같은 이름의 결과 파일이 이미 있습니다', '기존 파일을 옮기거나 다른 폴더를 골라 주세요.')
    tool = tool_path(app_dir)
    ignore = tool.with_name('ignore_3dstool.txt')
    if (not tool.is_file() or core.sha256(tool) != TOOL_3DSTOOL_SHA256
            or not ignore.is_file() or core.sha256(ignore) != TOOL_IGNORE_SHA256):
        raise RecipeError('게임 재구성 도구(3dstool)가 없거나 바뀌었습니다', '통합 툴 ZIP 전체를 새 폴더에 다시 풀어 주세요.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kpm-recipe-', dir=output.parent) as td:
        td = Path(td)
        cci = output if fmt == 'CCI' else td / (output.stem + '.3ds')
        try:
            eng.open_recipe(package_path, td / 'recipe')
            record = eng.build_rom(td / 'recipe', inputs['base'], inputs['update'], inputs.get('dlc'), cci, tool, progress, log)
        except Exception as error:
            raise _wrap(error) from error
        if fmt == 'CIA':
            if progress: progress('3DS용 CIA로 바꾸기', None)
            record['cia'] = to_cia(cci, output, keys, bridge, manifest['title_id'], int(manifest['requires']['update']['version']),
                                   inputs['update'], td, log)
            cci.unlink()
        (output.parent / (output.name + '.검증.json')).write_text(json.dumps(record, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        return record


def _range_sha(path, offset, size):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        stream.seek(offset)
        left = size
        while left:
            block = stream.read(min(left, 1 << 22))
            if not block:
                raise RecipeError('결과 파일이 잘렸습니다', '다시 만들어 주세요.')
            digest.update(block); left -= len(block)
    return digest.hexdigest()


def _cci_partition0(path):
    with open(path, 'rb') as stream:
        head = stream.read(0x200)
    if len(head) < 0x200 or head[0x100:0x104] != b'NCSD':
        raise RecipeError('합본 파일 형식이 올바르지 않습니다')
    return path, int.from_bytes(head[0x120:0x124], 'little') * 0x200, int.from_bytes(head[0x124:0x128], 'little') * 0x200


def _cia_content0(path):
    """(path, offset, size) of the first content; the bridge stores it unencrypted (content type 0)."""
    align = lambda v: (v + 63) & ~63
    with open(path, 'rb') as stream:
        head = stream.read(0x20)
        header, cert, ticket, tmd_size = (int.from_bytes(head[o:o + 4], 'little') for o in (0, 8, 12, 16))
        tmd_offset = align(header) + align(cert) + align(ticket)
        stream.seek(tmd_offset)
        tmd = stream.read(tmd_size)
    sig = int.from_bytes(tmd[:4], 'big')
    body = {0x10000: 0x240, 0x10001: 0x140, 0x10002: 0x80, 0x10003: 0x240, 0x10004: 0x140, 0x10005: 0x80}.get(sig)
    if body is None:
        raise RecipeError('만든 CIA의 TMD 형식을 읽을 수 없습니다')
    chunk = body + 0xC4 + 0x900
    ctype, size = int.from_bytes(tmd[chunk + 6:chunk + 8], 'big'), int.from_bytes(tmd[chunk + 8:chunk + 16], 'big')
    if ctype & 1:
        raise RecipeError('만든 CIA의 게임 내용이 암호화되어 검사할 수 없습니다')
    return path, align(header) + align(cert) + align(ticket) + align(tmd_size), size


def to_cia(cci, output, keys, bridge, title_id, version, certificate_source, temp, log=None):
    """Convert the verified .3ds into a CIA with the RomForge bridge (no content change; TMD version = update's)."""
    temp = Path(temp)
    key_dir = temp / 'keys'
    if keys:
        shutil.copytree(keys, key_dir)
    else:
        key_dir.mkdir()
    if not (key_dir / 'certs.bin').is_file():
        # the update CIA carries the same public certificate chain RomForge needs; the user's key folder is not written
        (key_dir / 'certs.bin').write_bytes(metadata.cia_certificates(certificate_source))
    patch = temp / 'empty.zip'
    with zipfile.ZipFile(patch, 'w'):
        pass
    request = {'input': str(cci), 'patch': str(patch), 'outputBase': str(temp / 'cia'), 'keyDirectory': str(key_dir),
               'format': 'CIA', 'titleId': title_id, 'ciaVersion': version, 'codeIps': False, 'files': []}
    proc = subprocess.run(runtime.bridge_command(bridge), input=json.dumps(request).encode('utf-8'), capture_output=True,
                          creationflags=runtime.hidden_process_flags())
    lines = [l[len('KPM_RESULT:'):] for l in proc.stdout.decode('utf-8', errors='replace').splitlines() if l.startswith('KPM_RESULT:')]
    result = json.loads(lines[-1]) if lines else {}
    if proc.returncode or not result.get('ok'):
        detail = result.get('error') or proc.stderr.decode('utf-8', errors='replace')[-2000:]
        raise RecipeError('CIA로 바꾸지 못했습니다', 'aes_keys.txt가 든 키 폴더가 필요합니다. 키 폴더를 확인해 주세요.', detail)
    generated = Path(result['output']).resolve()
    if generated.parent != temp.resolve() or not generated.is_file():
        raise RecipeError('CIA로 바꾸지 못했습니다', '결과 경로가 올바르지 않습니다.')
    info = metadata.identify(generated)
    if info['title_id'] != title_id or info.get('title_version') != version:
        raise RecipeError('만든 CIA 정보가 예상과 다릅니다', '', f"{info['title_id']} v{info.get('title_version')}")
    if _range_sha(*_cia_content0(generated)) != _range_sha(*_cci_partition0(cci)):
        raise RecipeError('만든 CIA의 게임 내용이 검사한 합본과 다릅니다', '결과를 저장하지 않았습니다.')
    shutil.move(str(generated), output)
    if log: log(f'CIA 완성: {output.name} (TMD v{version})')
    return {'output': str(output), 'sha256': core.sha256(output), 'title_version': version}

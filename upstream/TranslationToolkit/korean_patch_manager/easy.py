"""Engine for the one-screen beginner flow.

The user hands over what they received (a patch) and, when the patch needs it,
their own game file. Everything else is worked out here: what kind of patch it
is, whether the game matches, where the SD card or emulator folder is, what the
result should be called. Nothing in this module touches Tk.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import string
import tempfile
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from . import core, metadata, romforge, simple

GAME_SUFFIXES = ('.cia', '.cci', '.3ds', '.zcci')
INSTALL, MERGE, DELTA = 'install', 'merge', 'delta'
SD, EMULATOR = 'sd', 'emulator'
MAX_ENTRIES = 100_000

_LUMA = re.compile(r'(?:^|/)luma/titles/([0-9A-Fa-f]{16})/(.+)$')
_AZAHAR = re.compile(r'(?:^|/)load/mods/([0-9A-Fa-f]{16})/(.+)$')
_BARE = re.compile(r'^(?:[^/]+/)?([0-9A-Fa-f]{16})/((?:romfs/|exefs/).+|code\.(?:ips|bps|bin)|exheader\.bin)$')
_CODE_NAMES = ('code.ips', 'code.bps', 'code.bin')


class EasyError(ValueError):
    """A problem worded for someone who has never patched a game before."""

    def __init__(self, title, body='', topic=None, detail=''):
        super().__init__(title + ('\n' + body if body else ''))
        self.title, self.body, self.topic, self.detail = title, body, topic, detail


class NeedsConfirmation(Exception):
    """An older or different patch is already installed; the user decides whether to replace it."""

    def __init__(self, plan):
        super().__init__('replace')
        self.plan = plan


# ---------------------------------------------------------------- patch recognition

class _ZipSource:
    """The ZIP is only held open while files are being read, so the user can still move or delete it."""

    def __init__(self, path):
        self.path, self.zip = Path(path), None
        with zipfile.ZipFile(path) as archive:
            infos = [i for i in archive.infolist() if not i.is_dir()]
        if len(infos) > MAX_ENTRIES:
            raise EasyError('파일이 너무 많은 ZIP입니다', '패치 ZIP이 맞는지 확인해 주세요.')
        self.infos = {i.filename.replace('\\', '/'): i for i in infos}
        if len(self.infos) != len(infos):
            raise EasyError('ZIP에 같은 이름의 파일이 두 번 들어 있습니다', '패치를 다시 받아 주세요.')

    def names(self): return list(self.infos)
    def size(self, name): return self.infos[name].file_size
    def crc(self, name): return self.infos[name].CRC

    def open(self, name):
        if self.zip is None:
            self.zip = zipfile.ZipFile(self.path)
        return self.zip.open(self.infos[name])

    def close(self):
        if self.zip is not None:
            self.zip.close()
            self.zip = None


class _DirSource:
    """A folder the user extracted by hand; names are prefixed with the folder's own name."""

    def __init__(self, path):
        self.root = Path(path)
        self.paths = {}
        for file in self.root.rglob('*'):
            if file.is_file():
                self.paths[self.root.name + '/' + file.relative_to(self.root).as_posix()] = file
                if len(self.paths) > MAX_ENTRIES:
                    raise EasyError('파일이 너무 많은 폴더입니다', '패치 폴더(luma)를 골랐는지 확인해 주세요.')

    def names(self): return list(self.paths)
    def size(self, name): return self.paths[name].stat().st_size
    def crc(self, name): return _file_crc(self.paths[name])
    def open(self, name): return self.paths[name].open('rb')
    def close(self): pass


def _file_crc(path):
    value = 0
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value = zlib.crc32(block, value)
    return value


@dataclass
class Package:
    path: Path
    kind: str
    title_id: str
    file_count: int
    code_patch: bool = False
    banner: bool = False
    layouts: dict = field(default_factory=dict)   # install: layout -> {title_id: {relative path: source name}}
    manifest: dict | None = None
    source: object = None

    @property
    def needs_game(self):
        return self.kind in (MERGE, DELTA)

    @property
    def summary(self):
        if self.kind == INSTALL:
            parts = ['바로 설치하는 패치', f'파일 {self.file_count}개']
        elif self.kind == MERGE:
            parts = ['게임 파일에 합치는 패치', f'파일 {self.file_count}개']
        else:
            parts = ['게임 파일이 필요한 패치', f'파일 {self.file_count}개']
        if self.code_patch: parts.append('코드 패치 포함')
        if self.banner: parts.append('HOME 배너 포함')
        return '  ·  '.join(parts)

    def close(self):
        if self.source is not None:
            self.source.close()


def _safe_relative(value):
    parts = value.split('/')
    if not value or any(p in ('', '.', '..') for p in parts) or any(ord(c) < 32 or c in ':\\' for c in value):
        raise EasyError('패치 안에 안전하지 않은 파일 경로가 있습니다', '패치를 받은 곳에서 다시 내려받아 주세요.', detail=value)
    return value


def detect_package(path):
    """Work out what the user dropped. Raises EasyError with a plain explanation otherwise."""
    path = Path(path)
    if path.is_dir():
        source = _DirSource(path)
    elif not path.is_file():
        raise EasyError('파일을 찾을 수 없습니다', str(path))
    elif path.suffix.lower() in GAME_SUFFIXES:
        raise EasyError('이 파일은 게임 파일입니다', '패치 ZIP을 먼저 넣어 주세요. 게임 파일은 패치가 필요로 할 때만 넣습니다.')
    elif path.suffix.lower() in ('.7z', '.rar'):
        raise EasyError('7z·rar 파일은 바로 읽을 수 없습니다', '먼저 압축을 풀고, 풀린 폴더를 통째로 넣어 주세요.')
    elif not zipfile.is_zipfile(path):
        raise EasyError('패치 파일이 아닙니다', '받은 패치 ZIP 파일(또는 압축을 푼 폴더)을 넣어 주세요.')
    else:
        try:
            source = _ZipSource(path)
        except zipfile.BadZipFile:
            raise EasyError('ZIP 파일이 손상됐습니다', '패치를 다시 내려받아 주세요.') from None
    try:
        return _classify(path, source)
    finally:
        source.close()


def _classify(path, source):
    layouts = {'luma': {}, 'azahar': {}, 'bare': {}}
    for name in source.names():
        for layout, pattern in (('luma', _LUMA), ('azahar', _AZAHAR), ('bare', _BARE)):
            match = pattern.search(name)
            if match:
                files = layouts[layout].setdefault(match.group(1).upper(), {})
                relative = _safe_relative(match.group(2))
                if any(relative.casefold() == other.casefold() for other in files):
                    raise EasyError('패치 안에 이름이 겹치는 파일이 있습니다', '패치를 다시 받아 주세요.', detail=relative)
                files[relative] = name
                break
    layouts = {k: v for k, v in layouts.items() if v}
    if layouts:
        titles = next(iter(layouts.values()))
        files = next(iter(titles.values()))
        code = any(rel in _CODE_NAMES or rel.startswith('exefs/') for t in titles.values() for rel in t)
        count = sum(1 for t in titles.values() for rel in t if rel.startswith('romfs/'))
        return Package(path, INSTALL, sorted(titles)[0], count or len(files), code, layouts=layouts, source=source)
    manifest = None
    if isinstance(source, _ZipSource) and 'manifest.json' in source.infos and source.size('manifest.json') <= 8 * 1024 * 1024:
        try:
            with source.open('manifest.json') as stream:
                manifest = json.loads(stream.read())
        except (ValueError, UnicodeDecodeError):
            manifest = None
    if isinstance(manifest, dict) and isinstance(manifest.get('changes'), list):
        try:
            title = core.title_id(str(manifest.get('title_id', '')))
        except ValueError:
            title = None
        names = source.names()
        if title and manifest.get('format') == 'kpm-merge-v1':
            parts = manifest.get('components') or {}
            return Package(path, MERGE, title, len(manifest['changes']), bool(manifest.get('includes_ips')),
                           'banner' in parts, manifest=manifest, source=source)
        if title and manifest.get('format') in (None, 'kpm-xdelta-v2') and any(
                n.startswith(('patches/', 'added/')) or n == 'code.ips' for n in names):
            return Package(path, DELTA, title, len(manifest['changes']), bool(manifest.get('code_ips_sha256')),
                           manifest=manifest, source=source)
    raise EasyError('어떤 패치인지 알 수 없습니다',
                    'luma 폴더가 들어 있는 설치용 ZIP이나, 이 프로그램으로 만든 패치 ZIP을 넣어 주세요.\n'
                    '받은 파일이 여러 개라면 패치 안내서에서 어떤 파일을 쓰는지 확인해 주세요.', topic='package')


# ---------------------------------------------------------------- game file

@dataclass
class Game:
    path: Path
    format: str
    title_id: str | None
    size: int
    encrypted: bool | None = None


def _encrypted(path):
    """True/False when the first NCCH header is readable, otherwise None. Never raises."""
    try:
        length = Path(path).stat().st_size
        with Path(path).open('rb') as stream:
            head = stream.read(0x200)
            if head[0x100:0x104] == b'NCSD':
                offset = int.from_bytes(head[0x120:0x124], 'little') * 0x200
            elif head[0x100:0x104] == b'NCCH':
                offset = 0
            elif int.from_bytes(head[:4], 'little') == 0x2020:
                align = lambda x: (x + 63) & ~63
                sizes = [int.from_bytes(head[i:i + 4], 'little') for i in (8, 12, 16)]   # certificates, ticket, TMD
                offset = align(0x2020) + align(sizes[0]) + align(sizes[1]) + align(sizes[2])
            else:
                return None
            if offset + 0x200 > length:
                return None
            stream.seek(offset)
            ncch = stream.read(0x200)
            if ncch[0x100:0x104] != b'NCCH':
                return True      # a CIA whose content is wrapped in title-key encryption
            return not ncch[0x18F] & 0x04
    except OSError:
        return None


def check_game(path, package):
    """Confirm the file is a game this patch was made for (header check only)."""
    path = Path(path)
    if path.is_dir():
        raise EasyError('폴더가 아니라 게임 파일 하나를 넣어 주세요', '.cia · .3ds · .cci 파일입니다.', 'game')
    if not path.is_file():
        raise EasyError('파일을 찾을 수 없습니다', str(path))
    suffix = path.suffix.lower()
    if suffix in ('.zip', '.7z', '.rar'):
        raise EasyError('압축 파일은 게임 파일로 쓸 수 없습니다', '압축을 풀어 나온 .cia · .3ds · .cci 파일을 넣어 주세요.', 'game')
    if suffix not in GAME_SUFFIXES:
        raise EasyError('게임 파일이 아닙니다', '.cia · .3ds · .cci · .zcci 파일을 넣어 주세요.', 'game')
    size = path.stat().st_size
    if suffix == '.zcci':
        return Game(path, 'ZCCI', None, size)
    try:
        info = metadata.identify(path)
    except (ValueError, OSError) as error:
        raise EasyError('게임 파일을 읽을 수 없습니다', '파일이 손상됐거나 3DS 게임 파일이 아닙니다.', 'game', str(error)) from None
    title = info['title_id']
    if package is not None and title != package.title_id:
        if title[:8] == '0004000E' and title[8:] == package.title_id[8:]:
            raise EasyError('이 파일은 업데이트 파일입니다', '업데이트가 아니라 게임 본편 파일을 넣어 주세요.', 'game')
        raise EasyError('이 패치와 다른 게임입니다',
                        '지역(일본판·북미판 등)이 다른 게임일 수 있습니다. 패치 안내서에 적힌 게임을 확인해 주세요.',
                        'game', f'패치 {package.title_id} / 게임 {title}')
    return Game(path, info['format'], title, size, _encrypted(path))


# ---------------------------------------------------------------- where it goes

@dataclass
class Place:
    root: Path
    label: str
    free: int = 0
    likely: bool = False


def find_sd_cards():
    """Removable drives, the ones that look like a 3DS card first."""
    if os.name != 'nt':
        return []
    kernel = ctypes.windll.kernel32
    kernel.SetErrorMode(1)      # an empty card reader must not raise a "no disk" dialog
    mask, found = kernel.GetLogicalDrives(), []
    for index, letter in enumerate(string.ascii_uppercase):
        if not mask >> index & 1:
            continue
        root = f'{letter}:\\'
        kind = kernel.GetDriveTypeW(root)
        if kind not in (2, 3):
            continue
        try:
            marks = [os.path.isdir(root + 'Nintendo 3DS'), os.path.isdir(root + 'luma'), os.path.isfile(root + 'boot.firm')]
            if kind == 3 and not (marks[0] and (marks[1] or marks[2])):
                continue      # a fixed disk only counts when it is unmistakably a 3DS card
            free = shutil.disk_usage(root).free
        except OSError:
            continue
        name = ctypes.create_unicode_buffer(64)
        kernel.GetVolumeInformationW(root, name, 64, None, None, None, None, 0)
        found.append(Place(Path(root), f'{letter}: {name.value}'.strip(), free, any(marks)))
    return sorted(found, key=lambda p: (not p.likely, str(p.root)))


def find_emulators():
    """User-data folders of Citra-family emulators that already exist."""
    found, base = [], os.environ.get('APPDATA', '')
    for name in ('Azahar', 'Lime3DS', 'Citra'):
        root = Path(base) / name if base else None
        if root and root.is_dir():
            found.append(Place(root, name, likely=True))
    return found


def find_keys(*folders):
    """The first folder that holds aes_keys.txt, or None."""
    for folder in folders:
        if folder and (Path(folder) / 'aes_keys.txt').is_file():
            return Path(folder)
    return None


def default_output(game, fmt, folder=None):
    """A fresh file name next to the game: 'Game [한글패치].cci', numbered when taken."""
    game = Path(game)
    folder = Path(folder) if folder else game.parent
    stem, suffix = f'{game.stem} [한글패치]', '.' + fmt.lower()
    candidate, index = folder / (stem + suffix), 2
    while candidate.exists():
        candidate = folder / f'{stem} ({index}){suffix}'
        index += 1
    return candidate


def size_text(count):
    for unit, scale in (('GB', 1 << 30), ('MB', 1 << 20), ('KB', 1 << 10)):
        if count >= scale:
            return f'{count / scale:.1f}{unit}'
    return f'{count}B'


# ---------------------------------------------------------------- installing a ready-made patch

@dataclass
class InstallPlan:
    kind: str
    root: Path
    container: Path                     # .../luma/titles or .../load/mods
    titles: dict                        # title_id -> {relative path: source name}
    state: str                          # 'fresh' | 'same' | 'replace'
    total: int


def _entries_for(package, kind):
    """Choose the layout that suits the destination and translate file names where they differ."""
    order = ('luma', 'bare', 'azahar') if kind == SD else ('azahar', 'luma', 'bare')
    layout = next(name for name in order if name in package.layouts)
    titles = {}
    for title, files in package.layouts[layout].items():
        mapped = {}
        for relative, name in files.items():
            if kind == SD and layout == 'azahar' and relative.startswith('exefs/') and relative[6:] in _CODE_NAMES:
                relative = relative[6:]          # Luma reads code patches from the title folder itself
            mapped[relative] = name
        titles[title] = mapped
    return titles


def plan_install(package, kind, root):
    root = Path(root)
    if not root.is_dir():
        raise EasyError('설치할 위치를 찾을 수 없습니다',
                        'SD 카드가 빠졌거나 폴더가 없어졌습니다. 다시 골라 주세요.' if kind == SD else 'Azahar 폴더를 다시 골라 주세요.')
    container = root / 'luma' / 'titles' if kind == SD else root / 'load' / 'mods'
    titles = _entries_for(package, kind)
    try:
        for title in titles:
            core.reject_reparse(container / title)
    except ValueError as error:
        raise EasyError('이 위치에는 자동으로 설치할 수 없습니다',
                        '설치 폴더가 다른 위치로 연결된 폴더(정션·링크)입니다. 직접 복사해 주세요.', detail=str(error)) from None
    source, total, same, occupied = package.source, 0, True, False
    for title, files in titles.items():
        target = container / title
        if target.is_dir() and any(target.iterdir()):
            occupied = True
        for relative, name in files.items():
            total += source.size(name)
            existing = target.joinpath(*relative.split('/'))
            if same and not (existing.is_file() and existing.stat().st_size == source.size(name)
                             and _file_crc(existing) == source.crc(name)):
                same = False
    state = 'same' if same else 'replace' if occupied else 'fresh'
    return InstallPlan(kind, root, container, titles, state, total)


def install(package, plan, progress=None):
    """Stage every file beside the destination, then swap folders. Existing folders are moved to a backup."""
    if plan.state == 'same':
        return {'state': 'same', 'targets': [str(plan.container / t) for t in plan.titles], 'backups': []}
    if shutil.disk_usage(plan.root).free < plan.total + 16 * 1024 * 1024:
        raise EasyError('저장 공간이 부족합니다', f'{plan.root}에 {size_text(plan.total)} 이상의 빈 공간이 필요합니다.')
    source = package.source
    plan.container.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.kpm-install-', dir=plan.container))
    moved, placed, done = [], [], 0
    try:
        for title, files in plan.titles.items():
            for relative, name in files.items():
                dest = stage / title / Path(*relative.split('/'))
                dest.parent.mkdir(parents=True, exist_ok=True)
                check = 0
                with source.open(name) as src, dest.open('xb') as out:
                    for block in iter(lambda: src.read(1024 * 1024), b''):
                        out.write(block)
                        check = zlib.crc32(block, check)
                        done += len(block)
                        if progress: progress(done, plan.total)
                if check != source.crc(name) or dest.stat().st_size != source.size(name):
                    raise EasyError('복사한 파일이 원본과 다릅니다', 'SD 카드나 디스크에 문제가 있을 수 있습니다. 다시 시도해 주세요.', detail=relative)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        backup_root = plan.container.parent / (plan.container.name + '_backup')
        for title in plan.titles:
            target = plan.container / title
            if target.is_dir() and not any(target.iterdir()):
                target.rmdir()
            elif target.exists():
                backup_root.mkdir(exist_ok=True)
                backup = backup_root / f'{title}_{stamp}'
                os.replace(target, backup)
                moved.append((backup, target))
            os.replace(stage / title, target)
            placed.append(target)
    except BaseException:
        for target in placed:
            shutil.rmtree(target, ignore_errors=True)
        for backup, target in moved:
            if not target.exists():
                os.replace(backup, target)
        raise
    finally:
        source.close()
        shutil.rmtree(stage, ignore_errors=True)
    return {'state': plan.state, 'targets': [str(t) for t in placed], 'backups': [str(b) for b, _ in moved]}


def run_install(package, kind, root, allow_replace=False, progress=None):
    plan = plan_install(package, kind, root)
    if plan.state == 'replace' and not allow_replace:
        raise NeedsConfirmation(plan)
    return install(package, plan, progress)


# ---------------------------------------------------------------- patches that need the game

def _require_space(folder, game, factor):
    need = int(Path(game).stat().st_size * factor) + 256 * 1024 * 1024
    if shutil.disk_usage(folder).free < need:
        raise EasyError('저장 공간이 부족합니다', f'{Path(folder).anchor} 드라이브에 약 {size_text(need)}의 빈 공간이 필요합니다.\n'
                        '작업하는 동안만 쓰고, 끝나면 결과 파일만 남습니다.')


def run_merge(package, game, fmt, keys, bridge, xdelta, output, progress=None):
    """Build a patched game file. progress(phase, fraction or None) with phases 0..3."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    _require_space(output.parent, game, 4.2)
    if progress: progress(0, None)

    def log(line):
        if not progress: return
        if line.startswith('2/3'):
            progress(1, None)
        elif line.startswith('RomForge 리팩 진행:'):
            try:
                progress(2, min(1.0, float(line.rsplit(':', 1)[1].strip().rstrip('%')) / 100.0))
            except ValueError:
                pass
        elif line.startswith('3/3'):
            progress(3, None)
    return simple.apply_merged_game(game, package.path, output, fmt, str(keys) if keys else '', bridge, xdelta, log=log)


def run_delta(package, game, kind, root, keys, bridge, xdelta, allow_replace=False, progress=None):
    """Rebuild the patch files from the user's own game, then install them like a ready-made patch."""
    game = Path(game)
    try:
        workdir = game.parent
        probe = tempfile.TemporaryDirectory(prefix='kpm-easy-', dir=workdir)
    except OSError:
        workdir = Path(tempfile.gettempdir())
        probe = tempfile.TemporaryDirectory(prefix='kpm-easy-', dir=workdir)
    with probe as td:
        temp = Path(td)
        _require_space(workdir, game, 2.4)
        if progress: progress(0, None)
        key_dir = Path(keys) if keys else temp / 'keys'
        if not keys:
            key_dir.mkdir()
        workspace = romforge.setup_workspace(game, key_dir, temp / 'base', bridge)
        if progress: progress(1, None)
        core.apply_xdelta_bundle(package.path, workspace['project']['original'], temp / 'restored', xdelta)
        staged = temp / package.title_id
        (temp / 'restored').rename(staged)
        (staged / 'manifest.json').unlink(missing_ok=True)
        restored = detect_package(staged)
        try:
            if progress: progress(2, 0.0)
            return run_install(restored, kind, root, allow_replace,
                               (lambda done, total: progress(2, done / max(1, total))) if progress else None)
        finally:
            restored.close()


# ---------------------------------------------------------------- wording for failures and next steps

def explain(error):
    """Turn any failure into (title, body, help topic, technical detail)."""
    if isinstance(error, EasyError):
        return error.title, error.body, error.topic, error.detail
    detail = str(error)
    low = detail.lower()
    if '해시 불일치' in detail or 'hash' in low:
        return ('게임 파일이 이 패치와 맞지 않습니다',
                '지역이나 버전이 다른 게임이거나, 이미 다른 패치가 적용된 파일일 수 있습니다.\n패치 안내서에 적힌 원본 게임을 확인해 주세요.',
                'game', detail)
    if isinstance(error, PermissionError) or 'winerror 5' in low or 'winerror 32' in low:
        return ('파일을 쓸 수 없습니다', '파일이 다른 프로그램에서 열려 있거나 쓰기가 막힌 위치입니다.\n프로그램을 닫고 다시 시도해 주세요.', None, detail)
    if 'no space' in low or 'winerror 112' in low:
        return '저장 공간이 부족합니다', '빈 공간을 늘린 뒤 다시 시도해 주세요.', None, detail
    # '키' alone would also match 패키지, so only whole phrases about keys count.
    if re.search(r'(?<![a-z])key|certs\.bin|seeddb|boot9|decrypt|common0|키 폴더|키 파일|키가|키를|복호화|암호화', low):
        return ('키 파일이 필요합니다', '이 게임 파일을 읽거나 3DS용 파일을 만들려면 본인 3DS에서 꺼낸 키 파일이 필요합니다.\n준비 방법을 확인한 뒤 키 폴더를 골라 주세요.',
                'keys', detail)
    if '이미 있습니다' in detail:
        return '같은 이름의 결과 파일이 이미 있습니다', '저장 위치를 바꾸거나 기존 파일을 옮긴 뒤 다시 시도해 주세요.', None, detail
    return '작업을 끝내지 못했습니다', '넣은 파일이 맞는지 확인하고 다시 시도해 주세요.\n계속 실패하면 아래 내용을 패치 제작자에게 알려 주세요.', None, detail


def next_steps(kind, fmt=None):
    if kind == SD:
        return ['SD 카드를 「안전하게 제거」한 뒤 3DS에 꽂습니다.',
                'SELECT 버튼을 누른 채 3DS 전원을 켭니다.',
                '「Enable game patching」에 표시하고 START로 저장합니다. (한 번만 하면 됩니다)',
                '게임을 실행합니다. 업데이트 조건이 있다면 패치 안내서를 확인하세요.']
    if kind == EMULATOR:
        return ['에뮬레이터를 켜고 게임을 실행합니다. 실행 중이었다면 게임을 껐다 켭니다.',
                '패치 안내서에 「업데이트 설치」 조건이 있다면 먼저 설치해 주세요.']
    if fmt == 'CIA':
        return ['만들어진 .cia 파일을 SD 카드에 복사합니다.',
                '3DS에서 FBI를 열어 그 파일을 설치합니다.',
                '공식 업데이트가 이미 설치돼 있으면 패치가 가려질 수 있습니다. 패치 안내서를 확인하세요.']
    return ['Azahar에서 「파일 → 파일 불러오기」로 만들어진 파일을 엽니다.',
            '공식 업데이트가 따로 설치돼 있으면 패치가 가려질 수 있습니다. 패치 안내서를 확인하세요.']


# ---------------------------------------------------------------- remembered choices

def _settings_path():
    override = os.environ.get('KPM_SETTINGS')
    if override:
        return Path(override)
    base = os.environ.get('APPDATA') or str(Path.home())
    return Path(base) / 'KoreanPatchManager' / 'easy.json'


def load_settings():
    try:
        data = json.loads(_settings_path().read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        pass

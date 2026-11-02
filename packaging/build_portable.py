"""Build a Windows x64 portable folder with isolated Python and a self-contained bridge.

Run from the source checkout with 64-bit Python 3.12, .NET 10 SDK, git and xdelta.
The destination must not already exist; game files and keys are never copied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'upstream' / 'TranslationToolkit' if (ROOT / 'upstream').is_dir() else ROOT / 'TranslationToolkit'
ROMFORGE = ROOT / 'RomForge-reference' if (ROOT / 'RomForge-reference').is_dir() else ROOT / 'RomForge'
XDELTA_SHA256 = '732980d52f9220dc81817ccdd0c295eadce8a0048a24c2a771a73f791ac824e9'
BINJ_SHA256 = '8158174216fe90d5c3135894734e97d4a031e2e4'
BINJ_FILE_SHA256 = '8af74104ea9a6444fe8c394c4e8873fa069aa582d070ddbae4741d9006e6756e'
LICENSE_URLS = {
    'xdelta-LICENSE.txt': 'https://raw.githubusercontent.com/jmacd/xdelta/v3.2.2/xdelta3/LICENSE',
    '7zip-LICENSE.txt': 'https://raw.githubusercontent.com/ip7z/7zip/main/DOC/License.txt',
    '7zip-copying.txt': 'https://raw.githubusercontent.com/ip7z/7zip/main/DOC/copying.txt',
    'SevenZipSharp-LICENSE.txt': 'https://raw.githubusercontent.com/squid-box/SevenZipSharp/master/license',
    'ZstdSharp-LICENSE.txt': 'https://raw.githubusercontent.com/oleg-st/ZstdSharp/master/LICENSE',
}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def git_copy(repo, destination, allowed=None):
    if (repo / '.git').exists():
        names = subprocess.check_output(['git', '-c', 'core.quotepath=false', 'ls-files', '-z',
                                         '--cached', '--others', '--exclude-standard'], cwd=repo).split(b'\0')
        paths = [Path(raw.decode('utf-8')) for raw in names if raw]
    else:
        paths = [p.relative_to(repo) for p in repo.rglob('*') if p.is_file()]
    for rel in paths:
        if allowed is not None and rel.parts[0] not in allowed:
            continue
        if any(part in {'.git', 'bin', 'obj', '__pycache__', 'site-packages'} for part in rel.parts):
            continue
        if rel.name.lower() in {'aes_keys.txt', 'certs.bin', 'boot9.bin', 'seeddb.bin'} or rel.suffix.lower() in {'.cia', '.cci', '.3ds', '.zcci'}:
            continue
        src = repo / rel
        if src.is_file():
            dst = destination / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def ignored(_directory, names):
    return {n for n in names if n in {'site-packages', '__pycache__', 'test', 'tests', 'idlelib', 'ensurepip'} or n.endswith(('.pyc', '.pyo'))}


def write_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding='utf-8', newline='\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / '3DS-Korean-Patch-Manager-Windows-x64')
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists():
        raise SystemExit(f'출력 경로가 이미 있습니다: {out}')
    if os.name != 'nt' or struct.calcsize('P') != 8 or sys.version_info[:2] != (3, 12):
        raise SystemExit('64비트 Windows의 Python 3.12에서 빌드하세요.')
    python = Path(sys.base_prefix)
    for rel in ('python.exe', 'pythonw.exe', 'python312.dll', 'DLLs/_tkinter.pyd', 'tcl/tcl8.6', 'Lib/tkinter'):
        if not (python / rel).exists():
            raise SystemExit(f'Python 구성 요소 없음: {python / rel}')
    xdelta = APP / 'tools' / 'xdelta-3.2.2' / 'xdelta3.exe'
    if not xdelta.is_file() and (ROOT.parent / 'app/tools/xdelta-3.2.2/xdelta3.exe').is_file():
        xdelta = ROOT.parent / 'app/tools/xdelta-3.2.2/xdelta3.exe'
    if not xdelta.is_file() or digest(xdelta) != XDELTA_SHA256:
        raise SystemExit('검증된 xdelta3.exe가 필요합니다. 툴의 xdelta 설치 기능으로 준비하세요.')
    if not (APP / 'BinJEditor/JTools.py').is_file() or not (APP / 'BinJEditor/LICENSE').is_file():
        raise SystemExit('BinJEditor/JTools.py와 LICENSE가 필요합니다.')
    if digest(APP / 'BinJEditor/JTools.py') != BINJ_FILE_SHA256:
        raise SystemExit('BinJEditor JTools.py가 지정된 원본 리비전과 다릅니다.')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir()
    app = out / 'app'
    app.mkdir()
    for source in APP.glob('*.py'):
        shutil.copy2(source, app / source.name)
    for name in ('korean_patch_manager', 'BinJEditor', 'Resources'):
        shutil.copytree(APP / name, app / name, ignore=ignored)
    (app / 'tools/xdelta-3.2.2').mkdir(parents=True)
    shutil.copy2(xdelta, app / 'tools/xdelta-3.2.2/xdelta3.exe')
    shutil.copy2(APP / 'README_KO.md', app / 'README_KO.md')

    rt = out / 'runtime'
    rt.mkdir()
    for pattern in ('python.exe', 'pythonw.exe', 'python*.dll', 'vcruntime*.dll', 'LICENSE.txt'):
        for file in python.glob(pattern):
            shutil.copy2(file, rt / file.name)
    for name in ('Lib', 'DLLs', 'tcl'):
        shutil.copytree(python / name, rt / name, ignore=ignored)

    with tempfile.TemporaryDirectory(prefix='kpm-publish-', dir=out.parent) as temp:
        subprocess.run(['dotnet', 'publish', str(ROMFORGE / 'KpmBridge/KpmBridge.csproj'), '-c', 'Release',
                        '-r', 'win-x64', '--self-contained', 'true', '-p:KpmBridge=true',
                        '-p:PublishSingleFile=false', '-o', str(Path(temp) / 'bridge'), '--nologo'], check=True)
        shutil.copytree(Path(temp) / 'bridge', out / 'bridge')

    notice = out / 'licenses'
    notice.mkdir()
    for src, name in ((APP / 'LICENSE', 'TranslationToolkit-GPL-3.0.txt'),
                      (APP / 'BinJEditor/LICENSE', 'BinJEditor-GPL-3.0.txt'),
                      (ROMFORGE / 'LICENSE', 'RomForge-GPL-2.0.txt'),
                      (python / 'LICENSE.txt', 'Python-LICENSE.txt'),
                      (Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'dotnet/LICENSE.txt', 'dotnet-LICENSE.txt'),
                      (Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'dotnet/ThirdPartyNotices.txt', 'dotnet-ThirdPartyNotices.txt')):
        shutil.copy2(src, notice / name)
    for name, url in LICENSE_URLS.items():
        request = urllib.request.Request(url, headers={'User-Agent': '3DS-Patch-Manager-Build'})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(512 * 1024)
        (notice / name).write_bytes(data)
    for folder in ('tcl8.6', 'tk8.6'):
        p = python / 'tcl' / folder / 'license.terms'
        if p.is_file():
            shutil.copy2(p, notice / f'{folder}-license.terms')
    write_text(notice / '구성요소.txt',
               'Translation Toolkit 및 BinJEditor: GPL-3.0; RomForge: GPL-2.0-or-later.\n'
               f'BinJEditor JTools.py: https://github.com/Ich73/BinJEditor/tree/{BINJ_SHA256}\n'
               'xdelta 3.2.2: Apache-2.0; ZstdSharp.Port 0.8.8: MIT; SevenZipSharp.Lite 1.6.2.24: LGPL-3.0-only.\n'
               '7-Zip, Python, Tcl/Tk 및 .NET의 고지는 이 폴더의 각 파일을 참조하세요.\n'
               '수정된 프로그램 소스와 RomForge 브리지 빌드 소스는 source 폴더에 있습니다.\n')
    git_copy(APP, out / 'source/TranslationToolkit')
    shutil.copytree(APP / 'BinJEditor', out / 'source/TranslationToolkit/BinJEditor', ignore=ignored, dirs_exist_ok=True)
    git_copy(ROMFORGE, out / 'source/RomForge', {'KpmBridge', 'KpmBridgeFixture', 'Common', 'N3DS', 'Patch', 'RomForge.Core', 'LICENSE', 'README_KPM.md', 'README.md'})
    (out / 'source/packaging').mkdir(parents=True)
    shutil.copy2(Path(__file__), out / 'source/packaging/build_portable.py')
    verification = Path(__file__).with_name('verify_portable.py')
    if verification.is_file():
        shutil.copy2(verification, out / 'source/packaging/verify_portable.py')

    write_text(out / 'portable.manifest.json', json.dumps({'format': 1, 'platform': 'win-x64',
               'python': sys.version.split()[0], 'xdelta_sha256': XDELTA_SHA256,
               'binjeditor_commit': BINJ_SHA256}, indent=2))
    launcher = '@echo off\r\nset "PYTHONHOME=%~dp0runtime"\r\nset "PYTHONPATH="\r\nset "PYTHONNOUSERSITE=1"\r\nset "PYTHONDONTWRITEBYTECODE=1"\r\nstart "" "%~dp0runtime\\pythonw.exe" "%~dp0app\\KoreanPatchManager.py"\r\n'
    (out / '3DS 한글패치 매니저 실행.bat').write_bytes(launcher.encode('ascii'))
    debug = launcher.replace('start "" "%~dp0runtime\\pythonw.exe"', '"%~dp0runtime\\python.exe"') + 'pause\r\n'
    (out / '실행 문제 확인.bat').write_bytes(debug.encode('ascii'))
    write_text(out / '처음 읽어 주세요.txt',
               '3DS 한글패치 매니저 · Windows 64비트\n\n'
               '1. ZIP을 폴더에 모두 압축 해제합니다.\n'
               '2. 「3DS 한글패치 매니저 실행.bat」을 두 번 누릅니다.\n'
               '3. 처음 시작 화면에서 원하는 방법을 고르고 준비물과 안내를 확인합니다.\n\n'
               'Python, .NET, xdelta는 이 폴더에 포함되어 있습니다. 게임·패치·키·certs.bin은 포함되지 않습니다.\n'
               'CIA 출력에는 필요한 키와 certs.bin이 필요합니다. 통합 CIA의 실제 기기 실행은 별도로 확인해야 합니다.\n'
               '실행되지 않으면 「실행 문제 확인.bat」을 실행하여 오류를 확인하세요.\n'
               '폴더 전체를 함께 이동하세요. 상세 설명은 app/README_KO.md를 참조하세요.\n')
    print(f'Portable folder: {out}')


if __name__ == '__main__':
    main()

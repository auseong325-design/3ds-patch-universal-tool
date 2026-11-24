"""Exercise the portable runtime and synthetic merge with system Python/.NET hidden."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTABLE = ROOT.parent if (ROOT.parent / 'portable.manifest.json').is_file() else ROOT / 'dist' / '3DS-Korean-Patch-Manager-Windows-x64'
ROMFORGE = ROOT / 'RomForge-reference' if (ROOT / 'RomForge-reference').is_dir() else ROOT / 'RomForge'
FIXTURE = ROMFORGE / 'KpmBridgeFixture/bin/Release/net10.0/KpmBridgeFixture.dll'
DOTNET = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'dotnet/dotnet.exe'

SCRIPT = r'''
import os, pathlib, sys, tkinter
from pathlib import Path
from KoreanPatchManager import App
from korean_patch_manager import core, merge, metadata, romforge, runtime, simple
import TranslationPatcher
root = Path(os.environ['KPM_VERIFY_PORTABLE'])
scratch = Path(os.environ['KPM_VERIFY_SCRATCH'])
assert Path(sys.prefix).resolve() == (root/'runtime').resolve(), sys.prefix
bridge = runtime.bridge_path(root/'app')
assert bridge == root/'bridge/KpmBridge.exe' and bridge.is_file()
xdelta = root/'app/tools/xdelta-3.2.2/xdelta3.exe'
app = App()
errors = []
app.report_callback_exception = lambda *args: errors.append(args)
app.withdraw(); app.update()
assert app.mode.get() == '초간단 모드'
assert app.easy_panel.tools() == (bridge, xdelta)
app.mode.set('간편 모드'); app.update()
assert Path(app.vars['xdelta'].get()) == xdelta
app.scenario.set('받은 통합 패키지 적용 · 배너 포함 가능'); app.update()
assert 'RomForge' in app.checks_text.get('1.0', 'end')
assert not errors, errors
app.destroy()
fixture = scratch/'fixture'
base = scratch/'base-ws'; update = scratch/'update-ws'
romforge.setup_workspace(fixture/'game.cci', fixture/'keys', base, bridge)
romforge.setup_workspace(fixture/'update.cci', fixture/'keys', update, bridge)
patch = scratch/'patch'; patch.mkdir(); (patch/'test.bin').write_bytes(b'Korean text')
bundle = scratch/'merge.zip'
m = merge.build_bundle(base, update, patch, bundle, xdelta, ips=fixture/'code.ips', banner=fixture/'banner_ko.bin', title='마리오&루이지 RPG3 DX')
assert len(m['changes']) == 1 and {'banner', 'icon'} <= set(m['components']) and m['home_title'] == '마리오&루이지 RPG3 DX'
for fmt in ('CCI', 'CIA', 'ZCCI'):
    output = scratch/('result.'+fmt.lower())
    result = merge.repack_bundle(bundle, base, fixture/'keys', output, fmt, bridge, xdelta, cia_version='0')
    assert result['checked_files'] == 1 and result['code_checked'] and output.is_file()
assert metadata.identify(scratch/'result.cci')['title_id'] == m['title_id']
result = simple.apply_merged_game(fixture/'game.cci', bundle, scratch/'simple.cci', 'CCI', '', bridge, xdelta)
assert result['code_checked'] and (scratch/'simple.cci').is_file()
from korean_patch_manager import recipe
from korean_patch_manager.games.ssb3ds import engine as ssb3ds
tool = recipe.tool_path(root/'app')
assert tool.is_file() and core.sha256(tool) == recipe.TOOL_3DSTOOL_SHA256
assert core.sha256(tool.with_name('ignore_3dstool.txt')) == recipe.TOOL_IGNORE_SHA256
assert ssb3ds.FORMAT == recipe.FORMAT and recipe.engine('ssb3ds') is ssb3ds
assert 'PIL' not in sys.modules and 'numpy' not in sys.modules
import subprocess as _sp
assert b'3dstool' in _sp.run([str(tool)], capture_output=True, creationflags=runtime.hidden_process_flags()).stdout.lower()
print('OK: bundled Python/Tk, legacy import, xdelta, standalone bridge, merge CCI/CIA/ZCCI, recipe engine + 3dstool')
'''


def main():
    portable = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else PORTABLE
    if not portable.is_dir() or not FIXTURE.is_file():
        raise SystemExit('휴대용 배포 폴더 또는 합성 시험 도구가 없습니다. KpmBridgeFixture를 Release로 빌드하세요.')
    with tempfile.TemporaryDirectory(prefix='kpm-portable-test-') as td:
        scratch = Path(td)
        subprocess.run([str(DOTNET), str(FIXTURE), str(scratch/'fixture')], check=True, capture_output=True)
        env = os.environ.copy()
        env.update({
            'PATH': str(Path(os.environ['SystemRoot']) / 'System32'),
            'PYTHONHOME': str(portable / 'runtime'),
            'PYTHONPATH': str(portable / 'app'),
            'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
            'DOTNET_ROOT': str(scratch/'missing-dotnet'), 'DOTNET_MULTILEVEL_LOOKUP': '0',
            'KPM_VERIFY_PORTABLE': str(portable), 'KPM_VERIFY_SCRATCH': str(scratch),
        })
        subprocess.run([str(portable / 'runtime/python.exe'), '-B', '-c', SCRIPT],
                       cwd=scratch, env=env, check=True, timeout=120)


if __name__ == '__main__':
    main()

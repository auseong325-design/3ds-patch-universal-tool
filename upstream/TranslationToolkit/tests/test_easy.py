import json
import os
import struct
import subprocess
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

from korean_patch_manager import core, easy, merge, metadata, paint, romforge, simple

TITLE = '00040000001CA900'


def install_zip(path, prefix=f'luma/titles/{TITLE}/', files=None, code='code.ips'):
    files = files or {'romfs/Msg/a.dat': b'hangul', 'romfs/Lyt/b.dat': b'layout' * 100}
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('luma/', b'')
        for name, data in files.items():
            z.writestr(prefix + name, data)
        if code:
            z.writestr(prefix + code, b'PATCHEOF')
    return files


def ncch_game(path, title=TITLE, flags7=0x04):
    header = bytearray(0x200)
    header[0x100:0x104] = b'NCCH'
    header[0x118:0x120] = int(title, 16).to_bytes(8, 'little')
    header[0x150:0x15A] = b'CTR-P-TEST'
    header[0x18F] = flags7
    Path(path).write_bytes(bytes(header))


class RecognitionTests(unittest.TestCase):
    def test_install_zip_layouts_and_folder(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            install_zip(root / 'luma.zip')
            package = easy.detect_package(root / 'luma.zip')
            self.addCleanup(package.close)
            self.assertEqual((package.kind, package.title_id, package.file_count, package.code_patch),
                             (easy.INSTALL, TITLE, 2, True))
            self.assertFalse(package.needs_game)
            install_zip(root / 'nested.zip', prefix=f'My Patch/load/mods/{TITLE}/', code='exefs/code.ips')
            nested = easy.detect_package(root / 'nested.zip')
            self.addCleanup(nested.close)
            self.assertIn('azahar', nested.layouts)
            self.assertEqual(easy._entries_for(nested, easy.SD)[TITLE]['code.ips'], f'My Patch/load/mods/{TITLE}/exefs/code.ips')
            self.assertIn('exefs/code.ips', easy._entries_for(nested, easy.EMULATOR)[TITLE])
            with zipfile.ZipFile(root / 'luma.zip') as z:
                z.extractall(root / 'unzipped')
            for folder in (root / 'unzipped', root / 'unzipped' / 'luma', root / 'unzipped' / 'luma' / 'titles' / TITLE):
                found = easy.detect_package(folder)
                self.assertEqual((found.kind, found.title_id, found.file_count), (easy.INSTALL, TITLE, 2), folder)

    def test_manifest_packages_and_rejections(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with zipfile.ZipFile(root / 'merge.zip', 'w') as z:
                z.writestr('manifest.json', json.dumps({'format': 'kpm-merge-v1', 'title_id': TITLE, 'changes': [{}] * 3,
                                                        'includes_ips': True, 'components': {'code': {}, 'exheader': {}, 'banner': {}}}))
            package = easy.detect_package(root / 'merge.zip'); self.addCleanup(package.close)
            self.assertEqual((package.kind, package.file_count, package.code_patch, package.banner), (easy.MERGE, 3, True, True))
            self.assertTrue(package.needs_game)
            with zipfile.ZipFile(root / 'delta.zip', 'w') as z:
                z.writestr('manifest.json', json.dumps({'format': 'kpm-xdelta-v2', 'title_id': TITLE, 'changes': [{}]}))
                z.writestr('patches/a.xdelta', b'x')
            delta = easy.detect_package(root / 'delta.zip'); self.addCleanup(delta.close)
            self.assertEqual(delta.kind, easy.DELTA)
            with zipfile.ZipFile(root / 'photos.zip', 'w') as z:
                z.writestr('a.png', b'x')
            with self.assertRaises(easy.EasyError) as caught:
                easy.detect_package(root / 'photos.zip')
            self.assertEqual(caught.exception.topic, 'package')
            with zipfile.ZipFile(root / 'evil.zip', 'w') as z:
                z.writestr(f'luma/titles/{TITLE}/romfs/../../../boot.firm', b'x')
            with self.assertRaisesRegex(easy.EasyError, '안전하지 않은'):
                easy.detect_package(root / 'evil.zip')
            (root / 'note.txt').write_text('hello')
            (root / 'game.cia').write_bytes(b'x')
            (root / 'patch.7z').write_bytes(b'x')
            for name, text in (('note.txt', '패치 파일이 아닙니다'), ('game.cia', '게임 파일'), ('patch.7z', '7z'), ('missing.zip', '찾을 수 없습니다')):
                with self.assertRaisesRegex(easy.EasyError, text):
                    easy.detect_package(root / name)

    def test_game_check(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            install_zip(root / 'p.zip')
            package = easy.detect_package(root / 'p.zip'); self.addCleanup(package.close)
            ncch_game(root / 'ok.cci')
            game = easy.check_game(root / 'ok.cci', package)
            self.assertEqual((game.title_id, game.encrypted), (TITLE, False))
            ncch_game(root / 'locked.3ds', flags7=0)
            self.assertTrue(easy.check_game(root / 'locked.3ds', package).encrypted)
            ncch_game(root / 'update.cci', title='0004000E001CA900')
            with self.assertRaisesRegex(easy.EasyError, '업데이트 파일'):
                easy.check_game(root / 'update.cci', package)
            ncch_game(root / 'other.cci', title='00040000001D1400')
            with self.assertRaisesRegex(easy.EasyError, '다른 게임'):
                easy.check_game(root / 'other.cci', package)
            (root / 'junk.cia').write_bytes(b'\0' * 0x400)
            with self.assertRaisesRegex(easy.EasyError, '읽을 수 없습니다'):
                easy.check_game(root / 'junk.cia', package)
            with self.assertRaisesRegex(easy.EasyError, '압축 파일'):
                easy.check_game(root / 'p.zip', package)
            self.assertEqual(easy.default_output(root / 'ok.cci', 'CCI').name, 'ok [한글패치].cci')
            (root / 'ok [한글패치].cci').write_bytes(b'x')
            self.assertEqual(easy.default_output(root / 'ok.cci', 'CCI').name, 'ok [한글패치] (2).cci')


class InstallTests(unittest.TestCase):
    def test_fresh_same_replace_and_rollback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); sd = root / 'sd'; sd.mkdir()
            files = install_zip(root / 'v1.zip')
            package = easy.detect_package(root / 'v1.zip'); self.addCleanup(package.close)
            seen = []
            result = easy.run_install(package, easy.SD, sd, progress=lambda done, total: seen.append((done, total)))
            target = sd / 'luma' / 'titles' / TITLE
            self.assertEqual(result['state'], 'fresh')
            self.assertEqual(seen[-1][0], seen[-1][1])
            for name, data in files.items():
                self.assertEqual((target / name).read_bytes(), data)
            self.assertEqual((target / 'code.ips').read_bytes(), b'PATCHEOF')
            self.assertEqual([p.name for p in (sd / 'luma' / 'titles').iterdir()], [TITLE])
            self.assertEqual(easy.run_install(package, easy.SD, sd)['state'], 'same')

            install_zip(root / 'v2.zip', files={'romfs/Msg/a.dat': b'hangul v2'}, code=None)
            newer = easy.detect_package(root / 'v2.zip'); self.addCleanup(newer.close)
            (target / 'my-own-file.txt').write_text('keep me')
            with self.assertRaises(easy.NeedsConfirmation) as waiting:
                easy.run_install(newer, easy.SD, sd)
            self.assertEqual(waiting.exception.plan.state, 'replace')
            self.assertEqual((target / 'romfs/Msg/a.dat').read_bytes(), b'hangul')
            result = easy.run_install(newer, easy.SD, sd, allow_replace=True)
            self.assertEqual((target / 'romfs/Msg/a.dat').read_bytes(), b'hangul v2')
            self.assertFalse((target / 'code.ips').exists())
            backup = Path(result['backups'][0])
            self.assertEqual(backup.parent, sd / 'luma' / 'titles_backup')
            self.assertEqual((backup / 'my-own-file.txt').read_text(), 'keep me')
            self.assertEqual((backup / 'code.ips').read_bytes(), b'PATCHEOF')

            # A source that fails halfway must leave the installed patch exactly as it was.
            class Broken:
                def __init__(self, inner): self.inner = inner
                def __getattr__(self, name): return getattr(self.inner, name)
                def crc(self, name): return self.inner.crc(name) ^ 1
            package.source = Broken(package.source)
            with self.assertRaisesRegex(easy.EasyError, '원본과 다릅니다'):
                easy.run_install(package, easy.SD, sd, allow_replace=True)
            package.source = package.source.inner
            self.assertEqual((target / 'romfs/Msg/a.dat').read_bytes(), b'hangul v2')
            self.assertEqual(len(list((sd / 'luma' / 'titles_backup').iterdir())), 1)
            self.assertEqual([p.name for p in (sd / 'luma' / 'titles').iterdir()], [TITLE])

    def test_emulator_layout_and_missing_place(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); user = root / 'Azahar'; user.mkdir()
            install_zip(root / 'p.zip')
            package = easy.detect_package(root / 'p.zip'); self.addCleanup(package.close)
            easy.run_install(package, easy.EMULATOR, user)
            self.assertTrue((user / 'load' / 'mods' / TITLE / 'romfs' / 'Msg' / 'a.dat').is_file())
            self.assertTrue((user / 'load' / 'mods' / TITLE / 'code.ips').is_file())
            with self.assertRaisesRegex(easy.EasyError, '설치할 위치'):
                easy.run_install(package, easy.SD, root / 'gone')

    def test_explain_and_settings(self):
        self.assertEqual(easy.explain(ValueError('패키지 매니페스트가 너무 큽니다.'))[2], None)
        self.assertEqual(easy.explain(RuntimeError('RomForge 리팩 실패: aes_keys.txt not found'))[2], 'keys')
        self.assertEqual(easy.explain(ValueError('원본 해시 불일치: romfs/a'))[2], 'game')
        self.assertIn('쓸 수 없습니다', easy.explain(PermissionError(13, 'denied'))[0])
        with tempfile.TemporaryDirectory() as td:
            os.environ['KPM_SETTINGS'] = str(Path(td) / 'deep' / 'easy.json')
            try:
                self.assertEqual(easy.load_settings(), {})
                easy.save_settings({'place': 'sd'})
                self.assertEqual(easy.load_settings(), {'place': 'sd'})
            finally:
                del os.environ['KPM_SETTINGS']


class PaintTests(unittest.TestCase):
    def test_png_is_valid_and_symmetric(self):
        data, pad = paint.rounded(40, 20, 6, '#FFFFFF', border='#D0D0D0', shadow=('#000000', 0.2, 6, 2))
        self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
        width, height = struct.unpack('>II', data[16:24])
        self.assertEqual((width, height), (40 + 2 * pad, 20 + 2 * pad))
        raw = zlib.decompress(data[data.index(b'IDAT') + 4:data.index(b'IEND') - 8])
        rows = [raw[i * (width * 4 + 1) + 1:(i + 1) * (width * 4 + 1)] for i in range(height)]
        centre = rows[height // 2]
        self.assertEqual(centre[(width // 2) * 4:(width // 2) * 4 + 4], b'\xff\xff\xff\xff')
        pixels = [centre[i:i + 4] for i in range(0, len(centre), 4)]
        self.assertEqual(pixels, pixels[::-1])
        self.assertEqual(rows[0][:4][3], 0)
        self.assertEqual(paint.mix('#000000', '#FFFFFF', 0.5), '#808080')
        self.assertEqual(paint.app_icon(32, '#3B5BDB', '#2F49B8')[:4], b'\x89PNG')


class HomeMenuTests(unittest.TestCase):
    def test_title_slot_and_certificates_from_cia(self):
        icon = bytearray(metadata.SMDH_SIZE); icon[:4] = b'SMDH'
        english = 8 + 0x200
        icon[english:english + 8] = 'Game'.encode('utf-16-le'); icon[english + 0x180:english + 0x190] = 'Nintendo'.encode('utf-16-le')
        icon[0x2040:0x2050] = bytes(range(16))            # icon pixels must survive
        renamed = metadata.smdh_with_title(bytes(icon), '마리오&루이지 RPG3 DX')
        titles = metadata.smdh_titles(renamed)
        self.assertEqual(titles['ko'], ('마리오&루이지 RPG3 DX', '마리오&루이지 RPG3 DX', 'Nintendo'))
        self.assertEqual(titles['en'][0], 'Game')
        korean = 8 + 7 * 0x200
        self.assertEqual(renamed[:korean], bytes(icon[:korean]))
        self.assertEqual(renamed[korean + 0x200:], bytes(icon[korean + 0x200:]))
        for bad in ('', 'x' * 64, 'two\nlines'):
            with self.assertRaises(ValueError):
                metadata.smdh_with_title(bytes(icon), bad)
        with self.assertRaisesRegex(ValueError, 'SMDH'):
            metadata.smdh_titles(b'not an icon')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            chain = b'\0' * 0x40 + b'Root-CA00000003' + b'\1' * 0x9B1
            header = (0x2020).to_bytes(4, 'little') + bytes(4) + len(chain).to_bytes(4, 'little') + bytes(0x2014)
            (root / 'game.cia').write_bytes(header.ljust(0x2040, b'\0') + chain + bytes(64))
            self.assertEqual(metadata.cia_certificates(root / 'game.cia'), chain)
            (root / 'game.cci').write_bytes(b'x' * 0x4000)
            keys = root / 'keys'; keys.mkdir(); (keys / 'aes_keys.txt').write_text('common0=' + '0' * 32)
            simple.validate_cia_keys(keys, root / 'game.cia')         # certs.bin comes from the CIA itself
            with self.assertRaisesRegex(ValueError, 'certs.bin'):
                simple.validate_cia_keys(keys, root / 'game.cci')


@unittest.skipUnless(all(os.environ.get(k) for k in ('KPM_TEST_ROMFORGE_BRIDGE', 'KPM_TEST_ROMFORGE_FIXTURE', 'KPM_TEST_XDELTA')), '통합 시험 도구 필요')
class EasyIntegrationTests(unittest.TestCase):
    def test_merge_and_delta_from_game_file_only(self):
        bridge, xdelta = os.environ['KPM_TEST_ROMFORGE_BRIDGE'], os.environ['KPM_TEST_XDELTA']
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(['dotnet', os.environ['KPM_TEST_ROMFORGE_FIXTURE'], str(root / 'fixture')], check=True, capture_output=True)
            f = root / 'fixture'
            romforge.setup_workspace(f / 'game.cci', f / 'keys', root / 'base', bridge)
            romforge.setup_workspace(f / 'update.cci', f / 'keys', root / 'update', bridge)
            patch = root / 'patch'; patch.mkdir(); (patch / 'test.bin').write_bytes(b'translation')
            merge.build_bundle(root / 'base', root / 'update', patch, root / 'merge.zip', xdelta,
                               banner=f / 'banner_ko.bin', title='마리오&루이지 RPG3 DX')
            package = easy.detect_package(root / 'merge.zip'); self.addCleanup(package.close)
            self.assertEqual((package.banner, package.home_title), (True, '마리오&루이지 RPG3 DX'))
            self.assertIn('HOME 이름', package.summary)
            with self.assertRaisesRegex(ValueError, '하나만'):
                merge.build_bundle(root / 'base', root / 'update', patch, root / 'both.zip', xdelta, icon=f / 'icon_ko.bin', title='x')
            merge.build_bundle(root / 'base', root / 'update', patch, root / 'file.zip', xdelta, icon=f / 'icon_ko.bin')
            for name, expected in (('merge.zip', '마리오&루이지 RPG3 DX'), ('file.zip', '새 이름')):
                # the rebuilt game must carry the new icon: extract it again and read the Korean title
                made = root / (name + '.cia')
                simple.apply_merged_game(f / 'game.cci', root / name, made, 'CIA', str(f / 'keys'), bridge, xdelta)
                romforge.setup_workspace(made, f / 'keys', root / (name + '-check'), bridge)
                rebuilt = (root / (name + '-check') / 'original_icon.bin').read_bytes()
                self.assertEqual(metadata.smdh_titles(rebuilt)['ko'][0], expected)
                if name == 'merge.zip':
                    self.assertEqual((root / (name + '-check') / 'original_banner.bin').read_bytes(), (f / 'banner_ko.bin').read_bytes())
            game = easy.check_game(f / 'game.cci', package)
            self.assertEqual(game.title_id, package.title_id)
            phases = []
            output = easy.default_output(game.path, 'CCI', root / 'out')
            result = easy.run_merge(package, game.path, 'CCI', None, bridge, xdelta, output,
                                    progress=lambda phase, fraction: phases.append(phase))
            self.assertTrue(output.is_file())
            self.assertEqual(result['checked_files'], 1)
            self.assertEqual(sorted(set(phases)), [0, 1, 2, 3])
            self.assertEqual([p.name for p in (root / 'out').iterdir()], [output.name])

            project = json.loads((root / 'base' / 'project.json').read_text(encoding='utf-8'))
            (Path(project['edited']) / 'test.bin').write_bytes(b'delta translation')
            core.xdelta_bundle({'title_id': project['title_id'], 'original': project['original'], 'edited': project['edited']},
                               root / 'delta.zip', xdelta)
            delta = easy.detect_package(root / 'delta.zip'); self.addCleanup(delta.close)
            self.assertEqual(delta.kind, easy.DELTA)
            sd = root / 'sd'; sd.mkdir()
            before = sorted(p.name for p in f.iterdir())
            easy.run_delta(delta, f / 'game.cci', easy.SD, sd, None, bridge, xdelta)
            self.assertEqual((sd / 'luma' / 'titles' / project['title_id'] / 'romfs' / 'test.bin').read_bytes(), b'delta translation')
            self.assertEqual(sorted(p.name for p in f.iterdir()), before)


if __name__ == '__main__':
    unittest.main()

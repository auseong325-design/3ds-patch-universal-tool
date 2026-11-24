"""Recipe patches (kpm-recipe-v1): recognition, the user's files, Luma placement check, CIA content check.

The real-dump test runs only with KPM_TEST_SSB3DS=<folder> holding ssb3ds_ko_recipe.zip and the user's own decrypted
Korean Super Smash Bros. dumps (base .3ds and update 1.1.7 .cia); nothing game-derived is in this repository.
"""
import hashlib
import json
import os
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from korean_patch_manager import easy, recipe
from korean_patch_manager.games.ssb3ds import engine as ssb3ds

BASE, UPDATE, DLC = '0004000000167C00', '0004000E00167C00', '0004008C00167C00'


def recipe_zip(path, engine='ssb3ds', requires=None, **extra):
    manifest = dict(format='kpm-recipe-v1', engine=engine, engine_version=1, title_id=BASE, name='테스트 패치', version='1.0',
                    requires=requires or {'base': {'title_id': BASE}, 'update': {'title_id': UPDATE, 'version': 35088},
                                          'dlc': {'title_id': DLC, 'optional': True}},
                    rom_name='TEST_GAME', files={'messages.json': '0' * 64}, **extra)
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest))
        z.writestr('recipe/messages.json', '{}')
    return manifest


def ncch_file(path, title, flags7=0x04):
    header = bytearray(0x200)
    header[0x100:0x104] = b'NCCH'
    header[0x118:0x120] = int(title, 16).to_bytes(8, 'little')
    header[0x18F] = flags7
    Path(path).write_bytes(bytes(header))


def ncsd_file(path, title):
    """A cartridge-dump-shaped file: NCSD header, then the NCCH header of partition 0."""
    head = bytearray(0x200)
    head[0x100:0x104] = b'NCSD'
    head[0x120:0x128] = (1).to_bytes(4, 'little') + (1).to_bytes(4, 'little')
    ncch_file(path, title)
    Path(path).write_bytes(bytes(head) + Path(path).read_bytes())


def ips(*records):
    out = bytearray(b'PATCH')
    for offset, size in records:
        out += offset.to_bytes(3, 'big') + size.to_bytes(2, 'big') + bytes(size)
    return bytes(out + b'EOF')


class RecipeRecognitionTests(unittest.TestCase):
    def test_recipe_is_recognised_and_needs_the_users_files(self):
        with tempfile.TemporaryDirectory() as td:
            recipe_zip(Path(td) / 'r.zip')
            package = easy.detect_package(Path(td) / 'r.zip')
            self.addCleanup(package.close)
            self.assertEqual((package.kind, package.title_id), (easy.RECIPE, BASE))
            self.assertTrue(package.needs_game)
            self.assertIn('내 게임 파일로 만드는 패치', package.summary)
            self.assertEqual(package.requires('update')['version'], 35088)

    def test_unknown_engine_and_broken_manifest_are_explained(self):
        with tempfile.TemporaryDirectory() as td:
            recipe_zip(Path(td) / 'unknown.zip', engine='no_such_game')
            with self.assertRaises(easy.EasyError) as raised:
                easy.detect_package(Path(td) / 'unknown.zip')
            self.assertIn('최신 버전', raised.exception.body)
            recipe_zip(Path(td) / 'bad.zip', requires={'base': {'title_id': BASE}})
            with self.assertRaises(easy.EasyError):
                easy.detect_package(Path(td) / 'bad.zip')
            recipe_zip(Path(td) / 'path.zip', engine='../easy')
            with self.assertRaises(easy.EasyError):
                easy.detect_package(Path(td) / 'path.zip')

    def test_users_files_are_sorted_by_title_id(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            recipe_zip(root / 'r.zip')
            package = easy.detect_package(root / 'r.zip')
            self.addCleanup(package.close)
            for name, title in (('base.3ds', BASE), ('update.cia', UPDATE), ('dlc.cia', DLC)):
                ncch_file(root / name, title)
            ncsd_file(root / 'cart.3ds', BASE)
            self.assertEqual(easy.check_recipe_input(root / 'cart.3ds', package).format, 'NCSD/CCI')
            self.assertEqual([easy.check_recipe_input(root / n, package).role for n in ('update.cia', 'dlc.cia', 'base.3ds')],
                             ['update', 'dlc', 'base'])
            ncch_file(root / 'other.3ds', '00040000001CA900')
            with self.assertRaises(easy.EasyError) as raised:
                easy.check_recipe_input(root / 'other.3ds', package)
            self.assertIn('다른 게임', raised.exception.title)
            ncch_file(root / 'locked.3ds', BASE, flags7=0)
            with self.assertRaises(easy.EasyError) as raised:
                easy.check_recipe_input(root / 'locked.3ds', package)
            self.assertIn('암호화', raised.exception.title)

    def test_output_name_and_phases(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            recipe_zip(root / 'r.zip')
            package = easy.detect_package(root / 'r.zip')
            self.addCleanup(package.close)
            ncch_file(root / 'base.3ds', BASE)
            inputs = {'base': easy.check_recipe_input(root / 'base.3ds', package)}
            first = easy.recipe_output(package, inputs, 'CCI')
            self.assertEqual(first.name, 'TEST_GAME.3ds')
            first.write_bytes(b'x')
            self.assertEqual(easy.recipe_output(package, inputs, 'CCI').name, 'TEST_GAME (2).3ds')
            self.assertEqual(easy.recipe_output(package, dict(inputs, dlc=inputs['base']), 'CIA').name, 'TEST_GAME_DLC.cia')
        seen = []
        report = easy._phase_reporter(lambda phase, fraction: seen.append(phase), 'rom')
        for label in ('본편 확인', '본편·업데이트 데이터 분해', '글꼴에 한글 추가', '합본 게임 파일 조립', '번역·글꼴·Wii U 메뉴 적용', '3DS용 CIA로 바꾸기'):
            report(label, None)
        self.assertEqual(seen, [0, 0, 1, 2, 2, 3])


class LumaPlacementTests(unittest.TestCase):
    def test_code_right_after_text_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'code.ips'
            text = 0xA6110C                       # SSB3DS 1.1.7: Luma writes its LayeredFS code at 0xA6110C
            path.write_bytes(ips((0x2A21C, 4), (0xA61110, 0xC9)))
            self.assertEqual(recipe.luma_conflicts(path, text), [(0xA61110, 0xA611D9)])
            path.write_bytes(ips((0x2A21C, 4), (0xA61F00, 0xC9)))
            self.assertEqual(recipe.luma_conflicts(path, text), [])

    def test_shipped_engine_keeps_its_code_clear_of_luma(self):
        self.assertEqual(ssb3ds.FORMAT, recipe.FORMAT)
        self.assertEqual(ssb3ds.KPM_LAYOUT['ssb3ds-ko/rex/dt'], 'romfs/_ko_update/dt')
        source = json.loads((Path(ssb3ds.__file__).parent / 'SOURCE.json').read_text(encoding='utf-8'))
        for name, digest in source['files'].items():
            text = (Path(ssb3ds.__file__).parent / name).read_text(encoding='utf-8')
            self.assertEqual(hashlib.sha256(text.encode('utf-8')).hexdigest(), digest, name)


class CiaContentTests(unittest.TestCase):
    def test_cia_content_must_equal_the_checked_cci(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            content = os.urandom(0x400)
            cci = bytearray(0x200) + content
            cci[0x100:0x104] = b'NCSD'
            struct.pack_into('<II', cci, 0x120, 1, 2)
            (root / 'game.3ds').write_bytes(bytes(cci))
            tmd = bytearray(0xB34)
            struct.pack_into('>I', tmd, 0, 0x10004)
            struct.pack_into('>IHHQ', tmd, 0x140 + 0xC4 + 0x900, 0, 0, 0, len(content))
            header = struct.pack('<IHHIIII', 0x2020, 0, 0, 0, 0, len(tmd), 0).ljust(0x2040, b'\0')
            tmd_block = bytes(tmd).ljust((len(tmd) + 63) & ~63, b'\0')
            (root / 'game.cia').write_bytes(header + tmd_block + content)
            self.assertEqual(recipe._range_sha(*recipe._cia_content0(root / 'game.cia')),
                             recipe._range_sha(*recipe._cci_partition0(root / 'game.3ds')))
            (root / 'other.cia').write_bytes(header + tmd_block + os.urandom(0x400))
            self.assertNotEqual(recipe._range_sha(*recipe._cia_content0(root / 'other.cia')),
                                recipe._range_sha(*recipe._cci_partition0(root / 'game.3ds')))


@unittest.skipUnless(os.environ.get('KPM_TEST_SSB3DS'), 'set KPM_TEST_SSB3DS to a folder with the recipe and your own dumps')
class RealDumpTests(unittest.TestCase):
    def test_install_build_matches_the_tested_result(self):
        folder = Path(os.environ['KPM_TEST_SSB3DS'])
        package = easy.detect_package(folder / 'ssb3ds_ko_recipe.zip')
        self.addCleanup(package.close)
        games = [p for p in folder.iterdir() if p.suffix.lower() in ('.3ds', '.cci', '.cia')]
        inputs = {}
        for path in games:
            game = easy.check_recipe_input(path, package)
            inputs[game.role] = game
        with tempfile.TemporaryDirectory() as td, zipfile.ZipFile(folder / 'ssb3ds_ko_recipe.zip') as z:
            expected = json.loads(z.read('recipe/expected.json'))['outputs']
            outcome = easy.run_recipe(package, inputs, 'install', easy.SD, Path(td))
            title = Path(outcome['targets'][0])
            for logical, relative in ssb3ds.KPM_LAYOUT.items():
                digest = hashlib.sha256((title / relative).read_bytes()).hexdigest()
                self.assertEqual(digest, expected[logical], relative)
            self.assertEqual(hashlib.sha256((title / 'code.ips').read_bytes()).hexdigest(), package.manifest['files']['code.ips'])
            self.assertEqual(easy.run_recipe(package, inputs, 'install', easy.SD, Path(td))['state'], 'same')


if __name__ == '__main__':
    unittest.main()

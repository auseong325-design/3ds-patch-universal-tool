import ctypes
import json
import os
import subprocess
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

TITLE = '00040000001CA900'
INTEGRATION = all(os.environ.get(k) for k in ('KPM_TEST_ROMFORGE_BRIDGE', 'KPM_TEST_ROMFORGE_FIXTURE', 'KPM_TEST_XDELTA'))


def install_zip(path, text=b'hangul'):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr(f'luma/titles/{TITLE}/romfs/Msg/a.dat', text)
        z.writestr(f'luma/titles/{TITLE}/code.ips', b'PATCHEOF')


def drop_files(app, paths):
    """Post a real WM_DROPFILES, the message Explorer sends when files are dropped on the window."""
    from ctypes import wintypes
    kernel, user = ctypes.windll.kernel32, ctypes.windll.user32
    names = ''.join(str(p) + '\0' for p in paths) + '\0'
    data = (20).to_bytes(4, 'little') + bytes(12) + (1).to_bytes(4, 'little') + names.encode('utf-16-le')
    kernel.GlobalAlloc.restype = kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalLock.argtypes = kernel.GlobalUnlock.argtypes = [ctypes.c_void_p]
    handle = kernel.GlobalAlloc(0x0042, len(data))
    ctypes.memmove(kernel.GlobalLock(handle), data, len(data))
    kernel.GlobalUnlock(handle)
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW(int(app.frame(), 16), 0x0233, handle, 0)


@unittest.skipUnless(os.environ.get('KPM_TEST_GUI') == '1', 'GUI 시험은 KPM_TEST_GUI=1로 실행합니다')
class EasyUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        os.environ['KPM_SETTINGS'] = str(self.root / 'settings.json')
        from KoreanPatchManager import App
        self.app = App()
        self.errors = []
        self.app.report_callback_exception = lambda *args: self.errors.append(args)
        self.panel = self.app.easy_panel
        self.pump(0.3)
        self.app.focus_force()      # generated key presses only arrive when the window holds the focus
        self.pump(0.1)

    def tearDown(self):
        self.app.destroy()
        os.environ.pop('KPM_SETTINGS', None)
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def pump(self, seconds=0.15, until=None):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not (until and until()):
            self.app.update(); time.sleep(0.01)

    def texts(self):
        c = self.panel.c
        return ' | '.join(c.itemcget(i, 'text') for i in c.find_all() if c.type(i) == 'text')

    def click(self, text):
        """Press the control whose label is `text` with real pointer events on the canvas."""
        c = self.panel.c
        item = next(i for i in c.find_all() if c.type(i) == 'text' and c.itemcget(i, 'text') == text)
        x0, y0, x1, y1 = c.bbox(item)
        x, y = (x0 + x1) // 2, (y0 + y1) // 2
        for sequence in ('<Motion>', '<ButtonPress-1>', '<ButtonRelease-1>'):
            c.event_generate(sequence, x=x, y=y)
        self.pump()

    def test_install_patch_with_pointer_keyboard_and_drop(self):
        import KoreanPatchManager as kpm
        app, panel = self.app, self.panel
        self.assertEqual(app.mode.get(), kpm.EASY)
        self.assertIn('받은 패치 파일을 여기에 놓아 주세요', self.texts())
        install_zip(self.root / 'patch.zip')
        sd = self.root / 'sd'; sd.mkdir()
        panel.cards = []
        if os.name == 'nt':
            self.pump(0.3, lambda: panel.drops is not None)
            self.assertIsNotNone(panel.drops)
            drop_files(app, [self.root / 'patch.zip'])
            self.pump(2, lambda: panel.package is not None)
        else:
            panel.add_paths([self.root / 'patch.zip'])
        self.assertEqual(panel.package.kind, 'install')
        self.assertEqual(panel.blocker(), '어디에서 플레이할지 골라 주세요.')
        self.assertNotIn('게임 파일이 필요해요', self.texts())
        self.click('닌텐도 3DS')
        self.assertEqual(panel.where, '3ds')
        self.assertEqual(json.loads((self.root / 'settings.json').read_text(encoding='utf-8'))['place'], '3ds')
        self.assertIn('SD 카드를 PC에 꽂아 주세요', self.texts())
        app.event_generate('<Return>'); self.pump()
        self.assertEqual(panel.view, 'home')            # nothing happens while something is missing
        panel.sd_root = sd; panel.render()
        self.assertIsNone(panel.blocker())
        app.event_generate('<Return>')
        self.pump(10, lambda: panel.view == 'done')
        target = sd / 'luma' / 'titles' / TITLE
        self.assertEqual((target / 'romfs' / 'Msg' / 'a.dat').read_bytes(), b'hangul')
        self.assertIn('설치를 마쳤어요', self.texts())
        self.assertIn('Enable game patching', self.texts())
        self.click('처음으로')
        self.assertIsNone(panel.package)

        install_zip(self.root / 'newer.zip', b'hangul 2')
        panel.add_paths([self.root / 'newer.zip'])
        panel.sd_root = sd; panel.render()
        self.click('설치 시작')
        self.pump(10, lambda: panel.view == 'confirm')
        self.assertEqual((target / 'romfs' / 'Msg' / 'a.dat').read_bytes(), b'hangul')
        self.click('취소')
        self.assertEqual(panel.view, 'home')
        self.click('설치 시작')
        self.pump(10, lambda: panel.view == 'confirm')
        self.click('옮기고 설치')
        self.pump(10, lambda: panel.view == 'done')
        self.assertEqual((target / 'romfs' / 'Msg' / 'a.dat').read_bytes(), b'hangul 2')
        self.assertEqual(len(list((sd / 'luma' / 'titles_backup').iterdir())), 1)

    def test_recipe_patch_sorts_files_and_switches_modes(self):
        from korean_patch_manager import easy
        import importlib.util
        spec = importlib.util.spec_from_file_location('kpm_test_recipe', Path(__file__).with_name('test_recipe.py'))
        helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)
        recipe_zip, ncch_file, BASE, UPDATE, DLC = helpers.recipe_zip, helpers.ncch_file, helpers.BASE, helpers.UPDATE, helpers.DLC
        panel = self.panel
        recipe_zip(self.root / 'recipe.zip')
        for name, title in (('update.cia', UPDATE), ('dlc.cia', DLC)):
            ncch_file(self.root / name, title)
        helpers.ncsd_file(self.root / 'game.3ds', BASE)
        panel.add_paths([self.root / 'recipe.zip', self.root / 'update.cia', self.root / 'game.3ds'])
        self.pump()
        self.assertEqual(panel.package.kind, easy.RECIPE)
        self.assertEqual(sorted(panel.inputs), ['base', 'update'])
        self.assertIn('무엇을 만들까요?', self.texts())
        self.assertNotIn('DLC 파일 (선택)', self.texts())             # DLC only goes into the all-in-one
        self.click('합본 게임 파일')
        self.assertEqual(panel.recipe_mode, 'rom')
        self.assertIn('DLC 파일 (선택)', self.texts())
        panel.add_paths([self.root / 'dlc.cia']); self.pump()
        self.assertEqual(sorted(panel.inputs), ['base', 'dlc', 'update'])
        self.click('PC')
        self.assertIn('합본 게임 파일(.3ds)', self.texts())
        self.assertIn('만들기 시작', self.texts())
        calls = []
        original = easy.run_recipe

        def fake(package, inputs, mode, *args, **kwargs):
            calls.append((mode, sorted(inputs), kwargs.get('fmt'), Path(kwargs['output']).name))
            Path(kwargs['output']).write_bytes(b'rom')
            return {'output': str(kwargs['output']), 'sha256': '0' * 64, 'record': {}}
        easy.run_recipe = fake
        self.addCleanup(setattr, easy, 'run_recipe', original)
        from korean_patch_manager import recipe
        tool = recipe.tool_path(panel.app_dir)
        if tool.is_file():                         # 3dstool is bundled in app/tools; without it the button stays blocked
            self.assertIsNone(panel.blocker())
            self.click('만들기 시작')
            self.pump(10, lambda: panel.view == 'done')
            self.assertEqual(calls, [('rom', ['base', 'dlc', 'update'], 'CCI', 'TEST_GAME_DLC.3ds')])
            self.assertIn('합본 게임 파일을 만들었어요', self.texts())
            self.assertIn('업데이트·DLC·모드가 따로 설치돼 있으면', self.texts())
            self.click('처음으로')
        panel.add_paths([self.root / 'recipe.zip', self.root / 'game.3ds']); self.pump()
        self.click('패치 설치')
        self.assertEqual(panel.blocker(), '업데이트 파일을(를) 넣어 주세요.')
        self.assertEqual(json.loads((self.root / 'settings.json').read_text(encoding='utf-8'))['recipe_mode'], 'install')
        sd = self.root / 'sd'; (sd / 'luma' / 'titles' / BASE).mkdir(parents=True)
        panel.sd_root, panel.cards = sd, []
        self.click('합본 게임 파일'); self.click('닌텐도 3DS')
        self.assertIn('SD 카드에 이 게임의 패치 폴더가 있어요', self.texts())

    def test_wrong_files_help_modes_and_themes(self):
        import KoreanPatchManager as kpm
        from korean_patch_manager import easy_ui
        app, panel = self.app, self.panel
        (self.root / 'note.txt').write_text('x')
        panel.add_paths([self.root / 'note.txt'])
        self.assertIn('패치 파일이 아닙니다', self.texts())
        self.assertEqual(panel.blocker(), '패치 파일을 넣어 주세요.')
        with zipfile.ZipFile(self.root / 'merge.zip', 'w') as z:
            z.writestr('manifest.json', json.dumps({'format': 'kpm-merge-v1', 'title_id': TITLE, 'changes': [],
                                                    'components': {'code': {}, 'exheader': {}}}))
        header = bytearray(0x200); header[0x100:0x104] = b'NCCH'; header[0x18F] = 4
        header[0x118:0x120] = int('00040000001D1400', 16).to_bytes(8, 'little')
        (self.root / 'usa.cci').write_bytes(bytes(header))
        header[0x118:0x120] = int(TITLE, 16).to_bytes(8, 'little')
        (self.root / 'japan.cci').write_bytes(bytes(header))
        panel.add_paths([self.root / 'usa.cci', self.root / 'merge.zip'])      # order must not matter
        self.assertIn('이 패치와 다른 게임입니다', self.texts())
        panel.add_paths([self.root / 'japan.cci'])
        self.assertIn('패치와 맞는 게임이에요', self.texts())
        panel.key_dir = None
        self.click('닌텐도 3DS')
        self.assertEqual(panel.blocker(), '키 폴더를 골라 주세요.')
        self.click('준비 방법')
        self.assertEqual((panel.view, panel.help_topic), ('help', 'keys'))
        for topic in easy_ui.HELP:
            panel.open_help(topic); self.pump(0.03)
        app.event_generate('<Escape>'); self.pump()
        self.assertEqual(panel.view, 'home')
        self.click('PC')
        self.assertIn('.cci', self.texts())
        keys = self.root / 'keys'; keys.mkdir()
        (keys / 'certs.bin').write_bytes(b'c'); (keys / 'aes_keys.txt').write_text('common0=' + '0' * 32)
        panel.key_dir = keys; panel.choose_place('3ds')
        self.assertIn('.cia', self.texts())

        self.click('제작자 도구')
        self.assertEqual(app.mode.get(), kpm.SIMPLE)
        self.assertTrue(app.simple_panel.winfo_ismapped())
        app.mode.set(kpm.FULL); self.pump()
        self.assertTrue(app.book.winfo_ismapped())
        app.mode.set(kpm.EASY); self.pump()
        self.assertTrue(panel.winfo_ismapped())
        self.assertEqual(panel.package.kind, 'merge')      # choices survive a visit to the other modes

        for dark in (True, False):
            other = easy_ui.EasyPanel(app, app, dark=dark)
            other.place(x=0, y=0, relwidth=1, relheight=1); self.pump(0.1)
            other.add_paths([self.root / 'merge.zip', self.root / 'japan.cci'])
            for view in ('home', 'help'):
                other.go(view); self.pump(0.03)
            other.destroy()

    @unittest.skipUnless(INTEGRATION, '통합 시험 도구 필요')
    def test_merged_game_from_two_files(self):
        from korean_patch_manager import merge, romforge
        bridge, xdelta = os.environ['KPM_TEST_ROMFORGE_BRIDGE'], os.environ['KPM_TEST_XDELTA']
        app, panel, root = self.app, self.panel, self.root
        subprocess.run(['dotnet', os.environ['KPM_TEST_ROMFORGE_FIXTURE'], str(root / 'fixture')], check=True, capture_output=True)
        f = root / 'fixture'
        romforge.setup_workspace(f / 'game.cci', f / 'keys', root / 'base', bridge)
        romforge.setup_workspace(f / 'update.cci', f / 'keys', root / 'update', bridge)
        patch = root / 'patch'; patch.mkdir(); (patch / 'test.bin').write_bytes(b'translation')
        merge.build_bundle(root / 'base', root / 'update', patch, root / 'merge.zip', xdelta)
        app.vars['xdelta'].set(xdelta)
        panel.add_paths([root / 'merge.zip', f / 'game.cci'])
        self.click('PC')
        self.assertIsNone(panel.blocker(), self.texts())
        self.click('패치 시작')
        self.assertEqual(panel.view, 'working')
        self.pump(60, lambda: panel.view != 'working')
        self.assertEqual(panel.view, 'done', panel.failure)
        output = f / 'game [한글패치].cci'
        self.assertTrue(output.is_file())
        self.assertIn('패치된 게임 파일을 만들었어요', self.texts())
        self.assertFalse(list(f.glob('kpm-*')))


if __name__ == '__main__':
    unittest.main()

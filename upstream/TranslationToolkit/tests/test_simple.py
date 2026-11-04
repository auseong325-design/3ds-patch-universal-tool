import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from korean_patch_manager import simple, merge, romforge


class SimpleValidationTests(unittest.TestCase):
    def test_rejects_wrong_zip_kind_and_missing_cia_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with zipfile.ZipFile(root / 'install.zip', 'w') as z:
                z.writestr('luma/titles/game/romfs/test', b'text')
            with self.assertRaisesRegex(ValueError, '설치 ZIP'):
                simple.package_info(root / 'install.zip', {'kpm-merge-v1'})
            with zipfile.ZipFile(root / 'delta.zip', 'w') as z:
                z.writestr('manifest.json', json.dumps({'format': 'kpm-xdelta-v2'}))
            with self.assertRaisesRegex(ValueError, '종류가 다릅니다'):
                simple.package_info(root / 'delta.zip', {'kpm-merge-v1'})
            with self.assertRaisesRegex(ValueError, 'certs.bin'):
                simple.validate_cia_keys(root)
            (root / 'certs.bin').write_bytes(b'fake certificate')
            with self.assertRaisesRegex(ValueError, 'common0'):
                simple.validate_cia_keys(root)
            (root / 'aes_keys.txt').write_text('common0=' + '0' * 32)
            simple.validate_cia_keys(root)


@unittest.skipUnless(all(os.environ.get(k) for k in ('KPM_TEST_ROMFORGE_BRIDGE', 'KPM_TEST_ROMFORGE_FIXTURE', 'KPM_TEST_XDELTA')), '통합 시험 도구 필요')
class SimpleIntegrationTests(unittest.TestCase):
    def test_one_step_merge_uses_original_and_package_only(self):
        bridge = os.environ['KPM_TEST_ROMFORGE_BRIDGE']
        xdelta = os.environ['KPM_TEST_XDELTA']
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(['dotnet', os.environ['KPM_TEST_ROMFORGE_FIXTURE'], str(root / 'fixture')], check=True, capture_output=True)
            f = root / 'fixture'
            romforge.setup_workspace(f / 'game.cci', f / 'keys', root / 'base', bridge)
            romforge.setup_workspace(f / 'update.cci', f / 'keys', root / 'update', bridge)
            patch = root / 'patch'; patch.mkdir(); (patch / 'test.bin').write_bytes(b'translation')
            merge.build_bundle(root / 'base', root / 'update', patch, root / 'merge.zip', xdelta, banner=f / 'banner_ko.bin')
            for fmt, keys in (('CCI', ''), ('CIA', str(f / 'keys'))):
                output = root / ('result.' + fmt.lower())
                result = simple.apply_merged_game(f / 'game.cci', root / 'merge.zip', output, fmt, keys, bridge, xdelta)
                self.assertTrue(output.is_file())
                self.assertTrue(result['code_checked'])
                self.assertEqual(result['checked_files'], 1)
            self.assertFalse(list(root.glob('kpm-simple-*')))
            self.assertEqual((f / 'original/test.bin').read_bytes(), b'original-data')
            if os.environ.get('KPM_TEST_GUI') == '1':
                import time
                from KoreanPatchManager import App
                app = App()
                try:
                    app.withdraw(); app.update()
                    panel = app.simple_panel
                    panel.go(1)
                    for key, value in {'game': f / 'game.cci', 'package': root / 'merge.zip', 'output': root / 'via-gui.cci'}.items():
                        panel.values[key].set(str(value))
                    app.vars['xdelta'].set(xdelta)
                    self.assertEqual(app.vars['original_rom'].get(), '')
                    panel.next()
                    self.assertEqual(panel.step, 2)
                    panel.execute()
                    deadline = time.monotonic() + 20
                    while app.busy and time.monotonic() < deadline:
                        app.update(); time.sleep(.01)
                    self.assertFalse(app.busy)
                    self.assertTrue(panel.result_ok, panel.result_text)
                    self.assertTrue((root / 'via-gui.cci').is_file())
                finally: app.destroy()

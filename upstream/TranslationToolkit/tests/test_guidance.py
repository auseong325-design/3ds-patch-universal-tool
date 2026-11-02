import tempfile
import unittest
from pathlib import Path

from korean_patch_manager import guidance


class GuidanceTests(unittest.TestCase):
    def test_merge_recipient_checks_base_and_cia_certificate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = root / 'base'
            base.mkdir()
            keys = root / 'keys'
            keys.mkdir()
            xdelta = root / 'xdelta.exe'
            xdelta.touch()
            bridge = root / 'bridge.dll'
            bridge.touch()
            config = {'merge_base': str(base), 'keys': str(keys),
                      'xdelta': str(xdelta), 'repack_format': 'CIA'}
            name = '받은 통합 패키지 적용 · 배너 포함 가능'
            states = {key: ok for key, ok, _ in guidance.checks_for(name, config, bridge)}
            self.assertFalse(states['merge_base'])
            self.assertFalse(states['certs'])
            self.assertTrue(states['xdelta'])
            for name_part in ('project.json', 'workspace-manifest.json', 'original_code.bin', 'original_exheader.bin'):
                (base / name_part).touch()
            (keys / 'certs.bin').touch()
            states = {key: ok for key, ok, _ in guidance.checks_for(name, config, bridge)}
            self.assertTrue(states['merge_base'])
            self.assertTrue(states['certs'])
            self.assertNotIn('merge_update', states)
            config['repack_format'] = 'CCI'
            self.assertNotIn('certs', {key for key, _, _ in guidance.checks_for(name, config, bridge)})

    def test_title_id_requires_sixteen_hex_digits(self):
        name = '설치 ZIP 만들기 · 제작자용'
        for value, expected in [('00040000001CA900', True), ('not-a-title-id!!', False), ('', False)]:
            row = guidance.checks_for(name, {'title_id': value}, 'missing')[0]
            self.assertEqual(row[:2], ('title_id', expected))


if __name__ == '__main__':
    unittest.main()

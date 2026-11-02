import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from korean_patch_manager import core


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.original = self.root / 'original'; self.original.mkdir()
        self.edited = self.root / 'edited'; self.edited.mkdir()
        self.cfg = {'title_id': '0004000000123456', 'original': str(self.original), 'edited': str(self.edited), 'region': 'KOR', 'version': '1.0'}
        for root, content in ((self.original, b'old'), (self.edited, b'new')):
            (root / 'a').mkdir(); (root / 'a' / 'text.bin').write_bytes(content)
            (root / 'same.bin').write_bytes(b'same')
        (self.edited / 'new.bin').write_bytes(b'new file')

    def tearDown(self): self.tmp.cleanup()

    def test_compare_and_archives(self):
        report = core.build(self.cfg, self.root / 'output')
        self.assertEqual([x['path'] for x in report['changes']], ['a/text.bin', 'new.bin'])
        self.assertIsNone(report['changes'][1]['original_sha256'])
        for filename, prefix in [('luma.zip', 'luma/titles'), ('azahar.zip', 'load/mods')]:
            with zipfile.ZipFile(self.root / 'output' / filename) as z:
                self.assertEqual(z.read(f'{prefix}/0004000000123456/romfs/a/text.bin'), b'new')
                self.assertNotIn(f'{prefix}/0004000000123456/romfs/same.bin', z.namelist())
        with self.assertRaises(ValueError): core.build(self.cfg, self.root / 'output')

    def test_deletion_and_overlapping_inputs_are_rejected(self):
        (self.edited / 'same.bin').unlink()
        with self.assertRaisesRegex(ValueError, '삭제'): core.build(self.cfg, self.root / 'out')
        with self.assertRaises(ValueError): core.compare(self.original, self.original / 'a')

    def test_ips_validation_and_paths(self):
        ips = self.root / 'code.ips'; ips.write_bytes(b'PATCH' + b'\x00\x00\x01\x00\x02AB' + b'EOF')
        code = self.root / 'code.bin'; code.write_bytes(b'012345')
        self.cfg.update(code_ips=str(ips), original_code=str(code))
        self.assertEqual(core.validate_ips(ips, 6), 1)
        core.build(self.cfg, self.root / 'out')
        with zipfile.ZipFile(self.root / 'out' / 'azahar.zip') as z:
            self.assertIn('load/mods/0004000000123456/exefs/code.ips', z.namelist())
        with self.assertRaises(ValueError): core.validate_ips(ips, 2)
        ips.write_bytes(b'PATCH' + b'\x00\x00\x01\x00\x02AB' * 2 + b'EOF')
        with self.assertRaisesRegex(ValueError, '겹치는'): core.validate_ips(ips)
        ips.write_bytes(b'PATCH\x00');
        with self.assertRaises(ValueError): core.validate_ips(ips)

    def test_separate_ips_conflicts(self):
        first = self.root / 'first.ips'; second = self.root / 'second.ips'
        first.write_bytes(b'PATCH' + b'\x00\x00\x01\x00\x02AB' + b'EOF')
        second.write_bytes(b'PATCH' + b'\x00\x00\x02\x00\x02CD' + b'EOF')
        self.assertEqual(core.ips_conflicts([first, second])[0][2:], (2, 3))
        second.write_bytes(b'PATCH' + b'\x00\x00\x03\x00\x02CD' + b'EOF')
        self.assertEqual(core.ips_conflicts([first, second]), [])

    def test_azahar_deployment_does_not_overwrite(self):
        user_dir = self.root / 'user'; user_dir.mkdir()
        target = core.deploy_azahar(self.cfg, user_dir)
        self.assertEqual((target / 'romfs/a/text.bin').read_bytes(), b'new')
        (target / 'romfs/other.bin').write_bytes(b'other mod')
        core.deploy_azahar(self.cfg, user_dir)
        self.assertEqual((target / 'romfs/other.bin').read_bytes(), b'other mod')
        (target / 'romfs/a/text.bin').write_bytes(b'conflict')
        with self.assertRaisesRegex(ValueError, '충돌'): core.deploy_azahar(self.cfg, user_dir)
        self.assertEqual((target / 'romfs/a/text.bin').read_bytes(), b'conflict')

    def test_azahar_existing_identical_code_is_idempotent(self):
        ips=self.root/'code.ips'; ips.write_bytes(b'PATCH' + b'\x00\x00\x01\x00\x01A' + b'EOF')
        self.cfg['code_ips']=str(ips)
        user=self.root/'user'; user.mkdir()
        target=core.deploy_azahar(self.cfg,user)
        core.deploy_azahar(self.cfg,user)
        self.assertEqual((target/'exefs/code.ips').read_bytes(),ips.read_bytes())
        (target/'exefs/code.ips').write_bytes(b'other patch')
        with self.assertRaisesRegex(ValueError,'충돌'):
            core.deploy_azahar(self.cfg,user)

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows 정션 시험')
    def test_azahar_rejects_actual_windows_junction(self):
        import os, subprocess
        user=self.root/'user'; target=self.root/'junction-target'
        user.mkdir(); target.mkdir(); (user/'load').mkdir()
        junction=user/'load/mods'
        env=os.environ.copy(); env['KPM_TEST_LINK']=str(junction); env['KPM_TEST_TARGET']=str(target)
        subprocess.run(['powershell','-NoProfile','-Command','New-Item -ItemType Junction -Path $env:KPM_TEST_LINK -Target $env:KPM_TEST_TARGET | Out-Null'],env=env,check=True,capture_output=True,timeout=15)
        try:
            with self.assertRaisesRegex(ValueError, '정션'): core.deploy_azahar(self.cfg,user)
            self.assertEqual(list(target.iterdir()),[])
        finally:
            os.rmdir(junction)

    def test_delta_roundtrip_and_new_files(self):
        def fake_run(args, **kwargs):
            if args[1] == '-e': Path(args[-1]).write_bytes(Path(args[-2]).read_bytes())
            else: Path(args[-1]).write_bytes(Path(args[-2]).read_bytes())
        with mock.patch('korean_patch_manager.core.subprocess.run', side_effect=fake_run):
            core.xdelta_bundle(self.cfg, self.root / 'p.zip', 'xdelta')
        with zipfile.ZipFile(self.root / 'p.zip') as z:
            self.assertEqual(z.read('patches/a/text.bin.xdelta'), b'new')
        with mock.patch('korean_patch_manager.core.subprocess.run', side_effect=fake_run):
            core.apply_xdelta_bundle(self.root / 'p.zip', self.original, self.root / 'restored', 'xdelta')
        self.assertEqual((self.root / 'restored/romfs/a/text.bin').read_bytes(), b'new')
        self.assertEqual((self.root / 'restored/romfs/new.bin').read_bytes(), b'new file')
        core.restored_packages(self.root / 'restored', self.root / 'packages')
        with zipfile.ZipFile(self.root / 'packages/luma.zip') as z:
            self.assertEqual(z.read('luma/titles/0004000000123456/romfs/new.bin'), b'new file')
        (self.original / 'a/text.bin').write_bytes(b'wrong')
        with mock.patch('korean_patch_manager.core.subprocess.run', side_effect=fake_run):
            with self.assertRaisesRegex(ValueError, '해시 불일치'):
                core.apply_xdelta_bundle(self.root / 'p.zip', self.original, self.root / 'bad', 'xdelta')
        self.assertFalse((self.root / 'bad').exists())


    def test_romforge_layout_and_added_file_guard(self):
        with self.assertRaisesRegex(ValueError, '추가된'): core.build_romforge(self.cfg, self.root / 'rf.zip')
        (self.edited / 'new.bin').unlink()
        core.build_romforge(self.cfg, self.root / 'rf.zip')
        with zipfile.ZipFile(self.root / 'rf.zip') as z:
            self.assertEqual(z.namelist(), ['romfs/a/text.bin'])
            self.assertEqual(z.read('romfs/a/text.bin'), b'new')

    def test_xdelta_rejects_path_escape_and_code_only_works(self):
        import json
        bad = {'title_id':'0004000000123456','changes':[{'path':'../outside','original_sha256':None,'modified_sha256':'bad'}]}
        with zipfile.ZipFile(self.root / 'bad.zip', 'w') as z: z.writestr('manifest.json', json.dumps(bad))
        with self.assertRaisesRegex(ValueError, '경로'):
            core.apply_xdelta_bundle(self.root / 'bad.zip', self.original, self.root / 'bad-output', 'xdelta')
        self.assertFalse((self.root / 'bad-output').exists())
        (self.edited / 'a/text.bin').write_bytes(b'old')
        (self.edited / 'new.bin').unlink()
        ips=self.root / 'code.ips'; ips.write_bytes(b'PATCH' + b'\x00\x00\x01\x00\x01A' + b'EOF')
        self.cfg['code_ips']=str(ips)
        core.xdelta_bundle(self.cfg, self.root / 'code.zip', 'unused')
        core.apply_xdelta_bundle(self.root / 'code.zip', self.original, self.root / 'code-out', 'unused')
        core.restored_packages(self.root / 'code-out', self.root / 'code-packages')
        with zipfile.ZipFile(self.root / 'code-packages/azahar.zip') as z:
            self.assertEqual(z.read('load/mods/0004000000123456/exefs/code.ips'), ips.read_bytes())

    def test_bad_title(self):
        self.cfg['title_id'] = '../bad'
        with self.assertRaises(ValueError): core.inspect(self.cfg)


if __name__ == '__main__': unittest.main()

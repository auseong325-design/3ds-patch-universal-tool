import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from korean_patch_manager import core, merge, romforge

BRIDGE=os.environ.get('KPM_TEST_ROMFORGE_BRIDGE')
FIXTURE=os.environ.get('KPM_TEST_ROMFORGE_FIXTURE')
XDELTA=os.environ.get('KPM_TEST_XDELTA')

@unittest.skipUnless(BRIDGE and FIXTURE and XDELTA, 'RomForge와 xdelta 통합 테스트 도구가 필요합니다')
class MergeIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        subprocess.run(['dotnet', FIXTURE, str(self.root)], check=True, capture_output=True)
        romforge.setup_workspace(self.root/'game.cci',self.root/'keys',self.root/'base-ws',BRIDGE)
        romforge.setup_workspace(self.root/'update.cci',self.root/'keys',self.root/'update-ws',BRIDGE)
        self.patch=self.root/'patch'; self.patch.mkdir(); (self.patch/'test.bin').write_bytes(b'Korean text')
    def tearDown(self): self.tmp.cleanup()
    def test_create_and_repack_components(self):
        r=self.root
        manifest=merge.build_bundle(r/'base-ws',r/'update-ws',self.patch,r/'merge.zip',XDELTA,ips=r/'code.ips',banner=r/'banner_ko.bin')
        self.assertEqual(len(manifest['changes']),1)
        self.assertEqual(set(manifest['components']),{'code','exheader','banner'})
        with zipfile.ZipFile(r/'merge.zip') as z:
            self.assertNotIn('exefs/code.bin',z.namelist())
            self.assertIn('components/code.xdelta',z.namelist())
        for fmt in ('CCI','CIA','ZCCI'):
            result=merge.repack_bundle(r/'merge.zip',r/'base-ws',r/'keys',r/('merged.'+fmt.lower()),fmt,BRIDGE,XDELTA,cia_version='0')
            self.assertEqual(result['checked_files'],1); self.assertTrue(result['code_checked'])
        (r/'base-ws/original_code.bin').write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError,'원본 .*해시'):
            merge.repack_bundle(r/'merge.zip',r/'base-ws',r/'keys',r/'bad.cci','CCI',BRIDGE,XDELTA)
        self.assertFalse((r/'bad.cci').exists())
    def test_unsupported_header_change_is_rejected(self):
        r=self.root; h=r/'update-ws/original_exheader.bin'; data=bytearray(h.read_bytes()); data[0x11]^=1; h.write_bytes(data)
        record=r/'update-ws/workspace-manifest.json'; baseline=json.loads(record.read_text(encoding='utf-8')); baseline['components']['exheader']=core.sha256(h); record.write_text(json.dumps(baseline),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'메모리 배치'):
            merge.build_bundle(r/'base-ws',r/'update-ws',self.patch,r/'bad.zip',XDELTA)
        self.assertFalse((r/'bad.zip').exists())
    def test_modified_or_deleted_workspace_inputs_rejected(self):
        r=self.root; source=r/'update-ws/original/test.bin'; source.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'업데이트.*해시'):
            merge.build_bundle(r/'base-ws',r/'update-ws',self.patch,r/'bad.zip',XDELTA)
        source.unlink()
        with self.assertRaisesRegex(ValueError,'파일 목록'):
            merge.build_bundle(r/'base-ws',r/'update-ws',self.patch,r/'bad.zip',XDELTA)
        self.assertFalse((r/'bad.zip').exists())

    def test_optional_code_ips_can_be_omitted(self):
        r=self.root
        m=merge.build_bundle(r/'base-ws',r/'update-ws',None,r/'update-only.zip',XDELTA)
        self.assertFalse(m['includes_ips'])
        self.assertEqual(m['components']['code']['modified_sha256'],core.sha256(r/'update-ws/original_code.bin'))
        result=merge.repack_bundle(r/'update-only.zip',r/'base-ws',r/'keys',r/'update-only.cci','CCI',BRIDGE,XDELTA)
        self.assertTrue(result['code_checked'])

    def test_tampered_delta_is_rejected(self):
        r=self.root
        merge.build_bundle(r/'base-ws',r/'update-ws',self.patch,r/'good.zip',XDELTA)
        with zipfile.ZipFile(r/'good.zip') as src, zipfile.ZipFile(r/'bad.zip','w') as dst:
            for item in src.infolist(): dst.writestr(item, b'not a delta' if item.filename=='components/code.xdelta' else src.read(item))
        with self.assertRaises(subprocess.CalledProcessError):
            merge.repack_bundle(r/'bad.zip',r/'base-ws',r/'keys',r/'bad.cci','CCI',BRIDGE,XDELTA)
        self.assertFalse((r/'bad.cci').exists())

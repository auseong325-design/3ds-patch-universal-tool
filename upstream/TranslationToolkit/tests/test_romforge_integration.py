import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from korean_patch_manager import romforge, core

BRIDGE=os.environ.get('KPM_TEST_ROMFORGE_BRIDGE')
FIXTURE=os.environ.get('KPM_TEST_ROMFORGE_FIXTURE')

@unittest.skipUnless(BRIDGE and FIXTURE, 'RomForge 테스트 브리지와 합성 게임 생성기가 필요합니다')
class RomForgeIntegration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        subprocess.run(['dotnet', FIXTURE, str(self.root)], check=True, capture_output=True)
        r=self.root
        self.cfg={'title_id':'0004000000123456','original':str(r/'original'),'edited':str(r/'edited'),
                  'original_rom':str(r/'game.cci'),'code_ips':str(r/'code.ips'),'original_code':str(r/'code.bin'),'cia_version':'1042'}
    def tearDown(self): self.temp.cleanup()

    def test_workspace_extract_then_repack(self):
        import json
        result=romforge.setup_workspace(self.root/'game.cci',self.root/'keys',self.root/'workspace',BRIDGE)
        cfg=result['project']
        self.assertEqual(cfg['title_id'], '0004000000123456')
        self.assertEqual((self.root/'workspace/original/test.bin').read_bytes(),b'original-data')
        self.assertEqual((self.root/'workspace/edited/test.bin').read_bytes(),b'original-data')
        self.assertEqual((self.root/'workspace/original_code.bin').read_bytes(),(self.root/'code.bin').read_bytes())
        self.assertEqual(json.loads((self.root/'workspace/project.json').read_text(encoding='utf-8'))['original'],cfg['original'])
        (self.root/'workspace/edited/test.bin').write_bytes(b'translated-data')
        cfg['code_ips']=str(self.root/'code.ips')
        out=romforge.repack(cfg,self.root/'keys',self.root/'workspace-result.cci','CCI',BRIDGE)
        self.assertEqual(out['checked_files'],1)
        with self.assertRaisesRegex(ValueError,'이미 있습니다'):
            romforge.setup_workspace(self.root/'game.cci',self.root/'keys',self.root/'workspace',BRIDGE)

    def test_cci_cia_zcci_end_to_end_and_zcci_input(self):
        before=core.sha256(self.root/'game.cci')
        for fmt in ('CCI','CIA','ZCCI'):
            result=romforge.repack(self.cfg,self.root/'keys',self.root/('result.'+fmt.lower()),fmt,BRIDGE)
            self.assertEqual(result['checked_files'],1); self.assertTrue(result['code_checked'])
            self.assertEqual(core.sha256(result['output']),result['sha256'])
        self.assertEqual(core.sha256(self.root/'game.cci'),before)
        from korean_patch_manager import metadata
        self.assertEqual(metadata.identify(self.root/'result.cia')['title_version'],1042)
        self.cfg['original_rom']=str(self.root/'result.zcci')
        self.assertEqual(core.inspect(self.cfg)['rom_metadata']['format'],'ZCCI')
        with self.assertRaisesRegex(RuntimeError,'해시 불일치'):
            romforge.repack(self.cfg,self.root/'keys',self.root/'second.cci','CCI',BRIDGE)
        self.assertFalse((self.root/'second.cci').exists())

    def test_cia_version_required_and_preserved(self):
        from korean_patch_manager import metadata
        cfg=dict(self.cfg); cfg['cia_version']=''
        with self.assertRaisesRegex(RuntimeError,'TMD 버전'):
            romforge.repack(cfg,self.root/'keys',self.root/'missing.cia','CIA',BRIDGE)
        self.assertFalse((self.root/'missing.cia').exists())
        romforge.repack(self.cfg,self.root/'keys',self.root/'first.cia','CIA',BRIDGE)
        workspace=romforge.setup_workspace(self.root/'first.cia',self.root/'keys',self.root/'ciaworkspace',BRIDGE)['project']
        (self.root/'ciaworkspace/edited/test.bin').write_bytes(b'another translation')
        romforge.repack(workspace,self.root/'keys',self.root/'second.cia','CIA',BRIDGE)
        self.assertEqual(metadata.identify(self.root/'second.cia')['title_version'],1042)

    def test_wrong_romfs_and_wrong_code_do_not_publish(self):
        (self.root/'original/test.bin').write_bytes(b'wrong original')
        with self.assertRaisesRegex(RuntimeError,'해시 불일치'):
            romforge.repack(self.cfg,self.root/'keys',self.root/'bad.cci','CCI',BRIDGE)
        self.assertFalse((self.root/'bad.cci').exists())
        (self.root/'original/test.bin').write_bytes(b'original-data')
        (self.root/'code.bin').write_bytes(b'wrong code')
        with self.assertRaisesRegex(RuntimeError,'원본 코드 해시'):
            romforge.repack(self.cfg,self.root/'keys',self.root/'bad.cci','CCI',BRIDGE)
        self.assertFalse((self.root/'bad.cci').exists())

    @unittest.skipUnless(os.environ.get('KPM_TEST_XDELTA'), '실제 xdelta가 필요합니다')
    def test_delta_package_direct_repack(self):
        xdelta=os.environ['KPM_TEST_XDELTA']
        core.xdelta_bundle(self.cfg,self.root/'delta.zip',xdelta)
        result=romforge.repack_delta(self.root/'delta.zip',self.root/'original',self.root/'game.cci',self.root/'keys',self.root/'delta-result.cci','CCI',BRIDGE,xdelta)
        self.assertEqual(result['checked_files'],1); self.assertTrue(result['code_checked'])
        other=self.root/'other.cci'; other.write_bytes((self.root/'game.cci').read_bytes()+b'x')
        with self.assertRaisesRegex(ValueError,'게임 파일 해시'):
            romforge.repack_delta(self.root/'delta.zip',self.root/'original',other,self.root/'keys',self.root/'bad.cci','CCI',BRIDGE,xdelta)
        self.assertFalse((self.root/'bad.cci').exists())

    def test_plaintext_cia_needs_no_keys_encrypted_still_does(self):
        import struct
        r=self.root
        romforge.repack(self.cfg,r/'keys',r/'plain.cia','CIA',BRIDGE)
        empty=r/'empty-keys'; empty.mkdir()
        got=romforge.setup_workspace(r/'plain.cia',empty,r/'plain-ws',BRIDGE)
        self.assertEqual(got['project']['title_id'],'0004000000123456')
        raw=bytearray((r/'plain.cia').read_bytes())
        align=lambda x:(x+63)&~63
        header, cert, ticket=struct.unpack_from('<I4xII',raw,0)
        tmd=align(header)+align(cert)+align(ticket)
        chunk=tmd+0x140+0xc4+0x24*64
        raw[chunk+6:chunk+8]=(1).to_bytes(2,'big')
        (r/'marked-encrypted.cia').write_bytes(raw)
        with self.assertRaisesRegex(RuntimeError,'Common Key'):
            romforge.setup_workspace(r/'marked-encrypted.cia',empty,r/'encrypted-ws',BRIDGE)
        self.assertFalse((r/'encrypted-ws').exists())

    def test_missing_keys_title_and_existing_output(self):
        cfg=dict(self.cfg); cfg['title_id']='0004000000123457'
        with self.assertRaisesRegex(RuntimeError,'Title ID'):
            romforge.repack(cfg,self.root/'keys',self.root/'bad.cci','CCI',BRIDGE)
        (self.root/'existing.cci').write_bytes(b'preserve')
        with self.assertRaisesRegex(ValueError,'이미 있습니다'):
            romforge.repack(self.cfg,self.root/'keys',self.root/'existing.cci','CCI',BRIDGE)
        self.assertEqual((self.root/'existing.cci').read_bytes(),b'preserve')
        (self.root/'keys/certs.bin').unlink()
        with self.assertRaisesRegex(RuntimeError,'certs.bin'):
            romforge.repack(self.cfg,self.root/'keys',self.root/'bad.cia','CIA',BRIDGE)
        self.assertFalse((self.root/'bad.cia').exists())

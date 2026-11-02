import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from korean_patch_manager import core, metadata


class MetadataTests(unittest.TestCase):
    def test_ncch_ncsd_cia_and_truncated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); ncch = bytearray(0x200)
            ncch[0x100:0x104] = b'NCCH'; ncch[0x118:0x120] = int('0004000000123456',16).to_bytes(8,'little')
            ncch[0x150:0x15b] = b'CTR-P-TESTX'
            (root/'test.cxi').write_bytes(ncch)
            self.assertEqual(metadata.identify(root/'test.cxi')['title_id'], '0004000000123456')
            ncsd = bytearray(0x400); ncsd[0x100:0x104] = b'NCSD'; ncsd[0x120:0x124] = (1).to_bytes(4,'little'); ncsd[0x124:0x128] = (1).to_bytes(4,'little'); ncsd[0x200:] = ncch
            (root/'test.3ds').write_bytes(ncsd)
            self.assertEqual(metadata.identify(root/'test.3ds')['format'], 'NCSD/CCI')
            cia = bytearray(0x2500); cia[:4] = (0x2020).to_bytes(4,'little'); cia[16:20] = (0x1de).to_bytes(4,'little')
            off = 0x2040; cia[off:off+4] = (0x10004).to_bytes(4,'big'); hdr = off+0x140
            cia[hdr+0x4c:hdr+0x54] = int('0004000000123456',16).to_bytes(8,'big')
            cia[hdr+0x9c:hdr+0x9e] = (1042).to_bytes(2,'big')
            (root/'test.cia').write_bytes(cia)
            self.assertEqual(metadata.identify(root/'test.cia')['version_display'], '1.1.2')
            (root/'bad').write_bytes(cia[:100])
            with self.assertRaises(ValueError): metadata.identify(root/'bad')

    def test_mismatching_title_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/'orig').mkdir(); (root/'edit').mkdir()
            ncch=bytearray(0x200); ncch[0x100:0x104]=b'NCCH'; ncch[0x118:0x120]=(1).to_bytes(8,'little')
            (root/'game.cxi').write_bytes(ncch)
            cfg={'title_id':'0004000000123456','original':str(root/'orig'),'edited':str(root/'edit'),'original_rom':str(root/'game.cxi')}
            with self.assertRaisesRegex(ValueError, '다릅니다'): core.inspect(cfg)


class ActualXdeltaTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('KPM_TEST_XDELTA'), '실제 xdelta 경로가 지정되지 않음')
    def test_real_bundle_apply_and_install_zips(self):
        exe=os.environ['KPM_TEST_XDELTA']
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); a=root/'original'; b=root/'edited'; a.mkdir(); b.mkdir()
            (a/'file.bin').write_bytes(b'A'*10000); (b/'file.bin').write_bytes(b'A'*4000+b'KOREAN'+b'A'*5994)
            (b/'added.bin').write_bytes(b'new')
            cfg={'title_id':'0004000000123456','original':str(a),'edited':str(b)}
            core.xdelta_bundle(cfg, root/'patch.zip', exe)
            core.apply_xdelta_bundle(root/'patch.zip', a, root/'restored', exe)
            self.assertEqual((root/'restored/romfs/file.bin').read_bytes(), (b/'file.bin').read_bytes())
            core.restored_packages(root/'restored',root/'packages')
            with zipfile.ZipFile(root/'packages/azahar.zip') as z:
                self.assertEqual(z.read('load/mods/0004000000123456/romfs/added.bin'),b'new')
            (a/'file.bin').write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError, '해시 불일치'): core.apply_xdelta_bundle(root/'patch.zip',a,root/'bad',exe)


if __name__ == '__main__': unittest.main()

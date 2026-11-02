import tempfile
import threading
import unittest
from pathlib import Path
from korean_patch_manager import core

try:
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer
except ImportError:
    FTPServer = None


@unittest.skipUnless(FTPServer, '선택 테스트 의존성 pyftpdlib가 없음')
class FTPIntegration(unittest.TestCase):
    def test_local_ftp_upload_and_existing_title_guard(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); remote = root/'sd'; remote.mkdir(); original=root/'original'; original.mkdir(); edited=root/'edited'; edited.mkdir()
            (original/'test.bin').write_bytes(b'old'); (edited/'test.bin').write_bytes(b'new')
            authorizer=DummyAuthorizer(); authorizer.add_user('tester','testpass',str(remote),perm='elradfmwMT')
            class Handler(FTPHandler): pass
            Handler.authorizer=authorizer
            server=FTPServer(('127.0.0.1',0),Handler); port=server.socket.getsockname()[1]
            thread=threading.Thread(target=server.serve_forever,kwargs={'timeout':0.05,'blocking':True,'handle_exit':False},daemon=True); thread.start()
            try:
                cfg={'title_id':'0004000000123456','original':str(original),'edited':str(edited)}
                self.assertEqual(core.deploy_ftp(cfg,'127.0.0.1',port,'tester','testpass'),1)
                self.assertEqual((remote/'luma/titles/0004000000123456/romfs/test.bin').read_bytes(),b'new')
                with self.assertRaisesRegex(ValueError, '이미 있습니다'):
                    core.deploy_ftp(cfg,'127.0.0.1',port,'tester','testpass')
            finally:
                server.close_all(); thread.join(timeout=2)


if __name__ == '__main__': unittest.main()

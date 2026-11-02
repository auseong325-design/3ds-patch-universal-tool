"""Optional pinned download of the upstream Windows x64 xdelta binary."""
import hashlib
import io
import os
import platform
import tempfile
import urllib.request
import zipfile
from pathlib import Path

URL = 'https://github.com/jmacd/xdelta/releases/download/v3.2.2/xdelta3-3.2.2-windows-x86_64.zip'
SHA256 = 'd48f7651d86b78fdca397057b46601f89131f37219675df736db70af44ec3379'
ENTRY = 'xdelta3-3.2.2-windows-x86_64/xdelta3.exe'


def install_xdelta(destination):
    if os.name != 'nt' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise ValueError('자동 설치는 Windows x64만 지원합니다. 해당 플랫폼의 xdelta를 직접 선택하세요.')
    dest = Path(destination)
    if dest.exists(): raise ValueError(f'파일이 이미 있습니다: {dest}')
    request = urllib.request.Request(URL, headers={'User-Agent':'3DS-Korean-Patch-Manager'})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('xdelta 다운로드의 SHA-256이 예상값과 다릅니다.')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = [item for item in z.infolist() if item.filename == ENTRY]
        if len(infos) != 1 or infos[0].file_size > 10 * 1024 * 1024:
            raise ValueError('xdelta 압축 파일 내용이 올바르지 않습니다.')
        binary = z.read(infos[0])
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open('xb') as out: out.write(binary)
    return dest

"""Read and validate the owner's decrypted Korean DLC CIA, without installing it."""
import hashlib,struct
from pathlib import Path
from .patchcore import PatchError,sha256_file
from .ctr_dump import _align,_u32,_u64
from .rom_container import romfs_files,extent_sha

DLC_TID=0x0004008C00167C00
DLC_DIR='_ko_dlc'

class Content:
    def __init__(self,owner,record):
        self.owner=owner;self.path=owner.path;self.f=owner.f
        self.ncch=record['offset'];h=self._read(self.ncch,0x200)
        self.romfs=self.ncch+_u32(h,0x1b0)*512
        self.end=self.ncch+record['size']
        if (h[0x100:0x104]!=b'NCCH' or _u64(h,0x118)!=DLC_TID
                or _u32(h,0x104)*512!=record['size'] or not h[0x18f]&4
                or not _u32(h,0x1b4) or self.romfs+_u32(h,0x1b4)*512>self.end):
            raise ValueError('Invalid, encrypted or foreign DLC content')
    def _read(self,off,size):return self.owner._read(off,size)

class DLC:
    def __init__(self,path):
        self.path=Path(path).resolve();self.f=None
        try:
            self.f=self.path.open('rb');head=self._read(0,0x2020)
            if _u32(head,0)!=0x2020:raise ValueError('DLC must be a CIA')
            cert,ticket,tmd_size=(_u32(head,o) for o in (8,12,16))
            if cert>1<<20 or ticket>1<<20 or not 0xb04<=tmd_size<=1<<20:raise ValueError('CIA section sizes')
            tmd_start=_align(0x2020,64)+_align(cert,64)+_align(ticket,64)
            tmd=self._read(tmd_start,tmd_size)
            if struct.unpack_from('>I',tmd,0)[0]!=0x10004:raise ValueError('Unsupported TMD signature layout')
            tid=struct.unpack_from('>Q',tmd,0x18c)[0]
            if tid!=DLC_TID:raise ValueError(f'Expected Korean DLC 0004008C00167C00, got {tid:016X}')
            self.version=struct.unpack_from('>H',tmd,0x1dc)[0]
            self.title_type=struct.unpack_from('>I',tmd,0x194)[0]
            count=struct.unpack_from('>H',tmd,0x1de)[0]
            if not 1<=count<=160 or 0xb04+count*48>len(tmd):raise ValueError('Unsupported DLC content count')
            self.records=[];self.files={};self.contents={}
            cursor=tmd_start+_align(tmd_size,64);start=cursor
            for n in range(count):
                off=0xb04+n*48;cid,index,flags,size=struct.unpack_from('>IHHQ',tmd,off)
                if index!=n or flags&0x8000 or not 0x200<=size<=64<<20:raise ValueError('Unsupported DLC index/type/size')
                present=bool(head[0x20+index//8]&(0x80>>(index%8)))
                record=dict(index=index,content_id=cid,flags=flags,size=size,present=present,offset=cursor if present else None)
                self.records.append(record)
                if not present:continue
                if extent_sha(self,cursor,size)!=tmd[off+16:off+48].hex():raise ValueError('DLC content checksum mismatch')
                content=Content(self,record);self.contents[index]=content
                for name,(pos,length) in romfs_files(content).items():
                    if pos<content.ncch or pos+length>content.end:raise ValueError('DLC file outside content')
                    self.files[f'{DLC_DIR}/{index:04x}/{name}']=(pos,length)
                cursor+=size
            if not self.contents or 0 not in self.contents:raise ValueError('DLC catalog content 0 is required')
            if cursor-start!=_u64(head,0x18) or cursor>self.path.stat().st_size:raise ValueError('CIA content length mismatch')
            self.sha256=sha256_file(self.path)
        except (OSError,ValueError,struct.error,PatchError) as exc:
            self.close()
            raise PatchError('D01','DLC 덤프를 읽거나 검증할 수 없습니다.',
                '복호화된 한국판 DLC CIA(0004008C00167C00)를 선택해 주세요. '+str(exc)) from exc
    def _read(self,off,size):
        self.f.seek(off);value=self.f.read(size)
        if len(value)!=size:raise ValueError('Truncated DLC dump')
        return value
    def close(self):
        if self.f:self.f.close()
    def extract(self,root):
        root=Path(root).resolve()
        if (root/DLC_DIR).exists():raise PatchError('D02','이미 DLC 데이터가 들어 있는 본편입니다.','수정하지 않은 본편 덤프를 사용해 주세요.')
        for name,(off,size) in self.files.items():
            target=root/name
            if not target.resolve().is_relative_to(root):raise ValueError('Unsafe DLC output path')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(self._read(off,size))
    def report(self):
        return dict(title_id=f'{DLC_TID:016X}',version=self.version,input_sha256=self.sha256,
            content_count=len(self.records),included_content_indices=list(self.contents),file_count=len(self.files))

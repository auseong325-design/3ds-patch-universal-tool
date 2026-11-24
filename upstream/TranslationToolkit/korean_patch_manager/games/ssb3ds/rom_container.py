"""Read all RomFS file extents and verify the NCCH hashes of a rebuilt image."""
import hashlib,struct
from pathlib import PurePosixPath
from .ctr_dump import _u32,_u64,_align

def romfs_files(dump):
    ivfc=dump._read(dump.romfs,0x60)
    if ivfc[:4]!=b'IVFC':raise ValueError('IVFC magic')
    lv3=dump.romfs+_align(0x60+_u32(ivfc,8),1<<_u32(ivfc,0x4c))
    header=dump._read(lv3,0x28)
    dirs=dump._read(lv3+_u32(header,0x0c),_u32(header,0x10))
    files=dump._read(lv3+_u32(header,0x1c),_u32(header,0x20))
    data=lv3+_u32(header,0x24)
    result,seen_dirs,seen_files={},set(),set()
    def walk(offset,parent):
        if offset in seen_dirs or offset+24>len(dirs):raise ValueError('Cyclic or invalid RomFS directory')
        seen_dirs.add(offset)
        name_size=_u32(dirs,offset+20)
        name=dirs[offset+24:offset+24+name_size].decode('utf-16-le')
        prefix=parent+'/'+name if parent and name else name or parent
        child_file=_u32(dirs,offset+12)
        while child_file!=0xffffffff:
            p=child_file
            if p in seen_files or p+32>len(files):raise ValueError('Cyclic or invalid RomFS file')
            seen_files.add(p)
            length=_u32(files,p+28)
            name=files[p+32:p+32+length].decode('utf-16-le')
            relative=(prefix+'/' if prefix else '')+name
            path=PurePosixPath(relative)
            if path.is_absolute() or '..' in path.parts or ':' in relative or relative in result:
                raise ValueError('Unsafe or duplicate RomFS path')
            absolute=data+_u64(files,p+8);size=_u64(files,p+16)
            if absolute+size>dump.path.stat().st_size:raise ValueError('RomFS file outside dump')
            result[relative]=(absolute,size)
            child_file=_u32(files,p+4)
        child_dir=_u32(dirs,offset+8)
        while child_dir!=0xffffffff:
            walk(child_dir,prefix)
            child_dir=_u32(dirs,child_dir+4)
    walk(0,'')
    return result

def extent_sha(dump,offset,size):
    h=hashlib.sha256();dump.f.seek(offset)
    while size:
        data=dump.f.read(min(size,4<<20))
        if not data:raise ValueError('Truncated extent')
        h.update(data);size-=len(data)
    return h.hexdigest()

def verify_ncch(dump):
    header=dump._read(dump.ncch,0x200)
    checks={}
    for name,start,size,expected in (
        ('exheader',dump.ncch+0x200,_u32(header,0x180),header[0x160:0x180]),
        ('exefs',dump.exefs,_u32(header,0x1a8)*0x200,header[0x1c0:0x1e0]),
        ('romfs',dump.romfs,_u32(header,0x1b8)*0x200,header[0x1e0:0x200])):
        got=extent_sha(dump,start,size)
        if got!=expected.hex():raise ValueError(f'{name} header hash mismatch')
        checks[name]=got
    exh=dump._read(dump.ncch+0x200,0x400)
    if exh[0x0d]&1:raise ValueError('Expected uncompressed rebuilt executable')
    exefs=dump._read(dump.exefs,0x200)
    for index in range(10):
        name=exefs[index*16:index*16+8].rstrip(b'\0').decode('ascii')
        if not name:continue
        offset=_u32(exefs,index*16+8);size=_u32(exefs,index*16+12)
        want=exefs[0x1e0-index*32:0x200-index*32].hex()
        got=extent_sha(dump,dump.exefs+0x200+offset,size)
        if got!=want:raise ValueError(f'ExeFS {name} hash mismatch')
        checks['exefs/'+name]=got
    return checks

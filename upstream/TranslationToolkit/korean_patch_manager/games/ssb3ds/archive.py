"""Read-only DT/LS and resource reader, based on pinned DTLS/Sm4shExplorer sources.

No archive writer. Every extracted extent and decompressed size is checked.
"""
from pathlib import Path
import struct, zlib

def u32(b,o): return struct.unpack_from('<I',b,o)[0]
def cstr(b,o):
    if not 0<=o<len(b): raise ValueError('String offset out of bounds')
    return b[o:b.index(b'\0',o)].decode('ascii')

def crc(name):
    b=bytearray(name.encode('ascii'))
    for i in range(4): b[i]^=255
    return zlib.crc32(b)&0xffffffff

def ls_read(path):
    b=path.read_bytes()
    if b[:2]!=b'of': raise ValueError('LS magic')
    v=struct.unpack_from('<H',b,2)[0]; n=u32(b,4)
    if v not in (1,2): raise ValueError('Unknown LS version')
    stride={1:12,2:16}[v]
    if len(b)!=8+n*stride: raise ValueError('LS length')
    out={}
    for i in range(n):
        k,off,size=struct.unpack_from('<III',b,8+i*stride)
        idx=struct.unpack_from('<H',b,8+i*stride+12)[0] if v==2 else 0
        if k in out: raise ValueError('Duplicate LS CRC')
        out[k]=(idx,off,size)
    return out

def resource_read(path):
    return resource_parse(path.read_bytes())

def resource_parse(src):
    if src[:2]!=b'RF' or u32(src,4)!=128: raise ValueError('RF header')
    dec=zlib.decompress(src[128:])
    if len(dec)!=u32(src,28): raise ValueError('RF decompressed length')
    if len(src)-128!=u32(src,24): raise ValueError('RF compressed length')
    b=src[:128]+dec; names=u32(b,32); pos=names+u32(b,names)*8192+4
    exts=[cstr(b,names+4+u32(b,pos+4+i*4)) for i in range(u32(b,pos))]
    pos=u32(b,12); pos+=u32(b,pos)*8+4; pos+=u32(b,pos)+4
    rows=[]; parts=[]
    for i in range(u32(b,40)):
        off,ne,cmp,dec,stamp,flags=struct.unpack_from('<6I',b,pos+i*24)
        if off==0xbbbbbbbb: continue
        no=ne&0xfffff; at=names+4+no
        if ne&0x800000:
            ref=struct.unpack_from('<H',b,at)[0]
            size=(ref&31)+4; dist=((ref&0xe0)>>6<<8)|(ref>>8)
            name=cstr(b,at-dist)[:size]+cstr(b,at+2)
        else: name=cstr(b,at)
        name+=exts[ne>>24]
        if not name: continue
        depth=flags&255
        if not 1<=depth<=len(parts)+1: raise ValueError(f'RF depth {depth}: {name}')
        parts=parts[:depth-1]+[name]
        rel=''.join(parts)
        if rel.startswith('/') or '..' in Path(rel).parts or ':' in rel: raise ValueError('Unsafe resource path')
        rows.append(dict(path=rel,offset=off,compressed_size=cmp,size=dec,flags=flags,entry_index=i))
    return rows

def _input_path(value):
    return Path(value)

class Archive:
    def __init__(self,base,patch=None):
        self.base=_input_path(base); self.patch=_input_path(patch) if patch else None; self.ls=ls_read(self.base/'ls'); self.handles={}
        if patch is None:
            self.patch_paths=set();return
        b=(self.patch/'patchlist').read_bytes(); n=u32(b,4)
        if len(b)!=128+128*n: raise ValueError('Patchlist length')
        self.patch_paths={cstr(b,128+i*128) for i in range(n)}

    def entries(self,rf):
        partition='data'+rf.name[len('resource'):]
        parents=[]
        for r in resource_read(rf):
            r=dict(r,partition=partition)
            full=partition+'/'+r['path']; depth=r['flags']&255
            parents=parents[:depth-1]
            src=None
            if r['flags']&0x400:
                key=full.rstrip('/')+('/packed' if r['flags']&0x200 else '')
                if key in self.patch_paths: src=('patch',key,0,(self.patch/key).stat().st_size)
                elif crc(key) in self.ls:
                    idx,off,size=self.ls[crc(key)]; src=('dt',idx,off,size)
            if src is None and parents: src=next((s for s in reversed(parents) if s is not None),None)
            parents.append(src)
            if r['flags']&0x4000:
                key=full if full in self.patch_paths else 'data/'+r['path']
                if key not in self.patch_paths: raise ValueError(f'Missing external override {full}')
                src=('loose',key,0,(self.patch/key).stat().st_size)
            r['storage']=src
            if not r['flags']&0x200 and not r['path'].endswith('/'):
                yield r

    def read(self,r):
        src=r['storage']
        if src is None: raise ValueError('Unresolved storage')
        kind,key,start,extent=src
        p=(self.base/'dt' if (self.base/'dt').exists() else self.base/f'dt{key:02d}') if kind=='dt' else self.patch/key
        off=0 if kind=='loose' else r['offset']
        size=extent if kind=='loose' else r['compressed_size']
        if off+size>extent: raise ValueError(f'Extent overflow {off}+{size}>{extent}')
        if p not in self.handles: self.handles[p]=p.open('rb')
        f=self.handles[p]; f.seek(start+off); b=f.read(size)
        if len(b)!=size: raise ValueError('Truncated storage')
        if len(b)>1 and b[0]==0x78 and (b[0]*256+b[1])%31==0: b=zlib.decompress(b)
        if len(b)!=r['size']: raise ValueError(f'Decoded size {len(b)} != {r["size"]}')
        return b

    def close(self):
        for f in self.handles.values(): f.close()

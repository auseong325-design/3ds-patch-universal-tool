"""Bind our service adapter to metadata validated from a user's DLC CIA."""
import struct
from .patchcore import PatchError
from .dlc_dump import DLC_TID

def apply(code,dlc,template,config):
    result=bytearray(code);blob=bytearray(template)
    pos=config['metadata_offset'];capacity=config['capacity']
    if len(dlc.records)>capacity:raise PatchError('D03','DLC 콘텐츠 수가 제작기 지원 범위를 넘습니다.','제작기를 업데이트해 주세요.')
    if any(blob[pos:]):raise ValueError('DLC template must not contain game metadata')
    struct.pack_into('<IQQHHI',blob,pos,len(dlc.records),DLC_TID,dlc.records[0]['size'],dlc.version,0,dlc.title_type)
    pos+=28
    for r in dlc.records:
        struct.pack_into('<IIQ',blob,pos,r['index']|(r['flags']<<16)|(int(r['present'])<<31),r['content_id'],r['size'])
        pos+=16
    off=config['offset']
    if off+len(blob)>len(result) or any(result[off:off+len(blob)]):raise ValueError('DLC code cave is not empty')
    result[off:off+len(blob)]=blob
    for hook in config['hooks']:
        off=hook['offset']
        if result[off:off+4]!=bytes.fromhex(hook['original']):raise ValueError('DLC service call differs')
        result[off:off+4]=bytes.fromhex(hook['new'])
    return bytes(result)

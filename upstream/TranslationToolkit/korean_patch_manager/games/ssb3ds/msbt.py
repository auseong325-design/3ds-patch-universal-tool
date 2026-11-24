"""Lossless MSBT container reader/writer with opaque attributes and control tags.

Only UTF-16 v3 is editable. Unknown sections are preserved, not interpreted.
"""
import struct,unicodedata

class MSBT:
    def __init__(self,b):
        if b[:8]!=b'MsgStdBn': raise ValueError('MSBT magic')
        self.order={b'\xfe\xff':'>',b'\xff\xfe':'<'}[b[8:10]]
        self.codec='utf-16-be' if self.order=='>' else 'utf-16-le'
        self.header=b[:32]; self.sections=[]
        if self.u32(b,18)!=len(b): raise ValueError('MSBT file size')
        if b[12]!=1 or b[13]!=3: raise ValueError('Unsupported encoding/version; preserve raw file')
        p=32
        for _ in range(self.u16(b,14)):
            n=self.u32(b,p+4); end=p+16+n; aligned=(end+15)&~15
            if aligned>len(b): raise ValueError('Section bounds')
            self.sections.append(dict(tag=b[p:p+4],header=b[p:p+16],body=b[p+16:end],pad=b[end:aligned]))
            p=aligned
        self.tail=b[p:]
        self.txt=next(s for s in self.sections if s['tag']==b'TXT2')
        txt=self.txt['body']; n=self.u32(txt,0)
        offsets=[self.u32(txt,4+i*4) for i in range(n)]
        if offsets!=sorted(offsets) or (offsets and offsets[0]<4+4*n): raise ValueError('TXT2 offsets')
        self.text_prefix=txt[:offsets[0]] if offsets else txt
        self.raw=[txt[o:offsets[i+1] if i+1<n else len(txt)] for i,o in enumerate(offsets)]
        self.labels={}
        lbl=next((s['body'] for s in self.sections if s['tag']==b'LBL1'),None)
        if lbl:
            for i in range(self.u32(lbl,0)):
                count,off=struct.unpack_from(self.order+'II',lbl,4+i*8)
                for _ in range(count):
                    length=lbl[off]; name=lbl[off+1:off+1+length].decode('ascii'); off+=1+length
                    idx=self.u32(lbl,off);off+=4
                    if name in self.labels or idx>=n: raise ValueError('Duplicate label or invalid index')
                    self.labels[name]=idx
        self.tokens=[self.tokenize(r) for r in self.raw]

    def u16(self,b,o):return struct.unpack_from(self.order+'H',b,o)[0]
    def u32(self,b,o):return struct.unpack_from(self.order+'I',b,o)[0]

    def tokenize(self,b):
        p=0; text_start=0; tokens=[]
        while p+2<=len(b):
            cp=self.u16(b,p)
            if cp in (0,14,15):
                if p>text_start: tokens.append(dict(type='text',value=b[text_start:p].decode(self.codec)))
                if cp==0:
                    tokens.append(dict(type='terminator_and_padding',raw=b[p:].hex()));return tokens
                end=p+8+self.u16(b,p+6) if cp==14 else p+6
                if end>len(b):raise ValueError('Truncated control tag')
                tokens.append(dict(type='control',raw=b[p:end].hex()))
                p=end;text_start=p
            else:p+=2
        raise ValueError('Missing text terminator')

    def serialize(self,replacements=None,appends=None):
        replacements=replacements or {}; sections=self.sections
        raws=list(self.raw)
        for label,text in replacements.items():
            idx=self.labels[label]
            if any(t['type']=='control' for t in self.tokens[idx]):raise ValueError('Control-bearing edits require token-aware review')
            if any(ord(c)<32 and c!='\n' for c in text): raise ValueError('Unsafe control character')
            if unicodedata.normalize('NFC',text)!=text: raise ValueError('Not NFC')
            raws[idx]=text.encode(self.codec)+b'\0\0'
        for label,text in (appends or {}).items():
            if label in replacements: raise ValueError('Conflicting replacement and append')
            if any(ord(c)<32 and c!='\n' for c in text): raise ValueError('Unsafe control character')
            if unicodedata.normalize('NFC',text)!=text: raise ValueError('Not NFC')
            idx=self.labels[label]
            terminator=bytes.fromhex(self.tokens[idx][-1]['raw'])
            # Append after original text, preserving every existing byte/tag and Ruby span.
            raws[idx]=self.raw[idx][:-len(terminator)]+text.encode(self.codec)+terminator
        if replacements:
            prefix=bytearray(self.text_prefix);off=len(prefix)
            for i,r in enumerate(raws):struct.pack_into(self.order+'I',prefix,4+i*4,off);off+=len(r)
            txt=bytes(prefix)+b''.join(raws)
        else:
            # Rebuild offset table and raw strings; exact untouched prefix retained.
            prefix=bytearray(self.text_prefix);off=len(prefix)
            for i,r in enumerate(raws):struct.pack_into(self.order+'I',prefix,4+i*4,off);off+=len(r)
            txt=bytes(prefix)+b''.join(raws)
        parts=[]
        for s in sections:
            body=txt if s is self.txt else s['body'];head=bytearray(s['header'])
            struct.pack_into(self.order+'I',head,4,len(body))
            needed=(-len(body))%16
            pad=s['pad'] if len(s['pad'])==needed else (s['pad'][:1] or b'\xab')*needed
            parts.append(bytes(head)+body+pad)
        result=b''.join(parts)+self.tail;header=bytearray(self.header)
        struct.pack_into(self.order+'I',header,18,len(header)+len(result))
        return bytes(header)+result

    def serialize_token_replacements(self, replacements=None, segments=None):
        """Replace visible text while retaining every original control token.

        ``segments`` supplies one replacement string for each text token in a
        control-bearing label, in token order.  The concatenation must equal
        the value in ``replacements``.  This keeps ruby/layout control bytes
        opaque while making the translated visible string auditable.
        """
        replacements = replacements or {}
        segments = segments or {}
        raws = list(self.raw)

        def validate(text):
            if any(ord(c) < 32 and c != '\n' for c in text):
                raise ValueError('Unsafe control character')
            if unicodedata.normalize('NFC', text) != text:
                raise ValueError('Not NFC')

        for label, text in replacements.items():
            validate(text)
            idx = self.labels[label]
            tokens = self.tokens[idx]
            text_tokens = [t for t in tokens if t['type'] == 'text']
            control_tokens = [t for t in tokens if t['type'] == 'control']
            if control_tokens:
                parts = segments.get(label)
                if parts is None:
                    raise ValueError(f'Control-bearing edit requires segments: {label}')
                if len(parts) != len(text_tokens) or ''.join(parts) != text:
                    raise ValueError(f'Segment mismatch: {label}')
                for part in parts:
                    validate(part)
                it = iter(parts)
                out = bytearray()
                for token in tokens:
                    if token['type'] == 'text':
                        out.extend(next(it).encode(self.codec))
                    else:
                        out.extend(bytes.fromhex(token['raw']))
                raws[idx] = bytes(out)
            else:
                if label in segments and (len(segments[label]) != 1 or segments[label][0] != text):
                    raise ValueError(f'Unexpected segments for control-free label: {label}')
                raws[idx] = text.encode(self.codec) + b'\0\0'

        prefix = bytearray(self.text_prefix)
        off = len(prefix)
        for i, raw in enumerate(raws):
            struct.pack_into(self.order + 'I', prefix, 4 + i * 4, off)
            off += len(raw)
        txt = bytes(prefix) + b''.join(raws)
        parts = []
        for section in self.sections:
            body = txt if section is self.txt else section['body']
            head = bytearray(section['header'])
            struct.pack_into(self.order + 'I', head, 4, len(body))
            needed = (-len(body)) % 16
            pad = section['pad'] if len(section['pad']) == needed else (section['pad'][:1] or b'\xab') * needed
            parts.append(bytes(head) + body + pad)
        result = b''.join(parts) + self.tail
        header = bytearray(self.header)
        struct.pack_into(self.order + 'I', header, 18, len(header) + len(result))
        return bytes(header) + result

    def serialize_raw_strings(self, raw_by_label=None):
        """Rebuild the file from complete per-message raw strings.

        ``raw_by_label`` maps a label to the FULL raw byte string for that
        message (text segments + control raw bytes + terminator/padding), i.e.
        exactly what ``self.raw[idx]`` holds.  Callers assemble these bytes
        themselves, so this serializer stays opaque about control tags: it only
        rebuilds the TXT2 offset table and leaves every other section, the
        prefix, padding and tail byte-identical.  Labels not supplied keep their
        original raw bytes, so an empty mapping round-trips to the input.
        """
        raw_by_label = raw_by_label or {}
        raws = list(self.raw)
        for label, data in raw_by_label.items():
            if label not in self.labels:
                raise ValueError(f'Unknown label: {label}')
            raws[self.labels[label]] = bytes(data)
        prefix = bytearray(self.text_prefix)
        off = len(prefix)
        for i, r in enumerate(raws):
            struct.pack_into(self.order + 'I', prefix, 4 + i * 4, off)
            off += len(r)
        txt = bytes(prefix) + b''.join(raws)
        parts = []
        for section in self.sections:
            body = txt if section is self.txt else section['body']
            head = bytearray(section['header'])
            struct.pack_into(self.order + 'I', head, 4, len(body))
            needed = (-len(body)) % 16
            pad = section['pad'] if len(section['pad']) == needed else (section['pad'][:1] or b'\xab') * needed
            parts.append(bytes(head) + body + pad)
        result = b''.join(parts) + self.tail
        header = bytearray(self.header)
        struct.pack_into(self.order + 'I', header, 18, len(header) + len(result))
        return bytes(header) + result

    def messages(self):
        for label,idx in self.labels.items():
            yield dict(label=label,index=idx,text=''.join(t['value'] for t in self.tokens[idx] if t['type']=='text'),
                       tokens=self.tokens[idx],raw_hex=self.raw[idx].hex())

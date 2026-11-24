"""Build one decrypted .3ds with Korean text and update 1.1.7 inside its RomFS."""
import hashlib,json,os,shutil,struct,subprocess
from pathlib import Path
from .ctr_dump import Dump,classify,_u32
from .patchcore import PatchError,build_sd,sha256_file
from .rom_container import romfs_files,extent_sha,verify_ncch
from .dlc_dump import DLC
from .dlc_runtime import apply as apply_dlc

APP_NAME='스매시브라더스 3DS 한글 ROM 제작기'
APP_VERSION='0.2.1'
OUTPUT_NAME='SSB3DS_KOR_1.1.7_Korean.3ds'
DLC_OUTPUT_NAME='SSB3DS_KOR_1.1.7_Korean_DLC.3ds'

def apply_ips(code,ips):
    if not ips.startswith(b'PATCH'):raise ValueError('IPS header')
    result=bytearray(code);pos=5;seen=[]
    while ips[pos:pos+3]!=b'EOF':
        if pos+5>len(ips):raise ValueError('Truncated IPS')
        off=int.from_bytes(ips[pos:pos+3],'big');size=int.from_bytes(ips[pos+3:pos+5],'big');pos+=5
        if not size or pos+size>len(ips) or off+size>len(result):raise ValueError('Invalid embedded IPS record')
        if any(off<end and off+size>start for start,end in seen):raise ValueError('Overlapping IPS records')
        result[off:off+size]=ips[pos:pos+size];pos+=size;seen.append((off,off+size))
    if pos+3!=len(ips):raise ValueError('IPS trailer')
    return bytes(result)

def tool_run(tool,args,log,working):
    completed=subprocess.run([str(tool),*map(str,args)],cwd=working,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    output=completed.stdout.decode('utf-8',errors='replace').strip()
    log('3dstool: '+' '.join(str(a) for a in args[:6]))
    if output:log(output)
    if completed.returncode:
        raise PatchError('R10','게임 파일 재구성에 실패했습니다.',
                         '제작_기록.txt의 3dstool 오류를 확인해 주세요. 원본 파일은 변경되지 않았습니다.')

def check_recipe(recipe,tool):
    try:
        manifest=json.loads((recipe/'manifest.json').read_text(encoding='utf-8'))
        for name,want in manifest['files'].items():
            if Path(name).name!=name or sha256_file(recipe/name)!=want:raise ValueError('Recipe hash mismatch')
        config=json.loads((recipe/'standalone.json').read_text(encoding='utf-8'))
        if sha256_file(tool)!=config['tool_sha256']:raise ValueError('3dstool hash mismatch')
        if sha256_file(tool.with_name('ignore_3dstool.txt'))!=config['tool_ignore_sha256']:raise ValueError('Exclusion policy hash mismatch')
        if config['update_dir']!='_ko_update':raise ValueError('Unsupported embedded path')
        return config
    except (OSError,KeyError,ValueError) as exc:
        raise PatchError('R01','제작기 파일이 손상됐거나 일부 파일이 없습니다.','배포 ZIP 전체를 새 폴더에 다시 풀어 주세요.') from exc

def verify_output(path,base,update,rom,rex,patched_code,progress=None,dlc=None):
    progress=progress or (lambda *a:None)
    result=Dump(path)
    try:
        if result.kind!='3DS(카트리지 덤프)' or result.title_id!=base.title_id:raise ValueError('Output identity mismatch')
        checks=verify_ncch(result)
        code_offset,code_size=result.exefs_file('.code')
        if extent_sha(result,code_offset,code_size)!=hashlib.sha256(patched_code).hexdigest():raise ValueError('Embedded code mismatch')
        originals=[(base,'',rom,{'ls','dt'}),(update,'_ko_update/',rex,{'ls','dt','cro.sarc'})]
        files=romfs_files(result);expected={};done=0
        total=sum(len(romfs_files(d)) for d,p,r,changes in originals)+(len(dlc.files) if dlc else 0)
        for source,prefix,patched,changes in originals:
            for name,(off,size) in romfs_files(source).items():
                relative=prefix+name
                if relative not in files:raise ValueError('Missing rebuilt file '+relative)
                new_off,new_size=files[relative]
                if name in changes:
                    local=patched/name;want=sha256_file(local);want_size=local.stat().st_size
                else:
                    want=extent_sha(source,off,size);want_size=size
                if new_size!=want_size or extent_sha(result,new_off,new_size)!=want:
                    raise ValueError('Rebuilt file differs: '+relative)
                expected[relative]=want;done+=1;progress('완성 ROM의 파일 확인',done/total)
        if dlc:
            for name,(off,size) in dlc.files.items():
                want=extent_sha(dlc,off,size)
                if name not in files or files[name][1]!=size or extent_sha(result,*files[name])!=want:
                    raise ValueError('Embedded DLC file differs: '+name)
                expected[name]=want;done+=1;progress('완성 ROM의 DLC 확인',done/total)
        if set(files)!=set(expected):raise ValueError('Unexpected rebuilt files')
        return dict(file_count=len(files),file_hashes=expected,ncch_hashes=checks,code_sha256=checks['exefs/.code'])
    finally:result.close()

def build_rom(cart,update,out,recipe,tool,progress=None,log=None,keep_work=False,dlc_path=None):
    cart,update,out,recipe,tool=(Path(p).resolve() for p in (cart,update,out,recipe,tool))
    prog=progress or (lambda *a:None);log=log or (lambda *a:None)
    config=check_recipe(recipe,tool)
    out=out.resolve();out.mkdir(parents=True,exist_ok=True)
    output_name=DLC_OUTPUT_NAME if dlc_path else OUTPUT_NAME
    target=out/output_name
    if target.exists():raise PatchError('R02','같은 이름의 완성 ROM이 이미 있습니다.','기존 결과를 보관하거나 다른 출력 폴더를 선택해 주세요.')
    original_paths={cart.resolve(),update.resolve()}
    if dlc_path:original_paths.add(Path(dlc_path).resolve())
    if target.resolve() in original_paths:raise PatchError('R03','원본과 출력 경로가 같습니다.','출력 폴더를 바꿔 주세요.')
    if shutil.disk_usage(out).free<12*1024**3:raise PatchError('E20','작업 공간이 부족합니다.','출력 드라이브에 여유 공간 12GB 이상을 준비해 주세요.')
    work=out/'_rom_build_work'
    if work.exists():raise PatchError('R04','이전 작업 폴더가 남아 있습니다.','다른 출력 폴더를 선택해 주세요. 이전 작업을 자동으로 지우지 않습니다.')
    partial=out/(output_name+'.part')
    if partial.exists():raise PatchError('R04','이전 미완성 ROM 파일이 있습니다.','다른 출력 폴더를 선택해 주세요.')
    work.mkdir()
    base=upd=dlc=None
    try:
        prog('원본 지역·버전 확인',0)
        base,upd=classify([cart,update])
        if base.kind!='3DS(카트리지 덤프)':raise PatchError('R05','본편은 .3ds 또는 .cci 카트리지 덤프가 필요합니다.','복호화된 한국판 본편 카트리지 덤프를 선택해 주세요.')
        log('본편·업데이트 원본 SHA-256 기록')
        inputs={'cart':sha256_file(base.path),'update':sha256_file(upd.path)}
        if dlc_path:
            prog('DLC 덤프·콘텐츠 검증',0)
            dlc=DLC(dlc_path);inputs['dlc']=dlc.sha256
            log('한국판 DLC: '+str(len(dlc.contents))+'개 콘텐츠 / '+str(len(dlc.records))+'개 목록')
        head=base._read(0,0x4000)
        partitions=[]
        for i in range(8):
            off=_u32(head,0x120+i*8)*512;size=_u32(head,0x124+i*8)*512
            if off and size:partitions.append((i,off,size))
        if not partitions or partitions[0][0]!=0:raise PatchError('R06','카트리지 파티션 구조를 읽을 수 없습니다.','원본을 다시 덤프해 주세요.')
        prog('게임 실행 파일 분해',0)
        ncsd=work/'ncsd.header'
        argv=['-x','-t','cci','-f',base.path,'--header',ncsd]
        for i,off,size in partitions:argv += [f'--partition{i}',work/f'partition{i}.ncch']
        tool_run(tool,argv,log,work)
        upd_header=upd._read(upd.ncch,0x200)
        upd.copy(upd.ncch,_u32(upd_header,0x104)*512,work/'update.ncch')
        for name,input_file in [('base',work/'partition0.ncch'),('update',work/'update.ncch')]:
            tool_run(tool,['-x','-t','cxi','-f',input_file,'--header',work/(name+'.header'),'--exh',work/(name+'.exheader'),
                '--exefs',work/(name+'.exefs'),'--romfs',work/(name+'.romfs'),'--plain',work/(name+'.plain'),'--logo',work/(name+'.logo')],log,work)
        romfs=work/'romfs';nested=romfs/config['update_dir']
        prog('본편·업데이트 데이터 분해',0)
        tool_run(tool,['-x','-t','romfs','-f',work/'base.romfs','--romfs-dir',romfs],log,work)
        if nested.exists():raise PatchError('R07','이미 변경된 본편 덤프입니다.','수정하지 않은 한국판 원본 덤프를 사용해 주세요.')
        tool_run(tool,['-x','-t','romfs','-f',work/'update.romfs','--romfs-dir',nested],log,work)
        exefs=work/'exefs'
        tool_run(tool,['-x','-u','-t','exefs','-f',work/'update.exefs','--header',work/'exefs.header','--exefs-dir',exefs],log,work)
        raw_code=work/'code.exefs'
        upd.copy(*upd.exefs_file('.code'),raw_code)
        prog('번역·글꼴·Wii U 메뉴 적용',0)
        layers=work/'patched'
        data_report=build_sd(romfs,nested,raw_code,recipe,layers,prog,include_loader=False)
        for layer,dest,names in [('rom',romfs,('ls','dt')),('rex',nested,('ls','dt','cro.sarc'))]:
            for name in names:shutil.copy2(layers/'ssb3ds-ko'/layer/name,dest/name)
        codefile=exefs/'code.bin'
        code=codefile.read_bytes()
        if hashlib.sha256(code).hexdigest()!=config['original_code_sha256']:raise PatchError('R08','실행 코드가 한국판 1.1.7과 다릅니다.','원본 본편과 업데이트 1.1.7을 선택해 주세요.')
        patched=apply_ips(code,(recipe/'embedded.ips').read_bytes())
        if hashlib.sha256(patched).hexdigest()!=config['embedded_code_sha256']:raise PatchError('R08','ROM 내부 경로 패치 검증에 실패했습니다.','제작기 묶음을 다시 풀어 주세요.')
        if dlc:
            dlc.extract(romfs)
            patched=apply_dlc(patched,dlc,(recipe/'dlc_adapter.bin').read_bytes(),config['dlc_adapter'])
        codefile.write_bytes(patched)
        header=bytearray((work/'update.header').read_bytes());base_header=(work/'base.header').read_bytes()
        header[0x108:0x110]=base_header[0x108:0x110]
        header[0x118:0x120]=base_header[0x118:0x120]
        header[0x150:0x160]=base_header[0x150:0x160]
        header[0x18f]=(header[0x18f]|4)&~0x21
        (work/'embedded.header').write_bytes(header)
        exheader=bytearray((work/'update.exheader').read_bytes());exheader[0x0d]&=~1
        (work/'embedded.exheader').write_bytes(exheader)
        prog('한글 ROM 재조립',0)
        tool_run(tool,['-c','-t','exefs','-f',work/'embedded.exefs','--header',work/'exefs.header','--exefs-dir',exefs],log,work)
        tool_run(tool,['-c','-t','romfs','-f',work/'embedded.romfs','--romfs-dir',romfs],log,work)
        argv=['-c','-t','cxi','-f',work/'embedded.ncch','--header',work/'embedded.header','--exh',work/'embedded.exheader',
              '--exefs',work/'embedded.exefs','--romfs',work/'embedded.romfs','--not-encrypt']
        for flag,source in [('--logo',work/'update.logo'),('--plain',work/'update.plain')]:
            if source.exists() and source.stat().st_size:argv += [flag,source]
        tool_run(tool,argv,log,work)
        argv=['-c','-t','cci','-f',partial,'--header',ncsd,'--not-pad']
        for i,off,size in partitions:argv += [f'--partition{i}',work/'embedded.ncch' if i==0 else work/f'partition{i}.ncch']
        tool_run(tool,argv,log,work)
        prog('완성 ROM 검증',0)
        verified=verify_output(partial,base,upd,romfs,nested,patched,prog,dlc)
        final_sha=sha256_file(partial)
        known=config.get('known_build',{})
        expected_rom=known.get('output_sha256') if known.get('input_sha256')==inputs else None
        if expected_rom and final_sha!=expected_rom:
            raise PatchError('R21','만든 ROM이 검증된 시험판과 다릅니다.','제작 기록을 확인해 주세요. 결과를 완성본으로 내보내지 않습니다.')
        preserved={}
        new_rom=Dump(partial)
        try:
            new_header=new_rom._read(0,0x4000)
            for index,off,size in partitions:
                if index==0:continue
                new_off=_u32(new_header,0x120+index*8)*512
                new_size=_u32(new_header,0x124+index*8)*512
                original_hash=extent_sha(base,off,size)
                if new_size!=size or extent_sha(new_rom,new_off,new_size)!=original_hash:raise ValueError('Secondary partition changed')
                preserved[str(index)]=original_hash
        finally:new_rom.close()
        record=dict(result='PASS',format='EMBEDDED_UPDATE_3DS',recipe_version=config['version'],
            input_sha256=inputs,component_inputs=data_report['inputs'],patched_component_outputs=data_report['outputs'],
            output_file=output_name,output_bytes=partial.stat().st_size,output_sha256=final_sha,
            no_external_patch_required=True,update_117_embedded=True,title_id='0004000000167C00',
            preserved_partitions=preserved,verification=verified,
            reference_build_match='PASS' if expected_rom else 'DIFFERENT_INPUT_CONTAINER',
            runtime_validation='NOT_PERFORMED_BY_MAKER',android_validation='NOT_TESTED')
        record['dlc_embedded']=bool(dlc)
        if dlc:
            record['dlc']=dlc.report()
            adapter=config['dlc_adapter'];off=adapter['offset'];length=(recipe/'dlc_adapter.bin').stat().st_size
            record['dlc']['code_changes']=[dict(address=hex(h['address']),offset=hex(h['offset']),
                original=h['original'],new=h['new']) for h in adapter['hooks']]
            record['dlc']['code_changes'].append(dict(address=hex(adapter['address']),offset=hex(off),
                original=bytes(length).hex(),new=patched[off:off+length].hex()))
        (out/'ROM_검증.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        os.replace(partial,target)
        log('완성 ROM 검증 완료: '+final_sha)
        prog('완료',1)
        return target,record
    except (ValueError,struct.error) as exc:
        raise PatchError('R20','완성 ROM 구조 검증에 실패했습니다.',str(exc)) from exc
    finally:
        if base:base.close()
        if upd:upd.close()
        if dlc:dlc.close()
        if not keep_work:
            # This invocation created the folder, and every deletion stays below the output directory.
            if work.resolve().parent!=out or work.name!='_rom_build_work':raise RuntimeError('Unsafe cleanup path')
            shutil.rmtree(work)

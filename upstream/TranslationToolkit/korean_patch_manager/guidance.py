"""Short, task-oriented help for the desktop interface."""
from pathlib import Path

SCENARIOS = {
    '받은 설치 ZIP 사용': {
        'tab': '프로젝트 및 빌드',
        'text': '이미 완성된 Luma 또는 Azahar 설치 ZIP을 받았을 때의 방법입니다.\n\n'
                '준비: 게임, 패치 배포자가 지정한 업데이트, 설치 ZIP.\n'
                '3DS: ZIP의 luma 폴더를 SD 카드 루트에 복사하고 Luma의 Game Patching을 켭니다.\n'
                'Azahar: 배포 안내에 따라 모드 폴더 또는 사용자 데이터 폴더에 풉니다.\n\n'
                '이 과정에는 이 툴이나 키가 필요하지 않습니다. HOME 배너는 바뀌지 않습니다.\n'
                '설치 ZIP은 아래의 xdelta 적용 버튼에서 읽는 패키지가 아닙니다.',
        'checks': (),
        'note': '받은 ZIP의 안내서를 먼저 확인하세요. 파일 위치와 업데이트 조건은 패치마다 다릅니다.',
    },
    '받은 통합 패키지 적용 · 배너 포함 가능': {
        'tab': '업데이트 통합',
        'text': '배포자가 만든 통합 xdelta ZIP을 본편에 적용해 CCI·CIA·ZCCI를 만듭니다. '
                '패키지에 배너가 포함돼 있다면 결과물에도 반영됩니다.\n\n'
                '1. 프로젝트 탭에서 본편 파일을 지정하고, RomForge 탭에서 키 폴더를 고릅니다.\n'
                '2. RomForge 탭의 「원본 게임에서 번역 작업 폴더 준비」를 실행합니다.\n'
                '3. 출력 형식과 xdelta를 지정한 후 통합 탭에서 「통합 패키지를 본편에 적용 및 리팩」을 누릅니다.\n'
                '4. 대화상자에서 받은 통합 ZIP과 결과 파일 위치를 선택합니다. 업데이트 파일은 별도로 필요하지 않습니다.\n\n'
                'CCI는 에뮬레이터용, CIA는 실기 설치용입니다. CIA에는 키와 certs.bin이 필요합니다. '
                '게임마다 공식 업데이트가 통합 본편보다 우선할 수 있습니다.',
        'checks': ('merge_base', 'keys', 'xdelta', 'bridge', 'certs'),
        'note': '경로와 파일 유무만 확인합니다. 패키지·게임의 일치와 실행 여부는 작업 중 또는 별도로 확인합니다.',
    },
    '통합 패키지 만들기 · 제작자용': {
        'tab': '업데이트 통합',
        'text': '본편과 업데이트, 번역 파일을 합칠 수 있는 전용 xdelta ZIP을 만듭니다. '
                '패키지는 게임 파일 자체가 아니며, 받는 사람은 자신의 본편에 적용해야 합니다.\n\n'
                '1. 본편과 업데이트 게임에서 각각 작업 폴더를 준비합니다.\n'
                '2. 통합 탭에서 두 작업 폴더와 번역 RomFS를 선택합니다. 배너와 IPS는 선택 사항입니다.\n'
                '3. xdelta를 준비하고 「본편 기준 통합 xdelta 패키지 생성」을 누릅니다.\n\n'
                '제작자가 배너를 넣지 않으면 HOME 배너는 바뀌지 않습니다. '
                'IPS를 빼면 업데이트 코드를 그대로 쓰며, RomFS에 포함된 별도 파일은 그대로 남습니다.',
        'checks': ('merge_base', 'merge_update', 'xdelta'),
        'note': '경로만 확인합니다. 번역 폴더·배너·IPS는 선택 사항이며 버전 호환성은 제작자가 확인해야 합니다.',
    },
    '설치 ZIP 만들기 · 제작자용': {
        'tab': '프로젝트 및 빌드',
        'text': '원본과 편집된 RomFS 전체를 비교해 Luma와 Azahar용 설치 ZIP을 만듭니다.\n\n'
                '1. 본편 Title ID, 원본 RomFS와 수정 RomFS를 지정합니다.\n'
                '2. 필요하면 code.ips를 지정하고, 빈 출력 폴더를 선택합니다.\n'
                '3. 「변경 분석」으로 확인한 뒤 「Luma / Azahar ZIP 생성」을 누릅니다.\n\n'
                '수정 폴더는 원본 전체의 편집본이어야 합니다. 패치 파일만 든 폴더를 넣으면 삭제로 판단합니다. '
                '이 방식은 HOME 배너를 바꾸지 않습니다.',
        'checks': ('title_id', 'original', 'edited', 'output'),
        'note': '경로와 입력 형태만 확인합니다. 두 폴더의 내용이나 게임 버전은 확인하지 않습니다.',
    },
    '받은 일반 xdelta 패키지 복원': {
        'tab': '프로젝트 및 빌드',
        'text': '이 툴에서 만든 일반 xdelta ZIP을 원본 RomFS에 적용해 설치 ZIP으로 복원합니다.\n\n'
                '1. 패키지 기준에 맞는 원본 RomFS와 xdelta 실행 파일을 지정합니다.\n'
                '2. 「xdelta 적용」을 누르고 받은 ZIP과 복원 위치를 선택합니다.\n'
                '3. 복원된 설치 ZIP을 배포 안내에 따라 사용합니다.\n\n'
                '통합 xdelta ZIP은 이 버튼이 아니라 「업데이트 통합」 탭에서 적용합니다.',
        'checks': ('original', 'xdelta'),
        'note': '게임 파일 또는 원본 코드의 전체 해시는 해당 파일을 지정한 경우에만 추가로 확인합니다.',
    },
}

FIELD_HELP = {
    'title_id': '게임을 구별하는 16자리 ID입니다. 패치를 본편에 적용할 때는 업데이트가 아닌 본편 ID를 사용하세요.',
    'region': '제작자가 안내문에 기록하는 지역입니다. 실제 지역을 자동 판별하지 않습니다.',
    'version': '배포 안내에 표시할 게임·업데이트 버전입니다. 실제 설치된 버전을 자동 검사하지 않습니다.',
    'original': '변경 전 게임에서 추출한 RomFS 전체 폴더입니다. 일반 xdelta 복원에도 사용합니다.',
    'edited': '원본 RomFS 전체를 복사해 번역한 폴더입니다. 변경 파일만 있으면 나머지를 삭제된 파일로 판단합니다.',
    'original_rom': '본편 게임 파일(.cia, .3ds, .cci, .zcci)입니다. 압축 파일은 먼저 풀어 주세요. 작업 폴더 준비와 리팩에 필요합니다.',
    'code_ips': '선택 사항: 실행 코드에 적용할 IPS입니다. 반드시 해당 게임과 업데이트용인지 확인하세요.',
    'original_code': '선택 사항: 압축이 풀린 원본 실행 코드입니다. IPS 범위 검사는 버전이 맞다는 뜻이 아닙니다.',
    'output': '설치 ZIP을 저장할 빈 폴더입니다. 원본·수정 RomFS와 겹치지 않는 곳을 선택하세요.',
    'xdelta': '차분 패키지 생성·복원과 업데이트 통합에 필요합니다. Windows x64에서는 프로젝트 탭에서 설치할 수 있습니다.',
    'keys': 'RomForge 작업에 사용할 폴더입니다. 복호화된 입력과 CCI 출력은 빈 폴더로 가능한 경우가 있습니다. 암호화 입력은 해당 키가, CIA 출력은 필요한 키와 certs.bin이 필요합니다.',
    'cia_version': 'CIA 출력의 TMD 버전 숫자입니다. CIA 입력이면 비워 원본 값을 유지할 수 있습니다. CCI/3DS/ZCCI 입력이면 본편은 보통 0을 지정합니다.',
    'merge_base': '본편 게임에서 「원본 게임에서 번역 작업 폴더 준비」로 만든 폴더입니다. 받은 통합 패키지를 적용할 때도 필요합니다.',
    'merge_update': '업데이트 게임에서 따로 준비한 작업 폴더입니다. 통합 패키지를 만드는 제작자에게 필요하며, 만들어진 패키지를 적용할 때는 필요하지 않습니다.',
    'merge_patch': '선택 사항: 번역으로 바뀐 파일만 담은 RomFS 폴더입니다. 통합 패키지 제작 시 업데이트 위에 덮어씁니다.',
    'merge_ips': '선택 사항: 업데이트 코드용 IPS입니다. 비워 두면 업데이트 코드가 그대로 사용됩니다. 코드 버전의 일치는 별도로 확인하세요.',
    'merge_banner': '선택 사항: HOME 배너를 교체할 banner.bin 파일입니다. 패키지 생성 시 지정해야 결과물에 반영됩니다.',
    'merge_title': '선택 사항: 한국어 본체의 HOME 메뉴에 나올 게임 이름입니다. 본편 아이콘의 한국어 칸만 바꿉니다. 한글은 한국어 본체에서만 보입니다.',
    'merge_icon': '선택 사항: 직접 만든 아이콘(SMDH, 14,016바이트) 파일입니다. 이름 칸과 함께 쓸 수 없습니다. 그림까지 바꿀 때만 사용하세요.',
    'azahar': 'Azahar 사용자 데이터 폴더입니다. load/mods 폴더 자체가 아닙니다. 정션·심볼릭 링크가 포함된 경로는 설치가 거부됩니다.',
    'host': '같은 네트워크에서 실행 중인 3DS FTP 서버의 주소입니다. 기존 타이틀 폴더가 있으면 전송하지 않습니다.',
    'port': '3DS FTP 서버가 표시하는 포트입니다. 기본 입력은 5000입니다.',
    'user': 'FTP 서버가 계정을 요구할 때 입력합니다. 필요 없다면 비워 두세요.',
    'password': 'FTP 서버 비밀번호입니다. 프로젝트 설정 파일에 저장하지 않습니다.',
}

CHECK_LABELS = {
    'merge_base': '본편 작업 폴더', 'merge_update': '업데이트 작업 폴더',
    'keys': '키 폴더', 'xdelta': 'xdelta 실행 파일', 'bridge': 'RomForge 브리지',
    'certs': 'CIA용 certs.bin', 'title_id': '본편 Title ID',
    'original': '원본 RomFS', 'edited': '수정 RomFS', 'output': '출력 폴더',
}


def checks_for(scenario, config, bridge):
    """Report shallow preparation checks, never claim compatibility or readiness."""
    rows = []
    for key in SCENARIOS[scenario]['checks']:
        value = str(config.get(key, '')).strip()
        if key == 'certs':
            if config.get('repack_format', '').upper() != 'CIA':
                continue
            folder = str(config.get('keys', '')).strip()
            ok = bool(folder) and (Path(folder) / 'certs.bin').is_file()
            detail = '파일 있음; 필요한 키는 별도 확인' if ok else '키 폴더에 certs.bin 필요'
        elif key == 'bridge':
            ok = Path(bridge).is_file()
            detail = '파일 있음' if ok else 'RomForge 탭에서 브리지 빌드 필요'
        elif key == 'title_id':
            ok = len(value) == 16 and all(c in '0123456789abcdefABCDEF' for c in value)
            detail = '형식 확인' if ok else '16자리 ID 입력'
        elif key in ('merge_base', 'merge_update'):
            p = Path(value) if value else None
            ok = bool(p and p.is_dir() and all((p / n).is_file() for n in ('project.json', 'workspace-manifest.json', 'original_code.bin', 'original_exheader.bin')))
            detail = '작업 폴더의 기본 파일 확인' if ok else '최신 툴로 작업 폴더 준비'
        else:
            p = Path(value) if value else None
            ok = bool(p and (p.is_file() if key == 'xdelta' else p.is_dir()))
            detail = '경로 확인' if ok else '지정 필요 또는 경로 없음'
        rows.append((key, ok, detail))
    return rows

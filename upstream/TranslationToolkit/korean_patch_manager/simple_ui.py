"""A purpose-first, three-step interface over the existing patch engines."""
import webbrowser
import zipfile
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog
from . import core, runtime, simple

PURPOSES = {
    'merge': ('게임에 패치 넣기', '통합 패치 ZIP을 받은 경우입니다. 패치가 들어간 게임 파일을 만듭니다.'),
    'install': ('패치 설치 방법 보기', '설치용 ZIP을 SD 카드나 Azahar에 넣는 방법을 안내합니다.'),
    'delta': ('xdelta 패치 풀기', '일반 xdelta ZIP을 설치용으로 바꿉니다. 원본 게임 데이터 폴더가 필요합니다.'),
    'build': ('배포용 패치 만들기', '제작자용입니다. 번역한 파일을 Luma·Azahar용 설치 ZIP으로 묶습니다.'),
}
HELP = {
    'game': ('원본 게임 파일', '게임 카드나 설치된 게임은 GodMode9로 파일로 내보낼 수 있습니다.\n'
             '3DS에서 START를 누른 채 부팅해 GodMode9를 연 뒤, 아래 게임 추출 안내를 따르세요.\n'
             'PC로 옮긴 .cia·.cci·.3ds·.zcci를 선택합니다. ZIP·7z는 먼저 압축을 풀어 주세요.\n'
             '패치와 같은 지역·버전의 원본이 필요합니다.', '게임 추출 안내', 'https://3ds.hacks.guide/dumping-titles-and-game-cartridges.html'),
    'keys': ('키 파일은 언제 필요한가요?', '복호화된 원본으로 CCI를 만들 때는 키 폴더를 비워 먼저 시도할 수 있습니다.\n'
             '암호화 입력이나 CIA 출력에는 필요한 키를 본인 기기에서 추출해 준비합니다.\n\n'
             'Lime3DS 보관 저장소의 DumpKeys.gm9를 SD:/gm9/scripts에 넣습니다.\n'
             'GodMode9에서 HOME → Scripts → DumpKeys를 실행합니다.\n'
             'SD:/gm9의 aes_keys.txt와 seeddb.bin을 PC의 키 폴더로 복사합니다.\n'
             '현재 키 로더는 CIA 출력에 common0 또는 commonkey0 항목이 필요합니다.\n'
             'boot9.bin만으로 모든 키를 대신할 수는 없습니다.', 'DumpKeys 원문 · 보관 문서', 'https://github.com/Lime3DS/lime3ds-archive/tree/master/dist/dumpkeys'),
    'certs': ('CIA에 필요한 certs.bin', 'certs.bin은 암호키가 아니라 인증서 묶음입니다.\n'
              'RomForge 원본 앱의 「유틸 → certs.bin 추출」에 본인이 가진 정식 CIA를 넣어 추출합니다.\n'
              '생성된 certs.bin을 aes_keys.txt와 같은 키 폴더에 복사하고, 그 폴더를 선택하세요.\n'
              '이 매니저에는 해당 추출 유틸이 포함되어 있지 않습니다.', 'RomForge 준비 안내', 'https://github.com/sinjunyoung/RomForge/blob/main/docs/installation.md'),
    'boot9': ('boot9.bin이 필요하다는 오류가 나면', 'GodMode9를 실행하고 HOME → Scripts → GM9Megascript를 선택합니다.\n'
              'Dump Options에서 boot9.bin / boot11.bin 덤프 항목을 실행합니다.\n'
              'SD:/gm9/out에 생성된 boot9 파일을 PC로 복사하고, 복사본 이름을 boot9.bin으로 맞춰 키 폴더에 넣습니다.\n\n'
              '이 파일만으로 CIA 출력용 키가 모두 준비되는 것은 아닙니다.\n'
              'aeskeydb.bin은 다른 형식이므로 aes_keys.txt로 이름만 바꿔 사용할 수 없습니다.',
              'GodMode9 원문 스크립트', 'https://github.com/d0k3/GodMode9/blob/master/resources/gm9/scripts/GM9Megascript.gm9'),
    'romfs': ('RomFS 폴더 준비', 'RomFS는 게임 안의 데이터 폴더입니다.\n'
              '전체 모드의 RomForge 리팩에서 게임 파일과 키 폴더를 지정하고 「번역 작업 폴더 준비」를 누릅니다.\n'
              '생성된 original은 원본, edited는 편집할 전체 복사본입니다.\n'
              '일반 xdelta 복원은 배포자가 지정한 기준의 original이 필요합니다.\n'
              '설치 ZIP 제작에는 변경 파일만 든 폴더가 아니라 edited 전체를 선택하세요.', None, None),
    'package': ('어떤 ZIP을 골라야 하나요?', '설치 ZIP: luma 또는 romfs 등의 폴더가 들어 있습니다. 「패치 설치 방법 보기」를 선택하세요.\n'
                '통합 ZIP: 이 툴의 통합 패키지이며 본편에 업데이트·번역·선택한 배너를 적용합니다.\n'
                '일반 xdelta ZIP: 원본 RomFS에서 번역 파일을 복원합니다.\n'
                '받은 파일이 어느 종류인지 배포 안내를 먼저 확인하세요.', None, None),
}


def show_preparation_help(parent, topic='game'):
    window = tk.Toplevel(parent)
    window.title('준비물 도움말'); window.geometry('640x380'); window.minsize(480, 300)
    window.transient(parent)
    frame = ttk.Frame(window, padding=16); frame.pack(fill='both', expand=True)
    selected = tk.StringVar(value=topic)
    box = ttk.Combobox(frame, state='readonly', values=[v[0] for v in HELP.values()])
    box.pack(fill='x', pady=(0, 12))
    text_frame = ttk.Frame(frame); text_frame.pack(fill='both', expand=True)
    text = tk.Text(text_frame, wrap='word', relief='flat', height=12, padx=8, pady=8,
                   font='TkDefaultFont', spacing3=5)
    text.pack(side='left', fill='both', expand=True)
    scroll = ttk.Scrollbar(text_frame, command=text.yview); scroll.pack(side='right', fill='y')
    text.configure(yscrollcommand=scroll.set)
    link = ttk.Button(frame)
    def render(_=None):
        key = list(HELP)[box.current()]
        selected.set(key)
        _, body, label, url = HELP[key]
        text.configure(state='normal'); text.delete('1.0', 'end'); text.insert('1.0', body); text.configure(state='disabled')
        link.pack_forget()
        if url:
            link.configure(text=label, command=lambda: webbrowser.open(url)); link.pack(anchor='w', pady=8)
    box.current(list(HELP).index(topic)); box.bind('<<ComboboxSelected>>', render); render()
    ttk.Button(frame, text='닫기', command=window.destroy).pack(anchor='e')
    return window


class SimplePanel(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.step = 0
        self.purpose = tk.StringVar(value='merge')
        self.fmt = tk.StringVar(value='CCI')
        self.optional = tk.BooleanVar(value=False)
        self.values = {key: tk.StringVar() for key in ('game', 'package', 'output', 'keys', 'original', 'edited', 'title_id', 'ips')}
        self.canvas = tk.Canvas(self, highlightthickness=0, background=app.cget('background'))
        scroll = ttk.Scrollbar(self, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y'); self.canvas.pack(side='left', fill='both', expand=True)
        self.body = ttk.Frame(self.canvas, padding=18)
        self.item = self.canvas.create_window(0, 0, window=self.body, anchor='nw')
        self.body.bind('<Configure>', self.resize)
        self.canvas.bind('<Configure>', self.resize)
        self.render()

    def resize(self, _=None):
        width = max(280, self.canvas.winfo_width())
        self.canvas.itemconfigure(self.item, width=width)
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        for child in self.body.winfo_children():
            if isinstance(child, ttk.Label) and child.cget('wraplength'):
                child.configure(wraplength=max(240, width - 50))

    def label(self, text, style=None):
        widget = ttk.Label(self.body, text=text, wraplength=780, **({'style': style} if style else {}))
        widget.pack(anchor='w', fill='x', pady=(0, 10))
        return widget

    def field(self, key, title, kind='file', topic=None):
        row = ttk.Frame(self.body); row.pack(fill='x', pady=(0, 10))
        ttk.Label(row, text=title).pack(anchor='w', pady=(0, 3))
        line = ttk.Frame(row); line.pack(fill='x')
        entry = ttk.Entry(line, textvariable=self.values[key]); entry.pack(side='left', fill='x', expand=True)
        if kind != 'text':
            ttk.Button(line, text='찾기', command=lambda: self.browse(key, kind)).pack(side='left', padx=5)
        if topic:
            ttk.Button(line, text='준비 방법', command=lambda: self.help(topic)).pack(side='left')

    def browse(self, key, kind):
        if kind == 'dir': value = filedialog.askdirectory(parent=self.app)
        elif kind == 'save':
            ext = '.' + self.fmt.get().lower()
            value = filedialog.asksaveasfilename(parent=self.app, defaultextension=ext, filetypes=[(self.fmt.get(), '*' + ext)])
        else: value = filedialog.askopenfilename(parent=self.app)
        if value: self.values[key].set(value)

    def help(self, topic='package'):
        return show_preparation_help(self.app, topic)

    def render(self):
        for widget in self.body.winfo_children(): widget.destroy()
        self.canvas.yview_moveto(0)
        self.label('간편 모드  ·  ' + (f'{self.step + 1}/3  ' + ('할 일 고르기', '파일 고르기', '시작하기')[self.step] if self.step < 3 else '완료' if self.result_ok else '확인이 필요해요'), 'Info.TLabel')
        purpose = self.purpose.get()
        if self.step == 0:
            self.label('어떤 작업을 할까요?')
            for key, (title, desc) in PURPOSES.items():
                ttk.Radiobutton(self.body, text=title, value=key, variable=self.purpose).pack(anchor='w', pady=(6, 3))
                self.label(desc)
            ttk.Button(self.body, text='다음', command=self.next).pack(anchor='e', pady=10)
            return
        self.label(PURPOSES[purpose][0])
        if self.step == 1:
            if purpose == 'install':
                self.label('준비물은 설치 ZIP과 게임입니다. 업데이트 조건은 패치 배포 안내를 확인하세요.')
                self.label('3DS: ZIP의 luma 폴더를 SD 카드 맨 위에 복사하고 Luma의 Game Patching을 켭니다.\n\n'
                           'Azahar: 배포 안내에 따라 모드 폴더에 romfs와 코드 패치를 복사합니다.\n\n'
                           '이 방식은 HOME 배너를 바꾸지 않습니다. 배너까지 포함하려면 통합 패키지가 필요합니다.')
                self.label('키 파일이나 작업 폴더는 필요하지 않습니다.', 'Success.TLabel')
            elif purpose == 'merge':
                line = ttk.Frame(self.body); line.pack(anchor='w', pady=(0, 10))
                for text, value in (('PC에서 사용', 'CCI'), ('3DS에 설치', 'CIA')):
                    ttk.Radiobutton(line, text=text, value=value, variable=self.fmt, command=self.render).pack(side='left', padx=(0, 15))
                self.field('game', '원본 게임 파일 · 본편', topic='game')
                self.field('package', '통합 패치 ZIP', topic='package')
                self.field('output', '저장할 파일 이름', 'save')
                if self.fmt.get() == 'CIA':
                    self.field('keys', '키 파일이 든 폴더', 'dir', 'keys')
                    ttk.Button(self.body, text='certs.bin 준비 방법', command=lambda: self.help('certs')).pack(anchor='w', pady=(0, 8))
                    self.label('CIA에는 키와 certs.bin이 필요합니다. 통합 CIA의 실기 실행은 별도 확인이 필요합니다.', 'Warning.TLabel')
                else:
                    self.label('복호화된 본편이면 키 없이 먼저 시도할 수 있습니다.', 'Info.TLabel')
                    ttk.Checkbutton(self.body, text='키 파일 직접 선택', variable=self.optional, command=self.render).pack(anchor='w', pady=(0, 8))
                    if self.optional.get(): self.field('keys', '키 폴더', 'dir', 'keys')
            elif purpose == 'delta':
                self.field('original', '원본 게임 데이터 폴더 (RomFS)', 'dir', 'romfs')
                self.field('package', 'xdelta 패치 ZIP', topic='package')
                self.field('output', '저장할 폴더', 'dir')
                self.label('이 작업 자체에는 키가 필요하지 않습니다. 원본 추출에는 필요할 수 있습니다.', 'Info.TLabel')
            else:
                self.field('title_id', '게임 ID (Title ID · 16자리)', 'text')
                self.field('original', '원본 데이터 전체 폴더 (RomFS)', 'dir', 'romfs')
                self.field('edited', '번역한 데이터 전체 폴더 (RomFS)', 'dir', 'romfs')
                self.field('output', '설치 ZIP을 저장할 빈 폴더', 'dir')
                ttk.Checkbutton(self.body, text='코드 패치(IPS)도 넣기', variable=self.optional, command=self.render).pack(anchor='w', pady=(0, 8))
                if self.optional.get(): self.field('ips', '이 게임 버전용 code.ips')
            if purpose in ('merge', 'delta') and not Path(self.app.vars['xdelta'].get()).is_file():
                self.label('xdelta가 아직 준비되지 않았습니다.', 'Warning.TLabel')
                ttk.Button(self.body, text='xdelta 자동 설치 · Windows x64', command=self.app.install_xdelta).pack(anchor='w')
                ttk.Button(self.body, text='설치된 xdelta 선택', command=lambda: self.app.browse('xdelta', 'file')).pack(anchor='w', pady=5)
            self.validation = self.label('파일을 고른 뒤 다음을 누르세요.' if purpose != 'install' else '안내에 따라 설치하면 됩니다.', 'Info.TLabel')
            self.nav('처음으로' if purpose == 'install' else '다음', self.next)
        elif self.step == 2:
            self.label('선택한 내용으로 진행합니다. 원본 파일은 수정하지 않습니다.', 'Info.TLabel')
            values = self.snapshot()
            for key, title in self.required_fields(): self.label(f'{title}: {values[key]}')
            if purpose == 'merge':
                self.label(f"만들 파일: {'PC용 CCI' if self.fmt.get() == 'CCI' else '3DS 설치용 CIA'}")
                self.label('패키지의 60fps·배너 구성이 그대로 적용됩니다. 설치된 공식 업데이트가 우선할 수 있으니 배포 안내를 확인하세요.', 'Warning.TLabel')
            else: self.label('완성된 설치 ZIP은 배포 안내에 따라 설치하세요.')
            self.nav({'merge': '패치 적용', 'delta': '파일 복원', 'build': 'ZIP 만들기'}[purpose], self.execute)
        else:
            self.label(self.result_text, 'Success.TLabel' if self.result_ok else 'Warning.TLabel')
            if not self.result_ok:
                ttk.Button(self.body, text='키·필요 파일 준비 방법', command=lambda: self.help('keys')).pack(anchor='w', pady=8)
                ttk.Button(self.body, text='파일 다시 고르기', command=lambda: self.go(1)).pack(anchor='w')
            ttk.Button(self.body, text='처음으로', command=lambda: self.go(0)).pack(anchor='e', pady=10)

    def go(self, step):
        self.step = step; self.render()

    def nav(self, title, action):
        row = ttk.Frame(self.body); row.pack(fill='x', pady=10)
        ttk.Button(row, text='뒤로', command=lambda: self.go(self.step - 1)).pack(side='left')
        ttk.Button(row, text=title, command=action).pack(side='right')

    def snapshot(self):
        return {key: var.get().strip() for key, var in self.values.items()}

    def required_fields(self):
        return {'merge': [('game', '원본 게임 파일'), ('package', '통합 패치 ZIP'), ('output', '저장할 파일 이름')],
                'delta': [('original', '원본 RomFS'), ('package', 'xdelta ZIP'), ('output', '저장 폴더')],
                'build': [('title_id', '본편 ID'), ('original', '원본 RomFS'), ('edited', '수정 RomFS'), ('output', '출력 폴더')],
                'install': []}[self.purpose.get()] + ([('keys', '키·인증서 폴더')] if self.purpose.get() == 'merge' and self.fmt.get() == 'CIA' else [])

    def next(self):
        if self.step == 0: self.go(1); return
        if self.purpose.get() == 'install': self.go(0); return
        try:
            values = self.snapshot()
            for key, title in self.required_fields():
                if not values[key]: raise ValueError(f'{title}을 선택해 주세요.')
            for key in ('game', 'package') if self.purpose.get() == 'merge' else ('package',) if self.purpose.get() == 'delta' else ():
                if not Path(values[key]).is_file(): raise ValueError('선택한 파일을 찾을 수 없습니다.')
            for key in ('original', 'edited') if self.purpose.get() == 'build' else ('original',) if self.purpose.get() == 'delta' else ():
                if not Path(values[key]).is_dir(): raise ValueError('선택한 RomFS 폴더를 찾을 수 없습니다.')
            if self.purpose.get() in ('merge', 'delta'):
                simple.package_info(values['package'], {'kpm-merge-v1'} if self.purpose.get() == 'merge' else {None, 'kpm-xdelta-v2'})
                if not Path(self.app.vars['xdelta'].get()).is_file(): raise ValueError('xdelta 설치 또는 선택을 먼저 마쳐 주세요.')
            if self.purpose.get() == 'merge':
                if not runtime.bridge_path(Path(__file__).resolve().parents[1]).is_file(): raise ValueError('RomForge 실행 파일이 없습니다. 전체 모드에서 브리지를 준비하세요.')
                if Path(values['output']).suffix.lower() != '.' + self.fmt.get().lower(): raise ValueError('출력 형식에 맞는 파일 이름을 새로 선택하세요.')
                if self.fmt.get() == 'CIA': simple.validate_cia_keys(values['keys'])
            if self.purpose.get() == 'build': core.title_id(values['title_id'])
        except (ValueError, OSError, KeyError, TypeError, RuntimeError, zipfile.BadZipFile) as error:
            self.validation.configure(text=str(error), style='Warning.TLabel'); return
        self.go(2)

    def execute(self):
        values = self.snapshot(); purpose = self.purpose.get(); fmt = self.fmt.get()
        xdelta = self.app.vars['xdelta'].get()
        keys = values['keys'] if fmt == 'CIA' or self.optional.get() else ''
        ips = values['ips'] if self.optional.get() else ''
        bridge = runtime.bridge_path(Path(__file__).resolve().parents[1])
        def task():
            if purpose == 'merge':
                result = simple.apply_merged_game(values['game'], values['package'], values['output'], fmt, keys, bridge, xdelta,
                                                  log=lambda line: self.app.messages.put(('progress', line)))
                return f"패치가 적용된 게임 파일을 만들었습니다.\n{result['output']}\n설치와 실행은 게임별 안내를 확인하세요."
            if purpose == 'delta':
                result = simple.restore_delta(values['original'], values['package'], values['output'], xdelta)
                return f"설치 ZIP 복원 완료\n{result['output']}"
            core.build({'title_id': values['title_id'], 'original': values['original'], 'edited': values['edited'], 'code_ips': ips}, values['output'])
            return f"Luma·Azahar 설치 ZIP 생성 완료\n{values['output']}"
        self.app.run_job('간편 작업', task, on_done=self.finished)

    def finished(self, state, result):
        self.result_ok = state != 'error'
        if self.result_ok: self.result_text = str(result)
        elif any(term in str(result).lower() for term in ('key', '키', 'certs', 'seed')):
            self.result_text = '키 또는 인증서 준비가 필요합니다. 준비 방법을 확인한 뒤 입력으로 돌아가 주세요.\n자세한 오류는 작업 기록에서 볼 수 있습니다.'
        else: self.result_text = '작업이 중단됐습니다. 입력 파일과 저장 위치를 확인해 주세요.\n' + str(result)[:240]
        self.go(3)

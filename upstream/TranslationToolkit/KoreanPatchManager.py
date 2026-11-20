"""Korean desktop front end for Translation Toolkit."""
import json
import os
import queue
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from korean_patch_manager import core, metadata, tools, romforge, merge, guidance, runtime, simple_ui, easy_ui, paint

EASY, SIMPLE, FULL = '초간단 모드', '간편 모드', '전체 모드'


def crisp_text():
    """Ask Windows not to bitmap-stretch the window on scaled displays. Must run before the first window."""
    if os.name == 'nt':
        import ctypes
        try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            try: ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError): pass


class App(tk.Tk):
    def __init__(self):
        crisp_text()
        super().__init__()
        self.title('3DS 한글패치 매니저')
        self.ui_scale = max(1.0, self.winfo_fpixels('1i') / 96.0)
        self.icons = [tk.PhotoImage(master=self, data=paint.app_icon(size, '#3B5BDB', '#2B44AD'), format='png') for size in (64, 32, 16)]
        self.iconphoto(True, *self.icons)
        self.fit_window(860, 660, 620, 520, centre=True)
        self.project = None
        self.messages = queue.Queue()
        self.busy = False
        self.current_job = ''
        self.job_callback = None
        self.mode = tk.StringVar(value=EASY)
        self.details = tk.BooleanVar(value=False)
        self.vars = {k: tk.StringVar() for k in ('title_id', 'region', 'version', 'original', 'edited',
                     'original_rom', 'code_ips', 'original_code', 'output', 'azahar', 'xdelta', 'host', 'port', 'user', 'password', 'keys', 'repack_format', 'cia_version', 'merge_base', 'merge_update', 'merge_patch', 'merge_ips', 'merge_banner')}
        self.vars['port'].set('5000')
        self.vars['repack_format'].set('CCI')
        local_xdelta = Path(__file__).resolve().parent / 'tools' / 'xdelta-3.2.2' / 'xdelta3.exe'
        if local_xdelta.is_file(): self.vars['xdelta'].set(str(local_xdelta))
        self.make_ui()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.poll_timer = self.after(100, self.poll)

    def fit_window(self, width, height, min_width, min_height, centre=False):
        """Sizes are given for a 100% display; they follow the display scale and never exceed the screen."""
        s = self.ui_scale
        room_w, room_h = self.winfo_screenwidth() - int(40 * s), self.winfo_screenheight() - int(100 * s)
        width, height = min(int(width * s), room_w), min(int(height * s), room_h)
        self.minsize(min(int(min_width * s), width), min(int(min_height * s), height))
        self.update_idletasks()
        if not centre:
            if self.winfo_width() >= width and self.winfo_height() >= height:
                return
            width, height = max(width, self.winfo_width()), max(height, self.winfo_height())
        self.geometry(f'{width}x{height}+{max(0, (self.winfo_screenwidth() - width) // 2)}+{max(0, (room_h - height) // 2)}')

    def dark_title_bar(self, dark):
        if os.name != 'nt':
            return
        import ctypes
        try:
            value = ctypes.c_int(1 if dark else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(int(self.frame(), 16), 20, ctypes.byref(value), ctypes.sizeof(value))
        except (AttributeError, OSError, ValueError, tk.TclError):
            pass

    def close(self):
        if (self.busy or self.easy_panel.busy) and not messagebox.askyesno(
                '작업 중', '아직 작업이 끝나지 않았습니다.\n지금 닫으면 만들던 임시 파일이 남을 수 있습니다. 닫을까요?', parent=self):
            return
        self.destroy()

    def destroy(self):
        if getattr(self, 'poll_timer', None):
            self.after_cancel(self.poll_timer)
            self.poll_timer = None
        super().destroy()

    def make_ui(self):
        style = ttk.Style(self)
        style.configure('Info.TLabel', foreground='#245e91')
        style.configure('Success.TLabel', foreground='#267044')
        style.configure('Warning.TLabel', foreground='#965800')
        style.configure('Error.TLabel', foreground='#ac3333')
        outer = self.outer = ttk.Frame(self, padding=12)
        header = ttk.Frame(outer); header.pack(fill='x')
        self.project_buttons = ttk.Frame(header)
        self.project_buttons.pack(side='left')
        for label, action in [('새 프로젝트', self.new), ('열기', self.open), ('저장', self.save), ('다른 이름 저장', lambda: self.save(True))]:
            ttk.Button(self.project_buttons, text=label, command=action).pack(side='left', padx=(0, 6))
        self.legacy_button = ttk.Button(header, text='기존 Toolkit CLI 실행', command=self.legacy)
        self.legacy_button.pack(side='right')
        ttk.Button(header, text='도움말  F1', command=self.show_help).pack(side='right', padx=(0, 8))
        ttk.Combobox(header, textvariable=self.mode, values=(EASY, SIMPLE, FULL), state='readonly', width=12).pack(side='right', padx=(0, 8))
        ttk.Button(header, text='← 초간단 모드', command=lambda: self.mode.set(EASY)).pack(side='right', padx=(0, 8))
        self.tab_hint = ttk.Label(outer, text='', style='Info.TLabel', wraplength=900)
        self.tab_hint.pack(anchor='w', pady=6)
        book = self.book = ttk.Notebook(outer)
        self.tab_frames = {}
        start = self.scroll_tab(book, '처음 시작')
        start.columnconfigure(0, weight=1); start.rowconfigure(2, weight=1)
        choice = ttk.Frame(start); choice.grid(row=0, column=0, sticky='ew')
        ttk.Label(choice, text='어떤 작업을 할까요?').pack(side='left', padx=(0, 10))
        self.scenario = tk.StringVar(value=next(iter(guidance.SCENARIOS)))
        ttk.Combobox(choice, textvariable=self.scenario, values=tuple(guidance.SCENARIOS),
                     state='readonly', width=38).pack(side='left', fill='x', expand=True)
        ttk.Label(start, text='목적을 선택하면 준비물과 순서가 나옵니다. 아래 버튼으로 관련 화면에 이동하세요.',
                  style='Info.TLabel', wraplength=850).grid(row=1, column=0, sticky='w', pady=(7, 5))
        guide_frame = ttk.Frame(start)
        guide_frame.grid(row=2, column=0, sticky='nsew')
        guide_frame.columnconfigure(0, weight=1); guide_frame.rowconfigure(0, weight=1)
        self.guide_text = tk.Text(guide_frame, height=11, wrap='word', relief='flat', padx=10, pady=8,
                                  background='#f2f6fa', foreground='#1c3446', cursor='arrow')
        self.guide_text.grid(row=0, column=0, sticky='nsew')
        guide_scroll = ttk.Scrollbar(guide_frame, command=self.guide_text.yview)
        guide_scroll.grid(row=0, column=1, sticky='ns'); self.guide_text.configure(yscrollcommand=guide_scroll.set)
        checks = ttk.LabelFrame(start, text='준비 상태  ·  경로 확인만')
        checks.grid(row=3, column=0, sticky='ew', pady=(7, 0)); checks.columnconfigure(0, weight=1)
        self.checks_text = tk.Text(checks, height=4, wrap='word', relief='flat', padx=8, pady=4,
                                   background='#fafafa', cursor='arrow')
        self.checks_text.grid(row=0, column=0, sticky='ew')
        check_scroll = ttk.Scrollbar(checks, command=self.checks_text.yview)
        check_scroll.grid(row=0, column=1, sticky='ns'); self.checks_text.configure(yscrollcommand=check_scroll.set)
        self.checks_text.tag_configure('ok', foreground='#267044')
        self.checks_text.tag_configure('missing', foreground='#965800')
        self.guide_note = ttk.Label(start, text='', style='Warning.TLabel', wraplength=850)
        self.guide_note.grid(row=4, column=0, sticky='w', pady=5)
        nav = ttk.Frame(start); nav.grid(row=5, column=0, sticky='w')
        ttk.Button(nav, text='관련 화면으로 이동', command=self.open_scenario_tab).pack(side='left', padx=(0, 8))
        ttk.Button(nav, text='작업 폴더 준비 화면', command=lambda: self.select_tab('RomForge 리팩')).pack(side='left', padx=(0, 8))
        ttk.Button(nav, text='xdelta 설정 화면', command=lambda: self.select_tab('프로젝트 및 빌드')).pack(side='left')
        tab = self.scroll_tab(book, '프로젝트 및 빌드')
        tab.columnconfigure(1, weight=1)
        fields = [('title_id', 'Title ID (16자리)', None), ('region', '지역 (수동)', None), ('version', '게임·업데이트 버전 (수동)', None),
                  ('original', '원본 RomFS 폴더', 'dir'), ('edited', '수정 RomFS 폴더', 'dir'),
                  ('original_rom', '원본 게임 파일 (선택: SHA-256)', 'file'), ('code_ips', 'code.ips (선택)', 'file'),
                  ('original_code', '원본 코드 파일 (선택: 범위 검증)', 'file'), ('output', '빈 출력 폴더', 'dir'),
                  ('xdelta', 'xdelta 실행 파일 (선택)', 'file')]
        for row, (key, label, kind) in enumerate(fields): self.field(tab, row, key, label, kind)
        actions = ttk.Frame(tab); actions.grid(row=len(fields), column=0, columnspan=3, sticky='w', pady=10)
        for index, (label, action) in enumerate([('변경 분석', self.scan), ('Luma / Azahar ZIP 생성', self.build), ('xdelta 생성', self.delta), ('xdelta 적용', self.apply_delta), ('RomForge ZIP', self.romforge)]):
            ttk.Button(actions, text=label, command=action).grid(row=index // 3, column=index % 3, sticky='w', padx=(0, 7), pady=2)
        ttk.Button(tab, text='게임 파일 정보 읽기', command=self.identify_rom).grid(row=len(fields)+2, column=0, sticky='w', pady=5)
        ttk.Button(tab, text='IPS 간 충돌 검사', command=self.check_ips).grid(row=len(fields)+2, column=1, sticky='w', pady=5)
        ttk.Button(tab, text='xdelta 설치 (Windows x64)', command=self.install_xdelta).grid(row=len(fields)+3, column=0, sticky='w', pady=5)
        ttk.Label(tab, text='추가된 파일은 LayeredFS에 포함됩니다. 삭제는 지원되지 않으며 빌드가 중단됩니다.\nIPS는 형식·내부 중첩을 검사하며, 별도 패치와의 충돌이나 게임 실행 여부는 확인하지 않습니다.', wraplength=900).grid(row=len(fields)+1, column=0, columnspan=3, sticky='w')
        rf = self.scroll_tab(book, 'RomForge 리팩'); rf.columnconfigure(1, weight=1)
        self.field(rf, 0, 'keys', 'RomForge 키 파일 폴더', 'dir')
        ttk.Label(rf, text='출력 형식').grid(row=1, column=0, sticky='w', pady=5)
        ttk.Combobox(rf, textvariable=self.vars['repack_format'], values=('CCI', 'CIA', 'ZCCI'), state='readonly', width=12).grid(row=1, column=1, sticky='w')
        ttk.Button(rf, text='원본 게임에 패치 적용 및 리팩', command=self.repack_game).grid(row=2, column=0, columnspan=2, sticky='w', pady=10)
        ttk.Button(rf, text='원본 게임에서 번역 작업 폴더 준비', command=self.setup_workspace).grid(row=5, column=0, columnspan=2, sticky='w', pady=10)
        self.field(rf, 6, 'cia_version', 'CIA TMD 버전 숫자 (본편 0)', None)
        ttk.Button(rf, text='RomForge 브리지 빌드', command=self.build_bridge).grid(row=7, column=0, sticky='w', pady=5)
        ttk.Button(rf, text='xdelta 패키지를 게임에 적용 및 리팩', command=self.repack_delta).grid(row=4, column=0, columnspan=2, sticky='w', pady=10)
        ttk.Label(rf, text='프로젝트 탭의 원본 게임, Title ID, 원본/수정 RomFS와 선택한 code.ips를 사용합니다.\n게임 내부의 원본 파일과 리팩된 파일을 각각 SHA-256으로 검증합니다. 게임 실행 검증은 별도입니다.', wraplength=880).grid(row=3, column=0, columnspan=3, sticky='w')
        mt = self.scroll_tab(book, '업데이트 통합'); mt.columnconfigure(1, weight=1)
        for row, (key, label, kind) in enumerate([
            ('merge_base', '본편 작업 폴더', 'dir'), ('merge_update', '업데이트 작업 폴더', 'dir'),
            ('merge_patch', '번역 패치 RomFS 폴더 (선택)', 'dir'), ('merge_ips', '업데이트 코드용 IPS (선택)', 'file'),
            ('merge_banner', '교체 HOME 배너 (선택)', 'file')]):
            self.field(mt, row, key, label, kind)
        ttk.Button(mt, text='본편 기준 통합 xdelta 패키지 생성', command=self.make_merge).grid(row=5, column=0, columnspan=2, sticky='w', pady=8)
        ttk.Button(mt, text='통합 패키지를 본편에 적용 및 리팩', command=self.apply_merge).grid(row=6, column=0, columnspan=2, sticky='w', pady=8)
        ttk.Label(mt, text='먼저 본편과 업데이트 각각에서 작업 폴더를 준비하세요. 패치 폴더는 전체 RomFS가 아닌 변경 파일만 넣습니다.\n통합 본편과 별도 업데이트가 함께 설치되면 업데이트가 우선될 수 있으므로 게임별로 확인하세요.', wraplength=900).grid(row=7, column=0, columnspan=3, sticky='w')
        deploy = self.scroll_tab(book, '직접 전송'); deploy.columnconfigure(1, weight=1)
        for row, (key, label, kind) in enumerate([('azahar', 'Azahar 사용자 데이터 폴더', 'dir'), ('host', '3DS FTP 주소', None), ('port', 'FTP 포트', None), ('user', 'FTP 사용자 (선택)', None), ('password', 'FTP 암호 (저장 안 함)', 'password')]):
            self.field(deploy, row, key, label, kind)
        buttons = ttk.Frame(deploy); buttons.grid(row=5, column=0, columnspan=3, sticky='w', pady=10)
        ttk.Button(buttons, text='Azahar에 설치', command=self.azahar).pack(side='left', padx=(0, 8))
        ttk.Button(buttons, text='3DS FTP 전송', command=self.ftp).pack(side='left')
        ttk.Label(deploy, text='Azahar의 서로 다른 파일은 병합하고 충돌 파일은 덮어쓰지 않습니다. FTP는 기존 Title ID 폴더를 사용하지 않으며 암호화되지 않습니다.', wraplength=900).grid(row=6, column=0, columnspan=3, sticky='w')
        for frame in (start, tab, rf, mt, deploy):
            frame.bind('<Configure>', self.fit_tab_text, add='+')
        outer.bind('<Configure>', self.fit_header_text)
        book.bind('<<NotebookTabChanged>>', self.update_tab_hint)
        self.scenario.trace_add('write', self.update_guidance)
        for var in self.vars.values(): var.trace_add('write', self.update_guidance)
        self.bind('<F1>', lambda event: self.show_help())
        bottom = ttk.Frame(outer); bottom.pack(side='bottom', fill='x', pady=(8, 0))
        status = ttk.Frame(bottom); status.pack(fill='x', pady=(0, 2))
        ttk.Label(status, text='결과 및 진행 상황').pack(side='left')
        ttk.Checkbutton(status, text='작업 기록 보기', variable=self.details, command=self.update_mode).pack(side='left', padx=10)
        self.status_label = ttk.Label(status, text='대기 중', style='Info.TLabel')
        self.status_label.pack(side='right')
        self.progress = ttk.Progressbar(status, mode='indeterminate', length=100)
        logframe = self.logframe = ttk.Frame(bottom); logframe.pack(fill='x')
        self.log = tk.Text(logframe, height=4, wrap='word'); self.log.pack(side='left', fill='x', expand=True)
        self.log.tag_configure('ok', foreground='#267044')
        self.log.tag_configure('error', foreground='#ac3333')
        self.log.tag_configure('progress', foreground='#245e91')
        scroll = ttk.Scrollbar(logframe, command=self.log.yview); scroll.pack(side='right', fill='y'); self.log.configure(yscrollcommand=scroll.set)
        book.pack(side='top', fill='both', expand=True)
        self.simple_panel = simple_ui.SimplePanel(outer, self)
        self.easy_panel = easy_ui.EasyPanel(self, self)
        self.bind('<Return>', lambda event: self.easy_panel.key_enter() if self.mode.get() == EASY else None)
        self.bind('<Escape>', lambda event: self.easy_panel.key_escape() if self.mode.get() == EASY else None)
        self.mode.trace_add('write', self.update_mode)
        self.update_guidance(); self.update_tab_hint()
        self.update_mode()

    def update_mode(self, *_):
        if self.mode.get() == EASY:
            if self.busy:      # a job started in the other modes reports there; stay until it ends
                self.mode.set(SIMPLE); return
            self.outer.pack_forget()
            self.easy_panel.pack(fill='both', expand=True)
            self.dark_title_bar(self.easy_panel.dark)
            return
        if self.easy_panel.busy:
            self.mode.set(EASY); return
        self.easy_panel.pack_forget()
        self.outer.pack(fill='both', expand=True)
        self.dark_title_bar(False)
        self.fit_window(1020, 830, 760, 600)
        simple_mode = self.mode.get() == SIMPLE
        self.book.pack_forget(); self.simple_panel.pack_forget()
        self.tab_hint.pack_forget(); self.project_buttons.pack_forget(); self.legacy_button.pack_forget()
        if simple_mode:
            self.simple_panel.pack(side='top', fill='both', expand=True)
        else:
            self.project_buttons.pack(side='left')
            self.legacy_button.pack(side='right')
            self.book.pack(side='top', fill='both', expand=True)
            self.tab_hint.pack(anchor='w', pady=6, before=self.book)
        self.logframe.pack_forget()
        if not simple_mode or self.details.get(): self.logframe.pack(fill='x')

    def scroll_tab(self, book, name):
        page = ttk.Frame(book)
        book.add(page, text=name)
        self.tab_frames[name] = page
        canvas = tk.Canvas(page, highlightthickness=0, background=self.cget('background'), width=600, height=220)
        scroll = ttk.Scrollbar(page, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side='left', fill='both', expand=True); scroll.pack(side='right', fill='y')
        content = ttk.Frame(canvas, padding=10)
        item = canvas.create_window((0, 0), window=content, anchor='nw')
        def resize(_event=None):
            canvas.itemconfigure(item, width=max(1, canvas.winfo_width()),
                                 height=max(canvas.winfo_height(), content.winfo_reqheight()))
            canvas.configure(scrollregion=canvas.bbox('all'))
        content.bind('<Configure>', resize, add='+')
        canvas.bind('<Configure>', resize)
        return content

    def field(self, parent, row, key, label, kind):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=3)
        ttk.Entry(parent, textvariable=self.vars[key], show='*' if kind == 'password' else '').grid(row=row, column=1, sticky='ew', pady=3)
        if kind in ('dir', 'file'):
            ttk.Button(parent, text='찾기', command=lambda: self.browse(key, kind)).grid(row=row, column=2, padx=(7, 0))
        ttk.Button(parent, text='?', width=3, command=lambda: self.field_help(key, label)).grid(row=row, column=3, padx=(3, 0))

    def field_help(self, key, label):
        messagebox.showinfo(label, guidance.FIELD_HELP.get(key, '이 항목의 값을 입력하세요.'), parent=self)

    def select_tab(self, name):
        self.mode.set(FULL)
        self.book.select(self.tab_frames[name])

    def open_scenario_tab(self):
        self.select_tab(guidance.SCENARIOS[self.scenario.get()]['tab'])

    def update_guidance(self, *_):
        scenario = guidance.SCENARIOS.get(self.scenario.get())
        if not scenario:
            return
        self.guide_text.configure(state='normal')
        self.guide_text.delete('1.0', 'end')
        self.guide_text.insert('1.0', scenario['text'])
        self.guide_text.configure(state='disabled')
        bridge = runtime.bridge_path(Path(__file__).resolve().parent)
        rows = guidance.checks_for(self.scenario.get(), self.config(), bridge)
        self.checks_text.configure(state='normal', height=max(2, min(6, len(rows) + 1)))
        self.checks_text.delete('1.0', 'end')
        if not rows:
            self.checks_text.insert('end', '이 방법은 툴에서 선택할 파일이 없습니다.\n', 'ok')
        for key, ok, detail in rows:
            self.checks_text.insert('end', f"{'● 확인' if ok else '○ 필요'}  {guidance.CHECK_LABELS[key]}: {detail}\n", 'ok' if ok else 'missing')
        self.checks_text.configure(state='disabled')
        self.guide_note.configure(text=scenario['note'])

    def update_tab_hint(self, _event=None):
        name = self.book.tab(self.book.select(), 'text')
        hints = {
            '처음 시작': '방법을 고르고 필요한 준비물을 확인하세요. 입력란 옆 ? 버튼에도 설명이 있습니다.',
            '프로젝트 및 빌드': '제작자용 설치 ZIP과 일반 xdelta를 다룹니다. 받은 설치 ZIP은 직접 압축을 풀어 사용하세요.',
            'RomForge 리팩': '게임에서 작업 폴더를 준비하거나 수정 사항을 게임 파일에 반영합니다. 키 요구 사항은 입력·출력에 따라 다릅니다.',
            '업데이트 통합': '본편·업데이트·번역·선택한 HOME 배너를 통합하는 패키지를 만들거나 받은 패키지를 적용합니다.',
            '직접 전송': '프로젝트의 원본·수정 RomFS를 비교해 전송합니다. 받은 설치 ZIP을 불러오는 기능은 아닙니다.',
        }
        self.tab_hint.configure(text=hints.get(name, ''))

    def fit_tab_text(self, event):
        width = max(250, event.width - 40)
        def fit(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Label) and int(child.cget('wraplength') or 0):
                    if int(child.cget('wraplength')) != width:
                        child.configure(wraplength=width)
                fit(child)
        fit(event.widget)

    def fit_header_text(self, event):
        width = max(250, event.width - 35)
        if int(self.tab_hint.cget('wraplength')) != width:
            self.tab_hint.configure(wraplength=width)

    def show_help(self):
        if self.mode.get() == EASY:
            self.easy_panel.open_help()
            return
        if self.mode.get() == SIMPLE:
            self.simple_panel.help()
            return
        name = self.book.tab(self.book.select(), 'text')
        messages = {
            '처음 시작': '목적을 선택하면 준비물과 단계별 설명이 나타납니다. 경로 확인 표시는 파일 존재 여부만 뜻합니다.\n\n입력란 옆 ? 버튼을 누르면 해당 항목의 설명을 볼 수 있습니다.',
            '프로젝트 및 빌드': '원본과 편집한 RomFS 전체를 비교해 설치 ZIP 또는 일반 xdelta를 만듭니다. 일반 xdelta는 이 탭에서 복원합니다.\n\n받은 설치 ZIP은 툴에 불러오지 않고 배포 안내대로 압축을 풉니다. 통합 xdelta는 업데이트 통합 탭에서 적용합니다.',
            'RomForge 리팩': '본편 파일을 프로젝트 탭에서 선택하고 키 폴더를 지정한 뒤 작업 폴더를 준비합니다. 복호화된 입력은 빈 키 폴더로 열 수 있는 경우가 있습니다.\n\nCIA 출력은 필요한 키와 certs.bin이 필요합니다. 변경 대상의 해시 확인은 게임 실행 성공을 보장하지 않습니다.',
            '업데이트 통합': '제작: 본편과 업데이트의 작업 폴더, xdelta를 지정하고 필요하면 번역·IPS·HOME 배너를 추가합니다.\n\n적용: 본편 작업 폴더, 키 폴더, xdelta와 받은 통합 ZIP이 필요합니다. ZIP과 결과 저장 위치는 버튼을 누른 후 선택합니다.\n\n설치된 공식 업데이트가 통합 본편의 변경을 덮을 수 있으므로 해당 게임의 안내를 확인하세요.',
            '직접 전송': '프로젝트 탭의 원본·수정 RomFS를 비교해 Azahar 또는 3DS FTP로 보냅니다. 이미 받은 ZIP을 전송하는 버튼이 아닙니다.\n\n다른 내용의 기존 모드는 충돌로 중단되며, FTP는 기존 타이틀 폴더가 있으면 중단합니다.',
        }
        messagebox.showinfo(f'{name} 도움말', messages.get(name, ''), parent=self)

    def browse(self, key, kind):
        value = filedialog.askdirectory() if kind == 'dir' else filedialog.askopenfilename()
        if value: self.vars[key].set(value)

    def config(self): return {k: v.get().strip() for k, v in self.vars.items() if k != 'password'}
    def new(self):
        for v in self.vars.values(): v.set('')
        self.vars['port'].set('5000'); self.vars['repack_format'].set('CCI'); self.project = None
        local_xdelta = Path(__file__).resolve().parent / 'tools' / 'xdelta-3.2.2' / 'xdelta3.exe'
        if local_xdelta.is_file(): self.vars['xdelta'].set(str(local_xdelta))
    def open(self):
        path = filedialog.askopenfilename(filetypes=[('프로젝트 JSON', '*.json')])
        if not path: return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            self.new()
            for k, v in data.items():
                if k in self.vars and k != 'password': self.vars[k].set(v)
            self.project = path
        except Exception as e: messagebox.showerror('열기 실패', str(e))
    def save(self, ask=False):
        path = self.project if self.project and not ask else filedialog.asksaveasfilename(defaultextension='.json', filetypes=[('프로젝트 JSON', '*.json')])
        if not path: return
        try:
            Path(path).write_text(json.dumps(self.config(), ensure_ascii=False, indent=2), encoding='utf-8'); self.project = path
        except Exception as e: messagebox.showerror('저장 실패', str(e))
    def legacy(self):
        # The original CLI runs in the translation project's directory so .ttparams is respected.
        folder = filedialog.askdirectory(title='기존 Toolkit 번역 작업 폴더 선택')
        if not folder: return
        script = Path(__file__).resolve().parent / 'TranslationToolkit.py'
        if os.name == 'nt':
            interpreter = Path(sys.executable)
            if interpreter.name.lower() == 'pythonw.exe': interpreter = interpreter.with_name('python.exe')
            subprocess.Popen([str(interpreter), str(script)], cwd=folder, creationflags=subprocess.CREATE_NEW_CONSOLE)
        else: messagebox.showinfo('기존 CLI', f'터미널에서 작업 폴더로 이동한 후 실행하세요:\n{sys.executable} {script}')
    def run_job(self, label, func, on_done=None):
        if self.busy: messagebox.showinfo('작업 중', '현재 작업이 끝난 후 다시 시도하세요.'); return
        self.busy = True; self.current_job = label; self.job_callback = on_done
        self.status_label.configure(text=f'{label} 진행 중', style='Info.TLabel')
        self.progress.pack(side='right', padx=(0, 10)); self.progress.start(12)
        self.write(f'\n{label} 시작...', 'progress')
        def work():
            try: self.messages.put(('ok', func()))
            except Exception as e:
                if isinstance(e, subprocess.CalledProcessError):
                    error = (e.stderr or b'').decode(errors='replace')
                    self.messages.put(('error', f'외부 도구 실패: {error or str(e)}'))
                else: self.messages.put(('error', str(e)))
        threading.Thread(target=work, daemon=True).start()
    def poll(self):
        try:
            while True:
                state, result = self.messages.get_nowait()
                if state == 'progress':
                    self.write(str(result), 'progress'); continue
                if state == 'workspace':
                    for key, value in result['project'].items():
                        if key in self.vars: self.vars[key].set(value)
                    self.project = result['project_file']
                    kind = 'merge_update' if result['project']['title_id'].startswith('0004000E') else 'merge_base'
                    self.vars[kind].set(str(Path(result['project_file']).parent))
                    self.write(f"프로젝트 로드: {self.project}")
                    continue
                if state == 'tool':
                    self.vars['xdelta'].set(str(result)); self.write(f'xdelta 설치: {result}')
                    continue
                if state == 'metadata':
                    self.vars['title_id'].set(result['title_id'])
                    if result['version_display']: self.vars['version'].set(result['version_display'])
                    self.write(f"형식: {result['format']}, Title ID: {result['title_id']}, TMD 버전: {result['version_display'] or '확인 불가'}, 제품 코드: {result['product_code'] or '확인 불가'}")
                    continue
                self.busy = False
                self.progress.stop(); self.progress.pack_forget()
                self.status_label.configure(text=f"{self.current_job} {'실패' if state == 'error' else '완료 · 결과 확인'}",
                                            style='Error.TLabel' if state == 'error' else 'Success.TLabel')
                self.write(str(result), 'error' if state == 'error' else 'ok')
                callback = self.job_callback; self.job_callback = None
                if callback: callback(state, result)
                elif state == 'error': messagebox.showerror('작업 실패', str(result))
        except queue.Empty: pass
        self.poll_timer = self.after(100, self.poll)
    def write(self, text, tag=None):
        self.log.insert('end', text + '\n', tag or ())
        self.log.see('end')
    def install_xdelta(self):
        dest = Path(__file__).resolve().parent / 'tools' / 'xdelta-3.2.2' / 'xdelta3.exe'
        if dest.is_file():
            self.vars['xdelta'].set(str(dest)); self.write(f'기존 xdelta 선택: {dest}'); return
        def task():
            result = tools.install_xdelta(dest)
            self.messages.put(('tool', str(result)))
            return '공식 xdelta 다운로드 및 SHA-256 확인 완료.'
        self.run_job('xdelta 설치', task)
    def check_ips(self):
        paths = filedialog.askopenfilenames(title='비교할 IPS 파일 2개 이상 선택', filetypes=[('IPS', '*.ips')])
        if not paths: return
        code = self.vars['original_code'].get().strip()
        def task():
            conflicts = core.ips_conflicts(paths, Path(code).stat().st_size if code else None)
            if not conflicts: return 'IPS 기록 범위가 겹치지 않습니다. 실제 코드 버전과 로드 여부는 별도 확인해야 합니다.'
            return 'IPS 충돌 범위:\n' + '\n'.join(f'{Path(a).name} / {Path(b).name}: 0x{start:X}–0x{end-1:X}' for a,b,start,end in conflicts)
        self.run_job('IPS 충돌 검사', task)
    def identify_rom(self):
        path = self.vars['original_rom'].get().strip()
        if not path: messagebox.showerror('게임 파일', '원본 게임 파일을 먼저 선택하세요.'); return
        def task():
            info = metadata.identify(path)
            self.messages.put(('metadata', info))
            return '게임 파일 헤더 읽기 완료. 영역 정보와 설치된 업데이트는 별도로 확인하세요.'
        self.run_job('게임 정보 확인', task)
    def make_merge(self):
        config = self.config()
        if not all(config[k] for k in ('merge_base', 'merge_update', 'xdelta')):
            messagebox.showerror('통합 패키지', '본편·업데이트 작업 폴더와 xdelta를 지정하세요.'); return
        path = filedialog.asksaveasfilename(defaultextension='.zip', filetypes=[('ZIP', '*.zip')])
        if not path: return
        def task():
            report = merge.build_bundle(config['merge_base'], config['merge_update'], config['merge_patch'] or None, path, config['xdelta'], ips=config['merge_ips'] or None, banner=config['merge_banner'] or None, log=lambda line: self.messages.put(('progress', line)))
            return f"통합 패키지 생성: {len(report['changes'])}개 RomFS 파일; 구성 요소: {', '.join(report['components'])}\n{path}"
        self.run_job('통합 패키지 생성', task)
    def apply_merge(self):
        config = self.config()
        if not all(config[k] for k in ('merge_base', 'keys', 'xdelta')):
            messagebox.showerror('통합 리팩', '본편 작업 폴더, 키 폴더와 xdelta를 지정하세요.'); return
        package = filedialog.askopenfilename(title='통합 패키지 선택', filetypes=[('ZIP', '*.zip')])
        if not package: return
        fmt=config['repack_format'].upper()
        output=filedialog.asksaveasfilename(defaultextension='.'+fmt.lower(), filetypes=[(fmt, '*.'+fmt.lower())])
        if not output: return
        bridge=runtime.bridge_path(Path(__file__).resolve().parent)
        def task():
            result=merge.repack_bundle(package,config['merge_base'],config['keys'],output,fmt,bridge,config['xdelta'],cia_version=config['cia_version'],log=lambda line:self.messages.put(('progress',line)))
            return f"통합 리팩과 파일 검증 완료: {result['checked_files']}개\n{result['output']}\nSHA-256: {result['sha256']}\n공식 업데이트 설치 여부와 게임 실행은 별도로 확인하세요."
        self.run_job('통합 패키지 리팩', task)
    def build_bridge(self):
        bundled = runtime.bridge_path(Path(__file__).resolve().parent)
        if bundled.suffix.lower() == '.exe':
            messagebox.showinfo('RomForge 브리지', '실행 환경이 패키지에 포함되어 있습니다. 별도 빌드는 필요하지 않습니다.', parent=self)
            return
        project = Path(__file__).resolve().parents[2] / 'RomForge-reference' / 'KpmBridge' / 'KpmBridge.csproj'
        def task():
            if not project.is_file(): raise ValueError(f'RomForge 포크 소스를 찾을 수 없습니다: {project}')
            result = subprocess.run(['dotnet', 'build', str(project), '-c', 'Release', '-p:KpmBridge=true', '--nologo'], capture_output=True, creationflags=runtime.hidden_process_flags())
            if result.returncode:
                raise RuntimeError((result.stdout + result.stderr).decode('utf-8', errors='replace'))
            return 'RomForge 브리지 빌드 완료.'
        self.run_job('RomForge 브리지 빌드', task)
    def setup_workspace(self):
        config = self.config()
        if not config['original_rom'] or not config['keys']:
            messagebox.showerror('작업 폴더', '원본 게임 파일과 키 폴더를 지정하세요.'); return
        parent = filedialog.askdirectory(title='새 작업 폴더를 만들 위치 선택')
        if not parent: return
        dest_path = Path(parent) / '3DS_Patch_Workspace'
        index = 2
        while dest_path.exists():
            dest_path = Path(parent) / f'3DS_Patch_Workspace_{index}'
            index += 1
        dest = str(dest_path)
        bridge = runtime.bridge_path(Path(__file__).resolve().parent)
        def task():
            result = romforge.setup_workspace(config['original_rom'], config['keys'], dest, bridge)
            self.messages.put(('workspace', result))
            return f"작업 폴더 준비 완료: {result['files']}개 파일\n{dest}\n수정 RomFS 폴더를 번역 도구로 편집하세요."
        self.run_job('번역 작업 폴더 준비', task)
    def repack_game(self):
        config = self.config()
        if not config['original_rom'] or not config['keys']:
            messagebox.showerror('RomForge 리팩', '원본 게임 파일과 키 파일 폴더를 지정하세요.'); return
        fmt = config['repack_format'].upper()
        path = filedialog.asksaveasfilename(defaultextension='.' + fmt.lower(), filetypes=[(fmt, '*.' + fmt.lower())])
        if not path: return
        bridge = runtime.bridge_path(Path(__file__).resolve().parent)
        def task():
            result = romforge.repack(config, config['keys'], path, fmt, bridge, log=lambda line: self.messages.put(('progress', line)))
            return f"리팩과 내부 파일 검증 완료: {result['checked_files']}개, 코드 확인: {result['code_checked']}\n{result['output']}\nSHA-256: {result['sha256']}"
        self.run_job('RomForge 리팩', task)
    def repack_delta(self):
        config = self.config()
        if not all(config[k] for k in ('original_rom', 'original', 'keys', 'xdelta')):
            messagebox.showerror('xdelta 리팩', '원본 게임, 원본 RomFS, 키 폴더 및 xdelta를 지정하세요.'); return
        bundle = filedialog.askopenfilename(title='xdelta 패키지 선택', filetypes=[('ZIP', '*.zip')])
        if not bundle: return
        fmt = config['repack_format'].upper()
        path = filedialog.asksaveasfilename(defaultextension='.' + fmt.lower(), filetypes=[(fmt, '*.' + fmt.lower())])
        if not path: return
        bridge = runtime.bridge_path(Path(__file__).resolve().parent)
        def task():
            result = romforge.repack_delta(bundle, config['original'], config['original_rom'], config['keys'], path, fmt, bridge, config['xdelta'], log=lambda line: self.messages.put(('progress', line)), cia_version=config['cia_version'])
            return f"xdelta 적용 및 리팩 결과 검증 완료: {result['checked_files']}개\n{result['output']}\nSHA-256: {result['sha256']}"
        self.run_job('xdelta 적용 및 RomForge 리팩', task)
    def romforge(self):
        config = self.config()
        path = filedialog.asksaveasfilename(defaultextension='.zip', filetypes=[('ZIP', '*.zip')])
        if path: self.run_job('RomForge 패키지 생성', lambda: f"RomForge 패키지 생성 완료: {len(core.build_romforge(config, path)['changes'])}개 파일\n{path}")
    def scan(self):
        config = self.config()
        def task():
            report = core.inspect(config)
            lines = [f"변경/추가: {len(report['changes'])}개, 삭제: {len(report['removed'])}개", f"원본 게임 SHA-256: {report['original_rom_sha256'] or '미검사'}"]
            if report.get('rom_metadata') and not report['rom_metadata']['title_id']: lines.append('ZCCI의 Title ID는 이 분석에서 확인하지 않았습니다. RomForge 리팩 시 검사합니다.')
            lines += [('추가: ' if e['original_sha256'] is None else '변경: ') + e['path'] for e in report['changes']]
            lines += ['삭제 (지원 안 됨): ' + p for p in report['removed']]
            if report['code_ips_sha256']: lines.append('IPS 구조 검사 통과' + ('; 원본 코드 크기 확인됨' if report['original_code_sha256'] else '; 원본 코드 범위 미검사'))
            return '\n'.join(lines)
        self.run_job('변경 분석', task)
    def build(self):
        config = self.config()
        if not config['output']: messagebox.showerror('출력 폴더', '빈 출력 폴더를 지정하세요.'); return
        self.run_job('ZIP 생성', lambda: f"생성 완료: {len(core.build(config, config['output'])['changes'])}개 파일\n{config['output']}")
    def delta(self):
        config = self.config()
        if not config['xdelta']: messagebox.showerror('xdelta', 'xdelta 실행 파일을 지정하세요.'); return
        path = filedialog.asksaveasfilename(defaultextension='.zip', filetypes=[('ZIP', '*.zip')])
        if path: self.run_job('xdelta 생성 및 복원 검사', lambda: f"생성 및 복원 검증 완료: {len(core.xdelta_bundle(config, path, config['xdelta'])['changes'])}개 파일\n{path}")
    def apply_delta(self):
        config = self.config()
        if not config['xdelta'] or not config['original']:
            messagebox.showerror('xdelta', '원본 RomFS 폴더와 xdelta 실행 파일을 지정하세요.'); return
        bundle = filedialog.askopenfilename(title='xdelta 패키지 선택', filetypes=[('ZIP', '*.zip')])
        if not bundle: return
        parent = filedialog.askdirectory(title='복원 폴더를 만들 위치 선택')
        if not parent: return
        destination = str(Path(parent) / 'restored_patch')
        archives = str(Path(parent) / 'restored_packages')
        if Path(destination).exists() or (Path(archives).exists() and (not Path(archives).is_dir() or any(Path(archives).iterdir()))):
            messagebox.showerror('출력 충돌', '선택한 위치에 복원 또는 패키지 폴더가 이미 있습니다. 다른 위치를 선택하세요.'); return
        def task():
            count = core.apply_xdelta_bundle(bundle, config['original'], destination, config['xdelta'], config['original_rom'] or None, config['original_code'] or None)
            core.restored_packages(destination, archives)
            return f"복원 및 SHA-256 검증 완료: {count}개 파일\n복원: {destination}\n설치 ZIP: {archives}\n게임 및 코드 파일은 선택한 경우에만 전체 해시를 검사합니다."
        self.run_job('xdelta 적용 및 설치 ZIP 생성', task)
    def azahar(self):
        config = self.config()
        if not config['azahar']: messagebox.showerror('Azahar', '사용자 데이터 폴더를 지정하세요.'); return
        self.run_job('Azahar 설치', lambda: f"설치 완료: {core.deploy_azahar(config, config['azahar'])}")
    def ftp(self):
        config = self.config(); password = self.vars['password'].get()
        if not config['host']: messagebox.showerror('FTP', '3DS FTP 주소를 입력하세요.'); return
        self.run_job('FTP 전송', lambda: f"FTP 전송 완료: RomFS {core.deploy_ftp(config, config['host'], config['port'], config['user'], password)}개 파일")


if __name__ == '__main__':
    App().mainloop()

"""초간단 모드: one screen, no typing. Drop what you received, say where you play, press start.

Everything is drawn on a single canvas so the panel can use anti-aliased cards
and buttons (see paint.py) while staying inside the standard library. The panel
keeps a small state (patch, game, destination) and redraws the whole scene when
that state changes; only the progress view updates items in place.
"""
from __future__ import annotations

import math
import os
import queue
import subprocess
import threading
import time
import traceback
import webbrowser
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog

from . import dnd, easy, paint, recipe, runtime, simple, simple_ui

LIGHT = dict(
    bg='#F3F4F7', card='#FFFFFF', line='#E3E6EC', text='#1A1D23', text2='#5B6472', text3='#8A92A0',
    accent='#3B5BDB', accent_hover='#3451C7', accent_press='#2B44AD', accent_soft='#EEF2FF', accent_line='#C3CEF9',
    on_accent='#FFFFFF', success='#178A54', success_soft='#E4F6EC', warn='#A86200', warn_soft='#FFF3DC',
    error='#C4373A', error_soft='#FDECEC', error_line='#F3C2C3', button='#FFFFFF', button_line='#D3D8E0',
    button_hover='#F4F6F9', button_press='#E9ECF1', ghost_hover='#E6E9EF', ghost_press='#DCE0E7', track='#E1E5EB',
    off='#E4E7EC', off_text='#9AA1AD', shadow=('#1B2A4A', 0.10, 14, 4), lift=('#2B44AD', 0.28, 12, 4))
DARK = dict(
    bg='#15171C', card='#1F2229', line='#2D313A', text='#F1F3F6', text2='#AAB2BF', text3='#78808E',
    accent='#8FA5FF', accent_hover='#5C77F2', accent_press='#425CD6', accent_soft='#222A45', accent_line='#3B4A82',
    on_accent='#FFFFFF', success='#4CC38A', success_soft='#1A3328', warn='#E5A23C', warn_soft='#3A2E17',
    error='#F07178', error_soft='#3A2023', error_line='#6B3438', button='#292D35', button_line='#3A3F4A',
    button_hover='#313640', button_press='#252930', ghost_hover='#282C34', ghost_press='#30343D', track='#2D313A',
    off='#292C33', off_text='#666D79', shadow=('#000000', 0.38, 14, 4), lift=('#000000', 0.45, 12, 4))
PRIMARY_FILL = {'light': ('#3B5BDB', '#3451C7', '#2B44AD'), 'dark': ('#4F6BF0', '#5C77F2', '#425CD6')}

ICONS = dict(zip_='', game='', sd='', pc='', check='', folder='', open='',
             info='', warn='', error='', help='', tools='', back='',
             next='', drop='', refresh='', copy='', link='', key='', close='')

PHASES = {
    easy.INSTALL: ('설치할 위치 확인', '패치 파일 복사'),
    easy.MERGE: ('게임 파일 풀기', '패치 합치기', '새 게임 파일 만들기', '결과 검사'),
    easy.DELTA: ('게임 파일 풀기', '패치 파일 되살리기', '패치 설치'),
    (easy.RECIPE, 'install'): easy.RECIPE_PHASES['install'],
    (easy.RECIPE, 'rom-cci'): easy.RECIPE_PHASES['rom'][:3],
    (easy.RECIPE, 'rom-cia'): easy.RECIPE_PHASES['rom'],
}
RECIPE_MODES = (('install', '패치 설치', 'SD 카드·에뮬레이터에 (추천)', 'sd'),
                ('rom', '합본 게임 파일', '본편+업데이트(+DLC)를 한 파일로', 'game'))

HELP = {
    'recipe': ('내 게임 파일로 만드는 패치란?',
               '이 패치 ZIP에는 게임 데이터가 들어 있지 않습니다. 번역문·글꼴·바꿀 부분만 들어 있고, '
               '프로그램이 본인의 게임 파일로 이 PC에서 패치를 만듭니다.\n\n'
               '· 패치 설치(추천): 만든 패치를 SD 카드나 Azahar에 넣습니다. 3DS에서는 공식 업데이트를 설치해 둔 채로 실행합니다.\n'
               '· 합본 게임 파일: 본편·업데이트·패치(넣었다면 DLC까지)를 한 파일로 만듭니다. PC는 .3ds, 3DS는 .cia를 만듭니다.\n\n'
               '필요한 파일은 패치 안내서에 적혀 있습니다. 파일을 한꺼번에 끌어다 놓으면 프로그램이 알아서 나눠 넣습니다. '
               '파일은 GodMode9에서 복호화(Decrypt)한 것이어야 합니다.', None, None),
    'package': ('어떤 파일을 넣나요?',
                '패치를 받은 곳에서 내려받은 ZIP 파일을 그대로 넣으면 됩니다. 압축을 풀 필요가 없습니다.\n\n'
                '프로그램이 ZIP 안을 보고 종류를 알아냅니다.\n'
                '· 바로 설치하는 패치: SD 카드나 에뮬레이터에 바로 넣습니다. 게임 파일은 필요 없습니다.\n'
                '· 게임 파일에 합치는 패치: 본인이 가진 게임 파일이 하나 더 필요합니다.\n\n'
                '7z나 rar로 받았다면 먼저 압축을 풀고, 풀린 폴더를 통째로 넣어 주세요.', None, None),
    'game': ('게임 파일은 어떻게 준비하나요?',
             '본인이 가진 게임 카드나 설치된 게임을 3DS의 GodMode9로 파일로 꺼낼 수 있습니다.\n\n'
             '1. START를 누른 채 3DS를 켜서 GodMode9를 엽니다.\n'
             '2. 아래 안내에 따라 게임을 .cia 또는 .3ds 파일로 꺼냅니다.\n'
             '3. SD 카드에서 PC로 옮긴 그 파일을 넣습니다.\n\n'
             '패치와 같은 지역(일본판·북미판 등)의 게임이어야 합니다. 업데이트 파일이 아니라 본편 파일을 넣어 주세요.',
             '게임 꺼내기 안내 열기', 'https://3ds.hacks.guide/dumping-titles-and-game-cartridges.html'),
    'luma': ('3DS에서 패치 켜기',
             'SD 카드에 패치를 넣은 다음 3DS에서 한 번만 설정하면 됩니다.\n\n'
             '1. SELECT 버튼을 누른 채 3DS 전원을 켭니다.\n'
             '2. Luma3DS 설정 화면에서 「Enable game patching」에 표시합니다.\n'
             '3. START를 눌러 저장합니다.\n\n'
             '커스텀 펌웨어(Luma3DS)가 설치된 3DS에서만 쓸 수 있습니다.', None, None),
    'keys': ('키 파일이 필요하다고 나와요',
             '3DS에 설치하는 파일(.cia)을 만들거나 암호화된 게임 파일을 읽을 때만 필요합니다.\n'
             '키 파일은 본인 3DS에서 직접 꺼내야 하며 이 프로그램에 들어 있지 않습니다.\n\n'
             '1. DumpKeys.gm9를 SD 카드의 gm9/scripts 폴더에 넣습니다.\n'
             '2. GodMode9에서 HOME → Scripts → DumpKeys를 실행합니다.\n'
             '3. SD 카드 gm9 폴더의 aes_keys.txt를 PC의 폴더 하나에 복사합니다.\n'
             '4. 3DS용 파일을 만들 때는 같은 폴더에 certs.bin도 넣습니다.\n\n'
             '그 폴더를 「키 폴더 고르기」로 한 번 고르면 다음부터는 기억합니다.',
             'DumpKeys 안내 열기', 'https://github.com/Lime3DS/lime3ds-archive/tree/master/dist/dumpkeys'),
    # The longer preparation notes are shared with the step-by-step mode.
    'certs': ('certs.bin은 무엇인가요?', *simple_ui.HELP['certs'][1:]),
    'boot9': simple_ui.HELP['boot9'],
}


def system_dark():
    if os.name != 'nt':
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            return winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] == 0
    except OSError:
        return False


class EasyPanel(tk.Frame):
    def __init__(self, parent, app, dark=None):
        self.dark = system_dark() if dark is None else dark
        self.t = dict(DARK if self.dark else LIGHT)
        super().__init__(parent, background=self.t['bg'])
        self.app = app
        self.scale = max(1.0, self.winfo_fpixels('1i') / 96.0)
        self.c = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=self.t['bg'])
        self.c.pack(fill='both', expand=True)
        families = set(tkfont.families(self))
        self.family = next((f for f in ('맑은 고딕', 'Malgun Gothic', 'Noto Sans KR') if f in families), 'TkDefaultFont')
        self.icon_family = next((f for f in ('Segoe Fluent Icons', 'Segoe MDL2 Assets') if f in families), None)
        self.fonts, self.images = {}, {}
        self.messages, self.drops = queue.Queue(), None
        self.view, self.busy = 'home', False
        self.package = self.package_error = self.game = self.game_error = self.game_path = None
        self.inputs, self.input_errors, self.loose = {}, {}, []
        self.note = ''
        settings = easy.load_settings()
        self.recipe_mode = settings.get('recipe_mode') if settings.get('recipe_mode') in ('install', 'rom') else 'install'
        self.where = settings.get('place') if settings.get('place') in ('3ds', 'pc') else None
        self.cards, self.sd_root = easy.find_sd_cards(), None
        self.emulators = easy.find_emulators()
        saved = settings.get('emulator')
        self.emu_root = Path(saved) if saved and Path(saved).is_dir() else (self.emulators[0].root if self.emulators else None)
        app_dir = Path(__file__).resolve().parents[1]
        self.app_dir = app_dir
        self.key_dir = easy.find_keys(settings.get('keys'), app_dir.parent / 'keys', app_dir / 'keys')
        self.out_dir = None
        self.job = self.result = self.failure = self.pending = None
        self.help_topic, self.help_back = 'package', 'home'
        self.primary = None
        self._size, self._redraw, self._n, self._hover = (0, 0), None, 0, None
        self.c.bind('<Configure>', self._resized)
        self.c.bind('<MouseWheel>', self._wheel)
        self._tick = self.after(120, self.tick)

    # ------------------------------------------------------------ small drawing kit

    def p(self, value):
        return int(round(value * self.scale))

    def font(self, size, bold=False):
        key = (size, bold)
        if key not in self.fonts:
            self.fonts[key] = tkfont.Font(self, family=self.family, size=-self.p(size), weight='bold' if bold else 'normal')
        return self.fonts[key]

    def image(self, key, make):
        if key not in self.images:
            self.images[key] = make()
        return self.images[key]

    def shape(self, x, y, w, h, radius, fill, border=None, border_width=1.0, shadow=None, gradient=None, tags=()):
        key = (int(w), int(h), radius, fill, border, border_width, shadow, gradient)
        def make():
            data, pad = paint.rounded(w, h, radius, fill, border, border_width, shadow, gradient)
            return tk.PhotoImage(master=self, data=data, format='png'), pad
        picture, pad = self.image(key, make)
        return self.c.create_image(x - pad, y - pad, image=picture, anchor='nw', tags=tags)

    def label(self, x, y, text, size=14, colour=None, bold=False, anchor='nw', width=None, tags=(), justify='left'):
        options = {'width': width} if width else {}
        return self.c.create_text(x, y, text=text, font=self.font(size, bold), fill=colour or self.t['text'],
                                  anchor=anchor, tags=tags, justify=justify, **options)

    def icon(self, x, y, name, size, colour, tags=()):
        if not self.icon_family:
            return None
        key = ('icon', size)
        if key not in self.fonts:
            self.fonts[key] = tkfont.Font(self, family=self.icon_family, size=-self.p(size))
        return self.c.create_text(x, y, text=ICONS[name], font=self.fonts[key], fill=colour, anchor='center', tags=tags)

    def badge(self, x, y, size, name, tone):
        """A tinted circle with an icon; tone is 'accent', 'success', 'warn' or 'error'."""
        d = self.p(size)
        self.shape(x, y, d, d, d / 2.0, self.t[tone + '_soft'])
        if self.icon(x + d / 2.0, y + d / 2.0, name, size * 0.46, self.t[tone]) is None:
            self.label(x + d / 2.0, y + d / 2.0, {'check': '✓', 'warn': '!', 'error': '!'}.get(name, '•'), size * 0.5,
                       self.t[tone], True, 'center')

    def fit(self, text, size, limit, bold=False):
        """Shorten the middle of a long name so it fits in `limit` pixels."""
        font = self.font(size, bold)
        if font.measure(text) <= limit:
            return text
        keep = len(text)
        while keep > 4:
            keep -= 1
            head = (keep * 3) // 5
            trial = text[:head] + '…' + text[len(text) - (keep - head):]
            if font.measure(trial) <= limit:
                return trial
        return text[:3] + '…'

    def clickable(self, tag, command, states=None, enabled=True):
        """Make every item carrying `tag` act as one control. `states` swaps the background image."""
        c = self.c
        def show(state):
            if states:
                picture, _ = states[state]
                c.itemconfigure(states['item'], image=picture)
        def enter(_):
            self._hover = tag
            if enabled:
                c.configure(cursor='hand2'); show('hover')
        def leave(_):
            if self._hover == tag: self._hover = None
            c.configure(cursor=''); show('normal')
        def press(_):
            if enabled: show('press')
        def release(_):
            if enabled:
                show('hover' if self._hover == tag else 'normal')
                if self._hover == tag: self.after_idle(command)
        for sequence, handler in (('<Enter>', enter), ('<Leave>', leave), ('<ButtonPress-1>', press), ('<ButtonRelease-1>', release)):
            c.tag_bind(tag, sequence, handler)

    def button(self, x, y, w, h, text, command, kind='secondary', icon=None, enabled=True, size=14):
        """kind: 'primary', 'secondary' or 'ghost'. Returns the right edge."""
        self._n += 1
        tag, t = f'b{self._n}', self.t
        radius = self.p(12 if kind == 'primary' else 9)
        mode = 'dark' if self.dark else 'light'
        if not enabled:
            looks = {s: dict(fill=t['off']) for s in ('normal', 'hover', 'press')}
            colour = t['off_text']
        elif kind == 'primary':
            fills = PRIMARY_FILL[mode]
            looks = {'normal': dict(fill=fills[0], gradient=paint.mix(fills[0], fills[2], 0.45), shadow=t['lift']),
                     'hover': dict(fill=fills[1], gradient=paint.mix(fills[1], fills[2], 0.55), shadow=t['lift']),
                     'press': dict(fill=fills[2], shadow=t['lift'])}
            colour = t['on_accent']
        elif kind == 'secondary':
            looks = {'normal': dict(fill=t['button'], border=t['button_line']),
                     'hover': dict(fill=t['button_hover'], border=t['button_line']),
                     'press': dict(fill=t['button_press'], border=t['button_line'])}
            colour = t['text']
        else:
            looks = {'normal': dict(fill=t['bg']), 'hover': dict(fill=t['ghost_hover']), 'press': dict(fill=t['ghost_press'])}
            colour = t['text2']
        states = {}
        for state, look in looks.items():
            key = ('btn', int(w), int(h), radius, tuple(sorted(look.items())))
            def make(look=look):
                data, pad = paint.rounded(w, h, radius, look['fill'], look.get('border'), 1.0, look.get('shadow'), look.get('gradient'))
                return tk.PhotoImage(master=self, data=data, format='png'), pad
            states[state] = self.image(key, make)
        picture, pad = states['normal']
        states['item'] = self.c.create_image(x - pad, y - pad, image=picture, anchor='nw', tags=(tag,))
        font = self.font(size, kind == 'primary')
        text_w = font.measure(text)
        gap = self.p(7) if icon and self.icon_family else 0
        icon_w = self.p(size + 2) if icon and self.icon_family else 0
        left = x + (w - text_w - gap - icon_w) / 2.0
        if icon_w:
            self.icon(left + icon_w / 2.0, y + h / 2.0, icon, size + 1, colour, tags=(tag,))
        self.c.create_text(left + icon_w + gap, y + h / 2.0 - self.p(1), text=text, font=font, fill=colour, anchor='w', tags=(tag,))
        self.clickable(tag, command, states, enabled)
        return x + w

    def button_width(self, text, size=14, icon=False, bold=False):
        return self.font(size, bold).measure(text) + self.p(30) + (self.p(size + 9) if icon and self.icon_family else 0)

    def link(self, x, y, text, command, size=13):
        self._n += 1
        tag = f'b{self._n}'
        item = self.label(x, y, text, size, self.t['accent'], tags=(tag,))
        underline = tkfont.Font(self, family=self.family, size=-self.p(size), underline=True)
        self.fonts[('underline', size)] = underline
        normal = self.font(size)
        self.clickable(tag, command)
        self.c.tag_bind(tag, '<Enter>', lambda e: self.c.itemconfigure(item, font=underline), add='+')
        self.c.tag_bind(tag, '<Leave>', lambda e: self.c.itemconfigure(item, font=normal), add='+')
        return self.c.bbox(item)

    # ------------------------------------------------------------ redraw plumbing

    def _resized(self, event):
        if (event.width, event.height) != self._size:
            self._size = (event.width, event.height)
            if self._redraw: self.after_cancel(self._redraw)
            self._redraw = self.after(12, self.render)

    def _wheel(self, event):
        if self.c.bbox('all') and self.c.bbox('all')[3] > self.c.winfo_height():
            self.c.yview_scroll(-1 if event.delta > 0 else 1, 'units')

    def destroy(self):
        for timer in (self._tick, self._redraw):
            if timer:
                try: self.after_cancel(timer)
                except tk.TclError: pass
        self._tick = self._redraw = None
        if self.package: self.package.close()
        super().destroy()

    def render(self):
        self._redraw = None
        width, height = self.c.winfo_width(), self.c.winfo_height()
        if width < self.p(240) or height < self.p(200):
            return
        self.c.delete('all')
        self.c.configure(cursor='')
        if len(self.images) > 260: self.images.clear()
        self._n, self._hover, self.primary, self.live = 0, None, None, {}
        cw = min(self.p(720), width - 2 * self.p(24))
        x = (width - cw) // 2
        y = self.header(x, self.p(22), cw)
        bottom = getattr(self, 'view_' + self.view)(x, y, cw, height)
        self.c.configure(scrollregion=(0, 0, width, max(height, bottom + self.p(18))))
        self.c.yview_moveto(0)

    def go(self, view):
        self.view = view
        self.render()

    # ------------------------------------------------------------ header shared by every view

    def header(self, x, y, cw):
        t, size = self.t, self.p(40)
        mode = 'dark' if self.dark else 'light'
        mark = self.image(('mark', size), lambda: (tk.PhotoImage(
            master=self, data=paint.app_icon(size, PRIMARY_FILL[mode][0], PRIMARY_FILL[mode][2]), format='png'), 0))[0]
        self.c.create_image(x, y, image=mark, anchor='nw')
        self.label(x + size + self.p(12), y - self.p(2), '3DS 한글패치', 20, bold=True)
        self.label(x + size + self.p(12), y + self.p(24), '받은 패치를 넣으면 알아서 설치해 드려요', 13, t['text2'])
        height = self.p(34)
        right = x + cw
        if self.view in ('home', 'done', 'failed') and not self.busy:
            w = self.button_width('제작자 도구', 13, True)
            self.button(right - w, y + self.p(3), w, height, '제작자 도구', self.open_advanced, 'ghost', 'tools', size=13)
            right -= w + self.p(4)
        if self.view != 'help' and not self.busy:
            w = self.button_width('도움말', 13, True)
            self.button(right - w, y + self.p(3), w, height, '도움말', lambda: self.open_help(), 'ghost', 'help', size=13)
        return y + size + self.p(24)

    # ------------------------------------------------------------ home: the three cards

    def view_home(self, x, y, cw, height):
        if not (self.package or self.package_error or self.game_path):
            return self.hero(x, y, cw, height)
        gap = self.p(12)
        y = self.file_card(x, y, cw, 'package') + gap
        if self.recipe():
            y = self.mode_card(x, y, cw) + gap
            for role in self.recipe_roles():
                y = self.file_card(x, y, cw, role) + gap
        elif self.game_path or (self.package and self.package.needs_game):
            y = self.file_card(x, y, cw, 'game') + gap
        if self.package:
            y = self.place_card(x, y, cw) + gap
        return self.action_bar(x, y + self.p(4), cw)

    def hero(self, x, y, cw, height):
        t = self.t
        h = max(self.p(230), min(self.p(330), height - y - self.p(120)))
        self._n += 1
        tag = f'b{self._n}'
        states = {}
        for state, fill, line in (('normal', t['accent_soft'], t['accent_line']), ('hover', paint.mix(t['accent_soft'], t['accent_line'], 0.25), t['accent']),
                                  ('press', paint.mix(t['accent_soft'], t['accent_line'], 0.4), t['accent'])):
            def make(fill=fill, line=line):
                data, pad = paint.rounded(cw, h, self.p(20), fill, line, self.p(1.5))
                return tk.PhotoImage(master=self, data=data, format='png'), pad
            states[state] = self.image(('hero', cw, h, state), make)
        states['item'] = self.c.create_image(x, y, image=states['normal'][0], anchor='nw', tags=(tag,))
        cx, top = x + cw / 2.0, y + (h - self.p(196)) / 2.0
        d = self.p(64)
        self.c.addtag_withtag(tag, self.shape(cx - d / 2.0, top, d, d, d / 2.0, t['card'], shadow=t['shadow']))
        if self.icon(cx, top + d / 2.0, 'drop', 28, t['accent'], tags=(tag,)) is None:
            self.label(cx, top + d / 2.0, '↓', 26, t['accent'], True, 'center', tags=(tag,))
        self.label(cx, top + d + self.p(20), '받은 패치 파일을 여기에 놓아 주세요', 19, bold=True, anchor='n', tags=(tag,))
        self.label(cx, top + d + self.p(52), 'ZIP 그대로 끌어다 놓거나, 아무 곳이나 눌러 파일을 고르세요', 14, t['text2'], anchor='n', tags=(tag,))
        self.clickable(tag, self.pick_any, states)
        bw, bh = self.p(150), self.p(42)
        self.button(cx - bw / 2.0, top + d + self.p(90), bw, bh, '파일 고르기', self.pick_any, 'primary', 'folder')
        self.primary = self.pick_any
        y += h + self.p(22)
        steps = ('패치 파일 넣기', '플레이할 곳 고르기', '시작 누르기')
        font, dot = self.font(13), self.p(22)
        total = sum(dot + self.p(8) + font.measure(s) for s in steps) + 2 * self.p(44)
        left = x + (cw - total) / 2.0
        for index, step in enumerate(steps):
            self.shape(left, y, dot, dot, dot / 2.0, t['track'])
            self.label(left + dot / 2.0, y + dot / 2.0 - self.p(1), str(index + 1), 12, t['text2'], True, 'center')
            self.label(left + dot + self.p(8), y + dot / 2.0 - self.p(1), step, 13, t['text2'], anchor='w')
            left += dot + self.p(8) + font.measure(step)
            if index < 2:
                if self.icon(left + self.p(22), y + dot / 2.0, 'next', 10, t['text3']) is None:
                    self.label(left + self.p(22), y + dot / 2.0, '›', 13, t['text3'], anchor='center')
                left += self.p(44)
        y += dot + self.p(14)
        self.label(x + cw / 2.0, y, '원본 게임 파일과 패치 파일은 바뀌지 않아요', 12, t['text3'], anchor='n')
        return y + self.p(20)

    def file_card(self, x, y, cw, role):
        t, pad = self.t, self.p(18)
        if role in easy.ROLES:
            value, error, pick = self.inputs.get(role), self.input_errors.get(role), (lambda r=role: self.pick_input(r))
        else:
            value = self.package if role == 'package' else self.game
            error = self.package_error if role == 'package' else self.game_error
            pick = self.pick_package if role == 'package' else self.pick_game
        badge, bx = self.p(40), x + pad
        tx = bx + badge + self.p(14)
        if error:
            bw = self.button_width('다시 고르기')
            limit = cw - (tx - x) - bw - 2 * pad
            first = len(self.c.find_all())
            title = self.label(tx, y + pad - self.p(2), error.title, 15, t['error'], True, width=limit)
            ty = self.c.bbox(title)[3] + self.p(4)
            if error.body:
                body = self.label(tx, ty, error.body, 13, t['text2'], width=limit)
                ty = self.c.bbox(body)[3] + self.p(2)
            if error.topic:
                ty = self.link(tx, ty + self.p(3), '도움말 보기', lambda: self.open_help(error.topic))[3]
            h = max(self.p(76), ty - y + pad - self.p(2))
            back = self.shape(x, y, cw, h, self.p(16), t['error_soft'], t['error_line'])
            self._lower(back, first)
            self.badge(bx, y + pad - self.p(2), 40, 'warn', 'error')
            self.button(x + cw - pad - bw, y + pad, bw, self.p(36), '다시 고르기', pick)
            return y + h
        if value is None:
            wording = {'package': ('패치 파일을 넣어 주세요', '받은 ZIP 파일을 끌어다 놓거나 골라 주세요', 'zip_'),
                       'game': ('게임 파일이 필요해요', '본인이 가진 게임 파일(.cia · .3ds · .cci)을 끌어다 놓거나 골라 주세요', 'game'),
                       'base': ('본편 게임 파일이 필요해요', self.requirement('base', '복호화한 카트리지 덤프(.3ds · .cci)'), 'game'),
                       'update': ('업데이트 파일이 필요해요', self.requirement('update', '복호화한 업데이트 CIA'), 'game'),
                       'dlc': ('DLC 파일 (선택)', '넣으면 합본에 DLC도 들어가요. 없으면 비워 두세요', 'game')}[role]
            bw = self.button_width('파일 고르기', bold=True)
            h = self.p(104 if role != 'package' else 80)
            self.shape(x, y, cw, h, self.p(16), t['accent_soft'], t['accent_line'], self.p(1.5))
            d = badge
            self.shape(bx, y + self.p(20), d, d, d / 2.0, t['card'])
            self.icon(bx + d / 2.0, y + self.p(20) + d / 2.0, wording[2], 19, t['accent'])
            self.label(tx, y + self.p(18), wording[0], 15, bold=True)
            self.label(tx, y + self.p(42), self.fit(wording[1], 13, cw - (tx - x) - bw - 2 * pad), 13, t['text2'])
            if role in ('game', 'base'):
                self.link(tx, y + self.p(68), '게임 파일은 어떻게 준비하나요?', lambda: self.open_help('game'))
            elif role in easy.ROLES:
                self.link(tx, y + self.p(68), '어떤 파일을 넣나요?', lambda: self.open_help('recipe'))
            self.button(x + cw - pad - bw, y + self.p(22), bw, self.p(38), '파일 고르기', pick, 'primary')
            return y + h
        h = self.p(76)
        self.shape(x, y, cw, h, self.p(16), t['card'], t['line'], shadow=t['shadow'])
        self.badge(bx, y + self.p(18), 40, 'check', 'success')
        bw = self.button_width('바꾸기', 13)
        limit = cw - (tx - x) - bw - 2 * pad
        if role == 'package':
            name, detail = value.path.name, value.summary
        elif role in easy.ROLES:
            name = value.path.name
            detail = f'{easy.ROLE_WORDS[role]} 확인됨  ·  ' + easy.size_text(value.size)
        else:
            name = value.path.name
            detail = ('게임 파일이에요' if not self.package else '패치와 맞는 게임이에요' if value.title_id
                      else '시작하면 패치와 맞는지 확인해요') + '  ·  ' + easy.size_text(value.size)
        self.label(tx, y + self.p(16), self.fit(name, 15, limit, True), 15, bold=True)
        self.label(tx, y + self.p(41), self.fit(detail, 13, limit), 13, t['text2'])
        if role == 'dlc':
            bw2 = self.button_width('빼기', 13)
            self.button(x + cw - pad - bw - bw2 - self.p(8), y + self.p(21), bw2, self.p(34), '빼기', self.remove_dlc, size=13)
        self.button(x + cw - pad - bw, y + self.p(21), bw, self.p(34), '바꾸기', pick, size=13)
        return y + h

    def _lower(self, item, first_index):
        """Put a background drawn after its content underneath that content."""
        items = self.c.find_all()
        if first_index < len(items) - 1:
            self.c.tag_lower(item, items[first_index])

    def place_card(self, x, y, cw):
        t, pad = self.t, self.p(18)
        first = len(self.c.find_all())
        self.label(x + pad, y + pad - self.p(2), '어디에서 플레이하나요?', 15, bold=True)
        ty = y + pad + self.p(30)
        gap = self.p(12)
        tw, th = (cw - 2 * pad - gap) // 2, self.p(72)
        for index, (key, title, sub, glyph) in enumerate((('3ds', '닌텐도 3DS', '실제 게임기에서', 'game'), ('pc', 'PC', 'Azahar 에뮬레이터에서', 'pc'))):
            self.tile(x + pad + index * (tw + gap), ty, tw, th, key, title, sub, glyph)
        bottom = ty + th
        if self.where:
            bottom = self.place_detail(x + pad, bottom + self.p(14), cw - 2 * pad)
        h = bottom - y + pad
        self._lower(self.shape(x, y, cw, h, self.p(16), t['card'], t['line'], shadow=t['shadow']), first)
        return y + h

    def mode_card(self, x, y, cw):
        t, pad = self.t, self.p(18)
        first = len(self.c.find_all())
        self.label(x + pad, y + pad - self.p(2), '무엇을 만들까요?', 15, bold=True)
        ty, gap = y + pad + self.p(30), self.p(12)
        tw, th = (cw - 2 * pad - gap) // 2, self.p(72)
        for index, (key, title, sub, glyph) in enumerate(RECIPE_MODES):
            self.tile(x + pad + index * (tw + gap), ty, tw, th, key, title, sub, glyph,
                      chosen=self.recipe_mode == key, command=lambda k=key: self.choose_mode(k))
        h = ty + th - y + pad
        self._lower(self.shape(x, y, cw, h, self.p(16), t['card'], t['line'], shadow=t['shadow']), first)
        return y + h

    def tile(self, x, y, w, h, key, title, sub, glyph, chosen=None, command=None):
        t = self.t
        chosen = self.where == key if chosen is None else chosen
        self._n += 1
        tag = f'b{self._n}'
        looks = {'normal': (t['accent_soft'], t['accent'], 2.0) if chosen else (t['card'], t['button_line'], 1.0),
                 'hover': (t['accent_soft'], t['accent'], 2.0) if chosen else (t['button_hover'], t['accent_line'], 1.5),
                 'press': (t['accent_soft'], t['accent'], 2.0) if chosen else (t['button_press'], t['accent_line'], 1.5)}
        states = {}
        for state, (fill, line, width) in looks.items():
            def make(fill=fill, line=line, width=width):
                data, pad = paint.rounded(w, h, self.p(12), fill, line, self.p(width))
                return tk.PhotoImage(master=self, data=data, format='png'), pad
            states[state] = self.image(('tile', w, h, fill, line, width), make)
        states['item'] = self.c.create_image(x, y, image=states['normal'][0], anchor='nw', tags=(tag,))
        colour = t['accent'] if chosen else t['text2']
        self.icon(x + self.p(34), y + h / 2.0, glyph, 26, colour, tags=(tag,))
        tx = x + self.p(64) if self.icon_family else x + self.p(18)
        self.label(tx, y + self.p(15), title, 15, bold=True, tags=(tag,))
        self.label(tx, y + self.p(40), sub, 13, t['text2'], tags=(tag,))
        if chosen:
            d = self.p(22)
            mode = 'dark' if self.dark else 'light'
            self.c.addtag_withtag(tag, self.shape(x + w - d - self.p(14), y + (h - d) / 2.0, d, d, d / 2.0, PRIMARY_FILL[mode][0]))
            if self.icon(x + w - d / 2.0 - self.p(14), y + h / 2.0, 'check', 11, '#FFFFFF', tags=(tag,)) is None:
                self.label(x + w - d / 2.0 - self.p(14), y + h / 2.0, '✓', 12, '#FFFFFF', True, 'center', tags=(tag,))
        self.clickable(tag, command or (lambda: self.choose_place(key)), states)

    def place_detail(self, x, y, w):
        """One box saying what will happen at the chosen place, with the single control it may need.

        Lines are plain strings or (prefix, path) pairs; paths are shortened to the room left beside the buttons.
        """
        t = self.t
        kind = self.package.kind
        allinone = self.recipe() and self.recipe_mode == 'rom'
        buttons, tone, glyph = [], 'text2', 'info'
        if allinone and self.where == 'pc':
            base = self.inputs.get('base')
            folder = self.out_dir or (base.path.parent if base else None)
            lines = ['Azahar에서 바로 여는 합본 게임 파일(.3ds)을 새로 만들어요. 작업 중 약 13GB가 필요해요.'] + ([('저장 위치: ', folder)] if folder else [])
            buttons = [('저장 위치 바꾸기', self.pick_output)]
        elif kind == easy.MERGE and self.where == 'pc':
            folder = self.out_dir or (self.game.path.parent if self.game else None)
            lines = ['Azahar에서 바로 열 수 있는 게임 파일(.cci)을 새로 만들어요.'] + ([('저장 위치: ', folder)] if folder else [])
            buttons = [('저장 위치 바꾸기', self.pick_output)]
        elif kind == easy.MERGE or allinone:
            problem = self.keys_problem()
            if problem:
                lines, tone, glyph = ['3DS에 설치하는 파일(.cia)을 만들려면 키 폴더가 필요해요.', problem], 'warn', 'key'
                buttons = [('키 폴더 고르기', self.pick_keys), ('준비 방법', lambda: self.open_help('keys'))]
            else:
                lines, tone, glyph = ['3DS에 설치하는 파일(.cia)을 새로 만들어요.', ('키 폴더: ', self.key_dir)], 'success', 'check'
                buttons = [('바꾸기', self.pick_keys)]
                if allinone:
                    folder = self.out_dir or (self.inputs['base'].path.parent if self.inputs.get('base') else None)
                    if folder: lines.append(('저장 위치: ', folder))
                    buttons.append(('저장 위치', self.pick_output))
        elif self.where == '3ds':
            root = self.current_sd()
            card = next((c for c in self.cards if c.root == root), None)
            if card:
                lines = [f'SD 카드 {card.label}에 설치해요.', f'남은 공간 {easy.size_text(card.free)}']
            elif root:
                lines = ['이 폴더를 SD 카드로 보고 설치해요.', ('', root)]
            if root:
                tone, glyph, buttons = 'success', 'check', [('바꾸기', self.sd_menu)]
            else:
                lines, tone, glyph = ['3DS의 SD 카드를 PC에 꽂아 주세요.', '꽂았다면 「다시 찾기」를 눌러 주세요.'], 'warn', 'sd'
                buttons = [('다시 찾기', self.rescan), ('폴더 직접 고르기', self.pick_sd)]
        elif self.emu_root:
            lines, tone, glyph = ['에뮬레이터 폴더에 설치해요.', ('', self.emu_root)], 'success', 'check'
            buttons = [('바꾸기', self.pick_emulator)]
        else:
            lines = ['Azahar 폴더를 찾지 못했어요.', 'Azahar의 「파일 → Azahar 폴더 열기」로 열리는 폴더를 골라 주세요.']
            tone, glyph, buttons = 'warn', 'folder', [('폴더 고르기', self.pick_emulator)]
        if self.game and self.game.encrypted and kind != easy.INSTALL and not self.key_dir and not (kind == easy.MERGE and self.where == '3ds'):
            lines.append('암호화된 게임 파일로 보여요. 실패하면 키 폴더를 골라 주세요.')
            buttons.append(('키 폴더 고르기', self.pick_keys))
        widths = [self.button_width(label, 13) for label, _ in buttons]
        left = x + self.p(46)
        room = max(self.p(150), w - self.p(46) - self.p(22) - sum(widths) - self.p(8) * len(widths))
        font = self.font(13)
        text = '\n'.join(line if isinstance(line, str) else line[0] + self.fit(str(line[1]), 13, room - font.measure(line[0]))
                         for line in lines)
        first = len(self.c.find_all())
        item = self.label(left, y + self.p(11), text, 13, t['text'], width=room)
        h = max(self.p(54), self.c.bbox(item)[3] - y + self.p(11))
        soft = t[tone + '_soft'] if tone != 'text2' else t['bg']
        self._lower(self.shape(x, y, w, h, self.p(12), soft), first)
        self.icon(x + self.p(24), y + h / 2.0, glyph, 16, t[tone] if tone != 'text2' else t['text2'])
        right = x + w - self.p(10)
        for (label, command), width in reversed(list(zip(buttons, widths))):
            self.button(right - width, y + (h - self.p(34)) / 2.0, width, self.p(34), label, command, size=13)
            right -= width + self.p(8)
        return y + h

    def action_bar(self, x, y, cw):
        t = self.t
        blocker = self.blocker()
        label = ('만들기 시작' if self.recipe() and self.recipe_mode == 'rom'
                 else '설치 시작' if self.package and self.package.kind != easy.MERGE else '패치 시작')
        bw, bh = self.p(176), self.p(50)
        self.button(x + cw - bw, y, bw, bh, label, self.start, 'primary', enabled=not blocker, size=16)
        self.primary = None if blocker else self.start
        message = self.note or blocker or '준비됐어요. 시작을 눌러 주세요.'
        colour = t['warn'] if self.note else t['text2'] if blocker else t['success']
        self.label(x + self.p(4), y + bh / 2.0, message, 14, colour, anchor='w', width=cw - bw - self.p(24))
        return y + bh

    # ------------------------------------------------------------ what is still missing

    def current_sd(self):
        if self.sd_root and self.sd_root.is_dir():
            return self.sd_root
        return self.cards[0].root if self.cards else None

    def keys_problem(self):
        if not self.key_dir:
            return '키 파일(aes_keys.txt)이 든 폴더를 골라 주세요.'
        source = self.inputs.get('update') if self.recipe() else self.game     # a CIA that carries certificates
        try:
            simple.validate_cia_keys(self.key_dir, source.path if source else None)
        except ValueError as error:
            return str(error)
        return None

    def tools(self):
        return runtime.bridge_path(self.app_dir), Path(self.app.vars['xdelta'].get() or 'missing')

    def blocker(self):
        """The one thing the user should do next, or None when everything is ready."""
        if self.package_error or not self.package: return '패치 파일을 넣어 주세요.'
        if self.recipe():
            return self.recipe_blocker()
        if self.package.needs_game:
            if self.game_error or not self.game: return '게임 파일을 넣어 주세요.'
            bridge, xdelta = self.tools()
            if not bridge.is_file() or not xdelta.is_file():
                return '필요한 도구가 빠져 있어요. 프로그램 폴더를 통째로 다시 받아 주세요.'
        if not self.where: return '어디에서 플레이할지 골라 주세요.'
        if self.package.kind == easy.MERGE:
            return '키 폴더를 골라 주세요.' if self.where == '3ds' and self.keys_problem() else None
        if self.where == '3ds': return None if self.current_sd() else 'SD 카드를 꽂거나 폴더를 골라 주세요.'
        return None if self.emu_root else '에뮬레이터 폴더를 골라 주세요.'

    def recipe_blocker(self):
        missing = [easy.ROLE_WORDS[r] for r in ('base', 'update') if not self.inputs.get(r)]
        if missing: return ' · '.join(missing) + '을(를) 넣어 주세요.'
        if not self.where: return '어디에서 플레이할지 골라 주세요.'
        if self.recipe_mode == 'install':
            if self.where == '3ds': return None if self.current_sd() else 'SD 카드를 꽂거나 폴더를 골라 주세요.'
            return None if self.emu_root else '에뮬레이터 폴더를 골라 주세요.'
        if self.inputs['base'].format != 'NCSD/CCI':
            return '합본을 만들려면 본편이 카트리지 덤프(.3ds · .cci)여야 해요.'
        if not recipe.tool_path(self.app_dir).is_file():
            return '필요한 도구가 빠져 있어요. 프로그램 폴더를 통째로 다시 받아 주세요.'
        if self.where == '3ds':
            if not self.tools()[0].is_file(): return '필요한 도구가 빠져 있어요. 프로그램 폴더를 통째로 다시 받아 주세요.'
            return '키 폴더를 골라 주세요.' if self.keys_problem() else None
        return None

    # ------------------------------------------------------------ recipe patches

    def recipe(self):
        return bool(self.package and self.package.kind == easy.RECIPE)

    def recipe_roles(self):
        return ('base', 'update', 'dlc') if self.recipe_mode == 'rom' else ('base', 'update')

    def requirement(self, role, fallback):
        spec = self.package.requires(role) if self.package else None
        return (spec or {}).get('label') or fallback

    def choose_mode(self, key):
        self.recipe_mode, self.note = key, ''
        self.remember()
        self.render()

    def assign_input(self, path, role=None):
        try:
            game = easy.check_recipe_input(path, self.package)
        except easy.EasyError as error:
            if role:
                self.inputs.pop(role, None); self.input_errors[role] = error
            else:
                self.note = f'{Path(path).name}: {error.title}'
            return
        if role and game.role != role:
            self.note = f'넣은 파일은 {easy.ROLE_WORDS[game.role]}이라 그 자리에 넣었어요.'
        self.inputs[game.role] = game
        self.input_errors.pop(game.role, None)
        if game.role == 'dlc' and self.recipe_mode == 'install':
            self.note = 'DLC는 합본 게임 파일에만 들어가요. 「합본 게임 파일」을 고르면 함께 넣어요.'

    def pick_input(self, role):
        chosen = filedialog.askopenfilename(parent=self.app, title=easy.ROLE_WORDS[role] + ' 고르기', filetypes=[
            ('3DS 게임 파일', '*.cia *.3ds *.cci'), ('모든 파일', '*.*')])
        if chosen:
            self.note = ''
            self.assign_input(Path(chosen), role)
            self.go('home')

    def remove_dlc(self):
        self.inputs.pop('dlc', None); self.input_errors.pop('dlc', None)
        self.render()

    # ------------------------------------------------------------ choosing files and places

    def add_paths(self, paths):
        """Sort dropped or picked files into patch and game by what they are."""
        if self.busy:
            return
        self.note = ''
        for raw in paths:
            path = Path(raw)
            if path.suffix.lower() in easy.GAME_SUFFIXES and path.is_file():
                self.game_path = path
                self.loose.append(path)
            else:
                self.set_package(path)
        self.check_game()
        self.go('home')

    def set_package(self, path):
        if self.package: self.package.close()
        self.package = self.package_error = None
        self.inputs, self.input_errors = {}, {}
        try:
            self.package = easy.detect_package(path)
        except easy.EasyError as error:
            self.package_error = error
        except Exception as error:
            self.package_error = easy.EasyError('패치 파일을 읽을 수 없습니다', '파일이 손상됐을 수 있어요. 다시 내려받아 주세요.', detail=str(error))

    def check_game(self):
        self.game = self.game_error = None
        if self.recipe():
            for path in self.loose:
                self.assign_input(path)
            self.loose, self.game_path = [], None
            return
        self.loose = []
        if not self.game_path:
            return
        if self.package and not self.package.needs_game:
            self.game_path = None
            self.note = '이 패치는 게임 파일 없이 바로 설치돼요. 게임 파일은 쓰지 않아요.'
            return
        try:
            self.game = easy.check_game(self.game_path, self.package)
        except easy.EasyError as error:
            self.game_error = error

    def pick_any(self):
        chosen = filedialog.askopenfilenames(parent=self.app, title='패치 파일 고르기', filetypes=[
            ('패치 ZIP · 게임 파일', '*.zip *.cia *.3ds *.cci *.zcci'), ('모든 파일', '*.*')])
        if chosen: self.add_paths(chosen)

    def pick_package(self):
        chosen = filedialog.askopenfilename(parent=self.app, title='패치 파일 고르기', filetypes=[('패치 ZIP', '*.zip'), ('모든 파일', '*.*')])
        if chosen: self.add_paths([chosen])

    def pick_game(self):
        chosen = filedialog.askopenfilename(parent=self.app, title='게임 파일 고르기', filetypes=[
            ('3DS 게임 파일', '*.cia *.3ds *.cci *.zcci'), ('모든 파일', '*.*')])
        if chosen:
            self.note, self.game_path = '', Path(chosen)
            self.loose = [self.game_path]
            self.check_game()
            self.go('home')

    def choose_place(self, key):
        self.where, self.note = key, ''
        if key == '3ds' and not self.current_sd(): self.cards = easy.find_sd_cards()
        self.remember()
        self.render()

    def rescan(self):
        self.cards, self.sd_root = easy.find_sd_cards(), None
        self.note = '' if self.cards else 'SD 카드를 찾지 못했어요. 카드 리더기에 꽂혀 있는지 확인해 주세요.'
        self.render()

    def pick_sd(self):
        chosen = filedialog.askdirectory(parent=self.app, title='SD 카드(또는 SD 카드처럼 쓸 폴더) 고르기')
        if chosen:
            self.sd_root, self.note = Path(chosen), ''
            self.render()

    def sd_menu(self):
        self.cards = easy.find_sd_cards()
        menu = tk.Menu(self, tearoff=0)
        for card in self.cards:
            menu.add_command(label=f'{card.label}   (남은 공간 {easy.size_text(card.free)})' + ('   · 3DS 카드로 보여요' if card.likely else ''),
                             command=lambda root=card.root: self.use_sd(root))
        if self.cards: menu.add_separator()
        menu.add_command(label='폴더 직접 고르기…', command=self.pick_sd)
        menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def use_sd(self, root):
        self.sd_root = root
        self.render()

    def pick_emulator(self):
        chosen = filedialog.askdirectory(parent=self.app, title='Azahar 폴더 고르기 (Azahar의 파일 → Azahar 폴더 열기)',
                                         initialdir=str(self.emu_root) if self.emu_root else None)
        if chosen:
            self.emu_root, self.note = Path(chosen), ''
            self.remember(); self.render()

    def pick_keys(self):
        chosen = filedialog.askdirectory(parent=self.app, title='키 파일(aes_keys.txt)이 든 폴더 고르기')
        if chosen:
            self.key_dir, self.note = Path(chosen), ''
            self.remember(); self.render()

    def pick_output(self):
        chosen = filedialog.askdirectory(parent=self.app, title='만든 게임 파일을 저장할 폴더 고르기')
        if chosen:
            self.out_dir = Path(chosen)
            self.render()

    def remember(self):
        easy.save_settings({'place': self.where, 'keys': str(self.key_dir) if self.key_dir else '',
                            'emulator': str(self.emu_root) if self.emu_root else '', 'recipe_mode': self.recipe_mode})

    def open_advanced(self):
        self.app.mode.set('간편 모드')

    def open_help(self, topic=None):
        if self.busy:
            return
        if self.view != 'help': self.help_back = self.view
        self.help_topic = topic or self.help_topic
        self.go('help')

    def reset(self):
        if self.package: self.package.close()
        self.package = self.package_error = self.game = self.game_error = self.game_path = None
        self.inputs, self.input_errors, self.loose = {}, {}, []
        self.note, self.out_dir, self.result, self.failure = '', None, None, None
        self.go('home')

    # ------------------------------------------------------------ running the job

    def start(self, allow_replace=False):
        if self.busy or self.blocker():
            return
        package, game, kind = self.package, self.game, self.package.kind
        target = easy.SD if self.where == '3ds' else easy.EMULATOR
        root = self.current_sd() if self.where == '3ds' else self.emu_root
        fmt = 'CIA' if self.where == '3ds' else 'CCI'
        bridge, xdelta = self.tools()
        keys, out_dir = self.key_dir, self.out_dir
        post = self.messages.put
        self.remember()
        self.note = ''
        mode, inputs, app_dir = self.recipe_mode, dict(self.inputs), self.app_dir
        job_kind, title = kind, '패치된 게임 파일을 만들고 있어요' if kind == easy.MERGE else '패치를 설치하고 있어요'
        if kind == easy.RECIPE:
            job_kind = (kind, 'install' if mode == 'install' else 'rom-' + fmt.lower())
            title = '내 게임 파일로 패치를 만들고 있어요' if mode == 'install' else '합본 게임 파일을 만들고 있어요'
        self.job = {'kind': job_kind, 'phase': 0, 'fraction': None, 'since': time.monotonic(), 'shown': 0.0, 'title': title}
        self.busy = True

        def work():
            try:
                if kind == easy.INSTALL:
                    post(('progress', 0, None))
                    outcome = easy.run_install(package, target, root, allow_replace,
                                               lambda done, total: post(('progress', 1, done / max(1, total))))
                    post(('done', {'mode': 'install', 'target': target, 'outcome': outcome}))
                elif kind == easy.RECIPE:
                    report = lambda phase, fraction: post(('progress', phase, fraction))
                    log = lambda line: post(('log', line))
                    if mode == 'install':
                        outcome = easy.run_recipe(package, inputs, 'install', target, root, allow_replace=allow_replace,
                                                  progress=report, log=log)
                        post(('done', {'mode': 'install', 'target': target, 'outcome': outcome}))
                    else:
                        output = easy.recipe_output(package, inputs, fmt, out_dir)
                        outcome = easy.run_recipe(package, inputs, 'rom', output=output, fmt=fmt, app_dir=app_dir, keys=keys,
                                                  bridge=bridge, progress=report, log=log)
                        post(('done', {'mode': 'game', 'fmt': fmt, 'outcome': outcome, 'allinone': True}))
                elif kind == easy.MERGE:
                    output = easy.default_output(game.path, fmt, out_dir)
                    outcome = easy.run_merge(package, game.path, fmt, keys, bridge, xdelta, output,
                                             lambda phase, fraction: post(('progress', phase, fraction)))
                    post(('done', {'mode': 'game', 'fmt': fmt, 'outcome': outcome}))
                else:
                    outcome = easy.run_delta(package, game.path, target, root, keys, bridge, xdelta, allow_replace,
                                             lambda phase, fraction: post(('progress', phase, fraction)))
                    post(('done', {'mode': 'install', 'target': target, 'outcome': outcome}))
            except easy.NeedsConfirmation as waiting:
                post(('confirm', waiting.plan))
            except subprocess.CalledProcessError as error:
                detail = (error.stderr or b'').decode(errors='replace') or str(error)
                post(('fail', RuntimeError('외부 도구 실패: ' + detail), ''))
            except Exception as error:
                post(('fail', error, traceback.format_exc()))
        threading.Thread(target=work, daemon=True).start()
        self.go('working')

    def tick(self):
        try:
            if self.drops is None and self.winfo_viewable():
                drops = dnd.enable(self.app)
                self.drops = [] if drops is None else drops      # keep the very list the window hook fills
            while self.drops:
                paths = self.drops.pop(0)
                if self.winfo_viewable() and self.view in ('home', 'failed', 'done', 'help'):
                    if self.view in ('failed', 'done'): self.reset()
                    self.add_paths(paths)
            while True:
                message = self.messages.get_nowait()
                if message[0] == 'log':
                    self.app.write(str(message[1]))
                    continue
                if message[0] == 'progress':
                    if message[1] != self.job['phase']:
                        self.job['since'] = time.monotonic()
                    self.job['phase'], self.job['fraction'] = message[1], message[2]
                    continue
                self.busy = False
                if message[0] == 'done':
                    self.result = message[1]
                    self.app.write('초간단 모드 완료: ' + ', '.join(message[1]['outcome'].get('targets', []) or [str(message[1]['outcome'].get('output', ''))]), 'ok')
                    self.go('done')
                elif message[0] == 'confirm':
                    self.pending = message[1]
                    self.go('confirm')
                else:
                    self.failure = easy.explain(message[1])
                    self.app.write('초간단 모드 실패: ' + str(message[1]) + ('\n' + message[2] if message[2] else ''), 'error')
                    self.go('failed')
        except queue.Empty:
            pass
        finally:      # whatever a redraw raised, the next message must still be picked up
            self._tick = self.after(60 if self.busy else 150, self.tick)
        if self.view == 'working' and self.busy:
            self.update_progress()

    # ------------------------------------------------------------ working

    def centre_card(self, x, y, cw, h):
        self.shape(x, y, cw, h, self.p(18), self.t['card'], self.t['line'], shadow=self.t['shadow'])

    def view_working(self, x, y, cw, height):
        t, pad = self.t, self.p(30)
        phases = PHASES[self.job['kind']]
        h = self.p(118) + len(phases) * self.p(34) + pad
        self.centre_card(x, y, cw, h)
        self.label(x + pad, y + pad - self.p(4), self.job['title'], 19, bold=True)
        hint = '창을 닫지 말고 잠시만 기다려 주세요.' + (' 게임 크기에 따라 몇 분 걸릴 수 있어요.' if self.job['kind'] != easy.INSTALL else '')
        self.label(x + pad, y + pad + self.p(28), hint, 13, t['text2'])
        bar_y, bar_w, bar_h = y + pad + self.p(70), cw - 2 * pad, self.p(10)
        self.shape(x + pad, bar_y, bar_w, bar_h, bar_h / 2.0, t['track'])
        self.live = {'bar': (x + pad, bar_y, bar_w, bar_h), 'fill': self.c.create_image(x + pad, bar_y, anchor='nw'),
                     'percent': self.label(x + cw - pad, bar_y - self.p(10), '', 13, t['text2'], True, 'se'), 'rows': [], 'width': -1, 'phase': -1}
        row_y = bar_y + self.p(34)
        for name in phases:
            d = self.p(22)
            dot = self.c.create_image(x + pad, row_y, anchor='nw')
            mark = self.icon(x + pad + d / 2.0, row_y + d / 2.0, 'check', 11, t['success'])
            text = self.label(x + pad + d + self.p(12), row_y + d / 2.0 - self.p(1), name, 14, t['text3'], anchor='w')
            self.live['rows'].append((dot, mark, text))
            row_y += self.p(34)
        self.update_progress()
        return y + h

    def update_progress(self):
        live, job, t = self.live, self.job, self.t
        if not live or 'bar' not in live:
            return
        phases = PHASES[job['kind']]
        local = job['fraction']
        if local is None:      # no figure from the engine: creep forward so the bar never looks frozen
            local = 0.9 * (1.0 - math.exp(-(time.monotonic() - job['since']) / 40.0))
        overall = min(0.995, (job['phase'] + local) / len(phases))
        job['shown'] = max(job['shown'], overall)
        x, y, w, h = live['bar']
        width = max(h, int(w * job['shown']))
        if width != live['width']:
            mode = 'dark' if self.dark else 'light'
            data, _ = paint.rounded(width, h, h / 2.0, PRIMARY_FILL[mode][0])
            live['picture'] = tk.PhotoImage(master=self, data=data, format='png')
            self.c.itemconfigure(live['fill'], image=live['picture'])
            self.c.itemconfigure(live['percent'], text=f"{int(job['shown'] * 100)}%")
            live['width'] = width
        if job['phase'] != live['phase']:
            live['phase'] = job['phase']
            d = self.p(22)
            for index, (dot, mark, text) in enumerate(live['rows']):
                state = 'done' if index < job['phase'] else 'now' if index == job['phase'] else 'wait'
                fill, line = {'done': (t['success_soft'], None), 'now': (t['card'], t['accent']), 'wait': (t['track'], None)}[state]
                picture = self.image(('dot', d, state), lambda: (tk.PhotoImage(master=self, format='png', data=paint.rounded(
                    d, d, d / 2.0, fill, line, self.p(2.5))[0]), 0))[0]
                self.c.itemconfigure(dot, image=picture)
                if mark: self.c.itemconfigure(mark, state='normal' if state == 'done' else 'hidden')
                self.c.itemconfigure(text, fill=t['text'] if state != 'wait' else t['text3'],
                                     font=self.font(14, state == 'now'))

    # ------------------------------------------------------------ result, failure, confirmation, help

    def verdict(self, x, y, cw, glyph, tone, title, body):
        """The big centred icon, headline and explanation shared by the closing views."""
        d, cx = self.p(72), x + cw / 2.0
        self.badge(cx - d / 2.0, y, 72, glyph, tone)
        y += d + self.p(18)
        self.label(cx, y, title, 22, bold=True, anchor='n', width=cw - self.p(40), justify='center')
        y = self.c.bbox(self.c.find_all()[-1])[3] + self.p(8)
        if body:
            item = self.label(cx, y, body, 14, self.t['text2'], anchor='n', width=cw - self.p(80), justify='center')
            y = self.c.bbox(item)[3]
        return y + self.p(20)

    def button_row(self, x, y, cw, buttons):
        height = self.p(46)
        widths = [max(self.p(132), self.button_width(label, 15, bool(icon), kind == 'primary')) for label, _, kind, icon in buttons]
        left = x + (cw - sum(widths) - self.p(10) * (len(widths) - 1)) / 2.0
        for (label, command, kind, icon), width in zip(buttons, widths):
            self.button(left, y, width, height, label, command, kind, icon, size=15)
            left += width + self.p(10)
        return y + height

    def view_done(self, x, y, cw, height):
        t, result = self.t, self.result
        outcome = result['outcome']
        if result['mode'] == 'game':
            output = Path(outcome['output'])
            title, body, shown = '패치된 게임 파일을 만들었어요', '원본 게임 파일은 그대로 있어요.', output
            if result.get('allinone'):
                title = '합본 게임 파일을 만들었어요'
            reveal = lambda: subprocess.Popen(['explorer', '/select,', str(output)])
            steps = easy.next_steps(None, result['fmt'], result.get('allinone', False))
        else:
            shown = Path(outcome['targets'][0])
            reveal = lambda: os.startfile(shown)
            steps = easy.next_steps(result['target'])
            if outcome['state'] == 'same':
                title, body = '이미 설치되어 있어요', '같은 패치가 이미 들어 있어서 바꾼 것이 없어요.'
            else:
                title = '설치를 마쳤어요'
                body = 'SD 카드에 패치를 넣었어요.' if result['target'] == easy.SD else '에뮬레이터에 패치를 넣었어요.'
                if outcome['backups']:
                    body += f"\n전에 있던 폴더는 {Path(outcome['backups'][0]).parent.name} 폴더로 옮겨 두었어요."
        y = self.verdict(x, y + self.p(4), cw, 'check', 'success', title, body)
        chip_h, text = self.p(34), self.fit(str(shown), 13, cw - self.p(120))
        chip_w = self.font(13).measure(text) + self.p(28)
        self.shape(x + (cw - chip_w) / 2.0, y - self.p(6), chip_w, chip_h, chip_h / 2.0, t['track'])
        self.label(x + cw / 2.0, y - self.p(6) + chip_h / 2.0 - self.p(1), text, 13, t['text2'], anchor='center')
        y = self.button_row(x, y + chip_h + self.p(12), cw, [('폴더 열기', reveal, 'primary', 'open'), ('처음으로', self.reset, 'secondary', None)])
        self.primary = self.reset
        y += self.p(22)
        first = len(self.c.find_all())
        pad, top = self.p(22), y
        self.label(x + pad, y + pad - self.p(4), '이제 이렇게 하세요', 15, bold=True)
        y += pad + self.p(26)
        for index, step in enumerate(steps):
            d = self.p(22)
            self.shape(x + pad, y, d, d, d / 2.0, t['accent_soft'])
            self.label(x + pad + d / 2.0, y + d / 2.0 - self.p(1), str(index + 1), 12, t['accent'], True, 'center')
            item = self.label(x + pad + d + self.p(12), y + self.p(1), step, 14, width=cw - 2 * pad - d - self.p(12))
            y = max(y + d, self.c.bbox(item)[3]) + self.p(10)
        if result['mode'] == 'install' and result['target'] == easy.SD:
            y = self.link(x + pad + self.p(34), y, '3DS에서 패치 켜는 방법 자세히 보기', lambda: self.open_help('luma'))[3] + self.p(6)
        self._lower(self.shape(x, top, cw, y - top + pad - self.p(10), self.p(16), t['card'], t['line'], shadow=t['shadow']), first)
        return y + pad

    def view_failed(self, x, y, cw, height):
        title, body, topic, detail = self.failure
        y = self.verdict(x, y + self.p(10), cw, 'warn', 'error', title, body)
        buttons = [('다시 시도', lambda: self.go('home'), 'primary', None)]
        if topic: buttons.append(('해결 방법 보기', lambda: self.open_help(topic), 'secondary', 'help'))
        y = self.button_row(x, y, cw, buttons)
        self.primary = lambda: self.go('home')
        if detail:
            y += self.p(26)
            first = len(self.c.find_all())
            pad, top = self.p(18), y
            self.label(x + pad, y + pad - self.p(4), '자세한 내용', 13, self.t['text2'], True)
            w = self.button_width('복사', 12, True)
            self.button(x + cw - pad - w, y + pad - self.p(10), w, self.p(30), '복사', lambda: self.copy(detail), 'ghost', 'copy', size=12)
            item = self.label(x + pad, y + pad + self.p(20), detail[:420] + ('…' if len(detail) > 420 else ''), 12, self.t['text3'], width=cw - 2 * pad)
            y = self.c.bbox(item)[3] + pad
            self._lower(self.shape(x, top, cw, y - top, self.p(14), self.t['bg'], self.t['line']), first)
        return y

    def copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

    def view_confirm(self, x, y, cw, height):
        plan = self.pending
        where = plan.container / next(iter(plan.titles))
        backup = plan.container.parent / (plan.container.name + '_backup')
        body = (f'{self.fit(str(where), 14, cw - self.p(120))}\n\n이 자리에 다른 버전의 패치나 다른 모드가 이미 있어요.\n'
                f'지우지 않고 {backup.name} 폴더로 옮긴 다음 새 패치를 설치할게요.')
        y = self.verdict(x, y + self.p(10), cw, 'warn', 'warn', '이미 다른 패치가 들어 있어요', body)
        y = self.button_row(x, y, cw, [('옮기고 설치', lambda: self.start(True), 'primary', None), ('취소', lambda: self.go('home'), 'secondary', None)])
        self.primary = lambda: self.start(True)
        return y

    def view_help(self, x, y, cw, height):
        t = self.t
        w = self.button_width('돌아가기', 13, True)
        self.button(x - self.p(8), y - self.p(8), w, self.p(34), '돌아가기', lambda: self.go(self.help_back), 'ghost', 'back', size=13)
        self.primary = lambda: self.go(self.help_back)
        y += self.p(38)
        self.label(x, y, '도움말', 22, bold=True)
        y += self.p(44)
        left, row_h = x, self.p(36)
        for key, (title, *_rest) in HELP.items():
            width = self.font(13).measure(title) + self.p(28)
            if left + width > x + cw:
                left, y = x, y + row_h + self.p(8)
            chosen = key == self.help_topic
            self._n += 1
            tag = f'b{self._n}'
            mode = 'dark' if self.dark else 'light'
            fill = PRIMARY_FILL[mode][0] if chosen else t['card']
            self.c.addtag_withtag(tag, self.shape(left, y, width, row_h, row_h / 2.0, fill, None if chosen else t['button_line']))
            self.label(left + width / 2.0, y + row_h / 2.0 - self.p(1), title, 13, '#FFFFFF' if chosen else t['text'], chosen, 'center', tags=(tag,))
            self.clickable(tag, lambda key=key: self.open_help(key))
            left += width + self.p(8)
        y += row_h + self.p(18)
        title, body, link_label, url = HELP[self.help_topic]
        first = len(self.c.find_all())
        pad, top = self.p(24), y
        self.label(x + pad, y + pad - self.p(4), title, 17, bold=True)
        item = self.label(x + pad, y + pad + self.p(30), body, 14, width=cw - 2 * pad)
        y = self.c.bbox(item)[3] + self.p(14)
        if url:
            w = self.button_width(link_label, 13, True)
            self.button(x + pad, y, w, self.p(36), link_label, lambda: webbrowser.open(url), 'secondary', 'link', size=13)
            y += self.p(36) + self.p(6)
        y += pad - self.p(8)
        self._lower(self.shape(x, top, cw, y - top, self.p(16), t['card'], t['line'], shadow=t['shadow']), first)
        return y

    # ------------------------------------------------------------ keyboard

    def key_enter(self):
        if self.primary and not self.busy: self.primary()

    def key_escape(self):
        if self.busy:
            return
        if self.view == 'help': self.go(self.help_back)
        elif self.view in ('confirm', 'failed'): self.go('home')

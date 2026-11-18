# -*- coding: utf-8 -*-
"""Buddy RPA 前置提醒窗 —— 仿 Windows 11 通知样式（深色圆角，右下角）。

为什么重写（旧版用原生 WS_CAPTION + 原生 ComboBox/Button）：
    原生控件受系统主题约束，必然是浅色、直角边框、带标题栏，还会被强制居中。
    圆角、深色、hover 三件事一件都做不到。
    新版整卡 PIL 自绘 + UpdateLayeredWindow，交互靠自己算矩形命中，
    不生成任何原生控件，所以外观完全可控，也能和 Win11 通知对齐。

外观规格（对齐 Windows 11 toast / 更新提示）：
    卡片 400x184，圆角 8，背景 #202020，1px 内描边（白 8%），下方柔和阴影
    图标 26x26 accent 圆角方块 + 应用名（灰） + 标题（白 16 粗）
    正文倒计时（#D6D6D6） + 1px 分隔线
    底部：[5 分钟后 ▾]  [稍后提醒]  [立即领取]
位置：工作区右下角（多显示器跟随鼠标所在屏），距右边/底边各 16px。不居中。

交互：
    下拉    → 弹出同风格深色列表，选 3/5/10/30/60 分钟
    稍后提醒 → 原地重置倒计时，on_event 回报
    立即领取 → 立即返回，跳过等待
    ×       → 等同于「立即领取」（主动关掉提醒通常意味着「我知道，你直接做」，
              不应因误点 × 而漏领当天积分）

到点自动返回 'timeout'；ask() 阻塞直到窗口销毁。
"""
import ctypes
import os
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
    import splash_render          # 复用其 UpdateLayeredWindow 贴图
    _PIL_OK = True
except Exception:
    _PIL_OK = False

# ---------------- Win32 常量 ----------------
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080

WM_DESTROY = 0x0002
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_TIMER = 0x0113

SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040
IDC_ARROW = 32512
IDC_HAND = 32649
SPI_GETWORKAREA = 0x0030
MONITOR_DEFAULTTONEAREST = 2

TID_TICK = 1
TID_LIST = 2

MINUTES = [3, 5, 10, 30, 60]
DEFAULT_IDX = 1                      # 默认 5 分钟

# ---------------- 逻辑像素布局 ----------------
CARD_W, CARD_H = 400, 184
RADIUS = 8
PAD = 16
SHADOW = 10                          # 阴影留白
WIN_W = CARD_W + SHADOW * 2
WIN_H = CARD_H + SHADOW * 2 + 4
EDGE_MARGIN = 16                     # 距工作区右/下边

BTN_Y, BTN_H = 132, 36
COMBO_X, COMBO_W = 16, 150
SNOOZE_X, SNOOZE_W = 174, 100
NOW_X, NOW_W = 282, 102

ITEM_H = 32
LIST_PAD = 6
LIST_W = COMBO_W
LIST_H = LIST_PAD * 2 + ITEM_H * len(MINUTES)

# ---------------- 配色（Win11 dark toast） ----------------
# 线（卡片描边 / 分隔线 / 按钮描边）必须是**不透明白**：
#   PIL 的 ImageDraw 是「替换像素」而不是「混合」，画 (255,255,255,24) 会把那 1px
#   的 alpha 直接写成 24 —— 等于在卡片上开了一圈 1px 的洞，透出后面的窗口内容。
#   浅色背景上看着像白线，深色背景上就变成暗线，视觉完全取决于背后是什么。
#   改成 alpha=255 后颜色就是它自己，与背后窗体无关。
C_BG = (32, 32, 32, 255)
C_EDGE = (255, 255, 255, 255)
C_LINE = (255, 255, 255, 255)
C_TXT = (255, 255, 255, 255)
C_SUB = (214, 214, 214, 255)
C_MUTED = (160, 160, 160, 255)
C_BTN = (48, 48, 48, 255)
C_BTN_H = (68, 68, 68, 255)
C_BTN_EDGE = (255, 255, 255, 255)
C_ACC = (0, 120, 212, 255)
C_ACC_H = (28, 142, 234, 255)
C_LIST = (43, 43, 43, 255)
C_ITEM_H = (62, 62, 62, 255)

CLASS_MAIN = 'BuddyRpaToastMain'
CLASS_LIST = 'BuddyRpaToastList'

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ('style', wintypes.UINT), ('lpfnWndProc', WNDPROC),
        ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
        ('hInstance', wintypes.HINSTANCE), ('hIcon', ctypes.c_void_p),
        ('hCursor', ctypes.c_void_p), ('hbrBackground', wintypes.HBRUSH),
        ('lpszMenuName', wintypes.LPCWSTR), ('lpszClassName', wintypes.LPCWSTR),
    ]


class _PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ('hdc', wintypes.HDC), ('fErase', wintypes.BOOL),
        ('rcPaint', wintypes.RECT), ('fRestore', wintypes.BOOL),
        ('fIncUpdate', wintypes.BOOL), ('rgbReserved', ctypes.c_byte * 32),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]


user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(_PAINTSTRUCT)]
user32.BeginPaint.restype = wintypes.HDC
user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(_PAINTSTRUCT)]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.ShowCursor.argtypes = [wintypes.BOOL]
user32.ShowCursor.restype = ctypes.c_int
user32.SetActiveWindow.argtypes = [wintypes.HWND]
user32.SetActiveWindow.restype = wintypes.HWND
user32.SetFocus.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_uint
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.KillTimer.restype = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.GetDeviceCaps.restype = ctypes.c_int


# ---------------- 字体 / DPI ----------------
_FONT_CACHE = {}


def _font(size, bold=False):
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    names = ('msyhbd.ttc', 'msyh.ttc') if bold else ('msyh.ttc', 'msyhbd.ttc')
    f = None
    for n in names:
        p = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', n)
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size)
                break
            except Exception:
                pass
    if f is None:
        try:
            f = ImageFont.truetype('simhei.ttf', size)
        except Exception:
            f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def _dpi_scale():
    try:
        hdc = user32.GetDC(None)
        dpi = gdi32.GetDeviceCaps(hdc, 88)      # LOGPIXELSX
        user32.ReleaseDC(None, hdc)
        if dpi >= 72:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


# ---------------- 几何（设备像素） ----------------
def _geo(S):
    ox, oy = SHADOW * S, SHADOW * S

    def R(x, y, w, h):
        return (ox + x * S, oy + y * S, ox + (x + w) * S, oy + (y + h) * S)

    return {
        'card': R(0, 0, CARD_W, CARD_H),
        'close': R(CARD_W - PAD - 24, 12, 24, 24),
        'combo': R(COMBO_X, BTN_Y, COMBO_W, BTN_H),
        'snooze': R(SNOOZE_X, BTN_Y, SNOOZE_W, BTN_H),
        'now': R(NOW_X, BTN_Y, NOW_W, BTN_H),
    }


def _hit(x, y, g):
    for k in ('close', 'combo', 'snooze', 'now'):
        r = g[k]
        if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
            return k
    return None


# ---------------- 渲染 ----------------
def _render(st, S):
    W, H = int(round(WIN_W * S)), int(round(WIN_H * S))
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ox, oy = SHADOW * S, SHADOW * S
    cw, ch = CARD_W * S, CARD_H * S
    r = RADIUS * S
    g = _geo(S)

    # 阴影
    sh = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [ox, oy + 4 * S, ox + cw, oy + ch + 4 * S], radius=r, fill=(0, 0, 0, 110))
    sh = sh.filter(ImageFilter.GaussianBlur(9 * S))
    img = Image.alpha_composite(img, sh)

    d = ImageDraw.Draw(img)
    d.rounded_rectangle([ox, oy, ox + cw, oy + ch], radius=r, fill=C_BG,
                        outline=C_EDGE, width=max(1, int(round(S))))

    def L(x, y):
        return ox + x * S, oy + y * S

    # 应用图标：accent 圆角方块 + 白色闪电
    # （不写字母 B——会和右边「Buddy 加油站」的首字母连成一串，看着像重复）
    ix, iy = L(PAD, 14)
    isz = 26 * S
    d.rounded_rectangle([ix, iy, ix + isz, iy + isz], radius=7 * S, fill=C_ACC)
    bolt = ((14.2, 4.6), (7.6, 14.4), (11.9, 14.4), (10.2, 21.4),
            (18.6, 11.6), (13.8, 11.6))
    d.polygon([(ix + bx * S, iy + by * S) for bx, by in bolt], fill=C_TXT)

    # 应用名（图标右侧留 8px 间隙，不能与图标同 x）
    ax, ay = L(PAD + 26 + 8, 27)
    d.text((ax, ay), 'Buddy 加油站', font=_font(int(round(12 * S))),
           fill=C_MUTED, anchor='lm')

    # 关闭按钮
    cl = g['close']
    if st['hover'] == 'close':
        d.rounded_rectangle(list(cl), radius=4 * S, fill=(255, 255, 255, 30))
    px = (cl[2] - cl[0]) * 0.34
    py = (cl[3] - cl[1]) * 0.34
    lw = max(1, int(round(1.6 * S)))
    d.line([cl[0] + px, cl[1] + py, cl[2] - px, cl[3] - py], fill=(198, 198, 198, 255), width=lw)
    d.line([cl[2] - px, cl[1] + py, cl[0] + px, cl[3] - py], fill=(198, 198, 198, 255), width=lw)

    # 标题
    tx, ty = L(PAD, 62)
    d.text((tx, ty), '积分自动领取', font=_font(int(round(16 * S)), True),
           fill=C_TXT, anchor='lm')

    # 正文倒计时
    rem = max(0, int(st['remain']))
    m, sec = divmod(rem, 60)
    bx, by = L(PAD, 92)
    d.text((bx, by), '将在 %d 分 %02d 秒后自动开始领取' % (m, sec),
           font=_font(int(round(13 * S))), fill=C_SUB, anchor='lm')

    # 分隔线
    ly = oy + 114 * S
    d.line([ox + PAD * S, ly, ox + (CARD_W - PAD) * S, ly],
           fill=C_LINE, width=max(1, int(round(S))))

    # 下拉框
    rc = g['combo']
    d.rounded_rectangle(list(rc), radius=4 * S,
                        fill=C_BTN_H if st['hover'] == 'combo' or st['open'] else C_BTN,
                        outline=C_BTN_EDGE, width=max(1, int(round(S))))
    d.text((rc[0] + 10 * S, (rc[1] + rc[3]) / 2), '%d 分钟后' % MINUTES[st['sel']],
           font=_font(int(round(13 * S))), fill=C_TXT, anchor='lm')
    axc, ayc = rc[2] - 14 * S, (rc[1] + rc[3]) / 2
    d.line([axc - 4 * S, ayc - 2 * S, axc, ayc + 2.5 * S, axc + 4 * S, ayc - 2 * S],
           fill=C_SUB, width=max(1, int(round(1.6 * S))), joint='curve')

    # 按钮
    def btn(rect, text, hover, accent=False):
        if accent:
            bg = C_ACC_H if hover else C_ACC
            edge = (0, 0, 0, 0)
        else:
            bg = C_BTN_H if hover else C_BTN
            edge = C_BTN_EDGE
        d.rounded_rectangle(list(rect), radius=4 * S, fill=bg, outline=edge,
                            width=max(1, int(round(S))))
        d.text(((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2), text,
               font=_font(int(round(13 * S))), fill=C_TXT, anchor='mm')

    btn(g['snooze'], '稍后提醒', st['hover'] == 'snooze')
    btn(g['now'], '立即领取', st['hover'] == 'now', accent=True)
    return img


def _render_list(sel, hover_idx, S):
    W, H = int(round(LIST_W * S)), int(round(LIST_H * S))
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=8 * S, fill=C_LIST,
                        outline=C_BTN_EDGE, width=max(1, int(round(S))))
    lw = max(2, int(round(1.8 * S)))
    for i, mn in enumerate(MINUTES):
        y0 = (LIST_PAD + i * ITEM_H) * S
        h = ITEM_H * S
        if i == hover_idx:
            d.rounded_rectangle([4 * S, y0 + 2 * S, W - 4 * S, y0 + h - 2 * S],
                                radius=4 * S, fill=C_ITEM_H)
        col = C_TXT if i == sel else C_SUB
        d.text((12 * S, y0 + h / 2), '%d 分钟后' % mn,
               font=_font(int(round(13 * S))), fill=col, anchor='lm')
        if i == sel:
            cx, cy = W - 20 * S, y0 + h / 2
            d.line([cx - 4 * S, cy, cx - 1 * S, cy + 3.2 * S, cx + 5 * S, cy - 3.6 * S],
                   fill=C_ACC, width=lw, joint='curve')
    return img


def _premultiply(img):
    """UpdateLayeredWindow 要预乘 alpha；不预乘的话圆角边缘和阴影会发白。"""
    a = img.split()[3]
    rgb = ImageChops.multiply(img.convert('RGB'), Image.merge('RGB', (a, a, a)))
    out = rgb.convert('RGBA')
    out.putalpha(a)
    return out


# ---------------- 全局状态 ----------------
_ST = {
    'remain': 300, 'sel': DEFAULT_IDX, 'hover': None, 'hover_item': -1,
    'open': False, 'snoozed': 0, 'action': 'timeout',
    'on_event': None, 'main': None, 'list': None, 'pos': (0, 0),
    'S': 1.0, 'dirty': True,
}
_proc_main = None
_proc_list = None


def _blit_main():
    if not _ST['main']:
        return
    try:
        img = _premultiply(_render(_ST, _ST['S']))
        splash_render.blit(_ST['main'], img, _ST['pos'][0], _ST['pos'][1])
    except Exception:
        pass


def _blit_list():
    if not _ST['list']:
        return
    try:
        img = _premultiply(_render_list(_ST['sel'], _ST['hover_item'], _ST['S']))
        splash_render.blit(_ST['list'], img, _ST['list_pos'][0], _ST['list_pos'][1])
    except Exception:
        pass


def _repaint(force=True):
    if force or _ST['dirty']:
        _blit_main()


def _lxy(lp):
    return ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value


def _set_cursor(kind):
    try:
        cur = user32.LoadCursorW(None, ctypes.c_void_p(IDC_HAND if kind else IDC_ARROW))
        user32.SetCursor(cur)
    except Exception:
        pass


# ---------------- 列表窗口 ----------------
def _close_list():
    if _ST['list']:
        h = _ST['list']
        _ST['list'] = None
        _ST['open'] = False
        _ST['hover_item'] = -1
        try:
            user32.KillTimer(h, TID_LIST)
            user32.DestroyWindow(h)
        except Exception:
            pass
        _blit_main()
        if _ST['main']:
            user32.SetTimer(_ST['main'], TID_TICK, 1000, None)


def _open_list():
    if _ST['list'] or not _ST['main']:
        return
    S = _ST['S']
    g = _geo(S)
    left, top = _ST['pos']
    lw, lh = int(round(LIST_W * S)), int(round(LIST_H * S))
    x = int(left + g['combo'][0])
    y = int(top + g['combo'][1]) - lh - int(round(4 * S))
    _ST['list_pos'] = (x, y)
    h = user32.CreateWindowExW(
        WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
        CLASS_LIST, '', WS_POPUP, x, y, lw, lh,
        _ST['main'], None, kernel32.GetModuleHandleW(None), None)
    if not h:
        return
    _ST['list'] = h
    _ST['open'] = True
    _ST['hover_item'] = _ST['sel']
    _blit_list()
    user32.ShowWindow(h, SW_SHOWNOACTIVATE)
    user32.SetTimer(h, TID_LIST, 110, None)
    _blit_main()


def _list_wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_ERASEBKGND:
        return 1
    if msg == WM_PAINT:
        ps = _PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        if hdc:
            user32.EndPaint(hwnd, ctypes.byref(ps))
        return 0
    if msg == WM_MOUSEMOVE:
        x, y = _lxy(lparam)
        idx = int((y - LIST_PAD * _ST['S']) // (ITEM_H * _ST['S']))
        if idx != _ST['hover_item'] and 0 <= idx < len(MINUTES):
            _ST['hover_item'] = idx
            _blit_list()
        return 0
    if msg == WM_LBUTTONDOWN:
        x, y = _lxy(lparam)
        idx = int((y - LIST_PAD * _ST['S']) // (ITEM_H * _ST['S']))
        if 0 <= idx < len(MINUTES):
            _ST['sel'] = idx
            _close_list()
            if _ST['on_event']:
                try:
                    _ST['on_event']('pick %d min' % MINUTES[idx])
                except Exception:
                    pass
        return 0
    if msg == WM_TIMER and wparam == TID_LIST:
        # 列表打开时轮询：左键按在列表和下拉框以外就收起（不抢输入）
        if user32.GetAsyncKeyState(0x01) & 0x8000:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            lx, ly = _ST['list_pos']
            S = _ST['S']
            in_list = (lx <= pt.x <= lx + LIST_W * S and ly <= pt.y <= ly + LIST_H * S)
            g = _geo(S)
            mx, my = _ST['pos']
            in_combo = (mx + g['combo'][0] <= pt.x <= mx + g['combo'][2]
                        and my + g['combo'][1] <= pt.y <= my + g['combo'][3])
            if not (in_list or in_combo):
                _close_list()
        return 0
    if msg == WM_DESTROY:
        _ST['list'] = None
        _ST['open'] = False
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


# ---------------- 主窗口 ----------------
def _main_wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_ERASEBKGND:
        return 1
    if msg == WM_PAINT:
        ps = _PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        if hdc:
            user32.EndPaint(hwnd, ctypes.byref(ps))
        return 0
    if msg == WM_MOUSEMOVE:
        x, y = _lxy(lparam)
        k = _hit(x, y, _geo(_ST['S']))
        if k != _ST['hover']:
            _ST['hover'] = k
            _blit_main()
        _set_cursor(k is not None)
        # 追踪鼠标离开：进来后没再收到 mousemove 时靠 tick 兜底
        return 0
    if msg == WM_LBUTTONDOWN:
        x, y = _lxy(lparam)
        k = _hit(x, y, _geo(_ST['S']))
        if k == 'close':
            _ST['action'] = 'dismiss'
            user32.DestroyWindow(hwnd)
        elif k == 'now':
            _ST['action'] = 'now'
            user32.DestroyWindow(hwnd)
        elif k == 'snooze':
            _ST['remain'] = MINUTES[_ST['sel']] * 60
            _ST['snoozed'] += 1
            if _ST['on_event']:
                try:
                    _ST['on_event']('snooze %d min (round %d)'
                                    % (MINUTES[_ST['sel']], _ST['snoozed']))
                except Exception:
                    pass
            _blit_main()
        elif k == 'combo':
            if _ST['open']:
                _close_list()
            else:
                _open_list()
        return 0
    if msg == WM_TIMER and wparam == TID_TICK:
        # 鼠标已在窗口外但没收到 mousemove：清掉 hover 态
        try:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            rc = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            inside = (rc.left <= pt.x <= rc.right and rc.top <= pt.y <= rc.bottom)
            if not inside and _ST['hover'] is not None:
                _ST['hover'] = None
                _blit_main()
        except Exception:
            pass
        _ST['remain'] -= 1
        if _ST['remain'] <= 0:
            _ST['remain'] = 0
            _ST['action'] = 'timeout'
            user32.DestroyWindow(hwnd)
            return 0
        _blit_main()
        return 0
    if msg == WM_DESTROY:
        user32.KillTimer(hwnd, TID_TICK)
        if _ST['list']:
            try:
                user32.DestroyWindow(_ST['list'])
            except Exception:
                pass
            _ST['list'] = None
        _ST['main'] = None
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _register_classes():
    global _proc_main, _proc_list
    hinst = kernel32.GetModuleHandleW(None)
    # 与 rpa_splash 共用进程：argtypes 必须在这里重设，否则被对方覆盖成 POINTER(_WNDCLASSW)
    user32.RegisterClassW.argtypes = [ctypes.c_void_p]
    user32.RegisterClassW.restype = wintypes.ATOM

    _proc_main = WNDPROC(_main_wndproc)
    wc = _WNDCLASSW()
    wc.style = 0
    wc.lpfnWndProc = _proc_main
    wc.hInstance = hinst
    wc.hbrBackground = None
    wc.lpszClassName = CLASS_MAIN
    user32.RegisterClassW(ctypes.byref(wc))

    _proc_list = WNDPROC(_list_wndproc)
    wc2 = _WNDCLASSW()
    wc2.style = 0
    wc2.lpfnWndProc = _proc_list
    wc2.hInstance = hinst
    wc2.hbrBackground = None
    wc2.lpszClassName = CLASS_LIST
    user32.RegisterClassW(ctypes.byref(wc2))


def _workarea():
    """返回工作区 (left, top, right, bottom)：多显示器跟随鼠标所在屏。"""
    try:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        mon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
            r = mi.rcWork
            if r.right > r.left and r.bottom > r.top:
                return (r.left, r.top, r.right, r.bottom)
    except Exception:
        pass
    rc = wintypes.RECT()
    try:
        if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rc), 0):
            return (rc.left, rc.top, rc.right, rc.bottom)
    except Exception:
        pass
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    return (0, 0, sw, sh)


def ask(seconds=300, on_event=None):
    """显示右下角通知样式提醒窗，阻塞到结束。

    返回 {'action': 'timeout'|'now'|'dismiss', 'snoozed': N}
    PIL 不可用时直接返回 timeout（不阻断领取主链路）。
    """
    if not _PIL_OK:
        return {'action': 'timeout', 'snoozed': 0}

    S = _dpi_scale()
    _ST.update({'remain': int(seconds), 'sel': DEFAULT_IDX, 'hover': None,
                'hover_item': -1, 'open': False, 'snoozed': 0,
                'action': 'timeout', 'on_event': on_event, 'S': S,
                'main': None, 'list': None})

    _register_classes()
    hinst = kernel32.GetModuleHandleW(None)

    w = int(round(WIN_W * S))
    h = int(round(WIN_H * S))
    wa = _workarea()
    x = wa[2] - w - int(round(EDGE_MARGIN * S)) + int(round(SHADOW * S))
    y = wa[3] - h - int(round(EDGE_MARGIN * S)) + int(round(SHADOW * S))
    _ST['pos'] = (x, y)

    hwnd = user32.CreateWindowExW(
        WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
        CLASS_MAIN, 'Buddy 积分自动领取',
        WS_POPUP, x, y, w, h, None, None, hinst, None)
    if not hwnd:
        return {'action': 'timeout', 'snoozed': 0}
    _ST['main'] = hwnd

    _blit_main()
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    user32.SetTimer(hwnd, TID_TICK, 1000, None)

    msg = wintypes.MSG()
    while True:
        r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if r <= 0:
            break
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    return {'action': _ST['action'], 'snoozed': _ST['snoozed']}


if __name__ == '__main__':
    import sys
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    print(ask(secs, on_event=lambda m: print('[event]', m)))

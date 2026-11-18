# -*- coding: utf-8 -*-
"""Buddy RPA 全屏提示遮罩（splash）—— 自绘磨砂版。

渲染链路：
    截当前桌面 → 高斯模糊 → 叠色调蒙层 → 画中央卡片 → UpdateLayeredWindow 贴到全屏窗口

为什么不再用系统 Acrylic：
    Acrylic 的模糊半径由系统写死，只有 tint 的 alpha 能间接影响浓度，
    「模糊度」根本不可调，Win10/Win11 效果还不一致。改自绘后 blur / 浓度 /
    卡片透明度都变成 splash_config.json 里的真实数字，且与调参器所见完全一致。

参数：同目录 splash_config.json，由 splash_tuner.py 调好后写入。
      文件缺失或损坏时用 splash_render.DEFAULTS，不影响领取。

窗口属性（缺一不可）：
- LAYERED            UpdateLayeredWindow 贴图的前提，也是鼠标穿透真正生效的前提
- TRANSPARENT +      RPA 的 SetCursorPos / mouse_event 要能打到下层 WorkBuddy
  WM_NCHITTEST=HTTRANSPARENT
- NOACTIVATE         绝不抢焦点，否则 buddy_station 的 bring_to_front 会更难成功
- TOPMOST            盖在 WorkBuddy 之上
- TOOLWINDOW         不进任务栏 / Alt+Tab

文字由 PIL 直接画进位图，不生成任何 UIA 控件节点 —— 否则"领取""加油站"
这些字会被 buddy_station 的 scan_all() 扫到，误判成页面状态。
"""
import ctypes
import threading
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
    from PIL import ImageGrab
    import splash_render
    _RENDER = True
except Exception:                                   # 渲染依赖缺失时退回纯色遮罩
    _RENDER = False
    splash_render = None

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
LWA_ALPHA = 0x00000002

WM_DESTROY = 0x0002
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_NCHITTEST = 0x0084
WM_APP_STOP = 0x8000 + 1
HTTRANSPARENT = -1

SW_SHOWNOACTIVATE = 4
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

FALLBACK_ALPHA = 150          # 渲染不可用时的纯色遮罩不透明度

CLASS_NAME = 'BuddyRpaSplash'

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
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
user32.FillRect.restype = ctypes.c_int
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF,
                                              wintypes.BYTE, wintypes.DWORD]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t

gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteObject.restype = wintypes.BOOL

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

_proc_ref = None
_ready = threading.Event()
_hwnd = None
_thread = None
_use_lwa = False        # True = 已用 UpdateLayeredWindow 贴图（此时不能再 SetLayeredWindowAttributes）


def _paint(hwnd):
    """只在贴图失败、退回纯色遮罩时才真正绘制。"""
    ps = _PAINTSTRUCT()
    hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
    if not hdc:
        return
    try:
        if not _use_lwa:
            rc = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rc))
            bg = gdi32.CreateSolidBrush(0xF2F3F5)
            user32.FillRect(hdc, ctypes.byref(rc), bg)
            gdi32.DeleteObject(bg)
    finally:
        user32.EndPaint(hwnd, ctypes.byref(ps))


def _wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_ERASEBKGND:
        return 1
    if msg == WM_NCHITTEST:
        return HTTRANSPARENT
    if msg == WM_PAINT:
        _paint(hwnd)
        return 0
    if msg == WM_APP_STOP:
        user32.DestroyWindow(hwnd)
        user32.PostQuitMessage(0)
        return 0
    if msg == WM_DESTROY:
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _register():
    wc = _WNDCLASSW()
    wc.style = 0
    proc = WNDPROC(_wndproc)
    wc.lpfnWndProc = proc
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = CLASS_NAME
    user32.RegisterClassW.argtypes = [ctypes.c_void_p]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.RegisterClassW(ctypes.byref(wc))
    return proc


def _run():
    global _hwnd, _use_lwa, _proc_ref
    _proc_ref = _register()

    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) or user32.GetSystemMetrics(0)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) or user32.GetSystemMetrics(1)

    ex = (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE |
          WS_EX_TOPMOST | WS_EX_TOOLWINDOW)
    hwnd = user32.CreateWindowExW(ex, CLASS_NAME, '', WS_POPUP,
                                  vx, vy, vw, vh, None, None,
                                  kernel32.GetModuleHandleW(None), None)
    _hwnd = hwnd
    if not hwnd:
        _ready.set()
        return

    # 此刻窗口尚未 Show，截屏不会拍到自己
    _use_lwa = False
    if _RENDER:
        try:
            bg = ImageGrab.grab(all_screens=True)
            img = splash_render.compose(bg, splash_render.load(), 1.0)
            _use_lwa = splash_render.blit(hwnd, img, vx, vy)
        except Exception:
            _use_lwa = False
    if not _use_lwa:
        user32.SetLayeredWindowAttributes(hwnd, 0, FALLBACK_ALPHA, LWA_ALPHA)

    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    _ready.set()

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def start():
    """启动遮罩（独立线程），返回窗口句柄；失败返回 None。"""
    global _thread, _hwnd
    if _thread is not None and _thread.is_alive():
        return _hwnd
    _ready.clear()
    _hwnd = None
    _thread = threading.Thread(target=_run, name='buddy-splash', daemon=True)
    _thread.start()
    _ready.wait(8)
    return _hwnd


def stop():
    """关闭遮罩并等待线程退出。销毁动作由窗口所属线程自己完成。"""
    global _thread, _hwnd
    if _hwnd:
        try:
            user32.PostMessageW(_hwnd, WM_APP_STOP, 0, 0)
        except Exception:
            pass
    if _thread is not None:
        _thread.join(timeout=5)
    _hwnd = None
    _thread = None

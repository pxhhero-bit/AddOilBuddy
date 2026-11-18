# -*- coding: utf-8 -*-
"""Splash 视觉参数 + 合成渲染。

调参器（splash_tuner.py）和真遮罩（rpa_splash.py）共用这一套算法与参数文件，
所以调参器里看到什么，实际运行就是什么。

为什么不用系统 Acrylic：
    Acrylic 的模糊半径由系统固定，透明度只能靠 tint 的 alpha 间接影响，
    「模糊度」这个维度根本没法调。改自绘（截图 → 高斯模糊 → 叠色 → 贴图）
    之后两个维度都变成真正的数字，跨 Win10/Win11 表现也一致。

参数文件：同目录 splash_config.json
"""
import json
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'splash_config.json')

# 默认值 = 当前线上效果的等效值（Acrylic 45% 淡蓝白，约等效 24px 模糊）
DEFAULTS = {
    'blur': 24,                # 背景模糊半径（px）。0 = 不模糊
    'veil_alpha': 150,         # 蒙层不透明度 0-255。越小越透，能看到背景
    'tint': [246, 248, 250],   # 蒙层色调 RGB（偏冷白）
    'card_alpha': 242,         # 卡片不透明度 0-255
    'card_w': 760,             # 卡片宽
    'card_h': 240,             # 卡片高
    'card_radius': 18,         # 卡片圆角
    'card_dy': -40,            # 卡片垂直偏移（负 = 往上）
    'shadow': 70,              # 卡片阴影强度 0-255
}

TITLE = '正在自动领取 Buddy 加油站积分'
SUBTITLE = '请勿操作鼠标与键盘，完成后自动关闭'


def load():
    """读参数文件；缺失或损坏都退回默认值，绝不抛异常打断领取。"""
    p = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            d = json.load(f)
        for k in DEFAULTS:
            if k in d:
                v = d[k]
                if k == 'tint' and (not isinstance(v, list) or len(v) != 3):
                    continue
                p[k] = v
    except Exception:
        pass
    return p


def save(params):
    tmp = CONFIG_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(params, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


_FONT_CACHE = {}


def _font(size, bold=False):
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    names = ('msyhbd.ttc', 'msyh.ttc') if bold else ('msyh.ttc', 'msyhbd.ttc')
    f = None
    for n in names:
        fp = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', n)
        if os.path.exists(fp):
            try:
                f = ImageFont.truetype(fp, size)
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


def blur_bg(img, radius):
    """快速高斯模糊：先降采样再模糊再放大，大半径也不会卡。"""
    if radius <= 0:
        return img.convert('RGB')
    w, h = img.size
    scale = max(1, min(4, int(radius // 8) + 1))
    sw, sh = max(1, w // scale), max(1, h // scale)
    small = img.resize((sw, sh), Image.BILINEAR)
    small = small.filter(ImageFilter.GaussianBlur(radius / scale))
    return small.resize((w, h), Image.BICUBIC)


def _draw_card(img, p, sc):
    """在 img(RGB) 上画卡片。只在卡片外扩一圈的局部区域做 alpha 合成——
    全屏 RGBA 合成要 200ms+，局部只要十几毫秒。"""
    W, H = img.size
    cw, ch = int(p['card_w'] * sc), int(p['card_h'] * sc)
    r = max(0, int(p['card_radius'] * sc))
    cx = (W - cw) // 2
    cy = (H - ch) // 2 + int(p['card_dy'] * sc)

    pad = int(max(48, 40 * sc))
    x0, y0 = max(0, cx - pad), max(0, cy - pad)
    x1, y1 = min(W, cx + cw + pad), min(H, cy + ch + pad)
    bw, bh = x1 - x0, y1 - y0
    if bw <= 2 or bh <= 2:
        return img
    ox, oy = cx - x0, cy - y0

    layer = Image.new('RGBA', (bw, bh), (0, 0, 0, 0))

    shadow_a = max(0, min(255, int(p['shadow'])))
    if shadow_a > 0:
        sh = Image.new('RGBA', (bw, bh), (0, 0, 0, 0))
        ds = ImageDraw.Draw(sh)
        off = max(2, int(8 * sc))
        ds.rounded_rectangle([ox, oy + off, ox + cw, oy + ch + off],
                             radius=r, fill=(18, 24, 34, shadow_a))
        sh = sh.filter(ImageFilter.GaussianBlur(max(4, 16 * sc)))
        layer = Image.alpha_composite(layer, sh)

    card_a = max(0, min(255, int(p['card_alpha'])))
    if card_a > 0:
        dc = ImageDraw.Draw(layer)
        dc.rounded_rectangle([ox, oy, ox + cw, oy + ch], radius=r,
                             fill=(255, 255, 255, card_a))
        f1 = _font(max(12, int(37 * sc)), bold=True)
        f2 = _font(max(10, int(21 * sc)), bold=False)
        dc.text((ox + cw / 2, oy + ch * 0.34), TITLE, font=f1,
                fill=(31, 35, 41, card_a), anchor='mm')
        dc.text((ox + cw / 2, oy + ch * 0.68), SUBTITLE, font=f2,
                fill=(92, 100, 112, card_a), anchor='mm')

    crop = img.crop((x0, y0, x1, y1)).convert('RGBA')
    out = Image.alpha_composite(crop, layer).convert('RGB')
    img.paste(out, (x0, y0))
    return img


def compose(bg, params, quality=1.0):
    """把屏幕截图合成成 splash 最终画面（RGB）。

    bg      : PIL 图像（桌面截图，不能包含 splash 自己）
    params  : load() 返回的参数字典
    quality : 1.0 全质量；0.55 左右用于拖动滑块时的即时预览
    """
    p = params
    src_w, src_h = bg.size
    if quality < 1.0:
        w = max(2, int(src_w * quality))
        h = max(2, int(src_h * quality))
    else:
        w, h = src_w, src_h

    img = bg.convert('RGB')
    if (w, h) != (src_w, src_h):
        img = img.resize((w, h), Image.BILINEAR)

    # 1) 背景模糊
    img = blur_bg(img, p['blur'] * quality)

    # 2) 蒙层（色调 + 浓度）
    veil_a = max(0, min(255, int(p['veil_alpha'])))
    if veil_a > 0:
        tint = tuple(int(c) for c in p['tint'])
        overlay = Image.new('RGB', img.size, tint)
        img = Image.blend(img, overlay, veil_a / 255.0)

    # 3) 卡片
    sc = w / float(src_w) if src_w else 1.0
    img = _draw_card(img, p, sc)

    if (w, h) != (src_w, src_h):
        img = img.resize((src_w, src_h), Image.BICUBIC)
    return img


def compose_fullscreen(params, quality=1.0):
    """截当前桌面并合成（调用前必须确保 splash 窗口不可见，否则会截到自己）。"""
    from PIL import ImageGrab
    bg = ImageGrab.grab(all_screens=True)
    return compose(bg, params, quality)


# ---------- 贴图（layered 窗口） ----------
import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', wintypes.DWORD), ('biWidth', ctypes.c_long),
        ('biHeight', ctypes.c_long), ('biPlanes', wintypes.WORD),
        ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
        ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', ctypes.c_long),
        ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wintypes.DWORD),
        ('biClrImportant', wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', _BITMAPINFOHEADER), ('bmiColors', wintypes.DWORD * 3)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [('BlendOp', ctypes.c_ubyte), ('BlendFlags', ctypes.c_ubyte),
                ('SourceConstantAlpha', ctypes.c_ubyte), ('AlphaFormat', ctypes.c_ubyte)]


class _POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


class _SIZE(ctypes.Structure):
    _fields_ = [('cx', ctypes.c_long), ('cy', ctypes.c_long)]


_user32.GetDC.restype = wintypes.HDC
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.UpdateLayeredWindow.restype = wintypes.BOOL
_user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(_POINT), ctypes.POINTER(_SIZE),
    wintypes.HDC, ctypes.POINTER(_POINT), wintypes.DWORD,
    ctypes.POINTER(_BLENDFUNCTION), wintypes.DWORD]
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
                                    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, ctypes.c_void_p]
_gdi32.SelectObject.restype = ctypes.c_void_p
_gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
_gdi32.DeleteDC.argtypes = [wintypes.HDC]


def blit(hwnd, img, x=0, y=0):
    """把 PIL 图贴到 layered 窗口（窗口尺寸自动跟随图片）。成功返回 True。"""
    w, h = img.size
    data = img.convert('RGBA').tobytes('raw', 'BGRA')
    hdc_screen = _user32.GetDC(None)
    if not hdc_screen:
        return False
    hdc_mem = _gdi32.CreateCompatibleDC(hdc_screen)
    ok = False
    bmi = _BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    ppv = ctypes.c_void_p()
    hbmp = _gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), 0,
                                   ctypes.byref(ppv), None, 0)
    if hbmp and ppv:
        ctypes.memmove(ppv, data, len(data))
        old = _gdi32.SelectObject(hdc_mem, hbmp)
        blend = _BLENDFUNCTION(0, 0, 255, 1)
        pt_dst = _POINT(int(x), int(y))
        pt_src = _POINT(0, 0)
        size = _SIZE(w, h)
        ok = bool(_user32.UpdateLayeredWindow(hwnd, hdc_screen, ctypes.byref(pt_dst),
                                              ctypes.byref(size), hdc_mem,
                                              ctypes.byref(pt_src), 0,
                                              ctypes.byref(blend), 2))
        _gdi32.SelectObject(hdc_mem, old)
        _gdi32.DeleteObject(hbmp)
    _gdi32.DeleteDC(hdc_mem)
    _user32.ReleaseDC(None, hdc_screen)
    return ok

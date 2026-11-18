# -*- coding: utf-8 -*-
"""Buddy 加油站每日自动领取（本机 RPA）。

设计原则：**执行期零 UIA 扫描，全部坐标由窗口矩形推算。**

坐标基准实测结论（9 种窗口尺寸交叉验证，含高度自适应）：
    设窗口矩形 = (L, T, R, B)，H = B - T
    用户条   = (L+8,   B-48,  L+200, B-8)            9/9 全中，与窗口尺寸无关
    铃铛     = (L+204, B-44,  L+236, B-12)           9/9 全中
    Hero入口 = (L+25,  B-426, L+W, B-426+88)         H>=721
               (L+25,  T+295, L+W, T+295+88)         H< 721（浮层被钳制）
    浮窗卡片 = (L+12,  B-172, L+271, B-60)
    轮播按钮 = (L+222, B-83)      中心
    关闭 ×   = (L+251, B-152)    中心
    其中 W = 311（H>=760）/ 301（H<760，滚动条把浮层里的入口挤窄 10px）。

基准是窗口**左下角**而不是顶框——弹层是从底部工具条向上弹出的。
左侧栏高度自适应只影响两处，且都在矮窗下才出现：滚动条让浮层窄 10px；
H<721 时浮层顶部被钳制在 T+295（实测 H=700/640/600 全部停在这个位置）。

为什么不用扫描：
    一次 scan_all(236 节点 × 6 属性) ≈ 978ms，原流程扫 8-10 次 ≈ 8-10 秒。
    改成推算后，执行期成本只剩像素采样（复用位图后单次抓图 16.7ms），完全不碰 UIA。

按钮底色判定（2026-11-18 新增，取代「UIA 读按钮名」+「三步阶梯等待 + 确认弹层 walk」）：
    未领取 = 黑底白字，底色 (26,26,26)；已领取 = 灰底灰字，底色 (235,235,235)。
    两张实测截图交叉测得：暗像素占比 0.638 vs 0.000，中位亮度 26 vs 235——
    两态分得极开，不需要模糊判定，单次采样就够。
    采样窗口**右对齐**：按钮宽度随文案变（「今日已领取」74px /「立即使用」61px），
    但右边距(15)、高(24)、底边距(10)三种尺寸都一致，所以固定取右侧 74×24 全部像素，
    宽度多出来的部分落在白卡上，只是不贡献暗像素，不影响判据。

启动前置：清残留卡片（2026-11-18 新增）
    上一轮若关窗失败（或被中断），浮层会留在屏幕上。此时 card 区域**始终是卡片**，
    后续「点入口 → 等浮层出现」的像素检测就失明了：区域本来就没变，wait_change 必然超时，
    重试一轮后仍然失败 —— 这就是次日 `rc=4 FAIL no popup` 的成因。
    所以开跑先问一句「卡片是不是已经在屏幕上」，在就先点 × 清掉。
    前置**只做清卡片这一件事**，不做「菜单是否展开」的判断——试过，不可靠：

    判据纯像素，两个条件缺一不可（都是踩出来的）：
      1) 整块是成片白：卡片容器 **#FFFFFF(255)**，没有卡片时那块是主界面背景
         **#F7F7F7(247)**。实测有卡 0.855 / 0.854（两张不同内容的卡）、无卡 0.000。
         再要求 4x3 分块里至少 6 块也是白的（有卡 11/12、无卡 0/12），
         防止主界面恰好滚出一块白色图片被当成卡片。
      2) 右下角有实心按钮：**这条是必须的**。点用户条弹出的底部面板有时显示的是
         「意见反馈/检查更新/退出登录」，它同样是一整块白（0.85），只看白占比会把它
         当成卡片，然后去点 × —— 那个位置正好是「检查更新」，会点错东西。
         真卡片的按钮是灰底 235（已领取）或黑底 26（未领取），而用户菜单在该位置是
         **纯白 255（min=max=255）**、无卡片时是 247。阈值卡在 245。
         四态实测：已领取卡 235 / 推广卡黑按钮 26 / 用户菜单 255 / 无卡片 247。

    为什么不靠白占比判断「菜单是否展开」来跳过用户条：
        试过，entry 区域白占比在「Hero 面板」和「用户菜单页」上都高达 0.9，
        分不出来；清完卡片若误以为菜单还开着而跳过用户条，点入口就落在空处。
        所以宁可无条件点一下用户条（toggle），状态乱了由后面的 popup retry 兜底。

为什么不等轮播、不读按钮文字：
    点入口后初始页面必然是「立即领取」卡（推广卡要等约 6 秒才轮播过来）。
    只要「点入口 → 点领取」间隔够短，就不会碰到推广卡。
    注意：**只有在浮层已经开着**（残留场景）时才可能停在推广卡上，那由上面的前置清掉。

卡片「已经在」时为什么不按区域变化等：
    菜单一展开卡片往往就渲染好了（实测白占比 0.873），这时点入口不会让这块区域再变
    一次。还按「区域变化」等就必然等满 POPUP_WAIT_MAX（1.8s），然后走 retry；
    而 retry 里那次点用户条会把菜单关掉，反而真失败（11-18 复现过）。
    所以先看状态：卡片已经在就跳过变化等待，只等画面稳定（淡入会持续约 50ms）。
    同理，按钮还没画出来时（整块白 med=255）不能算「灰底已领取」——
    旧判据 `med>=200` 正好把这种情况吃了进去，导致点了空、连 × 都点不掉。

为什么「等渲染」用检测而不是硬计时（实测数据）：
    单帧抓图 16.7ms、采样周期 31ms，检测滞后于真实渲染约 31ms——也就是说检测本身
    几乎不占时间，time 全花在应用渲染上。渲染耗时又极不稳定：
        菜单展开  冷 850ms / 热 260~310ms
        浮层出现  冷 980ms / 热 380ms
    用固定 900ms 硬计时，冷启动那天一样要等满 900（省不到），热启动时白等
    600ms。检测是自适应的，冷热都刚好停住，所以保留检测、只把上限收紧。

用法: python buddy_station.py <stage>
  claim   : 完整领取链路
  menu    : 调试——只导航到加油站浮层出现为止
  probe   : 调试——导航后采样按钮底色并留存图片，**不点领取**，然后关窗
  nocheck : 同 claim（兼容旧调用）

返回码:
  0  CLAIM_OK       真正的领取动作已发生
  7  ALREADY_CLAIMED 点前就是「今日已领取」——当天早已领过，本次没有产生领取
                     （执行流程不因此改变，照旧点击、照旧关窗，只是状态区分）
  1/4/6 各种失败

验收标准只有一条：**按钮底色**。
    未领取=黑底、已领取=灰底。点前采一次 → 已领过就记 ALREADY_CLAIMED；
    点后再采一次 → 底色由黑翻灰才是真的领到了。
    这里刻意**不内置鼠标钩子**：钩子只能证明「那个坐标被点过」，已领取状态下点同一
    个键同样会命中，× 关窗也会让 closed=True —— 11-18 那次假阳性就是这么来的。
    钩子只是**临时验收工具**（见 verify_click.py，由外部启动、跑完即退），
    不是产品功能，不该固化成运行时依赖。
"""
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

try:
    import uiautomation as auto
except Exception:
    auto = None          # uiautomation 仅用于兜底找窗口/失败 dump；主流程不依赖

try:
    import rpa_splash
except Exception:
    rpa_splash = None

try:
    import rpa_countdown
except Exception:
    rpa_countdown = None

try:
    from PIL import Image, ImageChops, ImageStat
    _PIL = True
except Exception:
    _PIL = False

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# 日志与诊断产物写到脚本所在目录，随 skill 安装到任意位置都能用
BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, 'claim_log.txt')

# ---- 时序参数 ----
MENU_WAIT_MAX = 1.2      # 点用户条后等菜单展开的上限（冷 850 / 热 260~310）
POPUP_WAIT_MAX = 1.6     # 点入口后等浮层出现的上限（冷 980 / 热 380）
POPUP_SETTLE = 0.12      # 浮层淡入，采点前状态前先让它稳定
CONFIRM_DELAY = 0.35     # 点领取后到唯一一次采样的等待（等按钮状态翻转渲染）
CONFIRM_RETRY = 0.35     # 仅当采到「仍是黑底」时补采一次（排除渲染滞后）
CLICK_SETTLE = 0.05      # 单次点击后的稳定时间
HOVER_X = 0.42           # 悬停到卡片的时长（× 只在 hover 时渲染）
CHANGE_TH = 12.0         # 截屏差异阈值（0-255）
GRAB_INTERVAL = 0.015    # 视觉检测采样间隔：抓图本身约 16ms，循环周期 ≈ 31ms
CLOSE_WAIT_MAX = 0.50    # 点 × 后等浮窗消失的上限（原为固定 sleep 0.16）

# ---- 按钮底色判据（两张实测截图：0.638 vs 0.000，余量极大）----
BTN_W = 74               # 采样窗宽度：按最宽文案「今日已领取」取，右对齐
BTN_H = 24               # 按钮高（1x 实测 24，不随窗口尺寸变）
BTN_DARK_TH = 90         # 单像素亮度低于它算「暗」（黑底 26 / 灰底 235）
BTN_DARK_MIN = 0.30      # 暗占比 >= 此值 → 黑底 = 未领取
BTN_GREY_MAX = 0.08      # 暗占比 <= 此值且中位亮度在 [200,250] → 灰底 = 已领取
BTN_BLANK_TH = 250       # 中位亮度高于它就是「整块白」：卡片还没渲染出按钮（灰底是 235）

# ---- 卡片存在性判据（前置清残留用；实测白占比 0.855 / 0.000）----
CARD_WHITE_TH = 252      # 卡片容器纯白 255；无卡片时背景是 #F7F7F7 = 247
CARD_WHITE_MIN = 0.50    # 整块白占比 >= 此值 → 卡片在（实测 0.855）
CARD_WHITE_MAX = 0.20    # 整块白占比 <= 此值 → 卡片不在（实测 0.000）
CARD_GW, CARD_GH = 4, 3  # 分块：防「背景里恰好有块白色图片」被当成卡片
CARD_CELL_MIN = 0.5      # 单块算「白块」的门槛
CARD_CELLS_MIN = 6       # 12 块里至少这么多块是白的（卡片在 11 / 不在 0）
CARD_BTN_MAX = 245       # 按钮区中位亮度上限：灰按钮 235 / 黑按钮 26 才算卡片；
                         # 纯白 255（用户菜单）、247（会话列表）都不算

# ---- 菜单展开判据（同一套白占比，作用于 entry 区域）----
MENU_WHITE_MIN = 0.60    # 菜单展开时 Hero 入口卡片是白块（实测 0.922 / 关闭 0.091）
MENU_CELLS_MIN = 10      # 12 块里至少 10 块白（实测展开 12/12、关闭 0/12）

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

DWMWA_EXTENDED_FRAME_BOUNDS = 9
try:
    dwmapi = ctypes.windll.dwmapi
    dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                             ctypes.c_void_p, wintypes.DWORD]
    dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
except Exception:
    dwmapi = None

user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]


def idle_seconds():
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(lii)):
        return 1e9
    return (kernel32.GetTickCount() - lii.dwTime) / 1000.0


def get_cursor():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


def set_cursor(x, y):
    user32.SetCursorPos(int(x), int(y))


def real_click(x, y, hover=0.0, settle=None):
    """真实鼠标点击。hover>0 时先原地悬停（× 只在悬停时才渲染出来）。

    settle 置 0 用于「点完马上要轮询检测」的场合——那 50ms 的盲等由检测吸收掉，
    命中即走，比固定等待更准也更快。
    """
    set_cursor(x, y)
    if hover > 0:
        time.sleep(hover)
    time.sleep(0.06)
    user32.mouse_event(2, 0, 0, 0, 0)   # LEFTDOWN
    time.sleep(0.03)
    user32.mouse_event(4, 0, 0, 0, 0)   # LEFTUP
    time.sleep(CLICK_SETTLE if settle is None else settle)


def log_line(msg):
    line = '[%s] %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    print(line)
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def rect(r):
    try:
        return [r.left, r.top, r.right, r.bottom]
    except Exception:
        return None


def center(rc):
    return ((rc[0] + rc[2]) // 2, (rc[1] + rc[3]) // 2)


def find_main_hwnd():
    """用 Win32 FindWindow 直接取句柄。

    原实现走 UIA 遍历所有顶层窗口（每个取 ClassName+Name），进程首次调用
    还要初始化 UIA 客户端，实测要 2-3 秒——比整个执行段还长。
    """
    try:
        h = user32.FindWindowW('Chrome_WidgetWin_1', 'WorkBuddy')
        if h:
            return h
    except Exception:
        pass
    if auto is None:
        return None
    try:
        root = auto.GetRootControl()
        for w in root.GetChildren():
            try:
                if w.ClassName == 'Chrome_WidgetWin_1' and (w.Name or '') == 'WorkBuddy':
                    return w.NativeWindowHandle
            except Exception:
                pass
    except Exception:
        pass
    return None


def bring_to_front(hwnd, tries=2):
    if not hwnd:
        return False
    for _ in range(tries):
        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)      # SW_RESTORE
                time.sleep(0.45)
            if user32.GetForegroundWindow() == hwnd:
                return True
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.2)
            if user32.GetForegroundWindow() == hwnd:
                return True
        except Exception:
            pass
    return False


def frame_rect(hwnd):
    """GetWindowRect 的原始矩形——**PrintWindow 的图像原点在这里**。

    实测（最大化 1920x1040）：frame=(-8,-8,1928,1048)、可见=DWM(0,0,1920,1040)，
    而 PrintWindow 画出的图与屏幕实拍做交叉相关，恰好整体偏移 (+8,+8)，
    差值 0.05（等于完美对齐）。所以：
        坐标推算 / 点击  → 用 DWM 可见矩形（window_rect）
        截图裁剪        → 用 frame 矩形（本函数）
    非最大化窗口两者相同，不影响原行为。
    """
    r = wintypes.RECT()
    try:
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return None
    except Exception:
        return None
    if r.right <= r.left or r.bottom <= r.top:
        return None
    return [r.left, r.top, r.right, r.bottom]


def window_rect(hwnd):
    """窗口**可见**矩形。

    坑：窗口最大化时 GetWindowRect 会把 DWM 的不可见缩放边框算进去
    （实测 1920x1080 下报 (-8,-8,1928,1048)，真正看得见的内容从 (0,0) 起）。
    所有坐标都是从这块矩形推的，差 8px 就足以让采样窗错位、× 点空。
    所以优先用 DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)，
    拿不到再退回 GetWindowRect（非最大化窗口两者一致）。
    """
    r = wintypes.RECT()
    ok = False
    if dwmapi is not None:
        try:
            if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                                            ctypes.byref(r), ctypes.sizeof(r)) == 0:
                ok = r.right > r.left and r.bottom > r.top
        except Exception:
            ok = False
    if not ok:
        try:
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return None
        except Exception:
            return None
    if r.right <= r.left or r.bottom <= r.top:
        return None
    return [r.left, r.top, r.right, r.bottom]


def geo(wr):
    """由窗口矩形推算全部关键坐标。基准是窗口左下角（弹层从底部工具条向上弹）。

    两处高度自适应（9 组尺寸实测）：
      H < 760 → 浮层里的入口窄 10px（滚动条），入口右边界 311 → 301
      H < 721 → 浮层顶部被钳制在窗口内固定位置 T+295（实测 H=700/640/600 都不动）
    注意：**卡片和 × 不跟着变**（H=640 实测 × 中心仍是 L+251），只有入口变窄。
    系统允许的最小窗口高度是 600，所以这两档足够覆盖。
    """
    L, T, R, B = wr
    h = B - T
    cw = 311 if h >= 760 else 301       # 只有浮层里的入口会随滚动条变窄
    etop = (B - 426) if h >= 721 else (T + 295)
    # 按钮：右对齐，距卡片右 15 / 距卡片底 10，高 24（两图 1x/2x 交叉验证）
    bright = L + 271 - 15
    bbot = B - 60 - 10
    return {
        'userbar': (L + 8, B - 48, L + 200, B - 8),
        'bell': (L + 204, B - 44, L + 236, B - 12),
        'entry': (L + 25, etop, L + cw, etop + 88),
        'card': (L + 12, B - 172, L + 271, B - 60),
        'btn': (bright - BTN_W, bbot - BTN_H, bright, bbot),
        'go': (L + 222, B - 83),
        'close': (L + 251, B - 152),
    }


# ---------------- 视觉检测（代替 UIA 轮询） ----------------
# 为什么不用 ImageGrab：splash 是全屏置顶遮罩，ImageGrab 截的是屏幕合成结果，
# 拿到的永远是遮罩本身，菜单/浮层「检测不到变化」。
# 改用 PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT)：让窗口自己重绘到内存 DC，
# 不受任何窗口遮挡影响（实测遮罩全屏覆盖时仍能拿到清晰内容）。
class _BMIH(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', ctypes.c_long),
                ('biHeight', ctypes.c_long), ('biPlanes', wintypes.WORD),
                ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD)]


class _BMI(ctypes.Structure):
    _fields_ = [('bmiHeader', _BMIH), ('bmiColors', wintypes.DWORD * 3)]


gdi32 = ctypes.windll.gdi32
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL
user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.POINTER(_BMI), wintypes.UINT]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                         wintypes.DWORD]
gdi32.BitBlt.restype = wintypes.BOOL
SRCCOPY = 0x00CC0020


def print_window(hwnd, w, h):
    """把窗口内容画到内存位图并转成 PIL Image（不受遮挡影响）。"""
    if not _PIL:
        return None
    try:
        hdc = user32.GetWindowDC(hwnd)
        if not hdc:
            return None
        mdc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = gdi32.SelectObject(mdc, bmp)
        ok = user32.PrintWindow(hwnd, mdc, 2)      # PW_RENDERFULLCONTENT
        buf = ctypes.create_string_buffer(w * h * 4)
        bi = _BMI()
        bi.bmiHeader.biSize = ctypes.sizeof(_BMIH)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        lines = gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, hdc)
        if not ok or lines == 0:
            return None
        return Image.frombuffer('RGB', (w, h), buf, 'raw', 'BGRX', 0, 1)
    except Exception:
        return None


class Grabber:
    """可复用的窗口截图器：一次建好后台位图，之后每次只回读目标小块。

    原实现每次采样都「新建 DC + 新建兼容位图 + 整窗 PrintWindow + 整窗 GetDIBits」，
    实测拆开是：
        新建 DC + CreateCompatibleBitmap(1936x1056)   6~8ms
        PrintWindow(PW_RENDERFULLCONTENT)             45~57ms  ← 渲染本身，压不动
        GetDIBits 整窗 1936x1056                      6~10ms
        GetDIBits 只读目标块（259x112）                0.9ms
    于是这里做三件事：位图与 DC 全程复用（省 6~8ms/次）；PrintWindow 之后用
    BitBlt 只把目标块拷进小位图（省 6~9ms/次）；小图直接出灰度，不再整窗转 PIL。
    实测 960x1040 窗口：单次 24.8ms → 16.7ms，且像素与「整窗抓取后裁剪」
    **完全一致（diff 0.000）**。

    试过但走不通：给 PrintWindow 的 DC 设 SetViewportOrgEx 负偏移，让它只渲染目标
    区域。PW_RENDERFULLCONTENT 无视 DC 的坐标变换——偏移前后输出逐像素相同，
    都是窗口左上角，所以「区域渲染」这条路不存在，只能整窗渲染 + 小块回读。
    """

    def __init__(self, hwnd, w, h):
        self.hwnd = hwnd
        self.w = max(1, int(w))
        self.h = max(1, int(h))
        self.hdc = user32.GetWindowDC(hwnd)
        self.mdc = gdi32.CreateCompatibleDC(self.hdc)
        self.bmp = gdi32.CreateCompatibleBitmap(self.hdc, self.w, self.h)
        self.old = gdi32.SelectObject(self.mdc, self.bmp)
        self.sdc = gdi32.CreateCompatibleDC(self.hdc)
        self.sw, self.sh = 8, 8
        self.sbmp = gdi32.CreateCompatibleBitmap(self.hdc, self.sw, self.sh)
        self.sold = gdi32.SelectObject(self.sdc, self.sbmp)
        self.buf = None
        self.t_print = 0.0
        self.t_read = 0.0
        self.n = 0
        self.sum_print = 0.0
        self.sum_read = 0.0

    def _ensure_small(self, w, h):
        """目标块尺寸变了才重建小位图（同一轮里尺寸基本固定，几乎不触发）。"""
        if w > self.sw or h > self.sh:
            nw, nh = max(w, self.sw), max(h, self.sh)
            gdi32.SelectObject(self.sdc, self.sold)
            gdi32.DeleteObject(self.sbmp)
            self.sbmp = gdi32.CreateCompatibleBitmap(self.hdc, nw, nh)
            self.sold = gdi32.SelectObject(self.sdc, self.sbmp)
            self.sw, self.sh = nw, nh
        if self.buf is None or len(self.buf) < w * h * 4:
            self.buf = ctypes.create_string_buffer(w * h * 4)

    def grab(self, origin, bbox):
        """抓 bbox（屏幕坐标）区域，返回灰度小图。origin = PrintWindow 的图像原点。"""
        if not _PIL:
            return None
        try:
            x0 = max(0, min(self.w - 1, int(bbox[0] - origin[0])))
            y0 = max(0, min(self.h - 1, int(bbox[1] - origin[1])))
            x1 = max(x0 + 1, min(self.w, int(bbox[2] - origin[0])))
            y1 = max(y0 + 1, min(self.h, int(bbox[3] - origin[1])))
            w, h = x1 - x0, y1 - y0
            t = time.perf_counter()
            if not user32.PrintWindow(self.hwnd, self.mdc, 2):   # PW_RENDERFULLCONTENT
                return None
            t1 = time.perf_counter()
            self._ensure_small(w, h)
            gdi32.BitBlt(self.sdc, 0, 0, w, h, self.mdc, x0, y0, SRCCOPY)
            bi = _BMI()
            bi.bmiHeader.biSize = ctypes.sizeof(_BMIH)
            bi.bmiHeader.biWidth = w
            bi.bmiHeader.biHeight = -h
            bi.bmiHeader.biPlanes = 1
            bi.bmiHeader.biBitCount = 32
            bi.bmiHeader.biCompression = 0
            lines = gdi32.GetDIBits(self.sdc, self.sbmp, 0, h, self.buf,
                                    ctypes.byref(bi), 0)
            t2 = time.perf_counter()
            self.t_print = (t1 - t) * 1000
            self.t_read = (t2 - t1) * 1000
            self.n += 1
            self.sum_print += self.t_print
            self.sum_read += self.t_read
            if lines == 0:
                return None
            return Image.frombuffer('RGB', (w, h), self.buf,
                                    'raw', 'BGRX', 0, 1).convert('L')
        except Exception:
            return None

    def close(self):
        try:
            gdi32.SelectObject(self.sdc, self.sold)
            gdi32.DeleteObject(self.sbmp)
            gdi32.DeleteDC(self.sdc)
            gdi32.SelectObject(self.mdc, self.old)
            gdi32.DeleteObject(self.bmp)
            gdi32.DeleteDC(self.mdc)
            user32.ReleaseDC(self.hwnd, self.hdc)
        except Exception:
            pass

    def stats(self):
        """抓图成本汇总：写进日志，方便日后确认优化是否还成立。"""
        if not self.n:
            return 'grab: 0 frames'
        return 'grab: %d frames avg=%.1fms (print %.1f / read %.1f)' % (
            self.n, (self.sum_print + self.sum_read) / self.n,
            self.sum_print / self.n, self.sum_read / self.n)


def diff_mean(a, b):
    if a is None or b is None:
        return 0.0
    try:
        return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]
    except Exception:
        return 0.0


def save_png(img, name):
    """把采样到的画面留一份，便于事后核对坐标是否对准。"""
    if img is None:
        return None
    try:
        p = os.path.join(BASE, name)
        img.save(p)
        return p
    except Exception:
        return None


def btn_verdict(card_img, g):
    """从卡片区域的灰度截图判断领取按钮底色。

    未领取 = 黑底白字；已领取 = 灰底灰字。两态暗像素占比 0.638 vs 0.000，
    所以判据取「占比」而不是「中位色」——文字只占按钮的一小块，即便采样窗
    因为文案变窄而多框了白卡，也不会把黑底拖成灰底。

    返回 (state, detail)：state ∈ {'unclaimed','claimed','unknown',None}
    """
    if card_img is None:
        return None, 'no-frame'
    vals = btn_region(card_img, g)
    if not vals:
        return None, 'btn-window-out-of-frame'
    n = len(vals)
    dark = sum(1 for v in vals if v < BTN_DARK_TH)
    ratio = dark / float(n)
    med = sorted(vals)[n // 2]
    detail = 'dark=%.2f med=%d n=%d' % (ratio, med, n)
    if ratio >= BTN_DARK_MIN:
        return 'unclaimed', detail
    if ratio <= BTN_GREY_MAX and 200 <= med <= BTN_BLANK_TH:
        return 'claimed', detail
    # 整块白（med > 250）：卡片在，但按钮还没画出来。绝不能算已领取——
    # 之前 med=255 被当成灰底，导致点了个空、连 × 都点不掉。
    if med > BTN_BLANK_TH:
        return 'blank', detail
    return 'unknown', detail


_WHITE_TBL = bytes(1 if i >= CARD_WHITE_TH else 0 for i in range(256))


def white_area(img, white_min=None, cells_min=None):
    """判断一块区域是不是「成片白色」。前置状态归位全靠它，同一套判据用两处：

        浮层卡片  card  区域：卡片容器 #FFFFFF(255) vs 主界面背景 #F7F7F7(247)
                             实测 有卡 0.855 / 0.854（两张不同内容的卡）、无卡 0.000
        菜单展开  entry 区域：Hero 入口卡片是白的 vs 菜单关闭时是列表内容
                             实测 展开 0.922、关闭 0.091

    只用「白像素占比」会被主界面里恰好滚出一块白色图片骗到，所以再加一条
    「4x3 分块里有多少块也是白的」——要求它必须是**成片的一大块白**。
    卡片在 11/12、菜单开 12/12、两者不在时都是 0/12，区间完全分开。

    返回 (state, detail)：state ∈ {'present','absent',None}（None = 落在灰区，不下结论）
    成本：一次 bytes.translate + 若干次切片 count，约 1ms（不含抓图）。
    """
    wm = CARD_WHITE_MIN if white_min is None else white_min
    cm = CARD_CELLS_MIN if cells_min is None else cells_min
    if img is None:
        return None, 'no-frame'
    try:
        w, h = img.size
        mask = img.tobytes().translate(_WHITE_TBL)   # 白像素 → 0x01
    except Exception:
        return None, 'bad-frame'
    if w < 16 or h < 16:
        return None, 'tiny-frame[%dx%d]' % (w, h)
    total = mask.count(b'\x01') / float(w * h)
    cells = 0
    for j in range(CARD_GH):
        y0, y1 = h * j // CARD_GH, h * (j + 1) // CARD_GH
        for i in range(CARD_GW):
            x0, x1 = w * i // CARD_GW, w * (i + 1) // CARD_GW
            c = 0
            for y in range(y0, y1):
                c += mask[y * w + x0:y * w + x1].count(b'\x01')
            if c / float(max(1, (y1 - y0) * (x1 - x0))) >= CARD_CELL_MIN:
                cells += 1
    detail = 'white=%.3f cells=%d/%d n=%dx%d' % (total, cells, CARD_GW * CARD_GH, w, h)
    if total >= wm and cells >= cm:
        return 'present', detail
    if total <= CARD_WHITE_MAX:
        return 'absent', detail
    return None, detail


def btn_region(card_img, g):
    """从卡片截图里裁出按钮采样窗（灰度字节）。越界返回 None。"""
    cb = g['card']
    b = g['btn']
    try:
        x0 = max(0, int(b[0] - cb[0]))
        y0 = max(0, int(b[1] - cb[1]))
        x1 = min(card_img.width, int(b[2] - cb[0]) + 1)
        y1 = min(card_img.height, int(b[3] - cb[1]) + 1)
    except Exception:
        return None
    if x1 - x0 < 12 or y1 - y0 < 8:
        return None
    return card_img.crop((x0, y0, x1, y1)).tobytes()


def card_present(card_img, g):
    """浮层领取卡片是否真的在屏幕上。两个条件缺一不可：

        1) 整块是成片白   —— 排除「卡片不在」（那块是 #F7F7F7 会话列表）
        2) 右下角有实心按钮 —— 排除「面板开着但显示的是用户菜单」

    第 2 条是踩出来的：点用户条弹出的底部面板有时显示「意见反馈/检查更新/退出登录」，
    它同样是一整块白（white 0.85），只看白占比会把它当成卡片；但它在按钮位置是
    **纯白 255（min=max=255）**，而真卡片的按钮是灰底 235 或黑底 26。实测四态：
        已领取卡片 235 / 未领取(推广卡) 26 / 用户菜单 255 / 无卡片 247
    阈值 245 正好卡在 235 与 247 之间。

    返回 (state, detail)：state ∈ {'present','absent',None}
    """
    st, d = white_area(card_img)
    if st != 'present':
        return st, d
    vals = btn_region(card_img, g)
    if not vals:
        return None, d + ' btn=out-of-frame'
    med = sorted(vals)[len(vals) // 2]
    if med <= CARD_BTN_MAX:
        return 'present', d + ' btnmed=%d' % med
    return 'absent', d + ' btnmed=%d(no-button)' % med


def menu_open(img):
    """Hero 菜单是否已展开——决定前置要不要点用户条。

    坑：菜单一展开，卡片往往也跟着在。所以清完卡片**不能**直接点用户条开菜单，
    那一下会把它关掉，后面点入口就落在空处，白等一轮 POPUP_WAIT_MAX 才走 retry。
    这里先问清楚菜单状态，一次点击就归位。
    """
    return white_area(img, MENU_WHITE_MIN, MENU_CELLS_MIN)



def wait_change(grab, base, timeout, interval=GRAB_INTERVAL):
    """等到 grab() 的结果相对 base 出现明显变化，返回耗时(ms)；超时返回 None。

    替代原来的「固定 sleep + UIA 轮询」：命中即走，最坏不超过 timeout
    （等于把原来的固定等待保留为硬上限）。

    采样间隔从 50ms 收到 15ms：抓图本身约 16ms，循环周期约 31ms（原 66ms），
    检测滞后因此少一半。多出来的抓图次数不便宜也贵不到哪去——
    一次 16ms 的 PrintWindow 对 CPU 是可忽略的。
    """
    if base is None:                      # 抓图不可用：退化为固定等待
        time.sleep(min(timeout, 0.8))
        return None
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if diff_mean(grab(), base) > CHANGE_TH:
            return (time.perf_counter() - t0) * 1000
        time.sleep(interval)
    return None


def wait_state(grab, check, timeout, interval=GRAB_INTERVAL):
    """轮询到 check(画面) 为真，返回耗时(ms)；超时返回 None。

    与 wait_change 的区别：这里问的是「现在处于什么状态」，不是「画面变没变」。
    「变没变」在两种情况下会骗人：
        1) 目标区域本来就是目标状态（卡片随菜单一起渲染）→ 永远等不到变化
        2) 只是 hover 高亮之类的局部变化 → 以为目标已就位，其实没有
    状态判据同时解决这两件事，代价是每次轮询多一次 ~1ms 的统计。
    """
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        try:
            if check(grab()):
                return (time.perf_counter() - t0) * 1000
        except Exception:
            pass
        time.sleep(interval)
    return None


def wait_stable(grab, timeout=0.6, interval=GRAB_INTERVAL):
    """等画面稳定下来（连续两帧差异 < CHANGE_TH），返回耗时(ms)；超时返回 None。

    用途：卡片已经「在屏幕上」但可能还在淡入。卡片的出场动画让白占比从
    0.78 爬到 0.86，按钮却要更晚才画出来 —— 这时整块按钮区域是纯白 255，
    btn_verdict 会把它当成「灰底已领取」（med=255 >= 200 且无暗像素），
    于是点了个空、连关窗都关不掉。等稳定是成本最低的修法（通常 30~60ms）。
    """
    prev = grab()
    if prev is None:
        return None
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        time.sleep(interval)
        cur = grab()
        if diff_mean(cur, prev) <= CHANGE_TH:
            return (time.perf_counter() - t0) * 1000
        prev = cur
    return None


def scan_all(only_main=True, max_depth=14):
    """仅用于失败诊断 dump；正常路径不调用。"""
    if auto is None:
        return [{'error': 'uiautomation not installed'}]
    root = auto.GetRootControl()
    out = []

    def walk(c, d=0):
        nodes = []
        try:
            nodes.append({'name': c.Name, 'type': c.ControlTypeName,
                          'class': c.ClassName, 'aid': c.AutomationId,
                          'rect': rect(c.BoundingRectangle), 'off': c.IsOffscreen})
        except Exception:
            return nodes
        if d >= max_depth:
            return nodes
        try:
            for ch in c.GetChildren():
                nodes.extend(walk(ch, d + 1))
        except Exception:
            pass
        return nodes

    for w in root.GetChildren():
        try:
            if only_main and not ((w.ClassName or '') == 'Chrome_WidgetWin_1'
                                  and (w.Name or '') == 'WorkBuddy'):
                continue
            out.append({'win': {'name': w.Name, 'class': w.ClassName,
                                'rect': rect(w.BoundingRectangle)},
                        'nodes': walk(w)})
        except Exception as e:
            out.append({'win': {'error': repr(e)}, 'nodes': []})
    return out


def dump_json(name, data):
    path = os.path.join(BASE, name)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return path


def stop_splash():
    if rpa_splash is not None:
        try:
            rpa_splash.stop()
            log_line('splash off')
        except Exception as e:
            log_line('WARN splash stop failed: %r' % e)


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else 'claim'
    t_start = time.perf_counter()
    log_line('=== run stage=%s ===' % stage)

    # 前置倒计时提醒窗（带推迟 / 立即执行）；调试阶段跳过
    if stage not in ('menu', 'nocheck', 'probe') and rpa_countdown is not None:
        try:
            cd = rpa_countdown.ask(300, on_event=lambda m: log_line('countdown ' + m))
            log_line('countdown done %s' % cd)
        except Exception as e:
            log_line('WARN countdown failed: %r (proceed)' % e)

    if rpa_splash is not None:
        try:
            log_line('splash on hwnd=%s' % rpa_splash.start())
        except Exception as e:
            log_line('WARN splash start failed: %r' % e)

    # 不再预热 UIA：执行期已完全不碰 UIA（状态判定改像素），
    # 只留 FindWindowW 取句柄，成本 ~0ms。
    hwnd = find_main_hwnd()
    if not hwnd:
        log_line('FAIL no-window (WorkBuddy not running?)')
        stop_splash()
        return 1

    orig_cursor = get_cursor()
    grabber = None
    tm = {}
    t_exec = time.perf_counter()

    try:
        if not bring_to_front(hwnd):
            log_line('WARN could not bring WorkBuddy to front')
        wr = window_rect(hwnd)
        if wr is None:
            log_line('FAIL no window rect')
            return 1
        fr = frame_rect(hwnd) or wr
        g = geo(wr)
        if fr[0] != wr[0] or fr[1] != wr[1]:
            log_line('note: maximized/invisible border, printwindow offset=(%d,%d)'
                     % (wr[0] - fr[0], wr[1] - fr[1]))
        log_line('window=%s  userbar=%s entry=%s card=%s btn=%s go=%s close=%s'
                 % (wr, g['userbar'], g['entry'], g['card'], g['btn'],
                    g['go'], g['close']))
        if g['entry'][1] < wr[1]:
            log_line('WARN window too short, entry above window top: %s' % (g['entry'],))

        entry_bbox = g['entry']
        card_bbox = g['card']

        # 复用的截图器：整轮只建一次位图，之后每次只回读目标块（24.8→16.7ms）
        grabber = Grabber(hwnd, fr[2] - fr[0], fr[3] - fr[1])
        log_line('grabber %dx%d ready' % (fr[2] - fr[0], fr[3] - fr[1]))

        def cap(bb):
            # 裁剪基准用 fr（PrintWindow 原点），坐标本身仍由 wr 推算
            return grabber.grab(fr, bb)

        # 0. 前置：清掉上一轮残留在屏幕上的浮层卡片。
        #    卡片还在的话，「点入口 → 等浮层出现」永远等不到区域变化，
        #    必然超时 → 重试一轮 → rc=4（次日 FAIL no popup 就是这么来的）。
        #    判据纯像素，见 card_present：整块白(#FFFFFF vs #F7F7F7) + 右下角有实心按钮。
        t = time.perf_counter()
        pre_card = cap(card_bbox)
        cst, cdt = card_present(pre_card, g)
        if cst == 'present':
            # 连采两帧都判定有卡才动手，防止重绘/滚动瞬间的白块误判
            time.sleep(0.06)
            pre_card = cap(card_bbox)
            cst, cdt = card_present(pre_card, g)
        log_line('precheck card: %s (%s)' % (cst, cdt))

        if cst == 'present':
            # × 只在 hover 时才渲染，第一次点前要留悬停时间（与正常关窗同套路）
            base0 = pre_card
            first = True
            for dx in (0, -10):
                real_click(g['close'][0] + dx, g['close'][1],
                           hover=HOVER_X if first else 0.05, settle=0)
                first = False
                d = wait_state(lambda: cap(card_bbox),
                               lambda im: card_present(im, g)[0] != 'present',
                               CLOSE_WAIT_MAX)
                log_line('precheck close dx=%d detect=%s'
                         % (dx, 'timeout' if d is None else '%.0fms' % d))
                if d is not None:
                    break
            cst2, cdt2 = card_present(cap(card_bbox), g)
            log_line('precheck clear: state=%s (%s) %.0fms'
                     % (cst2, cdt2, (time.perf_counter() - t) * 1000))
            if cst2 == 'present':
                # 清不掉就别纠缠，交给下面的正常流程去兜底（它自带重试）
                log_line('WARN stale card still on screen -> continue anyway')

        tm['precheck'] = time.perf_counter() - t

        # 1. 点用户条打开 Hero 弹层（视觉检测，命中即走）。
        #    这里**不做**「菜单已经开着就跳过」的优化：点 × 关掉卡片后，底部面板可能
        #    只是换成了「意见反馈/检查更新/退出登录」那一页，它同样是白的，白占比判据
        #    分辨不出「Hero 面板」和「用户菜单页」——跳过用户条直接点入口会落空。
        #    无条件点一下是 toggle，状态乱了后面 popup 超时还有 retry 兜底。
        t = time.perf_counter()
        real_click(*center(g['userbar']))
        menu_ms = wait_state(lambda: cap(entry_bbox),
                             lambda im: menu_open(im)[0] == 'present',
                             MENU_WAIT_MAX)
        tm['menu'] = time.perf_counter() - t
        log_line('menu open: click+wait %.0fms (detect=%s)'
                 % ((time.perf_counter() - t) * 1000,
                    'timeout' if menu_ms is None else '%.0fms' % menu_ms))

        # 2. 点入口上半部（下半是「去邀约」推广语，会跳浏览器）
        ent = g['entry']
        ex = (ent[0] + ent[2]) // 2
        ey = ent[1] + (ent[3] - ent[1]) // 3
        base = cap(card_bbox)
        base_st, base_dt = card_present(base, g)
        t = time.perf_counter()
        real_click(ex, ey)
        if base_st == 'present':
            # 菜单一展开卡片就渲染好了（实测白占比 0.873），点入口不会让这块区域
            # 再变一次 —— 还按「区域变化」等就必然等满 POPUP_WAIT_MAX（1.8s），
            # 然后走 retry；retry 里那次点用户条会把菜单关掉，反而真失败。
            # 所以这里直接认账：卡片已经在屏幕上，进入颜色判定。
            popup_ms = 0.0
            log_line('card already visible before entry click (%s), skip change-wait'
                     % base_dt)
            settle = wait_stable(lambda: cap(card_bbox))
            log_line('card settle: %s'
                     % ('timeout' if settle is None else '%.0fms' % settle))
        else:
            popup_ms = wait_state(lambda: cap(card_bbox),
                                  lambda im: card_present(im, g)[0] == 'present',
                                  POPUP_WAIT_MAX)
        tm['popup'] = time.perf_counter() - t
        log_line('entry click -> popup %.0fms (detect=%s)'
                 % ((time.perf_counter() - t) * 1000,
                    'timeout' if popup_ms is None else '%.0fms' % popup_ms))

        if popup_ms is None:
            # 兜底：菜单可能本来就是开着的，第一次点击把它关掉了 → 重开一次
            log_line('WARN no popup, assume menu was open -> retry')
            before = cap(entry_bbox)
            real_click(*center(g['userbar']))
            wait_change(lambda: cap(entry_bbox), before, MENU_WAIT_MAX)
            base = cap(card_bbox)
            base_st, base_dt = card_present(base, g)
            real_click(ex, ey)
            if base_st == 'present':
                popup_ms = 0.0
                log_line('retry: card already visible (%s), skip change-wait' % base_dt)
            else:
                popup_ms = wait_state(lambda: cap(card_bbox),
                                      lambda im: card_present(im, g)[0] == 'present',
                                      POPUP_WAIT_MAX)
            log_line('retry popup: %s'
                     % ('timeout' if popup_ms is None else '%.0fms' % popup_ms))
        if popup_ms is None:
            dump_json('fail_no_popup.json', scan_all())
            log_line('FAIL no popup after retry (dumped fail_no_popup.json)')
            return 4

        if stage == 'menu':
            log_line('MENU OK (popup present)')
            return 0

        # 2.5 点前状态：像素判定按钮底色（黑=未领取 / 灰=已领取）。
        #     只读，不改变后续任何动作——点击、确认、关窗流程与原来完全一致。
        time.sleep(POPUP_SETTLE)          # 浮层淡入，先让它稳
        pre_state, pre_detail = None, 'no-frame'
        pre_img = None
        for _ in range(3):
            pre_img = cap(card_bbox)
            pre_state, pre_detail = btn_verdict(pre_img, g)
            if pre_state in ('claimed', 'unclaimed'):
                break
            if pre_state == 'blank':
                # 卡片在、按钮还没画出来：等画面稳定再采，别急着下结论
                wait_stable(lambda: cap(card_bbox), 0.6)
            else:
                time.sleep(0.12)          # 只给淡入/轮播过渡留一次机会
        pre_already = (pre_state == 'claimed')
        log_line('pre-claim pixel: state=%s (%s) already=%s'
                 % (pre_state, pre_detail, pre_already))
        save_png(pre_img, 'probe_pre_card.png')

        if stage == 'probe':
            # 维护用：只验证坐标推算与底色判定，不点领取、不产生任何领取动作，
            # 采完直接把浮层关掉。活动改版后重标定就跑这个。
            save_png(print_window(hwnd, fr[2] - fr[0], fr[3] - fr[1]), 'probe_full_window.png')
            t = time.perf_counter()
            base2 = cap(card_bbox)
            real_click(g['close'][0], g['close'][1], hover=HOVER_X)
            time.sleep(0.2)
            closed = diff_mean(cap(card_bbox), base2) > CHANGE_TH
            log_line('PROBE state=%s (%s) closed=%s %.0fms'
                     % (pre_state, pre_detail, closed,
                        (time.perf_counter() - t) * 1000))
            return 0 if pre_state in ('claimed', 'unclaimed') else 6

        # 3. 点领取按钮（推算中心）。点击是否真的落在按钮上，由外部验收脚本
        #    （verify_click.py，临时钩子）独立观察——主脚本不内置钩子：
        #    钩子只是「临时验收标准」，不是产品功能，不该固化成运行时依赖。
        t = time.perf_counter()
        real_click(*g['go'])
        t_click = time.perf_counter()
        tm['claim'] = time.perf_counter() - t
        log_line('claim click %.0fms' % ((time.perf_counter() - t) * 1000))

        # 3.5 领取点击后立刻把指针挪到 × 位置悬停：× 只在 hover 时才渲染，
        #     这段悬停等待正好被下一步的等待吸收掉，等于免费。
        set_cursor(int(g['close'][0]), int(g['close'][1]))
        cursor_on_close = True

        # 4. step2：点后**单次**像素采样，看按钮底色有没有翻成灰。
        #    旧实现（0.32/0.55/0.85 三次阶梯等待 + UIA 递归 walk 找确认弹层）
        #    实测 0.67~1.0s，且绝大多数日子没有弹层，纯白等。
        #    现在判据从「卡片以外画面变没变」换成「按钮底色是什么」——
        #    又便宜又直接对上业务状态：黑→灰 就是领取成功的硬证据。
        t = time.perf_counter()
        time.sleep(max(0.0, CONFIRM_DELAY - (time.perf_counter() - t_click)))
        frame = cap(card_bbox)
        state, detail = btn_verdict(frame, g)
        log_line('step2 pixel: state=%s (%s)' % (state, detail))
        if state in ('unclaimed', 'blank'):
            # 正常路径只采 1 次；只有「仍是黑底」或「整块白（按钮没画出来）」
            # 这两种异常才补采一次，用来排除状态还没渲染完的误判。
            time.sleep(CONFIRM_RETRY)
            frame = cap(card_bbox)
            state, detail = btn_verdict(frame, g)
            log_line('step2 pixel retry: state=%s (%s)' % (state, detail))
        save_png(frame, 'probe_post_card.png')
        tm['step2'] = time.perf_counter() - t

        # 5. 关浮窗：指针在 step3.5 就已经挪到 × 上、step2 又占了 0.35s，
        #    所以 × 早已渲染好，hover 只留一点保险。点完不再盲等 0.16s——
        #    浮窗消散实测 60~120ms，改成轮询后命中即走，比固定等待准也快。
        #    矮窗卡片窄 10px 导致 × 左移，所以留一组 dx=-10 兜底。
        #    base2 直接复用刚才那帧，省一次抓图。
        cl = g['close']
        t = time.perf_counter()
        base2 = frame if frame is not None else cap(card_bbox)
        closed = False
        # 卡片已经不在（比如点「今日已领取」时自己收了）：不该再点 ×。
        # 多出来的那次点击就是「弹窗后多出的鼠标动作」，还可能误触底下的页面。
        if card_present(cap(card_bbox), g)[0] != 'present':
            closed = True
            log_line('close: card already gone, skip click')
        else:
            for dx in (0, -10):
                real_click(cl[0] + dx, cl[1],
                           hover=0.05 if cursor_on_close else HOVER_X, settle=0)
                cursor_on_close = False
                d = wait_state(lambda: cap(card_bbox),
                               lambda im: card_present(im, g)[0] != 'present',
                               CLOSE_WAIT_MAX)
                log_line('close attempt dx=%d detect=%s'
                         % (dx, 'timeout' if d is None else '%.0fms' % d))
                if d is not None:
                    closed = True
                    break
            if not closed:
                log_line('close: gave up, last diff=%.1f (threshold %.0f)'
                         % (diff_mean(cap(card_bbox), base2), CHANGE_TH))
        log_line('close %.0fms closed=%s' % ((time.perf_counter() - t) * 1000, closed))
        tm['close'] = time.perf_counter() - t
        log_line('timings ms: ' + ' '.join('%s=%.0f' % (k, v * 1000)
                                           for k, v in sorted(tm.items(),
                                                              key=lambda kv: -kv[1])))
        if grabber is not None:
            log_line(grabber.stats())

        total = time.perf_counter() - t_exec
        # 点前已知「今日已领取」：本次没有产生领取，单列状态，避免和真成功混在一起。
        # 执行流程未改动——照旧点击、照旧关窗，这里只是在报告层做区分。
        if pre_already:
            log_line('ALREADY_CLAIMED | pre-state=%s | closed=%s | exec=%.1fs'
                     % (pre_state, closed, total))
            return 7
        # 底色黑→灰：状态真的翻转，这才是领取成功的硬证据。
        if state == 'claimed':
            log_line('CLAIM_OK_PIXEL | button grey | pre=%s post=(%s) | closed=%s | exec=%.1fs'
                     % (pre_state, detail, closed, total))
            return 0
        # 点完仍是黑底：没领到。坐标确实被点过也不代表成功，
        # 这正是 11-18 那次假阳性的根因（旧版靠 click-signal 判成功）。
        if state == 'unclaimed':
            dump_json('fail_claim.json', scan_all())
            log_line('CLAIM_UNVERIFIED | button still black | pre=%s post=(%s) | closed=%s | exec=%.1fs (dumped fail_claim.json)'
                     % (pre_state, detail, closed, total))
            return 6
        # 采到「既非黑也非灰」：卡片可能已经关掉/被别的东西覆盖，只剩「浮窗关了」这一个弱证据。
        if closed:
            log_line('CLAIM_OK_VISUAL | popup closed | post=(%s) | exec=%.1fs'
                     % (detail, total))
            return 0
        dump_json('fail_claim.json', scan_all())
        log_line('CLAIM_UNVERIFIED | post=(%s) closed=%s | exec=%.1fs (dumped fail_claim.json)'
                 % (detail, closed, total))
        return 6
    finally:
        if grabber is not None:
            grabber.close()
        set_cursor(*orig_cursor)
        stop_splash()
        log_line('=== run end total=%.1fs (exec %.1fs) ==='
                 % (time.perf_counter() - t_start, time.perf_counter() - t_exec))


if __name__ == '__main__':
    sys.exit(main())

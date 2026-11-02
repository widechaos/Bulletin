"""单画布桌面弹幕 overlay:

- 一个全屏透明置顶窗口(仅覆盖主屏一块;弹幕限制在此屏内,不越界飞到外接显示器)
- QPainter 一帧画完所有弹幕 → 零闪烁(不再是每条一个窗口)
- 泳道 + 等待队列 → 数学上保证不重叠;满了就排队,天然控密度
- 每条弹幕生成时预渲染成一张图,每帧只贴图;只重绘弹幕所在的横带,空闲不重绘
- 默认鼠标穿透;指针停在某条上时该条暂停+可点(左键击碎/右键复制并击碎/Ctrl·⌘+左键原文/中键或⌥右键菜单)
- 来源分色标签、自适应速度(带开关)
"""
from __future__ import annotations

import collections
import ctypes
import math
import os
import random
import sys
import threading
import time
import webbrowser

from PySide6 import QtCore, QtGui, QtWidgets

import autostart
import config as cfg
from danmaku import Danmu
from sources import probe_feed, resolve_url

PRO_URL = "https://widechaos.cn/bulletin/pro"
POSITIONS = (("top", "顶部"), ("middle", "居中"), ("bottom", "底部"))
SPAWN_GAP = 60   # 同泳道两条之间最小水平间隔(px)
FRAME = 1 / 60   # 速度单位:px/帧(按 60fps 计),实际按真实间隔折算
GRAVITY = 0.32   # 碎片重力(px/帧²)
BADGE_LIFE = 1.6  #「已复制」胶囊停留总时长(秒)
GLOW = 22         # 悬停光晕会超出弹幕本身的上下边距;重绘横带要把它算进去,不然擦不干净
_RING = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1))
# 按住它点左键 = 打开原文。mac 上 ⌘ 报 ControlModifier;⌃ 报 MetaModifier,且 ⌃+左键会被系统改成右键
_LINK_MODS = QtCore.Qt.KeyboardModifier.ControlModifier | QtCore.Qt.KeyboardModifier.MetaModifier


class _Shard:
    """击碎后的一块三角碎片:自带小图,绕质心旋转飞散、下落、淡出。"""
    __slots__ = ("pix", "ox", "oy", "x", "y", "vx", "vy", "a", "va", "age", "life", "r", "wait")

    def __init__(self, pix, ox, oy, x, y, vx, vy, va, life, r, wait):
        self.pix, self.ox, self.oy = pix, ox, oy    # 小图相对质心的偏移
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.a, self.va = 0.0, va
        self.age, self.life, self.r = 0.0, life, r  # r=外接半径,算重绘范围用
        self.wait = wait                            # 起飞前原地停留(秒):裂纹从点击处向两侧传开


# ===== macOS 原生鼠标穿透:NSWindow.ignoresMouseEvents =====
# (Qt 的 WA_TransparentForMouseEvents 在 mac 全屏顶层窗口上不可靠,直接调 AppKit)
_OBJC = None
if sys.platform == "darwin":
    try:
        _OBJC = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.dylib")
        _OBJC.sel_registerName.restype = ctypes.c_void_p
        _OBJC.sel_registerName.argtypes = [ctypes.c_char_p]
    except OSError:
        _OBJC = None


def _nswindow_from_view(view_ptr):
    if not _OBJC or not view_ptr:
        return None
    _OBJC.objc_msgSend.restype = ctypes.c_void_p
    _OBJC.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    return _OBJC.objc_msgSend(ctypes.c_void_p(int(view_ptr)), _OBJC.sel_registerName(b"window"))


def _set_ignores_mouse(nswindow, on: bool):
    if not _OBJC or not nswindow:
        return
    fn = _OBJC.objc_msgSend
    fn.restype = None
    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
    fn(ctypes.c_void_p(nswindow), _OBJC.sel_registerName(b"setIgnoresMouseEvents:"), on)


def _activate_app():
    """菜单栏程序没有前台身份;从菜单打开窗口时要先把自己拉到前台,否则窗口落在别人后面。"""
    if not _OBJC:
        return
    _OBJC.objc_getClass.restype = ctypes.c_void_p
    _OBJC.objc_getClass.argtypes = [ctypes.c_char_p]
    fn = _OBJC.objc_msgSend
    fn.restype = ctypes.c_void_p
    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    app = fn(_OBJC.objc_getClass(b"NSApplication"), _OBJC.sel_registerName(b"sharedApplication"))
    fn.restype = None
    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
    fn(app, _OBJC.sel_registerName(b"activateIgnoringOtherApps:"), True)


def _ensure_tray_position(from_right: float = 360.0):
    """新装时把菜单栏图标放到靠右的位置。macOS 默认把新图标插在最左边,
    刘海屏菜单栏一满就被藏到刘海后面,用户只会觉得「启动不了」。已有位置(含用户⌘拖动过的)不动。"""
    if not _OBJC:
        return
    o = _OBJC
    o.objc_getClass.restype = ctypes.c_void_p
    o.objc_getClass.argtypes = [ctypes.c_char_p]
    fn = o.objc_msgSend
    fn.restype = ctypes.c_void_p
    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    ud = fn(o.objc_getClass(b"NSUserDefaults"), o.sel_registerName(b"standardUserDefaults"))
    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
    key = fn(o.objc_getClass(b"NSString"), o.sel_registerName(b"stringWithUTF8String:"),
             b"NSStatusItem Preferred Position Item-0")
    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    if fn(ud, o.sel_registerName(b"objectForKey:"), key):
        return
    fn.restype = None
    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_double, ctypes.c_void_p]
    fn(ud, o.sel_registerName(b"setDouble:forKey:"), from_right, key)
    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    fn(ud, o.sel_registerName(b"synchronize"))


def _objc_fn(restype, *argtypes):
    fn = _OBJC.objc_msgSend
    fn.restype = restype
    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, *argtypes]
    return fn


def _frontmost_pid() -> int:
    """当前前台应用的进程号(NSWorkspace.frontmostApplication)。"""
    if not _OBJC:
        return 0
    _OBJC.objc_getClass.restype = ctypes.c_void_p
    _OBJC.objc_getClass.argtypes = [ctypes.c_char_p]
    sel = _OBJC.sel_registerName
    ws = _objc_fn(ctypes.c_void_p)(_OBJC.objc_getClass(b"NSWorkspace"), sel(b"sharedWorkspace"))
    ra = _objc_fn(ctypes.c_void_p)(ws, sel(b"frontmostApplication"))
    return _objc_fn(ctypes.c_int)(ra, sel(b"processIdentifier")) if ra else 0


def _activate_pid(pid: int):
    """把焦点交还给指定进程。点弹幕时 macOS 会把本程序拉到前台,NSApp.deactivate 在新系统上无效,只能这样还回去。"""
    if not _OBJC or not pid:
        return
    _OBJC.objc_getClass.restype = ctypes.c_void_p
    _OBJC.objc_getClass.argtypes = [ctypes.c_char_p]
    sel = _OBJC.sel_registerName
    ra = _objc_fn(ctypes.c_void_p, ctypes.c_int)(
        _OBJC.objc_getClass(b"NSRunningApplication"), sel(b"runningApplicationWithProcessIdentifier:"), pid)
    if ra:
        _objc_fn(ctypes.c_bool, ctypes.c_ulong)(ra, sel(b"activateWithOptions:"), 0)


def _beep():
    if sys.platform == "darwin":
        import subprocess
        subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif sys.platform.startswith("win"):
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    else:
        QtWidgets.QApplication.beep()


def rel_time(ts: float) -> str:
    """近的→相对(刚刚/几分钟前/几小时前),远的→详细时间。"""
    if not ts:
        return ""
    d = time.time() - ts
    if d < 0:
        d = 0
    if d < 60:
        return "刚刚"
    if d < 3600:
        return f"{int(d // 60)}分钟前"
    if d < 86400:
        return f"{int(d // 3600)}小时前"
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


class OverlayWindow(QtWidgets.QWidget):
    update_found = QtCore.Signal(str, str)   # (tag, url) 发现新版本

    def __init__(self, conf: dict, item_queue):
        super().__init__(
            None,
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.WindowDoesNotAcceptFocus
            | QtCore.Qt.WindowType.NoDropShadowWindowHint,
        )
        self.conf = conf
        self.q = item_queue
        self.danmu: list[Danmu] = []
        self.pending: collections.deque = collections.deque()
        self.hovered: Danmu | None = None
        self.shards: list[_Shard] = []
        self.bursts: list[dict] = []        # 击碎点的冲击环 +「已复制」胶囊
        self._last_tick = time.monotonic()
        self._painted_band: tuple[int, int] | None = None   # 上一帧画过的纵向范围
        self._src_colors: dict[str, str] = {}
        self.seen_sources: list[str] = []   # 出现过的来源(供「按来源开关」列表)
        self._dlg: SettingsDialog | None = None
        self._settings_paused = False
        self._hidden = False                # 菜单「隐藏弹幕」
        self._link_mode = False             # 按着 Ctrl/⌘:指针变手形、显示下划线,左键打开原文
        self._flow = 1.0                    # 流速系数:暂停/恢复时在 0↔1 间缓动,不是一下刹死
        self._hover_since = 0.0             # 悬停光晕淡入计时
        self._fade: QtCore.QPropertyAnimation | None = None
        self._last_beep = 0.0
        self._notify_url = ""

        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # mac 用原生 NSWindow.ignoresMouseEvents 做穿透(WA_TransparentForMouseEvents 在 mac 全屏顶层不可靠);
        # 其它平台用 WA_TransparentForMouseEvents。
        if sys.platform != "darwin":
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._nswin = None
        self._click_through = True

        # 覆盖目标显示器(默认主屏;可选横贯全部/指定屏)。弹幕限制在此几何内,不越界。
        self.setGeometry(self._target_geometry())

        self._rebuild_font()
        self.setWindowOpacity(float(conf["opacity"]))

        self.anim = QtCore.QTimer(self)
        self.anim.setTimerType(QtCore.Qt.TimerType.PreciseTimer)   # 默认粗定时器在 mac 上会合并触发,滚动发顿
        self.anim.timeout.connect(self._advance)
        self.anim.start(16)
        self.spawn_t = QtCore.QTimer(self)
        self.spawn_t.setSingleShot(True)                 # 单次触发→每次按积压量重新排期,自然流不喷泉
        self.spawn_t.timeout.connect(self._try_spawn)
        self.spawn_t.start(self._next_delay())
        self.hover_t = QtCore.QTimer(self)
        self.hover_t.timeout.connect(self._hover)
        self.hover_t.start(50)
        self._fs_paused = False
        self.fs_t = QtCore.QTimer(self)
        self.fs_t.timeout.connect(self._check_fullscreen)
        self.fs_t.start(2000)          # 每 2s 查一次前台是否全屏

        if getattr(sys, "frozen", False):     # 只在打包版改用户偏好,源码运行写的是 python 的域
            try:
                _ensure_tray_position()
            except Exception:
                pass
        self._build_tray()
        self.show()          # 用 show()(几何已=整屏);不用 showFullScreen 以免进 macOS 全屏 Space
        self.raise_()
        self._apply_native()

        # 插拔显示器/改分辨率时跟着挪:原来只在启动时定一次位置,拔掉外接屏后弹幕层还停在旧尺寸上
        app = QtWidgets.QApplication.instance()
        app.screenAdded.connect(self._watch_screen)
        app.screenRemoved.connect(lambda _s: self._schedule_refit())
        app.primaryScreenChanged.connect(lambda _s: self._schedule_refit())
        for scr in app.screens():
            self._watch_screen(scr, refit=False)

        # 程序已在运行时再次打开它(双击图标/启动台/Spotlight):弹出设置窗口。
        # 菜单栏图标多时 macOS 会把托盘图标藏到刘海后面,不然用户只会觉得「启动不了」。
        self._born = time.monotonic()
        self._self_activated = 0.0
        app.applicationStateChanged.connect(self._on_app_state)

    def _on_app_state(self, st):
        if st != QtCore.Qt.ApplicationState.ApplicationActive:
            return
        if time.monotonic() - self._born < 4:          # 启动本身那次激活不算
            return
        QtCore.QTimer.singleShot(250, self._show_on_reopen)

    def _show_on_reopen(self):
        app = QtWidgets.QApplication.instance()
        if time.monotonic() - self._self_activated < 1.5:   # 是自己拉到前台的(打开设置)
            return
        if time.monotonic() - getattr(self, "_last_press", 0.0) < 1.5:   # 刚点过弹幕
            return
        if app.activePopupWidget() or app.activeModalWidget():
            return
        if any(w is not self and w.isWindow() and w.isVisible() and not isinstance(w, QtWidgets.QMenu)
               for w in app.topLevelWidgets()):
            return
        self.open_settings()

    def _watch_screen(self, scr, refit: bool = True):
        scr.geometryChanged.connect(lambda _g: self._schedule_refit())
        if refit:
            self._schedule_refit()

    def _schedule_refit(self):
        QtCore.QTimer.singleShot(600, self._refit)    # 等系统把屏幕排布稳定下来

    def _refit(self):
        g = self._target_geometry()
        if g == self.geometry():
            return
        self.setGeometry(g)
        self._apply_native()
        self.danmu = [d for d in self.danmu if d.y + d.h <= self.height()]   # 新屏放不下的泳道直接收掉
        self.hovered = None
        self.update()

    def _apply_native(self):
        """窗口级原生属性:鼠标穿透。显示/重建窗口后都要再设一次。"""
        if sys.platform == "darwin":
            self._nswin = _nswindow_from_view(self.winId())
            _set_ignores_mouse(self._nswin, self._click_through)   # 默认穿透(OS 级)

    # ---------- 显示器 ----------
    def _target_geometry(self) -> QtCore.QRect:
        app = QtWidgets.QApplication.instance()
        disp = self.conf.get("display", "primary")
        if disp == "span":
            return app.primaryScreen().virtualGeometry()   # 横贯所有屏(接力/双屏都显示)
        if disp and disp != "primary":
            for s in app.screens():
                if s.name() == disp:
                    return s.geometry()
        return app.primaryScreen().geometry()

    # ---------- 字体 ----------
    def _rebuild_font(self):
        fam = self.conf["font_family"] or cfg.default_font_family()
        self._font = QtGui.QFont(fam, int(self.conf["font_size"]))
        self._font.setBold(True)
        self._fm = QtGui.QFontMetrics(self._font)
        px = max(14, min(22, round(int(self.conf["font_size"]) * 0.56)))
        self._badge_font = QtGui.QFont(fam)
        self._badge_font.setPixelSize(px)
        self._badge_font.setWeight(QtGui.QFont.Weight.DemiBold)
        self._badge_sub = QtGui.QFont(fam)
        self._badge_sub.setPixelSize(px - 2)
        self._alert_font = QtGui.QFont(self._font)
        self._alert_font.setPointSizeF(self._font.pointSizeF() * 1.12)
        self._alert_fm = QtGui.QFontMetrics(self._alert_font)

    def _lane_h(self) -> int:
        return self._fm.height() + 10

    def _color_for(self, source: str) -> str:
        if source not in self._src_colors:
            pal = cfg.SOURCE_PALETTE
            self._src_colors[source] = pal[len(self._src_colors) % len(pal)]
        return self._src_colors[source]

    # ---------- 生成 ----------
    def _drain_queue(self):
        """收新条目;标题或摘要命中关注词的插队到最前,并按需响铃/发通知。社区版只认第 1 个关注词。"""
        words = [w.strip() for w in self.conf.get("alert_keywords", [])[:1] if w.strip()]
        kw = words[0].lower() if words else ""
        while True:
            try:
                item = self.q.get_nowait()
            except Exception:
                break
            if item.source not in self.seen_sources:   # 供设置里「按来源开关」列出
                self.seen_sources.append(item.source)
            item.alert = bool(kw) and kw in f"{item.title}\n{item.desc}".lower()
            if item.alert:
                self.pending.appendleft(item)
                self._on_alert(item)
            else:
                self.pending.append(item)

    def _on_alert(self, item):
        if self.conf.get("alert_sound", True) and time.monotonic() - self._last_beep > 3:
            self._last_beep = time.monotonic()
            try:
                _beep()
            except Exception:
                pass
        if self.conf.get("alert_notify", True) and not self._visible_now():
            self._notify_url = item.url
            self.tray.showMessage(f"命中关注词 · {item.source}", item.title, self.tray.icon(), 8000)

    def _visible_now(self) -> bool:
        return not (self.paused or self._fs_paused or self._hidden)

    def _lane_of(self, d: Danmu, band_top: int, lh: int) -> int:
        return (d.y - band_top) // lh

    def _try_spawn(self):
        try:
            self._drain_queue()      # 暂停/隐藏时也收,恢复后接着放
            self._spawn_once()
        finally:
            self.spawn_t.start(self._next_delay())   # 每次按当前积压量重新排期

    def _next_delay(self) -> int:
        """出现节奏:把当前积压「摊平」到距下次轮询的时间里,持续滴出,不成堆。
        关键=积压多时不是吐得更快(那才是喷泉),而是按「还剩多少 / 还有多久下一批」拉开间隔。"""
        base = int(self.conf.get("spawn_interval_ms", 1400))
        backlog = len(self.pending)
        if backlog <= 1:
            d = base
        else:
            poll_ms = max(15, int(self.conf.get("poll_interval", 60))) * 1000
            spread = poll_ms / backlog                 # 把这批铺满到下次轮询前(不再≤base*4 秒内放完→不成堆)
            d = max(base * 0.4, spread)                # base*0.4 只作下限防机关枪;上不封顶,稀疏时也铺开
        return max(250, int(d * random.uniform(0.8, 1.2)))   # ±20% 抖动,自然不机械

    def _next_pending(self):
        """取下一条待放:跳过被关掉的来源;启用白名单时只放含关键词的。"""
        muted = set(self.conf.get("muted_sources", []))
        only_on = bool(self.conf.get("only_enabled", False))
        only = [k.strip().lower() for k in self.conf.get("only_keywords", []) if k.strip()]
        while self.pending:
            item = self.pending.popleft()
            if item.source in muted:
                continue
            if only_on and only and not item.alert and not any(k in item.title.lower() for k in only):
                continue   # 白名单不拦关注词
            return item
        return None

    def _spawn_once(self):
        if not self._visible_now() or not self._interval_ok():
            return
        if not self.pending or len(self.danmu) >= int(self.conf["max_on_screen"]):
            return
        W = self.width()
        H = self.height()
        lh = self._lane_h()
        band_top = int(H * float(self.conf["top_offset"]))
        usable = int(H * float(self.conf["top_fraction"]))
        lanes = max(1, usable // lh)

        order = random.sample(range(lanes), lanes)
        order.sort(key=lambda ln: bool(getattr(self._lane_last(ln, band_top, lh), "alert", False)))
        for ln in order:   # 慢速的关注词泳道排最后,别把后面的都拖慢
            last = self._lane_last(ln, band_top, lh)
            if last is not None and (last.x + last.w) >= W - SPAWN_GAP:
                continue
            item = self._next_pending()
            if item is None:
                return
            self._make_danmu(item, band_top + ln * lh, last, W)
            return   # 一次放一条

    def _lane_last(self, ln, band_top, lh):
        cur = [d for d in self.danmu if self._lane_of(d, band_top, lh) == ln]
        return max(cur, key=lambda d: d.x, default=None)

    def _make_danmu(self, item, y, last, W):
        d = Danmu(text=item.title, url=item.url, source=item.source, source_color=self._color_for(item.source),
                  x=float(W), y=int(y), time_str=(rel_time(item.ts) + "  ") if (
                      self.conf.get("show_time", True) and item.ts) else "",
                  alert=bool(getattr(item, "alert", False)))
        self._render(d)
        base = float(self.conf["speed"])
        speed = base * 0.65 if d.alert else base     # 命中的放慢,多看一眼
        if self.conf.get("adaptive_speed", False) and not d.alert:
            factor = 1.0 + min(0.6, (d.w / max(W, 1)) * 0.8)
            speed = min(base * factor, float(self.conf.get("adaptive_max_speed", 4.0)))
        if last is not None:                 # 不超车,防重叠
            speed = min(speed, last.speed)
        d.speed = speed
        self.danmu.append(d)

    def _render(self, d: Danmu):
        """把整条弹幕(描边+来源色+时间+正文)画进一张透明图。
        原来每帧每条要 drawText 9 次以上,现在生成时画一次,之后每帧只贴图;击碎也直接切这张图。
        命中关注词的:字大 12%,外面一个红色圆角底,文字改白。"""
        alert = d.alert
        fm = self._alert_fm if alert else self._fm
        tag = f"[{d.source}]  " if self.conf.get("show_source_tag", True) else ""
        d.tag_w = fm.horizontalAdvance(tag)
        d.time_w = fm.horizontalAdvance(d.time_str)
        ip, vp = (10, 3) if alert else (0, 0)           # 红底的内边距
        d.w = d.tag_w + d.time_w + fm.horizontalAdvance(d.text) + 2 * ip
        d.h = self._fm.height()                         # 泳道按普通字高算,命中条上下各溢出一点
        ol = int(self.conf.get("outline", 2))
        d.pad = pad = ol + 2
        d.pad_y = pad + vp + (fm.height() - d.h) // 2
        dpr = self.devicePixelRatioF()
        ph = fm.height() + 2 * vp + 2 * pad
        pix = QtGui.QPixmap(math.ceil((d.w + 2 * pad) * dpr), math.ceil(ph * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pix)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
        if alert:
            r = QtCore.QRectF(pad, pad, d.w, fm.height() + 2 * vp)
            g = QtGui.QLinearGradient(r.topLeft(), r.bottomLeft())
            g.setColorAt(0, QtGui.QColor(232, 52, 52, 215))
            g.setColorAt(1, QtGui.QColor(176, 24, 30, 215))
            p.setBrush(QtGui.QBrush(g))
            p.setPen(QtGui.QPen(QtGui.QColor(255, 150, 140, 230), 1.4))
            p.drawRoundedRect(r, r.height() / 2.6, r.height() / 2.6)
        p.setFont(self._alert_font if alert else self._font)
        x, base = pad + ip, pad + vp + fm.ascent()
        full = tag + d.time_str + d.text
        # 描边:八向黑边,强度=outline(0 时退化为单像素投影),保证任何背景上都清晰
        if ol > 0 and not alert:
            p.setPen(QtGui.QColor(0, 0, 0, 235))
            for dx, dy in _RING:
                p.drawText(x + dx * ol, base + dy * ol, full)
        else:                                           # 红底上不用粗黑边,轻投影就够
            p.setPen(QtGui.QColor(0, 0, 0, 160 if alert else 200))
            p.drawText(x + 1, base + 1, full)
        # 分段上色:来源色 → 时间灰 → 正文
        if tag:
            p.setPen(QtGui.QColor("#FFE3E0") if alert else QtGui.QColor(d.source_color))
            p.drawText(x, base, tag)
            x += d.tag_w
        if d.time_str:
            p.setPen(QtGui.QColor("#FFD0CB") if alert else QtGui.QColor("#93A2B4"))
            p.drawText(x, base, d.time_str)
            x += d.time_w
        colorful = self.conf.get("colorful", False)
        if alert:
            p.setPen(QtGui.QColor("#FFFFFF"))
        else:
            p.setPen(QtGui.QColor(d.source_color) if colorful else QtGui.QColor(self.conf["font_color"]))
        p.drawText(x, base, d.text)
        p.end()
        d.pix = pix

    # ---------- 击碎 ----------
    def _shatter(self, d: Danmu, at: QtCore.QPointF, badge: bool = True):
        """把弹幕图切成抖动网格三角片,从点击处向外炸开。碎片各自预裁好小图,每帧只是平移旋转贴图。"""
        src = d.pix
        dpr = src.devicePixelRatio()
        img = src.toImage()
        pw, ph = src.width() / dpr, src.height() / dpr
        left, top = d.x - d.pad, d.y - d.pad_y
        cols = max(4, min(36, round(pw / 20)))
        rows = max(2, min(4, round(ph / 15)))
        cw, rh = pw / cols, ph / rows
        grid = [[QtCore.QPointF(
            c * cw + (random.uniform(-0.38, 0.38) * cw if 0 < c < cols else 0),
            r * rh + (random.uniform(-0.38, 0.38) * rh if 0 < r < rows else 0))
            for c in range(cols + 1)] for r in range(rows + 1)]
        tris = []
        for r in range(rows):
            for c in range(cols):
                a, b = grid[r][c], grid[r][c + 1]
                e, f = grid[r + 1][c], grid[r + 1][c + 1]
                if random.random() < 0.5:
                    tris += [(a, b, f), (a, f, e)]
                else:
                    tris += [(a, b, e), (b, f, e)]
        reach = max(pw, 1.0)
        for tri in tris:
            poly = QtGui.QPolygonF(list(tri))
            br = poly.boundingRect().toAlignedRect()
            if not self._has_ink(img, br, dpr):     # 字缝里的空片不生成,省得白算
                continue
            sp = QtGui.QPixmap(math.ceil(br.width() * dpr) + 1, math.ceil(br.height() * dpr) + 1)
            sp.setDevicePixelRatio(dpr)
            sp.fill(QtCore.Qt.GlobalColor.transparent)
            q = QtGui.QPainter(sp)
            q.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            path = QtGui.QPainterPath()
            path.addPolygon(poly.translated(-br.left(), -br.top()))
            q.setClipPath(path)
            q.drawPixmap(QtCore.QPointF(-br.left(), -br.top()), src)
            q.end()
            cx = sum(pt.x() for pt in tri) / 3
            cy = sum(pt.y() for pt in tri) / 3
            sx, sy = left + cx, top + cy
            dx, dy = sx - at.x(), sy - at.y()
            dist = math.hypot(dx, dy) or 1.0
            near = max(0.0, 1.0 - dist / reach)     # 离点击处越近飞得越猛
            spd = random.uniform(1.5, 4.5) + 9.0 * near ** 2
            self.shards.append(_Shard(
                sp, br.left() - cx, br.top() - cy, sx, sy,
                vx=dx / dist * spd + random.uniform(-0.8, 0.8),
                vy=dy / dist * spd * 0.7 - random.uniform(1.5, 4.5),
                va=random.uniform(-16, 16), life=random.uniform(0.55, 0.95),
                r=math.hypot(br.width(), br.height()),
                wait=dist / 3200 + random.uniform(0, 0.02)))
        if badge:
            self.bursts.append(self._make_badge(d, at))
        else:                                # 左键纯击碎:只留冲击环
            self.bursts.append({"x": at.x(), "y": at.y(), "by": at.y(), "H": 0, "age": 0.0,
                                "life": 0.3, "color": QtGui.QColor(d.source_color)})

    def _make_badge(self, d: Danmu, at: QtCore.QPointF) -> dict:
        """「已复制」胶囊:摆在点击处上方(贴边时收回屏内);挑十几块碎片的位置当光点起点,
        光点沿弧线汇入左侧圆圈,圆圈点亮、勾画出来 —— 像碎片被收进了剪贴板。"""
        fm = QtGui.QFontMetricsF(self._badge_font)
        fm2 = QtGui.QFontMetricsF(self._badge_sub)
        snip = fm2.elidedText(d.text, QtCore.Qt.TextElideMode.ElideRight, fm2.horizontalAdvance("字") * 14)
        H = round(fm.height() * 2.05)
        cd = H - 10
        label_w = fm.horizontalAdvance("已复制")
        W0 = 5 + cd + 10 + label_w + H * 0.42           # 只含「✓ 已复制」的收拢宽度
        W = W0 + 13 + fm2.horizontalAdvance(snip)        # 摘要滑入后的展开宽度
        m = 14
        bx = min(max(at.x(), W / 2 + m), self.width() - W / 2 - m)
        by = at.y() - d.h / 2 - H / 2 - 18
        if by - H / 2 < m:                              # 顶上放不下就放到下方
            by = at.y() + d.h / 2 + H / 2 + 18
        picks = random.sample(self.shards[-160:], min(16, len(self.shards[-160:]))) if self.shards else []
        seeds = [(s.x, s.y, random.uniform(0.0, 0.07)) for s in picks]
        return {"x": at.x(), "y": at.y(), "bx": bx, "by": by, "W0": W0, "W": W, "H": H, "cd": cd,
                "label_w": label_w, "snip": snip, "seeds": seeds, "age": 0.0,
                "color": QtGui.QColor(d.source_color)}

    @staticmethod
    def _has_ink(img: QtGui.QImage, br: QtCore.QRect, dpr: float) -> bool:
        step = 3
        for yy in range(br.top(), br.bottom() + 1, step):
            for xx in range(br.left(), br.right() + 1, step):
                px, py = int(xx * dpr), int(yy * dpr)
                if 0 <= px < img.width() and 0 <= py < img.height() and img.pixelColor(px, py).alpha() > 40:
                    return True
        return False

    def _step_fx(self, dt: float) -> bool:
        """推进碎片与冲击环;dt 以帧为单位。返回是否还有特效在跑。"""
        sec = dt * FRAME
        alive = []
        drag = 0.985 ** dt
        for s in self.shards:
            if s.wait > 0:
                s.wait -= sec
                alive.append(s)
                continue
            s.age += sec
            if s.age >= s.life:
                continue
            s.vy += GRAVITY * dt
            s.vx *= drag
            s.x += s.vx * dt
            s.y += s.vy * dt
            s.a += s.va * dt
            alive.append(s)
        self.shards = alive
        for b in self.bursts:
            b["age"] += sec
        self.bursts = [b for b in self.bursts if b["age"] < b.get("life", BADGE_LIFE)]
        return bool(self.shards or self.bursts)

    # ---------- 动画 ----------
    def _check_fullscreen(self):
        if not self.conf.get("pause_on_fullscreen", True):
            self._fs_paused = False
            return
        try:
            import fullscreen
            self._fs_paused = fullscreen.foreground_is_fullscreen()
        except Exception:
            self._fs_paused = False

    def _advance(self):
        now = time.monotonic()
        dt = min(4.0, (now - self._last_tick) / FRAME)   # 按真实间隔折算成帧,卡顿后不瞬移太远
        self._last_tick = now
        moved = False
        target = 0.0 if (self.paused or self._fs_paused) else 1.0   # 全屏(看视频/演示)时冻结,不打扰
        if self._flow != target:                      # 0.35 秒缓停 / 缓起
            step = dt * FRAME / 0.35
            self._flow = min(target, self._flow + step) if target > self._flow else max(target, self._flow - step)
        if self._flow > 0:
            dead = []
            for d in self.danmu:
                if d.paused:
                    continue
                d.x -= d.speed * dt * self._flow
                moved = True
                if d.x + d.w < 0:
                    dead.append(d)
            for d in dead:
                if d is self.hovered:
                    self.hovered = None
                self.danmu.remove(d)
        fx = self._step_fx(dt)           # 碎片在暂停时也要飞完
        self._repaint_band(moved or fx or self._live_fx(now))

    def _live_fx(self, now: float) -> bool:
        """静止时也要逐帧重绘的装饰:悬停光晕淡入。"""
        return self.hovered is not None and now - self._hover_since < 0.25

    def _repaint_band(self, force: bool = True):
        """只重绘有东西的那一段横带(连同上一帧画过的范围,好擦掉残影);静止或全空时不重绘。"""
        ys = [(d.y - d.pad_y - GLOW, d.y + d.h + d.pad_y + GLOW) for d in self.danmu]
        ys += [(int(s.y - s.r), int(s.y + s.r)) for s in self.shards]
        ys += [(int(min(b["y"], b["by"], b.get("sy", b["y"])) - b["H"] - 50), int(max(b["y"], b["by"]) + b["H"] + 30))
               for b in self.bursts]
        cur = (min(a for a, _ in ys), max(b for _, b in ys)) if ys else None
        prev = self._painted_band
        if not force and cur == prev:
            return
        self._painted_band = cur
        span = [r for r in (prev, cur) if r]
        if span:
            top = min(a for a, _ in span) - 2
            bot = max(b for _, b in span) + 2
            self.update(0, top, self.width(), bot - top)

    # ---------- 绘制(单画布,零闪烁) ----------
    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
        now = time.monotonic()
        for d in self.danmu:
            x = int(d.x)
            if d is self.hovered:
                self._paint_halo(p, d, x, min(1.0, (now - self._hover_since) / 0.18))
            p.drawPixmap(x - d.pad, d.y - d.pad_y, d.pix)
            if d is self.hovered and d.url and self._link_mode and not d.alert:
                ul = d.y + self._fm.ascent() + 3
                p.setPen(QtGui.QPen(QtGui.QColor(d.source_color), 1))
                p.drawLine(x, ul, x + d.w, ul)
        if self.shards or self.bursts:
            self._paint_fx(p)
        p.end()

    def _paint_halo(self, p: QtGui.QPainter, d: Danmu, x: int, k: float):
        """悬停的那条:身后一圈来源色柔光,淡入;按着 Ctrl/⌘(可打开原文)时更亮。"""
        p.save()
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        r = QtCore.QRectF(x - 10, d.y - d.pad_y + d.pad - 3, d.w + 20, d.h + 2 * (d.pad_y - d.pad) + 6)
        strength = (0.032 if self._link_mode else 0.02) * k
        for i in range(6, 0, -1):
            c = QtGui.QColor(d.source_color)
            c.setAlphaF(strength * (7 - i))
            e = i * 2.6
            p.setBrush(c)
            p.drawRoundedRect(r.adjusted(-e, -e, e, e), r.height() / 2 + e, r.height() / 2 + e)
        p.restore()

    def _paint_fx(self, p: QtGui.QPainter):
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        for s in self.shards:
            t = s.age / s.life
            p.save()
            p.setOpacity(1.0 if t < 0.45 else max(0.0, 1 - ((t - 0.45) / 0.55) ** 1.5))
            p.translate(s.x, s.y)
            p.rotate(s.a)
            sc = 1.0 - 0.35 * t
            p.scale(sc, sc)
            p.drawPixmap(QtCore.QPointF(s.ox, s.oy), s.pix)
            p.restore()
        for b in self.bursts:
            t = b["age"]
            if t < 0.28:                       # 冲击环:快速扩张变细淡出
                k = t / 0.28
                c = QtGui.QColor(b["color"])
                c.setAlphaF(0.9 * (1 - k))
                p.setOpacity(1.0)
                p.setPen(QtGui.QPen(c, 3.5 * (1 - k) + 0.5))
                p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                rad = 6 + 46 * (1 - (1 - k) ** 3)
                p.drawEllipse(QtCore.QPointF(b["x"], b["y"]), rad, rad)
            if "W" in b:
                self._paint_badge(p, b, t)
        p.setOpacity(1.0)

    def _paint_badge(self, p: QtGui.QPainter, b: dict, t: float):
        QPF = QtCore.QPointF
        col = b["color"]
        H, cd = b["H"], b["cd"]
        ks2 = min(1.0, max(0.0, (t - 0.42) / 0.25))      # 勾画完后胶囊展开、摘要滑入
        W = b["W0"] + (b["W"] - b["W0"]) * (1 - (1 - ks2) ** 3)
        x, y = b["bx"], b["by"]
        left = x - W / 2
        ccx = left + 5 + cd / 2                 # 勾圈中心
        no_pen = QtCore.Qt.PenStyle.NoPen
        # 1) 光点:碎片沿弧线飞进勾圈,带三节拖尾
        p.setOpacity(1.0)
        p.setPen(no_pen)
        for sx, sy, dl in b["seeds"]:
            k = (t - dl) / 0.3
            if not 0 < k < 1:
                continue
            for j in range(4):
                kj = max(0.0, k - j * 0.04)
                e = kj * kj
                px = sx + (ccx - sx) * e
                py = sy + (y - sy) * e - math.sin(kj * math.pi) * 30
                c = QtGui.QColor(col)
                c.setAlphaF(0.95 * (1 - j / 4))
                p.setBrush(c)
                r = 3.0 - j * 0.6
                p.drawEllipse(QPF(px, py), r, r)
                if j == 0:
                    c.setAlphaF(0.22)
                    p.setBrush(c)
                    p.drawEllipse(QPF(px, py), r * 2.6, r * 2.6)
        # 2) 胶囊:回弹弹出 → 停留 → 上浮淡出
        ks = min(1.0, t / 0.3)
        k1 = ks - 1
        sc = 0.55 + 0.45 * (1 + 2.9 * k1 ** 3 + 1.9 * k1 ** 2)
        out = max(0.0, (t - (BADGE_LIFE - 0.3)) / 0.3)
        p.save()
        p.setOpacity(min(1.0, t / 0.08) * (1 - out))
        p.translate(x, y - 16 * (1 - (1 - out) ** 3))
        p.scale(sc, sc)
        p.translate(-x, -y)
        r = QtCore.QRectF(left, y - H / 2, W, H)
        p.setPen(no_pen)
        for i in range(6, 0, -1):               # 外发光
            g = QtGui.QColor(col)
            g.setAlphaF(0.035 * (7 - i))
            p.setBrush(g)
            e = i * 1.7
            p.drawRoundedRect(r.adjusted(-e, -e, e, e), H / 2 + e, H / 2 + e)
        grad = QtGui.QLinearGradient(r.topLeft(), r.topRight())
        c2 = QtGui.QColor(col)
        c2.setAlphaF(0.12)
        grad.setColorAt(0, col)
        grad.setColorAt(1, c2)
        p.setPen(QtGui.QPen(QtGui.QBrush(grad), 1.3))
        p.setBrush(QtGui.QColor(13, 16, 23, 232))
        p.drawRoundedRect(r, H / 2, H / 2)
        # 3) 勾圈:光点到齐时点亮、脉冲一下、外扩一圈,随后勾一笔画出
        lit = min(1.0, max(0.0, (t - 0.3) / 0.08))
        pulse = 1 + 0.16 * math.sin(min(1.0, max(0.0, (t - 0.3) / 0.22)) * math.pi)
        rad = cd / 2 * pulse
        ring = QtGui.QColor(col)
        ring.setAlphaF(0.55 + 0.45 * lit)
        fill = QtGui.QColor(col)
        fill.setAlphaF(lit)
        p.setPen(QtGui.QPen(ring, 1.8))
        p.setBrush(fill)
        p.drawEllipse(QPF(ccx, y), rad, rad)
        if 0.3 < t < 0.6:
            fl = (t - 0.3) / 0.3
            fc = QtGui.QColor(col)
            fc.setAlphaF(0.75 * (1 - fl))
            p.setPen(QtGui.QPen(fc, 2.2))
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPF(ccx, y), cd / 2 + 14 * fl, cd / 2 + 14 * fl)
        kc = min(1.0, max(0.0, (t - 0.36) / 0.16))
        if kc > 0:
            path = QtGui.QPainterPath(QPF(ccx - cd * 0.25, y + cd * 0.01))
            path.lineTo(QPF(ccx - cd * 0.07, y + cd * 0.2))
            path.lineTo(QPF(ccx + cd * 0.27, y - cd * 0.19))
            lw = max(2.4, cd * 0.11)
            pen = QtGui.QPen(QtGui.QColor(10, 12, 18), lw, QtCore.Qt.PenStyle.SolidLine,
                             QtCore.Qt.PenCapStyle.RoundCap, QtCore.Qt.PenJoinStyle.RoundJoin)
            pen.setDashPattern([path.length() * kc / lw + 0.01, 1000])
            p.setPen(pen)
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        # 4) 文字:「已复制」+ 细分隔 + 灰色标题摘要(勾画完后从左滑入)
        fm = QtGui.QFontMetricsF(self._badge_font)
        tx = left + 5 + cd + 10
        p.setFont(self._badge_font)
        p.setPen(QtGui.QColor(242, 245, 250))
        p.drawText(QPF(tx, y + (fm.ascent() - fm.descent()) / 2), "已复制")
        tx += b["label_w"] + 6
        if ks2 > 0:
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, round(46 * ks2)), 1))
            p.drawLine(QPF(tx, y - H * 0.22), QPF(tx, y + H * 0.22))
            fm2 = QtGui.QFontMetricsF(self._badge_sub)
            p.setOpacity(p.opacity() * ks2)
            p.setFont(self._badge_sub)
            p.setPen(QtGui.QColor(162, 172, 188))
            p.setClipRect(QtCore.QRectF(tx, y - H / 2, left + W - tx - H * 0.3, H))
            p.drawText(QPF(tx + 7 - 8 * (1 - ks2) ** 2, y + (fm2.ascent() - fm2.descent()) / 2), b["snip"])
        p.restore()

    # ---------- 悬停:指针下那条暂停并可点 ----------
    def _hover(self):
        lp = self.mapFromGlobal(QtGui.QCursor.pos())
        target = None
        for d in reversed(self.danmu):
            if QtCore.QRect(int(d.x), d.y, d.w, d.h).contains(lp):
                target = d
                break
        link = bool(QtGui.QGuiApplication.queryKeyboardModifiers() & _LINK_MODS)
        if target is not self.hovered or link != self._link_mode:
            if target is not self.hovered:
                if target is not None and self.hovered is None and \
                        QtGui.QGuiApplication.applicationState() != QtCore.Qt.ApplicationState.ApplicationActive:
                    pid = _frontmost_pid()             # 指针刚压上弹幕、还没点:记下用户正在用的应用
                    self._prev_pid = pid if pid != os.getpid() else 0
                if self.hovered is not None:
                    self.hovered.paused = False
                self.hovered = target
                self._hover_since = time.monotonic()
                if target is not None and self.conf.get("hover_pause", True):
                    target.paused = True
            self._link_mode = link
            self.setCursor(
                QtCore.Qt.CursorShape.PointingHandCursor
                if (target is not None and target.url and link)
                else QtCore.Qt.CursorShape.ArrowCursor
            )
            self._repaint_band()   # 下划线跟着变(暂停时动画不重绘,这里补一次)
        # 每帧幂等强制同步穿透:只有指针正压在某条弹幕上时才接管鼠标,其余一律穿透。
        # (不被上面的提前分支跳过——否则点开弹幕后会卡成全屏隐形墙。)
        self._set_click_through(target is None)

    def _set_click_through(self, on: bool):
        if on == self._click_through:
            return
        self._click_through = on
        if sys.platform == "darwin":
            _set_ignores_mouse(self._nswin, on)   # OS 级穿透
        else:
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, on)

    def mousePressEvent(self, e: QtGui.QMouseEvent):
        self._last_press = time.monotonic()    # 点弹幕引起的激活不能当成「重复打开」
        d = self.hovered
        if d is None:
            e.ignore()
            return
        btn = e.button()
        B = QtCore.Qt.MouseButton
        menu_mods = e.modifiers() & (QtCore.Qt.KeyboardModifier.AltModifier | QtCore.Qt.KeyboardModifier.ShiftModifier)
        if btn == B.MiddleButton or (btn == B.RightButton and menu_mods):
            self._ctx_menu = self._menu()      # 中键 / ⌥右键开菜单(触控板没中键);持有引用防被回收
            self._ctx_menu.popup(e.globalPosition().toPoint())
        elif btn in (B.LeftButton, B.RightButton) and e.modifiers() & _LINK_MODS:
            # Ctrl/⌘ + 左键:打开原文(mac 的 ⌃+左键到这里是「右键+Meta」,一并认)
            if d.url:
                webbrowser.open(d.url)
                self._dismiss(d)               # 点过即移除(相当于已读)
        elif btn == B.LeftButton:
            self._shatter(d, e.position(), badge=False)   # 左键:直接击碎
            self._dismiss(d)
            self._give_back_focus()
        elif btn == B.RightButton:
            # 右键:复制「标题 + 链接」,并当场击碎
            QtWidgets.QApplication.clipboard().setText(f"{d.text}\n{d.url}" if d.url else d.text)
            self._shatter(d, e.position())
            self._dismiss(d)
            self._give_back_focus()
        e.accept()

    def _give_back_focus(self):
        app = QtWidgets.QApplication.instance()
        if any(w is not self and w.isWindow() and w.isVisible() and not isinstance(w, QtWidgets.QMenu)
               for w in app.topLevelWidgets()):
            return                              # 设置开着,用户在用本程序,不让出
        pid = getattr(self, "_prev_pid", 0)
        if pid:
            QtCore.QTimer.singleShot(0, lambda: _activate_pid(pid))

    def _dismiss(self, d: Danmu):
        if d in self.danmu:
            self.danmu.remove(d)
        self.hovered = None
        self._set_click_through(True)          # 立即恢复穿透,别卡成隐形墙
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self._repaint_band()

    def _interval_ok(self):
        return not self._settings_paused

    # ---------- 托盘 / 菜单 ----------
    def _tray_icon(self):
        pm = QtGui.QPixmap(32, 32)
        pm.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setBrush(QtGui.QColor("#38E1F0"))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawEllipse(4, 4, 24, 24)
        p.setPen(QtGui.QColor("#04070C"))
        f = QtGui.QFont("PingFang SC", 15)
        f.setBold(True)
        p.setFont(f)
        p.drawText(pm.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, "弹")
        p.end()
        return QtGui.QIcon(pm)

    def _menu(self) -> QtWidgets.QMenu:
        m = QtWidgets.QMenu()
        m.addAction("继续" if getattr(self, "paused", False) else "暂停", self.toggle_pause)
        m.addAction("显示弹幕" if self._hidden else "隐藏弹幕", self.toggle_hidden)
        m.addAction("清屏", self.clear)
        m.addSeparator()
        m.addAction("设置…", self.open_settings)
        m.addSeparator()
        if getattr(self, "_update_url", ""):
            m.addAction(f"⬇ 下载新版 {self._update_tag} ↗", self._open_update)
        else:
            m.addAction("检查更新", self._check_update_async)
        m.addAction(f"关于  v{cfg.__version__}", lambda: None).setEnabled(False)
        m.addSeparator()
        m.addAction("退出", QtWidgets.QApplication.quit)
        return m

    def _build_tray(self):
        self.paused = False
        self._update_url = ""
        self._update_tag = ""
        self.tray = QtWidgets.QSystemTrayIcon(self._tray_icon(), self)
        self.tray.setToolTip(f"Bulletin 弹讯 v{cfg.__version__} · 桌面弹幕新闻")
        self.tray.setContextMenu(self._menu())
        self.tray.messageClicked.connect(self._on_notify_clicked)
        self.tray.show()
        self.update_found.connect(self._on_update_found)
        self._check_update_async()   # 启动后台静默检查一次

    def _check_update_async(self):
        def work():
            import updater
            has, tag, url = updater.check_update()
            if has:
                self.update_found.emit(tag, url)
        threading.Thread(target=work, daemon=True).start()

    def _on_update_found(self, tag: str, url: str):
        self._update_tag = tag
        self._update_url = url
        self.tray.setContextMenu(self._menu())
        self.tray.showMessage("Bulletin 有新版本",
                              f"{tag} 可更新 —— 托盘菜单「下载新版」", self.tray.icon(), 5000)

    def _open_update(self):
        if self._update_url:
            webbrowser.open(self._update_url)

    def _on_notify_clicked(self):
        self._self_activated = time.monotonic()
        if self._notify_url:
            webbrowser.open(self._notify_url)

    def toggle_pause(self):
        self.paused = not self.paused
        self.tray.setContextMenu(self._menu())

    def toggle_hidden(self):
        """隐藏 = 整个弹幕层收起来、停止放新条;关注词照样提醒(发通知)。"""
        self._hidden = not self._hidden
        if self._fade is not None:
            self._fade.stop()
        anim = QtCore.QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(220)
        anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        if self._hidden:                      # 淡出后再真正隐藏
            anim.setStartValue(self.windowOpacity())
            anim.setEndValue(0.0)
            anim.finished.connect(lambda: self._hidden and self.hide())
        else:                                 # 先透明地显示出来,再淡入到设定的不透明度
            self.setWindowOpacity(0.0)
            self.show()
            self.raise_()
            self._apply_native()
            anim.setStartValue(0.0)
            anim.setEndValue(float(self.conf["opacity"]))
        self._fade = anim
        anim.start()
        self.tray.setContextMenu(self._menu())

    def clear(self):
        """清屏:每条整条往下坠、轻转、淡出,从左到右依次倒下,像推倒一排多米诺。"""
        W = max(1, self.width())
        for d in self.danmu:
            dpr = d.pix.devicePixelRatio()
            pw, ph = d.pix.width() / dpr, d.pix.height() / dpr
            self.shards.append(_Shard(
                d.pix, -pw / 2, -ph / 2, d.x - d.pad + pw / 2, d.y - d.pad_y + ph / 2,
                vx=random.uniform(-0.6, 0.6), vy=random.uniform(-3.0, -1.0), va=random.uniform(-2.0, 2.0),
                life=random.uniform(0.8, 1.0), r=math.hypot(pw, ph) / 2,
                wait=max(0.0, min(d.x, W)) / W * 0.35))
        self.danmu.clear()
        self.pending.clear()
        self.hovered = None
        self._set_click_through(True)
        self._repaint_band()

    # ---------- 设置 ----------
    def open_settings(self):
        if self._dlg is not None and self._dlg.isVisible():
            self._dlg.raise_()
            self._dlg.activateWindow()
            return
        self._dlg = SettingsDialog(self.conf, list(self.seen_sources))
        self._dlg.applied.connect(self._apply_settings)
        self._dlg.show()
        self._self_activated = time.monotonic()
        _activate_app()
        self._dlg.raise_()
        self._dlg.activateWindow()

    def _apply_settings(self):
        cfg.save(self.conf)
        self._rebuild_font()
        for d in self.danmu:                         # 在飞的也换上新字体/颜色/描边
            self._render(d)
        self.setWindowOpacity(float(self.conf["opacity"]))
        # 出现节流是单次触发+按积压动态排期,间隔改动下一拍自动生效,无需 setInterval
        self.setGeometry(self._target_geometry())   # 显示器/区域切换实时生效
        self._apply_native()                         # 换了几何后重设穿透
        self.tray.setContextMenu(self._menu())


# ===== 设置面板主题(经典配色板;仅影响设置窗外观,不改桌面弹幕) =====
# 每套:bg 底 / card 卡片 / bd 边框 / inp 输入框 / tx 文字 / mut 次要文字 /
#       ti 分区标题 / fill 滑块与聚焦 / hd 滑块把手 / on 开关开启色 / vlab 数值
THEMES = {
    "浅色":             {"bg": "#F5F5F7", "card": "#FFFFFF", "bd": "#D9D9DE", "inp": "#FFFFFF",
                         "tx": "#1D1D1F", "mut": "#6E6E73", "ti": "#0A66D8", "fill": "#0A84FF",
                         "hd": "#FFFFFF", "on": "#34C759", "vlab": "#0A66D8"},
    "Tokyo Night":      {"bg": "#1A1B26", "card": "#24283B", "bd": "#2F334D", "inp": "#16161E",
                         "tx": "#C0CAF5", "mut": "#7982A9", "ti": "#7AA2F7", "fill": "#7AA2F7",
                         "hd": "#C0CAF5", "on": "#50FA7B", "vlab": "#7AA2F7"},
    "Dracula":          {"bg": "#282A36", "card": "#343746", "bd": "#44475A", "inp": "#21222C",
                         "tx": "#F8F8F2", "mut": "#6272A4", "ti": "#BD93F9", "fill": "#BD93F9",
                         "hd": "#F8F8F2", "on": "#50FA7B", "vlab": "#BD93F9"},
    "Gruvbox Dark":     {"bg": "#282828", "card": "#3C3836", "bd": "#504945", "inp": "#32302F",
                         "tx": "#EBDBB2", "mut": "#A89984", "ti": "#FABD2F", "fill": "#83A598",
                         "hd": "#EBDBB2", "on": "#689D6A", "vlab": "#83A598"},
    "Solarized Dark":   {"bg": "#002B36", "card": "#073642", "bd": "#0A4B59", "inp": "#00252E",
                         "tx": "#93A1A1", "mut": "#586E75", "ti": "#2AA198", "fill": "#268BD2",
                         "hd": "#EEE8D5", "on": "#859900", "vlab": "#2AA198"},
    "One Dark":         {"bg": "#282C34", "card": "#2C313A", "bd": "#3B4048", "inp": "#21252B",
                         "tx": "#ABB2BF", "mut": "#5C6370", "ti": "#61AFEF", "fill": "#61AFEF",
                         "hd": "#ABB2BF", "on": "#98C379", "vlab": "#61AFEF"},
    "Catppuccin Mocha": {"bg": "#1E1E2E", "card": "#313244", "bd": "#45475A", "inp": "#181825",
                         "tx": "#CDD6F4", "mut": "#A6ADC8", "ti": "#CBA6F7", "fill": "#89B4FA",
                         "hd": "#CDD6F4", "on": "#A6E3A1", "vlab": "#89B4FA"},
}


class ToggleSwitch(QtWidgets.QAbstractButton):
    """拨动开关:圆角轨道 + 滑块,颜色随主题(开=主题 on 色)。"""

    def __init__(self, checked: bool, owner, parent=None):
        super().__init__(parent)
        self._owner = owner
        self.setCheckable(True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setChecked(bool(checked))
        self.setFixedSize(42, 23)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _e):
        t = self._owner._t
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.4)                  # 会员功能没激活:淡一点,一眼看出点不动
        on = self.isChecked()
        r = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(t["on"]) if on else QtGui.QColor(t["bd"]))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        cx = (r.right() - d - 3) if on else (r.left() + 3)
        p.setBrush(QtGui.QColor(t["hd"]))
        p.drawEllipse(QtCore.QRectF(cx, r.top() + 3, d, d))
        if self.hasFocus():
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.setPen(QtGui.QPen(QtGui.QColor(t["ti"]), 2))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 10, 10)
        p.end()


class _PositionTile(QtWidgets.QAbstractButton):
    """一块小屏幕:按位置画出弹幕带,下面写顶部/居中/底部;选中描强调色边框。"""

    def __init__(self, owner, key: str, name: str):
        super().__init__()
        self._owner = owner
        self.key = key
        self.setText(name)
        self.setCheckable(True)
        self.frac = 0.5
        self.setFixedSize(92, 78)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _e):
        t = self._owner._t
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.4)
        on = self.isChecked()
        accent = QtGui.QColor(t["fill"])
        sw, sh = 72, 45
        x, y = (self.width() - sw) / 2, 4
        frame = QtCore.QRectF(x, y, sw, sh)
        edge = accent if (on or (self.underMouse() and self.isEnabled())) else QtGui.QColor(t["bd"])
        p.setPen(QtGui.QPen(edge, 2 if on else 1))
        p.setBrush(QtGui.QColor(t["inp"]))
        p.drawRoundedRect(frame, 6, 6)
        band = max(4.0, (sh - 6) * max(0.0, min(1.0, self.frac)))
        room = sh - 6 - band
        by = y + 3 + {"middle": room / 2, "bottom": room}.get(self.key, 0)
        fill = QtGui.QColor(accent)
        fill.setAlpha(110 if on else 55)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(QtCore.QRectF(x + 3, by, sw - 6, band), 3, 3)
        p.setPen(QtGui.QPen(accent, 1.6))
        for i in range(min(3, int(band // 6))):     # 几条弹幕线示意
            ly = by + 3 + i * 6
            p.drawLine(QtCore.QPointF(x + 7, ly), QtCore.QPointF(x + sw - 9 - (i % 2) * 16, ly))
        f = p.font()
        f.setBold(on)
        p.setFont(f)
        p.setPen(accent if on else QtGui.QColor(t["tx"]))
        p.drawText(QtCore.QRectF(0, y + sh + 4, self.width(), 22),
                   QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop, self.text())
        p.end()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


class PositionPicker(QtWidgets.QWidget):
    """弹幕带位置:三块小屏幕图形,点哪块选哪块。免费只能选顶部,居中、底部置灰。"""
    changed = QtCore.Signal()

    def __init__(self, owner, pos: str, frac: float):
        super().__init__()
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self.tiles = []
        for key, name in POSITIONS:
            tile = _PositionTile(owner, key, name)
            tile.frac = frac
            tile.setChecked(key == pos)
            self._group.addButton(tile)
            self.tiles.append(tile)
            h.addWidget(tile)
        h.addStretch(1)
        if not any(t.isChecked() for t in self.tiles):
            self.tiles[0].setChecked(True)
        self._group.buttonClicked.connect(lambda _b: self.changed.emit())

    def currentData(self) -> str:
        return next(t.key for t in self.tiles if t.isChecked())

    def setFraction(self, f: float):
        for t in self.tiles:
            t.frac = f
            t.update()

    def set_pro(self, pro: bool):
        """没激活:固定顶部,另外两块置灰;激活后三块都能点。"""
        for t in self.tiles:
            t.setEnabled(pro or t.key == "top")
            t.setToolTip("" if (pro or t.key == "top") else "会员可把弹幕带放在居中或底部")
        if not pro:
            self.tiles[0].setChecked(True)


class KeywordFlow(QtWidgets.QLayout):
    """Lay out removable keywords in rows, sized to the available width."""

    def __init__(self, parent):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(6)

    def addItem(self, item):
        self._items.append(item)
        self.invalidate()

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < self.count() else None

    def takeAt(self, index):
        if 0 <= index < self.count():
            item = self._items.pop(index)
            self.invalidate()
            return item
        return None

    def expandingDirections(self):
        return QtCore.Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QtCore.QRect(0, 0, width, 0), False)

    def minimumSize(self):
        return QtCore.QSize(0, 32 if self.count() else 0)

    def sizeHint(self):
        return QtCore.QSize(300, self.heightForWidth(300))

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, True)

    def _arrange(self, rect, apply):
        x, y, row_height = rect.x(), rect.y(), 0
        width = max(1, rect.width())
        for item in self._items:
            hint = item.sizeHint()
            w, h = min(width, hint.width()), hint.height()
            if x > rect.x() and x + w > rect.x() + width:
                x, y, row_height = rect.x(), y + row_height + self.spacing(), 0
            if apply:
                item.setGeometry(QtCore.QRect(x, y, w, h))
            x += w + self.spacing()
            row_height = max(row_height, h)
        return y - rect.y() + row_height


class KeywordChip(QtWidgets.QFrame):
    def __init__(self, word, remove):
        super().__init__()
        self.word = word
        self.setObjectName("keywordChip")
        self.setToolTip(word)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 2, 4, 2)
        layout.setSpacing(4)
        self.label = QtWidgets.QLabel(word)
        self.label.setMinimumWidth(0)
        self.label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        layout.addWidget(self.label, 1)
        self.remove_btn = QtWidgets.QToolButton()
        self.remove_btn.setObjectName("removeKeyword")
        self.remove_btn.setText("×")
        self.remove_btn.setFixedSize(24, 24)
        self.remove_btn.setAccessibleName(f"删除关注词 {word}")
        self.remove_btn.setToolTip(f"删除 {word}")
        self.remove_btn.clicked.connect(lambda: remove(word))
        layout.addWidget(self.remove_btn)

    def sizeHint(self):
        return QtCore.QSize(min(300, self.label.fontMetrics().horizontalAdvance(self.word) + 52), 32)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.label.setText(self.label.fontMetrics().elidedText(
            self.word, QtCore.Qt.TextElideMode.ElideRight, max(0, self.width() - 42)))


class KeywordList(QtWidgets.QWidget):
    """Compact removable keywords; free edition accepts one keyword."""

    changed = QtCore.Signal()

    def __init__(self, words, pro: bool):
        super().__init__()
        self._pro = pro
        self._words = list(dict.fromkeys(str(w).strip() for w in words if str(w).strip()))
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.chips = QtWidgets.QWidget()
        self.chips.setAccessibleName("已添加的关注词")
        self.flow = KeywordFlow(self.chips)
        v.addWidget(self.chips)
        row = QtWidgets.QHBoxLayout()
        self.edit = QtWidgets.QLineEdit()
        self.edit.setAccessibleName("新关注词")
        self.edit.returnPressed.connect(self._add)
        self.add_btn = QtWidgets.QPushButton("添加")
        self.add_btn.clicked.connect(self._add)
        row.addWidget(self.edit, 1)
        row.addWidget(self.add_btn)
        v.addLayout(row)
        self.count = QtWidgets.QLabel()
        self.count.setWordWrap(True)
        v.addWidget(self.count)
        self.set_pro(pro)

    def set_pro(self, pro: bool):
        self._pro = pro
        self.edit.setPlaceholderText("关键词或别名，如 贵州茅台|600519" if pro else "填一个关键词,如 降准")
        self._refresh()

    def _refresh(self):
        while self.flow.count():
            widget = self.flow.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        for word in self._words:
            self.flow.addWidget(KeywordChip(word, self._remove))
        self.chips.setVisible(bool(self._words))
        self.chips.updateGeometry()
        n = len(self._words)
        full = not self._pro and n >= 1
        self.edit.setEnabled(not full)
        self.add_btn.setEnabled(not full)
        if self._pro:
            self.count.setText(f"已配置 {n} 个关注词")
        elif n > 1:
            self.count.setText(f"已配置 {n} 个;免费版只有第 1 个生效,会员全部生效")
        else:
            self.count.setText(f"已配置 {n} 个 · 免费版 1 个;会员不限,填股票名或代码都能认出来")
        self.updateGeometry()

    def _add(self):
        for word in self.edit.text().replace("，", ",").split(","):
            word = word.strip()
            if not self._pro and self._words:
                break
            if word and word not in self._words:
                self._words.append(word)
        self.edit.clear()
        self._refresh()
        self.changed.emit()

    def _remove(self, word):
        if word not in self._words:
            return
        index = self._words.index(word)
        self._words.remove(word)
        self._refresh()
        self.changed.emit()
        if self.flow.count():
            self.flow.itemAt(min(index, self.flow.count() - 1)).widget().remove_btn.setFocus()
        else:
            self.edit.setFocus()

    def values(self) -> list:
        return list(self._words)


class SettingsDialog(QtWidgets.QDialog):
    applied = QtCore.Signal()
    probe_sig = QtCore.Signal(str)
    probe_done = QtCore.Signal()

    LBL_W = 96

    def __init__(self, conf: dict, sources: list | None = None):
        super().__init__()
        self.setWindowTitle("Bulletin · 弹讯设置")
        self.setMinimumSize(780, 520)
        avail = QtWidgets.QApplication.primaryScreen().availableGeometry().height()
        self.resize(940, min(800, avail - 40))
        self._c = conf
        self._t = THEMES.get(conf.get("theme", "浅色"), THEMES["浅色"])
        self._tags: list = []
        self._titles: list = []
        self._rlabels: list = []
        self._vlabels: list = []
        self._tlabels: list = []
        self._toggles: list = []
        self._previews: list = []
        self.setStyleSheet(self._qss(self._t))

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(0)
        self.nav = QtWidgets.QListWidget()
        self.nav.setObjectName("settingsNav")
        self.nav.setAccessibleName("设置分类")
        self.nav.setFixedWidth(156)
        self.nav.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pages = QtWidgets.QStackedWidget()
        self._page_layouts = []
        self._page_scrolls = []
        descriptions = [
            ("弹幕外观", "调整文字、显示区域和弹幕节奏，修改后自动生效。"),
            ("关注与过滤", "突出你关心的消息，减少不想看的内容。"),
            ("新闻源", "选择内容来源，添加 RSS 链接并检查是否可用。"),
            ("回看与隐私", "管理收藏位置、屏幕共享和快捷操作。"),
            ("会员", "查看会员状态与功能。免费功能始终可用。"),
        ]
        for title, description in descriptions:
            self.nav.addItem(title)
            item = self.nav.item(self.nav.count() - 1)
            item.setSizeHint(QtCore.QSize(140, 44))
            page = QtWidgets.QScrollArea()
            page.setWidgetResizable(True)
            page.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            content = QtWidgets.QWidget()
            content.setObjectName("settingsPage")
            layout = QtWidgets.QVBoxLayout(content)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.setSpacing(16)
            heading = QtWidgets.QLabel(title)
            heading.setObjectName("pageHeading")
            layout.addWidget(heading)
            hint = QtWidgets.QLabel(description)
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color:{self._t['mut']};background:transparent;")
            self._rlabels.append(hint)
            layout.addWidget(hint)
            page.setWidget(content)
            self.pages.addWidget(page)
            self._page_layouts.append(layout)
            self._page_scrolls.append(page)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        body.addWidget(self.nav)
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)

        g = self._card("设置面板", self._page_layouts[0])
        self.theme_combo = QtWidgets.QComboBox()
        self.theme_combo.addItems(THEMES)
        self.theme_combo.setCurrentText(conf.get("theme", "浅色"))
        self.theme_combo.currentTextChanged.connect(self._apply_theme)
        self._row(g, "面板主题", self.theme_combo)

        def _area_lbl(v):
            return {25: "1/4 屏", 50: "半屏", 75: "3/4 屏", 100: "满屏"}.get(v, f"{v}%")

        def _speed_lbl(v):
            s = v / 10
            return "慢" if s < 2 else ("快" if s > 4.5 else "适中")

        def _flow_lbl(v):
            return "密" if v < 700 else ("疏" if v > 1700 else "适中")

        # ============ 左栏:弹幕样式 ============
        g = self._card("弹幕样式", self._page_layouts[0])
        self.area = self._slider_row(g, "显示区域", 25, 100, float(conf["top_fraction"]) * 100, _area_lbl)
        self.position = PositionPicker(self, "top", float(conf["top_fraction"]))
        self.position.set_pro(False)
        self._previews += self.position.tiles
        self.area.valueChanged.connect(lambda v: self.position.setFraction(v / 100.0))
        pos_row = QtWidgets.QHBoxLayout()
        pos_row.addWidget(self.position, 1)
        self._row(g, "位置", self._pro_teaser(pos_row, "会员版可把弹幕带放在屏幕居中或底部"))
        self.opacity = self._slider_row(g, "不透明度", 20, 100, float(conf["opacity"]) * 100, lambda v: f"{v}%")
        self.size = self._slider_row(g, "字号", 12, 80, int(conf["font_size"]), lambda v: f"{v}px")
        self.speed = self._slider_row(g, "速度", 5, 90, float(conf["speed"]) * 10, _speed_lbl)
        self.outline = self._slider_row(g, "描边", 0, 6, int(conf.get("outline", 2)),
                                        lambda v: "无" if v == 0 else f"{v}px")

        self.color = conf["font_color"]
        self.color_btn = QtWidgets.QPushButton(self.color)
        self.color_btn.setFixedWidth(96)
        self.color_btn.clicked.connect(self._pick)
        cw, self.colorful = self._toggle_cell("按来源配色", conf.get("colorful", False))
        crow = QtWidgets.QHBoxLayout()
        crow.addWidget(self.color_btn)
        crow.addSpacing(14)
        crow.addWidget(cw, 1)
        self._row(g, "颜色", crow)

        cur_font = conf["font_family"] or cfg.default_font_family()
        installed = set(QtGui.QFontDatabase.families())
        cands = ["PingFang SC", "Hiragino Sans GB", "Songti SC", "Kaiti SC", "STKaiti",
                 "Xingkai SC", "Yuanti SC", "Heiti SC", "Noto Sans CJK SC", "Source Han Sans SC",
                 "Microsoft YaHei", "SF Pro Text", "Helvetica Neue", "Menlo"]
        fonts = [f for f in cands if f in installed]
        if cur_font and cur_font not in fonts:
            fonts.insert(0, cur_font)
        self.family = QtWidgets.QComboBox()    # 纯下拉:点一下直接出字体列表
        self.family.addItems(fonts)
        self.family.setCurrentText(cur_font)
        self._row(g, "字体", self.family)

        # ============ 左栏:显示与密度 ============
        g = self._card("显示与密度", self._page_layouts[0])
        self.display = QtWidgets.QComboBox()
        self.display.addItem("此屏(主)", "primary")
        self.display.addItem("横贯全部(接力/双屏)", "span")
        for s in QtWidgets.QApplication.screens():
            self.display.addItem(f"屏:{s.name()}", s.name())
        _i = self.display.findData(conf.get("display", "primary"))
        self.display.setCurrentIndex(_i if _i >= 0 else 0)
        self._row(g, "显示器", self.display)

        self.interval = self._slider_row(g, "消息密度", 200, 3000, int(conf["spawn_interval_ms"]), _flow_lbl)
        self._hint(g, "消息密度控制弹幕出现的疏密，不影响抓取频率；实际间隔会随待显示消息数调整。")
        self.maxon = self._slider_row(g, "同屏上限", 10, 200, int(conf["max_on_screen"]), lambda v: f"{v}条")

        tg = QtWidgets.QGridLayout()
        tg.setHorizontalSpacing(24)
        tg.setVerticalSpacing(7)
        w1, self.hover_pause = self._toggle_cell("鼠标停留时暂停", conf.get("hover_pause", True))
        w2, self.show_tag = self._toggle_cell("来源标签", conf.get("show_source_tag", True))
        w3, self.show_time = self._toggle_cell("显示时间", conf.get("show_time", True))
        w4, self.adaptive = self._toggle_cell("长消息加速", conf.get("adaptive_speed", False))
        w5, self.autostart_sw = self._toggle_cell("开机自启", autostart.is_enabled())
        w6, self.fs_pause_sw = self._toggle_cell("全屏应用时暂停", conf.get("pause_on_fullscreen", True))
        for i, w in enumerate((w1, w2, w3, w4, w5, w6)):
            tg.addWidget(w, i // 2, i % 2)
        self._row(g, "", tg)

        # ============ 右栏:关注词提醒 ============
        g = self._card("关注词提醒", self._page_layouts[1])
        self.alert_kw = KeywordList(conf.get("alert_keywords", []), False)
        self._row(g, "关注词", self.alert_kw)
        ag = QtWidgets.QGridLayout()
        ag.setHorizontalSpacing(24)
        a1, self.alert_sound = self._toggle_cell("命中时响提示音", conf.get("alert_sound", True))
        a2, self.alert_notify = self._toggle_cell("弹幕不可见时通知", conf.get("alert_notify", True))
        ag.addWidget(a1, 0, 0)
        ag.addWidget(a2, 0, 1)
        self._row(g, "", ag)
        tg2 = QtWidgets.QGridLayout()
        tg2.setHorizontalSpacing(24)
        for i, name in enumerate(("命中时始终通知", "只显示关注消息")):
            cell, sw = self._toggle_cell(name, False)
            sw.setEnabled(False)
            tg2.addWidget(cell, 0, i)
        self._row(g, "", self._pro_teaser(tg2, "会员版:弹幕在放时命中也发通知,或只放命中关注词的快讯"))

        # ============ 右栏:过滤 ============
        g = self._card("过滤", self._page_layouts[1])
        self.mute = QtWidgets.QLineEdit(", ".join(conf.get("mute_keywords", [])))
        self.mute.setPlaceholderText("屏蔽词,逗号分隔;含这些词的不显示")
        self._row(g, "屏蔽词", self.mute)

        self.only_kw = QtWidgets.QLineEdit(", ".join(conf.get("only_keywords", [])))
        self.only_kw.setPlaceholderText("只显示含这些词的弹幕(逗号分隔)")
        ww, self.only_enabled = self._toggle_cell("", conf.get("only_enabled", False))
        wl = QtWidgets.QHBoxLayout()
        wl.addWidget(self.only_kw, 1)
        wl.addSpacing(10)
        wl.addWidget(self.only_enabled)
        self._row(g, "内容关键词", wl)
        self._hint(g, "开启右侧开关后，只显示含这里任一关键词的消息；命中上方关注词的消息也会保留。多个词用逗号分隔。")
        self.only_kw.setEnabled(self.only_enabled.isChecked())
        self.only_enabled.toggled.connect(self.only_kw.setEnabled)

        self._sources = list(sources or [])
        self.src_checks: dict = {}
        muted = set(conf.get("muted_sources", []))
        if self._sources:
            sg = QtWidgets.QGridLayout()
            sg.setHorizontalSpacing(20)
            sg.setVerticalSpacing(7)
            for i, s in enumerate(self._sources):
                cell, sw = self._toggle_cell(s, s not in muted)
                self.src_checks[s] = sw
                sg.addWidget(cell, i // 2, i % 2)
            self._row(g, "按来源", sg)
        else:
            hint = QtWidgets.QLabel("等弹幕出现后再打开设置,即可逐源开关")
            hint.setStyleSheet(f"color:{self._t['mut']};background:transparent;")
            self._rlabels.append(hint)
            self._row(g, "按来源", hint)

        # ============ 右栏:新闻源 ============
        g = self._card("新闻源", self._page_layouts[2])
        self.rsshub = QtWidgets.QLineEdit(conf.get("rsshub_base", ""))
        self.rsshub.setPlaceholderText("自建 RSSHub 基址;feeds 里 {rsshub} 会替换成它")
        self._row(g, "自建中转", self.rsshub)
        self._hint(g, "仅使用自建 RSSHub 的用户需要填写。普通 RSS 链接无需修改此项；链接中的 {rsshub} 会替换为这个地址。")
        self.poll = self._slider_row(g, "检查间隔", 15, 600, int(conf["poll_interval"]), lambda v: f"{v}s")
        self.conserve = QtWidgets.QCheckBox("降低日报、热搜的检查频率")
        self.conserve.setChecked(conf.get("conserve_bandwidth", True))
        self._row(g, "省流", self.conserve)
        conserve_hint = QtWidgets.QLabel("日报每 30 分钟、热搜每 10 分钟检查一次。\n其它来源使用上面的检查间隔。")
        conserve_hint.setWordWrap(True)
        conserve_hint.setStyleSheet(f"color:{self._t['mut']};background:transparent;")
        self._rlabels.append(conserve_hint)
        self._row(g, "", conserve_hint)

        prow = QtWidgets.QHBoxLayout()
        plab = QtWidgets.QLabel("预设")
        plab.setStyleSheet(f"color:{self._t['mut']};background:transparent;")
        self._rlabels.append(plab)
        prow.addWidget(plab)
        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.addItems(cfg.PRESETS)
        prow.addWidget(self.preset_combo, 1)
        preset_btn = QtWidgets.QPushButton("使用预设…")
        preset_btn.clicked.connect(self._use_preset)
        prow.addWidget(preset_btn)
        prow.addStretch(1)
        self.detect_btn = QtWidgets.QPushButton("检测源")
        self.detect_btn.clicked.connect(self._detect)
        prow.addWidget(self.detect_btn)
        g.addLayout(prow)

        self.feeds = QtWidgets.QPlainTextEdit("\n".join(conf["feeds"]))
        self.feeds.setMinimumHeight(150)
        self.feeds.setPlaceholderText("每行一个 RSS 链接;用自建 RSSHub 时可写 {rsshub}/路由")
        self._row(g, "链接", self.feeds)

        self.probe_box = QtWidgets.QPlainTextEdit()
        self.probe_box.setReadOnly(True)
        self.probe_box.setFixedHeight(100)
        self.probe_box.setPlaceholderText("点「检测源」逐条测试(✓ 可用 / ✗ 不可用)")
        self.probe_sig.connect(self.probe_box.appendPlainText)
        self.probe_done.connect(lambda: self.detect_btn.setEnabled(True))
        self._row(g, "检测", self.probe_box)

        g = self._card("会员功能预览", self._page_layouts[3])
        hint = QtWidgets.QLabel("会员版支持回看与收藏、存到 Obsidian、共享屏幕时隐藏弹幕和全局快捷键。")
        hint.setWordWrap(True)
        self._row(g, "说明", hint)
        for name in ("共享屏幕时隐藏", "全局快捷键"):
            cell, switch = self._toggle_cell(name, False)
            switch.setEnabled(False)
            self._row(g, "", self._pro_teaser(cell, "此功能在会员版中提供"))

        # ============ 左栏:会员版功能 ============
        g = self._card("免费和会员差在哪", self._page_layouts[4])
        promo = QtWidgets.QLabel(
            "<table cellspacing='0' cellpadding='4'>"
            "<tr><td></td><td><b>免费</b></td><td><b>会员</b></td></tr>"
            "<tr><td>能盯几只股票、几个关键词</td><td>1 个</td><td>不限,填名字或代码都认得</td></tr>"
            "<tr><td>同一件事好几家都报</td><td>每条都放</td><td>只放一次</td></tr>"
            "<tr><td>走开时错过了什么</td><td>暂停时才通知</td><td>每条命中都进通知栏</td></tr>"
            "<tr><td>弹幕放哪</td><td>屏幕顶部</td><td>顶部、中间或底部</td></tr>"
            "</table>还有回看与收藏、共享屏幕时自动隐藏、全局快捷键。<br>"
            f'<a href="{PRO_URL}">了解会员版 ↗</a>')
        promo.setWordWrap(True)
        promo.setOpenExternalLinks(True)
        self._tlabels.append(promo)
        self._row(g, "", promo)

        for layout in self._page_layouts:
            layout.addStretch(1)

        self.saved_lbl = QtWidgets.QLabel("修改后自动生效，并记住设置")
        self.saved_lbl.setObjectName("saveStatus")
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.close)
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Close).setText("完成")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Close).setObjectName("primaryButton")
        for button in self.findChildren(QtWidgets.QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        bottom = QtWidgets.QHBoxLayout()
        bottom.setContentsMargins(20, 14, 20, 16)
        bottom.addWidget(self.saved_lbl)
        bottom.addStretch(1)
        bottom.addWidget(btns)
        footer = QtWidgets.QFrame()
        footer.setObjectName("settingsFooter")
        footer.setLayout(bottom)
        outer.addWidget(footer)
        self._live_timer = QtCore.QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.timeout.connect(self._save)
        self._last_autostart = self.autostart_sw.isChecked()
        for slider in self.findChildren(QtWidgets.QSlider):
            slider.valueChanged.connect(lambda _value: self._queue_apply(60))
        for switch in self._toggles:
            switch.toggled.connect(lambda _value: self._queue_apply(0))
        for combo in self.findChildren(QtWidgets.QComboBox):
            if combo is not self.preset_combo:
                combo.currentIndexChanged.connect(lambda _value: self._queue_apply(0))
        self.conserve.toggled.connect(lambda _value: self._queue_apply(0))
        for field in (self.mute, self.only_kw, self.rsshub):
            field.textChanged.connect(lambda _text: self._queue_apply(500))
        if hasattr(self, "obs_dir"):
            self.obs_dir.textChanged.connect(lambda _text: self._queue_apply(500))
        self.feeds.textChanged.connect(lambda: self._queue_apply(600))
        self.alert_kw.changed.connect(lambda: self._queue_apply(0))

    def _queue_apply(self, delay=0):
        self.saved_lbl.setText("正在应用…")
        if delay <= 60 and self._live_timer.isActive() and self._live_timer.remainingTime() <= delay:
            return
        self._live_timer.start(delay)

    def _flush_live(self):
        if self._live_timer.isActive():
            self._live_timer.stop()
            self._save()

    def closeEvent(self, event):
        self._flush_live()
        super().closeEvent(event)

    def reject(self):
        self._flush_live()
        super().reject()

    def keyPressEvent(self, event):
        # Enter in the keyword editor adds a tag without closing the dialog.
        if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)

    # ---------- 主题 ----------
    @staticmethod
    def _qss(t):
        accent = QtGui.QColor(t["fill"])
        selection = f"rgba({accent.red()},{accent.green()},{accent.blue()},28)"
        return (
            f"QDialog,QScrollArea,QScrollArea>QWidget>QWidget{{background:{t['bg']};}}"
            f"QLabel{{color:{t['tx']};background:transparent;}}"
            "QScrollArea{border:none;}"
            "QScrollBar:vertical{background:transparent;width:12px;margin:4px 0;border:0;}"
            f"QScrollBar::handle:vertical{{background:{t['mut']};border-radius:3px;min-height:36px;margin:0 3px;}}"
            f"QScrollBar::handle:vertical:hover{{background:{t['tx']};}}"
            f"QScrollBar::handle:vertical:pressed{{background:{t['fill']};}}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;width:0;background:transparent;border:0;}"
            "QScrollBar::up-arrow:vertical,QScrollBar::down-arrow:vertical{width:0;height:0;}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
            "QScrollBar:horizontal{background:transparent;height:12px;margin:0 4px;border:0;}"
            f"QScrollBar::handle:horizontal{{background:{t['mut']};border-radius:3px;min-width:36px;margin:3px 0;}}"
            f"QScrollBar::handle:horizontal:hover{{background:{t['tx']};}}"
            f"QScrollBar::handle:horizontal:pressed{{background:{t['fill']};}}"
            "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{height:0;width:0;background:transparent;border:0;}"
            "QScrollBar::left-arrow:horizontal,QScrollBar::right-arrow:horizontal{width:0;height:0;}"
            "QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal{background:transparent;}"
            "QAbstractScrollArea::corner{background:transparent;border:0;}"
            f"QFrame#settingsFooter{{background:{t['card']};border:0;border-top:1px solid {t['bd']};}}"

            f"QFrame#card{{background:{t['card']};border:1px solid {t['bd']};border-radius:10px;}}"
            f"QSpinBox,QPlainTextEdit,QLineEdit,QFontComboBox,QComboBox{{background:{t['inp']};"
            f"color:{t['tx']};border:1px solid {t['bd']};border-radius:8px;padding:7px 10px;}}"
            f"QLineEdit:hover,QComboBox:hover,QPlainTextEdit:hover{{border-color:{t['mut']};}}"
            f"QLineEdit:focus,QComboBox:focus,QPlainTextEdit:focus{{border-color:{t['fill']};}}"
            f"QComboBox::drop-down{{border:none;}}QComboBox QAbstractItemView{{background:{t['inp']};"
            f"color:{t['tx']};selection-background-color:{t['bd']};}}"
            f"QPushButton{{background:{t['card']};color:{t['tx']};border:1px solid {t['bd']};"
            f"border-radius:8px;padding:7px 14px;}}QPushButton:hover{{border-color:{t['fill']};}}"
            f"QSlider::groove:horizontal{{height:5px;background:{t['bd']};border-radius:2px;}}"
            f"QSlider::sub-page:horizontal{{background:{t['fill']};border-radius:2px;}}"
            f"QSlider::handle:horizontal{{width:15px;height:15px;margin:-5px 0;background:{t['hd']};"
            f"border:2px solid {t['fill']};border-radius:6px;}}"
            f"QListWidget{{background:{t['inp']};color:{t['tx']};border:1px solid {t['bd']};border-radius:6px;}}"
            "QListWidget::item{padding:3px 6px;}"
            f"QListWidget::item:selected{{background:{t['bd']};color:{t['tx']};}}"
            f"QLabel#proTag{{color:{t['fill']};border:1px solid {t['fill']};border-radius:8px;"
            "padding:2px 7px;font-size:11px;max-height:20px;}"
            f"QDialog{{font-size:13px;}}QWidget#settingsPage{{background:{t['bg']};}}"
            f"QLabel#pageHeading{{font-size:24px;font-weight:600;color:{t['tx']};}}"
            f"QLabel#saveStatus{{color:{t['mut']};}}"
            f"QListWidget#settingsNav{{background:{t['card']};border:0;border-right:1px solid {t['bd']};border-radius:0;padding:18px 8px;}}"
            f"QListWidget#settingsNav::item{{padding:0 12px;border-radius:6px;margin:3px 0;}}"
            f"QListWidget#settingsNav::item:selected{{background:{selection};color:{t['ti']};font-weight:600;}}"
            f"QListWidget#settingsNav::item:hover{{background:{selection};}}"
            f"QListWidget#settingsNav::item:focus{{border:1px solid {t['fill']};}}"
            f"QPushButton#primaryButton{{background:{t['ti']};color:{t['bg']};border-color:{t['ti']};font-weight:600;}}"
            f"QPushButton:focus,QAbstractButton:focus{{border:1px solid {t['fill']};}}"
            f"QPushButton:pressed{{background:{selection};}}"
            f"QSlider:focus{{background:{t['bd']};border-radius:4px;}}"
            f"QFrame#keywordChip{{background:{t['bd']};border-radius:16px;}}"
            f"QToolButton#removeKeyword{{color:{t['tx']};background:transparent;border:0;border-radius:12px;font-size:17px;}}"
            f"QToolButton#removeKeyword:hover,QToolButton#removeKeyword:focus{{background:{t['card']};border:1px solid {t['ti']};}}"
            f"QWidget:disabled{{color:{t['mut']};}}"
        )

    def _apply_theme(self, name):
        self._t = THEMES.get(name, self._t)
        t = self._t
        self.setStyleSheet(self._qss(t))
        for page in self._page_scrolls:
            page.viewport().setStyleSheet(f"background:{t['bg']};")
        for lb in self._titles:
            lb.setStyleSheet(f"color:{t['ti']};font-weight:600;font-size:14px;padding:4px 0;background:transparent;")
        for lb in self._rlabels:
            lb.setStyleSheet(f"color:{t['mut']};background:transparent;")
        for lb in self._vlabels:
            lb.setStyleSheet(f"color:{t['vlab']};background:transparent;")
        for lb in self._tlabels:
            lb.setStyleSheet(f"color:{t['tx']};background:transparent;")
        for sw in self._toggles:
            sw.update()
        for pv in self._previews:
            pv.update()

    def _pro_teaser(self, widget_or_layout, tip: str):
        """会员功能的预览:控件置灰,后面挂「会员」小标签,点开会员页。"""
        row = QtWidgets.QHBoxLayout()
        if isinstance(widget_or_layout, QtWidgets.QLayout):
            row.addLayout(widget_or_layout, 1)
        else:
            row.addWidget(widget_or_layout, 1)
        tag = QtWidgets.QLabel(f'<a href="{PRO_URL}" style="text-decoration:none;color:{self._t["fill"]}">会员</a>')
        tag.setObjectName("proTag")
        tag.setOpenExternalLinks(True)
        tag.setToolTip(tip + "。点击了解会员版。")
        row.addSpacing(8)
        row.addWidget(tag)
        return row

    # ---------- 布局小工具 ----------
    def _card(self, title, col):
        lab = QtWidgets.QLabel(title)
        lab.setStyleSheet(f"color:{self._t['ti']};font-weight:600;font-size:14px;padding:4px 0;background:transparent;")
        self._titles.append(lab)
        col.addWidget(lab)
        frame = QtWidgets.QFrame()
        frame.setObjectName("card")
        v = QtWidgets.QVBoxLayout(frame)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(14)
        col.addWidget(frame)
        return v

    def _row(self, card, label, widget_or_layout):
        row = QtWidgets.QHBoxLayout()
        lab = QtWidgets.QLabel(label)
        lab.setFixedWidth(self.LBL_W)
        lab.setWordWrap(True)
        lab.setStyleSheet(f"color:{self._t['mut']};background:transparent;")
        lab.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self._rlabels.append(lab)
        if label:
            row.addWidget(lab)
        else:
            lab.hide()
        if isinstance(widget_or_layout, QtWidgets.QLayout):
            row.addLayout(widget_or_layout, 1)
        else:
            row.addWidget(widget_or_layout, 1)
            if label:
                lab.setBuddy(widget_or_layout)
                widget_or_layout.setAccessibleName(label)
        card.addLayout(row)

    def _slider_row(self, card, label, lo, hi, val, fmt):
        s = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        s.setAccessibleName(label)
        s.setMinimumHeight(28)
        s.setRange(int(lo), int(hi))
        s.setValue(int(val))
        vlab = QtWidgets.QLabel(fmt(int(val)))
        vlab.setMinimumWidth(50)
        vlab.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        vlab.setStyleSheet(f"color:{self._t['vlab']};background:transparent;")
        self._vlabels.append(vlab)
        s.valueChanged.connect(lambda v: vlab.setText(fmt(v)))
        wrap = QtWidgets.QHBoxLayout()
        wrap.addWidget(s, 1)
        wrap.addWidget(vlab)
        self._row(card, label, wrap)
        return s

    def _hint(self, card, text):
        hint = QtWidgets.QLabel(text)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{self._t['mut']};background:transparent;")
        self._rlabels.append(hint)
        self._row(card, "", hint)

    def _toggle_cell(self, label, checked):
        descriptions = {'命中时始终通知': '收到含关注词的新消息就发系统通知，弹幕正在显示时也通知。', '只显示关注消息': '只显示含上方关注词的消息，其它消息不进入弹幕。', '命中时响提示音': '收到含关注词的新消息时响一声；连续命中最多每 3 秒响一次。', '弹幕不可见时通知': '弹幕暂停、隐藏或因全屏暂停时，收到含关注词的消息仍发系统通知。', '长消息加速': '让较长的文字更快滑过；关注词命中的消息仍保持慢速。', '按来源配色': '不同来源的弹幕用不同颜色，覆盖左侧选择的统一文字颜色。', '鼠标停留时暂停': '鼠标移到某条弹幕上时暂停这一条，移开后继续。', '全屏应用时暂停': '使用全屏应用时暂停显示弹幕，退出全屏后恢复。', '合并重复消息': '不同来源报道相似的同一事件时只显示一次，并标出新增来源数。', '共享屏幕时隐藏': '截图、录屏或共享屏幕时隐藏弹幕层；不影响自己桌面上观看。'}
        description = descriptions.get(label, "")
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        if label:
            text = QtWidgets.QVBoxLayout()
            text.setSpacing(4)
            lab = QtWidgets.QLabel(label)
            lab.setWordWrap(True)
            self._tlabels.append(lab)
            text.addWidget(lab)
            if description:
                hint = QtWidgets.QLabel(description)
                hint.setWordWrap(True)
                hint.setStyleSheet(f"color:{self._t['mut']};background:transparent;")
                self._rlabels.append(hint)
                text.addWidget(hint)
            h.addLayout(text, 1)
        else:
            h.addStretch(1)
        sw = ToggleSwitch(checked, self)
        sw.setAccessibleName(label or "启用内容关键词过滤")
        sw.setAccessibleDescription(description)
        self._toggles.append(sw)
        h.addWidget(sw)
        return w, sw

    def _use_preset(self):
        name = self.preset_combo.currentText()
        if self.feeds.toPlainText().strip():
            answer = QtWidgets.QMessageBox.question(
                self, "替换新闻源？", f"使用「{name}」预设会替换当前链接列表，并立即应用。",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No)
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self.feeds.setPlainText("\n".join(cfg.PRESETS[name]))

    def _detect(self):
        base = self.rsshub.text().strip()
        urls = [ln.strip() for ln in self.feeds.toPlainText().splitlines() if ln.strip()]
        self.probe_box.clear()
        if not urls:
            self.probe_box.setPlainText("请先添加 RSS 链接，或选择一套预设。")
            return
        self.detect_btn.setEnabled(False)

        def work():
            ok = 0
            for u in urls:
                good, n, age = probe_feed(u, base)
                shown = resolve_url(u, base)
                self.probe_sig.emit(f"{'✓' if good else '✗'} {n:>3}条 {age:<8} {shown[:46]}")
                if good:
                    ok += 1
            self.probe_sig.emit(f"—— {ok}/{len(urls)} 可用 ——")
            self.probe_done.emit()

        threading.Thread(target=work, daemon=True).start()

    def _pick(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.color), self, "弹幕颜色")
        if c.isValid():
            self.color = c.name()
            self.color_btn.setText(self.color)
            self._queue_apply(0)

    def _save(self):
        c = self._c
        before = dict(c)
        c["alert_keywords"] = self.alert_kw.values()[:1]
        c["alert_sound"] = self.alert_sound.isChecked()
        c["alert_notify"] = self.alert_notify.isChecked()
        c["font_size"] = self.size.value()
        c["font_color"] = self.color
        c["font_family"] = self.family.currentText()
        c["speed"] = round(self.speed.value() / 10, 1)
        c["opacity"] = round(self.opacity.value() / 100, 2)
        c["top_fraction"] = round(self.area.value() / 100, 2)
        c["colorful"] = self.colorful.isChecked()
        c["outline"] = self.outline.value()
        c["adaptive_speed"] = self.adaptive.isChecked()
        c["pause_on_fullscreen"] = self.fs_pause_sw.isChecked()
        c["autostart"] = self.autostart_sw.isChecked()
        if c["autostart"] != self._last_autostart:
            autostart.set_autostart(c["autostart"])
            self._last_autostart = c["autostart"]
        c["only_enabled"] = self.only_enabled.isChecked()
        c["only_keywords"] = [w.strip() for w in self.only_kw.text().replace("，", ",").split(",") if w.strip()]
        if self.src_checks:
            c["muted_sources"] = [s for s, sw in self.src_checks.items() if not sw.isChecked()]
        c["spawn_interval_ms"] = self.interval.value()
        c["max_on_screen"] = self.maxon.value()
        c["hover_pause"] = self.hover_pause.isChecked()
        c["show_source_tag"] = self.show_tag.isChecked()
        c["show_time"] = self.show_time.isChecked()
        c["display"] = self.display.currentData()
        c["theme"] = self.theme_combo.currentText()
        c["rsshub_base"] = self.rsshub.text().strip() or "https://rsshub.app"
        c["mute_keywords"] = [w.strip() for w in self.mute.text().replace("，", ",").split(",") if w.strip()]
        c["poll_interval"] = self.poll.value()
        c["conserve_bandwidth"] = self.conserve.isChecked()
        c["feeds"] = [ln.strip() for ln in self.feeds.toPlainText().splitlines() if ln.strip()]
        if c != before:
            self.applied.emit()
        self.saved_lbl.setText("已自动应用并记住设置")

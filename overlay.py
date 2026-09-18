"""单画布桌面弹幕 overlay:

- 一个全屏透明置顶窗口(仅覆盖主屏一块;弹幕限制在此屏内,不越界飞到外接显示器)
- QPainter 一帧画完所有弹幕 → 零闪烁(不再是每条一个窗口)
- 泳道 + 等待队列 → 数学上保证不重叠;满了就排队,天然控密度
- 默认鼠标穿透;指针停在某条上时该条暂停+可点(左键原文/右键菜单)
- 来源分色标签、自适应速度(带开关)
"""
from __future__ import annotations

import collections
import ctypes
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

SPAWN_GAP = 60   # 同泳道两条之间最小水平间隔(px)


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
        self._src_colors: dict[str, str] = {}
        self.seen_sources: list[str] = []   # 出现过的来源(供「按来源开关」列表)
        self._dlg: SettingsDialog | None = None
        self._settings_paused = False

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

        self._build_tray()
        self.show()          # 用 show()(几何已=整屏);不用 showFullScreen 以免进 macOS 全屏 Space
        self.raise_()
        if sys.platform == "darwin":
            self._nswin = _nswindow_from_view(self.winId())
            _set_ignores_mouse(self._nswin, True)   # 默认穿透(OS 级)

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

    def _lane_h(self) -> int:
        return self._fm.height() + 10

    def _color_for(self, source: str) -> str:
        if source not in self._src_colors:
            pal = cfg.SOURCE_PALETTE
            self._src_colors[source] = pal[len(self._src_colors) % len(pal)]
        return self._src_colors[source]

    # ---------- 生成 ----------
    def _drain_queue(self):
        while True:
            try:
                item = self.q.get_nowait()
            except Exception:
                break
            self.pending.append(item)
            if item.source not in self.seen_sources:   # 供设置里「按来源开关」列出
                self.seen_sources.append(item.source)

    def _lane_of(self, d: Danmu, band_top: int, lh: int) -> int:
        return (d.y - band_top) // lh

    def _try_spawn(self):
        try:
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
            if only_on and only and not any(k in item.title.lower() for k in only):
                continue
            return item
        return None

    def _spawn_once(self):
        if self.paused or self._fs_paused or not self._interval_ok():
            return
        self._drain_queue()
        if not self.pending or len(self.danmu) >= int(self.conf["max_on_screen"]):
            return
        W = self.width()
        H = self.height()
        lh = self._lane_h()
        band_top = int(H * float(self.conf["top_offset"]))
        usable = int(H * float(self.conf["top_fraction"]))
        lanes = max(1, usable // lh)

        for ln in random.sample(range(lanes), lanes):
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
        color = self._color_for(item.source)
        tag = f"[{item.source}]  " if self.conf.get("show_source_tag", True) else ""
        time_str = (rel_time(item.ts) + "  ") if (self.conf.get("show_time", True) and item.ts) else ""
        tag_w = self._fm.horizontalAdvance(tag)
        time_w = self._fm.horizontalAdvance(time_str)
        title_w = self._fm.horizontalAdvance(item.title)
        w = tag_w + time_w + title_w
        base = float(self.conf["speed"])
        speed = base
        if self.conf.get("adaptive_speed", False):
            factor = 1.0 + min(0.6, (w / max(W, 1)) * 0.8)
            speed = min(base * factor, float(self.conf.get("adaptive_max_speed", 4.0)))
            if last is not None:                 # 不超车,防重叠
                speed = min(speed, last.speed)
        d = Danmu(text=item.title, url=item.url, source=item.source, source_color=color,
                  x=float(W), y=int(y), w=int(w), h=self._fm.height(),
                  tag_w=int(tag_w), time_str=time_str, time_w=int(time_w), speed=speed)
        self.danmu.append(d)

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
        if self.paused or self._fs_paused:      # 全屏(看视频/演示)时冻结,不打扰
            return
        dead = []
        for d in self.danmu:
            if d.paused:
                continue
            d.x -= d.speed
            if d.x + d.w < 0:
                dead.append(d)
        for d in dead:
            if d is self.hovered:
                self.hovered = None
            self.danmu.remove(d)
        self.update()

    # ---------- 绘制(单画布,零闪烁) ----------
    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
        p.setFont(self._font)
        fm = self._fm
        base_color = QtGui.QColor(self.conf["font_color"])
        time_color = QtGui.QColor("#93A2B4")
        shadow = QtGui.QColor(0, 0, 0, 200)
        show_tag = self.conf.get("show_source_tag", True)
        colorful = self.conf.get("colorful", False)
        ol = int(self.conf.get("outline", 2))
        _ring = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1))
        for d in self.danmu:
            x = int(d.x)
            base = d.y + fm.ascent()
            tag = f"[{d.source}]  " if show_tag else ""
            full = tag + d.time_str + d.text
            # 描边:八向黑边,强度=outline(0 时退化为单像素投影),保证任何背景上都清晰
            if ol > 0:
                p.setPen(QtGui.QColor(0, 0, 0, 235))
                for dx, dy in _ring:
                    p.drawText(x + dx * ol, base + dy * ol, full)
            else:
                p.setPen(shadow)
                p.drawText(x + 1, base + 1, full)
            # 分段上色:来源色 → 时间灰 → 正文
            cx = x
            if tag:
                p.setPen(QtGui.QColor(d.source_color))
                p.drawText(cx, base, tag)
                cx += d.tag_w
            if d.time_str:
                p.setPen(time_color)
                p.drawText(cx, base, d.time_str)
                cx += d.time_w
            p.setPen(QtGui.QColor(d.source_color) if colorful else base_color)
            p.drawText(cx, base, d.text)
            if d is self.hovered and d.url:
                p.setPen(QtGui.QPen(QtGui.QColor(d.source_color), 1))
                p.drawLine(x, base + 3, x + d.w, base + 3)
        p.end()

    # ---------- 悬停:指针下那条暂停并可点 ----------
    def _hover(self):
        lp = self.mapFromGlobal(QtGui.QCursor.pos())
        target = None
        for d in reversed(self.danmu):
            if QtCore.QRect(int(d.x), d.y, d.w, d.h).contains(lp):
                target = d
                break
        if target is not self.hovered:
            if self.hovered is not None:
                self.hovered.paused = False
            self.hovered = target
            if target is not None and self.conf.get("hover_pause", True):
                target.paused = True
            self.setCursor(
                QtCore.Qt.CursorShape.PointingHandCursor
                if (target is not None and target.url)
                else QtCore.Qt.CursorShape.ArrowCursor
            )
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
        d = self.hovered
        if d is None:
            e.ignore()
            return
        if e.button() == QtCore.Qt.MouseButton.LeftButton:
            if d.url:
                webbrowser.open(d.url)
                if d in self.danmu:            # 点过即移除(相当于已读)
                    self.danmu.remove(d)
                self.hovered = None
                self._set_click_through(True)  # 立即恢复穿透,别卡成隐形墙
        elif e.button() == QtCore.Qt.MouseButton.MiddleButton:
            QtWidgets.QApplication.clipboard().setText(d.text)   # 中键复制标题
            self.tray.showMessage("已复制标题", d.text, self.tray.icon(), 1500)
        elif e.button() == QtCore.Qt.MouseButton.RightButton:
            self._menu().popup(e.globalPosition().toPoint())
        e.accept()

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

    def toggle_pause(self):
        self.paused = not self.paused
        self.tray.setContextMenu(self._menu())

    def clear(self):
        self.danmu.clear()
        self.pending.clear()
        self.hovered = None
        self.update()

    # ---------- 设置 ----------
    def open_settings(self):
        if self._dlg is not None and self._dlg.isVisible():
            self._dlg.raise_()
            self._dlg.activateWindow()
            return
        self._dlg = SettingsDialog(self.conf, list(self.seen_sources))
        self._dlg.applied.connect(self._apply_settings)
        self._dlg.show()
        self._dlg.raise_()
        self._dlg.activateWindow()

    def _apply_settings(self):
        cfg.save(self.conf)
        self._rebuild_font()
        self.setWindowOpacity(float(self.conf["opacity"]))
        # 出现节流是单次触发+按积压动态排期,间隔改动下一拍自动生效,无需 setInterval
        self.setGeometry(self._target_geometry())   # 显示器/区域切换实时生效


# ===== 设置面板主题(经典配色板;仅影响设置窗外观,不改桌面弹幕) =====
# 每套:bg 底 / card 卡片 / bd 边框 / inp 输入框 / tx 文字 / mut 次要文字 /
#       ti 分区标题 / fill 滑块与聚焦 / hd 滑块把手 / on 开关开启色 / vlab 数值
THEMES = {
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
        self.setChecked(bool(checked))
        self.setFixedSize(42, 23)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _e):
        t = self._owner._t
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        on = self.isChecked()
        r = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(t["on"]) if on else QtGui.QColor(t["bd"]))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        cx = (r.right() - d - 3) if on else (r.left() + 3)
        p.setBrush(QtGui.QColor(t["hd"]))
        p.drawEllipse(QtCore.QRectF(cx, r.top() + 3, d, d))
        p.end()


class AreaPreview(QtWidgets.QWidget):
    """B站式弹幕区域指引:一块屏幕轮廓,顶部按占比着色 —— 一眼看出满屏/半屏。"""

    def __init__(self, owner, frac: float):
        super().__init__()
        self._owner = owner
        self._frac = float(frac)
        self.setFixedHeight(44)
        self.setMinimumWidth(96)

    def setFraction(self, f: float):
        self._frac = max(0.0, min(1.0, float(f)))
        self.update()

    def paintEvent(self, _e):
        t = self._owner._t
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        W, H = self.width(), self.height()
        sw = min(W - 2, int((H - 4) * 1.6))
        sh = int(sw / 1.6)
        x = (W - sw) // 2
        y = (H - sh) // 2
        # 屏幕外框
        p.setPen(QtGui.QPen(QtGui.QColor(t["bd"]), 1))
        p.setBrush(QtGui.QColor(t["inp"]))
        p.drawRoundedRect(x, y, sw - 1, sh - 1, 5, 5)
        # 顶部弹幕带(占屏高的 frac)
        band = int((sh - 4) * max(0.0, min(1.0, self._frac)))
        if band > 0:
            col = QtGui.QColor(t["fill"])
            col.setAlpha(70)
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(x + 2, y + 2, sw - 5, band, 3, 3)
            # 几条弹幕线示意
            p.setPen(QtGui.QPen(QtGui.QColor(t["fill"]), 2))
            lines = max(1, band // 7)
            for i in range(min(lines, 4)):
                ly = y + 5 + i * 7
                if ly > y + 2 + band - 2:
                    break
                lw = sw - 10 - (i % 3) * 14
                p.drawLine(x + 5, ly, x + 5 + max(10, lw), ly)
        p.end()


class SettingsDialog(QtWidgets.QDialog):
    applied = QtCore.Signal()
    probe_sig = QtCore.Signal(str)
    probe_done = QtCore.Signal()

    LBL_W = 62

    def __init__(self, conf: dict, sources: list | None = None):
        super().__init__()
        self.setWindowTitle("Bulletin · 弹讯设置")
        self.setMinimumWidth(880)
        self.resize(940, 740)   # 一屏放下:左栏加了区域预览+两个开关后更高,窗口相应加高
        self._c = conf
        self._t = THEMES.get(conf.get("theme", "Tokyo Night"), THEMES["Tokyo Night"])
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
        self._scroll = scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setStyleSheet(f"background:{self._t['bg']};")
        self._content = content = QtWidgets.QWidget()
        content.setStyleSheet(f"background:{self._t['bg']};")
        rootv = QtWidgets.QVBoxLayout(content)
        rootv.setContentsMargins(16, 12, 16, 8)
        rootv.setSpacing(10)

        trow = QtWidgets.QHBoxLayout()
        tlab = QtWidgets.QLabel("主题")
        tlab.setFixedWidth(self.LBL_W)
        tlab.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        tlab.setStyleSheet(f"color:{self._t['mut']};background:transparent;")
        self._rlabels.append(tlab)
        self.theme_combo = QtWidgets.QComboBox()
        for name in THEMES:
            self.theme_combo.addItem(name)
        self.theme_combo.setCurrentText(conf.get("theme", "Tokyo Night"))
        self.theme_combo.currentTextChanged.connect(self._apply_theme)
        trow.addWidget(tlab)
        trow.addWidget(self.theme_combo, 1)
        rootv.addLayout(trow)

        cols = QtWidgets.QHBoxLayout()
        cols.setSpacing(14)
        self._colL = QtWidgets.QVBoxLayout(); self._colL.setSpacing(8)
        self._colR = QtWidgets.QVBoxLayout(); self._colR.setSpacing(8)
        cols.addLayout(self._colL, 1)
        cols.addLayout(self._colR, 1)
        rootv.addLayout(cols)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        def _area_lbl(v):
            return {25: "1/4 屏", 50: "半屏", 75: "3/4 屏", 100: "满屏"}.get(v, f"{v}%")

        def _speed_lbl(v):
            s = v / 10
            return "慢" if s < 2 else ("快" if s > 4.5 else "适中")

        def _flow_lbl(v):
            return "密" if v < 700 else ("疏" if v > 1700 else "适中")

        # ============ 左栏:弹幕样式 ============
        g = self._card("弹幕样式", self._colL)
        self.area = self._slider_row(g, "显示区域", 25, 100, float(conf["top_fraction"]) * 100, _area_lbl)
        self._area_prev = AreaPreview(self, float(conf["top_fraction"]))
        self._previews.append(self._area_prev)
        self.area.valueChanged.connect(lambda v: self._area_prev.setFraction(v / 100.0))
        self._row(g, "区域预览", self._area_prev)
        self.opacity = self._slider_row(g, "不透明度", 20, 100, float(conf["opacity"]) * 100, lambda v: f"{v}%")
        self.size = self._slider_row(g, "字号", 12, 80, int(conf["font_size"]), lambda v: f"{v}px")
        self.speed = self._slider_row(g, "速度", 5, 90, float(conf["speed"]) * 10, _speed_lbl)
        self.outline = self._slider_row(g, "描边", 0, 6, int(conf.get("outline", 2)),
                                        lambda v: "无" if v == 0 else f"{v}px")

        self.color = conf["font_color"]
        self.color_btn = QtWidgets.QPushButton(self.color)
        self.color_btn.setFixedWidth(96)
        self.color_btn.clicked.connect(self._pick)
        cw, self.colorful = self._toggle_cell("彩色弹幕", conf.get("colorful", False))
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
        g = self._card("显示与密度", self._colL)
        self.display = QtWidgets.QComboBox()
        self.display.addItem("此屏(主)", "primary")
        self.display.addItem("横贯全部(接力/双屏)", "span")
        for s in QtWidgets.QApplication.screens():
            self.display.addItem(f"屏:{s.name()}", s.name())
        _i = self.display.findData(conf.get("display", "primary"))
        self.display.setCurrentIndex(_i if _i >= 0 else 0)
        self._row(g, "显示器", self.display)

        self.interval = self._slider_row(g, "出现节奏", 200, 3000, int(conf["spawn_interval_ms"]), _flow_lbl)
        self.maxon = self._slider_row(g, "同屏上限", 10, 200, int(conf["max_on_screen"]), lambda v: f"{v}条")

        tg = QtWidgets.QGridLayout()
        tg.setHorizontalSpacing(24)
        tg.setVerticalSpacing(7)
        w1, self.hover_pause = self._toggle_cell("悬停暂停", conf.get("hover_pause", True))
        w2, self.show_tag = self._toggle_cell("来源标签", conf.get("show_source_tag", True))
        w3, self.show_time = self._toggle_cell("显示时间", conf.get("show_time", True))
        w4, self.adaptive = self._toggle_cell("长弹幕加速", conf.get("adaptive_speed", False))
        w5, self.autostart_sw = self._toggle_cell("开机自启", autostart.is_enabled())
        w6, self.fs_pause_sw = self._toggle_cell("全屏暂停", conf.get("pause_on_fullscreen", True))
        for i, w in enumerate((w1, w2, w3, w4, w5, w6)):
            tg.addWidget(w, i // 2, i % 2)
        self._row(g, "", tg)

        # ============ 右栏:过滤 ============
        g = self._card("过滤", self._colR)
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
        self._row(g, "白名单", wl)

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
        g = self._card("新闻源", self._colR)
        self.rsshub = QtWidgets.QLineEdit(conf.get("rsshub_base", ""))
        self.rsshub.setPlaceholderText("自建 RSSHub 基址;feeds 里 {rsshub} 会替换成它")
        self._row(g, "RSSHub", self.rsshub)
        self.poll = self._slider_row(g, "轮询", 15, 600, int(conf["poll_interval"]), lambda v: f"{v}s")

        prow = QtWidgets.QHBoxLayout()
        plab = QtWidgets.QLabel("预设")
        plab.setStyleSheet(f"color:{self._t['mut']};background:transparent;")
        self._rlabels.append(plab)
        prow.addWidget(plab)
        for name in cfg.PRESETS:
            b = QtWidgets.QPushButton(name)
            b.clicked.connect(lambda _=False, n=name: self.feeds.setPlainText("\n".join(cfg.PRESETS[n])))
            prow.addWidget(b)
        prow.addStretch(1)
        self.detect_btn = QtWidgets.QPushButton("检测源")
        self.detect_btn.clicked.connect(self._detect)
        prow.addWidget(self.detect_btn)
        g.addLayout(prow)

        self.feeds = QtWidgets.QPlainTextEdit("\n".join(conf["feeds"]))
        self.feeds.setMinimumHeight(58)
        self.feeds.setPlaceholderText("每行一个 RSS 链接;实时源见「实时·RSSHub」预设")
        self._row(g, "链接", self.feeds)

        self.probe_box = QtWidgets.QPlainTextEdit()
        self.probe_box.setReadOnly(True)
        self.probe_box.setMaximumHeight(52)
        self.probe_box.setPlaceholderText("点「检测源」逐条测试(✓ 可用 / ✗ 不可用)")
        self.probe_sig.connect(self.probe_box.appendPlainText)
        self.probe_done.connect(lambda: self.detect_btn.setEnabled(True))
        self._row(g, "检测", self.probe_box)

        self._colL.addStretch(1)
        self._colR.addStretch(1)

        self.saved_lbl = QtWidgets.QLabel("")
        self.saved_lbl.setStyleSheet("color:#7DE38B;background:transparent;")
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Close)
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Save).clicked.connect(self._save)
        btns.rejected.connect(self.close)
        bottom = QtWidgets.QHBoxLayout()
        bottom.setContentsMargins(18, 6, 18, 14)
        bottom.addWidget(self.saved_lbl)
        bottom.addStretch(1)
        bottom.addWidget(btns)
        outer.addLayout(bottom)

    # ---------- 主题 ----------
    def _qss(self, t):
        return (
            f"QDialog,QScrollArea,QScrollArea>QWidget>QWidget{{background:{t['bg']};}}"
            f"QLabel{{color:{t['tx']};background:transparent;}}"
            "QScrollArea{border:none;}"
            f"QFrame#card{{background:{t['card']};border:1px solid {t['bd']};border-radius:10px;}}"
            f"QSpinBox,QPlainTextEdit,QLineEdit,QFontComboBox,QComboBox{{background:{t['inp']};"
            f"color:{t['tx']};border:1px solid {t['bd']};border-radius:6px;padding:5px 8px;}}"
            f"QLineEdit:focus,QComboBox:focus,QPlainTextEdit:focus{{border-color:{t['fill']};}}"
            f"QComboBox::drop-down{{border:none;}}QComboBox QAbstractItemView{{background:{t['inp']};"
            f"color:{t['tx']};selection-background-color:{t['bd']};}}"
            f"QPushButton{{background:{t['card']};color:{t['tx']};border:1px solid {t['bd']};"
            f"border-radius:7px;padding:6px 12px;}}QPushButton:hover{{border-color:{t['fill']};}}"
            f"QSlider::groove:horizontal{{height:5px;background:{t['bd']};border-radius:3px;}}"
            f"QSlider::sub-page:horizontal{{background:{t['fill']};border-radius:3px;}}"
            f"QSlider::handle:horizontal{{width:15px;height:15px;margin:-5px 0;background:{t['hd']};"
            f"border:2px solid {t['fill']};border-radius:8px;}}"
        )

    def _apply_theme(self, name):
        self._t = THEMES.get(name, self._t)
        t = self._t
        self.setStyleSheet(self._qss(t))
        self._scroll.viewport().setStyleSheet(f"background:{t['bg']};")
        self._content.setStyleSheet(f"background:{t['bg']};")
        for lb in self._titles:
            lb.setStyleSheet(f"color:{t['ti']};font-weight:600;letter-spacing:2px;padding:5px 2px 2px;background:transparent;")
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

    # ---------- 布局小工具 ----------
    def _card(self, title, col):
        lab = QtWidgets.QLabel("▎ " + title)
        lab.setStyleSheet(f"color:{self._t['ti']};font-weight:600;letter-spacing:2px;padding:5px 2px 2px;background:transparent;")
        self._titles.append(lab)
        col.addWidget(lab)
        frame = QtWidgets.QFrame()
        frame.setObjectName("card")
        v = QtWidgets.QVBoxLayout(frame)
        v.setContentsMargins(14, 9, 14, 10)
        v.setSpacing(7)
        col.addWidget(frame)
        return v

    def _row(self, card, label, widget_or_layout):
        row = QtWidgets.QHBoxLayout()
        lab = QtWidgets.QLabel(label)
        lab.setFixedWidth(self.LBL_W)
        lab.setStyleSheet(f"color:{self._t['mut']};background:transparent;")
        lab.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self._rlabels.append(lab)
        row.addWidget(lab)
        if isinstance(widget_or_layout, QtWidgets.QLayout):
            row.addLayout(widget_or_layout, 1)
        else:
            row.addWidget(widget_or_layout, 1)
        card.addLayout(row)

    def _slider_row(self, card, label, lo, hi, val, fmt):
        s = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
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

    def _toggle_cell(self, label, checked):
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        if label:
            lab = QtWidgets.QLabel(label)
            lab.setStyleSheet(f"color:{self._t['tx']};background:transparent;")
            self._tlabels.append(lab)
            h.addWidget(lab)
        h.addStretch(1)
        sw = ToggleSwitch(checked, self)
        self._toggles.append(sw)
        h.addWidget(sw)
        return w, sw

    def _detect(self):
        base = self.rsshub.text().strip()
        urls = [ln.strip() for ln in self.feeds.toPlainText().splitlines() if ln.strip()]
        self.probe_box.clear()
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

    def _save(self):
        c = self._c
        c["font_size"] = self.size.value()
        c["font_color"] = self.color
        c["font_family"] = self.family.currentText()
        c["speed"] = round(self.speed.value() / 10, 1)
        c["opacity"] = round(self.opacity.value() / 100, 2)
        c["top_fraction"] = round(self.area.value() / 100, 2)
        c["top_offset"] = 0.03
        c["colorful"] = self.colorful.isChecked()
        c["outline"] = self.outline.value()
        c["adaptive_speed"] = self.adaptive.isChecked()
        c["pause_on_fullscreen"] = self.fs_pause_sw.isChecked()
        c["autostart"] = self.autostart_sw.isChecked()
        autostart.set_autostart(c["autostart"])         # 立即注册/注销登录启动项
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
        c["feeds"] = [ln.strip() for ln in self.feeds.toPlainText().splitlines() if ln.strip()]
        self.applied.emit()
        self.saved_lbl.setText("已保存 ✓")
        QtCore.QTimer.singleShot(1800, lambda: self.saved_lbl.setText(""))

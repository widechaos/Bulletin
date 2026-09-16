"""DangmuNews —— 桌面弹幕新闻(v2)

RSS/RSSHub 新闻源 → 弹幕直接飞在桌面上(全透明、不挡其它程序操作),
左键弹幕打开原文,右键弹幕或系统托盘图标开菜单/设置。
"""
import queue
import sys

from PySide6 import QtWidgets

import config as cfg
from overlay import OverlayWindow
from sources import RSSPoller


def _macos_accessory_mode():
    """macOS:把 app 设为配件模式(菜单栏托盘应用),不在 Dock 显示 Python 图标。"""
    if sys.platform != "darwin":
        return
    try:
        import ctypes

        objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.dylib")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        msg = objc.objc_msgSend
        cls = objc.objc_getClass(b"NSApplication")
        msg.restype = ctypes.c_void_p
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        app = msg(cls, objc.sel_registerName(b"sharedApplication"))
        msg.restype = ctypes.c_bool
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        msg(app, objc.sel_registerName(b"setActivationPolicy:"), 1)  # 1 = Accessory
    except Exception as e:  # 失败不影响运行,只是 Dock 会有图标
        print("[macos] 隐藏 Dock 图标失败(不影响运行):", e)


def main():
    conf = cfg.load()

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("DangmuNews")
    app.setQuitOnLastWindowClosed(False)   # 无主窗口,退出走托盘菜单
    _macos_accessory_mode()

    item_q: "queue.Queue" = queue.Queue()
    poller = RSSPoller(conf, item_q)   # 实时流:启动只放最新几条打底,之后只放新条
    poller.start()

    overlay = OverlayWindow(conf, item_q)  # noqa: F841  (持有引用,勿被 GC)

    exit_code = app.exec()
    poller.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

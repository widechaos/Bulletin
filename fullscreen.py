"""检测前台是否存在全屏窗口(用于自动暂停弹幕)。
纯 ctypes,无第三方依赖;任何检测失败一律返回 False,绝不误暂停。
"""
import ctypes
import sys


def _mac_fullscreen() -> bool:
    try:
        CG = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        CF = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

        class CGPoint(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

        class CGSize(ctypes.Structure):
            _fields_ = [("w", ctypes.c_double), ("h", ctypes.c_double)]

        class CGRect(ctypes.Structure):
            _fields_ = [("origin", CGPoint), ("size", CGSize)]

        CG.CGMainDisplayID.restype = ctypes.c_uint32
        CG.CGDisplayBounds.restype = CGRect
        CG.CGDisplayBounds.argtypes = [ctypes.c_uint32]
        disp = CG.CGDisplayBounds(CG.CGMainDisplayID())
        DW, DH = disp.size.w, disp.size.h

        CG.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
        CG.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        arr = CG.CGWindowListCopyWindowInfo(1, 0)   # kCGWindowListOptionOnScreenOnly, null window
        if not arr:
            return False

        CF.CFArrayGetCount.restype = ctypes.c_long
        CF.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        CF.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        CF.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        CF.CFDictionaryGetValue.restype = ctypes.c_void_p
        CF.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        CF.CFNumberGetValue.restype = ctypes.c_bool
        CF.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
        CF.CFRelease.argtypes = [ctypes.c_void_p]

        k_layer = ctypes.c_void_p.in_dll(CG, "kCGWindowLayer")
        k_bounds = ctypes.c_void_p.in_dll(CG, "kCGWindowBounds")
        CG.CGRectMakeWithDictionaryRepresentation.restype = ctypes.c_bool
        CG.CGRectMakeWithDictionaryRepresentation.argtypes = [ctypes.c_void_p, ctypes.POINTER(CGRect)]

        found = False
        for i in range(CF.CFArrayGetCount(arr)):
            d = CF.CFArrayGetValueAtIndex(arr, i)
            if not d:
                continue
            lp = CF.CFDictionaryGetValue(d, k_layer)
            layer = ctypes.c_int(0)
            if lp:
                CF.CFNumberGetValue(lp, 9, ctypes.byref(layer))   # kCFNumberIntType
            if layer.value != 0:            # 只看普通应用窗口(排除桌面/Dock/壁纸)
                continue
            bp = CF.CFDictionaryGetValue(d, k_bounds)
            if not bp:
                continue
            rect = CGRect()
            if not CG.CGRectMakeWithDictionaryRepresentation(bp, ctypes.byref(rect)):
                continue
            # 必须"正好铺满主屏"才算全屏:排除外接屏上的大窗口(坐标/尺寸都不等于主屏)、
            # 以及主屏上留着菜单栏的普通最大化窗口(其 y≈菜单栏高度,而非 0)。
            if (abs(rect.origin.x - disp.origin.x) <= 2 and abs(rect.origin.y - disp.origin.y) <= 2
                    and abs(rect.size.w - DW) <= 2 and abs(rect.size.h - DH) <= 2):
                found = True
                break
        CF.CFRelease(arr)
        return found
    except Exception:
        return False


def _win_fullscreen() -> bool:
    try:
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd or hwnd == u.GetShellWindow() or hwnd == u.GetDesktopWindow():
            return False

        class RECT(ctypes.Structure):
            _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                        ("r", ctypes.c_long), ("b", ctypes.c_long)]

        rect = RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rect))
        sw = u.GetSystemMetrics(0)
        sh = u.GetSystemMetrics(1)
        return (rect.l <= 0 and rect.t <= 0
                and (rect.r - rect.l) >= sw and (rect.b - rect.t) >= sh)
    except Exception:
        return False


def foreground_is_fullscreen() -> bool:
    if sys.platform == "darwin":
        return _mac_fullscreen()
    if sys.platform.startswith("win"):
        return _win_fullscreen()
    return False   # Linux 暂不检测(窗口系统差异大),feature 静默失效

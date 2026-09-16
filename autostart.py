"""开机自启:各平台注册/注销登录启动项。
mac→LaunchAgent plist;win→HKCU Run;linux→~/.config/autostart/*.desktop。
"""
import os
import plistlib
import sys

APP = "Bulletin"
BUNDLE_ID = "cn.widechaos.bulletin"


def _launch_cmd():
    """启动该程序的命令 argv。打包版=app 可执行档;源码=python main.py。"""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return [sys.executable, main_py]


# ---------- macOS ----------
def _mac_plist():
    return os.path.expanduser(f"~/Library/LaunchAgents/{BUNDLE_ID}.plist")


def _mac_set(enabled):
    p = _mac_plist()
    if enabled:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            plistlib.dump({"Label": BUNDLE_ID, "ProgramArguments": _launch_cmd(),
                           "RunAtLoad": True, "KeepAlive": False}, f)
    elif os.path.exists(p):
        os.remove(p)


def _mac_enabled():
    return os.path.exists(_mac_plist())


# ---------- Windows ----------
def _win_set(enabled):
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
    try:
        if enabled:
            cmd = " ".join(f'"{c}"' for c in _launch_cmd())
            winreg.SetValueEx(key, APP, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, APP)
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)


def _win_enabled():
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run")
        try:
            winreg.QueryValueEx(key, APP)
            return True
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except OSError:
        return False


# ---------- Linux ----------
def _linux_path():
    base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(base, "autostart", f"{APP}.desktop")


def _linux_set(enabled):
    p = _linux_path()
    if enabled:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        cmd = " ".join(_launch_cmd())
        with open(p, "w") as f:
            f.write(f"[Desktop Entry]\nType=Application\nName={APP}\nExec={cmd}\n"
                    "X-GNOME-Autostart-enabled=true\n")
    elif os.path.exists(p):
        os.remove(p)


def _linux_enabled():
    return os.path.exists(_linux_path())


def set_autostart(enabled: bool) -> bool:
    try:
        if sys.platform == "darwin":
            _mac_set(enabled)
        elif sys.platform.startswith("win"):
            _win_set(enabled)
        else:
            _linux_set(enabled)
        return True
    except Exception as e:
        print("[autostart] 设置失败:", e)
        return False


def is_enabled() -> bool:
    try:
        if sys.platform == "darwin":
            return _mac_enabled()
        if sys.platform.startswith("win"):
            return _win_enabled()
        return _linux_enabled()
    except Exception:
        return False

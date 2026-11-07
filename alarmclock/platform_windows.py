"""Windows integration, kept separate from portable application logic."""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path

APP_NAME = "CustomAlarmClock"


def show_corner_window(hwnd: int, always_on_top: bool):
    """Present Qt's existing overlay HWND without activating it or changing its geometry."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    position = user32.SetWindowPos
    position.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                        ctypes.c_int, ctypes.c_int, wintypes.UINT]
    position.restype = wintypes.BOOL
    visible = user32.IsWindowVisible
    visible.argtypes = [wintypes.HWND]
    visible.restype = wintypes.BOOL
    # Qt owns placement/DPI. Only show the window and set its Z-order here.
    # NOMOVE | NOSIZE | NOACTIVATE | SHOWWINDOW | NOOWNERZORDER
    flags = 0x0002 | 0x0001 | 0x0010 | 0x0040 | 0x0200
    insert_after = -1 if always_on_top else -2  # HWND_TOPMOST / HWND_NOTOPMOST
    if not position(hwnd, insert_after, 0, 0, 0, 0, flags):
        raise ctypes.WinError(ctypes.get_last_error())
    if not visible(hwnd):
        raise OSError("Windows did not make the corner image window visible.")


def data_directory() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return root / APP_NAME


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        args = [sys.executable, "--minimized"]
    else:
        executable = Path(sys.executable)
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.is_file():
            executable = pythonw
        args = [str(executable), str(Path(__file__).resolve().parent.parent / "main.py"), "--minimized"]
    return subprocess.list2cmdline(args)


def startup_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except FileNotFoundError:
        return False


def set_startup(enabled: bool):
    if sys.platform != "win32":
        if enabled:
            raise OSError("Start with Windows is available on Windows only.")
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass

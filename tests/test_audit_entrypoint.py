"""Real source launch and safe, mocked Windows integration checks."""
import os
import errno
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
import pytest


from alarmclock import platform_windows


def test_source_launch_and_clean_exit(tmp_path):
    main = Path(__file__).resolve().parents[1] / "main.py"
    profile = tmp_path / "Smoke 日本語 with spaces"
    result = subprocess.run([sys.executable, str(main), "--data-dir", str(profile), "--smoke-test"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert not (profile / "instance.lock").exists()
    with sqlite3.connect(profile / "alarms.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM alarms").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 0


def test_source_single_instance_restore_and_clean_exit(tmp_path):
    main = Path(__file__).resolve().parents[1] / "main.py"
    profile = tmp_path / "Profile 日本語 with spaces"
    command = [sys.executable, str(main), "--data-dir", str(profile), "--smoke-test"]
    # Supply a writable, short IPC path on hosts without /tmp. Qt's Unix sockets
    # also have a platform path-length limit. Windows uses named pipes instead.
    ipc = tempfile.TemporaryDirectory(prefix="ipc-", dir=main.parents[2])
    if sys.platform != "win32":
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.bind(str(Path(ipc.name) / "probe"))
        except OSError as exc:
            ipc.cleanup()
            if exc.errno in {errno.EPERM, errno.EACCES, errno.EAFNOSUPPORT, errno.ENOSYS}:
                pytest.skip(f"Host prohibits local IPC sockets: {exc}")
            raise
    env = {**os.environ, "TMPDIR": ipc.name, "TEMP": ipc.name}
    first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        deadline = time.monotonic() + 10
        while not (profile / "instance.lock").exists() and time.monotonic() < deadline and first.poll() is None:
            time.sleep(.01)
        assert (profile / "instance.lock").exists()
        second = subprocess.run(command, capture_output=True, text=True, timeout=10, env=env)
        stdout, stderr = first.communicate(timeout=10)
        assert first.returncode == 0, stderr
        assert second.returncode == 0, second.stderr
        assert not (profile / "instance.lock").exists()
        with sqlite3.connect(profile / "alarms.sqlite3") as connection:
            assert connection.execute("SELECT count(*) FROM alarms").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 0
        assert "Unhandled exception" not in (profile / "application.log").read_text()
    finally:
        if first.poll() is None:
            first.kill(); first.communicate(timeout=5)
        ipc.cleanup()


def test_instance_lock_rejects_a_second_owner(tmp_path):
    from PySide6.QtCore import QLockFile
    path = str(tmp_path / "instance.lock")
    first, second = QLockFile(path), QLockFile(path)
    first.setStaleLockTime(0); second.setStaleLockTime(0)
    assert first.tryLock(100)
    try:
        assert not second.tryLock(100)
    finally:
        first.unlock()
    assert second.tryLock(100)
    second.unlock()


def test_windows_data_path_and_frozen_startup_quoting(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_windows.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "User 日本語"))
    assert platform_windows.data_directory() == tmp_path / "User 日本語" / "CustomAlarmClock"
    monkeypatch.setattr(platform_windows.sys, "frozen", True, raising=False)
    monkeypatch.setattr(platform_windows.sys, "executable", "C:\\Program Files\\日本語\\CustomAlarmClock.exe")
    assert platform_windows.startup_command() == '"C:\\Program Files\\日本語\\CustomAlarmClock.exe" --minimized'


def test_windows_startup_registration_uses_current_user_only(monkeypatch):
    from types import SimpleNamespace
    writes, deleted = [], []
    class Key:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def create(hive, path):
        assert hive == "current-user" and path.endswith("\\Run")
        return Key()
    fake = SimpleNamespace(HKEY_CURRENT_USER="current-user", REG_SZ=1,
                           CreateKey=create, OpenKey=create,
                           SetValueEx=lambda *args: writes.append(args),
                           DeleteValue=lambda *args: deleted.append(args),
                           QueryValueEx=lambda *args: ("registered command", 1))
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(platform_windows.sys, "platform", "win32")
    monkeypatch.setattr(platform_windows, "startup_command", lambda: "quoted startup command")
    assert platform_windows.startup_enabled()
    platform_windows.set_startup(True); platform_windows.set_startup(False)
    assert writes[0][1:] == ("CustomAlarmClock", 0, 1, "quoted startup command")
    assert deleted[0][1] == "CustomAlarmClock"


def test_build_script_keeps_test_gate_and_verifies_executable_launch():
    root = Path(__file__).resolve().parents[1]
    build = (root / "build.bat").read_text()
    assert build.index("-m pytest -q") < build.index("-m PyInstaller") < build.index("smoke_executable.py") < build.index("SUCCESS:")
    assert 'scripts\\check_runtime.py --windows' in build
    assert build.count("if errorlevel 1 goto :fail") >= 8

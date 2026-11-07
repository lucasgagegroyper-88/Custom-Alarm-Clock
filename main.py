"""Windows desktop entry point. Also runnable on Linux for development tests."""
from __future__ import annotations
import argparse
import hashlib
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication
from alarmclock.controller import Controller
from alarmclock.platform_windows import data_directory
from alarmclock.storage import Repository
from alarmclock.ui.common import ErrorDialog, application_icon
from alarmclock.ui.dashboard import Dashboard
from alarmclock.ui.theme import apply_theme


def main(argv=None):
    parser = argparse.ArgumentParser(description="Custom Alarm Clock")
    parser.add_argument("--minimized", action="store_true")
    parser.add_argument("--data-dir", type=Path, help="Use a separate settings directory")
    parser.add_argument("--smoke-test", action="store_true", help="Open an empty GUI briefly and exit; no media")
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Custom Alarm Clock")
    app.setOrganizationName("CustomAlarmClock")
    app.setWindowIcon(application_icon())
    app.setQuitOnLastWindowClosed(False)
    location = (args.data_dir or data_directory()).resolve()
    location.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(location / "application.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s")
    name = "CustomAlarmClock-" + hashlib.sha256(str(location).encode()).hexdigest()[:20]
    lock = QLockFile(str(location / "instance.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        socket = QLocalSocket()
        socket.connectToServer(name)
        if socket.waitForConnected(1500):
            socket.write(b"show")
            socket.waitForBytesWritten(1000)
            socket.disconnectFromServer()
            return 0
        error = ErrorDialog("Custom Alarm Clock", "Another instance is using these settings. Close it before opening another instance.")
        error.exec()
        return 1
    try:
        repo = Repository(location / "alarms.sqlite3")
    except Exception as exc:
        logging.exception("Cannot open database")
        error = ErrorDialog("Cannot open alarm settings", str(exc))
        error.exec()
        lock.unlock()
        return 1
    apply_theme(app, repo.get("theme", "dark"))
    controller = Controller(repo)
    window = Dashboard(controller, location)
    server = QLocalServer(app)
    QLocalServer.removeServer(name)
    if not server.listen(name):
        logging.warning("Single-instance restore channel unavailable: %s", server.errorString())

    def on_connection():
        connection = server.nextPendingConnection()
        if connection:
            connection.disconnectFromServer()
            connection.deleteLater()
        window.restore_window()
    server.newConnection.connect(on_connection)

    def exception_hook(kind, value, trace):
        logging.error("Unhandled exception", exc_info=(kind, value, trace))
        window.show_error(f"{kind.__name__}: {value}\nDetails are saved to application.log.")
    sys.excepthook = exception_hook
    app.aboutToQuit.connect(controller.shutdown)
    if not (args.minimized or repo.get("start_minimized", False)) or not window.tray:
        window.show()
    if args.smoke_test:
        QTimer.singleShot(1000, window.exit_app)
    else:
        QTimer.singleShot(0, controller.start)
    code = app.exec()
    server.close()
    controller.shutdown()
    repo.close()
    lock.unlock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())

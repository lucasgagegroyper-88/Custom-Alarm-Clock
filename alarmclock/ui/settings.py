import sys
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QDialogButtonBox, QLabel, QWidget)
from .common import check, spin, combo, note
from ..platform_windows import startup_enabled, set_startup


class SettingsDialog(QDialog):
    def __init__(self, repo, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.setWindowTitle("General settings")
        self.resize(640, 440)
        root = QVBoxLayout(self)
        general = QWidget()
        general_layout = QVBoxLayout(general)
        form = QFormLayout()
        form.setVerticalSpacing(16)
        general_layout.addLayout(form)
        self.theme = combo([("dark", "Dark"), ("light", "Light")], repo.get("theme", "dark"))
        self.close_tray = check("Close to tray", repo.get("close_to_tray", True))
        self.minimize_tray = check("Minimize to tray", repo.get("minimize_to_tray", True))
        self.start_minimized = check("Open in the tray", repo.get("start_minimized", False))
        self.startup = check("Start with Windows", startup_enabled())
        self.startup.setEnabled(sys.platform == "win32")
        self.missed = combo([("latest", "Ring the latest missed occurrence"), ("skip", "Skip missed occurrences")], repo.get("missed_policy", "latest"))
        self.grace = spin(repo.get("missed_grace_seconds", 86400) // 60, 1, 43200, " minutes")
        self.restore = check("Restore on restart", repo.get("resume_active", True))
        for label, widget in [("Appearance", self.theme), ("Closing", self.close_tray), ("Minimizing", self.minimize_tray), ("Startup window", self.start_minimized), ("Windows sign-in", self.startup), ("Sleep / missed alarms", self.missed), ("Catch-up lookback", self.grace), ("Active alarms", self.restore)]:
            form.addRow(label, widget)
        general_layout.addWidget(note("Alarms run while the app is open. Missed alarms follow the policy above after sleep.\nAn infinite playlist holds the audio queue until stopped."))
        general_layout.addStretch()
        root.addWidget(general)
        self.error = QLabel()
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        root.addWidget(self.error)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.save)
        box.rejected.connect(self.reject)
        root.addWidget(box)

    def save(self):
        try:
            if sys.platform == "win32":
                set_startup(self.startup.isChecked())
            values = {"theme": self.theme.currentData(), "close_to_tray": self.close_tray.isChecked(), "minimize_to_tray": self.minimize_tray.isChecked(), "start_minimized": self.start_minimized.isChecked(), "missed_policy": self.missed.currentData(), "missed_grace_seconds": self.grace.value()*60, "resume_active": self.restore.isChecked()}
            with self.repo.transaction():
                for key, value in values.items():
                    self.repo.set(key, value)
            self.accept()
        except OSError as exc:
            self.error.setText(f"Could not update Windows startup: {exc}")

from __future__ import annotations
from dataclasses import asdict
from datetime import datetime
import time
from PySide6.QtCore import Qt, QTimer, QEvent, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QFrame, QSplitter,
    QSystemTrayIcon, QMenu, QDialog, QDialogButtonBox, QFormLayout, QTabWidget, QPlainTextEdit)
from ..models import UTC
from ..scheduling import next_occurrence, describe
from .common import note, application_icon, ErrorDialog, DurationControl
from .banner import BannerBackground
from .corner_overlay import CornerOverlay
from .editor import AlarmEditor
from .settings import SettingsDialog
from .theme import apply_theme, translucent_surfaces


class Dashboard(QMainWindow):
    def __init__(self, controller, data_dir):
        super().__init__()
        self.controller, self.repo, self.data_dir = controller, controller.repo, data_dir
        self._quitting = False
        self._refreshing = False
        self.dialogs = []
        self.setWindowTitle("Custom Alarm Clock")
        self.resize(1180, 830)
        self.setMinimumSize(860, 620)
        self.setWindowIcon(application_icon())
        # The central widget paints the selected alarm's existing banner behind all dashboard content.
        central = self.background = BannerBackground()
        self._bg_signature = None
        self._overlay_on = None
        central.error.connect(controller.report)
        root = QVBoxLayout(central)
        root.setContentsMargins(26, 20, 26, 20)
        root.setSpacing(16)
        self.setCentralWidget(central)
        heading = QHBoxLayout()
        title_block = QVBoxLayout()
        title = QLabel("Custom Alarm Clock")
        title.setObjectName("title")
        title_block.addWidget(title)
        heading.addLayout(title_block)
        heading.addStretch()
        self.clock = QLabel()
        self.clock.setObjectName("clock")
        self.date_label = note("")
        self.date_label.setWordWrap(False)
        clock_block = QVBoxLayout()
        clock_block.addWidget(self.clock, alignment=Qt.AlignmentFlag.AlignRight)
        clock_block.addWidget(self.date_label, alignment=Qt.AlignmentFlag.AlignRight)
        heading.addLayout(clock_block)
        root.addLayout(heading)
        card = self.card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 14, 18, 14)
        card_layout.addWidget(note("NEXT ALARM"))
        self.next_label = QLabel("No enabled alarms")
        self.next_label.setObjectName("section")
        card_layout.addWidget(self.next_label)
        root.addWidget(card)
        # Two explicit rows (never one crowded row): primary actions, then secondary actions.
        def make_button(text, callback, name=None, min_width=0, min_height=0):
            button = QPushButton(text)
            button.clicked.connect(callback)
            if name:
                button.setObjectName(name)
            if min_width:
                button.setMinimumWidth(min_width)
            if min_height:
                button.setMinimumHeight(min_height)
            return button

        primary_row = QHBoxLayout()
        primary_row.setSpacing(10)
        self.add_button = make_button("+ Add Alarm", self.add_alarm, "primary", 160, 38)
        self.edit_button = make_button("Edit Alarm", self.edit_alarm, None, 160, 38)
        for button in (self.add_button, self.edit_button):
            primary_row.addWidget(button)
        primary_row.addStretch()
        root.addLayout(primary_row)
        secondary_row = QHBoxLayout()
        secondary_row.setSpacing(10)
        for text, callback in [("Duplicate", self.duplicate_alarm), ("Delete", self.delete_alarm), ("Trigger Now", self.trigger_now), ("Settings", self.settings)]:
            secondary_row.addWidget(make_button(text, callback, None, 0, 34))
        secondary_row.addStretch()
        root.addLayout(secondary_row)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.alarms = self._table(["On", "Alarm", "Schedule", "Next activation", "Media"])
        self.alarms.itemChanged.connect(self._check_toggled)
        self.alarms.cellDoubleClicked.connect(lambda *_: self.edit_alarm())
        self.alarms.itemSelectionChanged.connect(self._sync_background)
        self.alarms.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        splitter.addWidget(self.alarms)
        self.tabs = QTabWidget()
        live = QWidget()
        live_layout = QVBoxLayout(live)
        self.active = self._table(["Active occurrence", "State / audio", "Next re-alert / snooze", "Re-alerts used"])
        live_layout.addWidget(self.active)
        actions = QHBoxLayout()
        for text, callback in [("Pause", lambda: self._active_action("pause")), ("Resume", lambda: self._active_action("resume")), ("Stop Sound", lambda: self._active_action("stop_sound")), ("Snooze…", self.snooze), ("Dismiss Current", lambda: self._active_action("dismiss")), ("Close Banner", lambda: self._active_action("close_banner")), ("Show Banner", lambda: self._active_action("show_banner"))]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button)
        live_layout.addLayout(actions)
        self.tabs.addTab(live, "Active alarms")
        self.history = self._table(["Alarm", "Scheduled", "State", "Additional alerts"])
        self.tabs.addTab(self.history, "History")
        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        open_data = QPushButton("Open settings & logs folder")
        open_data.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.data_dir))))
        log_layout.addWidget(open_data)
        self.tabs.addTab(log_page, "Event log")
        splitter.addWidget(self.tabs)
        splitter.setSizes([300, 270])
        root.addWidget(splitter, 1)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setObjectName("error")
        self.error_label.hide()
        root.addWidget(self.error_label)
        self.statusBar().showMessage("Ready")
        self._setup_tray()
        controller.changed.connect(self.refresh)
        controller.error.connect(self.show_error)
        controller.activated.connect(self._activated)
        controller.notice.connect(lambda m: self.statusBar().showMessage(m, 6000))
        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(1000)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(5000)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()
        QShortcut(QKeySequence("Ctrl+N"), self, self.add_alarm)
        QShortcut(QKeySequence("Ctrl+E"), self, self.edit_alarm)
        # Independent bottom-right corner image window; it keeps running while the dashboard is in the tray.
        self.corner_overlay = CornerOverlay(self.repo)
        self.corner_overlay.error.connect(self._corner_error)
        self.corner_overlay.notice.connect(lambda m: self.statusBar().showMessage(m, 12000))
        QApplication.instance().aboutToQuit.connect(self.corner_overlay.shutdown)
        # The overlay opens ONLY from genuine alarm activations delivered here (never from start-up, settings or refresh).
        self.controller.corner_events.connect(self.corner_overlay.handle_events)
        self.corner_overlay.start()
        self.refresh()

    def _table(self, columns):
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(44)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        return table

    def _selected(self, table):
        row_index = table.currentRow()
        item = table.item(row_index, 0) if row_index >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _fill(self, table, rows):
        selected = self._selected(table)
        table.blockSignals(True)
        table.setRowCount(len(rows))
        for index, (key, values) in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, key)
                item.setToolTip(str(value))
                table.setItem(index, column, item)
            if key == selected:
                table.setCurrentCell(index, 0)  # keep the same record selected across refreshes
        if selected is None and rows:
            table.selectRow(0)
        table.blockSignals(False)

    def refresh(self):
        if self._quitting or self._refreshing:
            return
        self._refreshing = True
        try:
            now = datetime.now(UTC)
            rows = []
            alarms = self.repo.alarms()
            for a in alarms:
                upcoming = next_occurrence(a.schedule, now, self.controller.engine.zone) if a.enabled else None
                media = ("Image / GIF" if a.banner.path else "No image") + f" · {len(a.audio.tracks)} tracks"
                rows.append((a.id, ["", a.name, describe(a.schedule), upcoming.astimezone(self.controller.engine.zone).strftime("%a %d %b · %H:%M:%S") if upcoming else ("Disabled" if not a.enabled else "No future occurrence"), media]))
            self._fill(self.alarms, rows)
            self.alarms.blockSignals(True)
            for i, a in enumerate(alarms):
                item = self.alarms.item(i, 0)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if a.enabled else Qt.CheckState.Unchecked)
            self.alarms.blockSignals(False)
            active = self.repo.active()
            active_rows = []
            for o in active:
                next_at = o.snooze_until if o.status == "snoozed" else o.next_alert
                status = "Snoozed" if o.status == "snoozed" else self.controller.audio.describe(o.id)
                count = "∞" if o.snapshot.realert.count is None else str(o.snapshot.realert.count)
                active_rows.append((o.id, [o.snapshot.name, status, datetime.fromtimestamp(next_at).strftime("%a %H:%M:%S") if next_at else "No pending re-alert", f"{o.additional_done} / {count}"]))
            self._fill(self.active, active_rows)
            self.tabs.setTabText(0, f"Active alarms ({len(active)})")
            self._fill(self.history, [(o.id, [o.snapshot.name, datetime.fromtimestamp(o.scheduled_at).strftime("%Y-%m-%d %H:%M:%S"), o.status.title(), o.additional_done]) for o in self.repo.history()])
            self.log_text.setPlainText("\n\n".join(datetime.fromtimestamp(r[0]).strftime("%Y-%m-%d %H:%M:%S") + "  " + r[1] for r in self.repo.events()))
            self.update_clock()
            self._sync_background()
        finally:
            self._refreshing = False

    def _sync_background(self):
        """Show the selected alarm's configured banner as the background. Reloads (and restarts a GIF)
        only when the selection or that alarm's banner settings change, never on alarm activation."""
        if self._quitting:
            return
        key = self._selected(self.alarms)
        alarm = self.repo.alarm(key) if key else None
        config = alarm.banner if alarm and alarm.banner.path else None
        signature = (alarm.id, asdict(config)) if config else None
        if signature == self._bg_signature:
            return
        self._bg_signature = signature
        message = self.background.show_banner(config)
        self._apply_overlay()
        if message:  # report the missing/undecodable file; nothing is substituted
            QTimer.singleShot(0, lambda: self.controller.report(message))

    def _apply_overlay(self, force=False):
        """Let the banner show through panels only while an image is displayed; otherwise keep the stock look."""
        on = self.background.has_image
        if on == self._overlay_on and not force:
            return
        self._overlay_on = on
        panel, field = translucent_surfaces(self.repo.get("theme", "dark"))
        styles = {
            self.card: f"QFrame#card {{ background:{panel}; }}",
            self.alarms: f"QTableWidget {{ background:{panel}; alternate-background-color:{field}; }}",
            self.active: f"QTableWidget {{ background:{panel}; alternate-background-color:{field}; }}",
            self.history: f"QTableWidget {{ background:{panel}; alternate-background-color:{field}; }}",
            self.log_text: f"QPlainTextEdit {{ background:{field}; }}",
            self.tabs: f"QTabWidget::pane {{ background:{panel}; }}",
        }
        for widget, style in styles.items():
            widget.setStyleSheet(style if on else "")

    def update_clock(self):
        now = datetime.now(UTC)
        local = now.astimezone(self.controller.engine.zone)
        self.clock.setText(local.strftime("%H:%M:%S"))
        self.date_label.setText(local.strftime("%A, %d %B %Y · %Z"))
        upcoming = []
        for a in self.repo.alarms():
            if a.enabled:
                value = next_occurrence(a.schedule, now, self.controller.engine.zone)
                if value:
                    upcoming.append((value.timestamp(), a.name))
        for o in self.repo.active():
            value = o.snooze_until if o.status == "snoozed" else o.next_alert
            if value:
                upcoming.append((value, o.snapshot.name + (" · snooze" if o.status == "snoozed" else " · re-alert")))
        if upcoming:
            at, name = min(upcoming)
            remaining = max(0, int(at - now.timestamp()))
            days, rest = divmod(remaining, 86400)
            hours, rest = divmod(rest, 3600)
            minutes, seconds = divmod(rest, 60)
            countdown = (f"{days}d " if days else "") + f"{hours:02}:{minutes:02}:{seconds:02}"
            stamp = datetime.fromtimestamp(at, self.controller.engine.zone).strftime("%a %d %b, %H:%M:%S")
            self.next_label.setText(f"{name}  ·  {stamp}  ·  in {countdown}")
        else:
            self.next_label.setText("No upcoming alarms")
        if self.tray:
            self.tray.setToolTip("Custom Alarm Clock\n" + self.next_label.text()[:110])

    def add_alarm(self):
        self._edit(None)

    def edit_alarm(self):
        key = self._selected(self.alarms)
        if key:
            self._edit(self.repo.alarm(key))

    def _edit(self, alarm):
        editor = AlarmEditor(alarm, self.controller, self)
        if editor.exec() == QDialog.DialogCode.Accepted:
            try:
                self.controller.save_alarm(editor.result_alarm)
            except (ValueError, OSError) as exc:
                self.show_error(str(exc))
        editor.deleteLater()

    def duplicate_alarm(self):
        key = self._selected(self.alarms)
        if key:
            copy = self.repo.alarm(key).clone()
            self.controller.save_alarm(copy)
            self.statusBar().showMessage("Alarm duplicated (disabled)", 6000)

    def delete_alarm(self):
        key = self._selected(self.alarms)
        if not key:
            return
        alarm = self.repo.alarm(key)
        dialog = QDialog(self)
        dialog.setWindowTitle("Delete alarm")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f'Delete “{alarm.name}”? Its active occurrence will also stop.'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        delete = buttons.addButton("Delete", QDialogButtonBox.ButtonRole.AcceptRole)
        delete.clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.controller.delete_alarm(key)
        dialog.deleteLater()

    def _check_toggled(self, item):
        if not self._refreshing and item.column() == 0:
            self.controller.toggle_alarm(item.data(Qt.ItemDataRole.UserRole))

    def trigger_now(self):
        key = self._selected(self.alarms)
        if key:
            self.controller.trigger_now(key)
            self.tabs.setCurrentIndex(0)

    def _active_action(self, action):
        key = self._selected(self.active)
        if key:
            getattr(self.controller, action)(key)
        else:
            self.statusBar().showMessage("Select an active alarm occurrence first.", 6000)

    def snooze(self):
        key = self._selected(self.active)
        occurrence = self.repo.occurrence(key) if key else None
        if not occurrence:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Snooze " + occurrence.snapshot.name)
        layout = QFormLayout(dialog)
        duration = DurationControl(occurrence.snapshot.snooze_seconds)
        layout.addRow("Snooze for", duration)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.controller.snooze(key, duration.seconds())
        dialog.deleteLater()

    def settings(self):
        dialog = SettingsDialog(self.repo, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            apply_theme(QApplication.instance(), self.repo.get("theme", "dark"))
            self._apply_overlay(force=True)
        dialog.deleteLater()

    def _setup_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray_menu = QMenu(self)
        self.tray_menu.aboutToShow.connect(self._tray_menu)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(lambda reason: self.restore_window() if reason in {QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick} else None)
        self.tray.show()

    def _tray_menu(self):
        self.tray_menu.clear()
        self.tray_menu.addAction("Open Custom Alarm Clock", self.restore_window)
        self.tray_menu.addSeparator()
        for o in self.repo.active():
            menu = self.tray_menu.addMenu(o.snapshot.name)
            for label, action in [("Stop Sound", "stop_sound"), ("Snooze", "snooze"), ("Dismiss Current Occurrence", "dismiss"), ("Close Banner", "close_banner"), ("Show Banner", "show_banner")]:
                menu.addAction(label, lambda key=o.id, method=action: getattr(self.controller, method)(key))
        if self.repo.active():
            self.tray_menu.addAction("Dismiss all active occurrences", self.controller.dismiss_all)
            self.tray_menu.addSeparator()
        self.tray_menu.addAction("Exit", self.exit_app)

    def _activated(self, name):
        self.statusBar().showMessage("Active alarm: " + name)
        # No OS notification sound is requested; audio is always user-selected.

    def show_error(self, message):
        self.error_label.setText(message)
        self.error_label.show()
        self.restore_window()
        dialog = ErrorDialog("Custom Alarm Clock · error", message, self)
        self.dialogs.append(dialog)
        dialog.finished.connect(lambda _: self._release_dialog(dialog))
        dialog.show()
        self.refresh()

    def _release_dialog(self, dialog):
        if dialog in self.dialogs:
            self.dialogs.remove(dialog)
        if not self.dialogs:
            self.error_label.hide()
        dialog.deleteLater()

    def restore_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized() and self.tray and self.repo.get("minimize_to_tray", True):
            QTimer.singleShot(0, self.hide)
        super().changeEvent(event)

    def closeEvent(self, event):
        if not self._quitting and self.tray and self.repo.get("close_to_tray", True):
            self.hide()
            event.ignore()
        else:
            self.exit_app()
            event.accept()

    def _corner_error(self, message):
        # Startup load failures must not pop the window of a tray-started app: log + status bar, and a dialog only if already visible.
        self.repo.log(time.time(), message)
        self.statusBar().showMessage(message.splitlines()[0], 15000)
        if self.isVisible():
            self.show_error(message)

    def exit_app(self):
        if self._quitting:
            return
        self._quitting = True
        self.clock_timer.stop()
        self.refresh_timer.stop()
        self.corner_overlay.shutdown()
        self.controller.shutdown()
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()

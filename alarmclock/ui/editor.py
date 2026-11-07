from __future__ import annotations
from datetime import datetime, timedelta
from pathlib import Path
from PySide6.QtCore import QDate, QTime, Qt
from PySide6.QtGui import QFont, QImageReader
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QFormLayout, QTabWidget, QWidget,
    QScrollArea, QLineEdit, QTextEdit, QDateEdit, QTimeEdit, QFontComboBox, QDialogButtonBox,
    QPushButton, QFileDialog, QListWidget, QAbstractItemView, QLabel, QGroupBox, QSizePolicy)
from ..models import Alarm, Schedule, Banner, Audio, ReAlert, Notify, UTC
from ..scheduling import next_occurrence
from ..media import IMAGE_FILTER, AUDIO_FILTER, validate_file, validate_image
from .common import responsive_row as row, spin, combo, check, note, ColorButton, DurationControl
from .banner import BannerWindow
from .corner_overlay import CornerConfig
from .corner_settings import CornerImageSettings


class AlarmEditor(QDialog):
    def __init__(self, alarm: Alarm | None, controller, parent=None):
        super().__init__(parent)
        self.original = alarm or Alarm()
        self.original_is_new = alarm is None
        if alarm is None:
            soon = datetime.now() + timedelta(minutes=5)
            self.original.schedule.start_date = soon.date().isoformat()
            self.original.schedule.time = soon.strftime("%H:%M:00")
        self.controller = controller
        self.result_alarm = None
        self.preview_key = "preview:" + self.original.id
        self.previews = []
        self.setWindowTitle("Edit alarm" if alarm else "Create alarm")
        self.resize(850, 770)
        self.setMinimumSize(680, 540)
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self._schedule_tab()
        self._banner_tab()
        self._corner_tab()
        self._audio_tab()
        self._realert_tab()
        self.error_label = QLabel()
        self.error_label.setObjectName("error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        box.accepted.connect(self.save)
        box.rejected.connect(self.reject)
        root.addWidget(box)
        self.controller.audio.changed.connect(self._audio_status)
        self.finished.connect(self._cleanup)

    def _corner_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.corner = CornerImageSettings(CornerConfig.from_corner(self.original.corner))  # this alarm's own draft
        scroll.setWidget(self.corner)
        self.tabs.addTab(scroll, "Corner Image")

    def _page(self, title):
        widget = QWidget()
        layout = QFormLayout(widget)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setVerticalSpacing(10)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        scroll.setWidget(widget)
        self.tabs.addTab(scroll, title)
        return layout

    def _schedule_tab(self):
        a, s = self.original, self.original.schedule
        f = self._page("Schedule")
        self.sched_form = f
        self.name = QLineEdit(a.name)
        self.message = QTextEdit(a.message)
        self.message.setMaximumHeight(100)
        self.enabled = check("Enabled", a.enabled)
        self.kind = combo([("once", "One time"), ("daily", "Every day"), ("weekly", "Selected weekdays"), ("interval", "Custom interval")], s.kind)
        self.start_date = QDateEdit(QDate.fromString(s.start_date, "yyyy-MM-dd"))
        self.start_date.setCalendarPopup(True)
        self.start_date.setDisplayFormat("yyyy-MM-dd")
        self.time = QTimeEdit(QTime.fromString(s.time, "HH:mm:ss"))
        self.time.setDisplayFormat("HH:mm:ss")
        f.addRow("Alarm name", self.name)
        f.addRow("Message", self.message)
        self.notify_enabled = check("Show notification", a.notify.enabled)
        self.notify_duration = spin(a.notify.duration, 1, 3600, " s")
        self.notify_font = spin(a.notify.font_size, 8, 72, " pt")
        f.addRow(self.notify_enabled)
        f.addRow("Notification duration", self.notify_duration)
        f.addRow("Notification font size", self.notify_font)
        f.addRow("State", self.enabled)
        f.addRow("Schedule", self.kind)
        f.addRow("Start date", self.start_date)
        f.addRow("Local time", self.time)
        self.weekdays = [check(name, i in s.weekdays) for i, name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])]
        self.days_row = row(*self.weekdays)
        f.addRow("Weekdays", self.days_row)
        self.interval = spin(s.interval, 1, 999999)
        self.interval_unit = combo([(v, v.capitalize()) for v in ["seconds", "minutes", "hours", "days", "weeks"]], s.interval_unit)
        self.interval_row = row(self.interval, self.interval_unit)
        f.addRow("Repeat every", self.interval_row)
        self.upcoming = QLabel()
        self.upcoming.setWordWrap(True)
        self.upcoming.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box = QGroupBox("Upcoming alarms")
        box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        QVBoxLayout(box).addWidget(self.upcoming)
        f.addRow(box)
        self.kind.currentIndexChanged.connect(self._schedule_mode)
        self.start_date.dateChanged.connect(self._update_upcoming)
        self.time.timeChanged.connect(self._update_upcoming)
        for w in self.weekdays:
            w.toggled.connect(self._update_upcoming)
        self.interval.valueChanged.connect(self._update_upcoming)
        self.interval_unit.currentIndexChanged.connect(self._update_upcoming)
        self._schedule_mode()

    def _schedule_mode(self):
        """Show only the rows (and their labels) that the chosen schedule type uses; then refresh the preview."""
        kind = self.kind.currentData()
        f = self.sched_form
        f.labelForField(self.start_date).setText("Date" if kind == "once" else "Starting date" if kind == "interval" else "Start date")
        f.labelForField(self.time).setText("Starting local time" if kind == "interval" else "Local time")
        f.setRowVisible(self.days_row, kind == "weekly")
        f.setRowVisible(self.interval_row, kind == "interval")
        self._update_upcoming()

    def _draft_schedule(self) -> Schedule:
        """A temporary Schedule built from the controls only. Never stored, saved or applied anywhere."""
        return Schedule(self.kind.currentData(), self.start_date.date().toString("yyyy-MM-dd"), self.time.time().toString("HH:mm:ss"),
                        [i for i, w in enumerate(self.weekdays) if w.isChecked()], self.interval.value(), self.interval_unit.currentData())

    def _schedule_problem(self, s: Schedule) -> str:
        if not self.start_date.date().isValid() or not self.time.time().isValid():
            return "Enter a valid date and time."
        if s.kind == "weekly" and not s.weekdays:
            return "Select at least one weekday."
        if s.kind == "interval" and s.interval < 1:
            return "Enter a positive recurrence interval."
        return ""

    @staticmethod
    def _format_time(value: datetime) -> str:
        hour = value.hour % 12 or 12
        seconds = f":{value.second:02d}" if value.second else ""
        return f"{value:%A, %B} {value.day}, {value.year}, {hour}:{value.minute:02d}{seconds} {'AM' if value.hour < 12 else 'PM'}"

    def upcoming_times(self, count=3):
        """(problem, [datetimes in the scheduling zone]) for the current draft. Pure calculation with the engine's own
        next_occurrence(); each result is the 'after' of the next call (strictly-after semantics). Writes nothing."""
        s = self._draft_schedule()
        problem = self._schedule_problem(s)
        if problem:
            return problem, []
        zone, found, after = self.controller.engine.zone, [], datetime.now(UTC)
        try:
            for _ in range(count):
                after = next_occurrence(s, after, zone)
                if after is None:
                    break
                found.append(after.astimezone(zone))
        except (ValueError, OverflowError) as exc:
            return str(exc) or "These values cannot form a valid schedule.", []
        return "", found

    def _update_upcoming(self, *_):
        problem, found = self.upcoming_times()
        self.upcoming.setText(problem or "\n".join(self._format_time(v) for v in found) or "No future occurrences")

    def _check_future_activation(self, a: Alarm):
        """Editor-level check: an enabled alarm whose new/changed schedule can never ring again is refused. Historical
        one-time alarms that were not edited still load and save normally."""
        if not a.enabled:
            return
        changed = self.original_is_new or a.schedule != self.original.schedule
        if not changed:
            return
        if next_occurrence(a.schedule, datetime.now(UTC), self.controller.engine.zone) is None:
            raise ValueError("This one-time alarm is in the past. Choose a future date and time, or turn off Enabled." if a.schedule.kind == "once"
                             else "This schedule has no future occurrence.")

    def _banner_tab(self):
        b = self.original.banner
        f = self._page("Banner")
        self.image_path = QLineEdit(b.path)
        select = QPushButton("Choose file…")
        select.clicked.connect(self._pick_image)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.image_path.clear)
        f.addRow("Image / GIF", row(self.image_path, select, remove))
        preview = QPushButton("Preview Banner")
        preview.setObjectName("primary")
        preview.clicked.connect(self.preview_banner)
        image_only = QPushButton("Image / GIF only")
        image_only.clicked.connect(self._image_only)
        f.addRow(row(preview, image_only, stretch=True))
        image_only.setToolTip("Hide text and buttons. Close with Esc or the banner context menu.")
        self.width = spin(b.width, 32, 16384, " px")
        self.height = spin(b.height, 32, 16384, " px")
        self.aspect = check("Lock width : height", b.lock_aspect)
        self._ratio = b.width / b.height
        self._sizing = False
        self.width.valueChanged.connect(lambda _: self._resize_ratio(True))
        self.height.valueChanged.connect(lambda _: self._resize_ratio(False))
        self.aspect.toggled.connect(lambda _: setattr(self, "_ratio", self.width.value() / self.height.value()))
        ratio = QPushButton("Use image ratio")
        ratio.clicked.connect(self._use_media_ratio)
        f.addRow("Dimensions", row(self.width, QLabel("×"), self.height))
        f.addRow("Aspect ratio", row(self.aspect, ratio))
        self.fit = combo([("fit", "Fit · preserve entire image"), ("fill", "Fill · crop edges"), ("stretch", "Stretch")], b.fit)
        f.addRow("Scaling", self.fit)
        self.position = combo([(v, v.replace("_", " ").title()) for v in ["center", "top_left", "top_right", "bottom_left", "bottom_right", "custom"]], b.position)
        self.x = spin(b.x, -32768, 32768, " px")
        self.y = spin(b.y, -32768, 32768, " px")
        f.addRow("Position", self.position)
        f.addRow("Custom X / Y", row(self.x, self.y))
        self.x.setToolTip("Horizontal offset from the selected monitor's work area")
        self.y.setToolTip("Vertical offset from the selected monitor's work area")
        screens = [("", "Primary monitor")] + [(s.name(), f"{s.name()} · {s.size().width()} × {s.size().height()}") for s in QApplication.screens()]
        if b.screen and b.screen not in [k for k, _ in screens]:
            screens.append((b.screen, b.screen + " (disconnected)"))
        self.screen = combo(screens, b.screen)
        self.screen.setSizeAdjustPolicy(self.screen.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.screen.setMinimumContentsLength(12)
        f.addRow("Monitor", self.screen)
        self.on_top = check("Always on top", b.always_on_top)
        self.fullscreen = check("Full-screen", b.fullscreen)
        f.addRow("Window mode", row(self.on_top, self.fullscreen))
        self.bg = ColorButton(b.background)
        self.transparent = check("Transparent background", b.transparent)
        f.addRow("Background", row(self.bg, self.transparent))
        self.opacity = spin(round(b.opacity * 100), 5, 100, "%")
        self.border = check("Show border", b.border)
        self.rounded = spin(b.rounded, 0, 240, " px radius")
        f.addRow("Window opacity", self.opacity)
        f.addRow("Edges", row(self.border, self.rounded))
        self.banner_text = QTextEdit(b.text)
        self.banner_text.setPlaceholderText("Leave blank to show the alarm name and message")
        self.banner_text.setMaximumHeight(90)
        f.addRow("Custom alarm text", self.banner_text)
        self.text_position = combo([(v, v.title()) for v in ["top", "center", "bottom"]], b.text_position)
        self.font = QFontComboBox()
        self.font.setSizeAdjustPolicy(self.font.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.font.setMinimumContentsLength(12)
        self.font.setCurrentFont(QFont(b.font_family))
        self.font_size = spin(b.font_size, 6, 240, " pt")
        self.text_color = ColorButton(b.text_color)
        f.addRow("Text position", self.text_position)
        f.addRow("Font", row(self.font, self.font_size))
        f.addRow("Text color", self.text_color)
        self.hide_text = check("Hide all text", b.hide_text)
        self.hide_buttons = check("Hide buttons", b.hide_buttons)
        f.addRow("Visible controls", row(self.hide_text, self.hide_buttons))
        self.gif_speed = spin(b.gif_speed, 1, 1000, "%")
        self.loop_gif = check("Loop GIF", b.loop_gif)
        f.addRow("GIF playback", row(self.gif_speed, self.loop_gif))
        self.duration = spin(b.duration, 0, 604800, " seconds", decimals=1)
        self.duration.setSpecialValueText("Manual close")
        self.auto_action = combo([("close", "Close banner only"), ("dismiss", "Dismiss this occurrence")], b.auto_action)
        f.addRow("Display duration", self.duration)
        f.addRow("When duration ends", self.auto_action)
        self.position.currentIndexChanged.connect(self._position_mode)
        self._position_mode()

    def _position_mode(self):
        enabled = self.position.currentData() == "custom"
        self.x.setEnabled(enabled)
        self.y.setEnabled(enabled)

    def _image_only(self):
        self.hide_text.setChecked(True)
        self.hide_buttons.setChecked(True)
        self.transparent.setChecked(True)
        self.border.setChecked(False)
        self.rounded.setValue(0)

    def _resize_ratio(self, width_changed):
        if self._sizing or not self.aspect.isChecked():
            return
        self._sizing = True
        if width_changed:
            self.height.setValue(round(self.width.value() / self._ratio))
        else:
            self.width.setValue(round(self.height.value() * self._ratio))
        self._sizing = False

    def _use_media_ratio(self):
        path = self.image_path.text().strip()
        if not path:
            return
        try:
            validate_image(path)
            size = QSvgRenderer(path).defaultSize() if Path(path).suffix.lower() == ".svg" else QImageReader(path).size()
            if size.width() and size.height():
                self._ratio = size.width() / size.height()
                self._sizing = True
                self.height.setValue(round(self.width.value() / self._ratio))
                self._sizing = False
        except ValueError as exc:
            self.show_error(str(exc))

    def _pick_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose your image or animated GIF", self.image_path.text(), IMAGE_FILTER)
        if path:
            try:
                validate_image(path)
                self.image_path.setText(path)
            except ValueError as exc:
                self.show_error(str(exc))

    def _audio_tab(self):
        a = self.original.audio
        f = self._page("Audio / playlist")
        self.tracks = QListWidget()
        self.tracks.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tracks.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tracks.setMinimumHeight(150)
        self.tracks.addItems(a.tracks)
        self.tracks.setCurrentRow(min(a.selected_track, len(a.tracks) - 1))
        f.addRow(self.tracks)
        buttons = []
        for text, callback in [("Import tracks…", self._import_tracks), ("Remove", self._remove_tracks), ("Move up", lambda: self._move_track(-1)), ("Move down", lambda: self._move_track(1))]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            buttons.append(button)
        f.addRow(row(*buttons))
        self.audio_mode = combo([("single", "Selected track once"), ("sequential", "Playlist once"), ("cyclic", "Cyclic playlist"), ("shuffle", "Shuffle playlist"), ("repeat_track", "Repeat selected track"), ("repeat_count", "Repeat playlist"), ("infinite", "Infinite playlist")], a.mode)
        f.addRow("Playback mode", self.audio_mode)
        self.cycles = spin(a.cycles or 1, 1, 999)
        self.infinite_audio = check("Infinite", a.cycles is None)
        f.addRow("Total playback cycles", row(self.cycles, self.infinite_audio))
        self.cycles.setToolTip("Total playlist cycles, including the first")
        self.shuffle_each = check("Reshuffle each cycle", a.shuffle_each_cycle)
        f.addRow("Shuffle", self.shuffle_each)
        self.volume = spin(a.volume, 0, 100, "%")
        self.fade_in = spin(a.fade_in, 0, 600, " seconds", decimals=1)
        self.fade_out = spin(a.fade_out, 0, 600, " seconds", decimals=1)
        self.track_gap = spin(a.track_gap, 0, 86400, " seconds", decimals=1)
        self.cycle_gap = spin(a.cycle_gap, 0, 86400, " seconds", decimals=1)
        f.addRow("Volume", self.volume)
        f.addRow("Fade in / out", row(self.fade_in, self.fade_out))
        f.addRow("Pause between tracks", self.track_gap)
        f.addRow("Pause between cycles", self.cycle_gap)
        controls = []
        for text, callback in [("Preview track", lambda: self.preview_audio(True)), ("Preview playlist", lambda: self.preview_audio(False)), ("Pause", self._pause_preview), ("Resume", self._resume_preview), ("Stop", self._stop_preview)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            controls.append(button)
        f.addRow(row(*controls))
        self.audio_status = note("Preview stopped")
        f.addRow(self.audio_status)
        self.audio_mode.currentIndexChanged.connect(self._audio_mode_changed)
        self.infinite_audio.toggled.connect(self._audio_mode_changed)
        self.volume.valueChanged.connect(self._live_volume)
        self._audio_mode_changed()

    def _audio_mode_changed(self):
        mode = self.audio_mode.currentData()
        custom = mode in {"cyclic", "shuffle", "repeat_track", "repeat_count"}
        self.cycles.setEnabled(custom and not self.infinite_audio.isChecked())
        self.infinite_audio.setEnabled(custom)
        self.shuffle_each.setEnabled(mode == "shuffle")

    def _import_tracks(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Import your audio tracks", "", AUDIO_FILTER)
        for path in paths:
            try:
                validate_file(path)
                self.tracks.addItem(path)
            except ValueError as exc:
                self.show_error(str(exc))
        if self.tracks.currentRow() < 0 and self.tracks.count():
            self.tracks.setCurrentRow(0)

    def _remove_tracks(self):
        for item in self.tracks.selectedItems():
            self.tracks.takeItem(self.tracks.row(item))
        if self.tracks.count() and self.tracks.currentRow() < 0:
            self.tracks.setCurrentRow(0)

    def _move_track(self, direction):
        index = self.tracks.currentRow()
        target = index + direction
        if 0 <= index < self.tracks.count() and 0 <= target < self.tracks.count():
            item = self.tracks.takeItem(index)
            self.tracks.insertItem(target, item)
            self.tracks.setCurrentRow(target)

    def _realert_tab(self):
        r = self.original.realert
        f = self._page("Re-alerts / snooze")
        self.realert_count = spin(r.count if r.count is not None else 1, 0, 999)
        self.realert_count.setSpecialValueText("No re-alerts")
        self.realert_infinite = check("Infinite until dismissed", r.count is None, wrap=True)
        f.addRow("Additional activations", row(self.realert_count, self.realert_infinite))
        self.realert_interval = DurationControl(r.interval_seconds)
        f.addRow("Re-alert every", self.realert_interval)
        self.restart_audio = check("Restart playlist at every re-alert", r.restart_audio, wrap=True)
        self.reopen_banner = check("Reopen banner on re-alert", r.reopen_banner, wrap=True)
        self.keep_banner = check("Keep banner after playback", r.keep_banner, wrap=True)
        f.addRow(self.restart_audio)
        f.addRow(self.reopen_banner)
        f.addRow(self.keep_banner)
        self.realert_count.setToolTip("Re-alerts after the initial activation; snooze preserves this count")
        self.snooze = DurationControl(self.original.snooze_seconds)
        f.addRow("Default snooze", self.snooze)
        self.realert_infinite.toggled.connect(lambda checked: self.realert_count.setEnabled(not checked))
        self.realert_count.setEnabled(not self.realert_infinite.isChecked())

    def gather(self):
        a = Alarm.from_dict(self.original.to_dict())
        a.name, a.message, a.enabled = self.name.text().strip(), self.message.toPlainText(), self.enabled.isChecked()
        a.schedule = Schedule(self.kind.currentData(), self.start_date.date().toString("yyyy-MM-dd"), self.time.time().toString("HH:mm:ss"), [i for i, w in enumerate(self.weekdays) if w.isChecked()], self.interval.value(), self.interval_unit.currentData())
        a.banner = Banner(path=self.image_path.text().strip(), width=self.width.value(), height=self.height.value(), lock_aspect=self.aspect.isChecked(), fit=self.fit.currentData(), position=self.position.currentData(), x=self.x.value(), y=self.y.value(), screen=self.screen.currentData(), always_on_top=self.on_top.isChecked(), fullscreen=self.fullscreen.isChecked(), background=self.bg.value, transparent=self.transparent.isChecked(), opacity=self.opacity.value()/100, border=self.border.isChecked(), rounded=self.rounded.value(), text=self.banner_text.toPlainText(), text_position=self.text_position.currentData(), font_family=self.font.currentFont().family(), font_size=self.font_size.value(), text_color=self.text_color.value, hide_text=self.hide_text.isChecked(), hide_buttons=self.hide_buttons.isChecked(), gif_speed=self.gif_speed.value(), loop_gif=self.loop_gif.isChecked(), duration=self.duration.value(), auto_action=self.auto_action.currentData())
        a.audio = Audio(tracks=[self.tracks.item(i).text() for i in range(self.tracks.count())], mode=self.audio_mode.currentData(), selected_track=max(0, self.tracks.currentRow()), cycles=None if self.infinite_audio.isChecked() else self.cycles.value(), volume=self.volume.value(), fade_in=self.fade_in.value(), fade_out=self.fade_out.value(), track_gap=self.track_gap.value(), cycle_gap=self.cycle_gap.value(), shuffle_each_cycle=self.shuffle_each.isChecked())
        a.realert = ReAlert(None if self.realert_infinite.isChecked() else self.realert_count.value(), self.realert_interval.seconds(), self.restart_audio.isChecked(), self.reopen_banner.isChecked(), self.keep_banner.isChecked())
        a.snooze_seconds = self.snooze.seconds()
        a.corner = self.corner.value().to_corner()
        a.notify = Notify(self.notify_enabled.isChecked(), self.notify_duration.value(), self.notify_font.value())
        return a

    def show_error(self, message):
        self.error_label.setText(message)
        self.error_label.show()

    def save(self):
        try:
            a = self.gather()
            a.validate()
            self._check_future_activation(a)
            validate_image(a.banner.path)
            for path in a.audio.tracks:
                validate_file(path)
            self.result_alarm = a
            self.accept()
        except (ValueError, OSError) as exc:
            self.show_error(str(exc))

    def preview_banner(self):
        try:
            a = self.gather()
            validate_image(a.banner.path)
            for old in list(self.previews):
                old.close()  # banner_closed -> _preview_closed removes and releases it
            banner = BannerWindow(a, preview=True, parent=self)
            self.previews.append(banner)
            banner.banner_closed.connect(lambda b=banner: self._preview_closed(b))
            banner.error.connect(self.show_error)
            banner.open_banner()
        except (ValueError, OSError) as exc:
            self.show_error(str(exc))

    def _preview_closed(self, banner):
        if banner in self.previews:
            self.previews.remove(banner)
            banner.deleteLater()

    def preview_audio(self, single):
        audio = self.gather().audio
        if single:
            audio.mode, audio.cycles = "single", 1
        self.controller.audio.enqueue(self.preview_key, audio)
        self._audio_status()

    def _pause_preview(self):
        if self.controller.audio.current == self.preview_key:
            self.controller.audio.playback.pause()

    def _resume_preview(self):
        if self.controller.audio.current == self.preview_key:
            self.controller.audio.playback.resume()

    def _stop_preview(self):
        self.controller.audio.cancel(self.preview_key, immediate=True)

    def _live_volume(self, value):
        if self.controller.audio.current == self.preview_key:
            self.controller.audio.playback.set_volume(value)

    def _audio_status(self):
        self.audio_status.setText(self.controller.audio.describe(self.preview_key))

    def _cleanup(self, _):
        self._stop_preview()
        try:
            self.controller.audio.changed.disconnect(self._audio_status)
        except (RuntimeError, TypeError):
            pass
        for banner in list(self.previews):
            banner.close()
        self.previews.clear()

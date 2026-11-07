import os
from dataclasses import replace
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication, QMovie, QPixmap
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QDialogButtonBox, QLabel, QWidget,
                               QFileDialog, QMessageBox, QPushButton, QListWidget, QListWidgetItem, QAbstractItemView)
from .common import check, spin, combo, responsive_row as row
from .corner_overlay import CornerConfig, CornerOverlay, MODE_LABELS, MAX_DIMENSION, MAX_CYCLE_SECONDS, monitor_choices
from ..media import IMAGE_FILTER, validate_image


ORDER_LABELS = [("sequential", "Sequential"), ("shuffle", "Shuffle (no repeats)")]
LOOP_LABELS = [(True, "Loop"), (False, "Hold last image")]


class CornerImageSettings(QWidget):
    """Corner Image controls for ONE alarm (the Corner Image tab of the alarm editor). It edits a private draft:
    nothing is written anywhere until the editor's Save, and Cancel simply discards the draft (``value()``)."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = CornerConfig.from_corner(config) if not isinstance(config, CornerConfig) else config.normalized()
        self._busy = False
        c = self.config
        root = QVBoxLayout(self)
        self.enabled = check("Show corner image", c.enabled)
        root.addWidget(self.enabled)
        root.addWidget(QLabel("Images"))
        self.images = QListWidget()
        self.images.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.images.setFixedHeight(130)
        root.addWidget(self.images)
        self.add_button = QPushButton("Add Images…")
        self.remove_button = QPushButton("Remove")
        self.up_button = QPushButton("Move Up")
        self.down_button = QPushButton("Move Down")
        self.preview_button = QPushButton("Preview Image")
        root.addWidget(row(self.add_button, self.remove_button, self.up_button, self.down_button, self.preview_button, stretch=True))
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setVerticalSpacing(8)
        root.addLayout(form)
        self.order = combo(ORDER_LABELS, c.order)
        self.loop = combo(LOOP_LABELS, c.loop)
        self.cycle = check("Cycle while visible", c.cycle)
        self.cycle_seconds = spin(c.cycle_seconds, 1, MAX_CYCLE_SECONDS, " s")
        self.cycle_seconds.setKeyboardTracking(False)
        self.width_spin = spin(c.width, 1, MAX_DIMENSION, " px")
        self.height_spin = spin(c.height, 1, MAX_DIMENSION, " px")
        self.keep = check("Keep aspect ratio", c.keep_aspect)
        self.restore_button = QPushButton("Original dimensions")
        self.mode = combo(MODE_LABELS, c.mode)
        self.opacity = spin(c.opacity, 5, 100, " %")
        for box in (self.width_spin, self.height_spin, self.opacity):
            box.setKeyboardTracking(False)  # applies on Enter / leaving the field / arrow buttons
        self.on_top = check("Always on Top", c.on_top)
        self.trim = check("Trim transparent edges", c.trim)
        self.monitor = combo(monitor_choices(c.monitor), c.monitor)
        self.monitor.setSizeAdjustPolicy(self.monitor.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.monitor.setMinimumContentsLength(12)
        for label, widget in [("Playback order", self.order), ("At the end", self.loop), ("", self.cycle), ("Cycle every", self.cycle_seconds),
                              ("Dimensions", row(self.width_spin, QLabel("×"), self.height_spin)), ("Aspect ratio", row(self.keep, self.restore_button)),
                              ("Scaling", self.mode), ("Opacity", self.opacity), ("", self.on_top), ("", self.trim), ("Monitor", self.monitor)]:
            form.addRow(label, widget)
        self.status = QLabel()
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        root.addStretch()

        self.enabled.toggled.connect(lambda on: self._apply(enabled=on))
        self.add_button.clicked.connect(self.choose_images)
        self.remove_button.clicked.connect(self.remove_selected)
        self.up_button.clicked.connect(lambda: self.move_selected(-1))
        self.down_button.clicked.connect(lambda: self.move_selected(1))
        self.preview_button.clicked.connect(self.preview_image)
        self.images.currentRowChanged.connect(lambda _: self._update_buttons())
        self.order.currentIndexChanged.connect(lambda: self._apply(order=self.order.currentData()))
        self.loop.currentIndexChanged.connect(lambda: self._apply(loop=bool(self.loop.currentData())))
        self.cycle.toggled.connect(lambda on: self._apply(cycle=on))
        self.cycle_seconds.valueChanged.connect(lambda value: self._apply(cycle_seconds=value))
        self.width_spin.valueChanged.connect(lambda: self._size_changed("width"))
        self.height_spin.valueChanged.connect(lambda: self._size_changed("height"))
        self.keep.toggled.connect(self._keep_toggled)
        self.restore_button.clicked.connect(self._restore_original)
        self.mode.currentIndexChanged.connect(lambda: self._apply(mode=self.mode.currentData()))
        self.opacity.valueChanged.connect(lambda value: self._apply(opacity=value))
        self.on_top.toggled.connect(lambda on: self._apply(on_top=on))
        self.trim.toggled.connect(lambda on: self._apply(trim=on))
        self.monitor.currentIndexChanged.connect(self._monitor_changed)
        app = QGuiApplication.instance()
        app.screenAdded.connect(self.refresh_monitors)
        app.screenRemoved.connect(self.refresh_monitors)
        self.sync_state()

    def value(self) -> CornerConfig:
        """The edited (validated) configuration; the editor stores it on the alarm when Save is pressed."""
        return self.config.normalized()

    def _apply(self, **changes):
        """Update the draft only (never touches the overlay, the database or global preferences)."""
        if self._busy:
            return ""
        self.config = replace(self.config, **changes).normalized()
        self.sync_state()
        return ""

    def selected_path(self) -> str:
        row_index = self.images.currentRow()
        images = self.config.images
        if 0 <= row_index < len(images):
            return images[row_index]
        return images[0] if images else ""

    def choose_images(self):
        images = self.config.images
        start = os.path.dirname(images[-1]) if images else ""
        paths, _ = QFileDialog.getOpenFileNames(self, "Add corner images", start, IMAGE_FILTER)
        if paths:
            self.add_paths(paths)

    def add_paths(self, paths):
        """Import files into this alarm's list (also used by tests). Files are only read, never copied or modified."""
        current = list(self.config.images)
        known = {os.path.normcase(os.path.normpath(p)) for p in current}
        added, errors = [], []
        for path in paths:
            key = os.path.normcase(os.path.normpath(path))
            if key in known:
                continue
            try:
                validate_image(path)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            known.add(key)
            added.append(path)
        if added:
            self._apply(images=current + added)
            if not current and self.keep.isChecked():
                self._conform_height_to_width()
        if errors:
            QMessageBox.warning(self, "Corner image", "Some files were not added:\n\n" + "\n\n".join(errors[:5]))
        return len(added), errors

    def remove_selected(self):
        index = self.images.currentRow()
        images = list(self.config.images)
        if 0 <= index < len(images):
            del images[index]
            self._apply(images=images)
            self.images.setCurrentRow(min(index, self.images.count() - 1))

    def move_selected(self, delta):
        index, images = self.images.currentRow(), list(self.config.images)
        target = index + delta
        if 0 <= index < len(images) and 0 <= target < len(images):
            images[index], images[target] = images[target], images[index]
            self._apply(images=images)
            self.images.setCurrentRow(target)

    def preview_image(self):
        """Show the selected file in a small window (independent of the overlay; the file is only read)."""
        path = self.selected_path()
        if not path:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Preview image")
        layout = QVBoxLayout(dialog)
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        movie = QMovie(path)
        if movie.isValid() and movie.frameCount() != 1:
            label.setMovie(movie)
            movie.start()
        else:
            pixmap = QPixmap(path)
            if pixmap.isNull():
                QMessageBox.warning(self, "Corner image", f"Could not load the image for preview:\n{path}")
                dialog.deleteLater()
                return
            label.setPixmap(pixmap.scaled(520, 420, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(label)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec()
        dialog.deleteLater()

    def _reference_size(self):
        """Size of the selected list entry (read without loading it into the overlay)."""
        path = self.selected_path()
        return CornerOverlay.probe_size(path) if path else QSize()

    def _ratio(self):
        size = self._reference_size()
        if size.isValid() and size.width() > 0:
            return size.height() / size.width()
        c = self.config
        return c.height / max(1, c.width)

    def _conform_height_to_width(self):
        self._busy = True
        self.height_spin.setValue(max(1, round(self.width_spin.value() * self._ratio())))
        self._busy = False
        self._apply(width=self.width_spin.value(), height=self.height_spin.value())

    def _size_changed(self, source):
        if self._busy:
            return
        if self.keep.isChecked():
            ratio = self._ratio()
            self._busy = True
            if source == "width":
                self.height_spin.setValue(max(1, round(self.width_spin.value() * ratio)))
            else:
                self.width_spin.setValue(max(1, round(self.height_spin.value() / ratio)))
            self._busy = False
        self._apply(width=self.width_spin.value(), height=self.height_spin.value())

    def _keep_toggled(self, on):
        if self._busy:
            return
        if on:
            self._conform_height_to_width()
        self._apply(keep_aspect=on)

    def _restore_original(self):
        size = self._reference_size()
        if not size.isValid() or size.width() <= 0:
            return
        self._busy = True
        self.width_spin.setValue(size.width())
        self.height_spin.setValue(size.height())
        self._busy = False
        self._apply(width=self.width_spin.value(), height=self.height_spin.value())

    def _monitor_changed(self):
        if not self._busy:
            self._apply(monitor=self.monitor.currentData())

    def refresh_monitors(self, *_):
        """Display configuration changed: rebuild the list without altering the saved choice."""
        saved = self.config.monitor
        self._busy = True
        self.monitor.clear()
        for key, label in monitor_choices(saved):
            self.monitor.addItem(label, key)
        self.monitor.setCurrentIndex(max(0, self.monitor.findData(saved)))
        self._busy = False

    def _update_buttons(self):
        c = self.config
        index = self.images.currentRow()
        have = bool(c.images)
        self.remove_button.setEnabled(index >= 0)
        self.up_button.setEnabled(index > 0)
        self.down_button.setEnabled(0 <= index < len(c.images) - 1)
        self.preview_button.setEnabled(have)
        self.restore_button.setEnabled(have)
        self.cycle_seconds.setEnabled(c.cycle)

    def sync_state(self):
        c = self.config
        self._busy = True
        signature = [(path, os.path.isfile(path)) for path in c.images]
        if signature != getattr(self, "_image_signature", None):
            self._image_signature = signature
            row_index = self.images.currentRow()
            self.images.clear()
            for number, (path, exists) in enumerate(signature, 1):
                item = QListWidgetItem(f"{number}. {os.path.basename(path) or path}" + ("   (file not found)" if not exists else ""))
                item.setToolTip(path)
                self.images.addItem(item)
            if c.images:
                self.images.setCurrentRow(max(0, min(row_index, len(c.images) - 1)))
        for widget, value in ((self.enabled, c.enabled), (self.cycle, c.cycle), (self.keep, c.keep_aspect), (self.on_top, c.on_top), (self.trim, c.trim)):
            if widget.isChecked() != value:
                widget.setChecked(value)
        for widget, value in ((self.order, c.order), (self.loop, c.loop), (self.mode, c.mode)):
            index = widget.findData(value)
            if index >= 0 and widget.currentIndex() != index:
                widget.setCurrentIndex(index)
        if self.cycle_seconds.value() != c.cycle_seconds:
            self.cycle_seconds.setValue(c.cycle_seconds)
        self._busy = False
        self._update_buttons()
        text = self._status_text()
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _status_text(self) -> str:
        c = self.config
        if not c.images:
            return ""
        missing = sum(1 for p in c.images if not os.path.isfile(p))
        return f"{missing} missing file(s)" if missing else ""

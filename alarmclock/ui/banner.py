"""Banner rendering. BannerMedia/paint_banner are shared by the popup (BannerWindow, used for
editor preview and the manual "Show Banner" command) and BannerBackground (the dashboard layer).
Only user-selected media is ever painted."""
from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt, QObject, QRectF, QSize, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImageReader, QKeySequence, QMovie, QPainter, QPainterPath, QPalette, QPen, QPixmap, QShortcut
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QHBoxLayout, QVBoxLayout, QMenu
from ..models import Alarm, Banner
from ..media import validate_image


class BannerMedia(QObject):
    """Decodes the user-selected image / GIF (QMovie) / SVG and draws it per the Banner config."""
    frame = Signal()
    error = Signal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.movie = None
        self.pixmap = None
        self.svg = None
        self._active = False
        self._paused = False

    def load(self) -> str:
        """Load the configured file. Returns an error message ('' on success); never substitutes media."""
        path = self.config.path
        if not path:
            return ""
        try:
            validate_image(path)
            if Path(path).suffix.lower() == ".svg":
                self.svg = QSvgRenderer(path, self)
            else:
                reader = QImageReader(path)
                reader.setAutoTransform(True)
                if bytes(reader.format()).lower() == b"gif":
                    self.movie = QMovie(path, parent=self)
                    self.movie.setCacheMode(QMovie.CacheMode.CacheNone)
                    self.movie.setSpeed(self.config.gif_speed)
                    self.movie.frameChanged.connect(self._frame_changed)
                    self.movie.error.connect(lambda _: self._media_error(f"Cannot decode GIF: {path}\n{self.movie.lastErrorString()}"))
                    self.movie.finished.connect(self._movie_finished)
                    if not self.movie.isValid():
                        raise ValueError(f"Cannot decode GIF: {path}")
                else:
                    image = reader.read()
                    if image.isNull():
                        raise ValueError(f"Cannot decode image: {path}\n{reader.errorString()}")
                    self.pixmap = QPixmap.fromImage(image)
        except ValueError as exc:
            return str(exc)
        return ""

    def start(self):
        self._active = True
        if self.movie:
            self.movie.start()

    def stop(self):
        self._active = False
        self._paused = False
        if self.movie:
            self.movie.stop()

    def pause(self):
        if self.movie and self._active and not self._paused and self.movie.state() == QMovie.MovieState.Running:
            self.movie.setPaused(True)
            self._paused = True

    def resume(self):
        if self.movie and self._paused:
            self._paused = False
            self.movie.setPaused(False)

    def _movie_finished(self):
        if self.movie and self.config.loop_gif and self._active:
            self.movie.start()

    def _frame_changed(self, index):
        self.frame.emit()
        # QMovie otherwise obeys an infinite loop encoded inside the GIF itself.
        if not self.config.loop_gif and self.movie.frameCount() > 0 and index == self.movie.frameCount() - 1:
            self.movie.stop()

    def _media_error(self, message):
        if self.movie:
            self.movie.stop()
        self.error.emit(message)

    def draw(self, painter, bounds: QRectF):
        pixmap = self.movie.currentPixmap() if self.movie else self.pixmap
        natural = QSize()
        if self.svg:
            natural = self.svg.defaultSize()
        elif pixmap is not None and not pixmap.isNull():
            natural = pixmap.size()
        if natural.isEmpty():
            return
        if self.config.fit == "stretch":
            target = bounds
        else:
            ratio = (min if self.config.fit == "fit" else max)(bounds.width() / natural.width(), bounds.height() / natural.height())
            w, h = natural.width() * ratio, natural.height() * ratio
            target = QRectF(bounds.center().x() - w / 2, bounds.center().y() - h / 2, w, h)
        if self.svg:
            self.svg.render(painter, target)
        else:
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))


def _luminance(color):
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(color.red()) + 0.7152 * channel(color.green()) + 0.0722 * channel(color.blue())


def _contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _panel_style(config):
    """Panel colours from the app theme (derived from the active palette set by apply_theme)."""
    app = QApplication.instance()
    dark = app is None or app.palette().color(QPalette.ColorRole.Window).lightness() < 128
    if dark:
        fill, border, fallback = QColor("#192132"), QColor("#34405A"), QColor("#edf2ff")
        fill.setAlpha(214)  # ~84%
    else:
        fill, border, fallback = QColor("#F2F5FB"), QColor("#D6DEEB"), QColor("#17233A")
        fill.setAlpha(225)  # ~88%
    configured = QColor(config.text_color)
    opaque = QColor(fill)
    opaque.setAlpha(255)
    text_color = configured if configured.isValid() and _contrast(configured, opaque) >= 4.5 else fallback
    return fill, border, text_color


def paint_banner(painter, rect, config, media, text, bottom_reserve=0, text_panel=False, tint=0):
    """Shared banner drawing: background, media, text, border, rounded corners."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    bounds = QRectF(rect).adjusted(1, 1, -1, -1)
    shape = QPainterPath()
    shape.addRoundedRect(bounds, config.rounded, config.rounded)
    painter.setClipPath(shape)
    if not config.transparent:
        painter.fillPath(shape, QColor(config.background))
    media.draw(painter, bounds)
    if tint:  # optional translucent dark layer between the media and the text (0 = off, normal banners unchanged)
        painter.fillPath(shape, QColor(10, 16, 32, tint))
    if text:
        painter.setFont(QFont(config.font_family, config.font_size))
        painter.setPen(QColor(config.text_color))
        text_rect = bounds.adjusted(22, 22, -22, -22 - bottom_reserve)
        alignment = {"top": Qt.AlignmentFlag.AlignTop, "center": Qt.AlignmentFlag.AlignVCenter, "bottom": Qt.AlignmentFlag.AlignBottom}[config.text_position]
        flags = int(Qt.AlignmentFlag.AlignHCenter | alignment | Qt.TextFlag.TextWordWrap)
        if text_panel and text_rect.width() > 0 and text_rect.height() > 0:
            fill, border, text_color = _panel_style(config)
            used = painter.boundingRect(text_rect, flags, text)  # actual wrapped text extent
            panel = used.adjusted(-14, -8, 14, 8).intersected(bounds.adjusted(6, 6, -6, -6))
            painter.setPen(QPen(border, 1))
            painter.setBrush(fill)
            painter.drawRoundedRect(panel, 10, 10)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(text_color)
        painter.drawText(text_rect, flags, text)
    if config.border:
        painter.setClipping(False)
        painter.setPen(QPen(QColor(config.text_color), 2))
        painter.drawPath(shape)


class BannerBackground(QWidget):
    """The dashboard's central widget: draws the selected alarm's existing banner behind its children.
    It only paints; it never takes mouse input, and alarm lifecycle never depends on it."""
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = None
        self.media = None

    @property
    def has_image(self) -> bool:
        return self.media is not None

    def show_banner(self, config) -> str:
        """Display `config` (an alarm's Banner). Returns a load error message, '' if fine."""
        self.clear()
        if not config or not config.path:
            return ""
        media = BannerMedia(config, self)
        message = media.load()
        if message:
            media.deleteLater()
            return message
        self.config, self.media = config, media
        media.frame.connect(lambda: self.update())
        media.error.connect(self.error.emit)
        if self.isVisible():
            media.start()
        self.update()
        return ""

    def clear(self):
        if self.media:
            self.media.stop()
            self.media.deleteLater()
        self.config = self.media = None
        self.update()

    def showEvent(self, event):
        if self.media:
            if self.media._paused:
                self.media.resume()
            else:
                self.media.start()
        super().showEvent(event)

    def hideEvent(self, event):
        if self.media:
            self.media.pause()
        super().hideEvent(event)

    def paintEvent(self, event):
        if not self.media:
            return
        painter = QPainter(self)
        painter.setOpacity(self.config.opacity)
        # Only the user's custom text; the popup's default name/message text is popup-specific.
        text = "" if self.config.hide_text else self.config.text
        paint_banner(painter, self.rect(), self.config, self.media, text)


NOTIFICATION_WIDTH = 360


def notification_config(alarm: Alarm) -> Banner:
    """Compact text-only config for the message-notification mode. Text/size come from the alarm's own
    Notify settings; Only this alarm's banner media (path/GIF speed/loop) is reused; banner text, size, fullscreen and buttons are not."""
    n = alarm.notify
    font = QFont(Banner().font_family, n.font_size)
    metrics = QFontMetrics(font)
    flags = int(Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap)
    wrapped = metrics.boundingRect(0, 0, NOTIFICATION_WIDTH - 44, 10000, flags, alarm.message).height()
    return Banner(path=alarm.banner.path, fit="fill", gif_speed=alarm.banner.gif_speed, loop_gif=alarm.banner.loop_gif, width=NOTIFICATION_WIDTH, height=max(48, min(wrapped + 44, 600)), position="top-left", always_on_top=True,
                  fullscreen=False, rounded=14, border=True, text=alarm.message, text_position="center", font_size=n.font_size,
                  hide_buttons=True, duration=float(n.duration), auto_action="close")


class BannerWindow(QWidget):
    dismiss_requested = Signal()
    snooze_requested = Signal()
    stop_requested = Signal()
    banner_closed = Signal()
    error = Signal(str)

    def __init__(self, alarm: Alarm, *, preview=False, notification=False, parent=None):
        super().__init__(parent)  # a parent inside a modal dialog's tree keeps the preview clickable
        self.alarm = alarm
        self.notification = notification  # compact, text-only message-notification mode
        self.config = notification_config(alarm) if notification else alarm.banner
        self.preview = preview
        self._closing = False
        self._cleaned = False
        self._load_error = ""
        self.media = BannerMedia(self.config, self)
        self.media.frame.connect(lambda: self.update())
        self.media.error.connect(self.error.emit)
        flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        if self.config.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        if notification:
            flags |= Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)
        if notification:
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(("Notification · " if notification else "Preview · " if preview else "Alarm · ") + alarm.name)
        self.setWindowOpacity(self.config.opacity)
        self.setMinimumSize(32, 32)
        self.resize(self.config.width, self.config.height)
        if notification:
            self._buttons_shown = False
            self.buttons = QPushButton("×", self)
            self.buttons.setFixedSize(18, 18)
            self.buttons.setToolTip("Close notification")
            self.buttons.setStyleSheet("QPushButton {background:transparent;color:white;border:none;font-size:14px;} QPushButton:hover {background:#3b5275;border-radius:9px;}")
            self.buttons.clicked.connect(self.close)  # closes only this window; never dismisses/snoozes the alarm
            self.buttons.move(self.width() - 22, 4)
            self.duration_timer = QTimer(self)
            self.duration_timer.setSingleShot(True)
            self.duration_timer.timeout.connect(self._expired)
            self._load_media()
            self._place()
            return
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.addStretch()
        buttons = QWidget(self)
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch()
        labels = [("Stop sound", self.stop_requested.emit), ("Snooze", self.snooze_requested.emit), ("Dismiss", self.dismiss_requested.emit), ("Close banner", self.close)]
        if preview:
            labels = [("Close preview", self.close)]
        for text, callback in labels:
            button = QPushButton(text)
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addStretch()
        layout.addWidget(buttons)
        self._buttons_shown = preview or not self.config.hide_buttons  # Close Preview can never be hidden
        buttons.setVisible(self._buttons_shown)
        self.buttons = buttons
        self.setStyleSheet("QPushButton {background:#25334b;color:white;border:1px solid #70809b;border-radius:7px;padding:8px 12px;} QPushButton:hover {background:#3b5275;}")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)
        self.duration_timer = QTimer(self)
        self.duration_timer.setSingleShot(True)
        self.duration_timer.timeout.connect(self._expired)
        self._load_media()
        self._place()

    movie = property(lambda self: self.media.movie)
    pixmap = property(lambda self: self.media.pixmap)
    svg = property(lambda self: self.media.svg)

    def _load_media(self):
        self._load_error = self.media.load()

    def _place(self):
        screens = QApplication.screens()
        screen = next((s for s in screens if s.name() == self.config.screen), None)
        if self.config.screen and screen is None:
            self._load_error = (self._load_error + "\n" if self._load_error else "") + f"Monitor '{self.config.screen}' is unavailable. Banner opened on the primary display; select a connected monitor in Banner settings."
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            return
        self.target_screen = screen
        area = screen.availableGeometry()
        position = self.config.position
        margin = 20
        x, y = area.x() + (area.width() - self.width()) // 2, area.y() + (area.height() - self.height()) // 2
        if "left" in position:
            x = area.left() + margin
        if "right" in position:
            x = area.right() - self.width() - margin + 1
        if "top" in position:
            y = area.top() + margin
        if "bottom" in position:
            y = area.bottom() - self.height() - margin + 1
        if position == "custom":
            x, y = area.x() + self.config.x, area.y() + self.config.y
        self.move(x, y)

    def open_banner(self):
        if self.config.fullscreen:
            self.show()
            if self.windowHandle() and hasattr(self, "target_screen"):
                self.windowHandle().setScreen(self.target_screen)
            self.showFullScreen()
        else:
            self.show()
        self.raise_()
        if not self.notification:
            self.activateWindow()
            self.setFocus()
        self.media.start()
        if self.config.duration > 0:
            self.duration_timer.start(min(2147483647, int(self.config.duration * 1000)))
        if self._load_error:
            self.error.emit(self._load_error)

    def _expired(self):
        if self.config.auto_action == "dismiss" and not self.preview:
            self.dismiss_requested.emit()
        else:
            self.close()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.notification:
            paint_banner(painter, self.rect(), self.config, self.media, self.alarm.message, tint=96)  # Alarm.message only, over the alarm's own banner media
            return
        text = "" if self.config.hide_text else (self.config.text or self.alarm.name + ("\n" + self.alarm.message if self.alarm.message else ""))
        paint_banner(painter, self.rect(), self.config, self.media, text, 58 if self._buttons_shown else 0, text_panel=True)

    def contextMenuEvent(self, event):
        if self.notification:
            return
        menu = QMenu(self)
        if not self.preview:
            menu.addAction("Stop sound", self.stop_requested.emit)
            menu.addAction("Snooze", self.snooze_requested.emit)
            menu.addAction("Dismiss occurrence", self.dismiss_requested.emit)
            menu.addSeparator()
        menu.addAction("Close banner", self.close)
        menu.exec(event.globalPos())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()  # Closing a window does not silently dismiss the occurrence.
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        if not self._closing:
            self._closing = True
            self.duration_timer.stop()
            self.media.stop()
            self.banner_closed.emit()
        self._cleaned = True
        event.accept()

"""Multi-image corner overlay: a separate frameless, translucent window that shows ONE user-imported image
(chosen by an event-driven carousel) flush against the bottom-right corner of the chosen monitor.

VISIBILITY RULE (the most important invariant of this module): the window is opened ONLY by
``CornerOverlay.handle_events`` receiving a genuine alarm activation (scheduled, Trigger Now, re-alert or snooze
expiry; never ``reason == "restore"``). ``refresh()``, settings changes, monitor changes, start-up and the
settings UI can only restyle / reposition a window that an alarm already opened; they can never open it.
Enabling the feature merely arms it.

Hover: the window accepts mouse input. While the cursor is over the image a small x button appears in its
top-right corner; clicking it closes the overlay only (the alarm, its sound and its banner are untouched).

It is independent of BannerWindow / BannerBackground, alarm audio and the playlist. Decoding and GIF animation
reuse BannerMedia; validation reuses media.validate_image. Only files the user imported are ever drawn; nothing
is generated, bundled, downloaded or substituted, and the original files are never modified.

Units: all sizes are Qt device-independent pixels (the same unit as QScreen.geometry()), so Windows display
scaling is respected. Placement uses QScreen.geometry() (never availableGeometry()):
    x = geometry.x() + geometry.width()  - overlay.width()
    y = geometry.y() + geometry.height() - overlay.height()
"""
from __future__ import annotations
import logging
import os
from dataclasses import asdict, dataclass
from types import SimpleNamespace
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QImageReader, QMovie, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QPushButton, QWidget
from .banner import BannerMedia
from ..carousel import Carousel
from ..models import Corner
from ..platform_windows import show_corner_window

MODES = ("original", "fit", "fill", "stretch")
MODE_LABELS = [("original", "Original size"), ("fit", "Fit (keep aspect ratio)"), ("fill", "Fill (crop to size)"), ("stretch", "Stretch")]
MAX_DIMENSION = 16384
MAX_TRIM_FRAMES = 1000


ORDERS = ("sequential", "shuffle")
MAX_CYCLE_SECONDS = 3600


def clean_images(values) -> list[str]:
    """Ordered, de-duplicated list of non-empty path strings."""
    result, seen = [], set()
    for value in values if isinstance(values, (list, tuple)) else []:
        text = str(value or "").strip()
        key = os.path.normcase(os.path.normpath(text)) if text else ""
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


@dataclass
class CornerConfig(Corner):
    """Corner settings of ONE alarm (fields come from models.Corner) plus validation. Nothing here is global."""

    @classmethod
    def from_corner(cls, corner) -> "CornerConfig":
        return cls(**asdict(corner)).normalized()

    def to_corner(self) -> Corner:
        return Corner(**asdict(self.normalized()))

    def normalized(self) -> "CornerConfig":
        def number(value, default, low, high):
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = default
            return max(low, min(high, value))
        defaults = CornerConfig()
        return CornerConfig(
            enabled=bool(self.enabled), images=clean_images(self.images),
            order=self.order if self.order in ORDERS else defaults.order, loop=bool(self.loop),
            cycle=bool(self.cycle), cycle_seconds=number(self.cycle_seconds, defaults.cycle_seconds, 1, MAX_CYCLE_SECONDS),
            width=number(self.width, defaults.width, 1, MAX_DIMENSION), height=number(self.height, defaults.height, 1, MAX_DIMENSION),
            keep_aspect=bool(self.keep_aspect), mode=self.mode if self.mode in MODES else defaults.mode,
            opacity=number(self.opacity, 100, 5, 100), monitor=str(self.monitor or "primary"),
            on_top=bool(self.on_top), trim=bool(self.trim))


@dataclass(frozen=True)
class Layout:
    win_w: int
    win_h: int
    tx: float  # where the (possibly larger, for Fill) image rectangle starts inside the window
    ty: float
    tw: float
    th: float


def compute_layout(src_w, src_h, mode, keep_aspect, box_w, box_h, max_w, max_h) -> Layout:
    """Window size and image rectangle. The window is always exactly as large as what it shows, so there is
    never unused transparent area between the picture and the screen corner.
    original: the image's own size.   fit: scaled to fit the width x height box, window shrinks to the picture.
    fill: window is the box, image covers it and is centre-cropped.   stretch: window is the box, aspect ignored
    (with 'maintain aspect ratio' on, stretch behaves like fit).   Never larger than the monitor."""
    src_w, src_h = max(1, src_w), max(1, src_h)
    box_w, box_h = max(1, min(box_w, max_w)), max(1, min(box_h, max_h))
    if mode == "fill":
        scale = max(box_w / src_w, box_h / src_h)
        tw, th = src_w * scale, src_h * scale
        return Layout(box_w, box_h, (box_w - tw) / 2, (box_h - th) / 2, tw, th)
    if mode == "stretch" and not keep_aspect:
        return Layout(box_w, box_h, 0, 0, box_w, box_h)
    if mode == "original":
        scale = min(1.0, max_w / src_w, max_h / src_h)
    else:  # fit, or stretch with aspect ratio kept
        scale = min(box_w / src_w, box_h / src_h)
    w, h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    return Layout(w, h, 0, 0, w, h)


def alpha_bounds(image: QImage) -> QRect | None:
    """Bounding rectangle of pixels with any opacity; None if the image is fully transparent."""
    full = QRect(0, 0, image.width(), image.height())
    if not image.hasAlphaChannel():
        return full
    alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
    if alpha.isNull():
        return full
    width, height, stride = alpha.width(), alpha.height(), alpha.bytesPerLine()
    data = bytes(alpha.constBits())
    top = bottom = None
    left, right = width, 0
    for y in range(height):
        row = data[y * stride:y * stride + width]
        stripped = row.lstrip(b"\x00")
        if not stripped:
            continue
        if top is None:
            top = y
        bottom = y
        left = min(left, width - len(stripped))
        right = max(right, len(row.rstrip(b"\x00")))
    if top is None:
        return None
    return QRect(left, top, right - left, bottom - top + 1)


def monitor_choices(saved="primary"):
    """[(key, label)] for the monitor selector. A saved monitor that is not connected stays listed."""
    app = QGuiApplication.instance()
    primary = app.primaryScreen()
    choices = [("primary", "Primary display")]
    for index, screen in enumerate(app.screens(), 1):
        g = screen.geometry()
        tag = " (primary)" if screen is primary else ""
        choices.append((screen.name(), f"Display {index}{tag}: {screen.name()} — {g.width()}×{g.height()} at ({g.x()}, {g.y()}), {round(screen.devicePixelRatio() * 100)}%"))
    if saved not in ("", "primary") and all(key != saved for key, _ in choices):
        choices.append((saved, f"Not connected: {saved} (primary display is used meanwhile)"))
    return choices


class CornerOverlay(QWidget):
    error = Signal(str)          # an image failed to decode/animate
    notice = Signal(str)         # non-fatal information, e.g. an imported file is unavailable
    shown = Signal(str)          # an image was put on screen (alarm activation or timed cycle)

    CLOSE_BUTTON_MAX = 24
    CLOSE_BUTTON_MARGIN = 4

    def __init__(self, repo, parent=None, rng=None):
        super().__init__(parent)
        self.repo = repo
        self.config = CornerConfig()  # config of the alarm whose picture is on screen (set per activation)
        self.alarm_id = ""
        self.carousel = None  # that alarm's own Carousel
        self._rng = rng
        self.media = None
        self.current_path = ""
        self.natural = QSize()
        self.trim = QRect()
        self.load_error = ""
        self.screen_in_use = None
        self._session_open = False   # True ONLY between a genuine alarm activation and its end (dismiss/snooze/x/exit)
        self._owners = set()         # occurrence ids whose activation opened the current session
        self._hover = False
        self._layout = None
        self._scaled = None
        self._cache_key = None
        self._problem = ""
        self._closed = False
        self._watched = set()
        self._flags_applied = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)  # hover is needed to reveal the x button
        self.setWindowTitle("Custom Alarm Clock corner image")
        self._sync_flags()
        self.close_button = QPushButton("×", self)
        self.close_button.setObjectName("cornerCloseButton")
        self.close_button.setToolTip("Close this picture (the alarm keeps going)")
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.close_overlay)
        self.close_button.hide()
        self.keep_on_top_timer = QTimer(self)
        self.keep_on_top_timer.setInterval(4000)
        self.keep_on_top_timer.timeout.connect(self._keep_on_top)
        self.cycle_timer = QTimer(self)  # optional timed cycling; only ever active during an open session
        self.cycle_timer.timeout.connect(self._cycle_tick)
        self.hover_guard = QTimer(self)  # runs only while the x is visible: guarantees it can never stay stuck
        self.hover_guard.setInterval(200)
        self.hover_guard.timeout.connect(self._hover_check)
        app = QGuiApplication.instance()
        app.screenAdded.connect(self._screens_changed)
        app.screenRemoved.connect(self._screens_changed)
        app.primaryScreenChanged.connect(self._screens_changed)
        for screen in app.screens():
            self._watch(screen)

    # ---- lifecycle -------------------------------------------------------------------------------
    def start(self):
        """Application start: nothing is loaded or shown. Each alarm carries its own configuration."""

    def shutdown(self):
        """Explicit application exit: stop timers and animation and destroy the window. Safe to call repeatedly."""
        if self._closed:
            return
        self._closed = True
        self._session_open = False
        self._owners.clear()
        for timer in (self.keep_on_top_timer, self.cycle_timer, self.hover_guard):
            timer.stop()
        self.hide()
        self._unload()
        self.close()
        self.deleteLater()

    # ---- alarm events: the ONLY way the overlay opens ----------------------------------------------
    def handle_events(self, events):
        """`events` is a list of (kind, occurrence_id, reason) tuples from Controller.corner_events.
        Genuine activations (scheduled, Trigger Now, re-alert, snooze expiry) open the overlay and advance the carousel
        exactly once for the whole batch; "restore" never does. Dismiss/snooze of an occurrence that owns the overlay
        closes it; unrelated occurrences do not."""
        if self._closed:
            return
        pending = []
        for kind, occurrence_id, reason in events:
            if kind == "activate":
                if reason != "restore":
                    pending.append(occurrence_id)
            elif kind in ("dismiss", "snooze"):
                self._owners.discard(occurrence_id)
                if occurrence_id in pending:
                    pending.remove(occurrence_id)
        shown = self._begin_activation(pending) if pending else False
        if not shown and self._session_open and not self._owners:
            self._end_session()

    def _begin_activation(self, occurrence_ids) -> bool:
        """One physical window, deterministic: the FIRST activation in the batch whose own alarm has Corner Image enabled with
        images wins. Only that alarm's carousel advances; other alarms in the batch are untouched. Alarms with it disabled or
        with no images show nothing (and leave any picture another alarm is showing alone)."""
        for occurrence_id in occurrence_ids:
            occurrence = self.repo.occurrence(occurrence_id)
            if occurrence is None:
                continue
            config = CornerConfig.from_corner(occurrence.snapshot.corner)
            if config.enabled and config.images:
                return self._show_for(occurrence_id, occurrence.snapshot.id, config)
        return False

    def _show_for(self, occurrence_id, alarm_id, c) -> bool:
        carousel = Carousel(self.repo, self._rng, alarm_id)
        path = carousel.select(c.images, c.order, c.loop)  # one activation == one selection, for this alarm only
        message = self._load_media(path, c.trim) if path else "No image."
        if message:
            self.load_error = message
            logging.error("Corner image unavailable: %s", message)
            self.notice.emit("Corner image skipped: " + message.splitlines()[0])
            self._end_session()  # never substitute another picture
            return False
        self.load_error = ""
        self.config, self.alarm_id, self.carousel = c, alarm_id, carousel
        self.current_path = path
        self._owners = {occurrence_id}
        self._session_open = True
        self.refresh()
        self.cycle_timer.stop()
        self._update_cycle_timer()  # restart the interval for this activation
        self.shown.emit(path)
        return True

    def close_overlay(self):
        """Close the picture without changing the alarm or its audio."""
        self._end_session()

    def closeEvent(self, event):
        self._end_session()
        event.accept()

    def _end_session(self):
        self._session_open = False
        self._owners.clear()
        for timer in (self.keep_on_top_timer, self.cycle_timer):
            timer.stop()
        self._set_hover(False)
        self.hide()
        self._unload()
        self.current_path = ""

    @property
    def session_open(self) -> bool:
        return self._session_open

    def is_showing(self) -> bool:
        return self._session_open and self.isVisible()

    # ---- helpers for the per-alarm settings tab ----------------------------------------------------
    @staticmethod
    def probe_size(path) -> QSize:
        """Size of an imported file without keeping it loaded (used by the settings UI for aspect ratios)."""
        try:
            if str(path).lower().endswith(".svg"):
                renderer = QSvgRenderer(str(path))
                size = renderer.defaultSize() if renderer.isValid() else QSize()
                return size if not size.isEmpty() else renderer.viewBox().size()
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            return reader.size()
        except Exception:
            return QSize()

    def original_size(self) -> QSize:
        """Size of the picture currently on screen (after optional trimming); invalid when nothing is loaded."""
        return self.trim.size() if self.media is not None else QSize()

    # ---- loading ---------------------------------------------------------------------------------
    def _unload(self):
        if self.media is not None:
            self.media.stop()
            self.media.deleteLater()
        self.media = None
        self.natural = QSize()
        self.trim = QRect()
        self._scaled = None
        self._cache_key = None
        self._layout = None

    def _load_media(self, path, trim) -> str:
        """Decode `path` with BannerMedia. Returns '' on success. On failure the current image is kept."""
        if not path:
            return "No image."
        media = BannerMedia(SimpleNamespace(path=path, gif_speed=100, loop_gif=True, fit="fit"), self)
        message = media.load()
        natural = QSize() if message else self._natural_size(media)
        if not message and natural.isEmpty():
            message = f"The image has no visible size: {path}"
        if message:
            media.deleteLater()
            return message
        self._unload()
        self.media, self.natural = media, natural
        media.frame.connect(self._frame)
        media.error.connect(self._media_error)
        self.trim = self._compute_trim() if trim else QRect(QPoint(0, 0), natural)
        if self.isVisible():
            media.start()
        return ""

    @staticmethod
    def _natural_size(media) -> QSize:
        if media.svg is not None:
            size = media.svg.defaultSize()
            return size if not size.isEmpty() else media.svg.viewBox().size()
        if media.movie is not None:
            media.movie.jumpToFrame(0)
            pixmap = media.movie.currentPixmap()
            return pixmap.size() if not pixmap.isNull() else media.movie.frameRect().size()
        return media.pixmap.size()

    def _compute_trim(self) -> QRect:
        """Opaque-pixel bounds, in memory only; the file is never touched."""
        full = QRect(QPoint(0, 0), self.natural)
        try:
            if self.media.svg is not None:
                image = QImage(self.natural, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                painter = QPainter(image)
                self.media.svg.render(painter, QRectF(image.rect()))
                painter.end()
                bounds = alpha_bounds(image)
            elif self.media.movie is not None:
                bounds = self._gif_bounds()
            else:
                bounds = alpha_bounds(self.media.pixmap.toImage())
        except Exception:
            logging.exception("Corner image trimming failed")
            bounds = None
        return bounds if bounds is not None and not bounds.isEmpty() else full

    def _gif_bounds(self):
        """Union of every frame's opaque bounds, so an animation is not clipped on any frame."""
        movie = QMovie(self.media.config.path)
        count = movie.frameCount()
        if not movie.isValid() or count <= 0 or count > MAX_TRIM_FRAMES:
            self.notice.emit("Transparent-edge trimming was skipped for this GIF (frame count unavailable or too large).")
            return None
        union = None
        for index in range(count):
            if not movie.jumpToFrame(index):
                break
            bounds = alpha_bounds(movie.currentImage())
            if bounds is not None:
                union = bounds if union is None else union.united(bounds)
        return union

    def _frame(self):
        if not self._closed:
            self.update()

    def _media_error(self, message):
        self.load_error = message
        self.error.emit(message)

    # ---- optional timed cycling (only while an alarm-opened session is visible) --------------------
    def _update_cycle_timer(self):
        c = self.config
        exhausted = not c.loop and self.carousel is not None and self.carousel.next_index >= len(c.images)
        if self._session_open and not self._closed and c.cycle and len(c.images) > 1 and not exhausted:
            interval = c.cycle_seconds * 1000
            if self.cycle_timer.interval() != interval:
                self.cycle_timer.setInterval(interval)
            if not self.cycle_timer.isActive():
                self.cycle_timer.start()
        else:
            self.cycle_timer.stop()

    def _cycle_tick(self):
        c = self.config
        if self._closed or not self._session_open or not self.isVisible() or not c.cycle or not c.images:
            self.cycle_timer.stop()
            return
        path = self.carousel.select(c.images, c.order, c.loop)
        if not path or path == self.current_path:  # finite mode at its last image: nothing new to show
            return
        message = self._load_media(path, c.trim)
        if message:  # keep the current picture; nothing is substituted
            logging.error("Corner image unavailable: %s", message)
            self.notice.emit("Corner image skipped: " + message.splitlines()[0])
            return
        self.current_path = path
        self.refresh()
        self.shown.emit(path)

    # ---- window state ----------------------------------------------------------------------------
    def _wanted_flags(self):
        # NOTE: deliberately NOT WindowTransparentForInput: the overlay must receive hover/clicks for its x button.
        W = Qt.WindowType
        flags = W.Tool | W.FramelessWindowHint | W.NoDropShadowWindowHint | W.WindowDoesNotAcceptFocus
        if self.config.on_top:
            flags |= W.WindowStaysOnTopHint
        return flags

    def _sync_flags(self):
        # Tool: no taskbar button. Frameless, no shadow, never takes focus. The window is only as large as its picture,
        # so it never blocks more of the screen than the image itself.
        wanted = self._wanted_flags()
        if self._flags_applied != wanted:
            self._flags_applied = wanted
            self.setWindowFlags(wanted)  # hides a visible window; refresh() shows it again if a session is open

    def refresh(self):
        """Make flags, opacity, size and position match the configuration for a session an alarm already opened.
        This method can NEVER open the overlay: with no open session it only keeps the hidden window hidden."""
        if self._closed:
            return
        c = self.config
        if self._session_open and (not c.enabled or not c.images or self.media is None):
            self._end_session()  # disabled / emptied while showing
            return
        if not self._session_open:
            if self.isVisible():
                self.hide()
            self.keep_on_top_timer.stop()
            self.cycle_timer.stop()
            return
        self._sync_flags()
        self.setWindowOpacity(c.opacity / 100)
        if not self.place():
            self._end_session()
            return
        if not self.isVisible():
            self.show()
        self._present()
        self._settle()
        if c.on_top:
            self.keep_on_top_timer.start()
        else:
            self.keep_on_top_timer.stop()
        self._update_cycle_timer()

    def _resolve_screen(self):
        app = QGuiApplication.instance()
        saved = self.config.monitor
        if saved in ("", "primary"):
            return app.primaryScreen(), ""
        for screen in app.screens():
            if screen.name() == saved:
                return screen, ""
        return app.primaryScreen(), (f"The selected monitor ({saved}) is not connected. The image is temporarily on the primary display; "
                                     "your saved monitor choice is unchanged and is used again when it reconnects.")

    def place(self) -> bool:
        """Size the window to its picture and put its bottom-right corner on the monitor's bottom-right corner."""
        if self._closed or self.media is None:
            return False
        screen, problem = self._resolve_screen()
        self._set_problem(problem)
        if screen is None:
            return False
        g = screen.geometry()  # full monitor rectangle in device-independent coordinates; never availableGeometry()
        c = self.config
        source = self.trim.size()
        layout = compute_layout(source.width(), source.height(), c.mode, c.keep_aspect, c.width, c.height, g.width(), g.height())
        self._layout = layout
        self.screen_in_use = screen
        self.setFixedSize(layout.win_w, layout.win_h)
        self._place_close_button()
        self._rebuild_cache(screen.devicePixelRatio())
        x = g.x() + g.width() - layout.win_w
        y = g.y() + g.height() - layout.win_h
        if self.windowHandle() is None:
            self.create()
        if self.windowHandle() is not None:
            self.windowHandle().setScreen(screen)
        self.setGeometry(x, y, layout.win_w, layout.win_h)
        self.update()
        return True

    def _settle(self):
        # Qt may rescale a window right after it lands on a screen with different scaling; place once more.
        QTimer.singleShot(0, self._replace_if_visible)
        QTimer.singleShot(250, self._replace_if_visible)

    def _replace_if_visible(self):
        if not self._closed and self._session_open and self.place():
            self._present()

    def _present(self):
        """Present the current session after Qt creates/recreates its native surface."""
        if self._closed or not self._session_open or self.media is None:
            return
        if not self.isVisible():
            self.show()
        if self.config.on_top:
            self.raise_()
        try:
            # Offscreen Qt uses synthetic IDs, even when its host OS is Windows.
            if QGuiApplication.platformName() == "windows":
                show_corner_window(int(self.winId()), self.config.on_top)
        except OSError as exc:
            message = f"Cannot show corner image: {exc}"
            if message != self.load_error:
                self.load_error = message
                logging.error(message)
                self.error.emit(message)
        self.update()

    def _set_problem(self, text):
        if text != self._problem:
            self._problem = text
            if text:
                logging.warning(text)
                self.notice.emit(text)
            else:
                self.notice.emit("The corner image is back on the selected monitor.")

    def _keep_on_top(self):
        if not self._closed and self._session_open and self.isVisible() and self.config.on_top:
            self.raise_()

    # ---- hover x button ---------------------------------------------------------------------------
    def cursor_position(self) -> QPoint:
        return QCursor.pos()

    def _cursor_inside(self) -> bool:
        return self.isVisible() and self.rect().contains(self.mapFromGlobal(self.cursor_position()))

    def _place_close_button(self):
        """Top-right corner, inside the overlay's own bounds (so never beyond the monitor edge)."""
        w, h = self.width(), self.height()
        side = min(w, h, max(12, min(self.CLOSE_BUTTON_MAX, min(w, h) // 2)))
        margin = self.CLOSE_BUTTON_MARGIN if min(w, h) >= side + 2 * self.CLOSE_BUTTON_MARGIN else 0
        self.close_button.setFixedSize(side, side)
        self.close_button.move(max(0, w - side - margin), margin)
        border = 1 if side >= 4 else 0
        self.close_button.setStyleSheet(
            f"QPushButton#cornerCloseButton {{ background: rgba(18,24,38,215); color: #edf2ff; border: {border}px solid rgba(237,242,255,110);"
            f" border-radius: {side // 2}px; font-size: {max(1, side - 8)}px; font-weight: 700; padding: 0; min-width: 0; min-height: 0; }}"
            f" QPushButton#cornerCloseButton:hover {{ background: rgba(214,60,80,240); }}"
            f" QPushButton#cornerCloseButton:pressed {{ background: rgba(160,36,56,245); }}")

    def _set_hover(self, on: bool):
        on = bool(on) and self._session_open and self.isVisible()
        if on == self._hover:
            return
        self._hover = on
        self.close_button.setVisible(on)
        if on:
            self.close_button.raise_()
            self.hover_guard.start()
        else:
            self.hover_guard.stop()

    def _hover_check(self):
        if not self._cursor_inside():
            self._set_hover(False)

    def enterEvent(self, event):
        self._set_hover(True)
        super().enterEvent(event)

    def mouseMoveEvent(self, event):
        self._set_hover(True)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        # Qt also sends Leave when the cursor moves onto the child x button; only hide when truly outside.
        if not self._cursor_inside():
            self._set_hover(False)
        super().leaveEvent(event)

    # ---- display changes -------------------------------------------------------------------------
    def _watch(self, screen):
        if screen not in self._watched:
            self._watched.add(screen)
            screen.geometryChanged.connect(self._screen_geometry)

    def _screens_changed(self, *_):
        if self._closed:
            return
        current = QGuiApplication.instance().screens()
        self._watched &= set(current)
        for screen in current:
            self._watch(screen)
        QTimer.singleShot(0, self.refresh)  # repositions an open overlay; can never open one

    def _screen_geometry(self, *_):
        QTimer.singleShot(0, self.refresh)

    # ---- painting --------------------------------------------------------------------------------
    def _rebuild_cache(self, dpr):
        """Static images are pre-scaled once (smooth, device-resolution) instead of on every paint."""
        if self.media is None or self.media.pixmap is None or self._layout is None:
            self._scaled = self._cache_key = None
            return
        layout = self._layout
        key = (self.media.pixmap.cacheKey(), self.trim.getRect(), layout, dpr)
        if key == self._cache_key:
            return
        cropped = self.media.pixmap.copy(self.trim)
        scaled = cropped.scaled(max(1, round(layout.tw * dpr)), max(1, round(layout.th * dpr)),
                                Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        self._scaled = scaled
        self._cache_key = key

    def paintEvent(self, event):
        if self.media is None or self._layout is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # Practically invisible (alpha 1/255) fill so that Windows hit-tests the whole picture area, including its
        # fully transparent pixels; otherwise hover would be lost over transparent parts of a PNG/GIF.
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
        L = self._layout
        target = QRectF(L.tx, L.ty, L.tw, L.th)
        media = self.media
        if media.svg is not None:
            sx, sy = target.width() / self.trim.width(), target.height() / self.trim.height()
            media.svg.render(painter, QRectF(target.x() - self.trim.x() * sx, target.y() - self.trim.y() * sy,
                                             self.natural.width() * sx, self.natural.height() * sy))
        elif media.movie is not None:
            pixmap = media.movie.currentPixmap()
            if not pixmap.isNull():
                painter.drawPixmap(target, pixmap, QRectF(self.trim))
        elif self._scaled is not None:
            painter.drawPixmap(QPointF(target.x(), target.y()), self._scaled)

    def showEvent(self, event):
        if self.media is not None:
            if self.media._paused:
                self.media.resume()
            else:
                self.media.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._set_hover(False)
        if self.media is not None:
            self.media.pause()
        super().hideEvent(event)

from PySide6.QtCore import Qt, QRect, QSize, QPoint
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPalette
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QLabel, QSpinBox, QDoubleSpinBox,
                             QComboBox, QCheckBox, QPushButton, QColorDialog, QDialog, QVBoxLayout, QPlainTextEdit, QSizePolicy, QLayout,
                             QStyle, QStyleOptionButton, QStylePainter)


class _WrappingRow(QLayout):
    """Keep each control at its readable size and wrap whole controls onto new lines."""
    def __init__(self, parent):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(6)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), False)

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        return size

    def sizeHint(self):
        return self.minimumSize()

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, True)

    def _arrange(self, rect, apply):
        x, y, line_height = rect.x(), rect.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            size = item.sizeHint().expandedTo(item.minimumSize())
            width = min(size.width(), max(item.minimumSize().width(), rect.width()))
            height = max(item.minimumSize().height(), item.heightForWidth(width)) if item.hasHeightForWidth() else size.height()
            if line_height and x + width > rect.x() + rect.width():
                x, y, line_height = rect.x(), y + line_height + self.spacing(), 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), QSize(width, height)))
            x += width + self.spacing()
            line_height = max(line_height, height)
        return y + line_height - rect.y()


def responsive_row(*widgets, stretch=False):
    result = QWidget()
    layout = _WrappingRow(result)
    for widget in widgets:
        layout.addWidget(widget)
    return result


def row(*widgets, stretch=False):
    result = QWidget()
    layout = QHBoxLayout(result)
    layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        if isinstance(widget, QLabel):
            widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        layout.addWidget(widget)
    if stretch:
        layout.addStretch()
    return result


def spin(value, minimum=0, maximum=999, suffix="", decimals=None):
    result = QDoubleSpinBox() if decimals is not None else QSpinBox()
    if decimals is not None:
        result.setDecimals(decimals)
    result.setRange(minimum, maximum)
    result.setValue(value)
    result.setSuffix(suffix)
    return result


def combo(options, value):
    result = QComboBox()
    for key, label in options:
        result.addItem(label, key)
    index = result.findData(value)
    result.setCurrentIndex(max(0, index))
    return result


class WrappingCheckBox(QCheckBox):
    """A native checkbox whose full label can take multiple lines."""
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def _text_rect(self):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        return self.style().subElementRect(QStyle.SubElement.SE_CheckBoxContents, option, self)

    def _label_spacing(self):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.rect = QRect(0, 0, 1000, super().sizeHint().height())
        contents = self.style().subElementRect(QStyle.SubElement.SE_CheckBoxContents, option, self)
        return max(0, option.rect.width() - contents.width())

    def minimumSizeHint(self):
        metrics = self.fontMetrics()
        word_width = max((metrics.horizontalAdvance(word) for word in self.text().split()), default=0)
        return QSize(word_width + self._label_spacing(), super().sizeHint().height())

    def heightForWidth(self, width):
        metrics = self.fontMetrics()
        bounds = metrics.boundingRect(QRect(0, 0, max(1, width - self._label_spacing()), 100000),
                                      int(Qt.TextFlag.TextWordWrap), self.text())
        extra = max(0, super().sizeHint().height() - metrics.height())
        return max(super().sizeHint().height(), bounds.height() + extra)

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        text_rect = self._text_rect()
        option.text = ""
        painter.drawControl(QStyle.ControlElement.CE_CheckBox, option)
        flags = int(Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignVCenter
                    | Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextShowMnemonic)
        self.style().drawItemText(painter, text_rect, flags, option.palette,
                                  self.isEnabled(), self.text(), QPalette.ColorRole.WindowText)


def check(text, checked, *, wrap=False):
    result = WrappingCheckBox(text) if wrap else QCheckBox(text)
    result.setChecked(checked)
    return result


def note(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setObjectName("hint")
    return label


class ColorButton(QPushButton):
    def __init__(self, value):
        super().__init__()
        self.value = value
        self.clicked.connect(self.choose)
        self._refresh()

    def _refresh(self):
        self.setText(self.value)
        self.setStyleSheet(f"border-left: 18px solid {self.value};")

    def choose(self):
        color = QColorDialog.getColor(QColor(self.value), self, "Choose color")
        if color.isValid():
            self.value = color.name()
            self._refresh()


class DurationControl(QWidget):
    def __init__(self, seconds, maximum=999999):
        super().__init__()
        unit = 3600 if seconds % 3600 == 0 else (60 if seconds % 60 == 0 else 1)
        self.amount = spin(seconds // unit, 1, maximum)
        self.unit = combo([(1, "Seconds"), (60, "Minutes"), (3600, "Hours")], unit)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.amount)
        layout.addWidget(self.unit)

    def seconds(self):
        return self.amount.value() * self.unit.currentData()


class ErrorDialog(QDialog):
    def __init__(self, title, message, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(650, 280)
        layout = QVBoxLayout(self)
        text = QPlainTextEdit(message)
        text.setReadOnly(True)
        layout.addWidget(text)
        button = QPushButton("Close")
        button.clicked.connect(self.accept)
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignRight)


def application_icon():
    # Ordinary programmatic application icon; never used as alarm content.
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#7696ff"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 16, 16)
    painter.setPen(QPen(QColor("#111827"), 4))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(13, 13, 38, 38)
    painter.drawLine(32, 21, 32, 33)
    painter.drawLine(32, 33, 41, 37)
    painter.end()
    return QIcon(pixmap)

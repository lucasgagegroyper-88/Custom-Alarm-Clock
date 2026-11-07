"""Larger font metrics must wrap controls, rather than clip or widen the editor."""
import pytest
from PySide6.QtWidgets import QWidget
from alarmclock.controller import Controller
from alarmclock.ui.common import _WrappingRow
from alarmclock.ui.editor import AlarmEditor
from alarmclock.ui.theme import apply_theme


@pytest.mark.parametrize("width,height", [(850, 770), (680, 540)])
def test_large_font_rows_wrap_without_clipping(app, repo, width, height):
    old_style = app.styleSheet()
    apply_theme(app, "dark")
    app.setStyleSheet(app.styleSheet().replace("10pt", "18pt"))
    controller = Controller(repo)
    editor = AlarmEditor(None, controller)
    editor.kind.setCurrentIndex(editor.kind.findData("weekly"))
    editor.corner.monitor.addItem("Display with a very long Windows monitor description " * 8, "test-display")
    try:
        editor.resize(width, height)
        editor.show()
        for index in range(editor.tabs.count()):
            editor.tabs.setCurrentIndex(index)
            app.processEvents()
            page = editor.tabs.currentWidget()
            assert page.horizontalScrollBar().maximum() == 0, editor.tabs.tabText(index)
            assert editor.size().width() == width and editor.size().height() == height
            for row in page.widget().findChildren(QWidget):
                layout = row.layout()
                if not row.isVisibleTo(page.widget()) or not isinstance(layout, _WrappingRow):
                    continue
                rectangles = [layout.itemAt(i).geometry() for i in range(layout.count())
                              if not layout.itemAt(i).isEmpty()]
                assert all(row.rect().contains(rect) for rect in rectangles)
                for i, rect in enumerate(rectangles):
                    assert all(not rect.intersects(other) for other in rectangles[i + 1:])
    finally:
        editor.reject()
        editor.deleteLater()
        controller.shutdown()
        controller.deleteLater()
        app.setStyleSheet(old_style)

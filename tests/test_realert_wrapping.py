"""Re-alert checkbox text must wrap even when its native single-line hint is wide."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget
from alarmclock.controller import Controller
from alarmclock.ui.editor import AlarmEditor
from alarmclock.ui.theme import apply_theme


@pytest.mark.parametrize("width,height", [(850, 770), (680, 540)])
@pytest.mark.parametrize("whole_page", [False, True])
def test_wide_realert_labels_fit_without_overflow(app, repo, width, height, whole_page):
    old_style = app.styleSheet()
    apply_theme(app, "dark")
    app.setStyleSheet(app.styleSheet().replace("10pt", "18pt"))
    controller = Controller(repo)
    editor = AlarmEditor(None, controller)
    boxes = [editor.restart_audio, editor.reopen_banner, editor.keep_banner, editor.realert_infinite]
    try:
        for box in editor.tabs.widget(4).widget().findChildren(QWidget) if whole_page else boxes:
            font = box.font()
            font.setStretch(225)
            box.setFont(font)
        editor.resize(width, height)
        editor.show()
        for index in range(editor.tabs.count()):
            editor.tabs.setCurrentIndex(index)
            app.processEvents()
            page = editor.tabs.currentWidget()
            assert page.horizontalScrollBar().maximum() == 0, editor.tabs.tabText(index)
            assert editor.size().width() == width and editor.size().height() == height
        for box in boxes:
            assert box.hasHeightForWidth()
            assert box.height() >= box.heightForWidth(box.width())
            assert box.parentWidget().rect().contains(box.geometry())
            before = box.isChecked()
            box.setFocus()
            QTest.keyClick(box, Qt.Key.Key_Space)
            assert box.isChecked() != before
        result = editor.gather()
        assert result.realert.restart_audio == editor.restart_audio.isChecked()
        assert result.realert.reopen_banner == editor.reopen_banner.isChecked()
        assert result.realert.keep_banner == editor.keep_banner.isChecked()
    finally:
        editor.reject()
        editor.deleteLater()
        controller.shutdown()
        controller.deleteLater()
        app.setStyleSheet(old_style)

"""Print per-tab geometry on the current Qt platform; exit nonzero on overflow."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from alarmclock.controller import Controller
from alarmclock.storage import Repository
from alarmclock.ui.editor import AlarmEditor
from alarmclock.ui.theme import apply_theme


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--font-points", type=int, help="Optional larger-font stress check")
    args = parser.parse_args()
    app = QApplication([])
    apply_theme(app, "dark")
    if args.font_points:
        app.setStyleSheet(app.styleSheet().replace("10pt", f"{args.font_points}pt"))
    repo = Repository(":memory:")
    controller = Controller(repo)
    editor = AlarmEditor(None, controller)
    editor.show()
    failed = False
    try:
        for width, height in [(850, 770), (680, 540)]:
            editor.resize(width, height)
            for index in range(editor.tabs.count()):
                editor.tabs.setCurrentIndex(index)
                app.processEvents()
                page = editor.tabs.currentWidget()
                overflow = page.horizontalScrollBar().maximum()
                size = editor.size()
                print(f"{width}x{height} | {editor.tabs.tabText(index)} | "
                      f"viewport={page.viewport().width()} content={page.widget().size().width()} "
                      f"minimum={page.widget().minimumSizeHint().width()} horizontal_max={overflow} "
                      f"dialog={size.width()}x{size.height()}")
                failed |= bool(overflow or size.width() != width or size.height() != height)
    finally:
        editor.reject()
        editor.deleteLater()
        controller.shutdown()
        controller.deleteLater()
        app.processEvents()
        repo.close()
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

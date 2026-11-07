import os
import sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform != "win32":
    os.environ.setdefault("QT_AUDIO_BACKEND", "pulseaudio")
import pytest
from PySide6.QtWidgets import QApplication
from alarmclock.storage import Repository

@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application

@pytest.fixture
def repo():
    repository = Repository(":memory:")
    yield repository
    repository.close()

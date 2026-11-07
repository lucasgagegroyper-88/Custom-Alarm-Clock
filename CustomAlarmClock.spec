# Build on Windows for CustomAlarmClock.exe. Linux builds are development smoke tests only.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, copy_metadata
from PyInstaller.utils.hooks.qt import pyside6_library_info
from PySide6.QtCore import QLibraryInfo
import PySide6

root = Path(SPECPATH)
binaries = []
for category in ('platforms', 'imageformats', 'iconengines', 'multimedia', 'styles'):
    collected = pyside6_library_info.collect_plugins(category)
    if not collected and category in ('platforms', 'imageformats', 'multimedia'):
        raise RuntimeError(f'Missing required Qt {category} plugins. Reinstall requirements.txt.')
    binaries += collected

# Standard PyInstaller Qt hooks discover linked DLLs. Also collect the wheel's
# FFmpeg DLLs explicitly so qffmpegmediaplugin can load its codec dependencies.
if sys.platform == 'win32':
    roots = {Path(PySide6.__file__).resolve().parent,
             Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.BinariesPath))}
    for directory in roots:
        for pattern in ('*avcodec*.dll', '*avformat*.dll', '*avutil*.dll', '*swresample*.dll', '*swscale*.dll'):
            for dll in directory.glob(pattern):
                binaries.append((str(dll), 'PySide6'))

datas = collect_data_files('tzdata')
for package in ('PySide6_Essentials', 'PySide6_Addons', 'shiboken6', 'tzlocal', 'tzdata'):
    datas += copy_metadata(package)
datas += [(str(root / 'LICENSE'), 'licenses'), (str(root / 'THIRD_PARTY_NOTICES.md'), 'licenses')]

a = Analysis(
    [str(root / 'main.py')],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=['PySide6.QtSvg', 'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'PySide6.QtNetwork', 'tzdata', 'tzlocal.win32'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQml', 'PySide6.QtQuick', 'tkinter', 'pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='CustomAlarmClock', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None,
    icon=str(root / 'resources' / 'CustomClock.ico') if sys.platform == 'win32' else None,
    version=str(root / 'windows_version.txt') if sys.platform == 'win32' else None,
)

"""Check Qt plugins and registered FFmpeg decoders without creating/playing media."""
import argparse
import ctypes
import os
from pathlib import Path
import sys
import PySide6
from PySide6.QtCore import QCoreApplication, QLibraryInfo, qVersion
from PySide6.QtGui import QImageReader
from PySide6.QtMultimedia import QMediaFormat


def load_ffmpeg(stem):
    directories = [Path(PySide6.__file__).resolve().parent,
                   Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.BinariesPath)),
                   Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.LibrariesPath))]
    handles = []
    if sys.platform == 'win32':
        for directory in directories:
            if directory.is_dir():
                handles.append(os.add_dll_directory(str(directory)))
    patterns = [f'{stem}*.dll', f'lib{stem}*.dll'] if sys.platform == 'win32' else [f'lib{stem}.so*', f'lib{stem}*.dylib']
    failures = []
    for directory in directories:
        for pattern in patterns:
            for path in sorted(directory.glob(pattern)):
                try:
                    return ctypes.CDLL(str(path)), handles
                except OSError as exc:
                    failures.append(str(exc))
    raise RuntimeError(f'Cannot load the Qt wheel\'s {stem} library: ' + '; '.join(failures))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--windows', action='store_true')
    args = parser.parse_args()
    if args.windows and sys.platform != 'win32':
        raise SystemExit('This build check must run on Windows.')
    _app = QCoreApplication([])  # Keep the Qt application alive through plugin checks.
    print('Python:', sys.version.split()[0], '| Qt:', qVersion())
    formats = {bytes(v).decode().lower() for v in QImageReader.supportedImageFormats()}
    required = {'gif', 'png', 'jpeg', 'webp', 'bmp', 'tiff', 'ico', 'svg'}
    missing = required - formats
    print('Available image formats:', ', '.join(sorted(formats)))
    if missing:
        raise SystemExit('Missing required image plugins: ' + ', '.join(sorted(missing)))
    plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
    if args.windows and not (plugins / 'platforms' / 'qwindows.dll').is_file():
        raise SystemExit('Missing qwindows.dll platform plugin')
    multimedia = plugins / 'multimedia'
    if not multimedia.is_dir() or not any(multimedia.iterdir()):
        raise SystemExit('Missing Qt multimedia backend plugins')
    # Let Qt initialize its backend and load dependent crypto/runtime libraries.
    # Inspect FFmpeg registrations directly: QMediaFormat's container list is
    # not a complete list of the raw audio streams accepted by QMediaPlayer.
    QMediaFormat().supportedFileFormats(QMediaFormat.ConversionMode.Decode)
    codec, codec_dirs = load_ffmpeg('avcodec')
    codec.avcodec_find_decoder_by_name.argtypes = [ctypes.c_char_p]
    codec.avcodec_find_decoder_by_name.restype = ctypes.c_void_p
    for label, candidates in [('MP3', [b'mp3float', b'mp3']), ('FLAC', [b'flac']), ('Ogg Vorbis', [b'vorbis']), ('WAV PCM', [b'pcm_s16le'])]:
        if not any(codec.avcodec_find_decoder_by_name(name) for name in candidates):
            raise SystemExit(f'Missing {label} decoder in the Qt FFmpeg library')
        print('Registered decoder:', label)
    container, container_dirs = load_ffmpeg('avformat')
    container.av_find_input_format.argtypes = [ctypes.c_char_p]
    container.av_find_input_format.restype = ctypes.c_void_p
    for name in [b'mp3', b'flac', b'ogg', b'wav']:
        if not container.av_find_input_format(name):
            raise SystemExit(f'Missing container reader: {name.decode()}')
    print('Container readers: MP3, FLAC, Ogg, WAV')
    print('Runtime checks passed. Actual user media and Windows hardware checks are in docs/WINDOWS_ACCEPTANCE.md.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

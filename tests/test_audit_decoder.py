"""Real Qt/FFmpeg decoding without opening an audio output device."""
import wave

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioDecoder
from PySide6.QtTest import QTest


def test_real_qt_decoder_reads_temporary_pcm_wav(app, tmp_path):
    path = tmp_path / "音声 test only.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 800)
    decoder = QAudioDecoder()
    frames, formats, done = [], [], []
    def read_buffer():
        buffer = decoder.read()
        frames.append(buffer.frameCount())
        formats.append((buffer.format().sampleRate(), buffer.format().channelCount()))
    decoder.bufferReady.connect(read_buffer)
    decoder.finished.connect(lambda: done.append(True))
    try:
        decoder.setSource(QUrl.fromLocalFile(str(path)))
        decoder.start()
        for _ in range(200):
            if done or decoder.errorString():
                break
            QTest.qWait(20)
        assert done == [True], decoder.errorString()
        assert sum(frames) == 800 and all(value == (8000, 1) for value in formats)
        assert not decoder.errorString()
    finally:
        decoder.stop()
        decoder.deleteLater()

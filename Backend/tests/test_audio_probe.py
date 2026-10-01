from __future__ import annotations

import sys
from types import SimpleNamespace


def test_probe_audio_uses_tinytag_when_soundfile_cannot_read_container(tmp_path, monkeypatch):
    from app.core import audio_probe

    audio = tmp_path / "clip.m4a"
    audio.write_bytes(b"container placeholder")
    monkeypatch.setattr(audio_probe, "_probe_with_soundfile", lambda _: None)
    monkeypatch.setitem(
        sys.modules,
        "tinytag",
        SimpleNamespace(TinyTag=SimpleNamespace(
            get=lambda _: SimpleNamespace(duration=1.25, samplerate=44_100, channels=2)
        )),
    )

    def no_ffprobe(*args, **kwargs):
        raise AssertionError("ffprobe must not be required for supported containers")

    monkeypatch.setattr(audio_probe.subprocess, "run", no_ffprobe)
    assert audio_probe.probe_audio(audio) == (1.25, 44_100, 2)

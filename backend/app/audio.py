"""Utilidades de audio basadas en FFmpeg."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


class AudioError(RuntimeError):
    pass


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise AudioError(f"No se encontró {cmd[0]}; instala FFmpeg.") from e
    except subprocess.CalledProcessError as e:
        raise AudioError(e.stderr.strip().splitlines()[-1] if e.stderr else str(e)) from e


def probe_duration(path: Path) -> float:
    out = _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)]
    ).stdout
    try:
        return float(json.loads(out)["format"]["duration"])
    except (KeyError, ValueError, TypeError) as e:
        raise AudioError("No se pudo leer la duración del audio.") from e


def to_wav16k(src: Path, dst: Path) -> Path:
    """Convierte cualquier audio o vídeo a WAV mono de 16 kHz (lo que esperan los modelos)."""
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(dst)])
    return dst


def cut_clip(src: Path, dst: Path, start: float, end: float) -> Path:
    """Extrae un fragmento como MP3, con fundido corto para que no corte en seco."""
    dur = max(0.1, end - start)
    fade = min(0.15, dur / 4)
    _run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
            "-vn", "-ac", "1",
            "-af", f"afade=t=in:d={fade:.3f},afade=t=out:st={dur - fade:.3f}:d={fade:.3f}",
            "-codec:a", "libmp3lame", "-q:a", "4",
            str(dst),
        ]
    )
    return dst

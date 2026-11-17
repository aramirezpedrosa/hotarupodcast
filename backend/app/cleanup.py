"""Limpieza de audio: reduce el ruido, sobre todo en los huecos sin voz, sin silenciarlos.

Cadena de procesado (todo en streaming, sin cargar el episodio entero en memoria):

1. FFmpeg decodifica el original y aplica un filtro paso alto (quita retumbe por
   debajo de 70 Hz) y un reductor de ruido espectral (afftdn) que sigue el ruido de
   fondo. Esa reducción afecta también a la voz, pero con poca fuerza.
2. En Python se aplica una "puerta suave": fuera de los tramos de voz el nivel baja
   varios dB más, con rampas para que el fondo no desaparezca de golpe.
   Los tramos de voz salen de los segmentos de la transcripción (ya corregidos por
   el usuario), con un margen para no cortar inicios de palabra ni respiraciones.
3. FFmpeg normaliza el volumen a -16 LUFS (estándar de podcast) y codifica.
"""

from __future__ import annotations

import bisect
import subprocess
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np

from .audio import AudioError

SAMPLE_RATE = 48000
# Bloques de 1 s: poca memoria y suficiente precisión para las rampas.
CHUNK_FRAMES = SAMPLE_RATE


class Strength(str, Enum):
    suave = "suave"
    media = "media"
    fuerte = "fuerte"


@dataclass(frozen=True)
class Preset:
    # Reducción de ruido espectral en toda la pista (dB).
    denoise_db: float
    # Atenuación extra en los huecos sin voz (dB).
    gap_db: float


PRESETS = {
    Strength.suave: Preset(denoise_db=6, gap_db=8),
    Strength.media: Preset(denoise_db=10, gap_db=14),
    Strength.fuerte: Preset(denoise_db=16, gap_db=20),
}

# Margen alrededor de la voz: antes (no cortar el ataque) y después (colas y respiraciones).
PAD_BEFORE = 0.15
PAD_AFTER = 0.25
PAD_BREATHS = 0.6
# Duración de las rampas de entrada y salida de la atenuación.
RAMP_SECONDS = 0.12
# Huecos más cortos que esto no se atenúan (pausas naturales entre frases).
MIN_GAP = 0.6

TARGET_LUFS = -16.0


@dataclass(frozen=True)
class CleanupOptions:
    strength: Strength = Strength.media
    keep_breaths: bool = True


def speech_intervals(
    segments: list[tuple[float, float]], keep_breaths: bool, offset: float = 0.0
) -> list[tuple[float, float]]:
    """Intervalos de voz con margen, unidos cuando el hueco entre ellos es muy corto.

    `offset` desplaza los tiempos (para procesar un fragmento que empieza en `offset`).
    """
    after = PAD_BREATHS if keep_breaths else PAD_AFTER
    padded = sorted(
        (max(0.0, s - PAD_BEFORE - offset), e + after - offset) for s, e in segments if e > s
    )
    merged: list[tuple[float, float]] = []
    for s, e in padded:
        if merged and s - merged[-1][1] < MIN_GAP:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return [(s, e) for s, e in merged if e > 0]


def gain_envelope(
    start_frame: int,
    n: int,
    speech: list[tuple[float, float]],
    gap_gain: float,
    ends: list[float] | None = None,
) -> np.ndarray:
    """Ganancia por muestra para el bloque [start_frame, start_frame + n).

    1.0 en la voz, `gap_gain` en los huecos, con rampas lineales dentro del hueco
    para que la transición no se oiga.
    """
    t = (start_frame + np.arange(n)) / SAMPLE_RATE
    # Distancia (en segundos) desde cada muestra al tramo de voz más cercano; 0 dentro.
    dist = np.full(n, np.inf)
    t0, t1 = t[0] - RAMP_SECONDS, t[-1] + RAMP_SECONDS
    # Solo los tramos que tocan este bloque (los intervalos están ordenados y no se solapan).
    first = bisect.bisect_left(ends if ends is not None else [e for _, e in speech], t0)
    for s, e in speech[first:]:
        if s > t1:
            break
        d = np.maximum(s - t, 0) + np.maximum(t - e, 0)
        np.minimum(dist, d, out=dist)
    ramp = np.clip(dist / RAMP_SECONDS, 0, 1)
    return (1 - ramp) + ramp * gap_gain


def clean_audio(
    src: Path,
    dst: Path,
    segments: list[tuple[float, float]],
    options: CleanupOptions,
    start: float = 0.0,
    duration: float | None = None,
) -> Path:
    """Procesa `src` (o el fragmento [start, start + duration]) y escribe `dst`.

    El formato de salida lo decide la extensión de `dst` (.mp3, .flac, .wav).
    """
    preset = PRESETS[Strength(options.strength)]
    speech = speech_intervals(segments, options.keep_breaths, offset=start)
    gap_gain = 10 ** (-preset.gap_db / 20)
    ends = [e for _, e in speech]

    trim = ["-ss", f"{start:.3f}"] if start > 0 else []
    if duration is not None:
        trim += ["-t", f"{duration:.3f}"]
    decode = [
        "ffmpeg", "-v", "error", *trim, "-i", str(src), "-vn",
        "-af", f"highpass=f=70,afftdn=nr={preset.denoise_db}:nf=-50:tn=1",
        "-ac", "2", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1",
    ]
    codec = {
        ".mp3": ["-codec:a", "libmp3lame", "-b:a", "192k"],
        ".flac": ["-codec:a", "flac"],
        ".wav": ["-codec:a", "pcm_s16le"],
    }.get(dst.suffix.lower())
    if codec is None:
        raise AudioError(f"Formato de salida no admitido: {dst.suffix}")
    encode = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "f32le", "-ac", "2", "-ar", str(SAMPLE_RATE), "-i", "pipe:0",
        "-af", f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11,aresample={SAMPLE_RATE}",
        *codec, str(dst),
    ]

    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    encode[-1] = str(tmp)
    try:
        dec = subprocess.Popen(decode, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        enc = subprocess.Popen(encode, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as e:
        raise AudioError("No se encontró ffmpeg; instala FFmpeg.") from e

    # Leemos los errores en hilos aparte para que ningún proceso se bloquee.
    errs: dict[str, bytes] = {}

    def drain(name: str, proc: subprocess.Popen) -> None:
        errs[name] = proc.stderr.read()

    threads = [
        threading.Thread(target=drain, args=("dec", dec), daemon=True),
        threading.Thread(target=drain, args=("enc", enc), daemon=True),
    ]
    for th in threads:
        th.start()

    frame = 0
    bytes_per_chunk = CHUNK_FRAMES * 2 * 4
    try:
        while True:
            buf = dec.stdout.read(bytes_per_chunk)
            if not buf:
                break
            usable = len(buf) - len(buf) % 8
            block = np.frombuffer(buf[:usable], dtype=np.float32).reshape(-1, 2)
            gain = gain_envelope(frame, len(block), speech, gap_gain, ends).astype(np.float32)
            enc.stdin.write((block * gain[:, None]).tobytes())
            frame += len(block)
    except BrokenPipeError:
        pass
    finally:
        enc.stdin.close()
        dec.wait()
        enc.wait()
        for th in threads:
            th.join()

    if dec.returncode != 0 or enc.returncode != 0 or frame == 0:
        tmp.unlink(missing_ok=True)
        msg = (errs.get("dec") or errs.get("enc") or b"").decode(errors="replace").strip()
        raise AudioError(msg.splitlines()[-1] if msg else "No se pudo procesar el audio")
    tmp.replace(dst)
    return dst

"""Motor de pruebas: no transcribe, inventa turnos alternos para probar el flujo.

Útil para desarrollar la interfaz sin GPU ni modelos (HOTARU_ENGINE=fake).
"""

from __future__ import annotations

from pathlib import Path

from ..audio import probe_duration
from ..models import SpeakerHints
from .base import EngineResult, RawWord

_PHRASES = [
    "Bienvenidos a un nuevo episodio del podcast.",
    "Hoy hablamos de un tema que nos interesa mucho.",
    "Exacto, y tenemos varias preguntas preparadas.",
    "Me parece una idea estupenda, empecemos.",
]


class FakeEngine:
    def __init__(self, turn_seconds: float = 4.0):
        self.turn_seconds = turn_seconds

    def run(self, audio_path: Path, hints: SpeakerHints) -> EngineResult:
        duration = probe_duration(audio_path)
        speakers = hints.num_speakers or hints.min_speakers or 2
        words: list[RawWord] = []
        t, turn = 0.0, 0
        while t < duration:
            spk = f"SPEAKER_{turn % speakers:02d}"
            end = min(duration, t + self.turn_seconds)
            phrase = _PHRASES[turn % len(_PHRASES)].split()
            step = (end - t) / len(phrase)
            for i, w in enumerate(phrase):
                words.append(RawWord(w, round(t + i * step, 3), round(t + (i + 0.9) * step, 3), spk))
            t, turn = end, turn + 1
        return EngineResult(language="es", words=words)

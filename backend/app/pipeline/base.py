"""Contrato común de los motores de transcripción + diarización."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..models import SpeakerHints


@dataclass
class RawWord:
    text: str
    start: float | None
    end: float | None
    # Etiqueta de voz del motor (p. ej. "SPEAKER_00"); None si no se pudo asignar.
    speaker: str | None


@dataclass
class EngineResult:
    language: str | None
    words: list[RawWord]
    embeddings: dict[str, list[float]] = field(default_factory=dict)


class Engine(Protocol):
    def run(self, audio_path: Path, hints: SpeakerHints) -> EngineResult: ...

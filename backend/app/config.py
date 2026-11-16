"""Configuración leída de variables de entorno (prefijo HOTARU_)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"HOTARU_{name}", default)


@dataclass(frozen=True)
class Settings:
    # Carpeta donde se guardan audios, resultados y clips de muestra.
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "data")).resolve())
    # "whisperx" (real, necesita el extra [ml]) o "fake" (desarrollo y pruebas).
    engine: str = field(default_factory=lambda: _env("ENGINE", "whisperx"))
    whisper_model: str = field(default_factory=lambda: _env("WHISPER_MODEL", "large-v3"))
    # Idioma por defecto; vacío para detección automática.
    language: str | None = field(default_factory=lambda: _env("LANGUAGE", "es") or None)
    device: str = field(default_factory=lambda: _env("DEVICE", "cpu"))
    compute_type: str = field(default_factory=lambda: _env("COMPUTE_TYPE", "int8"))
    batch_size: int = field(default_factory=lambda: int(_env("BATCH_SIZE", "8")))
    # Token de Hugging Face para los modelos de pyannote.
    hf_token: str | None = field(
        default_factory=lambda: _env("HF_TOKEN") or os.environ.get("HF_TOKEN")
    )
    diarization_model: str | None = field(default_factory=lambda: _env("DIARIZATION_MODEL"))
    # Duración máxima del clip de muestra de cada voz, en segundos.
    sample_seconds: float = field(default_factory=lambda: float(_env("SAMPLE_SECONDS", "6")))


def get_settings() -> Settings:
    return Settings()

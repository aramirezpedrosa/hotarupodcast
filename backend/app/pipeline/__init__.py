from __future__ import annotations

from ..config import Settings
from .base import Engine, EngineResult, RawWord


def create_engine(settings: Settings) -> Engine:
    if settings.engine == "fake":
        from .fake_engine import FakeEngine

        return FakeEngine()
    if settings.engine == "whisperx":
        from .whisperx_engine import WhisperXEngine

        return WhisperXEngine(settings)
    raise ValueError(f"Motor desconocido: {settings.engine!r} (usa 'whisperx' o 'fake')")


__all__ = ["Engine", "EngineResult", "RawWord", "create_engine"]

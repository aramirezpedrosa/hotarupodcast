"""Modelos de datos de un episodio.

La lista de segmentos es la fuente de verdad: cada segmento es un turno de
palabra continuo de una sola voz. La transcripción, la limpieza de audio y el
vídeo se derivan de ella.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EpisodeStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    done = "done"
    error = "error"


class Word(BaseModel):
    text: str
    start: float
    end: float


class Segment(BaseModel):
    id: str
    start: float
    end: float
    voice_id: str
    text: str
    words: list[Word] = Field(default_factory=list)


class Interval(BaseModel):
    start: float
    end: float


class Voice(BaseModel):
    id: str
    label: str
    color: str
    # Etiqueta original del motor de diarización (p. ej. SPEAKER_00).
    source_speakers: list[str] = Field(default_factory=list)
    talk_time: float = 0.0
    segment_count: int = 0
    sample: Interval | None = None
    # Huella de voz (embedding) para reconocer a la persona en otros episodios.
    embedding: list[float] | None = None


class SpeakerHints(BaseModel):
    num_speakers: int | None = Field(default=None, ge=1, le=20)
    min_speakers: int | None = Field(default=None, ge=1, le=20)
    max_speakers: int | None = Field(default=None, ge=1, le=20)


class Episode(BaseModel):
    id: str
    title: str
    original_filename: str
    status: EpisodeStatus = EpisodeStatus.pending
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    duration: float | None = None
    language: str | None = None
    hints: SpeakerHints = Field(default_factory=SpeakerHints)
    voices: list[Voice] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)


class EpisodeSummary(BaseModel):
    id: str
    title: str
    status: EpisodeStatus
    created_at: datetime
    duration: float | None
    voice_count: int


class MergeVoicesRequest(BaseModel):
    target_id: str
    source_ids: list[str] = Field(min_length=1)

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
    # Perfil del interviniente asignado por el usuario (biblioteca común a todos los episodios).
    profile_id: str | None = None


class SpeakerHints(BaseModel):
    num_speakers: int | None = Field(default=None, ge=1, le=20)
    min_speakers: int | None = Field(default=None, ge=1, le=20)
    max_speakers: int | None = Field(default=None, ge=1, le=20)


class CleanupStrength(str, Enum):
    suave = "suave"
    media = "media"
    fuerte = "fuerte"


class CleanupSettings(BaseModel):
    strength: CleanupStrength = CleanupStrength.media
    # Deja más margen tras cada frase para no cortar respiraciones ni colas de voz.
    keep_breaths: bool = True


class Cleanup(BaseModel):
    status: EpisodeStatus = EpisodeStatus.pending
    settings: CleanupSettings = Field(default_factory=CleanupSettings)
    error: str | None = None
    updated_at: datetime = Field(default_factory=utcnow)
    # True si los tramos de voz han cambiado desde la última limpieza.
    stale: bool = False


class VideoTemplate(str, Enum):
    mesa = "mesa"
    foco = "foco"


class VideoAspect(str, Enum):
    horizontal = "16:9"
    vertical = "9:16"
    cuadrado = "1:1"
    retrato = "4:5"


class VideoSettings(BaseModel):
    template: VideoTemplate = VideoTemplate.mesa
    aspect: VideoAspect = VideoAspect.horizontal
    # Los que no hablan se ven más apagados.
    dim_others: bool = True
    show_names: bool = True
    show_title: bool = True
    # Usar el audio limpio si existe (fase 3); si no, el original.
    use_cleaned_audio: bool = True
    # Fondo: id de un paisaje de naturaleza ("bosque", "aurora"…) o de una foto subida.
    background: str = Field(default="liso", min_length=1, max_length=64)


class Video(BaseModel):
    id: str
    settings: VideoSettings
    status: EpisodeStatus = EpisodeStatus.pending
    progress: float = 0.0
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    # Qué audio se usó: "limpio" u "original".
    audio: str | None = None


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
    cleanup: Cleanup | None = None
    # Se guardan aparte (videos.json) para que el progreso del render no pise ediciones.
    videos: list[Video] = Field(default_factory=list)
    # Calculados al responder; no se guardan en disco.
    can_undo: bool = False
    can_redo: bool = False


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


class Profile(BaseModel):
    """Interviniente reutilizable entre episodios (presentador fijo, invitado recurrente)."""

    id: str
    name: str = Field(min_length=1, max_length=120)
    role: str | None = Field(default=None, max_length=160)
    info: str | None = Field(default=None, max_length=4000)
    color: str = Field(default="#3B82F6", pattern=r"^#[0-9A-Fa-f]{6}$")
    # Nombre del archivo de la foto dentro de la carpeta de perfiles.
    photo: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: str | None = Field(default=None, max_length=160)
    info: str | None = Field(default=None, max_length=4000)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class ProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: str | None = Field(default=None, max_length=160)
    info: str | None = Field(default=None, max_length=4000)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class VoicePatch(BaseModel):
    # Cadena vacía o null para quitar el perfil asignado.
    profile_id: str | None = None
    label: str | None = Field(default=None, min_length=1, max_length=60)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class WordPosition(BaseModel):
    segment_id: str
    word_index: int = Field(ge=0)


class ReassignRequest(BaseModel):
    start: WordPosition
    end: WordPosition
    # Voz destino; null crea una voz nueva.
    voice_id: str | None = None



class CustomBackground(BaseModel):
    """Foto propia subida para usar como fondo de vídeo."""

    id: str
    name: str = Field(min_length=1, max_length=120)
    file: str
    created_at: datetime = Field(default_factory=utcnow)


class BackgroundOption(BaseModel):
    """Un fondo elegible en el panel de vídeo: paisaje generado o foto propia."""

    id: str
    name: str
    description: str | None = None
    kind: str  # "preset" | "custom"

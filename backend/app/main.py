"""API HTTP de HotaruPodcast (fase 1: transcripción y voces sugeridas).

Arranque: uvicorn app.main:create_app --factory
"""

from __future__ import annotations

import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .audio import AudioError, cut_clip
from .config import Settings, get_settings
from .jobs import JobRunner
from .models import (
    Episode,
    EpisodeStatus,
    EpisodeSummary,
    MergeVoicesRequest,
    SpeakerHints,
)
from .pipeline import Engine, create_engine
from .pipeline.voices import merge_voices
from .storage import NotFound, Storage

ALLOWED_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".webm", ".mp4", ".mov"}


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    storage = Storage(settings.data_dir)
    runner = JobRunner(storage, engine or create_engine(settings), settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Episodios que quedaron a medias al reiniciar el servidor se vuelven a lanzar.
        for ep in storage.list():
            if ep.status in (EpisodeStatus.pending, EpisodeStatus.processing):
                runner.submit(ep.id)
        yield
        runner.shutdown()

    app = FastAPI(title="HotaruPodcast", version="0.1.0", lifespan=lifespan)
    app.state.storage, app.state.runner, app.state.settings = storage, runner, settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def load(episode_id: str) -> Episode:
        try:
            return storage.load(episode_id)
        except NotFound:
            raise HTTPException(404, "Episodio no encontrado")

    @app.get("/api/health")
    def health():
        return {"ok": True, "engine": settings.engine}

    @app.post("/api/episodes", response_model=Episode, status_code=201)
    def create_episode(
        file: UploadFile = File(...),
        title: str | None = Form(None),
        num_speakers: int | None = Form(None),
        min_speakers: int | None = Form(None),
        max_speakers: int | None = Form(None),
    ):
        name = Path(file.filename or "audio").name
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_EXT:
            raise HTTPException(415, f"Formato no admitido: {ext or 'sin extensión'}")
        try:
            hints = SpeakerHints(
                num_speakers=num_speakers, min_speakers=min_speakers, max_speakers=max_speakers
            )
        except ValueError as e:
            raise HTTPException(422, str(e))
        ep = Episode(
            id=uuid.uuid4().hex[:12],
            title=(title or Path(name).stem).strip() or "Episodio",
            original_filename=name,
            hints=hints,
        )
        d = storage.episode_dir(ep.id)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"original{ext}", "wb") as out:
            shutil.copyfileobj(file.file, out)
        storage.save(ep)
        runner.submit(ep.id)
        return ep

    @app.get("/api/episodes", response_model=list[EpisodeSummary])
    def list_episodes():
        return [
            EpisodeSummary(
                id=e.id,
                title=e.title,
                status=e.status,
                created_at=e.created_at,
                duration=e.duration,
                voice_count=len(e.voices),
            )
            for e in storage.list()
        ]

    @app.get("/api/episodes/{episode_id}", response_model=Episode)
    def get_episode(episode_id: str):
        return load(episode_id)

    @app.post("/api/episodes/{episode_id}/reprocess", response_model=Episode)
    def reprocess(episode_id: str, hints: SpeakerHints | None = None):
        ep = load(episode_id)
        if ep.status == EpisodeStatus.processing:
            raise HTTPException(409, "El episodio ya se está procesando")
        if hints is not None:
            ep.hints = hints
        ep.status, ep.error, ep.voices, ep.segments = EpisodeStatus.pending, None, [], []
        storage.save(ep)
        runner.submit(ep.id)
        return ep

    @app.get("/api/episodes/{episode_id}/audio")
    def get_audio(episode_id: str):
        load(episode_id)
        return FileResponse(storage.original_path(episode_id))

    @app.get("/api/episodes/{episode_id}/voices/{voice_id}/sample")
    def get_sample(episode_id: str, voice_id: str):
        ep = load(episode_id)
        voice = next((v for v in ep.voices if v.id == voice_id), None)
        if voice is None or voice.sample is None:
            raise HTTPException(404, "Voz no encontrada")
        s = voice.sample
        clip = storage.samples_dir(episode_id) / f"{voice.id}-{s.start:.3f}-{s.end:.3f}.mp3"
        if not clip.exists():
            try:
                cut_clip(storage.original_path(episode_id), clip, s.start, s.end)
            except AudioError as e:
                raise HTTPException(500, f"No se pudo generar el clip: {e}")
        return FileResponse(clip, media_type="audio/mpeg")

    @app.post("/api/episodes/{episode_id}/voices/merge", response_model=Episode)
    def merge(episode_id: str, body: MergeVoicesRequest):
        ep = load(episode_id)
        if ep.status != EpisodeStatus.done:
            raise HTTPException(409, "El episodio aún no está procesado")
        try:
            ep.voices, ep.segments = merge_voices(
                ep.voices, ep.segments, body.target_id, body.source_ids, settings.sample_seconds
            )
        except KeyError as e:
            raise HTTPException(404, f"Voz no encontrada: {e.args[0]}")
        return storage.save(ep)

    return app


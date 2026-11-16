"""API HTTP de HotaruPodcast: episodios, voces, intervinientes y limpieza de audio.

Arranque: uvicorn app.main:create_app --factory
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse


from .audio import AudioError, cut_clip
from .cleanup import CleanupOptions, Strength, clean_audio
from .config import Settings, get_settings
from .jobs import JobRunner
from .models import (
    Cleanup,
    CleanupSettings,
    CleanupStrength,
    Episode,
    EpisodeStatus,
    EpisodeSummary,
    MergeVoicesRequest,
    Profile,
    ProfileInput,
    ProfilePatch,
    ReassignRequest,
    SpeakerHints,
    VoicePatch,
)
from .pipeline import Engine, create_engine
from .pipeline.voices import EditError, PALETTE, WordRef, merge_voices, reassign_words
from .storage import NotFound, Storage

# Fotos de perfil admitidas y tamaño máximo.
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_PHOTO_BYTES = 5 * 1024 * 1024

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
            elif ep.cleanup and ep.cleanup.status in (EpisodeStatus.pending, EpisodeStatus.processing):
                runner.submit_cleanup(ep.id)
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
        ep.cleanup = None
        storage.cleaned_path(ep.id).unlink(missing_ok=True)
        storage.clear_history(ep.id)
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

    def editable(episode_id: str) -> Episode:
        ep = load(episode_id)
        if ep.status != EpisodeStatus.done:
            raise HTTPException(409, "El episodio aún no está procesado")
        return ep

    @app.post("/api/episodes/{episode_id}/voices/merge", response_model=Episode)
    def merge(episode_id: str, body: MergeVoicesRequest):
        ep = editable(episode_id)
        before = storage.snapshot(ep)
        try:
            ep.voices, ep.segments = merge_voices(
                ep.voices, ep.segments, body.target_id, body.source_ids, settings.sample_seconds
            )
        except KeyError as e:
            raise HTTPException(404, f"Voz no encontrada: {e.args[0]}")
        return storage.save_edit(before, ep)

    @app.patch("/api/episodes/{episode_id}/voices/{voice_id}", response_model=Episode)
    def patch_voice(episode_id: str, voice_id: str, body: VoicePatch):
        ep = editable(episode_id)
        before = storage.snapshot(ep)
        voice = next((v for v in ep.voices if v.id == voice_id), None)
        if voice is None:
            raise HTTPException(404, "Voz no encontrada")
        fields = body.model_fields_set
        if "profile_id" in fields:
            if body.profile_id:
                try:
                    profile = storage.get_profile(body.profile_id)
                except NotFound:
                    raise HTTPException(404, "Perfil no encontrado")
                if any(v.profile_id == profile.id and v.id != voice_id for v in ep.voices):
                    raise HTTPException(
                        409, f"{profile.name} ya está asignado a otra voz; fusiónalas si son la misma persona"
                    )
                voice.profile_id = profile.id
                voice.color = profile.color
            else:
                voice.profile_id = None
        if body.label is not None:
            voice.label = body.label
        if body.color is not None:
            voice.color = body.color
        return storage.save_edit(before, ep)

    @app.post("/api/episodes/{episode_id}/segments/reassign", response_model=Episode)
    def reassign(episode_id: str, body: ReassignRequest):
        ep = editable(episode_id)
        before = storage.snapshot(ep)
        try:
            ep.voices, ep.segments, _ = reassign_words(
                ep.voices,
                ep.segments,
                WordRef(body.start.segment_id, body.start.word_index),
                WordRef(body.end.segment_id, body.end.word_index),
                body.voice_id,
                settings.sample_seconds,
            )
        except EditError as e:
            raise HTTPException(422, str(e))
        return storage.save_edit(before, ep)

    @app.post("/api/episodes/{episode_id}/undo", response_model=Episode)
    def undo(episode_id: str):
        ep = editable(episode_id)
        result = storage.undo(ep)
        if result is None:
            raise HTTPException(409, "No hay nada que deshacer")
        return result

    @app.post("/api/episodes/{episode_id}/redo", response_model=Episode)
    def redo(episode_id: str):
        ep = editable(episode_id)
        result = storage.redo(ep)
        if result is None:
            raise HTTPException(409, "No hay nada que rehacer")
        return result

    # --- Limpieza de audio -------------------------------------------------

    @app.put("/api/episodes/{episode_id}/cleanup", response_model=Episode)
    def start_cleanup(episode_id: str, body: CleanupSettings):
        ep = editable(episode_id)
        if ep.cleanup and ep.cleanup.status in (EpisodeStatus.pending, EpisodeStatus.processing):
            raise HTTPException(409, "Ya se está limpiando el audio")
        ep.cleanup = Cleanup(settings=body)
        storage.save(ep)
        runner.submit_cleanup(ep.id)
        return ep

    @app.get("/api/episodes/{episode_id}/cleanup/audio")
    def cleaned_audio(episode_id: str, download: bool = False):
        ep = load(episode_id)
        path = storage.cleaned_path(episode_id)
        if ep.cleanup is None or ep.cleanup.status != EpisodeStatus.done or not path.exists():
            raise HTTPException(404, "Todavía no hay audio limpio")
        return FileResponse(
            path,
            media_type="audio/mpeg",
            filename=f"{ep.title} (limpio).mp3",
            content_disposition_type="attachment" if download else "inline",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/api/episodes/{episode_id}/cleanup/preview")
    def cleanup_preview(
        episode_id: str,
        start: float = Query(0, ge=0),
        duration: float = Query(20, gt=0, le=30),
        strength: CleanupStrength = CleanupStrength.media,
        keep_breaths: bool = True,
    ):
        """Fragmento procesado con los ajustes indicados, para escuchar antes de aplicar."""
        ep = editable(episode_id)
        spans = [(s.start, s.end) for s in ep.segments]
        key = hashlib.sha1(
            repr((round(start, 2), round(duration, 2), strength.value, keep_breaths, spans)).encode()
        ).hexdigest()[:16]
        out = storage.previews_dir(episode_id) / f"{key}.mp3"
        if not out.exists():
            try:
                clean_audio(
                    storage.original_path(episode_id), out, spans,
                    CleanupOptions(Strength(strength.value), keep_breaths),
                    start=start, duration=duration,
                )
            except AudioError as e:
                raise HTTPException(500, f"No se pudo generar la escucha previa: {e}")
        return FileResponse(out, media_type="audio/mpeg")

    # --- Perfiles de intervinientes ----------------------------------------

    def get_profile_or_404(profile_id: str) -> Profile:
        try:
            return storage.get_profile(profile_id)
        except NotFound:
            raise HTTPException(404, "Perfil no encontrado")

    @app.get("/api/profiles", response_model=list[Profile])
    def list_profiles():
        return storage.list_profiles()

    @app.post("/api/profiles", response_model=Profile, status_code=201)
    def create_profile(body: ProfileInput):
        existing = storage.list_profiles()
        color = body.color or PALETTE[len(existing) % len(PALETTE)]
        profile = Profile(
            id=uuid.uuid4().hex[:12],
            name=body.name.strip(),
            role=(body.role or "").strip() or None,
            info=(body.info or "").strip() or None,
            color=color,
        )
        return storage.save_profile(profile)

    @app.get("/api/profiles/{profile_id}", response_model=Profile)
    def get_profile(profile_id: str):
        return get_profile_or_404(profile_id)

    @app.patch("/api/profiles/{profile_id}", response_model=Profile)
    def patch_profile(profile_id: str, body: ProfilePatch):
        profile = get_profile_or_404(profile_id)
        fields = body.model_fields_set
        if "name" in fields and body.name:
            profile.name = body.name.strip()
        for key in ("role", "info"):
            if key in fields:
                setattr(profile, key, (getattr(body, key) or "").strip() or None)
        if "color" in fields and body.color:
            profile.color = body.color
            # El color del perfil manda en las voces que lo usan.
            for ep in storage.list():
                changed = False
                for v in ep.voices:
                    if v.profile_id == profile.id and v.color != body.color:
                        v.color, changed = body.color, True
                if changed:
                    storage.save(ep)
        return storage.save_profile(profile)

    @app.delete("/api/profiles/{profile_id}", status_code=204)
    def delete_profile(profile_id: str):
        get_profile_or_404(profile_id)
        for ep in storage.list():
            if any(v.profile_id == profile_id for v in ep.voices):
                for v in ep.voices:
                    if v.profile_id == profile_id:
                        v.profile_id = None
                storage.save(ep)
        storage.delete_profile(profile_id)

    @app.put("/api/profiles/{profile_id}/photo", response_model=Profile)
    def upload_photo(profile_id: str, file: UploadFile = File(...)):
        profile = get_profile_or_404(profile_id)
        ext = PHOTO_TYPES.get(file.content_type or "")
        if ext is None:
            raise HTTPException(415, "La foto debe ser JPG, PNG o WebP")
        data = file.file.read(MAX_PHOTO_BYTES + 1)
        if len(data) > MAX_PHOTO_BYTES:
            raise HTTPException(413, "La foto no puede superar 5 MB")
        if not _looks_like_image(data, ext):
            raise HTTPException(415, "El archivo no parece una imagen válida")
        old = profile.photo
        # Nombre nuevo en cada subida para que el navegador no muestre la foto anterior.
        profile.photo = f"{profile.id}-{uuid.uuid4().hex[:8]}{ext}"
        (storage.photos_dir / profile.photo).write_bytes(data)
        saved = storage.save_profile(profile)
        if old:
            (storage.photos_dir / old).unlink(missing_ok=True)
        return saved

    @app.delete("/api/profiles/{profile_id}/photo", response_model=Profile)
    def delete_photo(profile_id: str):
        profile = get_profile_or_404(profile_id)
        if profile.photo:
            (storage.photos_dir / profile.photo).unlink(missing_ok=True)
            profile.photo = None
        return storage.save_profile(profile)

    @app.get("/api/profiles/{profile_id}/photo")
    def get_photo(profile_id: str):
        profile = get_profile_or_404(profile_id)
        if not profile.photo:
            raise HTTPException(404, "El perfil no tiene foto")
        return FileResponse(
            storage.photos_dir / profile.photo,
            headers={"Cache-Control": "no-cache"},
        )

    return app



def _looks_like_image(data: bytes, ext: str) -> bool:
    """Comprueba la firma del archivo, no solo el tipo que declara el navegador."""
    if ext == ".jpg":
        return data[:3] == b"\xff\xd8\xff"
    if ext == ".png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if ext == ".webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False

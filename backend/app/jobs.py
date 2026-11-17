"""Procesado en segundo plano: un episodio cada vez (los modelos ocupan mucha memoria)."""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor

from .audio import probe_duration, to_wav16k
from .cleanup import CleanupOptions, Strength, clean_audio
from .config import Settings
from .models import EpisodeStatus, utcnow
from .pipeline import Engine
from .pipeline.voices import build_episode_data
from .storage import Storage

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, storage: Storage, engine: Engine, settings: Settings):
        self.storage = storage
        self.engine = engine
        self.settings = settings
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hotaru-job")

    def submit(self, episode_id: str) -> Future:
        return self._pool.submit(self.process, episode_id)

    def process(self, episode_id: str) -> None:
        ep = self.storage.load(episode_id)
        ep.status, ep.error = EpisodeStatus.processing, None
        self.storage.save(ep)
        try:
            original = self.storage.original_path(episode_id)
            wav = to_wav16k(original, self.storage.wav_path(episode_id))
            ep.duration = round(probe_duration(wav), 3)
            result = self.engine.run(wav, ep.hints)
            voices, segments = build_episode_data(result, self.settings.sample_seconds)
            ep.language, ep.voices, ep.segments = result.language, voices, segments
            ep.status = EpisodeStatus.done
        except Exception as e:  # el error se muestra al usuario en la interfaz
            log.exception("Fallo procesando %s", episode_id)
            ep.status, ep.error = EpisodeStatus.error, str(e) or e.__class__.__name__
        self.storage.save(ep)

    def submit_cleanup(self, episode_id: str) -> Future:
        return self._pool.submit(self.process_cleanup, episode_id)

    def process_cleanup(self, episode_id: str) -> None:
        ep = self.storage.load(episode_id)
        if ep.cleanup is None:
            return
        ep.cleanup.status, ep.cleanup.error = EpisodeStatus.processing, None
        self.storage.save(ep)
        settings = ep.cleanup.settings
        # Los tramos de voz se toman en este momento; si el usuario los cambia
        # mientras se procesa, la limpieza queda marcada como desactualizada.
        spans = [(s.start, s.end) for s in ep.segments]
        try:
            clean_audio(
                self.storage.original_path(episode_id),
                self.storage.cleaned_path(episode_id),
                spans,
                CleanupOptions(Strength(settings.strength.value), settings.keep_breaths),
            )
            status, error = EpisodeStatus.done, None
        except Exception as e:
            log.exception("Fallo limpiando %s", episode_id)
            status, error = EpisodeStatus.error, str(e) or e.__class__.__name__
        ep = self.storage.load(episode_id)
        if ep.cleanup is None:
            return
        ep.cleanup.status, ep.cleanup.error = status, error
        ep.cleanup.updated_at = utcnow()
        ep.cleanup.stale = [(s.start, s.end) for s in ep.segments] != spans
        self.storage.save(ep)

    def submit_video(self, episode_id: str, video_id: str) -> Future:
        return self._pool.submit(self.process_video, episode_id, video_id)

    def process_video(self, episode_id: str, video_id: str) -> None:
        from .video import render_video

        ep = self.storage.load(episode_id)
        video = next((v for v in ep.videos if v.id == video_id), None)
        if video is None:
            return
        cfg = video.settings
        cleaned = self.storage.cleaned_path(episode_id)
        use_cleaned = (
            cfg.use_cleaned_audio
            and ep.cleanup is not None
            and ep.cleanup.status == EpisodeStatus.done
            and cleaned.exists()
        )
        audio = cleaned if use_cleaned else self.storage.original_path(episode_id)
        self.storage.update_video(
            episode_id, video_id, status=EpisodeStatus.processing, progress=0.0, error=None,
            audio="limpio" if use_cleaned else "original",
        )
        last = [0.0]

        def progress(frac: float) -> None:
            # Guardamos como mucho una vez por segundo.
            now = time.monotonic()
            if now - last[0] >= 1 or frac >= 1:
                last[0] = now
                self.storage.update_video(episode_id, video_id, progress=round(frac, 3))

        try:
            participants, spans = participants_for(ep, self.storage)
            render_video(
                participants, spans, audio, ep.duration or 0,
                video_options(ep, cfg, self.storage),
                self.storage.video_path(episode_id, video_id),
                progress,
            )
            self.storage.update_video(episode_id, video_id, status=EpisodeStatus.done, progress=1.0)
        except Exception as e:
            log.exception("Fallo generando el vídeo %s de %s", video_id, episode_id)
            self.storage.update_video(
                episode_id, video_id, status=EpisodeStatus.error, error=str(e) or e.__class__.__name__
            )

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def participants_for(ep, storage):
    """Intervinientes del vídeo, en orden de primera intervención, y sus tramos de voz."""
    from .storage import NotFound
    from .video import Participant

    first: dict[str, float] = {}
    for s in ep.segments:
        first.setdefault(s.voice_id, s.start)
    voices = sorted(ep.voices, key=lambda v: first.get(v.id, float("inf")))
    participants = []
    for v in voices:
        profile = None
        if v.profile_id:
            try:
                profile = storage.get_profile(v.profile_id)
            except NotFound:
                profile = None
        photo = storage.photos_dir / profile.photo if profile and profile.photo else None
        participants.append(
            Participant(
                voice_id=v.id,
                name=profile.name if profile else v.label,
                color=v.color,
                role=profile.role if profile else None,
                photo=photo if photo and photo.exists() else None,
            )
        )
    index = {p.voice_id: i for i, p in enumerate(participants)}
    spans = [(s.start, s.end, index[s.voice_id]) for s in ep.segments if s.voice_id in index]
    return participants, spans


class UnknownBackground(ValueError):
    pass


def video_options(ep, cfg, storage):
    """Traduce los ajustes guardados del vídeo a opciones de dibujo (resolviendo el fondo)."""
    from .backgrounds import PRESET_IDS
    from .storage import NotFound
    from .video import Aspect, Template, VideoOptions

    preset, image = "liso", None
    if cfg.background in PRESET_IDS:
        preset = cfg.background
    else:
        try:
            image = storage.background_file(storage.get_background(cfg.background))
        except NotFound:
            raise UnknownBackground("El fondo elegido ya no existe; elige otro")
        if not image.exists():
            raise UnknownBackground("Falta el archivo del fondo elegido; súbelo de nuevo")
    return VideoOptions(
        Template(cfg.template.value), Aspect(cfg.aspect.value),
        cfg.dim_others, cfg.show_names, ep.title if cfg.show_title else None,
        background=preset, background_image=image,
    )

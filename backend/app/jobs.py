"""Procesado en segundo plano: un episodio cada vez (los modelos ocupan mucha memoria)."""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor

from .audio import probe_duration, to_wav16k
from .config import Settings
from .models import EpisodeStatus
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

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

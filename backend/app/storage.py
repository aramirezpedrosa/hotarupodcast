"""Almacenamiento en disco: una carpeta por episodio con su JSON y sus audios.

    data/episodes/<id>/episode.json
    data/episodes/<id>/original.<ext>
    data/episodes/<id>/audio16k.wav
    data/episodes/<id>/samples/<voice>-<start>-<end>.mp3
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from .models import Episode, utcnow

_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")


class NotFound(KeyError):
    pass


class Storage:
    def __init__(self, root: Path):
        self.root = Path(root) / "episodes"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def episode_dir(self, episode_id: str) -> Path:
        if not _ID_RE.match(episode_id):
            raise NotFound(episode_id)
        return self.root / episode_id

    def save(self, episode: Episode) -> Episode:
        episode.updated_at = utcnow()
        d = self.episode_dir(episode.id)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "episode.json.tmp"
        with self._lock:
            tmp.write_text(episode.model_dump_json(indent=2), encoding="utf-8")
            os.replace(tmp, d / "episode.json")
        return episode

    def load(self, episode_id: str) -> Episode:
        path = self.episode_dir(episode_id) / "episode.json"
        if not path.exists():
            raise NotFound(episode_id)
        return Episode.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[Episode]:
        out = []
        for p in self.root.glob("*/episode.json"):
            try:
                out.append(Episode.model_validate_json(p.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return sorted(out, key=lambda e: e.created_at, reverse=True)

    def original_path(self, episode_id: str) -> Path:
        d = self.episode_dir(episode_id)
        found = sorted(d.glob("original.*"))
        if not found:
            raise NotFound(episode_id)
        return found[0]

    def wav_path(self, episode_id: str) -> Path:
        return self.episode_dir(episode_id) / "audio16k.wav"

    def samples_dir(self, episode_id: str) -> Path:
        d = self.episode_dir(episode_id) / "samples"
        d.mkdir(parents=True, exist_ok=True)
        return d

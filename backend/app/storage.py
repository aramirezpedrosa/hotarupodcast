"""Almacenamiento en disco: una carpeta por episodio con su JSON y sus audios.

    data/episodes/<id>/episode.json
    data/episodes/<id>/original.<ext>
    data/episodes/<id>/audio16k.wav
    data/episodes/<id>/samples/<voice>-<start>-<end>.mp3
    data/episodes/<id>/history.json      (deshacer / rehacer)
    data/episodes/<id>/cleaned.mp3       (audio limpio)
    data/episodes/<id>/videos.json       (vídeos y su progreso)
    data/episodes/<id>/videos/<vid>.mp4
    data/profiles/profiles.json
    data/profiles/photos/<id>-<n>.<ext>
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from .models import Episode, Profile, Segment, Video, Voice, utcnow

_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")
# Cambios que se pueden deshacer por episodio.
HISTORY_LIMIT = 50
_EPHEMERAL = {"can_undo", "can_redo", "videos"}


class NotFound(KeyError):
    pass


class Storage:
    def __init__(self, root: Path):
        self.root = Path(root) / "episodes"
        self.root.mkdir(parents=True, exist_ok=True)
        self.profiles_root = Path(root) / "profiles"
        self.photos_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def episode_dir(self, episode_id: str) -> Path:
        if not _ID_RE.match(episode_id):
            raise NotFound(episode_id)
        return self.root / episode_id

    def save(self, episode: Episode) -> Episode:
        episode.updated_at = utcnow()
        d = self.episode_dir(episode.id)
        d.mkdir(parents=True, exist_ok=True)
        with self._lock:
            _write_atomic(d / "episode.json", episode.model_dump_json(indent=2, exclude=_EPHEMERAL))
        self._fill_history_flags(episode)
        episode.videos = self.list_videos(episode.id)
        return episode

    def load(self, episode_id: str) -> Episode:
        path = self.episode_dir(episode_id) / "episode.json"
        if not path.exists():
            raise NotFound(episode_id)
        ep = Episode.model_validate_json(path.read_text(encoding="utf-8"))
        self._fill_history_flags(ep)
        ep.videos = self.list_videos(episode_id)
        return ep

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

    # --- Deshacer / rehacer -------------------------------------------------

    def _history_path(self, episode_id: str) -> Path:
        return self.episode_dir(episode_id) / "history.json"

    def _read_history(self, episode_id: str) -> dict:
        path = self._history_path(episode_id)
        if not path.exists():
            return {"undo": [], "redo": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_history(self, episode_id: str, history: dict) -> None:
        _write_atomic(self._history_path(episode_id), json.dumps(history))

    def _fill_history_flags(self, episode: Episode) -> None:
        h = self._read_history(episode.id)
        episode.can_undo, episode.can_redo = bool(h["undo"]), bool(h["redo"])

    @staticmethod
    def _snapshot(episode: Episode) -> dict:
        return {
            "voices": [v.model_dump(mode="json") for v in episode.voices],
            "segments": [s.model_dump(mode="json") for s in episode.segments],
        }

    @staticmethod
    def _restore(episode: Episode, snap: dict) -> None:
        episode.voices = [Voice.model_validate(v) for v in snap["voices"]]
        episode.segments = [Segment.model_validate(s) for s in snap["segments"]]

    def save_edit(self, before: dict, episode: Episode) -> Episode:
        """Guarda un cambio del usuario y apila el estado anterior para poder deshacerlo."""
        with self._lock:
            h = self._read_history(episode.id)
            h["undo"] = (h["undo"] + [before])[-HISTORY_LIMIT:]
            h["redo"] = []
            self._write_history(episode.id, h)
            self._mark_cleanup_stale(before, episode)
            return self.save(episode)

    def snapshot(self, episode: Episode) -> dict:
        return self._snapshot(episode)

    def undo(self, episode: Episode) -> Episode | None:
        return self._step(episode, "undo", "redo")

    def redo(self, episode: Episode) -> Episode | None:
        return self._step(episode, "redo", "undo")

    def _step(self, episode: Episode, src: str, dst: str) -> Episode | None:
        with self._lock:
            h = self._read_history(episode.id)
            if not h[src]:
                return None
            snap = h[src].pop()
            current = self._snapshot(episode)
            h[dst] = (h[dst] + [current])[-HISTORY_LIMIT:]
            self._restore(episode, snap)
            self._mark_cleanup_stale(current, episode)
            self._write_history(episode.id, h)
            return self.save(episode)

    @staticmethod
    def _mark_cleanup_stale(before: dict, episode: Episode) -> None:
        # La limpieza depende de dónde hay voz: si cambian los tramos, hay que repetirla.
        if episode.cleanup is None or episode.cleanup.status != "done":
            return
        old = [(s["start"], s["end"]) for s in before["segments"]]
        new = [(s.start, s.end) for s in episode.segments]
        if old != new:
            episode.cleanup.stale = True

    def cleaned_path(self, episode_id: str) -> Path:
        return self.episode_dir(episode_id) / "cleaned.mp3"

    def previews_dir(self, episode_id: str) -> Path:
        d = self.episode_dir(episode_id) / "previews"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- Vídeos -------------------------------------------------------------

    def _videos_path(self, episode_id: str) -> Path:
        return self.episode_dir(episode_id) / "videos.json"

    def list_videos(self, episode_id: str) -> list[Video]:
        path = self._videos_path(episode_id)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [Video.model_validate(v) for v in data]

    def _write_videos(self, episode_id: str, videos: list[Video]) -> None:
        _write_atomic(
            self._videos_path(episode_id),
            json.dumps([v.model_dump(mode="json") for v in videos], indent=2),
        )

    def add_video(self, episode_id: str, video: Video) -> None:
        with self._lock:
            self._write_videos(episode_id, [video] + self.list_videos(episode_id))

    def update_video(self, episode_id: str, video_id: str, **fields) -> Video | None:
        with self._lock:
            videos = self.list_videos(episode_id)
            found = None
            for v in videos:
                if v.id == video_id:
                    for k, val in fields.items():
                        setattr(v, k, val)
                    v.updated_at = utcnow()
                    found = v
            if found:
                self._write_videos(episode_id, videos)
            return found

    def delete_video(self, episode_id: str, video_id: str) -> None:
        with self._lock:
            rest = [v for v in self.list_videos(episode_id) if v.id != video_id]
            self._write_videos(episode_id, rest)
            self.video_path(episode_id, video_id).unlink(missing_ok=True)

    def video_path(self, episode_id: str, video_id: str) -> Path:
        if not _ID_RE.match(video_id):
            raise NotFound(video_id)
        d = self.episode_dir(episode_id) / "videos"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{video_id}.mp4"

    def clear_history(self, episode_id: str) -> None:
        self._history_path(episode_id).unlink(missing_ok=True)

    # --- Perfiles -----------------------------------------------------------

    @property
    def photos_dir(self) -> Path:
        return self.profiles_root / "photos"

    def _profiles_path(self) -> Path:
        return self.profiles_root / "profiles.json"

    def list_profiles(self) -> list[Profile]:
        path = self._profiles_path()
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return sorted((Profile.model_validate(p) for p in data), key=lambda p: p.name.lower())

    def get_profile(self, profile_id: str) -> Profile:
        p = next((p for p in self.list_profiles() if p.id == profile_id), None)
        if p is None:
            raise NotFound(profile_id)
        return p

    def save_profile(self, profile: Profile) -> Profile:
        profile.updated_at = utcnow()
        with self._lock:
            others = [p for p in self.list_profiles() if p.id != profile.id]
            data = [p.model_dump(mode="json") for p in others + [profile]]
            _write_atomic(self._profiles_path(), json.dumps(data, ensure_ascii=False, indent=2))
        return profile

    def delete_profile(self, profile_id: str) -> None:
        with self._lock:
            profile = self.get_profile(profile_id)
            rest = [p for p in self.list_profiles() if p.id != profile_id]
            _write_atomic(
                self._profiles_path(),
                json.dumps([p.model_dump(mode="json") for p in rest], ensure_ascii=False, indent=2),
            )
            if profile.photo:
                (self.photos_dir / profile.photo).unlink(missing_ok=True)


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)

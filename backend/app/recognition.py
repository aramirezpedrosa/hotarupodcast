"""Reconocimiento de intervinientes recurrentes por su huella de voz.

La huella de un interviniente no se guarda aparte: se calcula a partir de las voces que el
usuario le ha asignado en los episodios. Así siempre coincide con lo que hay (deshacer,
fusionar, borrar un episodio...) sin tener que mantener nada sincronizado. Cada episodio
aporta la huella de su voz, ponderada por el tiempo de habla, y se promedia.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .models import Episode, VoiceSuggestion

# Caché por episodio: (mtime_ns, [(profile_id, huella, tiempo de habla)]).
_cache: dict[Path, tuple[int, list[tuple[str, np.ndarray, float]]]] = {}
_lock = threading.Lock()


@dataclass
class Voiceprint:
    vector: np.ndarray  # normalizado
    episodes: int


def _unit(v) -> np.ndarray | None:
    a = np.asarray(v, dtype=np.float64)
    n = np.linalg.norm(a)
    return a / n if a.ndim == 1 and n > 0 and np.isfinite(n) else None


def _episode_prints(path: Path) -> list[tuple[str, np.ndarray, float]]:
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        return []
    with _lock:
        hit = _cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    prints = []
    for v in data.get("voices", []):
        u = _unit(v["embedding"]) if v.get("profile_id") and v.get("embedding") else None
        if u is not None:
            prints.append((v["profile_id"], u, float(v.get("talk_time") or 0.0)))
    with _lock:
        _cache[path] = (mtime, prints)
    return prints


def profile_voiceprints(episodes_root: Path, exclude: str | None = None) -> dict[str, Voiceprint]:
    """Huella media de cada interviniente con voces asignadas (sin contar `exclude`)."""
    acc: dict[str, list[tuple[np.ndarray, float]]] = {}
    for path in Path(episodes_root).glob("*/episode.json"):
        if path.parent.name == exclude:
            continue
        for pid, vec, talk in _episode_prints(path):
            acc.setdefault(pid, []).append((vec, talk))
    out = {}
    for pid, items in acc.items():
        dims = {len(v) for v, _ in items}
        if len(dims) != 1:  # huellas de modelos distintos: se usa la más reciente forma común
            common = max(dims, key=lambda d: sum(len(v) == d for v, _ in items))
            items = [(v, t) for v, t in items if len(v) == common]
        weights = np.array([max(t, 1.0) for _, t in items])
        mean = _unit(np.average(np.stack([v for v, _ in items]), axis=0, weights=weights))
        if mean is not None:
            out[pid] = Voiceprint(mean, len(items))
    return out


def suggest(
    ep: Episode,
    prints: dict[str, Voiceprint],
    threshold: float,
    confident: float,
    known_profiles: set[str] | None = None,
) -> list[VoiceSuggestion]:
    """Para cada voz sin interviniente, el conocido que más se le parece.

    Cada interviniente se sugiere como mucho para una voz (la que más se le parece), y no se
    sugieren los que ya están en el episodio ni los que el usuario ha descartado en esa voz.
    """
    taken = {v.profile_id for v in ep.voices if v.profile_id}
    candidates = []
    for v in ep.voices:
        if v.profile_id or not v.embedding:
            continue
        u = _unit(v.embedding)
        if u is None:
            continue
        for pid, vp in prints.items():
            if pid in taken or pid in v.dismissed_profiles or len(vp.vector) != len(u):
                continue
            if known_profiles is not None and pid not in known_profiles:
                continue
            sim = float(u @ vp.vector)
            if sim >= threshold:
                candidates.append((sim, v.id, pid))
    out, used_voices, used_profiles = [], set(), set()
    for sim, vid, pid in sorted(candidates, reverse=True):
        if vid in used_voices or pid in used_profiles:
            continue
        used_voices.add(vid)
        used_profiles.add(pid)
        out.append(
            VoiceSuggestion(
                voice_id=vid, profile_id=pid, similarity=round(sim, 3),
                episodes=prints[pid].episodes, confident=sim >= confident,
            )
        )
    order = {v.id: i for i, v in enumerate(ep.voices)}
    return sorted(out, key=lambda s: order[s.voice_id])

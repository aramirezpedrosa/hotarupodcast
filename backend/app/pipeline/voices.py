"""Convierte palabras etiquetadas por el motor en turnos y voces sugeridas."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from collections.abc import Iterable

from ..models import Interval, Segment, Voice, Word
from .base import EngineResult, RawWord

# Colores por defecto de cada voz (editables en la fase de perfiles).
PALETTE = [
    "#3B82F6",
    "#F97316",
    "#10B981",
    "#A855F7",
    "#EF4444",
    "#14B8A6",
    "#EAB308",
    "#EC4899",
]

# Un silencio más largo que esto parte el turno aunque siga la misma voz.
MAX_GAP_IN_TURN = 2.0
# Preferimos clips de muestra de al menos esta duración.
MIN_GOOD_SAMPLE = 2.0
# Margen al principio del clip para no cortar la primera sílaba.
SAMPLE_LEAD_IN = 0.15


def _fill_timings(words: list[RawWord]) -> list[RawWord]:
    """Rellena tiempos ausentes (números, símbolos) con los de sus vecinas."""
    out: list[RawWord] = []
    n = len(words)
    for i, w in enumerate(words):
        start, end = w.start, w.end
        if start is None:
            prev_end = next((o.end for o in reversed(out) if o.end is not None), None)
            start = prev_end if prev_end is not None else 0.0
        if end is None:
            nxt = next((x.start for x in words[i + 1 : n] if x.start is not None), None)
            end = max(start, nxt if nxt is not None else start)
        out.append(RawWord(text=w.text, start=start, end=max(start, end), speaker=w.speaker))
    return out


def _fill_speakers(words: list[RawWord]) -> list[RawWord]:
    """Las palabras sin voz asignada heredan la de la palabra anterior (o la siguiente)."""
    speakers = [w.speaker for w in words]
    last = None
    for i, s in enumerate(speakers):
        if s is None:
            speakers[i] = last
        else:
            last = s
    nxt = None
    for i in range(len(speakers) - 1, -1, -1):
        if speakers[i] is None:
            speakers[i] = nxt
        else:
            nxt = speakers[i]
    return [
        RawWord(text=w.text, start=w.start, end=w.end, speaker=s or "SPEAKER_00")
        for w, s in zip(words, speakers)
    ]


def _join(words: list[Word]) -> str:
    text = ""
    for w in words:
        t = w.text.strip()
        if not t:
            continue
        if text and t[0] not in ",.;:!?)»…":
            text += " "
        text += t
    return text


def group_turns(pairs: Iterable[tuple[str, Word]]) -> list[Segment]:
    """Agrupa palabras (voz, palabra) en turnos: palabras seguidas de la misma voz
    sin silencios largos forman un segmento."""
    turns: list[tuple[str, list[Word]]] = []
    for vid, word in pairs:
        if turns and turns[-1][0] == vid and word.start - turns[-1][1][-1].end <= MAX_GAP_IN_TURN:
            turns[-1][1].append(word)
        else:
            turns.append((vid, [word]))
    return [
        Segment(
            id=f"seg-{i + 1}",
            start=ws[0].start,
            end=ws[-1].end,
            voice_id=vid,
            text=_join(ws),
            words=ws,
        )
        for i, (vid, ws) in enumerate(turns)
    ]


def _flatten(segments: list[Segment]) -> list[tuple[str, Word]]:
    return [(s.voice_id, w) for s in sorted(segments, key=lambda s: s.start) for w in s.words]


def build_episode_data(
    result: EngineResult, sample_seconds: float = 6.0
) -> tuple[list[Voice], list[Segment]]:
    """Agrupa palabras en turnos y crea una voz sugerida por cada etiqueta del motor.

    Las voces se numeran por orden de primera aparición ("Voz 1" es quien habla
    primero), que es como el usuario las reconoce al escuchar.
    """
    words = [w for w in result.words if w.text and w.text.strip()]
    if not words:
        return [], []
    words = _fill_speakers(_fill_timings(words))

    # Numeración por orden de aparición.
    voice_of: dict[str, str] = {}
    for w in words:
        if w.speaker not in voice_of:
            voice_of[w.speaker] = f"voz-{len(voice_of) + 1}"

    segments = group_turns(
        (voice_of[w.speaker], Word(text=w.text.strip(), start=round(w.start, 3), end=round(w.end, 3)))
        for w in words
    )

    sources: dict[str, list[str]] = defaultdict(list)
    for spk, vid in voice_of.items():
        sources[vid].append(spk)

    voices: list[Voice] = []
    for i, vid in enumerate(voice_of.values()):
        spk = sources[vid][0]
        voices.append(
            Voice(
                id=vid,
                label=f"Voz {i + 1}",
                color=PALETTE[i % len(PALETTE)],
                source_speakers=list(sources[vid]),
                embedding=result.embeddings.get(spk),
            )
        )
    refresh_voice_stats(voices, segments, sample_seconds)
    return voices, segments


def pick_sample(segments: list[Segment], max_seconds: float) -> Interval | None:
    """Elige el fragmento más representativo de una voz: su turno más largo."""
    if not segments:
        return None
    best = max(segments, key=lambda s: s.end - s.start)
    start = max(0.0, best.start - SAMPLE_LEAD_IN)
    end = min(best.end, start + max_seconds)
    if end - start < MIN_GOOD_SAMPLE:
        # Turno muy corto: lo alargamos un poco para que se entienda.
        end = start + min(max_seconds, MIN_GOOD_SAMPLE)
    return Interval(start=round(start, 3), end=round(end, 3))


def refresh_voice_stats(
    voices: list[Voice], segments: list[Segment], sample_seconds: float = 6.0
) -> None:
    """Recalcula tiempo de habla, número de turnos y clip de muestra de cada voz."""
    by_voice: dict[str, list[Segment]] = defaultdict(list)
    for s in segments:
        by_voice[s.voice_id].append(s)
    for v in voices:
        segs = by_voice.get(v.id, [])
        v.talk_time = round(sum(s.end - s.start for s in segs), 3)
        v.segment_count = len(segs)
        v.sample = pick_sample(segs, sample_seconds)
    voices.sort(key=lambda v: -v.talk_time)


def merge_voices(
    voices: list[Voice],
    segments: list[Segment],
    target_id: str,
    source_ids: list[str],
    sample_seconds: float = 6.0,
) -> tuple[list[Voice], list[Segment]]:
    """Fusiona varias voces en una (cuando el motor separó a la misma persona).

    Los turnos contiguos que quedan con la misma voz se unen en uno solo.
    """
    ids = {v.id for v in voices}
    if target_id not in ids:
        raise KeyError(target_id)
    missing = [s for s in source_ids if s not in ids]
    if missing:
        raise KeyError(missing[0])
    sources = {s for s in source_ids if s != target_id}
    if not sources:
        return voices, segments

    target = next(v for v in voices if v.id == target_id)
    for v in voices:
        if v.id in sources:
            target.source_speakers.extend(v.source_speakers)
            if target.embedding is None:
                target.embedding = v.embedding
            if target.profile_id is None:
                target.profile_id = v.profile_id
    voices = [v for v in voices if v.id not in sources]

    merged = group_turns(
        (target_id if vid in sources else vid, w) for vid, w in _flatten(segments)
    )
    refresh_voice_stats(voices, merged, sample_seconds)
    return voices, merged


class EditError(ValueError):
    pass


@dataclass
class WordRef:
    segment_id: str
    word_index: int


def new_voice(voices: list[Voice]) -> Voice:
    """Crea una voz vacía para un interviniente que el detector no separó."""
    used = {v.id for v in voices}
    n = 1
    while f"voz-{n}" in used:
        n += 1
    used_colors = {v.color for v in voices}
    color = next((c for c in PALETTE if c not in used_colors), PALETTE[(n - 1) % len(PALETTE)])
    return Voice(id=f"voz-{n}", label=f"Voz {n}", color=color)


def reassign_words(
    voices: list[Voice],
    segments: list[Segment],
    start: WordRef,
    end: WordRef,
    voice_id: str | None,
    sample_seconds: float = 6.0,
) -> tuple[list[Voice], list[Segment], str]:
    """Asigna a otra voz las palabras entre start y end (ambas incluidas).

    El tramo puede abarcar varios segmentos. Con voice_id None se crea una voz nueva.
    Los turnos se recomponen y las voces que se quedan sin intervenciones desaparecen.
    Devuelve las voces, los segmentos y la voz asignada.
    """
    flat = _flatten(segments)
    offsets: dict[str, int] = {}
    pos = 0
    for s in sorted(segments, key=lambda s: s.start):
        offsets[s.id] = pos
        pos += len(s.words)
    by_id = {s.id: s for s in segments}

    def index(ref: WordRef) -> int:
        seg = by_id.get(ref.segment_id)
        if seg is None:
            raise EditError(f"Segmento no encontrado: {ref.segment_id}")
        if not 0 <= ref.word_index < len(seg.words):
            raise EditError(f"Palabra fuera de rango en {ref.segment_id}")
        return offsets[seg.id] + ref.word_index

    a, b = sorted((index(start), index(end)))
    voices = [v.model_copy(deep=True) for v in voices]
    if voice_id is None:
        voice = new_voice(voices)
        voices.append(voice)
        voice_id = voice.id
    elif voice_id not in {v.id for v in voices}:
        raise EditError(f"Voz no encontrada: {voice_id}")

    pairs = [(voice_id if a <= i <= b else vid, w) for i, (vid, w) in enumerate(flat)]
    new_segments = group_turns(pairs)
    speaking = {s.voice_id for s in new_segments}
    voices = [v for v in voices if v.id in speaking]
    refresh_voice_stats(voices, new_segments, sample_seconds)
    return voices, new_segments, voice_id

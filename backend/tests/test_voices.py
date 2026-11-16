from app.pipeline.base import EngineResult, RawWord
from app.pipeline.voices import build_episode_data, merge_voices, pick_sample


def w(text, start, end, spk):
    return RawWord(text, start, end, spk)


def test_voices_numbered_by_first_appearance_and_sorted_by_talk_time():
    result = EngineResult(
        language="es",
        words=[
            w("Hola", 0.0, 0.5, "SPEAKER_01"),
            w("a", 0.6, 0.7, "SPEAKER_01"),
            w("todos.", 0.8, 1.2, "SPEAKER_01"),
            w("Gracias", 1.5, 2.0, "SPEAKER_00"),
            w("por", 2.1, 2.3, "SPEAKER_00"),
            w("invitarme", 2.4, 3.0, "SPEAKER_00"),
            w("hoy.", 3.1, 6.0, "SPEAKER_00"),
        ],
        embeddings={"SPEAKER_00": [0.1, 0.2]},
    )
    voices, segments = build_episode_data(result)

    assert [s.voice_id for s in segments] == ["voz-1", "voz-2"]
    assert segments[0].text == "Hola a todos."
    by_id = {v.id: v for v in voices}
    assert by_id["voz-1"].label == "Voz 1" and by_id["voz-1"].source_speakers == ["SPEAKER_01"]
    assert by_id["voz-2"].embedding == [0.1, 0.2]
    # Voz 2 habla más tiempo, así que va primero.
    assert voices[0].id == "voz-2"
    assert voices[0].talk_time == 4.5


def test_missing_timings_and_speakers_are_filled():
    result = EngineResult(
        language="es",
        words=[
            w("Son", 0.0, 0.3, "SPEAKER_00"),
            w("25", None, None, None),
            w("euros", 1.0, 1.4, "SPEAKER_00"),
        ],
    )
    voices, segments = build_episode_data(result)
    assert len(segments) == 1
    words = segments[0].words
    assert words[1].start == 0.3 and words[1].end == 1.0
    assert segments[0].text == "Son 25 euros"


def test_long_pause_splits_turn_of_same_voice():
    result = EngineResult(
        language="es",
        words=[w("Uno.", 0.0, 1.0, "A"), w("Dos.", 5.0, 6.0, "A")],
    )
    _, segments = build_episode_data(result)
    assert len(segments) == 2


def test_punctuation_is_not_preceded_by_space():
    result = EngineResult(language="es", words=[w("Hola", 0, 1, "A"), w(",", 1, 1.1, "A"), w("tú", 1.2, 2, "A")])
    _, segments = build_episode_data(result)
    assert segments[0].text == "Hola, tú"


def test_empty_result():
    assert build_episode_data(EngineResult(language="es", words=[])) == ([], [])


def test_pick_sample_uses_longest_turn_capped():
    from app.models import Segment

    segs = [
        Segment(id="a", start=0, end=1, voice_id="v", text="x"),
        Segment(id="b", start=10, end=30, voice_id="v", text="y"),
    ]
    s = pick_sample(segs, 6)
    assert s.start == 9.85 and s.end == 15.85


def test_merge_voices_joins_adjacent_turns():
    result = EngineResult(
        language="es",
        words=[
            w("Hola.", 0.0, 1.0, "A"),
            w("Sí.", 1.2, 2.0, "B"),
            w("Claro.", 2.2, 3.0, "C"),
            w("Vale.", 3.2, 4.0, "A"),
        ],
    )
    voices, segments = build_episode_data(result)
    assert len(voices) == 3 and len(segments) == 4

    voices, segments = merge_voices(voices, segments, "voz-2", ["voz-3"])
    assert {v.id for v in voices} == {"voz-1", "voz-2"}
    assert [s.voice_id for s in segments] == ["voz-1", "voz-2", "voz-1"]
    assert segments[1].text == "Sí. Claro."
    assert [s.id for s in segments] == ["seg-1", "seg-2", "seg-3"]
    merged = next(v for v in voices if v.id == "voz-2")
    assert merged.source_speakers == ["B", "C"]
    assert merged.segment_count == 1


def test_merge_unknown_voice_raises():
    voices, segments = build_episode_data(
        EngineResult(language="es", words=[w("Hola", 0, 1, "A")])
    )
    try:
        merge_voices(voices, segments, "voz-1", ["voz-9"])
    except KeyError as e:
        assert e.args[0] == "voz-9"
    else:
        raise AssertionError("debería fallar")


def _three_speakers():
    return build_episode_data(
        EngineResult(
            language="es",
            words=[
                w("Hola", 0.0, 0.4, "A"),
                w("a", 0.5, 0.6, "A"),
                w("todos.", 0.7, 1.0, "A"),
                w("Bienvenida.", 1.2, 2.0, "A"),
                w("Gracias.", 2.2, 3.0, "B"),
            ],
        )
    )


def test_reassign_middle_of_turn_splits_it():
    from app.pipeline.voices import WordRef, reassign_words

    voices, segments = _three_speakers()
    # "Bienvenida." la dijo en realidad la voz 2.
    voices, segments, vid = reassign_words(
        voices, segments, WordRef("seg-1", 3), WordRef("seg-1", 3), "voz-2"
    )
    assert vid == "voz-2"
    assert [(s.voice_id, s.text) for s in segments] == [
        ("voz-1", "Hola a todos."),
        ("voz-2", "Bienvenida. Gracias."),
    ]


def test_reassign_across_segments_to_new_voice_and_drop_empty_voice():
    from app.pipeline.voices import WordRef, reassign_words

    voices, segments = _three_speakers()
    voices, segments, vid = reassign_words(
        voices, segments, WordRef("seg-2", 0), WordRef("seg-1", 3), None
    )
    assert vid == "voz-3"
    assert [(s.voice_id, s.text) for s in segments] == [
        ("voz-1", "Hola a todos."),
        ("voz-3", "Bienvenida. Gracias."),
    ]
    # La voz 2 se queda sin intervenciones y desaparece.
    assert sorted(v.id for v in voices) == ["voz-1", "voz-3"]
    assert next(v for v in voices if v.id == "voz-3").label == "Voz 3"


def test_reassign_rejects_bad_positions():
    import pytest

    from app.pipeline.voices import EditError, WordRef, reassign_words

    voices, segments = _three_speakers()
    with pytest.raises(EditError):
        reassign_words(voices, segments, WordRef("seg-1", 9), WordRef("seg-1", 9), "voz-2")
    with pytest.raises(EditError):
        reassign_words(voices, segments, WordRef("seg-9", 0), WordRef("seg-1", 0), "voz-2")
    with pytest.raises(EditError):
        reassign_words(voices, segments, WordRef("seg-1", 0), WordRef("seg-1", 0), "voz-7")

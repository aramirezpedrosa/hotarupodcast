import io
import subprocess

import numpy as np
import pytest
from PIL import Image

from app.video import (
    FRAMES_PER_PHASE,
    Animation,
    Aspect,
    FrameRenderer,
    Template,
    VideoOptions,
    build_captions,
)
from tests.test_api import client, tone, upload, wait_done  # noqa: F401  (fixtures)
from tests.test_video import PEOPLE, wait_video

WORDS = [(i * 0.4, i * 0.4 + 0.3, w, 1)
         for i, w in enumerate("Hoy hablamos de los bosques del norte y de cómo cambian.".split())]


def _region(frame: np.ndarray, box) -> np.ndarray:
    x, y, w, h = box
    return frame[y:y + h, x:x + w]


def _frame(r, data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.uint8).reshape(r.H, r.W, 3)


def test_captions_split_on_speaker_pause_and_length():
    words = [(0.0, 0.3, "Hola", 0), (0.4, 0.7, "a", 0), (0.8, 1.0, "todos.", 0),
             (1.1, 1.4, "Gracias", 1), (1.5, 1.8, "por", 1),
             (4.0, 4.3, "venir", 1)]  # pausa larga antes
    caps = build_captions(words, max_chars=40)
    assert [[w for _, _, w in c.words] for c in caps] == [["Hola", "a", "todos."], ["Gracias", "por"], ["venir"]]
    assert [c.idx for c in caps] == [0, 1, 1]
    assert caps[0].end == 1.1  # se mantiene hasta que empieza el siguiente
    assert caps[1].end == pytest.approx(2.4)  # 0,6 s más tras la última palabra
    long = [(i * 0.2, i * 0.2 + 0.1, "palabra", 0) for i in range(20)]
    assert all(sum(len(w) + 1 for _, _, w in c.words) - 1 <= 30 for c in build_captions(long, 30))


@pytest.mark.parametrize("animation", [Animation.ondas, Animation.barras])
def test_moving_animations_change_over_time(animation):
    r = FrameRenderer(PEOPLE, VideoOptions(Template.mesa, Aspect.horizontal, animation=animation))
    slot = r.layout.slots[0]
    a, b = r.patch(slot, 0, 8, phase=0), r.patch(slot, 0, 8, phase=3)
    assert not np.array_equal(a, b)  # se mueve aunque el volumen no cambie
    assert not np.array_equal(r.patch(slot, 0, 2, phase=0), a)  # y sigue a la voz
    pulse = FrameRenderer(PEOPLE, VideoOptions(Template.mesa, Aspect.horizontal))
    assert np.array_equal(pulse.patch(slot, 0, 8, phase=0), pulse.patch(slot, 0, 8, phase=3))
    n = FRAMES_PER_PHASE * 3
    frames = list(r.frames(np.zeros(n, dtype=np.int16), np.full(n, 8)))
    assert frames[0] != frames[-1]


def test_cara_a_cara_splits_screen():
    for aspect in (Aspect.horizontal, Aspect.vertical):
        r = FrameRenderer(PEOPLE[:2], VideoOptions(Template.cara_a_cara, aspect))
        a, b = r.layout.slots
        assert a.panel and b.panel and a.radius == b.radius
        if aspect == Aspect.horizontal:
            assert a.y == b.y and a.x + a.w <= b.x
        else:
            assert a.x == b.x and a.y + a.h <= b.y
    # Con más de dos, el resto va en una fila abajo, sin panel.
    r = FrameRenderer(PEOPLE, VideoOptions(Template.cara_a_cara, Aspect.horizontal))
    assert [s.panel for s in r.layout.slots] == [True, True, False]
    assert r.layout.slots[2].radius < r.layout.slots[0].radius


def test_audiogram_wave_follows_audio():
    r = FrameRenderer(PEOPLE, VideoOptions(Template.audiograma, Aspect.cuadrado))
    assert r.layout.wave_box and r.layout.focus and r.layout.slots == []
    n = 30
    tl = np.array([-1] * 10 + [1] * 20, dtype=np.int16)
    curve = np.concatenate([np.zeros(10), np.linspace(0.2, 1, 20)]).astype(np.float32)
    frames = [_frame(r, f) for f in r.frames(tl, np.round(curve * 11).astype(np.int8), curve)]
    quiet, loud = _region(frames[9], r.layout.wave_box), _region(frames[-1], r.layout.wave_box)
    assert loud.astype(float).mean() > quiet.astype(float).mean() + 2
    # Las barras recientes llevan el color del que habla (naranja: más rojo que azul).
    right = loud[:, -loud.shape[1] // 10:].reshape(-1, 3).astype(int)
    lit = right[right.sum(axis=1) > 300]
    assert len(lit) and (lit[:, 0] > lit[:, 2]).mean() > 0.9


def test_subtitles_highlight_spoken_word():
    opts = VideoOptions(Template.foco, Aspect.horizontal, subtitles=True)
    r = FrameRenderer(PEOPLE, opts, WORDS)
    box = r.layout.caption_box
    assert box and box[1] + box[3] <= r.H
    assert all(s.y + s.h <= box[1] for s in r.layout.slots)  # no pisan a los avatares
    tl = np.full(25 * 3, 1, dtype=np.int16)
    frames = [_frame(r, f) for f in r.frames(tl, np.full(len(tl), 6))]
    caps = [_region(frames[k], box) for k in (2, 12, 22)]  # 0,08 s, 0,48 s, 0,88 s
    assert not np.array_equal(caps[0], caps[1]) and not np.array_equal(caps[1], caps[2])
    empty = FrameRenderer(PEOPLE, opts, [])
    assert not np.array_equal(_region(frames[2], box), _region(np.asarray(empty.base), box))


def test_extras_endpoints(client, tone):
    ep = wait_done(client, upload(client, tone, num_speakers=2).json()["id"])
    eid = ep["id"]
    for params in (
        {"template": "audiograma", "animation": "ondas", "subtitles": True},
        {"template": "cara_a_cara", "aspect": "9:16", "animation": "barras"},
    ):
        r = client.get(f"/api/episodes/{eid}/video/preview.png", params={**params, "width": 320})
        assert r.status_code == 200, r.text
        assert Image.open(io.BytesIO(r.content)).width == 320
    assert client.get(f"/api/episodes/{eid}/video/preview.png", params={"animation": "nada"}).status_code == 422

    r = client.post(f"/api/episodes/{eid}/videos",
                    json={"template": "cara_a_cara", "aspect": "1:1", "animation": "barras", "subtitles": True})
    assert r.status_code == 201
    video = wait_video(client, eid)["videos"][0]
    assert video["status"] == "done", video["error"]
    assert video["settings"]["animation"] == "barras" and video["settings"]["subtitles"] is True
    data = client.get(f"/api/episodes/{eid}/videos/{video['id']}/file").content
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv", "-"],
                           input=data, capture_output=True, check=True).stdout.decode()
    assert "h264" in probe and "aac" in probe

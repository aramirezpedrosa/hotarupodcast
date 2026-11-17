import io
import subprocess
import time

import numpy as np
import pytest
from PIL import Image

from app.video import (
    Aspect,
    FrameRenderer,
    LEVELS,
    Participant,
    SIZES,
    Template,
    VideoOptions,
    loudness_levels,
    make_layout,
    speaker_timeline,
)
from tests.test_api import client, tone, upload, wait_done  # noqa: F401  (fixtures)

PEOPLE = [
    Participant("voz-1", "María López Fernández de la Vega", "#3B82F6", "Presentadora"),
    Participant("voz-2", "Juan", "#F97316", "Director de Innovación y Nuevos Formatos"),
    Participant("voz-3", "Voz 3", "#10B981"),
]


@pytest.mark.parametrize("template", list(Template))
@pytest.mark.parametrize("aspect", list(Aspect))
@pytest.mark.parametrize("n", [1, 2, 3, 5, 8])
def test_layout_slots_fit_and_do_not_overlap(template, aspect, n):
    W, H = SIZES[aspect]
    layout = make_layout(n, VideoOptions(template, aspect, title="Título"))
    boxes = layout.slots + ([layout.focus] if layout.focus else [])
    for s in boxes:
        assert 0 <= s.x and s.x + s.w <= W and 0 <= s.y and s.y + s.h <= H
        # El avatar con el pulso al máximo cabe en su hueco.
        assert s.cx - 1.3 * s.radius >= s.x - 1 and s.cx + 1.3 * s.radius <= s.x + s.w + 1
        assert s.cy - 1.3 * s.radius >= s.y - 1
        assert s.radius >= 20
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y


def test_speaker_timeline_holds_briefly_after_speaking():
    tl = speaker_timeline([(0.0, 1.0, 0), (1.1, 2.0, 1), (5.0, 6.0, 0)], n_frames=200, fps=25)
    assert tl[10] == 0
    assert tl[26] == 0  # 1.04 s: sigue activo un momento tras terminar
    assert tl[28] == 1  # 1.12 s: ya habla el segundo
    assert tl[25 * 3] == -1  # silencio largo: nadie
    assert tl[25 * 5 + 5] == 0


def test_loudness_levels_follow_the_voice(tmp_path):
    path = tmp_path / "a.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=200:d=4",
         "-af", "volume='if(between(t,1,2),1,0.001)':eval=frame", str(path)],
        check=True,
    )
    lv = loudness_levels(path, n_frames=100, fps=25)
    assert lv.max() == LEVELS - 1
    assert lv[37] >= LEVELS - 2  # 1.5 s: voz fuerte
    assert lv[10] == 0  # 0.4 s: casi silencio
    assert lv[52] < lv[49]  # al callar, baja de forma gradual


def test_frames_highlight_active_speaker():
    r = FrameRenderer(PEOPLE, VideoOptions(Template.mesa, Aspect.cuadrado))
    idle = np.asarray(r.still(None))
    talk = np.asarray(r.still(1, 1.0))
    slot = r.layout.slots[1]
    region = (slice(slot.y, slot.y + slot.h), slice(slot.x, slot.x + slot.w))
    other = r.layout.slots[0]
    other_region = (slice(other.y, other.y + other.h), slice(other.x, other.x + other.w))
    assert talk[region].mean() > idle[region].mean()  # el que habla se ilumina
    assert np.array_equal(talk[other_region], idle[other_region])  # el resto no cambia
    frames = list(r.frames(np.array([-1, 1, 1, -1]), np.array([0, 5, 11, 0])))
    assert len(frames) == 4 and all(len(f) == r.W * r.H * 3 for f in frames)
    assert frames[0] == frames[3]  # vuelve al reposo


def wait_video(client, episode_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        ep = client.get(f"/api/episodes/{episode_id}").json()
        v = ep["videos"][0]
        if v["status"] in ("done", "error"):
            return ep
        time.sleep(0.2)
    raise AssertionError("el vídeo no terminó a tiempo")


def test_video_endpoints(client, tone):
    ep = wait_done(client, upload(client, tone, num_speakers=2).json()["id"])
    eid = ep["id"]

    r = client.get(f"/api/episodes/{eid}/video/preview.png",
                   params={"template": "foco", "aspect": "9:16", "speaker": "voz-2", "width": 300})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(r.content))
    assert img.size == (300, 533)

    r = client.post(f"/api/episodes/{eid}/videos", json={"template": "mesa", "aspect": "1:1"})
    assert r.status_code == 201
    ep = wait_video(client, eid)
    video = ep["videos"][0]
    assert video["status"] == "done", video["error"]
    assert video["progress"] == 1.0 and video["audio"] == "original"

    r = client.get(f"/api/episodes/{eid}/videos/{video['id']}/file", params={"download": True})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height:format=duration",
         "-of", "json", "-"], input=r.content, capture_output=True, check=True,
    ).stdout.decode()
    assert '"h264"' in probe and '"aac"' in probe and '"width": 1080' in probe

    # Las ediciones del episodio no pisan el estado del vídeo.
    client.patch(f"/api/episodes/{eid}/voices/voz-1", json={"label": "Ana"})
    assert client.get(f"/api/episodes/{eid}").json()["videos"][0]["status"] == "done"

    r = client.delete(f"/api/episodes/{eid}/videos/{video['id']}")
    assert r.status_code == 200 and r.json()["videos"] == []

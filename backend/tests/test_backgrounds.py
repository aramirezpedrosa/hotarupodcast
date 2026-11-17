import io

import numpy as np
import pytest
from PIL import Image

from app.backgrounds import PRESET_IDS, make_background, preset_background
from app.video import Aspect, FrameRenderer, SIZES, Template, VideoOptions
from tests.test_api import client, tone, upload, wait_done  # noqa: F401  (fixtures)
from tests.test_video import PEOPLE, wait_video


def _jpeg(size=(800, 500), color=(40, 120, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.mark.parametrize("preset", PRESET_IDS)
@pytest.mark.parametrize("aspect", list(Aspect))
def test_presets_fill_every_format(preset, aspect):
    size = SIZES[aspect]
    img = make_background(preset, size)
    assert img.size == size and img.mode == "RGB"
    arr = np.asarray(img, dtype=np.float32)
    # Oscuros para que se lean los nombres, pero no negros.
    assert 5 < arr.mean() < 110
    if preset != "liso":
        assert arr.std() > 4  # hay paisaje, no un color plano


def test_presets_are_deterministic():
    preset_background.cache_clear()
    a = np.asarray(make_background("bosque", (640, 360)))
    preset_background.cache_clear()
    b = np.asarray(make_background("bosque", (640, 360)))
    assert np.array_equal(a, b)
    assert not np.array_equal(a, np.asarray(make_background("oceano", (640, 360))))


def test_custom_image_is_cropped_and_darkened(tmp_path):
    path = tmp_path / "foto.jpg"
    path.write_bytes(_jpeg((400, 900), (200, 220, 240)))
    img = make_background(None, (1920, 1080), path)
    assert img.size == (1920, 1080)
    assert np.asarray(img).mean() < 200 * 0.8  # oscurecida


def test_renderer_draws_on_background():
    plain = FrameRenderer(PEOPLE, VideoOptions(Template.mesa, Aspect.horizontal))
    forest = FrameRenderer(PEOPLE, VideoOptions(Template.mesa, Aspect.horizontal, background="bosque"))
    assert not np.array_equal(plain.base, forest.base)
    # El fondo se ve también dentro de los huecos de cada interviniente.
    s = forest.layout.slots[0]
    corner = forest.base[s.y:s.y + 10, s.x:s.x + 10]
    assert np.array_equal(corner, forest.bg[s.y:s.y + 10, s.x:s.x + 10])


def test_background_endpoints(client, tone):
    r = client.get("/api/backgrounds")
    ids = [b["id"] for b in r.json()]
    assert ids == list(PRESET_IDS)

    r = client.get("/api/backgrounds/aurora/thumb.jpg", params={"aspect": "9:16", "width": 180})
    assert r.status_code == 200 and Image.open(io.BytesIO(r.content)).size == (180, 320)

    r = client.post("/api/backgrounds", files={"file": ("playa.jpg", io.BytesIO(b"no"), "image/jpeg")})
    assert r.status_code == 415
    r = client.post("/api/backgrounds", files={"file": ("playa.jpg", io.BytesIO(_jpeg()), "image/jpeg")})
    assert r.status_code == 201
    custom = r.json()
    assert custom["name"] == "playa" and custom["kind"] == "custom"
    assert client.get("/api/backgrounds").json()[-1]["id"] == custom["id"]
    assert client.get(f"/api/backgrounds/{custom['id']}/thumb.jpg").status_code == 200

    ep = wait_done(client, upload(client, tone, num_speakers=2).json()["id"])
    eid = ep["id"]
    for bg in ("montanas", custom["id"]):
        r = client.get(f"/api/episodes/{eid}/video/preview.png", params={"background": bg, "width": 320})
        assert r.status_code == 200
    r = client.get(f"/api/episodes/{eid}/video/preview.png", params={"background": "nada"})
    assert r.status_code == 422
    assert client.post(f"/api/episodes/{eid}/videos", json={"background": "nada"}).status_code == 422

    r = client.post(f"/api/episodes/{eid}/videos", json={"aspect": "9:16", "background": custom["id"]})
    assert r.status_code == 201
    video = wait_video(client, eid)["videos"][0]
    assert video["status"] == "done", video["error"]
    assert video["settings"]["background"] == custom["id"]

    assert client.delete(f"/api/backgrounds/{custom['id']}").status_code == 204
    assert client.delete(f"/api/backgrounds/{custom['id']}").status_code == 404
    assert [b["id"] for b in client.get("/api/backgrounds").json()] == list(PRESET_IDS)

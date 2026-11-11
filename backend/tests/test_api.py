import subprocess
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.pipeline.fake_engine import FakeEngine


@pytest.fixture
def tone(tmp_path):
    """Audio de prueba de 10 s generado con FFmpeg."""
    path = tmp_path / "prueba.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=10",
         "-codec:a", "libmp3lame", str(path)],
        check=True,
    )
    return path


@pytest.fixture
def client(tmp_path):
    settings = replace(Settings(), data_dir=tmp_path / "data", engine="fake")
    with TestClient(create_app(settings, FakeEngine(turn_seconds=2.5))) as c:
        yield c


def wait_done(client, episode_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        ep = client.get(f"/api/episodes/{episode_id}").json()
        if ep["status"] in ("done", "error"):
            return ep
        time.sleep(0.1)
    raise AssertionError("el procesado no terminó a tiempo")


def upload(client, tone, **form):
    with open(tone, "rb") as f:
        return client.post(
            "/api/episodes",
            files={"file": ("Episodio 1.mp3", f, "audio/mpeg")},
            data={k: str(v) for k, v in form.items()},
        )


def test_full_flow(client, tone):
    r = upload(client, tone, num_speakers=3)
    assert r.status_code == 201, r.text
    ep_id = r.json()["id"]
    assert r.json()["title"] == "Episodio 1"

    ep = wait_done(client, ep_id)
    assert ep["status"] == "done", ep["error"]
    assert 9.9 < ep["duration"] < 10.2
    assert len(ep["voices"]) == 3
    assert ep["segments"][0]["voice_id"] == "voz-1"

    listing = client.get("/api/episodes").json()
    assert listing[0]["id"] == ep_id and listing[0]["voice_count"] == 3

    sample = client.get(f"/api/episodes/{ep_id}/voices/voz-1/sample")
    assert sample.status_code == 200
    assert sample.headers["content-type"] == "audio/mpeg"
    assert len(sample.content) > 1000

    r = client.post(
        f"/api/episodes/{ep_id}/voices/merge", json={"target_id": "voz-1", "source_ids": ["voz-3"]}
    )
    assert r.status_code == 200, r.text
    assert sorted(v["id"] for v in r.json()["voices"]) == ["voz-1", "voz-2"]


def test_rejects_unknown_format(client, tmp_path):
    p = tmp_path / "notas.txt"
    p.write_text("hola")
    with open(p, "rb") as f:
        r = client.post("/api/episodes", files={"file": ("notas.txt", f, "text/plain")})
    assert r.status_code == 415


def test_broken_audio_reports_error(client, tmp_path):
    p = tmp_path / "roto.mp3"
    p.write_bytes(b"esto no es audio")
    with open(p, "rb") as f:
        r = client.post("/api/episodes", files={"file": ("roto.mp3", f, "audio/mpeg")})
    ep = wait_done(client, r.json()["id"])
    assert ep["status"] == "error" and ep["error"]


def test_unknown_episode_404(client):
    assert client.get("/api/episodes/noexiste").status_code == 404
    assert client.get("/api/episodes/../etc").status_code == 404

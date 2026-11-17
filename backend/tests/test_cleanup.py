import subprocess
import time

import numpy as np
import pytest

from app.cleanup import (
    CleanupOptions,
    Strength,
    clean_audio,
    gain_envelope,
    speech_intervals,
    SAMPLE_RATE,
)
from tests.test_api import client, tone, upload, wait_done  # noqa: F401  (fixtures)


def test_speech_intervals_pad_merge_and_offset():
    spans = speech_intervals([(1.0, 2.0), (2.3, 3.0), (6.0, 7.0)], keep_breaths=False)
    # 2.0 + 0.25 y 2.3 - 0.15 se solapan: un solo tramo. El hueco de 3 s se mantiene.
    flat = lambda xs: [v for pair in xs for v in pair]
    assert flat(spans) == pytest.approx([0.85, 3.25, 5.85, 7.25])
    shifted = speech_intervals([(1.0, 2.0), (6.0, 7.0)], keep_breaths=True, offset=5.0)
    assert flat(shifted) == pytest.approx([0.85, 2.6])


def test_gain_envelope_ramps_softly():
    g = gain_envelope(0, SAMPLE_RATE * 3, [(1.0, 2.0)], gap_gain=0.1)
    at = lambda t: g[int(t * SAMPLE_RATE)]
    assert at(1.5) == pytest.approx(1.0)
    assert at(0.2) == pytest.approx(0.1)
    # A mitad de la rampa (60 ms antes de la voz), a medio camino.
    assert at(1.0 - 0.06) == pytest.approx(0.55, abs=0.02)
    assert np.all(np.diff(g[: SAMPLE_RATE]) >= -1e-7)  # sube sin saltos antes de la voz


def _noisy_speech(path):
    """10 s de ruido de fondo constante con 'voz' (un tono) en 1-3 s y 6-8 s."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "anoisesrc=d=10:c=white:a=0.02:r=48000",
         "-f", "lavfi", "-i", "sine=f=300:d=10:r=48000",
         "-filter_complex",
         "[1]volume=0.3,volume='between(t,1,3)+between(t,6,8)':eval=frame[v];"
         "[0][v]amix=inputs=2:normalize=0",
         "-ac", "1", str(path)],
        check=True,
    )


def _db(path, a, b):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(a), "-t", str(b - a), "-i", str(path),
         "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    x = np.frombuffer(raw, np.float32)
    return 20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-12)


@pytest.mark.parametrize("strength,min_gain_db", [("suave", 5), ("media", 10), ("fuerte", 15)])
def test_clean_audio_lowers_noise_in_gaps_not_voice(tmp_path, strength, min_gain_db):
    src, out = tmp_path / "in.wav", tmp_path / "out.wav"
    _noisy_speech(src)
    clean_audio(src, out, [(1, 3), (6, 8)], CleanupOptions(Strength(strength), keep_breaths=False))
    before = _db(src, 1.5, 2.5) - _db(src, 4.0, 5.0)
    after = _db(out, 1.5, 2.5) - _db(out, 4.0, 5.0)
    # La diferencia entre la voz y el ruido de los huecos crece, pero el hueco no queda mudo.
    assert after - before >= min_gain_db
    assert _db(out, 4.0, 5.0) > -90
    # La duración no cambia.
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True).stdout)
    assert dur == pytest.approx(10.0, abs=0.05)


def test_clean_audio_fragment(tmp_path):
    src, out = tmp_path / "in.wav", tmp_path / "frag.mp3"
    _noisy_speech(src)
    clean_audio(src, out, [(1, 3), (6, 8)], CleanupOptions(), start=5.0, duration=4.0)
    assert out.exists() and out.stat().st_size > 1000


def wait_cleanup(client, episode_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        ep = client.get(f"/api/episodes/{episode_id}").json()
        if ep["cleanup"] and ep["cleanup"]["status"] in ("done", "error"):
            return ep
        time.sleep(0.1)
    raise AssertionError("la limpieza no terminó a tiempo")


def test_cleanup_endpoints(client, tone):
    ep = wait_done(client, upload(client, tone, num_speakers=2).json()["id"])
    eid = ep["id"]
    assert ep["cleanup"] is None
    assert client.get(f"/api/episodes/{eid}/cleanup/audio").status_code == 404

    r = client.get(f"/api/episodes/{eid}/cleanup/preview", params={"start": 2, "duration": 5, "strength": "fuerte"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert client.get(f"/api/episodes/{eid}/cleanup/preview", params={"duration": 60}).status_code == 422

    r = client.put(f"/api/episodes/{eid}/cleanup", json={"strength": "suave", "keep_breaths": False})
    assert r.status_code == 200
    ep = wait_cleanup(client, eid)
    assert ep["cleanup"]["status"] == "done", ep["cleanup"]["error"]
    assert ep["cleanup"]["settings"] == {"strength": "suave", "keep_breaths": False}
    assert ep["cleanup"]["stale"] is False

    r = client.get(f"/api/episodes/{eid}/cleanup/audio", params={"download": True})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]

    # Cambiar los tramos de voz deja la limpieza desactualizada.
    first = ep["segments"][0]
    r = client.post(
        f"/api/episodes/{eid}/segments/reassign",
        json={"start": {"segment_id": first["id"], "word_index": 0},
              "end": {"segment_id": first["id"], "word_index": 0}, "voice_id": None},
    )
    assert r.json()["cleanup"]["stale"] is True

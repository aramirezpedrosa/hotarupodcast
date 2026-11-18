import numpy as np

from app.models import Episode, Voice
from app.recognition import Voiceprint, profile_voiceprints, suggest
from tests.test_api import client, tone, upload, wait_done  # noqa: F401  (fixtures)


def _vec(seed, noise=0.0, dim=32):
    v = np.random.default_rng(seed).normal(size=dim)
    if noise:
        v = v + np.random.default_rng(seed + 999).normal(scale=noise, size=dim)
    return v.tolist()


def _ep(*voices):
    return Episode(id="e1", title="t", original_filename="a.mp3", voices=list(voices))


def test_suggest_picks_best_unique_match():
    prints = {"ana": Voiceprint(np.array(_vec(1)) / np.linalg.norm(_vec(1)), 2),
              "luis": Voiceprint(np.array(_vec(2)) / np.linalg.norm(_vec(2)), 1)}
    ep = _ep(
        Voice(id="voz-1", label="Voz 1", color="#000000", embedding=_vec(2, 0.3)),
        Voice(id="voz-2", label="Voz 2", color="#000000", embedding=_vec(1, 0.3)),
        Voice(id="voz-3", label="Voz 3", color="#000000", embedding=_vec(3)),  # desconocida
    )
    out = suggest(ep, prints, 0.5, 0.7)
    assert [(s.voice_id, s.profile_id) for s in out] == [("voz-1", "luis"), ("voz-2", "ana")]
    assert all(s.confident and s.similarity > 0.9 for s in out)
    assert out[1].episodes == 2

    # Si dos voces se parecen al mismo, solo se sugiere en la más parecida.
    ep = _ep(
        Voice(id="voz-1", label="Voz 1", color="#000000", embedding=_vec(1, 0.6)),
        Voice(id="voz-2", label="Voz 2", color="#000000", embedding=_vec(1, 0.2)),
    )
    assert [(s.voice_id, s.profile_id) for s in suggest(ep, prints, 0.5, 0.7)] == [("voz-2", "ana")]

    # No se sugiere quien ya está en el episodio ni quien se ha descartado.
    ep = _ep(
        Voice(id="voz-1", label="Voz 1", color="#000000", embedding=_vec(1), profile_id="ana"),
        Voice(id="voz-2", label="Voz 2", color="#000000", embedding=_vec(2), dismissed_profiles=["luis"]),
    )
    assert suggest(ep, prints, 0.5, 0.7) == []


def test_voiceprint_is_weighted_mean_over_episodes(tmp_path):
    root = tmp_path / "episodes"
    for i, (emb, talk) in enumerate([(_vec(1), 100.0), (_vec(2), 1.0)]):
        e = _ep(Voice(id="voz-1", label="V", color="#000000", embedding=emb, talk_time=talk, profile_id="ana"))
        (root / f"e{i}").mkdir(parents=True)
        (root / f"e{i}" / "episode.json").write_text(e.model_dump_json())
    prints = profile_voiceprints(root)
    assert prints["ana"].episodes == 2
    a = np.array(_vec(1)) / np.linalg.norm(_vec(1))
    assert prints["ana"].vector @ a > 0.95
    assert profile_voiceprints(root, exclude="e0")["ana"].vector @ a < 0.5


def test_recurring_guest_is_recognised(client, tone):
    # Primer episodio: el usuario dice quién es cada voz.
    first = wait_done(client, upload(client, tone, num_speakers=2).json()["id"])
    ana = client.post("/api/profiles", json={"name": "Ana", "color": "#AA3366"}).json()
    luis = client.post("/api/profiles", json={"name": "Luis"}).json()
    client.patch(f"/api/episodes/{first['id']}/voices/voz-1", json={"profile_id": ana["id"]})
    client.patch(f"/api/episodes/{first['id']}/voices/voz-2", json={"profile_id": luis["id"]})
    assert client.get(f"/api/episodes/{first['id']}/suggestions").json() == []

    # Segundo episodio: se reconocen solos.
    second = wait_done(client, upload(client, tone, num_speakers=3).json()["id"])
    eid = second["id"]
    sugs = client.get(f"/api/episodes/{eid}/suggestions").json()
    assert [(s["voice_id"], s["profile_id"]) for s in sugs] == [("voz-1", ana["id"]), ("voz-2", luis["id"])]
    assert all(s["confident"] and s["episodes"] == 1 for s in sugs)

    # "No es Luis" en la voz 2: deja de sugerirse; se puede deshacer.
    r = client.post(f"/api/episodes/{eid}/voices/voz-2/dismiss", json={"profile_id": luis["id"]})
    assert r.status_code == 200
    assert [s["voice_id"] for s in client.get(f"/api/episodes/{eid}/suggestions").json()] == ["voz-1"]
    client.post(f"/api/episodes/{eid}/undo")
    assert len(client.get(f"/api/episodes/{eid}/suggestions").json()) == 2

    # Aceptar todas las seguras de una vez, con el color del perfil.
    ep = client.post(f"/api/episodes/{eid}/suggestions/accept", json={}).json()
    voices = {v["id"]: v for v in ep["voices"]}
    assert voices["voz-1"]["profile_id"] == ana["id"] and voices["voz-1"]["color"] == "#AA3366"
    assert voices["voz-2"]["profile_id"] == luis["id"] and voices["voz-3"]["profile_id"] is None
    assert ep["can_undo"]
    assert client.get(f"/api/episodes/{eid}/suggestions").json() == []

    # Si se borra el perfil, ya no se sugiere.
    client.post(f"/api/episodes/{eid}/undo")
    client.delete(f"/api/profiles/{luis['id']}")
    assert [s["profile_id"] for s in client.get(f"/api/episodes/{eid}/suggestions").json()] == [ana["id"]]

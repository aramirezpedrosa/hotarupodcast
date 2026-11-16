import io

from tests.test_api import client, tone, upload, wait_done  # noqa: F401  (fixtures)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def ready_episode(client, tone):
    ep = wait_done(client, upload(client, tone, num_speakers=2).json()["id"])
    assert ep["status"] == "done"
    return ep


def test_profile_crud_and_photo(client):
    r = client.post("/api/profiles", json={"name": "  María López ", "role": "Presentadora"})
    assert r.status_code == 201
    prof = r.json()
    assert prof["name"] == "María López" and prof["role"] == "Presentadora" and prof["photo"] is None

    r = client.patch(f"/api/profiles/{prof['id']}", json={"role": "", "info": "Periodista"})
    assert r.json()["role"] is None and r.json()["info"] == "Periodista"

    r = client.put(
        f"/api/profiles/{prof['id']}/photo", files={"file": ("cara.png", io.BytesIO(PNG), "image/png")}
    )
    assert r.status_code == 200 and r.json()["photo"].endswith(".png")
    assert client.get(f"/api/profiles/{prof['id']}/photo").content == PNG

    # Un archivo que dice ser PNG pero no lo es se rechaza.
    r = client.put(
        f"/api/profiles/{prof['id']}/photo",
        files={"file": ("x.png", io.BytesIO(b"hola"), "image/png")},
    )
    assert r.status_code == 415

    assert [p["name"] for p in client.get("/api/profiles").json()] == ["María López"]
    assert client.delete(f"/api/profiles/{prof['id']}").status_code == 204
    assert client.get("/api/profiles").json() == []


def test_assign_profile_to_voice_and_undo_redo(client, tone):
    ep = ready_episode(client, tone)
    prof = client.post("/api/profiles", json={"name": "Ana", "color": "#112233"}).json()

    r = client.patch(f"/api/episodes/{ep['id']}/voices/voz-1", json={"profile_id": prof["id"]})
    assert r.status_code == 200, r.text
    voice = next(v for v in r.json()["voices"] if v["id"] == "voz-1")
    assert voice["profile_id"] == prof["id"] and voice["color"] == "#112233"
    assert r.json()["can_undo"] is True

    # El mismo perfil no puede estar en dos voces del episodio.
    r = client.patch(f"/api/episodes/{ep['id']}/voices/voz-2", json={"profile_id": prof["id"]})
    assert r.status_code == 409

    # Cambiar el color del perfil cambia el de la voz.
    client.patch(f"/api/profiles/{prof['id']}", json={"color": "#445566"})
    ep2 = client.get(f"/api/episodes/{ep['id']}").json()
    assert next(v for v in ep2["voices"] if v["id"] == "voz-1")["color"] == "#445566"

    r = client.post(f"/api/episodes/{ep['id']}/undo")
    assert next(v for v in r.json()["voices"] if v["id"] == "voz-1")["profile_id"] is None
    assert r.json()["can_redo"] is True
    r = client.post(f"/api/episodes/{ep['id']}/redo")
    assert next(v for v in r.json()["voices"] if v["id"] == "voz-1")["profile_id"] == prof["id"]

    # Al borrar el perfil, la voz se queda sin él.
    client.delete(f"/api/profiles/{prof['id']}")
    ep3 = client.get(f"/api/episodes/{ep['id']}").json()
    assert next(v for v in ep3["voices"] if v["id"] == "voz-1")["profile_id"] is None


def test_reassign_endpoint(client, tone):
    ep = ready_episode(client, tone)
    first = ep["segments"][0]
    assert first["voice_id"] == "voz-1"
    last = len(first["words"]) - 1
    r = client.post(
        f"/api/episodes/{ep['id']}/segments/reassign",
        json={
            "start": {"segment_id": first["id"], "word_index": 0},
            "end": {"segment_id": first["id"], "word_index": last},
            "voice_id": None,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["segments"][0]["voice_id"] == "voz-3"

    r = client.post(
        f"/api/episodes/{ep['id']}/segments/reassign",
        json={
            "start": {"segment_id": "seg-999", "word_index": 0},
            "end": {"segment_id": "seg-1", "word_index": 0},
            "voice_id": "voz-1",
        },
    )
    assert r.status_code == 422

    r = client.post(f"/api/episodes/{ep['id']}/undo")
    assert r.json()["segments"][0]["voice_id"] == "voz-1"
    assert r.json()["can_undo"] is False
    assert client.post(f"/api/episodes/{ep['id']}/undo").status_code == 409

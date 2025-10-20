import uuid
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

# Importá tu router real
from app.secret.endpoints import secret_router
from app.secret import endpoints as endpoints_mod
from app.secret.enums import SecretType

# Helpers
def make_app():
    app = FastAPI()
    app.include_router(secret_router)
    return app

def make_secret_dto(
    secret_id,
    game_id,
    owner_id,
    type_="COMMON",
    desc="...",
    revealed=False,
    name="Secret",
):
    return SimpleNamespace(
        id=secret_id,
        game_id=game_id,
        owner_player_id=owner_id,
        role=type_ if isinstance(type_, SecretType) else SecretType(type_), 
        name=name,
        description=desc,
        revealed=revealed,
    )

# =========================
# TESTS
# =========================

def test_missing_player_and_secret_returns_400(monkeypatch):
    app = make_app()
    c = TestClient(app)
    gid = uuid.uuid4()
    r = c.get(f"/secrets?game_id={gid}")
    assert r.status_code == 400
    assert "player_id is required" in r.text.lower() or "required" in r.text.lower()


def test_secretid_without_playerid_returns_400(monkeypatch):
    app = make_app()
    c = TestClient(app)
    gid = uuid.uuid4()
    sid = uuid.uuid4()
    r = c.get(f"/secrets?game_id={gid}&secret_id={sid}")
    assert r.status_code == 400


def test_secret_not_found_returns_404(monkeypatch):
    app = make_app()
    c = TestClient(app)
    gid = uuid.uuid4()
    pid = uuid.uuid4()
    sid = uuid.uuid4()

    def fake_get_secret_by_id(db, secret_id):
        return None

    monkeypatch.setattr(endpoints_mod.SecretService, "get_secret_by_id", fake_get_secret_by_id)

    r = c.get(f"/secrets?game_id={gid}&player_id={pid}&secret_id={sid}")
    assert r.status_code == 404


def test_secret_game_mismatch_returns_404(monkeypatch):
    app = make_app()
    c = TestClient(app)
    gid = uuid.uuid4()
    pid = uuid.uuid4()
    sid = uuid.uuid4()
    other_gid = uuid.uuid4()

    def fake_get_secret_by_id(db, secret_id):
        return make_secret_dto(sid, other_gid, pid)

    monkeypatch.setattr(endpoints_mod.SecretService, "get_secret_by_id", fake_get_secret_by_id)

    r = c.get(f"/secrets?game_id={gid}&player_id={pid}&secret_id={sid}")
    assert r.status_code == 404


def test_secret_owner_mismatch_returns_404(monkeypatch):
    app = make_app()
    c = TestClient(app)
    gid = uuid.uuid4()
    pid = uuid.uuid4()
    sid = uuid.uuid4()
    other_owner = uuid.uuid4()

    def fake_get_secret_by_id(db, secret_id):
        return make_secret_dto(sid, gid, other_owner)

    monkeypatch.setattr(endpoints_mod.SecretService, "get_secret_by_id", fake_get_secret_by_id)

    r = c.get(f"/secrets?game_id={gid}&player_id={pid}&secret_id={sid}")
    assert r.status_code == 404


def test_get_secret_ok_returns_200_and_payload(monkeypatch):
    app = make_app()
    c = TestClient(app)
    gid = uuid.uuid4()
    pid = uuid.uuid4()
    sid = uuid.uuid4()

    dto = make_secret_dto(sid, gid, pid, type_=SecretType.MURDERER, desc="foo", revealed=True)

    def fake_get_secret_by_id(db, secret_id):
        assert secret_id == sid
        return dto

    monkeypatch.setattr(endpoints_mod.SecretService, "get_secret_by_id", fake_get_secret_by_id)

    r = c.get(f"/secrets?game_id={gid}&player_id={pid}&secret_id={sid}")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list) and len(data) == 1
    item = data[0]
    assert item["id"] == str(sid)
    assert item["owner_player_id"] == str(pid)
    assert item["type"] == "MURDERER"
    assert item["description"] == "foo"
    assert item.get("revealed") is True


def test_list_by_player_filters_by_game_returns_only_matching(monkeypatch):
    app = make_app()
    c = TestClient(app)
    gid = uuid.uuid4()
    other_gid = uuid.uuid4()
    pid = uuid.uuid4()

    dto_ok1 = make_secret_dto(uuid.uuid4(), gid, pid, type_="COMMON")
    dto_ok2 = make_secret_dto(uuid.uuid4(), gid, pid, type_="ACCOMPLICE")
    dto_other_game = make_secret_dto(uuid.uuid4(), other_gid, pid)

    def fake_get_secrets_by_player_id(db, player_id):
        assert player_id == pid
        return [dto_ok1, dto_other_game, dto_ok2]

    monkeypatch.setattr(endpoints_mod.SecretService, "get_secrets_by_player_id", fake_get_secrets_by_player_id)

    r = c.get(f"/secrets?game_id={gid}&player_id={pid}")
    assert r.status_code == 200
    data = r.json()
    # Debe filtrar por game_id en el endpoint
    returned_ids = {item["id"] for item in data}
    assert str(dto_ok1.id) in returned_ids
    assert str(dto_ok2.id) in returned_ids
    assert str(dto_other_game.id) not in returned_ids


def test_list_by_player_empty_ok(monkeypatch):
    app = make_app()
    c = TestClient(app)
    gid = uuid.uuid4()
    pid = uuid.uuid4()

    def fake_get_secrets_by_player_id(db, player_id):
        return []

    monkeypatch.setattr(endpoints_mod.SecretService, "get_secrets_by_player_id", fake_get_secrets_by_player_id)

    r = c.get(f"/secrets?game_id={gid}&player_id={pid}")
    assert r.status_code == 200
    assert r.json() == []

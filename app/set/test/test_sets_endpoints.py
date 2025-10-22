import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.set import endpoints as endpoints_mod
from app.set.dtos import SetOut, SetPlayResult
from app.set.endpoints import sets_router
from app.set.enums import SetType
from app.game.schemas import EndGameResult
from app.set import schemas


def make_app():
    app = FastAPI()
    app.include_router(sets_router)
    return app


def make_set_out(set_id, game_id, owner_id, type_: SetType = SetType.MS):
    return SetOut(
        id=set_id,
        game_id=game_id,
        owner_player_id=owner_id,
        type=type_,
    )


def patch_manager(monkeypatch):
    fake_manager = SimpleNamespace(broadcast_to_game=AsyncMock())
    monkeypatch.setattr(endpoints_mod, "manager", fake_manager)
    return fake_manager

@pytest.fixture
def client(monkeypatch):
    app = make_app()
    client = TestClient(app)
    return client


def test_missing_player_and_set_returns_400(monkeypatch):
    app = make_app()
    client = TestClient(app)
    gid = uuid.uuid4()
    fake_manager = patch_manager(monkeypatch)

    response = client.get(f"/sets?game_id={gid}")

    assert response.status_code == 400
    assert fake_manager.broadcast_to_game.await_count == 0


def test_set_id_without_player_returns_400(monkeypatch):
    app = make_app()
    client = TestClient(app)
    gid = uuid.uuid4()
    sid = uuid.uuid4()
    fake_manager = patch_manager(monkeypatch)

    response = client.get(f"/sets?game_id={gid}&set_id={sid}")

    assert response.status_code == 400
    assert fake_manager.broadcast_to_game.await_count == 0


def test_set_not_found_returns_404(monkeypatch):
    app = make_app()
    client = TestClient(app)
    gid = uuid.uuid4()
    pid = uuid.uuid4()
    sid = uuid.uuid4()
    fake_manager = patch_manager(monkeypatch)

    def fake_get_set_by_id(db, set_id):
        assert set_id == sid
        return None

    monkeypatch.setattr(endpoints_mod.SetService, "get_set_by_id", fake_get_set_by_id)

    response = client.get(f"/sets?game_id={gid}&player_id={pid}&set_id={sid}")

    assert response.status_code == 404
    assert fake_manager.broadcast_to_game.await_count == 0


def test_set_game_mismatch_returns_404(monkeypatch):
    app = make_app()
    client = TestClient(app)
    gid = uuid.uuid4()
    other_gid = uuid.uuid4()
    pid = uuid.uuid4()
    sid = uuid.uuid4()
    fake_manager = patch_manager(monkeypatch)

    dto = make_set_out(sid, other_gid, pid)

    def fake_get_set_by_id(db, set_id):
        return dto

    monkeypatch.setattr(endpoints_mod.SetService, "get_set_by_id", fake_get_set_by_id)

    response = client.get(f"/sets?game_id={gid}&player_id={pid}&set_id={sid}")

    assert response.status_code == 404
    assert fake_manager.broadcast_to_game.await_count == 0


def test_set_owner_mismatch_returns_404(monkeypatch):
    app = make_app()
    client = TestClient(app)
    gid = uuid.uuid4()
    pid = uuid.uuid4()
    other_pid = uuid.uuid4()
    sid = uuid.uuid4()
    fake_manager = patch_manager(monkeypatch)

    dto = make_set_out(sid, gid, other_pid)

    def fake_get_set_by_id(db, set_id):
        return dto

    monkeypatch.setattr(endpoints_mod.SetService, "get_set_by_id", fake_get_set_by_id)

    response = client.get(f"/sets?game_id={gid}&player_id={pid}&set_id={sid}")

    assert response.status_code == 404
    assert fake_manager.broadcast_to_game.await_count == 0


def test_get_set_ok_returns_payload(monkeypatch):
    app = make_app()
    client = TestClient(app)
    gid = uuid.uuid4()
    pid = uuid.uuid4()
    sid = uuid.uuid4()
    fake_manager = patch_manager(monkeypatch)

    dto = make_set_out(sid, gid, pid, type_=SetType.HARLEY_MS)

    def fake_get_set_by_id(db, set_id):
        return dto

    monkeypatch.setattr(endpoints_mod.SetService, "get_set_by_id", fake_get_set_by_id)

    response = client.get(f"/sets?game_id={gid}&player_id={pid}&set_id={sid}")

    assert response.status_code == 200
    fake_manager.broadcast_to_game.assert_awaited_once()
    args, _ = fake_manager.broadcast_to_game.call_args
    assert args[0] == gid
    payload = args[1]
    assert payload["type"] == "sets/query"
    assert payload["game_id"] == str(gid)
    assert payload["data"]["player_id"] == str(pid)
    assert payload["data"]["requested_set_id"] == str(sid)
    assert payload["data"]["set_ids"] == [str(sid)]
    assert payload["data"]["count"] == 1

    data = response.json()
    assert isinstance(data, list) and len(data) == 1
    item = data[0]
    assert item["id"] == str(sid)
    assert item["game_id"] == str(gid)
    assert item["owner_player_id"] == str(pid)
    assert item["type"] == "HARLEY_MS"


def test_list_sets_by_player_returns_payload(monkeypatch):
    app = make_app()
    client = TestClient(app)
    gid = uuid.uuid4()
    pid = uuid.uuid4()
    fake_manager = patch_manager(monkeypatch)

    dto_1 = make_set_out(uuid.uuid4(), gid, pid, type_=SetType.MS)
    dto_2 = make_set_out(uuid.uuid4(), gid, pid, type_=SetType.TB)

    captured_args = {}

    def fake_get_sets_for_player_in_game(db, *, player_id, game_id):
        captured_args["player_id"] = player_id
        captured_args["game_id"] = game_id
        return [dto_1, dto_2]

    monkeypatch.setattr(
        endpoints_mod.SetService,
        "get_sets_for_player_in_game",
        fake_get_sets_for_player_in_game,
    )

    response = client.get(f"/sets?game_id={gid}&player_id={pid}")

    assert response.status_code == 200
    fake_manager.broadcast_to_game.assert_awaited_once()
    args, _ = fake_manager.broadcast_to_game.call_args
    assert args[0] == gid
    payload = args[1]
    assert payload["type"] == "sets/query"
    assert payload["game_id"] == str(gid)
    assert payload["data"]["player_id"] == str(pid)
    assert "requested_set_id" not in payload["data"]
    assert set(payload["data"]["set_ids"]) == {str(dto_1.id), str(dto_2.id)}
    assert payload["data"]["count"] == 2
    assert captured_args == {"player_id": pid, "game_id": gid}
    data = response.json()
    returned_ids = {item["id"] for item in data}
    assert str(dto_1.id) in returned_ids
    assert str(dto_2.id) in returned_ids


def test_list_sets_by_player_empty_returns_empty(monkeypatch):
    app = make_app()
    client = TestClient(app)
    gid = uuid.uuid4()
    pid = uuid.uuid4()
    fake_manager = patch_manager(monkeypatch)

    def fake_get_sets_for_player_in_game(db, *, player_id, game_id):
        return []

    monkeypatch.setattr(
        endpoints_mod.SetService,
        "get_sets_for_player_in_game",
        fake_get_sets_for_player_in_game,
    )

    response = client.get(f"/sets?game_id={gid}&player_id={pid}")

    assert response.status_code == 200
    assert response.json() == []
    fake_manager.broadcast_to_game.assert_awaited_once()
    args, _ = fake_manager.broadcast_to_game.call_args
    assert args[0] == gid
    payload = args[1]
    assert payload["data"]["set_ids"] == []
    assert payload["data"]["count"] == 0

# TESTS GET /verify/{game_id}

def test_verify_set_ok(client, monkeypatch):
    gid = uuid.uuid4()
    cid1 = uuid.uuid4()
    cid2 = uuid.uuid4()

    fake_set_service = MagicMock()
    # Hacemos que validate_set devuelva un tipo específico
    fake_set_service.validate_set.return_value = SetType.MS

    monkeypatch.setattr(endpoints_mod, "SetService", lambda db: fake_set_service)

    # Hacemos la llamada GET con 'cards' como query parameters
    response = client.get(f"/sets/verify?cards={cid1}&cards={cid2}")

    assert response.status_code == 200
    assert response.json() == "MS" # El enum se serializa a su string
    fake_set_service.validate_set.assert_called_once_with([cid1, cid2])


def test_verify_set_fails_on_value_error(client, monkeypatch):
    gid = uuid.uuid4()
    cid1 = uuid.uuid4()
    cid2 = uuid.uuid4()

    # Mock del SetService para que falle
    fake_set_service = MagicMock()
    fake_set_service.validate_set.side_effect = ValueError("Invalid set")
    
    monkeypatch.setattr(endpoints_mod, "SetService", lambda db: fake_set_service)

    response = client.get(f"/sets/verify?cards={cid1}&cards={cid2}")

    assert response.status_code == 400
    assert response.json() == {"detail": "notValidSet"}


# TESTS POST /play/{game_id}

@pytest.fixture
def play_set_mocks(monkeypatch):
    """Fixture para mockear las dependencias de play_set"""
    gid = uuid.uuid4()
    pid = uuid.uuid4()
    tid = uuid.uuid4() # Target ID
    cid1, cid2 = uuid.uuid4(), uuid.uuid4()

    # 1. Mock GameService
    fake_game = MagicMock()
    fake_game.players_ids = [pid, tid]
    
    fake_game_service = MagicMock()
    fake_game_service.get_game_by_id.return_value = fake_game
    fake_game_service.get_turn.return_value = pid
    
    monkeypatch.setattr(endpoints_mod, "GameService", lambda db: fake_game_service)

    fake_set_out = make_set_out(uuid.uuid4(), gid, pid, SetType.MS)
    
    fake_set_service = MagicMock()
    fake_set_service.determine_set_type.return_value = SetType.MS
    fake_set_service.create_set.return_value = fake_set_out
    fake_set_service.play_set.return_value = fake_set_out
    
    monkeypatch.setattr(endpoints_mod, "SetService", lambda db: fake_set_service)

    fake_manager = patch_manager(monkeypatch)

    payload = schemas.SetPlayIn(
        player_id=pid,
        cards=[cid1, cid2],
        target_player_id=tid,
        secret_id=None
    ).model_dump(mode='json')

    return SimpleNamespace(
        gid=gid, pid=pid, tid=tid, payload=payload,
        game_service=fake_game_service,
        set_service=fake_set_service,
        manager=fake_manager
    )


def test_play_set_validation_fails_not_in_game(client, play_set_mocks):
    play_set_mocks.game_service.get_game_by_id.return_value.players_ids = [uuid.uuid4()]
    
    response = client.post(f"/sets/play/{play_set_mocks.gid}", json=play_set_mocks.payload)
    
    assert response.status_code == 400
    assert response.json() == {"detail": "BadRequest"}


def test_play_set_validation_fails_not_player_turn(client, play_set_mocks):
    play_set_mocks.game_service.get_turn.return_value = uuid.uuid4()
    
    response = client.post(f"/sets/play/{play_set_mocks.gid}", json=play_set_mocks.payload)
    
    assert response.status_code == 400
    assert response.json() == {"detail": "BadRequest"}


def test_play_set_happy_path_ms_set(client, play_set_mocks):
    response = client.post(f"/sets/play/{play_set_mocks.gid}", json=play_set_mocks.payload)

    assert response.status_code == 201
    play_set_mocks.set_service.determine_set_type.assert_called_once()
    play_set_mocks.set_service.create_set.assert_called_once()
    
    play_set_mocks.set_service.play_set.assert_not_called()
    
    play_set_mocks.manager.broadcast_to_game.assert_awaited_once()
    args, _ = play_set_mocks.manager.broadcast_to_game.call_args
    assert args[0] == play_set_mocks.gid
    assert args[1]["type"] == "targetPlayerElection"
    assert args[1]["data"]["set_type"] == "MS"


def test_play_set_happy_path_hp_set(client, play_set_mocks):
    play_set_mocks.set_service.determine_set_type.return_value = SetType.HP
    play_set_mocks.payload["secret_id"] = str(uuid.uuid4())
    
    hp_set_out = make_set_out(uuid.uuid4(), play_set_mocks.gid, play_set_mocks.pid, SetType.HP)
    play_set_mocks.set_service.create_set.return_value = hp_set_out
    play_set_mocks.set_service.play_set.return_value = hp_set_out

    fake_play_result = SetPlayResult(set_out=hp_set_out, end_game_result=None)
    play_set_mocks.set_service.play_set.return_value = fake_play_result

    response = client.post(f"/sets/play/{play_set_mocks.gid}", json=play_set_mocks.payload)
    
    assert response.status_code == 201
    play_set_mocks.set_service.determine_set_type.assert_called_once()
    play_set_mocks.set_service.create_set.assert_called_once()
    
    # Debe llamar a play_set
    play_set_mocks.set_service.play_set.assert_called_once()
    play_set_mocks.manager.broadcast_to_game.assert_awaited_once()
    args, _ = play_set_mocks.manager.broadcast_to_game.call_args
    assert args[0] == play_set_mocks.gid
    assert args[1]["type"] == "playSet"
    assert args[1]["data"]["set_type"] == "HP"


def test_play_set_hp_set_missing_secret_fails(client, play_set_mocks):
    play_set_mocks.set_service.determine_set_type.return_value = SetType.HP
    play_set_mocks.payload["secret_id"] = None

    response = client.post(f"/sets/play/{play_set_mocks.gid}", json=play_set_mocks.payload)

    assert response.status_code == 400
    assert response.json() == {"detail": "BadRequest"}
    play_set_mocks.set_service.play_set.assert_not_called()


# TESTS POST /election_secret/{game_id}

@pytest.fixture
def election_mocks(monkeypatch):
    """Fixture para mockear las dependencias de election_secret"""
    gid = uuid.uuid4()
    pid = uuid.uuid4() # El jugador que elige
    sid = uuid.uuid4() # Set ID
    sec_id = uuid.uuid4() # Secret ID

    fake_game = MagicMock()
    fake_game.players_ids = [pid]
    fake_game_service = MagicMock()
    fake_game_service.get_game_by_id.return_value = fake_game
    monkeypatch.setattr(endpoints_mod, "GameService", lambda db: fake_game_service)

    played_set_out = make_set_out(sid, gid, pid, SetType.MS)
    
    fake_set_service = MagicMock()
    fake_set_service.get_set_by_id.return_value = played_set_out

    fake_play_result = SetPlayResult(set_out=played_set_out, end_game_result=None)
    fake_set_service.play_set.return_value = fake_play_result
    
    monkeypatch.setattr(endpoints_mod, "SetService", lambda db: fake_set_service)

    fake_manager = patch_manager(monkeypatch)

    payload = schemas.SetElectionPlayer(
        player_id=pid,
        set_id=sid,
        secret_id=sec_id
    ).model_dump(mode='json')

    return SimpleNamespace(
        gid=gid,
        payload=payload,
        pid=pid,
        sid=sid,
        sec_id=sec_id,
        game_service=fake_game_service,
        set_service=fake_set_service,
        manager=fake_manager
    )


def test_election_secret_happy_path(client, election_mocks):
    response = client.post(f"/sets/election_secret/{election_mocks.gid}", json=election_mocks.payload)
    
    assert response.status_code == 200
    
    election_mocks.game_service.get_game_by_id.assert_called_once_with(election_mocks.gid)
    election_mocks.set_service.play_set.assert_called_once_with(
        election_mocks.sid,
        election_mocks.pid,
        election_mocks.sec_id
    )
    
    election_mocks.manager.broadcast_to_game.assert_awaited_once()
    args, _ = election_mocks.manager.broadcast_to_game.call_args
    assert args[0] == election_mocks.gid
    assert args[1]["type"] == "playSet"
    assert args[1]["data"]["set_type"] == "MS"
    
    data = response.json()
    assert data["id"] == election_mocks.payload["set_id"]


def test_election_secret_validation_fails_not_in_game(client, election_mocks):
    election_mocks.game_service.get_game_by_id.return_value.players_ids = [uuid.uuid4()]
    
    response = client.post(f"/sets/election_secret/{election_mocks.gid}", json=election_mocks.payload)
    
    assert response.status_code == 400
    assert response.json() == {"detail": "BadRequest"}
    election_mocks.set_service.play_set.assert_not_called()


# --- Test Endpoint play_set_detective (Caso Normal) ---
def test_play_set_detective_hp_set_no_end(client, play_set_mocks):
    play_set_mocks.set_service.determine_set_type.return_value = SetType.HP
    play_set_mocks.payload["secret_id"] = str(uuid.uuid4())
    
    hp_set_out = make_set_out(uuid.uuid4(), play_set_mocks.gid, play_set_mocks.pid, SetType.HP)
    play_set_mocks.set_service.create_set.return_value = hp_set_out
    
    fake_play_result = SetPlayResult(set_out=hp_set_out, end_game_result=None)
    play_set_mocks.set_service.play_set.return_value = fake_play_result

    response = client.post(f"/sets/play/{play_set_mocks.gid}", json=play_set_mocks.payload)
    
    assert response.status_code == 201
    play_set_mocks.set_service.determine_set_type.assert_called_once()
    play_set_mocks.set_service.create_set.assert_called_once()
    play_set_mocks.set_service.play_set.assert_called_once()
    
    play_set_mocks.manager.broadcast_to_game.assert_awaited_once()
    args, _ = play_set_mocks.manager.broadcast_to_game.call_args
    assert args[0] == play_set_mocks.gid
    assert args[1]["type"] == "playSet"
    assert args[1]["data"]["set_type"] == "HP"
    
    data = response.json()
    assert data["id"] == str(hp_set_out.id)

# --- Test Endpoint play_set_detective (Termina Juego) ---
def test_play_set_detective_hp_set_ends_game(client, play_set_mocks):
    play_set_mocks.set_service.determine_set_type.return_value = SetType.HP
    play_set_mocks.payload["secret_id"] = str(uuid.uuid4())
    
    hp_set_out = make_set_out(uuid.uuid4(), play_set_mocks.gid, play_set_mocks.pid, SetType.HP)
    play_set_mocks.set_service.create_set.return_value = hp_set_out
    
    mock_end_result = MagicMock(spec=EndGameResult)
    mock_end_result.model_dump.return_value = {"reason": "MURDERER_REVEALED", "winners": []} 
    fake_play_result = SetPlayResult(set_out=hp_set_out, end_game_result=mock_end_result)
    play_set_mocks.set_service.play_set.return_value = fake_play_result

    response = client.post(f"/sets/play/{play_set_mocks.gid}", json=play_set_mocks.payload)
    
    assert response.status_code == 201
    play_set_mocks.set_service.determine_set_type.assert_called_once()
    play_set_mocks.set_service.create_set.assert_called_once()
    play_set_mocks.set_service.play_set.assert_called_once()
    
    play_set_mocks.manager.broadcast_to_game.assert_awaited_once()
    args, _ = play_set_mocks.manager.broadcast_to_game.call_args
    assert args[0] == play_set_mocks.gid
    assert args[1]["type"] == "gameEnd"
    assert args[1]["data"]["reason"] == "MURDERER_REVEALED" 
    
    data = response.json()
    assert data["id"] == str(hp_set_out.id)

# --- Test Endpoint election_secret_set (Caso Normal) ---
def test_election_secret_happy_path_no_end(client, election_mocks):

    response = client.post(f"/sets/election_secret/{election_mocks.gid}", json=election_mocks.payload)
    
    assert response.status_code == 200
    election_mocks.game_service.get_game_by_id.assert_called_once_with(election_mocks.gid)
    election_mocks.set_service.play_set.assert_called_once_with(
        election_mocks.sid, election_mocks.pid, election_mocks.sec_id
    )
    
    election_mocks.manager.broadcast_to_game.assert_awaited_once()
    args, _ = election_mocks.manager.broadcast_to_game.call_args
    assert args[0] == election_mocks.gid
    assert args[1]["type"] == "playSet"
    assert args[1]["data"]["set_type"] == "MS" 
    
    data = response.json()
    assert data["id"] == election_mocks.payload["set_id"]

# --- Test Endpoint election_secret_set (Termina Juego) ---
def test_election_secret_ends_game(client, election_mocks):
    mock_set_out = election_mocks.set_service.play_set.return_value.set_out 
    mock_end_result = MagicMock(spec=EndGameResult)
    mock_end_result.model_dump.return_value = {"reason": "SECRETS_REVEALED", "winners": []} 
    fake_play_result_with_end = SetPlayResult(set_out=mock_set_out, end_game_result=mock_end_result)
    election_mocks.set_service.play_set.return_value = fake_play_result_with_end

    response = client.post(f"/sets/election_secret/{election_mocks.gid}", json=election_mocks.payload)
    
    assert response.status_code == 200 
    election_mocks.game_service.get_game_by_id.assert_called_once_with(election_mocks.gid)
    election_mocks.set_service.play_set.assert_called_once_with(
        election_mocks.sid, election_mocks.pid, election_mocks.sec_id
    )
    
    election_mocks.manager.broadcast_to_game.assert_awaited_once()
    args, _ = election_mocks.manager.broadcast_to_game.call_args
    assert args[0] == election_mocks.gid
    assert args[1]["type"] == "gameEnd" 
    assert args[1]["data"]["reason"] == "SECRETS_REVEALED"
    
    data = response.json()
    assert data["id"] == election_mocks.payload["set_id"]
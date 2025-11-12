import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, ANY

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.set import endpoints as endpoints_mod
from app.set.dtos import SetOut, SetPlayResult
from app.set.endpoints import sets_router
from app.set.enums import SetType
from app.game.schemas import EndGameResult
from app.set import schemas
from app.game.enums import TurnState


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
    
    fake_game_turn_state = MagicMock(turn_state=TurnState.IDLE)

    fake_game_service = MagicMock()
    fake_game_service.get_game_by_id.return_value = fake_game
    fake_game_service.get_turn.return_value = pid
    fake_game_service.get_turn_state.return_value = fake_game_turn_state
    
    monkeypatch.setattr(endpoints_mod, "GameService", lambda db: fake_game_service)

    fake_set_out = make_set_out(uuid.uuid4(), gid, pid, SetType.MS)
    
    fake_set_service = MagicMock()
    fake_set_service.determine_set_type.return_value = SetType.MS
    fake_set_service.create_set.return_value = fake_set_out
    fake_set_service.play_set.return_value = fake_set_out
    fake_set_service.verify_cancellable_new_set.return_value = False
    fake_set_service.verify_cancellable_set.return_value = False
    
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


def test_play_set_happy_path_ms_set(client, play_set_mocks, monkeypatch):
    from app.set import service as set_service_module
    fake_player = MagicMock()
    fake_player.social_disgrace = False
    fake_player_service = MagicMock()
    fake_player_service.get_player_entity_by_id.return_value = fake_player

    monkeypatch.setattr("app.set.endpoints.PlayerService", lambda db_: fake_player_service)
    response = client.post(f"/sets/play/{play_set_mocks.gid}", json=play_set_mocks.payload)

    assert response.status_code == 201
    play_set_mocks.set_service.determine_set_type.assert_called_once()
    play_set_mocks.set_service.create_set.assert_called_once()
    
    play_set_mocks.set_service.play_set.assert_not_called()

    calls = play_set_mocks.manager.broadcast_to_game.await_args_list
    assert len(calls) == 3
    assert calls[0].args[1]["type"] == "timerPaused"
    assert calls[2].args[1]["type"] == "targetPlayerElection"


def test_play_set_happy_path_hp_set(client, play_set_mocks,monkeypatch):
    play_set_mocks.set_service.determine_set_type.return_value = SetType.HP
    play_set_mocks.payload["secret_id"] = str(uuid.uuid4())
    
    hp_set_out = make_set_out(uuid.uuid4(), play_set_mocks.gid, play_set_mocks.pid, SetType.HP)
    play_set_mocks.set_service.create_set.return_value = hp_set_out
    play_set_mocks.set_service.play_set.return_value = hp_set_out

    fake_player = MagicMock()
    fake_player.social_disgrace = False
    fake_player_service = MagicMock()
    fake_player_service.get_player_entity_by_id.return_value = fake_player

    monkeypatch.setattr("app.set.endpoints.PlayerService", lambda db_: fake_player_service)
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


def test_play_set_hp_set_missing_secret_fails(client, play_set_mocks, monkeypatch):
    play_set_mocks.set_service.determine_set_type.return_value = SetType.HP
    play_set_mocks.payload["secret_id"] = None

    fake_player = MagicMock()
    fake_player.social_disgrace = False
    fake_player_service = MagicMock()
    fake_player_service.get_player_entity_by_id.return_value = fake_player

    monkeypatch.setattr("app.set.endpoints.PlayerService", lambda db_: fake_player_service)
    response = client.post(f"/sets/play/{play_set_mocks.gid}", json=play_set_mocks.payload)

    assert response.status_code == 400
    assert response.json() == {"detail": "BadRequest"}
    play_set_mocks.set_service.play_set.assert_not_called()

def test_play_set_with_social_disgrace(client, play_set_mocks, monkeypatch):
    play_set_mocks.set_service.determine_set_type.return_value = SetType.HP
    play_set_mocks.payload["secret_id"] = None

    fake_player = MagicMock()
    fake_player.social_disgrace = True
    fake_player_service = MagicMock()
    fake_player_service.get_player_entity_by_id.return_value = fake_player

    monkeypatch.setattr("app.set.endpoints.PlayerService", lambda db_: fake_player_service)
    response = client.post(f"/sets/play/{play_set_mocks.gid}", json=play_set_mocks.payload)

    assert response.status_code == 403
    assert response.json() == {"detail": "No se puede jugar un Set estando en desgracia social"}
    play_set_mocks.set_service.play_set.assert_not_called()


# TESTS POST /election_secret/{game_id}---------------------------------------------------

@pytest.fixture
def election_mocks(monkeypatch):
    """Fixture para mockear las dependencias de election_secret"""
    gid = uuid.uuid4()
    pid = uuid.uuid4() # El jugador que elige
    sid = uuid.uuid4() # Set ID
    sec_id = uuid.uuid4() # Secret ID

    fake_game = MagicMock()
    fake_game.players_ids = [pid]

    fake_game_turn_state = MagicMock(turn_state=TurnState.CHOOSING_SECRET)

    fake_game_service = MagicMock()
    fake_game_service.get_game_by_id.return_value = fake_game
    fake_game_service.get_turn_state.return_value = fake_game_turn_state
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

def test_election_secret_happy_path_without_ariadne(client, election_mocks):
    """Test del flujo normal sin carta Ariadne"""
    response = client.post(
        f"/sets/election_secret/{election_mocks.gid}", 
        json=election_mocks.payload
    )
    
    assert response.status_code == 200
    
    election_mocks.game_service.get_game_by_id.assert_called_once_with(election_mocks.gid)
    
    # Verificar que play_set se llama con card_id=None
    election_mocks.set_service.play_set.assert_called_once_with(
        election_mocks.sid,
        None,  # card_id es None en el flujo normal
        election_mocks.pid,
        election_mocks.sec_id
    )
    
    # Verificar broadcast
    calls = election_mocks.manager.broadcast_to_game.await_args_list
    assert len(calls) == 2  # timerResumed y playSet
    
    # Verificar timerResumed
    assert calls[0].args[0] == election_mocks.gid
    assert calls[0].args[1]["type"] == "timerResumed"
    
    # Verificar playSet
    assert calls[1].args[0] == election_mocks.gid
    assert calls[1].args[1]["type"] == "playSet"
    assert calls[1].args[1]["data"]["set_type"] == "MS"
    
    data = response.json()
    assert data["id"] == election_mocks.payload["set_id"]


def test_election_secret_with_ariadne_card(client, election_mocks, monkeypatch):
    """Test del flujo con carta Detective Ariadne Oliver"""
    ariadne_card_id = uuid.uuid4()
    
    # Mock de la carta Ariadne
    fake_ariadne_card = MagicMock()
    fake_ariadne_card.name = "D_AO"
    fake_ariadne_card.id = ariadne_card_id
    
    fake_card_service = MagicMock()
    fake_card_service.get_card_by_id.return_value = fake_ariadne_card
    monkeypatch.setattr(endpoints_mod, "CardService", lambda: fake_card_service)
    
    # Agregar card_id al payload
    payload_with_card = election_mocks.payload.copy()
    
    response = client.post(
        f"/sets/election_secret/{election_mocks.gid}?card_id={ariadne_card_id}",
        json=payload_with_card
    )
    
    assert response.status_code == 200
    
    # Verificar que se obtiene la carta
    fake_card_service.get_card_by_id.assert_called_once()
    
    # Verificar que play_set se llama con el card_id
    election_mocks.set_service.play_set.assert_called_once_with(
        election_mocks.sid,
        ariadne_card_id,
        election_mocks.pid,
        election_mocks.sec_id
    )
    
    # Verificar broadcasts (timerResumed y playSet o gameEnd)
    assert election_mocks.manager.broadcast_to_game.await_count >= 2


def test_election_secret_with_invalid_card_fails(client, election_mocks, monkeypatch):
    """Test que falla cuando la carta no es Ariadne"""
    wrong_card_id = uuid.uuid4()
    
    # Mock de una carta que NO es Ariadne
    fake_wrong_card = MagicMock()
    fake_wrong_card.name = "D_MS"  # No es D_AO
    
    fake_card_service = MagicMock()
    fake_card_service.get_card_by_id.return_value = fake_wrong_card
    monkeypatch.setattr(endpoints_mod, "CardService", lambda: fake_card_service)
    
    response = client.post(
        f"/sets/election_secret/{election_mocks.gid}?card_id={wrong_card_id}",
        json=election_mocks.payload
    )
    
    assert response.status_code == 400
    assert "Card is not Detective Ariadne Oliver" in response.json()["detail"]
    election_mocks.set_service.play_set.assert_not_called()


def test_election_secret_validation_fails_not_in_game(client, election_mocks):
    """Test de validación: jugador no está en el juego"""
    election_mocks.game_service.get_game_by_id.return_value.players_ids = [uuid.uuid4()]
    
    response = client.post(
        f"/sets/election_secret/{election_mocks.gid}", 
        json=election_mocks.payload
    )
    
    assert response.status_code == 400
    assert response.json() == {"detail": "BadRequest"}
    election_mocks.set_service.play_set.assert_not_called()


def test_election_secret_validation_fails_wrong_turn_state(client, election_mocks):
    """Test de validación: estado de turno incorrecto"""
    election_mocks.game_service.get_turn_state.return_value = MagicMock(
        turn_state=TurnState.IDLE  # No es CHOOSING_SECRET
    )
    
    response = client.post(
        f"/sets/election_secret/{election_mocks.gid}",
        json=election_mocks.payload
    )
    
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid accion for the game state"}
    election_mocks.set_service.play_set.assert_not_called()


def test_election_secret_ends_game_without_ariadne(client, election_mocks):
    """Test cuando el juego termina (sin Ariadne)"""
    mock_set_out = election_mocks.set_service.play_set.return_value.set_out 
    mock_end_result = MagicMock(spec=EndGameResult)
    mock_end_result.model_dump.return_value = {"reason": "SECRETS_REVEALED", "winners": []} 
    fake_play_result_with_end = SetPlayResult(
        set_out=mock_set_out, 
        end_game_result=mock_end_result
    )
    election_mocks.set_service.play_set.return_value = fake_play_result_with_end

    response = client.post(
        f"/sets/election_secret/{election_mocks.gid}", 
        json=election_mocks.payload
    )
    
    assert response.status_code == 200 
    
    # Verificar que play_set se llama con None como card_id
    election_mocks.set_service.play_set.assert_called_once_with(
        election_mocks.sid, 
        None,  # Sin carta Ariadne
        election_mocks.pid, 
        election_mocks.sec_id
    )
    
    # Verificar broadcasts
    calls = election_mocks.manager.broadcast_to_game.await_args_list
    assert len(calls) == 2
    
    # Verificar timerResumed
    assert calls[0].args[1]["type"] == "timerResumed"
    
    # Verificar gameEnd
    assert calls[1].args[0] == election_mocks.gid
    assert calls[1].args[1]["type"] == "gameEnd" 
    assert calls[1].args[1]["data"]["reason"] == "SECRETS_REVEALED"
    
    data = response.json()
    assert data["id"] == election_mocks.payload["set_id"]


def test_election_secret_ends_game_with_ariadne(client, election_mocks, monkeypatch):
    """Test cuando el juego termina usando carta Ariadne"""
    ariadne_card_id = uuid.uuid4()
    
    # Mock de la carta Ariadne
    fake_ariadne_card = MagicMock()
    fake_ariadne_card.name = "D_AO"
    
    fake_card_service = MagicMock()
    fake_card_service.get_card_by_id.return_value = fake_ariadne_card
    monkeypatch.setattr(endpoints_mod, "CardService", lambda: fake_card_service)
    
    # Mock del resultado con fin de juego
    mock_set_out = election_mocks.set_service.play_set.return_value.set_out 
    mock_end_result = MagicMock(spec=EndGameResult)
    mock_end_result.model_dump.return_value = {"reason": "MURDERER_REVEALED", "winners": []}
    fake_play_result_with_end = SetPlayResult(
        set_out=mock_set_out,
        end_game_result=mock_end_result
    )
    election_mocks.set_service.play_set.return_value = fake_play_result_with_end
    
    response = client.post(
        f"/sets/election_secret/{election_mocks.gid}?card_id={ariadne_card_id}",
        json=election_mocks.payload
    )
    
    assert response.status_code == 200
    
    # Verificar que play_set se llama con el card_id de Ariadne
    election_mocks.set_service.play_set.assert_called_once_with(
        election_mocks.sid,
        ariadne_card_id,
        election_mocks.pid,
        election_mocks.sec_id
    )
    
    # Verificar gameEnd broadcast
    calls = election_mocks.manager.broadcast_to_game.await_args_list
    assert any(call.args[1]["type"] == "gameEnd" for call in calls)



#-------------------------ADD CARD TO SET ENDPOINT--------------------------------

@pytest.fixture
def add_card_mocks(monkeypatch):
    """Fixture para mockear las dependencias de add_card_to_set"""
    gid = uuid.uuid4()
    pid = uuid.uuid4()
    sid = uuid.uuid4()
    cid = uuid.uuid4()
    tid = uuid.uuid4()

    # 1. Mock de la instancia de SetService (para TODOS los métodos)
    fake_set_service_instance = MagicMock()

    fake_set_dto = make_set_out(sid, gid, pid, SetType.MS)
    # El DTO devuelto por get_set_by_id
    fake_set_service_instance.get_set_by_id.return_value = fake_set_dto
    # El DTO devuelto por add_card_to_set
    updated_set_dto = make_set_out(sid, gid, pid, SetType.MS) 
    fake_set_service_instance.add_card_to_set.return_value = updated_set_dto
    fake_set_service_instance.verify_cancellable_set.return_value = False
    
    # 2. Mockear el constructor de SetService para que devuelva nuestra instancia mock
    monkeypatch.setattr(endpoints_mod, "SetService", lambda db: fake_set_service_instance)

    # 3. Mock del manager
    fake_manager = patch_manager(monkeypatch)

    # 4. Mock PlayerService to return a player without social disgrace
    fake_player = MagicMock()
    fake_player.social_disgrace = False
    fake_player_service = MagicMock()
    fake_player_service.get_player_entity_by_id.return_value = fake_player
    monkeypatch.setattr(endpoints_mod, "PlayerService", lambda db: fake_player_service)

    # 5. Mock GameService so game exists and it's the player's turn
    fake_game = MagicMock()
    fake_game.players_ids = [pid]
    fake_game_service = MagicMock()
    fake_game_service.get_game_by_id.return_value = fake_game
    fake_game_service.get_turn.return_value = pid
    fake_game_service.get_turn_state.return_value = MagicMock(turn_state=TurnState.IDLE)
    monkeypatch.setattr(endpoints_mod, "GameService", lambda db: fake_game_service)

    url = f"/sets/{sid}/cards/{cid}?game_id={gid}&player_id={pid}&target_player_id={tid}"

    # 4. Devolver los mocks correctos
    return SimpleNamespace(
        gid=gid, pid=pid, sid=sid, cid=cid, tid=tid,
        url=url,
        # Devolvemos la instancia de servicio para que los tests la manipulen
        set_service_instance=fake_set_service_instance,
        manager=fake_manager,
        player_service=fake_player_service,
        game_service=fake_game_service,
    )

def test_add_card_to_set_happy_path(client, add_card_mocks):
    response = client.put(add_card_mocks.url)

    assert response.status_code == 200
    
    # 1. Verifica la validación inicial (ahora en la instancia mock)
    add_card_mocks.set_service_instance.get_set_by_id.assert_called_once_with(
        ANY,  # Ignora el argumento 'db'
        add_card_mocks.sid
    )
    
    # 2. Verifica la llamada a la lógica de servicio
    add_card_mocks.set_service_instance.add_card_to_set.assert_called_once_with(
        add_card_mocks.gid,
        add_card_mocks.pid,
        add_card_mocks.sid,
        add_card_mocks.cid
    )
    
    # 3. Verifica el broadcast al websocket (para sets detectives el endpoint solicita elección de target)
    args, _ = add_card_mocks.manager.broadcast_to_game.call_args
    assert args[0] == add_card_mocks.gid
    payload = args[1]
    # En el flujo actual para sets detectives el tipo es 'targetPlayerElection'
    assert payload["type"] == "targetPlayerElection"
    assert payload["data"]["set_id"] == str(add_card_mocks.sid)
    assert payload["data"]["set_type"] == "MS"
    assert payload["data"]["target_player"] == str(add_card_mocks.tid)
    
    # 4. Verifica la respuesta
    data = response.json()
    assert data["id"] == str(add_card_mocks.sid)
    assert data["owner_player_id"] == str(add_card_mocks.pid)
    assert data["type"] == "MS"


def test_add_card_set_not_found(client, add_card_mocks):
    # Configura el mock en la *instancia* para que devuelva None
    add_card_mocks.set_service_instance.get_set_by_id.return_value = None
    
    response = client.put(add_card_mocks.url)
    
    assert response.status_code == 404
    assert response.json() == {"detail": "Set not found"}
    
    # Asegura que la lógica principal no se llamó
    add_card_mocks.set_service_instance.add_card_to_set.assert_not_called()
    add_card_mocks.manager.broadcast_to_game.assert_not_awaited()


def test_add_card_game_mismatch(client, add_card_mocks):
    # Configura el mock en la *instancia*
    wrong_gid = uuid.uuid4()
    mismatched_dto = make_set_out(add_card_mocks.sid, wrong_gid, add_card_mocks.pid)
    add_card_mocks.set_service_instance.get_set_by_id.return_value = mismatched_dto
    
    response = client.put(add_card_mocks.url)
    
    assert response.status_code == 400
    assert response.json() == {"detail": "Set-Game mismatch"}
    add_card_mocks.set_service_instance.add_card_to_set.assert_not_called()


def test_add_card_player_mismatch(client, add_card_mocks):
    # Configura el mock en la *instancia*
    wrong_pid = uuid.uuid4()
    mismatched_dto = make_set_out(add_card_mocks.sid, add_card_mocks.gid, wrong_pid)
    add_card_mocks.set_service_instance.get_set_by_id.return_value = mismatched_dto
    
    response = client.put(add_card_mocks.url)
    
    assert response.status_code == 400
    assert response.json() == {"detail": "Set-Player mismatch"}
    add_card_mocks.set_service_instance.add_card_to_set.assert_not_called()


def test_add_card_service_value_error(client, add_card_mocks):
    error_msg = "NotMatchingSetType"
    add_card_mocks.set_service_instance.add_card_to_set.side_effect = ValueError(error_msg)
    
    response = client.put(add_card_mocks.url)
    
    assert response.status_code == 400
    # Asume que el endpoint propaga el mensaje de error
    assert response.json() == {"detail": error_msg} 
    
    # Verifica que se intentó llamar al servicio
    add_card_mocks.set_service_instance.add_card_to_set.assert_called_once()
    # Verifica que no se envió broadcast por el error
    add_card_mocks.manager.broadcast_to_game.assert_not_awaited()



#---------------------------ADD ARIADNE CARD TO SET ENDPOINT----------------------------
# def test_add_ariadne_card_to_set_happy_path(client, add_card_mocks, monkeypatch):


# Fixture para mockear las dependencias del endpoint de Ariadne
@pytest.fixture
def ariadne_endpoint_mocks(monkeypatch):
    """Fixture para mockear las dependencias de play_detective_ariadne"""
    gid = uuid.uuid4()
    pid = uuid.uuid4()  # Jugador que juega la carta Ariadne
    sid = uuid.uuid4()  # ID del set
    set_owner_id = uuid.uuid4()  # Dueño del set (diferente al que juega Ariadne)
    cid = uuid.uuid4()  # ID de la carta Ariadne

    # Mock del jugador
    fake_player = MagicMock()
    fake_player.game_id = gid
    fake_player_service = MagicMock()
    fake_player.social_disgrace = False
    fake_player_service.get_player_by_id.return_value = fake_player
    fake_player_service.get_player_entity_by_id.return_value = fake_player 
    monkeypatch.setattr(endpoints_mod, "PlayerService", lambda db: fake_player_service)

    # Mock del jugador dueño del set (target)
    fake_target_player = MagicMock()
    fake_target_player.social_disgrace = False
    fake_player_service.get_player_entity_by_id.side_effect = lambda pid_: (
    fake_player if pid_ == pid else fake_target_player)

    # Mock del set
    fake_set_dto = make_set_out(sid, gid, set_owner_id, SetType.MS)
    fake_set_service_instance = MagicMock()
    fake_set_service_instance.get_set_by_id.return_value = fake_set_dto
    
    # Mock del set actualizado después de agregar Ariadne
    updated_set_dto = make_set_out(sid, gid, set_owner_id, SetType.MS)
    fake_set_service_instance.add_card_to_set.return_value = updated_set_dto
    monkeypatch.setattr(endpoints_mod, "SetService", lambda db: fake_set_service_instance)

    # Mock de la carta Ariadne
    fake_ariadne_card = MagicMock()
    fake_ariadne_card.name = "D_AO"
    fake_ariadne_card.owner_player_id = pid
    fake_card_service = MagicMock()
    fake_card_service.get_card_by_id.return_value = fake_ariadne_card
    monkeypatch.setattr(endpoints_mod, "CardService", lambda: fake_card_service)

    # Mock del GameService
    fake_game_service = MagicMock()
    monkeypatch.setattr(endpoints_mod, "GameService", lambda db: fake_game_service)

    # Mock del manager
    fake_manager = patch_manager(monkeypatch)

    url = f"/sets/ariadne/{sid}?game_id={gid}&player_id={pid}&card_id={cid}"

    return SimpleNamespace(
        gid=gid,
        pid=pid,
        sid=sid,
        set_owner_id=set_owner_id,
        cid=cid,
        url=url,
        player_service=fake_player_service,
        set_service=fake_set_service_instance,
        card_service=fake_card_service,
        game_service=fake_game_service,
        manager=fake_manager
    )


def test_play_ariadne_happy_path(client, ariadne_endpoint_mocks):
    """Test del flujo exitoso de jugar carta Ariadne en un set ajeno"""
    response = client.put(ariadne_endpoint_mocks.url)

    assert response.status_code == 200

    # Verificar que se consultó el jugador
    ariadne_endpoint_mocks.player_service.get_player_by_id.assert_called_once_with(
        ariadne_endpoint_mocks.pid
    )

    # Verificar que se consultó el set
    ariadne_endpoint_mocks.set_service.get_set_by_id.assert_called_once()

    # Verificar que se consultó la carta
    ariadne_endpoint_mocks.card_service.get_card_by_id.assert_called_once()

    # Verificar que se agregó la carta al set (nota: usa el owner del set, no el player_id)
    ariadne_endpoint_mocks.set_service.add_card_to_set.assert_called_once_with(
        ariadne_endpoint_mocks.gid,
        ariadne_endpoint_mocks.set_owner_id,  # Owner del set
        ariadne_endpoint_mocks.sid,
        ariadne_endpoint_mocks.cid
    )

    # Verificar que se cambió el estado del turno
    ariadne_endpoint_mocks.game_service.change_turn_state.assert_called_once_with(
        ariadne_endpoint_mocks.gid,
        TurnState.CHOOSING_SECRET,
        ariadne_endpoint_mocks.set_owner_id
    )

    # Verificar el broadcast
    ariadne_endpoint_mocks.manager.broadcast_to_game.assert_awaited_once()
    args, _ = ariadne_endpoint_mocks.manager.broadcast_to_game.call_args
    assert args[0] == ariadne_endpoint_mocks.gid
    payload = args[1]
    assert payload["type"] == "targetPlayerElection"
    assert payload["data"]["set_id"] == str(ariadne_endpoint_mocks.sid)
    assert payload["data"]["set_type"] == "MS"
    assert payload["data"]["target_player"] == str(ariadne_endpoint_mocks.set_owner_id)

    # Verificar la respuesta
    data = response.json()
    assert data["id"] == str(ariadne_endpoint_mocks.sid)
    assert data["owner_player_id"] == str(ariadne_endpoint_mocks.set_owner_id)
    assert data["type"] == "MS"


def test_play_ariadne_player_not_found(client, ariadne_endpoint_mocks):
    """Test cuando el jugador no existe"""
    ariadne_endpoint_mocks.player_service.get_player_by_id.return_value = None

    response = client.put(ariadne_endpoint_mocks.url)

    assert response.status_code == 404
    assert response.json() == {"detail": "Player not found"}
    
    # No debería llamar a los siguientes servicios
    ariadne_endpoint_mocks.set_service.add_card_to_set.assert_not_called()
    ariadne_endpoint_mocks.game_service.change_turn_state.assert_not_called()
    ariadne_endpoint_mocks.manager.broadcast_to_game.assert_not_awaited()


def test_play_ariadne_player_wrong_game(client, ariadne_endpoint_mocks):
    """Test cuando el jugador no pertenece al juego"""
    wrong_game_id = uuid.uuid4()
    fake_player = MagicMock()
    fake_player.game_id = wrong_game_id  # Juego diferente
    ariadne_endpoint_mocks.player_service.get_player_by_id.return_value = fake_player

    response = client.put(ariadne_endpoint_mocks.url)

    assert response.status_code == 404
    assert response.json() == {"detail": "Player not found"}
    
    ariadne_endpoint_mocks.set_service.add_card_to_set.assert_not_called()


def test_play_ariadne_set_not_found(client, ariadne_endpoint_mocks):
    """Test cuando el set no existe"""
    ariadne_endpoint_mocks.set_service.get_set_by_id.return_value = None

    response = client.put(ariadne_endpoint_mocks.url)

    assert response.status_code == 404
    assert response.json() == {"detail": "Set not found"}
    
    ariadne_endpoint_mocks.set_service.add_card_to_set.assert_not_called()
    ariadne_endpoint_mocks.game_service.change_turn_state.assert_not_called()


def test_play_ariadne_set_wrong_game(client, ariadne_endpoint_mocks):
    """Test cuando el set no pertenece al juego"""
    wrong_game_id = uuid.uuid4()
    wrong_set = make_set_out(
        ariadne_endpoint_mocks.sid,
        wrong_game_id,  # Juego diferente
        ariadne_endpoint_mocks.set_owner_id
    )
    ariadne_endpoint_mocks.set_service.get_set_by_id.return_value = wrong_set

    response = client.put(ariadne_endpoint_mocks.url)

    assert response.status_code == 404
    assert response.json() == {"detail": "Set not found"}
    
    ariadne_endpoint_mocks.set_service.add_card_to_set.assert_not_called()


def test_play_ariadne_card_not_found(client, ariadne_endpoint_mocks):
    """Test cuando la carta no existe"""
    ariadne_endpoint_mocks.card_service.get_card_by_id.return_value = None

    response = client.put(ariadne_endpoint_mocks.url)

    assert response.status_code == 404
    assert response.json() == {"detail": "Card not found"}
    
    ariadne_endpoint_mocks.set_service.add_card_to_set.assert_not_called()


def test_play_ariadne_card_wrong_owner(client, ariadne_endpoint_mocks):
    """Test cuando la carta no pertenece al jugador"""
    wrong_owner_id = uuid.uuid4()
    fake_card = MagicMock()
    fake_card.name = "D_AO"
    fake_card.owner_player_id = wrong_owner_id  # Dueño diferente
    ariadne_endpoint_mocks.card_service.get_card_by_id.return_value = fake_card

    response = client.put(ariadne_endpoint_mocks.url)

    assert response.status_code == 404
    assert response.json() == {"detail": "Card not found"}
    
    ariadne_endpoint_mocks.set_service.add_card_to_set.assert_not_called()


def test_play_ariadne_card_not_ariadne(client, ariadne_endpoint_mocks):
    """Test cuando la carta no es Detective Ariadne Oliver"""
    fake_card = MagicMock()
    fake_card.name = "D_MS"  # No es Ariadne
    fake_card.owner_player_id = ariadne_endpoint_mocks.pid
    ariadne_endpoint_mocks.card_service.get_card_by_id.return_value = fake_card

    response = client.put(ariadne_endpoint_mocks.url)

    assert response.status_code == 400
    assert response.json() == {"detail": "Card is not Detective Ariadne Oliver"}
    
    ariadne_endpoint_mocks.set_service.add_card_to_set.assert_not_called()


def test_play_ariadne_add_card_fails(client, ariadne_endpoint_mocks):
    """Test cuando falla agregar la carta al set"""
    error_msg = "Cannot add card to completed set"
    ariadne_endpoint_mocks.set_service.add_card_to_set.side_effect = ValueError(error_msg)

    response = client.put(ariadne_endpoint_mocks.url)

    assert response.status_code == 400
    assert response.json() == {"detail": error_msg}
    
    # Debería haber intentado agregar la carta
    ariadne_endpoint_mocks.set_service.add_card_to_set.assert_called_once()
    # Pero no debería cambiar el estado ni hacer broadcast
    ariadne_endpoint_mocks.game_service.change_turn_state.assert_not_called()
    ariadne_endpoint_mocks.manager.broadcast_to_game.assert_not_awaited()
    
#-------------------------------------------------
@pytest.fixture
def play_set_cancellation_mock(monkeypatch):
    """Fixture para mockear las dependencias de play_set cancelable."""
    gid = uuid.uuid4()
    pid = uuid.uuid4()
    tid = uuid.uuid4()  # Target player ID
    cid1, cid2 = uuid.uuid4(), uuid.uuid4()

    # --- Mock GameService ---
    fake_game = MagicMock()
    fake_game.players_ids = [pid, tid]

    fake_turn_state = SimpleNamespace(turn_state=TurnState.IDLE, is_cancelled=True)
    fake_game_service = MagicMock()
    fake_game_service.get_game_by_id.return_value = fake_game
    fake_game_service.get_turn.return_value = pid
    fake_game_service.get_turn_state.return_value = fake_turn_state
    fake_game_service.change_turn_state.return_value = None
    monkeypatch.setattr(endpoints_mod, "GameService", lambda db: fake_game_service)

    # --- Mock PlayerService ---
    fake_player = MagicMock(social_disgrace=False)
    fake_player_service = MagicMock()
    fake_player_service.get_player_entity_by_id.return_value = fake_player
    monkeypatch.setattr(endpoints_mod, "PlayerService", lambda db: fake_player_service)

    # --- Mock SetService ---
    fake_set_out = make_set_out(uuid.uuid4(), gid, pid, SetType.MS)
    fake_set_service = MagicMock()
    fake_set_service.determine_set_type.return_value = SetType.MS
    fake_set_service.create_set.return_value = fake_set_out
    fake_set_service.play_set.return_value = fake_set_out
    fake_set_service.verify_cancellable_new_set.return_value = True
    fake_set_service.verify_cancellable_set.return_value = True
    monkeypatch.setattr(endpoints_mod, "SetService", lambda db: fake_set_service)

    # --- Mock CardService.wait_for_cancellation ---
    async def fake_wait_for_cancellation(db, gid, timeout=50):
        return None  # Simula que terminó sin errores

    monkeypatch.setattr(
        endpoints_mod, "CardService",
        type("FakeCardService", (), {"wait_for_cancellation": staticmethod(fake_wait_for_cancellation)})
    )

    # --- Mock manager (broadcast) ---
    fake_manager = patch_manager(monkeypatch)

    # --- Payload válido ---
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

def test_play_set_cancelable_flow(client, play_set_cancellation_mock):
    """
    Caso: verify_cancellable_new_set=True
    - Cambia estado a CANCELLED_CARD_PENDING
    - Hace 2 broadcasts (waitingForCancellationSet + cancelationStopped)
    - Devuelve SetOut con status 201
    """
    mocks = play_set_cancellation_mock

    response = client.post(f"/sets/play/{mocks.gid}", json=mocks.payload)

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["id"] == str(mocks.set_service.create_set.return_value.id)
    assert data["type"] == mocks.set_service.create_set.return_value.type.value
    assert data["owner_player_id"] == str(mocks.set_service.create_set.return_value.owner_player_id)

    # --- Validar broadcasts ---
    calls = mocks.manager.broadcast_to_game.await_args_list
    assert len(calls) == 5

    first_event = calls[1].args[1]
    second_event = calls[4].args[1]

    assert first_event["type"] == "waitingForCancellationSet"
    assert "player_id" in first_event["data"]
    assert "set_type" in first_event["data"]

    assert second_event["type"] == "cancellationStopped"

def test_add_card_to_set_canceled_flow(client, play_set_cancellation_mock, monkeypatch):
    """
    Caso donde la carta se cancela efectivamente después de wait_for_cancellation.
    Debe emitir 'cancelationStopped' y retornar SetOut.
    """

    mocks = play_set_cancellation_mock
    gid, pid, tid = mocks.gid, mocks.pid, mocks.tid
    set_id, card_id = uuid.uuid4(), uuid.uuid4()

    # --- Mock SetService ---
    fake_set = MagicMock()
    fake_set.id = set_id
    fake_set.game_id = gid
    fake_set.owner_player_id = pid
    fake_set.type = SetType.MS

    mocks.set_service.get_set_by_id.return_value = fake_set
    mocks.set_service.add_card_to_set.return_value = fake_set
    mocks.set_service.verify_cancellable_set.return_value = True

    # --- Mock PlayerService ---
    fake_player = MagicMock(social_disgrace=False)
    fake_player_service = MagicMock()
    fake_player_service.get_player_entity_by_id.return_value = fake_player
    monkeypatch.setattr("app.set.endpoints.PlayerService", lambda db: fake_player_service)

    # --- Mock CardService ---
    fake_card_service = MagicMock()
    fake_card_service.wait_for_cancellation = AsyncMock(return_value=None)
    monkeypatch.setattr("app.set.endpoints.CardService", fake_card_service)

    # --- Mock GameService para simular cancelación ---
    mocks.game_service.get_turn_state.return_value.is_cancelled = True

    # --- Ejecutar petición ---
    response = client.put(
        f"/sets/{set_id}/cards/{card_id}",
        params={
            "game_id": str(gid),
            "player_id": str(pid),
            "target_player_id": str(tid),
        },
    )

    # --- Verificaciones ---
    assert response.status_code == 200, response.text
    data = response.json()

    # Debe haber emitido dos broadcasts:
    # 1. waitingForCancellationSet
    # 2. cancelationStopped
    calls = mocks.manager.broadcast_to_game.await_args_list
    assert len(calls) == 5

    types = [args[0][1]["type"] for args in calls]
    assert "waitingForCancellationSet" in types
    assert any(c[0][1]["type"] == "cancellationStopped" for c in calls)

    # Debe devolver el Set cancelado
    assert data["id"] == str(fake_set.id)
    assert data["type"] == fake_set.type.value
    assert data["owner_player_id"] == str(fake_set.owner_player_id)

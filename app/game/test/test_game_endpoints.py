import pytest
from fastapi.testclient import TestClient
from app.game.enums import GameEndReason, WinningTeam, PlayerRole
from app.game.schemas import EndGameResult

from app.main import app
from app.game.dtos import GameOutDTO

import uuid

client = TestClient(app)


@pytest.fixture(autouse=True)
def fake_db_dependency(monkeypatch):
    """Avoid hitting the real database in endpoint tests."""
    def fake_get_db():
        yield object()

    monkeypatch.setattr("app.game.endpoints.get_db", fake_get_db)


def make_game(
    game_id=None,
    players=None,
    max_players=4,
    ready=False,
):
    game_id = game_id or uuid.uuid4()
    players = players or []
    return GameOutDTO(
        id=game_id,
        name="Test Game",
        host_id=uuid.uuid4(),
        min_players=2,
        max_players=max_players,
        ready=ready,
        players_ids=players,
    )


def test_list_games_default(monkeypatch):
    """
    GET /games sin params:
    - Debe devolver 200
    - (Por defecto) solo partidas no iniciadas y no llenas.
    Nota: si no hay partidas, simplemente será [] y el loop no entra.
    """
    players = [uuid.uuid4()]

    class DummyGameService:
        def __init__(self, db): pass
        def get_games(self, full=False, ready=False):
            assert full is False
            assert ready is False
            return [make_game(players=players, ready=False, max_players=4)]

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)

    r = client.get("/games")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)

    for g in data:
        assert not g["ready"]
        assert len(g["players_ids"]) < g["max_players"]


def test_list_games_with_flags(monkeypatch):
    """
    GET /games con full=true y ready=true:
    - Debe devolver 200
    - Puede incluir partidas llenas e iniciadas (no hacemos aserciones de filtro).
    """
    class DummyGameService:
        def __init__(self, db): pass
        def get_games(self, full=False, ready=False):
            assert full is True
            assert ready is True
            return [make_game(ready=True, players=[uuid.uuid4()])]

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)

    r = client.get("/games", params={"full": True, "ready": True})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)

def test_list_games_full_true_ready_false(monkeypatch):
    """
    GET /games con full=true y ready=false:
    - 200 OK
    - Debe incluir SOLO partidas NO iniciadas (ready == False).
    - Puede incluir llenas o no (no se restringe por cantidad de jugadores).
    """
    class DummyGameService:
        def __init__(self, db): pass
        def get_games(self, full=False, ready=False):
            assert full is True
            assert ready is False
            return [make_game(ready=False)]

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)

    r = client.get("/games", params={"full": True, "ready": False})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)

    for g in data:
        assert not g["ready"]  # sigue filtrando iniciadas


def test_list_games_full_false_ready_true(monkeypatch):
    """
    GET /games con full=false y ready=true:
    - 200 OK
    - Debe incluir SOLO partidas NO llenas (len(players_ids) < max_players).
    - Pueden estar iniciadas o no (no se filtra por ready).
    """
    class DummyGameService:
        def __init__(self, db): pass
        def get_games(self, full=False, ready=False):
            assert full is False
            assert ready is True
            return [make_game(players=[uuid.uuid4()], max_players=2)]

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)

    r = client.get("/games", params={"full": False, "ready": True})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)

    for g in data:
        assert len(g["players_ids"]) < g["max_players"]  # sigue filtrando llenas


def test_get_game_by_id_not_found(monkeypatch):
    fake_id = uuid.uuid4()

    class DummyGameService:
        def __init__(self, db): pass
        def get_game_by_id(self, game_id): return None

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)

    response = client.get(f"/games/{fake_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Juego no encontrado"


def test_get_game_by_id_ok(monkeypatch):
    fake_id = uuid.uuid4()
    returned_game = make_game(game_id=fake_id)

    class DummyGameService:
        def __init__(self, db): pass
        def get_game_by_id(self, game_id): return returned_game

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)

    response = client.get(f"/games/{fake_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(fake_id)
    assert payload["name"] == "Test Game"


def test_create_game_endpoint(monkeypatch):
    created_game = make_game(players=[])
    captured = {}

    class DummyGameService:
        def __init__(self, db): pass
        def create_game(self, payload):
            captured["payload_type"] = type(payload).__name__
            return created_game

    class DummyMenuManager:
        def __init__(self): self.messages = []
        async def broadcast(self, message): self.messages.append(message)

    dummy_menu = DummyMenuManager()
    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)
    monkeypatch.setattr("app.game.endpoints.menu_manager", dummy_menu)

    response = client.post(
        "/games",
        json={
            "name": "New Game",
            "host_name": "Host",
            "birthday": "1990-01-01",
            "min_players": 2,
            "max_players": 4,
        },
    )

    assert response.status_code == 201
    assert response.json()["id"] == str(created_game.id)
    assert captured["payload_type"] == "GameInDTO"
    assert dummy_menu.messages
    assert dummy_menu.messages[0]["type"] == "gameAdd"


def test_add_player_game_unavailable(monkeypatch):
    game_id = uuid.uuid4()

    class DummyGameService:
        def __init__(self, db): pass
        def add_player(self, game_id, player_data): return None

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)

    response = client.post(
        f"/games/{game_id}/players",
        json={"name": "Alice", "birthday": "2000-01-01"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "GameUnavailable"


def test_add_player_game_not_found(monkeypatch):
    game_id = uuid.uuid4()

    class DummyGameService:
        def __init__(self, db): pass
        def add_player(self, game_id, player_data): return uuid.uuid4()
        def get_game_by_id(self, game_id): return None

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)

    response = client.post(
        f"/games/{game_id}/players",
        json={"name": "Alice", "birthday": "2000-01-01"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "GameNotFound"


def test_add_player_game_full_broadcast(monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    updated_game = make_game(
        game_id=game_id,
        players=[uuid.uuid4(), uuid.uuid4()],
        max_players=2,
    )

    class DummyGameService:
        def __init__(self, db): pass
        def add_player(self, gid, player_data): return player_id
        def get_game_by_id(self, gid): return updated_game

    class DummyManager:
        def __init__(self): self.calls = []
        async def broadcast_to_game(self, gid, payload): self.calls.append((gid, payload))

    class DummyMenuManager:
        def __init__(self): self.messages = []
        async def broadcast(self, message): self.messages.append(message)

    dummy_manager = DummyManager()
    dummy_menu = DummyMenuManager()

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)
    monkeypatch.setattr("app.game.endpoints.manager", dummy_manager)
    monkeypatch.setattr("app.game.endpoints.menu_manager", dummy_menu)

    response = client.post(
        f"/games/{game_id}/players",
        json={"name": "Alice", "birthday": "2000-01-01"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "game_id": str(game_id),
        "player_id": str(player_id),
    }
    assert dummy_manager.calls
    broadcast_payload = dummy_manager.calls[0][1]
    assert broadcast_payload["type"] == "playerJoined"
    assert broadcast_payload["data"]["game_id"] == str(game_id)
    assert dummy_menu.messages
    assert dummy_menu.messages[-1]["type"] == "gameUnavailable"


def test_add_player_game_not_full(monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    updated_game = make_game(
        game_id=game_id,
        players=[uuid.uuid4()],
        max_players=2,
    )

    class DummyGameService:
        def __init__(self, db): pass
        def add_player(self, gid, player_data): return player_id
        def get_game_by_id(self, gid): return updated_game

    class DummyManager:
        def __init__(self): self.calls = []
        async def broadcast_to_game(self, gid, payload): self.calls.append((gid, payload))

    class DummyMenuManager:
        def __init__(self): self.messages = []
        async def broadcast(self, message): self.messages.append(message)

    dummy_manager = DummyManager()
    dummy_menu = DummyMenuManager()

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)
    monkeypatch.setattr("app.game.endpoints.manager", dummy_manager)
    monkeypatch.setattr("app.game.endpoints.menu_manager", dummy_menu)

    response = client.post(
        f"/games/{game_id}/players",
        json={"name": "Alice", "birthday": "2000-01-01"},
    )

    assert response.status_code == 200
    assert dummy_manager.calls[0][1]["type"] == "playerJoined"
    assert dummy_manager.calls[0][1]["data"]["player_id"] == str(player_id)
    assert dummy_manager.calls[0][1]["data"]["player_name"] == "Alice"
    assert dummy_menu.messages[0]["type"] == "joinPlayerToGame"


def test_start_game_conditions_not_met(monkeypatch):
    game_id = uuid.uuid4()

    class DummyGameService:
        def __init__(self, db): pass
        def can_start(self, gid): return False

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)

    response = client.post(f"/games/{game_id}/start")

    assert response.status_code == 400
    assert response.json()["detail"] == "StartConditionsNotMet"


def test_start_game_not_found(monkeypatch):
    game_id = uuid.uuid4()

    class DummyGameService:
        def __init__(self, db): pass
        def can_start(self, gid): return True
        def start_game(self, gid): pass
        def get_game_by_id(self, gid): return None

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)

    response = client.post(f"/games/{game_id}/start")

    assert response.status_code == 404
    assert response.json()["detail"] == "GameNotFound"


def test_start_game_success(monkeypatch):
    game_id = uuid.uuid4()
    updated_game = make_game(game_id=game_id)
    flags = {"started": False}

    class DummyGameService:
        def __init__(self, db): pass
        def can_start(self, gid): return True
        def start_game(self, gid): flags["started"] = True
        def get_game_by_id(self, gid): return updated_game

    class DummyManager:
        def __init__(self): self.calls = []
        async def broadcast_to_game(self, gid, payload): self.calls.append((gid, payload))

    class DummyMenuManager:
        def __init__(self): self.messages = []
        async def broadcast(self, message): self.messages.append(message)

    dummy_manager = DummyManager()
    dummy_menu = DummyMenuManager()

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)
    monkeypatch.setattr("app.game.endpoints.manager", dummy_manager)
    monkeypatch.setattr("app.game.endpoints.menu_manager", dummy_menu)

    response = client.post(f"/games/{game_id}/start")

    assert response.status_code == 204
    assert flags["started"] is True
    assert dummy_manager.calls[0][1]["type"] == "GameStarted"
    assert dummy_menu.messages[0]["type"] == "gameUnavailable"


def test_get_turn_not_found(monkeypatch):
    """
    GET /turn/{game_id} cuando no hay turno:
    - Debe devolver 404 con detail="PlayerNotFound"
    """
    fake_id = uuid.uuid4()

    # mock del servicio para devolver None
    class DummyGameService:
        def __init__(self, db): pass
        def get_turn(self, game_id): return None

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)

    r = client.get(f"/games/turn/{fake_id}")
    assert r.status_code == 404
    assert r.json()["detail"] == "PlayerNotFound"


def test_get_turn_ok(monkeypatch):
    """
    GET /turn/{game_id} cuando existe turno:
    - Debe devolver 200 con {"id": <uuid>}
    """
    fake_id = uuid.uuid4()

    class DummyGameService:
        def __init__(self, db): pass
        def get_turn(self, game_id): return str(fake_id)

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)

    r = client.get(f"/games/turn/{fake_id}")
    assert r.status_code == 200
    assert r.json() == {"id": str(fake_id)}


def test_post_turn_not_found(monkeypatch):
    """
    POST /turn/{game_id} cuando no hay próximo jugador:
    - Debe devolver 404 con detail="PlayerNotFound"
    """
    fake_id = uuid.uuid4()

    class DummyGameService:
        def __init__(self, db): pass
        def next_player(self, game_id): 
            raise ValueError(f"El juego {game_id} no esta iniciado o no tiene suficientes jugadores")

    async def fake_broadcast(*args, **kwargs): return None

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)
    monkeypatch.setattr("app.game.endpoints.manager.broadcast_to_game", fake_broadcast)

    r = client.post(f"games/turn/{fake_id}")
    assert r.status_code == 404
    assert f"El juego {fake_id}" in r.json()["detail"]


def test_post_turn_ok(monkeypatch):
    """
    POST /turn/{game_id} cuando hay próximo jugador:
    - Debe devolver 200 con {"id": <uuid>}
    - Debe invocar broadcast_to_game
    """
    next_player_id = uuid.uuid4()

    # Mock GameService
    class DummyGameService:
        def __init__(self, db): pass
        def next_player(self, game_id): return next_player_id

    monkeypatch.setattr("app.game.endpoints.GameService", DummyGameService)

    # Mock CardService.update_draft y query_draft
    class DummyCardService:
        def __init__(self): pass
        def update_draft(self,db, game_id): return None  # simulamos que no hay cartas para actualizar
        def query_draft(self,db, game_id): return []

    monkeypatch.setattr("app.game.endpoints.CardService", DummyCardService)

    # Mock broadcast_to_game (async)
    called = {}
    async def fake_broadcast(self, game_id, payload):
        called["done"] = True
        called["payload"] = payload

    monkeypatch.setattr("app.game.endpoints.manager", type("Manager", (), {"broadcast_to_game": fake_broadcast})())

    game_id = uuid.uuid4()
    r = client.post(f"/games/turn/{game_id}")
    assert r.status_code == 200
    assert r.json() == {"id": str(next_player_id)}  # convertir UUID a str para comparación
    # Como no hay cartas para actualizar, solo se hace turnChange
    assert called["done"] is True
    assert called["payload"]["type"] == "turnChange"
    assert called["payload"]["data"] == str(next_player_id)

def test_turn_change_ends_game(mocker):
    """
    Prueba que el endpoint de cambio de turno maneja correctamente el fin de la partida.
    """
    game_id = uuid.uuid4()
    
    # Creamos un objeto falso de EndGameResult que el servicio simulará devolver
    fake_end_result = EndGameResult(
        reason=GameEndReason.DECK_EMPTY,
        winning_team=WinningTeam.MURDERERS,
        winners=[],
        player_roles=[]
    )

    mocker.patch(
        'app.game.endpoints.GameService.next_player',
        return_value=fake_end_result
    )
    
    mock_broadcast = mocker.patch('app.game.endpoints.manager.broadcast_to_game')

    response = client.post(f"/games/turn/{game_id}")
    
    # Verificamos la respuesta HTTP
    assert response.status_code == 200
    assert response.json() == {"detail": "Game has ended"}
    
    # Verificamos que se haya llamado al broadcast del websocket
    mock_broadcast.assert_called_once()
    
    # Verificamos que el broadcast se haya llamado con los datos correctos
    call_args = mock_broadcast.call_args[0]
    broadcast_payload = call_args[1]
    assert broadcast_payload["type"] == "gameEnded"
    assert broadcast_payload["data"]["reason"] == "DECK_EMPTY"

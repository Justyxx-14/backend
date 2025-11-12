import uuid
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.encoders import jsonable_encoder
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, ANY
from sqlalchemy.orm import Session
from app.main import app
from app.db import get_db

# Importá tu router real
from app.secret.endpoints import secret_router
from app.secret import endpoints as endpoints_mod
from app.secret.enums import SecretType
from app.secret.schemas import SecretOut
from app.game.enums import TurnState, GameEndReason
from app.game.schemas import GameTurnStateOut, EndGameResult
from app.game.models import Game

# Helpers
def make_app():
    app = FastAPI()
    app.include_router(secret_router)
    return app

def make_secret_dto(
    secret_id,
    game_id,
    owner_id,
    role_="COMMON",
    desc="...",
    revealed=False,
    name="Secret",
):
    return SimpleNamespace(
        id=secret_id,
        game_id=game_id,
        owner_player_id=owner_id,
        role=role_ if isinstance(role_, SecretType) else SecretType(role_), 
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

    dto = make_secret_dto(sid, gid, pid, role_=SecretType.MURDERER, desc="foo", revealed=True)

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
    assert item["role"] == "MURDERER"
    assert item["description"] == "foo"
    assert item.get("revealed") is True


def test_list_by_player_filters_by_game_returns_only_matching(monkeypatch):
    app = make_app()
    c = TestClient(app)
    gid = uuid.uuid4()
    other_gid = uuid.uuid4()
    pid = uuid.uuid4()

    dto_ok1 = SecretOut(
        id=uuid.uuid4(), game_id=gid, owner_player_id=pid,
        role=SecretType.COMMON, name="S1", description="d", revealed=False
    )
    dto_ok2 = SecretOut(
        id=uuid.uuid4(), game_id=gid, owner_player_id=pid,
        role=SecretType.ACCOMPLICE, name="S2", description="d", revealed=False
    )
    dto_other_game = SecretOut(
        id=uuid.uuid4(), game_id=other_gid, owner_player_id=pid,
        role=SecretType.COMMON, name="S3", description="d", revealed=False
    )

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


def test_social_disgrace_game_not_found(monkeypatch):
    app = make_app()
    c = TestClient(app)
    gid = uuid.uuid4()

    fake_game_service = SimpleNamespace(get_game_by_id=lambda game_id: None)
    fake_player_service = SimpleNamespace(get_players_by_game_id=lambda game_id: [])

    monkeypatch.setattr(endpoints_mod, "GameService", lambda db: fake_game_service)
    monkeypatch.setattr(endpoints_mod, "PlayerService", lambda db: fake_player_service)

    response = c.get(f"/secrets/social_disgrace?game_id={gid}")

    assert response.status_code == 404
    assert response.json()["detail"] == "GameNotFound"


def test_social_disgrace_without_players(monkeypatch):
    app = make_app()
    c = TestClient(app)
    gid = uuid.uuid4()

    fake_game_service = SimpleNamespace(get_game_by_id=lambda game_id: object())
    fake_player_service = SimpleNamespace(get_players_by_game_id=lambda game_id: [])

    monkeypatch.setattr(endpoints_mod, "GameService", lambda db: fake_game_service)
    monkeypatch.setattr(endpoints_mod, "PlayerService", lambda db: fake_player_service)

    response = c.get(f"/secrets/social_disgrace?game_id={gid}")

    assert response.status_code == 400
    assert response.json()["detail"] == "GameDontHavePlayers"


def test_social_disgrace_returns_mapping(monkeypatch):
    app = make_app()
    c = TestClient(app)
    gid = uuid.uuid4()

    fake_game_service = SimpleNamespace(get_game_by_id=lambda game_id: object())
    player_a = SimpleNamespace(id=uuid.uuid4(), social_disgrace=True)
    player_b = SimpleNamespace(id=uuid.uuid4(), social_disgrace=False)
    fake_player_service = SimpleNamespace(
        get_players_by_game_id=lambda game_id: [player_a, player_b]
    )

    monkeypatch.setattr(endpoints_mod, "GameService", lambda db: fake_game_service)
    monkeypatch.setattr(endpoints_mod, "PlayerService", lambda db: fake_player_service)

    response = c.get(f"/secrets/social_disgrace?game_id={gid}")

    assert response.status_code == 200
    data = response.json()
    assert data == {str(player_a.id): True, str(player_b.id): False}

@pytest.fixture
def client(monkeypatch):
    """
    Crea un TestClient de FastAPI y mockea la dependencia de 
    base de datos (get_db) para este archivo.
    """
    # 1. Mockea la base de datos
    mock_db = MagicMock(spec=Session)
    
    def _fake_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _fake_get_db
    
    # 2. Crea el cliente
    with TestClient(app) as c:
        yield c # El test se ejecuta aquí
    
    # 3. Limpia el mock
    app.dependency_overrides.clear()

@pytest.fixture
def pys_reveal_setup(monkeypatch):
    """Configura mocks para el endpoint /reveal_for_pys."""
    # Mock de GameService
    mock_game_service = MagicMock()
    mock_turn_state_dto = MagicMock() # El DTO
    mock_game_service.get_turn_state.return_value = mock_turn_state_dto
    
    # Mock de SecretService
    mock_secret_service = MagicMock()
    
    # Mock de Manager
    mock_manager = MagicMock()
    mock_manager.broadcast_to_game = AsyncMock()

    # Inyectar Mocks
    monkeypatch.setattr("app.secret.endpoints.GameService", lambda db: mock_game_service)
    monkeypatch.setattr("app.secret.endpoints.SecretService", mock_secret_service)
    monkeypatch.setattr("app.secret.endpoints.manager", mock_manager)

    return {
        "mock_game_service": mock_game_service,
        "mock_secret_service": mock_secret_service,
        "mock_manager": mock_manager,
        "mock_turn_state_dto": mock_turn_state_dto
    }

# --- Tests del endpoint /reveal_for_pys ---

def test_reveal_secret_for_pys_ok(client, pys_reveal_setup):
    """Prueba el flujo exitoso de revelación de secreto post-PYS."""
    # Arrange
    mock_game_service = pys_reveal_setup["mock_game_service"]
    mock_secret_service = pys_reveal_setup["mock_secret_service"]
    mock_manager = pys_reveal_setup["mock_manager"]
    mock_turn_state_dto = pys_reveal_setup["mock_turn_state_dto"]
    
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    secret_id = uuid.uuid4()
    
    # Configurar Mocks para el estado correcto
    mock_turn_state_dto.turn_state = TurnState.CHOOSING_SECRET_PYS
    mock_turn_state_dto.target_player_id = player_id # El jugador que llama ES el objetivo

    # Simular el secreto revelado que devuelve el servicio
    revealed_secret_dto = SecretOut(
        id=secret_id, 
        name="Test Secret", 
        revealed=True,
        game_id=game_id, # <-- Campo requerido
        role=SecretType.COMMON, # <-- Campo requerido
        description="A test secret", # <-- Campo requerido
        owner_player_id=player_id # <-- Campo requerido
    )
    # Convertir a dict para la aserción de la respuesta (FastAPI lo serializa)
    revealed_secret_dict = jsonable_encoder(revealed_secret_dto)
    
    # Mockear el método estático change_secret_status
    mock_secret_service.change_secret_status.return_value = revealed_secret_dto

    payload = {"player_id": str(player_id), "secret_id": str(secret_id)}

    # Act
    response = client.put(f"/secrets/reveal_for_pys/{game_id}", json=payload)
    
    # Assert
    assert response.status_code == 200
    assert response.json()["id"] == str(secret_id)
    assert response.json()["revealed"] is True

    # Verificar llamadas
    mock_secret_service.change_secret_status.assert_called_once_with(ANY, secret_id)
    mock_game_service.change_turn_state.assert_called_once_with(game_id, TurnState.DISCARDING)
    
    # Verificar broadcast

    args, _ = mock_manager.broadcast_to_game.call_args
    assert args[0] == game_id
    evt = args[1]
    assert evt["type"] == "secretRevealed"

def test_reveal_secret_for_pys_error_wrong_state(client, pys_reveal_setup):
    """Falla si el estado del juego NO es CHOOSING_SECRET_PYS."""
    # Arrange
    mock_turn_state_dto = pys_reveal_setup["mock_turn_state_dto"]
    
    game_id = uuid.uuid4()
    mock_turn_state_dto.turn_state = TurnState.IDLE # Estado incorrecto
    
    payload = {"player_id": str(uuid.uuid4()), "secret_id": str(uuid.uuid4())}

    # Act
    response = client.put(f"/secrets/reveal_for_pys/{game_id}", json=payload)
    
    # Assert
    assert response.status_code == 400
    assert "Invalid action" in response.json()["detail"]

def test_reveal_secret_for_pys_error_wrong_player(client, pys_reveal_setup):
    """Falla si el jugador que llama NO es el target_player_id."""
    # Arrange
    mock_turn_state_dto = pys_reveal_setup["mock_turn_state_dto"]
    
    game_id = uuid.uuid4()
    target_player_id = uuid.uuid4() # El jugador que DEBE revelar
    attacker_player_id = uuid.uuid4() # El jugador que INTENTA revelar
    
    # Configurar Mocks para el estado correcto
    mock_turn_state_dto.turn_state = TurnState.CHOOSING_SECRET_PYS
    mock_turn_state_dto.target_player_id = target_player_id

    # El payload es enviado por el jugador incorrecto
    payload = {"player_id": str(attacker_player_id), "secret_id": str(uuid.uuid4())}

    # Act
    response = client.put(f"/secrets/reveal_for_pys/{game_id}", json=payload)
    
    # Assert
    assert response.status_code == 403
    assert "not your turn to reveal" in response.json()["detail"]

@pytest.fixture
def sfp_reveal_setup(monkeypatch):
    """Configura mocks para el endpoint /reveal_for_sfp."""
    mock_game_service = MagicMock()
    mock_secret_service = MagicMock()
    mock_card_service = MagicMock() # Para check_players_SFP
    mock_manager = MagicMock(broadcast_to_game=AsyncMock())

    monkeypatch.setattr("app.secret.endpoints.GameService", lambda db: mock_game_service)
    monkeypatch.setattr("app.secret.endpoints.SecretService", mock_secret_service)
    monkeypatch.setattr("app.secret.endpoints.CardService", mock_card_service)
    monkeypatch.setattr("app.secret.endpoints.manager", mock_manager)
    
    # Mock del timer (importante)
    mock_timer = MagicMock(resume_timer=MagicMock(), get_remaining_time=MagicMock(return_value=10))
    monkeypatch.setattr("app.secret.endpoints.turn_timer_manager", mock_timer)

    return {
        "mock_game_service": mock_game_service,
        "mock_secret_service": mock_secret_service,
        "mock_card_service": mock_card_service,
        "mock_manager": mock_manager,
        "mock_timer": mock_timer
    }

def test_reveal_for_sfp_ok_last_player(client, sfp_reveal_setup):
    """
    Prueba que el último jugador SFP revela,
    la lista se vacía y el estado cambia a DISCARDING.
    """
    # Arrange
    game_id, p1_id, s1_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mock_game_service = sfp_reveal_setup["mock_game_service"]
    mock_secret_service = sfp_reveal_setup["mock_secret_service"]
    mock_card_service = sfp_reveal_setup["mock_card_service"]
    mock_manager = sfp_reveal_setup["mock_manager"]
    mock_timer = sfp_reveal_setup["mock_timer"]
    
    # 1. Estado es PENDING, p1 está en la lista
    mock_game_service.get_turn_state.return_value = MagicMock(
        spec=GameTurnStateOut, turn_state=TurnState.PENDING_DEVIOUS
    )
    mock_game_service.get_game_entity_by_id.return_value = MagicMock(
        spec=Game, turn_state=MagicMock(sfp_players=[str(p1_id)])
    )
    
    # 2. SecretService devuelve un secreto COMMON
    fake_secret = SecretOut(
        id=s1_id,
        name="S1",
        role=SecretType.COMMON,
        revealed=True,
        
        # --- AÑADE LOS CAMPOS FALTANTES ---
        game_id=game_id,
        description="Un secreto de prueba para el mock",
        owner_player_id=p1_id
    )
    mock_secret_service.change_secret_status.return_value = fake_secret
    
    # 3. check_players_SFP devuelve True (es el último)
    mock_card_service.check_players_SFP.return_value = True

    payload = {"player_id": str(p1_id), "secret_id": str(s1_id)}

    # Act
    response = client.put(f"/secrets/reveal_for_sfp/{game_id}", json=payload)
    
    # Assert
    assert response.status_code == 200
    assert response.json()["id"] == str(s1_id)

    # Verificar que se limpió la lista
    mock_card_service.check_players_SFP.assert_called_once_with(ANY, game_id, p1_id)
    # Verificar que el timer se reanudó
    mock_timer.resume_timer.assert_called_once_with(game_id)
    # Verificar que el estado cambió a DISCARDING
    mock_game_service.change_turn_state.assert_called_once_with(game_id, TurnState.DISCARDING)
    
    # Verificar que se envió 'secretRevealed'
    mock_manager.broadcast_to_game.assert_any_await(
        game_id,
        {"type": "secretRevealed", "data": ANY}
    )

def test_reveal_for_sfp_ok_not_last_player(client, sfp_reveal_setup):
    """
    Prueba que si un jugador SFP revela, pero NO es el último,
    el estado NO cambia a DISCARDING.
    """
    # ... (Setup similar al anterior)
    mock_game_service = sfp_reveal_setup["mock_game_service"]
    mock_secret_service = sfp_reveal_setup["mock_secret_service"]
    mock_card_service = sfp_reveal_setup["mock_card_service"]
    mock_timer = sfp_reveal_setup["mock_timer"]
    
    # 1. Estado es PENDING, p1 está en la lista
    mock_game_service.get_turn_state.return_value = MagicMock(
        spec=GameTurnStateOut, turn_state=TurnState.PENDING_DEVIOUS
    )

    game_id, p1_id, s1_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    
    mock_game_service.get_game_entity_by_id.return_value = MagicMock(
        spec=Game, 
        turn_state=MagicMock(sfp_players=[str(p1_id), str(uuid.uuid4())]) # <-- p1_id AHORA ESTÁ EN LA LISTA
    )
    
    # 2. SecretService devuelve un secreto COMMON
    mock_secret_service.change_secret_status.return_value = SecretOut(
        id=s1_id, name="S1", role=SecretType.COMMON, revealed=True,
        game_id=game_id, description="Otro mock", owner_player_id=p1_id
    )
    
    # 3. check_players_SFP devuelve False (NO es el último)
    mock_card_service.check_players_SFP.return_value = False
    
    payload = {"player_id": str(p1_id), "secret_id": str(s1_id)}

    # Act
    response = client.put(f"/secrets/reveal_for_sfp/{game_id}", json=payload)
    
    # Assert
    assert response.status_code == 200
    
    # Verificar que NO se reanuda el timer
    mock_timer.resume_timer.assert_not_called()
    # Verificar que NO se cambia el estado
    mock_game_service.change_turn_state.assert_not_called()

def test_reveal_for_sfp_fails_if_murderer(client, sfp_reveal_setup):
    """Prueba que si el jugador revela al ASESINO, el juego termina."""
    # ... (Setup)
    game_id, p1_id, s1_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mock_game_service = sfp_reveal_setup["mock_game_service"]
    mock_secret_service = sfp_reveal_setup["mock_secret_service"]
    mock_manager = sfp_reveal_setup["mock_manager"]
    mock_timer = sfp_reveal_setup["mock_timer"]
    
    mock_game_service.get_turn_state.return_value = MagicMock(
        spec=GameTurnStateOut, turn_state=TurnState.PENDING_DEVIOUS
    )
    mock_game_service.get_game_entity_by_id.return_value = MagicMock(
        spec=Game, turn_state=MagicMock(sfp_players=[str(p1_id)])
    )
    
    # 2. SecretService devuelve al ASESINO
    fake_secret = SecretOut(
        id=s1_id,
        name="S1",
        role=SecretType.MURDERER,
        revealed=True,
        
        # --- AÑADE LOS CAMPOS FALTANTES ---
        game_id=game_id,
        description="Un secreto de prueba para el mock",
        owner_player_id=p1_id
    )
    mock_secret_service.change_secret_status.return_value = fake_secret
    
    # 3. GameService.end_game devuelve un resultado
    mock_game_service.end_game.return_value = MagicMock(spec=EndGameResult)

    payload = {"player_id": str(p1_id), "secret_id": str(s1_id)}

    # Act
    response = client.put(f"/secrets/reveal_for_sfp/{game_id}", json=payload)
    
    # Assert
    assert response.status_code == 200 # El endpoint tiene éxito
    
    # Verificar que se llamó a end_game
    mock_game_service.end_game.assert_called_once_with(game_id, GameEndReason.MURDERER_REVEALED)
    
    # Verificar que se envió 'gameEnd'
    mock_manager.broadcast_to_game.assert_called_once_with(
        game_id,
        {"type": "gameEnd", "data": ANY}
    )
    
    # Verificar que NO se reanuda el timer NI se cambia el estado a DISCARDING
    mock_timer.resume_timer.assert_not_called()
    mock_game_service.change_turn_state.assert_not_called()
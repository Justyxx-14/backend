import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4
from fastapi.testclient import TestClient

from app.main import app
from app.card import schemas
from app.card.models import Card, CardOwner

client = TestClient(app)

@pytest.fixture
def mock_card():
    return Card(
        id=uuid4(),
        game_id=uuid4(),
        type="EVENT",
        name="TEST",
        description="TEST",
        owner=CardOwner.DRAFT,
        owner_player_id=None
    )

@pytest.fixture
def payload_dict():
    return {
        "game_id": str(uuid4()),
        "player_id": str(uuid4()),
        "card_id": str(uuid4())
    }

def test_draft_cards_success(mock_card):
    """Debe devolver las cartas del draft correctamente."""
    game_id = mock_card.game_id

    with (
        patch("app.card.endpoints.GameService") as mock_game_service,
        patch("app.card.endpoints.CardService.query_draft", return_value=[mock_card])
    ):
        mock_game_service.return_value.get_game_by_id.return_value = MagicMock(id=game_id)
        response = client.get(f"/cards/draft/{game_id}")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert data[0]["id"] == str(mock_card.id)
    assert data[0]["game_id"] == str(mock_card.game_id)
    assert data[0]["owner"] == mock_card.owner

def test_draft_cards_empty():
    """Debe devolver una lista vacía si no hay cartas."""
    game_id = uuid4()

    with (
        patch("app.card.endpoints.GameService") as mock_game_service,
        patch("app.card.endpoints.CardService.query_draft", return_value=None)
    ):
        mock_game_service.return_value.get_game_by_id.return_value = MagicMock(id=game_id)
        response = client.get(f"/cards/draft/{game_id}")

    assert response.status_code == 200
    assert response.json() == []

def test_draft_cards_game_not_found():
    """Debe devolver 400 si el juego no existe."""
    game_id = uuid4()

    with patch("app.card.endpoints.GameService") as mock_game_service:
        mock_game_service.return_value.get_game_by_id.return_value = None
        response = client.get(f"/cards/draft/{game_id}")

    assert response.status_code == 400
    assert response.json()["detail"] == "GameNotFound"

def test_pick_draft_card_success(mock_card):
    """Debe permitir al jugador tomar una carta del draft."""
    player_id = uuid4()
    game_id = mock_card.game_id

    payload = {
        "player_id": str(player_id),
        "card_id": str(mock_card.id),
        "to_owner": "PLAYER"
    }

    fake_game = MagicMock(id=game_id, players_ids=[player_id])

    with (
        patch("app.card.endpoints.GameService") as mock_game_service,
        patch("app.card.endpoints.CardService.pick_draft", return_value=(mock_card, False))
    ):
        mock_game_service.return_value.get_game_by_id.return_value = fake_game
        mock_game_service.return_value.get_turn.return_value = player_id
        response = client.put(f"/cards/draft/{game_id}", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(mock_card.id)
    assert data["game_id"] == str(mock_card.game_id)
    assert data["owner"] == mock_card.owner

def test_pick_draft_card_not_found(mock_card):
    """Debe devolver 404 si no hay cartas disponibles."""
    from app.card.endpoints import NoCardsException
    player_id = uuid4()
    game_id = mock_card.game_id

    payload = {
        "player_id": str(player_id),
        "card_id": str(mock_card.id),
        "to_owner": "PLAYER"
    }

    fake_game = MagicMock(id=game_id, players_ids=[player_id])

    with (
        patch("app.card.endpoints.GameService") as mock_game_service,
        patch("app.card.endpoints.CardService.pick_draft", side_effect=NoCardsException(game_id))
    ):
        mock_game_service.return_value.get_game_by_id.return_value = fake_game
        mock_game_service.return_value.get_turn.return_value = player_id
        response = client.put(f"/cards/draft/{game_id}", json=payload)

    assert response.status_code == 404
    assert "no hay cartas disponibles" in response.json()["detail"].lower()

def test_pick_draft_card_player_not_in_game(mock_card):
    """Debe devolver 400 si el jugador no pertenece al juego."""
    player_id = uuid4()
    game_id = mock_card.game_id

    payload = {
        "player_id": str(player_id),
        "card_id": str(mock_card.id),
        "to_owner": "PLAYER"
    }

    fake_game = MagicMock(id=game_id, players_ids=[])

    with patch("app.card.endpoints.GameService") as mock_game_service:
        mock_game_service.return_value.get_game_by_id.return_value = fake_game
        response = client.put(f"/cards/draft/{game_id}", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "GameNotFoundOrPlayerNotInGame"

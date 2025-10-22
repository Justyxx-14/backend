import datetime
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from app.player.endpoints import player_router
from app.player.schemas import PlayerOut


app = FastAPI()
app.include_router(player_router)
client = TestClient(app)

@pytest.fixture
def sample_player():
    return PlayerOut(
        id=uuid.uuid4(),
        name="test_player",
        birthday=datetime.date(1990, 1, 1),
        social_disgrace=False,
    )
    

def test_get_players_empty(monkeypatch):
    mock_service = MagicMock()
    mock_service.get_players.return_value = []
    monkeypatch.setattr("app.player.endpoints.PlayerService", lambda db: mock_service)

    response = client.get("/players")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []

def test_get_players_non_empty(monkeypatch, sample_player):
    mock_service = MagicMock()
    mock_service.get_players.return_value = [sample_player]
    monkeypatch.setattr("app.player.endpoints.PlayerService", lambda db: mock_service)

    response = client.get("/players")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == str(sample_player.id)
    assert data[0]["name"] == sample_player.name
    assert data[0]["birthday"] == sample_player.birthday.isoformat()
    assert data[0]["social_disgrace"] is sample_player.social_disgrace

def test_get_player_by_id_found(monkeypatch, sample_player):
    mock_service = MagicMock()
    mock_service.get_player_by_id.return_value = sample_player
    monkeypatch.setattr("app.player.endpoints.PlayerService", lambda db: mock_service)

    response = client.get(f"/players/{sample_player.id}")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == str(sample_player.id)
    assert data["name"] == sample_player.name
    assert data["birthday"] == sample_player.birthday.isoformat()
    assert data["social_disgrace"] is sample_player.social_disgrace

def test_get_player_by_id_not_found(monkeypatch):
    mock_service = MagicMock()
    mock_service.get_player_by_id.return_value = None
    monkeypatch.setattr("app.player.endpoints.PlayerService", lambda db: mock_service)

    response = client.get(f"/players/{uuid.uuid4()}")

    assert response.status_code == status.HTTP_404_NOT_FOUND

def test_create_player_success(monkeypatch):
    mock_service = MagicMock()
    new_player_id = uuid.uuid4()
    mock_service.create_player.return_value = MagicMock("id", id=new_player_id)
    monkeypatch.setattr("app.player.endpoints.PlayerService", lambda db: mock_service)

    player_data = {
        "name": "new_player",
        "birthday": "1990-01-01"
    }
    response = client.post("/players/", json=player_data)

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["id"] == str(new_player_id)

def test_create_player_invalid_data(monkeypatch):
    mock_service = MagicMock()
    mock_service.create_player.side_effect = Exception("Invalid data")
    monkeypatch.setattr("app.player.endpoints.PlayerService", lambda db: mock_service)

    player_data = {
        "name": "",  # Invalid name (too short)
        "birthday": "1990-01-01"
    }
    response = client.post("/players/", json=player_data)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

def test_create_player_missing_field(monkeypatch):
    mock_service = MagicMock()
    monkeypatch.setattr("app.player.endpoints.PlayerService", lambda db: mock_service)

    player_data = {
        "name": "new_player"
        # Missing birthday
    }
    response = client.post("/players/", json=player_data)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

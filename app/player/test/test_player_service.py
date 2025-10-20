import pytest
import uuid
import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pydantic import ValidationError
from app.player.models import Base, Player
from app.player.dtos import PlayerInDTO, PlayerOutDTO
from app.player.service import PlayerService
from app.secret.models import Secrets


@pytest.fixture(scope="function")
def db_session():
    """Crea una DB en memoria y devuelve una sesión fresca por test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    TestingSessionLocal = sessionmaker(bind=engine)

    # crear todas las tablas
    Base.metadata.create_all(engine, tables=[Player.__table__, Secrets.__table__])

    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()

@pytest.fixture
def player_service(db_session):
    return PlayerService(db_session)


def test_create_player(player_service, db_session):
    new = PlayerInDTO(name="Juan", birthday=datetime.date(1990, 1, 1))
    player = player_service.create_player(new)

    # Chequear que el objeto retornado es correcto
    assert player.id is not None
    assert isinstance(player.id, uuid.UUID)
    assert player.name == "Juan"
    assert player.birthday == datetime.date(1990, 1, 1)

    # Chequear que está en la base
    db_player = db_session.query(Player).first()
    assert db_player is not None
    assert db_player.id == player.id
    assert db_player.name == "Juan"
    assert db_player.birthday == datetime.date(1990, 1, 1)


def test_get_players(player_service, db_session):
    new1 = PlayerInDTO(name="Ana", birthday=datetime.date(1995, 5, 5))
    new2 = PlayerInDTO(name="Luis", birthday=datetime.date(1988, 12, 12))

    p1 = player_service.create_player(new1)
    p2 = player_service.create_player(new2)

    players = player_service.get_players()
    assert len(players) == 2
    ids = {p.id for p in players}
    assert {p1.id, p2.id} == ids


def test_get_players_empty(player_service):
    players = player_service.get_players()
    assert players == []


def test_get_player_by_id(player_service):
    new = PlayerInDTO(name="Carlos", birthday=datetime.date(1977, 11, 11))
    player = player_service.create_player(new)

    fetched = player_service.get_player_by_id(player.id)
    assert fetched is not None
    assert fetched.id == player.id
    assert fetched.name == "Carlos"
    assert fetched.birthday == datetime.date(1977, 11, 11)


def test_get_player_by_id_not_found(player_service):
    fetched = player_service.get_player_by_id(uuid.uuid4())  # UUID inexistente
    assert fetched is None

def test_get_player_entity_by_id(player_service):
    new = PlayerInDTO(name="Laura", birthday=datetime.date(1992, 3, 3))
    player = player_service.create_player(new)

    entity = player_service.get_player_entity_by_id(player.id)
    assert entity is not None
    assert entity.id == player.id
    assert entity.name == "Laura"
    assert entity.birthday == datetime.date(1992, 3, 3)

def test_assign_game_to_player(player_service):
    new = PlayerInDTO(name="Marta", birthday=datetime.date(2001, 2, 2))
    player = player_service.create_player(new)
    game_id = uuid.uuid4()

    updated = player_service.assign_game_to_player(player.id, game_id)
    assert updated.id == player.id
    assert updated.name == "Marta"
    assert updated.birthday == datetime.date(2001, 2, 2)
    assert updated.game_id == game_id

def test_delete_player(player_service, db_session):
    new = PlayerInDTO(name="Pedro", birthday=datetime.date(2000, 1, 1))
    player = player_service.create_player(new)

    deleted_id = player_service.delete_player(player.id)
    assert deleted_id == player.id

    # comprobar que ya no está en la base
    remaining = db_session.query(Player).filter(Player.id == player.id).first()
    assert remaining is None


def test_delete_player_not_found(player_service):
    with pytest.raises(ValueError) as exc:
        player_service.delete_player(uuid.uuid4())  # UUID inexistente
    assert "Player not found" in str(exc.value)
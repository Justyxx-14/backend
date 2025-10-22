import pytest
import uuid
import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pydantic import ValidationError
from app.card import models as card_models  # noqa: F401
from app.player.models import Base, Player
from app.player.dtos import PlayerInDTO, PlayerOutDTO
from app.player.service import PlayerService
from app.secret.models import Secrets
from app.secret.enums import SecretType
from app.game.models import Game


@pytest.fixture(scope="function")
def db_session():
    """Crea una DB en memoria y devuelve una sesión fresca por test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    TestingSessionLocal = sessionmaker(bind=engine)

    # crear todas las tablas
    Base.metadata.create_all(engine, tables=[Player.__table__, Secrets.__table__, Game.__table__])

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
    assert player.social_disgrace is False

    # Chequear que está en la base
    db_player = db_session.query(Player).first()
    assert db_player is not None
    assert db_player.id == player.id
    assert db_player.name == "Juan"
    assert db_player.birthday == datetime.date(1990, 1, 1)
    assert db_player.social_disgrace is False


def test_get_players(player_service, db_session):
    new1 = PlayerInDTO(name="Ana", birthday=datetime.date(1995, 5, 5))
    new2 = PlayerInDTO(name="Luis", birthday=datetime.date(1988, 12, 12))

    p1 = player_service.create_player(new1)
    p2 = player_service.create_player(new2)

    players = player_service.get_players()
    assert len(players) == 2
    ids = {p.id for p in players}
    assert {p1.id, p2.id} == ids
    assert all(p.social_disgrace is False for p in players)


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
    assert fetched.social_disgrace is False


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
    assert entity.social_disgrace is False

def test_assign_game_to_player(player_service):
    new = PlayerInDTO(name="Marta", birthday=datetime.date(2001, 2, 2))
    player = player_service.create_player(new)
    game_id = uuid.uuid4()

    updated = player_service.assign_game_to_player(player.id, game_id)
    assert updated.id == player.id
    assert updated.name == "Marta"
    assert updated.birthday == datetime.date(2001, 2, 2)
    assert updated.game_id == game_id
    assert updated.social_disgrace is False

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


def test_update_social_disgrace_sets_true_when_all_non_murderer_revealed(player_service, db_session):
    player = player_service.create_player(PlayerInDTO(name="Revelado", birthday=datetime.date(1999, 9, 9)))

    secret = Secrets(
        id=uuid.uuid4(),
        name="Chisme",
        game_id=uuid.uuid4(),
        role=SecretType.COMMON,
        description="Todos lo saben",
        owner_player_id=player.id,
        revealed=False,
    )
    db_session.add(secret)
    db_session.commit()

    PlayerService.update_social_disgrace(db_session, player.id)
    db_session.flush()
    player_entity = player_service.get_player_entity_by_id(player.id)
    db_session.refresh(player_entity)
    assert player_entity.social_disgrace is False

    secret.revealed = True
    db_session.flush()
    PlayerService.update_social_disgrace(db_session, player.id)
    db_session.flush()
    refreshed_player = player_service.get_player_entity_by_id(player.id)
    db_session.refresh(refreshed_player)
    assert refreshed_player.social_disgrace is True


def test_update_social_disgrace_stays_false_with_pending_secrets(player_service, db_session):
    player = player_service.create_player(PlayerInDTO(name="Pendiente", birthday=datetime.date(1998, 8, 8)))

    secret_hidden = Secrets(
        id=uuid.uuid4(),
        name="Oculto",
        game_id=uuid.uuid4(),
        role=SecretType.COMMON,
        description="No revelado",
        owner_player_id=player.id,
        revealed=False,
    )
    secret_revealed = Secrets(
        id=uuid.uuid4(),
        name="Revelado",
        game_id=uuid.uuid4(),
        role=SecretType.COMMON,
        description="Ya revelado",
        owner_player_id=player.id,
        revealed=True,
    )
    db_session.add_all([secret_hidden, secret_revealed])
    db_session.commit()

    PlayerService.update_social_disgrace(db_session, player.id)
    db_session.flush()
    refreshed_player = player_service.get_player_entity_by_id(player.id)
    db_session.refresh(refreshed_player)
    assert refreshed_player.social_disgrace is False


def test_update_social_disgrace_true_when_player_is_murderer(player_service, db_session):
    player = player_service.create_player(PlayerInDTO(name="Asesino", birthday=datetime.date(1997, 7, 7)))

    secret_murderer = Secrets(
        id=uuid.uuid4(),
        name="Murderer",
        game_id=uuid.uuid4(),
        role=SecretType.MURDERER,
        description="Es el asesino",
        owner_player_id=player.id,
        revealed=True,
    )
    secret_common = Secrets(
        id=uuid.uuid4(),
        name="Dato",
        game_id=uuid.uuid4(),
        role=SecretType.COMMON,
        description="Dato comun",
        owner_player_id=player.id,
        revealed=True,
    )
    db_session.add_all([secret_murderer, secret_common])
    db_session.commit()

    PlayerService.update_social_disgrace(db_session, player.id)
    db_session.flush()
    refreshed_player = player_service.get_player_entity_by_id(player.id)
    db_session.refresh(refreshed_player)
    assert refreshed_player.social_disgrace is True


def test_update_social_disgrace_transitions_with_multiple_secrets(player_service, db_session):
    player = player_service.create_player(PlayerInDTO(name="Multi", birthday=datetime.date(1996, 6, 6)))

    secrets = [
        Secrets(
            id=uuid.uuid4(),
            name=f"Secreto {idx}",
            game_id=uuid.uuid4(),
            role=SecretType.COMMON,
            description=f"Descripcion {idx}",
            owner_player_id=player.id,
            revealed=idx < 3,  # deja el último oculto inicialmente
        )
        for idx in range(4)
    ]
    db_session.add_all(secrets)
    db_session.commit()

    PlayerService.update_social_disgrace(db_session, player.id)
    db_session.flush()
    refreshed_player = player_service.get_player_entity_by_id(player.id)
    db_session.refresh(refreshed_player)
    assert refreshed_player.social_disgrace is False

    secrets[-1].revealed = True
    db_session.flush()
    PlayerService.update_social_disgrace(db_session, player.id)
    db_session.flush()
    db_session.refresh(refreshed_player)
    assert refreshed_player.social_disgrace is True

    secrets[0].revealed = False
    db_session.flush()
    PlayerService.update_social_disgrace(db_session, player.id)
    db_session.flush()
    db_session.refresh(refreshed_player)
    assert refreshed_player.social_disgrace is False


def test_update_social_disgrace_all_hidden_then_reveal(player_service, db_session):
    player = player_service.create_player(PlayerInDTO(name="Triple", birthday=datetime.date(1995, 5, 5)))

    secrets = [
        Secrets(
            id=uuid.uuid4(),
            name=f"Secreto oculto {idx}",
            game_id=uuid.uuid4(),
            role=SecretType.COMMON,
            description=f"Oculto {idx}",
            owner_player_id=player.id,
            revealed=False,
        )
        for idx in range(3)
    ]
    db_session.add_all(secrets)
    db_session.commit()

    PlayerService.update_social_disgrace(db_session, player.id)
    db_session.flush()
    state = player_service.get_player_entity_by_id(player.id)
    db_session.refresh(state)
    assert state.social_disgrace is False

    for idx, secret in enumerate(secrets):
        secret.revealed = True
        db_session.flush()
        PlayerService.update_social_disgrace(db_session, player.id)
        db_session.flush()
        state = player_service.get_player_entity_by_id(player.id)
        db_session.refresh(state)
        if idx < len(secrets) - 1:
            assert state.social_disgrace is False
        else:
            assert state.social_disgrace is True

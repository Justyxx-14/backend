import pytest
import uuid
import json
from unittest.mock import mock_open, MagicMock
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.secret.dtos import SecretOutDTO
from app.secret.service import SecretService
from app.secret.models import Secrets
from app.secret.enums import SecretType
from app.player.models import Player
import app.card.models  # noqa: F401
from app.player.service import PlayerService
from app.game.models import Game


# ---------------------------
# FIXTURES
# ---------------------------

@pytest.fixture(scope="function")
def db_session():
    """Crea una BD SQLite en memoria (sin tocar la BD real)."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def sample_game_and_players(db_session):
    """Crea un juego con jugadores de prueba (mínimo 5 para cubrir ACCOMPLICE)."""
    game_id = uuid.uuid4()
    players = [
        Player(id=uuid.uuid4(), name=f"Jugador{i}", birthday=date(2000, 1, i+1))
        for i in range(5)
    ]
    for p in players:
        db_session.add(p)
    db_session.flush()

    game = Game(
        id=game_id,
        name="Partida Test",
        host_id=players[0].id,
        min_players=2,
        max_players=6,
    )
    db_session.add(game)
    db_session.commit()
    return game, players


# ---------------------------
# TESTS: CREACIÓN DE SECRETOS
# ---------------------------

def test_create_secrets_creates_records(db_session, sample_game_and_players):
    """Verifica que create_secrets inserte correctamente secretos en la BD."""
    game, players = sample_game_and_players

    secrets_dto = SecretService.create_secrets(
        db_session, game.id, [p.id for p in players]
    )

    db_secrets = db_session.query(Secrets).filter_by(game_id=game.id).all()

    assert len(db_secrets) == len(secrets_dto)
    assert any(s.role == SecretType.MURDERER for s in db_secrets)
    assert any(s.role == SecretType.ACCOMPLICE for s in db_secrets)
    assert all(s.game_id == game.id for s in db_secrets)
    assert all(s.description for s in db_secrets)


def test_create_secrets_invalid_role_rejected(db_session):
    """Debe lanzar ValueError si se asigna un rol no válido manualmente."""
    with pytest.raises(ValueError):
        s = Secrets(
            id=uuid.uuid4(),
            name="Invalid",
            game_id=uuid.uuid4(),
            role="INVALID_ROLE",
            description="No debería permitirse",
            revealed=False,
        )
        db_session.add(s)
        db_session.flush()

def test_create_secrets_raises_file_not_found(db_session, mocker):
    """Debe lanzar FileNotFoundError si el deck no existe"""
    mocker.patch('app.secret.service.open', side_effect=FileNotFoundError)
    with pytest.raises(FileNotFoundError):
        SecretService.create_secrets(db_session, uuid.uuid4(), [uuid.uuid4(), uuid.uuid4()])

def test_create_secrets_raises_value_error_for_insufficient_common(db_session, mocker):
    """Debe lanzar ValueError si no hay suficientes COMMON secrets en el deck"""
    # Solo 1 COMMON
    fake_json = json.dumps({
        "items": [
            {"type": "COMMON", "name": "C1"},
            {"type": "MURDERER", "name": "M"},
            {"type": "ACCOMPLICE", "name": "A"}
        ]
    })

    mocker.patch(
        'app.secret.service.open',
        mock_open(read_data=fake_json)
    )

    jugadores_ids = [uuid.uuid4() for _ in range(3)]  # necesitan 3*3 -1 = 8 COMMONs
    with pytest.raises(ValueError):
        SecretService.create_secrets(db_session, uuid.uuid4(), jugadores_ids)


# ---------------------------
# TESTS: REPARTO DE SECRETOS
# ---------------------------

def test_deal_secrets_assigns_correctly(db_session, sample_game_and_players):
    """
    Cada jugador debe recibir al menos un secreto,
    tener exactamente 3 al comienzo de la partida y los cambios deben persistir.
    """
    game, players = sample_game_and_players

    SecretService.create_secrets(db_session, game.id, [p.id for p in players])
    result = SecretService.deal_secrets(db_session, game.id, [p.id for p in players])

    assert isinstance(result, dict)
    assert set(result.keys()) == {p.id for p in players}

    for pid, secrets_dto in result.items():
        assert len(secrets_dto) == 3, f"El jugador {pid} debería tener 3 secretos, pero tiene {len(secrets_dto)}"
        assert all(isinstance(s, SecretOutDTO) for s in secrets_dto)
        for s in secrets_dto:
            assert s.owner_player_id == pid

    persisted = db_session.query(Secrets).filter(Secrets.owner_player_id.isnot(None)).all()
    assert len(persisted) == len(db_session.query(Secrets).filter_by(game_id=game.id).all())
    


def test_deal_secrets_persistence_and_distribution(db_session, sample_game_and_players):
    """Verifica que el reparto se distribuya entre jugadores y se guarde en DB."""
    game, players = sample_game_and_players

    SecretService.create_secrets(db_session, game.id, [p.id for p in players])
    SecretService.deal_secrets(db_session, game.id, [p.id for p in players])

    all_secrets = db_session.query(Secrets).filter_by(game_id=game.id).all()
    owners = [s.owner_player_id for s in all_secrets]
    assert all(oid in [p.id for p in players] for oid in owners)
    assert all(oid is not None for oid in owners)
    assert len(set(owners)) <= len(players)


# ---------------------------
# TESTS: CAMBIO DE ESTADO Y DUEÑO
# ---------------------------

def test_change_secret_status_toggles_revealed_flag(db_session, sample_game_and_players):
    """El método debe invertir el flag 'revealed' y persistir el cambio."""
    game, players = sample_game_and_players

    SecretService.create_secrets(db_session, game.id, [p.id for p in players])
    secret_dto = SecretService.get_secrets_by_game_id(db_session, game.id)[0]

    initial_state = secret_dto.revealed
    SecretService.change_secret_status(db_session, secret_dto.id)
    updated_dto = SecretService.get_secret_by_id(db_session, secret_dto.id)

    assert updated_dto is not None
    assert updated_dto.revealed is not initial_state


def test_change_secret_status_updates_social_disgrace(db_session):
    player = Player(id=uuid.uuid4(), name="Jugador", birthday=date(1990, 1, 1))
    db_session.add(player)
    db_session.commit()

    secret = Secrets(
        id=uuid.uuid4(),
        name="Chisme",
        game_id=uuid.uuid4(),
        role=SecretType.COMMON,
        description="KETI",
        owner_player_id=player.id,
        revealed=False
    )
    db_session.add(secret)
    db_session.commit()

    dto = SecretService.change_secret_status(db_session, secret.id)
    player_refreshed = db_session.query(Player).filter_by(id=player.id).one()
    assert dto.revealed is True
    assert player_refreshed.social_disgrace is True

    dto_back = SecretService.change_secret_status(db_session, secret.id)
    player_refreshed = db_session.query(Player).filter_by(id=player.id).one()
    assert dto_back.revealed is False
    assert player_refreshed.social_disgrace is False


def test_move_secret_updates_owner(db_session, sample_game_and_players):
    """Debe poder cambiar el dueño de un secreto y persistir el cambio."""
    game, players = sample_game_and_players

    SecretService.create_secrets(db_session, game.id, [p.id for p in players])
    secret_dto = SecretService.get_secrets_by_game_id(db_session, game.id)[0]
    new_owner = players[1].id

    SecretService.move_secret(db_session, secret_dto.id, new_owner)
    updated_dto = SecretService.get_secret_by_id(db_session, secret_dto.id)

    assert updated_dto is not None
    assert updated_dto.owner_player_id == new_owner


def test_move_secret_updates_social_disgrace_for_owners(db_session):
    player_from = Player(id=uuid.uuid4(), name="Origen", birthday=date(1990, 1, 1))
    player_to = Player(id=uuid.uuid4(), name="Destino", birthday=date(1991, 2, 2))
    db_session.add_all([player_from, player_to])
    db_session.commit()

    secret = Secrets(
        id=uuid.uuid4(),
        name="Contraseña",
        game_id=uuid.uuid4(),
        role=SecretType.COMMON,
        description="MessiGOAT",
        owner_player_id=player_from.id,
        revealed=True,
    )
    db_session.add(secret)
    db_session.commit()

    PlayerService.update_social_disgrace(db_session, player_from.id)
    db_session.flush()
    db_session.refresh(player_from)
    assert player_from.social_disgrace is True

    SecretService.move_secret(db_session, secret.id, player_to.id)

    refreshed_from = db_session.query(Player).filter_by(id=player_from.id).one()
    refreshed_to = db_session.query(Player).filter_by(id=player_to.id).one()

    assert refreshed_from.social_disgrace is True
    assert refreshed_to.social_disgrace is True


def test_change_secret_status_error_if_not_found(db_session):
    """Debe lanzar error si se intenta cambiar un secreto inexistente."""
    with pytest.raises(ValueError, match="not found"):
        SecretService.change_secret_status(db_session, uuid.uuid4())


def test_move_secret_error_if_not_found(db_session):
    """Debe lanzar error si se intenta mover un secreto inexistente."""
    with pytest.raises(ValueError, match="not found"):
        SecretService.move_secret(db_session, uuid.uuid4(), uuid.uuid4())


# ---------------------------
# TESTS: GETTERS
# ---------------------------

def test_get_secret_by_id_returns_correct_secret(db_session, sample_game_and_players):
    """get_secret_by_id debe retornar el secreto correcto."""
    game, players = sample_game_and_players
    secrets_dto = SecretService.create_secrets(db_session, game.id, [p.id for p in players])
    target_dto = secrets_dto[0]

    fetched = SecretService.get_secret_by_id(db_session, target_dto.id)

    assert fetched is not None
    assert fetched.id == target_dto.id
    assert fetched.name == target_dto.name


def test_get_secrets_by_game_id_returns_all(db_session, sample_game_and_players):
    """get_secrets_by_game_id debe devolver todos los secretos del juego."""
    game, players = sample_game_and_players
    secrets_dto = SecretService.create_secrets(db_session, game.id, [p.id for p in players])

    fetched_dto = SecretService.get_secrets_by_game_id(db_session, game.id)
    assert len(fetched_dto) == len(secrets_dto)


def test_get_secrets_by_player_id_returns_assigned(db_session, sample_game_and_players):
    """get_secrets_by_player_id debe devolver solo los secretos del jugador indicado."""
    game, players = sample_game_and_players
    SecretService.create_secrets(db_session, game.id, [p.id for p in players])
    SecretService.deal_secrets(db_session, game.id, [p.id for p in players])

    pid = players[0].id
    player_secrets_dto = SecretService.get_secrets_by_player_id(db_session, pid)

    assert all(s.owner_player_id == pid for s in player_secrets_dto)
    assert all(isinstance(s, SecretOutDTO) for s in player_secrets_dto)


# ---------------------------
# TESTS EXTRA: CASOS LIMITE
# ---------------------------

def test_create_secrets_handles_minimal_players(db_session):
    """Debe poder crear secretos con la cantidad mínima de jugadores."""
    game_id = uuid.uuid4()
    jugadores = [uuid.uuid4() for _ in range(2)]

    secrets_dto = SecretService.create_secrets(db_session, game_id, jugadores)
    assert len(secrets_dto) > 0
    assert any(s.role == SecretType.MURDERER for s in secrets_dto)
    assert not any(s.role == SecretType.ACCOMPLICE for s in secrets_dto)


def test_deal_secrets_without_secrets_returns_empty(db_session, sample_game_and_players):
    """Si no hay secretos creados, el reparto no debe fallar."""
    game, players = sample_game_and_players

    result = SecretService.deal_secrets(db_session, game.id, [p.id for p in players])
    assert result in (None, {}, []), "Debe manejar correctamente la falta de secretos"


def test_deal_secrets_rerolls_on_invalid_hand(db_session, mocker):
    """
    Verifica que deal_secrets vuelve a barajar si la primera mano es inválida.
    """

    import random

    game_id = uuid.uuid4()
    player_ids = [uuid.uuid4() for _ in range(5)]

    murderer = Secrets(id=uuid.uuid4(), game_id=game_id, role=SecretType.MURDERER, name="Asesino", description="Asesino")
    accomplice = Secrets(id=uuid.uuid4(), game_id=game_id, role=SecretType.ACCOMPLICE, name="Cómplice", description="Cómplice")
    commons = [Secrets(id=uuid.uuid4(), game_id=game_id, role=SecretType.COMMON, name=f"Común_{i}", description=f"Común_{i}") for i in range(13)]
    
    db_session.add_all([murderer, accomplice] + commons)
    db_session.commit()

    # Guardamos la referencia al shuffle original para evitar un bucle infinito
    original_shuffle = random.shuffle

    # Usamos mocker para parchear una sola vez
    mock_shuffle = mocker.patch('app.secret.service.random.shuffle')
    
    def stack_the_deck(deck: list):
        # Forzar una mano inválida
        deck.sort(key=lambda s: 0 if s.role in (SecretType.MURDERER, SecretType.ACCOMPLICE) else 1)
        mock_shuffle.side_effect = original_shuffle
    
    mock_shuffle.side_effect = stack_the_deck

    SecretService.deal_secrets(db_session, game_id, player_ids)

    assert mock_shuffle.call_count > 1, "La función no reintentó la repartición ante una mano inválida."

def test_get_murderer_team_ids(db_session):
    game_id = uuid.uuid4()
    murderer_id = uuid.uuid4()
    accomplice_id = uuid.uuid4()

    mock_query_result = [
        (murderer_id,),
        (accomplice_id,),
        (None,)
    ]

    db_session.query = MagicMock()
    mock_query_obj = db_session.query.return_value

    mock_query_obj.filter.return_value = mock_query_obj
    mock_query_obj.all.return_value = mock_query_result

    result_ids = SecretService.get_murderer_team_ids(db_session, game_id)

    db_session.query.assert_called_once_with(Secrets.owner_player_id)
    mock_query_obj.filter.assert_called_once()
    mock_query_obj.all.assert_called_once()

    assert isinstance(result_ids, set)
    assert result_ids == {murderer_id, accomplice_id}

def test_get_murderer_team_ids_no_accomplice(db_session):
    game_id = uuid.uuid4()
    murderer_id = uuid.uuid4()
    
    mock_query_result = [(murderer_id,)]
    db_session.query = MagicMock()
    db_session.query.return_value.filter.return_value.all.return_value = mock_query_result

    result_ids = SecretService.get_murderer_team_ids(db_session, game_id)

    assert result_ids == {murderer_id}

def test_get_murderer_team_ids_no_team(db_session):
    game_id = uuid.uuid4()
    
    mock_query_result = []
    db_session.query = MagicMock()
    db_session.query.return_value.filter.return_value.all.return_value = mock_query_result

    result_ids = SecretService.get_murderer_team_ids(db_session, game_id)

    assert result_ids == set()

import pytest
import uuid
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, mock_open, MagicMock, patch, ANY
from fastapi import HTTPException

from app.db import Base
from app.card.models import Card
from app.card.enums import CardType
from app.card.schemas import CardIn, CardBatchIn
from app.card.service import CardService
from app.player.models import Player
from app.game.enums import GameEndReason, WinningTeam, TurnState
from app.game.models import Game
from app.game.models import GameTurnState
from app.game.service import GameService
from app.game.schemas import GameIn, EndGameResult, CurrentTurnResponse, GameTurnStateOut
from app.game.dtos import GameInDTO
from app.player.dtos import PlayerInDTO
from app.secret.models import Secrets
from app.secret.enums import SecretType
from app.secret.service import SecretService


# Configuración de la base de datos de prueba en memoria
@pytest.fixture(scope="function")
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Game.__table__.create(engine)
    Player.__table__.create(engine)
    Card.__table__.create(engine)
    Secrets.__table__.create(engine)
    GameTurnState.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Game.__table__.drop(engine)
    Player.__table__.drop(engine)
    Card.__table__.drop(engine)
    Secrets.__table__.drop(engine)
    GameTurnState.__table__.drop(engine)

@pytest.fixture(scope="function")
def game_service(db_session):
    return GameService(db_session)

# --- Crear juegos ---
@pytest.mark.parametrize("min_p,max_p", [(2,2), (2,6), (6,6)])
def test_create_game_edge_limits(game_service, min_p, max_p):
    dto = GameInDTO(name="Edge Game", host_name=str(uuid.uuid4), birthday=date(2000,1,1),
                     min_players=min_p, max_players=max_p)
    game = game_service.create_game(dto)
    assert game.min_players == min_p
    assert game.max_players == max_p
    assert game.players_ids[0] == game.host_id

def test_create_game_invalid_max_less_than_min(game_service):
    dto = GameInDTO(name="Invalid Game", host_name=str(uuid.uuid4), birthday=date(2000,1,1),
                     min_players=4, max_players=2)
    with pytest.raises(ValueError):
        game_service.create_game(dto)

def test_create_game_invalid_min_max_out_of_bounds(game_service):
    # min_players < 2
    with pytest.raises(ValueError):
        game_in = GameIn(
            name="Invalid Min",
            host=uuid.uuid4(),
            birthday=date(2000,1,1),
            min_players=1,  # inválido
            max_players=3
        )
        game_service.create_game(game_in.to_dto())

    # max_players > 6
    with pytest.raises(ValueError):
        game_in = GameIn(
            name="Invalid Max",
            host=uuid.uuid4(),
            birthday=date(2000,1,1),
            min_players=2,
            max_players=7  # inválido
        )
        game_service.create_game(game_in.to_dto())

def test_create_game_without_pass(game_service):
    "Prueba crear un juego sin contraseña"
    game_in = GameInDTO(
            name="Summoner's rift",
            host_name=str(uuid.uuid4()),
            birthday=date(2000,1,1),
            min_players=2,
            max_players=3
    )
    new_game=game_service.create_game(game_in)
    assert not new_game.password

def test_create_game_with_pass(game_service):
    "Prueba crear un juego con contraseña"
    game_in = GameInDTO(
            name="Summoner's rift",
            host_name=str(uuid.uuid4()),
            password = "Diego&ChunSonLo+",
            birthday=date(2000,1,1),
            min_players=2,
            max_players=3
    )
    new_game=game_service.create_game(game_in)
    assert new_game.password
    assert new_game.password == "Diego&ChunSonLo+"

# --- Obtener juegos ---
def test_get_games_multiple(game_service):
    dto1 = GameInDTO(name="Game 1", host_name=str(uuid.uuid4), birthday=date(2000,1,1), min_players=2, max_players=4)
    dto2 = GameInDTO(name="Game 2", host_name=str(uuid.uuid4), birthday=date(2000,1,1), min_players=2, max_players=3)
    game_service.create_game(dto1)
    game_service.create_game(dto2)
    games = game_service.get_games()
    assert len(games) == 2
    assert set(g.name for g in games) == {"Game 1", "Game 2"}

def test_get_game_by_id(game_service):
    dto = GameInDTO(name="Game Exist", host_name=str(uuid.uuid4), birthday=date(2000,1,1), min_players=2, max_players=4)
    game = game_service.create_game(dto)
    fetched = game_service.get_game_by_id(game.id)
    assert fetched.id == game.id
    assert fetched.name == "Game Exist"

def test_get_game_by_id_nonexistent(game_service):
    fetched = game_service.get_game_by_id(uuid.uuid4())
    assert fetched is None


# --- Agregar jugadores ---
def test_add_player_success(game_service):
    dto = GameInDTO(name="Add Player", host_name=str(uuid.uuid4), birthday=date(2000,1,1), min_players=2, max_players=3)
    game = game_service.create_game(dto)
    player = PlayerInDTO(name="Extra", birthday=date(2005,5,5))
    pid = game_service.add_player(game.id, player)
    assert pid is not None
    updated_game = game_service.get_game_by_id(game.id)
    assert len(updated_game.players_ids) == 2

def test_add_player_max_reached(game_service):
    dto = GameInDTO(name="Full Game", host_name=str(uuid.uuid4), birthday=date(2000,1,1), min_players=2, max_players=2)
    game = game_service.create_game(dto)
    game_service.add_player(game.id, PlayerInDTO(name="Player2", birthday=date(2001,1,1)))
    result = game_service.add_player(game.id, PlayerInDTO(name="Player3", birthday=date(2002,2,2)))
    assert result is None

deck_path = Path("app/card/deck.json")
with deck_path.open("r", encoding="utf-8") as f:
    deck_json = json.load(f)

def test_add_player_after_game_started(game_service):
    dto = GameInDTO(name="Ready Game", host_name=str(uuid.uuid4), birthday=date(2000,1,1), min_players=2, max_players=3)
    game = game_service.create_game(dto)
    game_service.add_player(game.id, PlayerInDTO(name="Second Player", birthday=date(2001,1,1)))
    
    # Pasamos el deck_json al iniciar la partida
    game_service.start_game(game.id, deck_json=deck_json)
    game = game_service.db.query(Game).filter(Game.id == game.id).first()
    result = game_service.add_player(game.id, PlayerInDTO(name="Late Player", birthday=date(2003,3,3)))
    assert result is None


# --- Lógica de inicio de juego ---
def test_can_start_logic(game_service):
    dto = GameInDTO(name="Start Logic", host_name=str(uuid.uuid4), birthday=date(2000,1,1), min_players=2, max_players=4)
    game = game_service.create_game(dto)
    assert not game_service.can_start(game.id)  # solo host
    game_service.add_player(game.id, PlayerInDTO(name="Player2", birthday=date(2002,2,2)))
    assert game_service.can_start(game.id)

def test_start_game_success(game_service):
    dto = GameInDTO(name="Start Game", host_name=str(uuid.uuid4), birthday=date(2000,1,1), min_players=2, max_players=2)
    game = game_service.create_game(dto)
    game_service.add_player(game.id, PlayerInDTO(name="Second Player", birthday=date(2001,1,1)))
    
    # Pasamos deck_json
    result = game_service.start_game(game.id, deck_json=deck_json)
    
    assert result
    updated_game = game_service.get_game_by_id(game.id)
    assert updated_game.ready

def test_start_game_fail_min_players(game_service):
    dto = GameInDTO(name="Start Fail", host_name=str(uuid.uuid4), birthday=date(2000,1,1), min_players=2, max_players=3)
    game = game_service.create_game(dto)
    result = game_service.start_game(game.id)
    assert not result


# --- Host siempre incluido ---
def test_host_always_in_players(game_service):
    dto = GameInDTO(name="Host Check", host_name=str(uuid.uuid4), birthday=date(2000,1,1), min_players=2, max_players=3)
    game = game_service.create_game(dto)
    assert game.players_ids[0] == game.host_id
    # Agrego jugadores extra
    game_service.add_player(game.id, PlayerInDTO(name="P2", birthday=date(2002,2,2)))
    game_service.add_player(game.id, PlayerInDTO(name="P3", birthday=date(2003,3,3)))
    updated_game = game_service.get_game_by_id(game.id)
    assert updated_game.players_ids[0] == game.host_id

# --- Tests para next_player y end_game (corregidos) ---
def test_next_player_normal_flow(game_service, db_session):
    # Crear juego y dos jugadores
    dto = GameInDTO(name="Turn Test", host_name=str(uuid.uuid4),
                    birthday=date(2000,1,1), min_players=2, max_players=3)
    game_dto = game_service.create_game(dto)
    p2_id = game_service.add_player(game_dto.id, PlayerInDTO(name="Player2", birthday=date(2001,1,1)))

    # Obtener objeto ORM
    game = db_session.query(Game).filter(Game.id == game_dto.id).first()

    # Crear cartas en mazo para que next_player pueda avanzar
    CardService.create_card(db_session, game.id,
                            CardIn(type=CardType.EVENT, name="A1", description="desc"))

    # Setear turno actual al host
    host_player = db_session.query(Player).filter(Player.id == game.host_id).first()
    game.current_turn = host_player.id
    db_session.commit()

    next_pid = game_service.next_player(game.id)
    assert next_pid == p2_id  # turno pasa al siguiente jugador

    next_pid2 = game_service.next_player(game.id)
    assert next_pid2 == host_player.id  # vuelve al host


def test_are_all_other_secrets_revealed_returns_true(game_service, db_session):
    """
    Prueba que la función devuelve True cuando todos los secretos no-asesinos están revelados.
    """
    dto = GameInDTO(name="Test Reveal True", host_name="Host", birthday=date(2000, 1, 1), min_players=4, max_players=4)
    game_dto = game_service.create_game(dto)
    for i in range(2, 5):
        game_service.add_player(game_dto.id, PlayerInDTO(name=f"Player{i}", birthday=date(2000, 1, i)))

    game_orm = db_session.query(Game).filter(Game.id == game_dto.id).first()
    player_ids = [p.id for p in game_orm.players]
    SecretService.create_secrets(db_session, game_orm.id, player_ids)
    SecretService.deal_secrets(db_session, game_orm.id, player_ids)

    secrets = db_session.query(Secrets).filter(Secrets.game_id == game_orm.id).all()

    # Revelamos todos los secretos que no son del asesino
    for secret in secrets:
        if secret.role != SecretType.MURDERER:
            secret.revealed = True
    db_session.commit()

    # La función debe devolver True
    assert game_service.are_all_other_secrets_revealed(game_orm.id) is True

def test_are_all_other_secrets_revealed_returns_false(game_service, db_session):
    """
    Prueba que la función devuelve False si al menos un secreto no-asesino no está revelado.
    """
    dto = GameInDTO(name="Test Reveal False", host_name="Host", birthday=date(2000, 1, 1), min_players=4, max_players=4)
    game_dto = game_service.create_game(dto)
    for i in range(2, 5):
        game_service.add_player(game_dto.id, PlayerInDTO(name=f"Player{i}", birthday=date(2000, 1, i)))

    game_orm = db_session.query(Game).filter(Game.id == game_dto.id).first()
    player_ids = [p.id for p in game_orm.players]
    SecretService.create_secrets(db_session, game_orm.id, player_ids)
    SecretService.deal_secrets(db_session, game_orm.id, player_ids)

    # La función debe devolver True
    assert game_service.are_all_other_secrets_revealed(game_orm.id) is False

def test_end_game_murderers_win(game_service, db_session):
    """
    Prueba el fin de juego cuando ganan los asesinos.
    """
    dto = GameInDTO(name="Murderers Win Test", host_name="Host", birthday=date(2000, 1, 1), min_players=5, max_players=6)
    game_dto = game_service.create_game(dto)
    for i in range(2, 6):
        game_service.add_player(game_dto.id, PlayerInDTO(name=f"Player{i}", birthday=date(2000, 1, i)))

    game_orm = db_session.query(Game).filter(Game.id == game_dto.id).first()
    player_ids = [p.id for p in game_orm.players]
    SecretService.create_secrets(db_session, game_orm.id, player_ids)
    SecretService.deal_secrets(db_session, game_orm.id, player_ids)

    db_session.refresh(game_orm)
    players = game_orm.players
    secrets = db_session.query(Secrets).filter(Secrets.game_id == game_orm.id).all()
    
    murderer_id = next(s.owner_player_id for s in secrets if s.role == SecretType.MURDERER)
    accomplice_id = next(s.owner_player_id for s in secrets if s.role == SecretType.ACCOMPLICE)
    
    result = game_service.end_game(game_orm.id, reason=GameEndReason.DECK_EMPTY)
    
    assert result.winning_team == WinningTeam.MURDERERS
    assert len(result.winners) >= 1
    assert murderer_id in [w.id for w in result.winners]
    assert accomplice_id in [w.id for w in result.winners]
    assert len(result.player_roles) == len(players)

def test_end_game_detectives_win(game_service, db_session):
    """
    Prueba el fin de juego cuando ganan los detectives.
    """
    dto = GameInDTO(name="Detectives Win Test", host_name="Host", birthday=date(2000, 1, 1), min_players=5, max_players=6)
    game_dto = game_service.create_game(dto)
    for i in range(2, 6):
        game_service.add_player(game_dto.id, PlayerInDTO(name=f"Player{i}", birthday=date(2000, 1, i)))

    game_orm = db_session.query(Game).filter(Game.id == game_dto.id).first()
    player_ids = [p.id for p in game_orm.players]
    SecretService.create_secrets(db_session, game_orm.id, player_ids)
    SecretService.deal_secrets(db_session, game_orm.id, player_ids)

    db_session.refresh(game_orm)
    players = game_orm.players
    secrets = db_session.query(Secrets).filter(Secrets.game_id == game_orm.id).all()
    
    murderer_id = next(s.owner_player_id for s in secrets if s.role == SecretType.MURDERER)
    accomplice_id = next(s.owner_player_id for s in secrets if s.role == SecretType.ACCOMPLICE)
    
    result = game_service.end_game(game_orm.id, reason=GameEndReason.MURDERER_REVEALED)
    
    assert result.winning_team == WinningTeam.DETECTIVES
    assert len(result.winners) >= 1
    assert murderer_id not in [w.id for w in result.winners]
    assert accomplice_id not in [w.id for w in result.winners]
    assert len(result.player_roles) == len(players)

# --- Tests para get_turn_state() ---
def test_get_turn_state_success(game_service, db_session):
    """Debe devolver el estado y target_player_id correctamente."""
    game = Game(name="TurnStateTest", host_id=uuid.uuid4(), min_players=2, max_players=4)
    db_session.add(game)
    db_session.commit()

    state = GameTurnState(
        game_id=game.id,
        state=TurnState.DRAWING_CARDS,
        target_player_id=uuid.uuid4()
    )
    db_session.add(state)
    db_session.commit()

    result = game_service.get_turn_state(game.id)

    assert isinstance(result, GameTurnStateOut)
    assert result.turn_state == TurnState.DRAWING_CARDS
    assert result.target_player_id == state.target_player_id


def test_get_turn_state_not_exists_raises(game_service, db_session):
    """Debe lanzar ValueError si no existe estado de turno para el juego."""
    game = Game(name="TurnStateMissing", host_id=uuid.uuid4(), min_players=2, max_players=4)
    db_session.add(game)
    db_session.commit()

    with pytest.raises(ValueError, match="No existe estado de turno"):
        game_service.get_turn_state(game.id)


# --- Tests para change_turn_state() ---
def test_change_turn_state_success_normal(game_service, db_session):
    """Debe cambiar correctamente el estado de turno sin target_player_id."""
    game = Game(name="ChangeTurn", host_id=uuid.uuid4(), min_players=2, max_players=4)
    db_session.add(game)
    db_session.commit()

    turn_state = GameTurnState(game_id=game.id, state=TurnState.IDLE)
    db_session.add(turn_state)
    db_session.commit()

    game.turn_state = turn_state
    db_session.commit()

    game_service.change_turn_state(game.id, TurnState.DISCARDING)

    updated = db_session.query(GameTurnState).filter_by(game_id=game.id).first()
    assert updated.state == TurnState.DISCARDING
    assert updated.target_player_id is None


def test_change_turn_state_to_choosing_secret_sets_target(game_service, db_session):
    """Debe setear target_player_id cuando el estado es CHOOSING_SECRET."""
    game = Game(name="ChangeTurnSecret", host_id=uuid.uuid4(), min_players=2, max_players=4)
    db_session.add(game)
    db_session.commit()

    turn_state = GameTurnState(game_id=game.id, state=TurnState.IDLE)
    db_session.add(turn_state)
    db_session.commit()

    game.turn_state = turn_state
    db_session.commit()

    target_id = uuid.uuid4()
    game_service.change_turn_state(game.id, TurnState.CHOOSING_SECRET, target_id)

    updated = db_session.query(GameTurnState).filter_by(game_id=game.id).first()
    assert updated.state == TurnState.CHOOSING_SECRET
    assert updated.target_player_id == target_id


def test_change_turn_state_missing_game_raises(game_service):
    """Debe lanzar ValueError si el juego no existe."""
    fake_game = uuid.uuid4()
    with pytest.raises(ValueError, match = "Juego no encontrado"):
        game_service.change_turn_state(fake_game, TurnState.DRAWING_CARDS)


def test_change_turn_state_missing_turn_state_raises(game_service, db_session):
    """Debe lanzar ValueError si el juego no tiene objeto turn_state."""
    game = Game(name="NoTurnState", host_id=uuid.uuid4(), min_players=2, max_players=4)
    db_session.add(game)
    db_session.commit()

    with pytest.raises(ValueError):
        game_service.change_turn_state(game.id, TurnState.DRAWING_CARDS)


def test_change_turn_state_choosing_secret_without_target_raises(game_service, db_session):
    """Debe lanzar ValueError si CHOOSING_SECRET no tiene target_player_id."""
    game = Game(name="NoTargetSecret", host_id=uuid.uuid4(), min_players=2, max_players=4)
    db_session.add(game)
    db_session.commit()

    turn_state = GameTurnState(game_id=game.id, state=TurnState.IDLE)
    db_session.add(turn_state)
    db_session.commit()

    game.turn_state = turn_state
    db_session.commit()

    with pytest.raises(ValueError):
        game_service.change_turn_state(game.id, TurnState.CHOOSING_SECRET)

@pytest.mark.asyncio
async def test_handler_end_timer_normal_state_triggers_expected_calls(db_session):
    """Debe ejecutar el flujo normal: handle_end_timer_normal_state, change_turn_state, next_player y broadcast."""
    from app.game.service import GameService

    # --- Setup ---
    game_service = GameService(db_session)
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()

    # Estado inicial simulado del turno
    state = MagicMock()
    state.state = TurnState.END_TURN

    # Crear objeto Game real en la base de prueba
    db_session.add(
        Game(
            id=game_id,
            name="TimerTest",
            host_id=player_id,
            min_players=2,
            max_players=4,
            current_turn=player_id
        )
    )
    db_session.commit()

    # Mockear métodos internos del servicio
    game_service.get_turn_state = MagicMock(
        return_value=GameTurnStateOut(turn_state=TurnState.DRAWING_CARDS, target_player_id=None)
    )
    fake_next_player = uuid.uuid4()
    game_service.handle_end_timer_normal_state = MagicMock()
    game_service.change_turn_state = MagicMock()
    game_service.next_player = MagicMock(return_value=fake_next_player)

    # Crear un objeto Game simulado para que devuelva la query
    mock_game = MagicMock()
    mock_game.turn_state.state = TurnState.IDLE
    mock_game.current_turn=player_id

    fake_manager = AsyncMock()

    # --- Act ---
    # Parchear la query para que devuelva el mock_game
    with (
        patch.object(game_service.db, "query") as mock_query,
        patch("app.game.service.manager", fake_manager)
    ):
        mock_query.return_value.filter.return_value.first.return_value = mock_game
        await game_service.handler_end_timer(game_id)

    # --- Assert ---
    game_service.handle_end_timer_normal_state.assert_called_once()
    game_service.change_turn_state.assert_called_once_with(game_id, TurnState.END_TURN)
    game_service.next_player.assert_called_once_with(game_id)
    fake_manager.broadcast_to_game.assert_any_call(
    game_id,
    {
        "type": "endTimer",
        "data": {
            "player_id": str(player_id)
        },
    },
    )
    fake_manager.broadcast_to_game.assert_any_call(
        game_id,
        {
            "type": "turnChange",
            "data": {
                "player_id": str(fake_next_player)
            },
        },
    )


@pytest.mark.asyncio
async def test_handler_end_timer_when_game_ends_broadcasts_gameEnded(db_session):
    """Debe detectar EndGameResult y enviar broadcast con gameEnded."""
    from app.game.service import GameService

    game_service = GameService(db_session)
    game_id = uuid.uuid4()

    db_session.add(Game(id=game_id, name="EndGameTimer", host_id=uuid.uuid4(), min_players=2, max_players=4))
    db_session.commit()

    game_service.get_turn_state = MagicMock(
        return_value=GameTurnStateOut(turn_state=TurnState.IDLE, target_player_id=None)
    )
    game_service.handle_end_timer_normal_state = MagicMock()
    game_service.change_turn_state = MagicMock()

    fake_end_result = EndGameResult(
        winning_team="DETECTIVES", winners=[], player_roles=[], reason="DECK_EMPTY"
    )
    game_service.next_player = MagicMock(return_value=fake_end_result)

    mock_game = MagicMock()
    mock_game.turn_state.state = TurnState.IDLE

    fake_manager = AsyncMock()
    with (
        patch.object(game_service.db, "query") as mock_query,
        patch("app.game.service.manager", fake_manager)
    ):
        mock_query.return_value.filter.return_value.first.return_value = mock_game
        await game_service.handler_end_timer(game_id)

    fake_manager.broadcast_to_game.assert_any_call(
        game_id, {"type": "gameEnded", "data": ANY}
    )


def test_handle_end_timer_normal_state_player_with_6_cards(monkeypatch, db_session):
    """Si el jugador tiene 6 cartas, debe descartar una y robar una nueva."""
    from app.game.service import GameService
    from app.card.enums import CardOwner
    from app.card.schemas import CardMoveIn

    game_service = GameService(db_session)
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()

    # Mocks
    monkeypatch.setattr("app.game.service.CardService.count_player_hand", MagicMock(return_value=6))
    fake_cards = [MagicMock(id=uuid.uuid4())]
    monkeypatch.setattr("app.game.service.CardService.get_cards_by_owner", MagicMock(return_value=fake_cards))
    monkeypatch.setattr("app.game.service.CardService.move_card", MagicMock())
    monkeypatch.setattr("app.game.service.CardService.moveDeckToPlayer", MagicMock())

    game_service.handle_end_timer_normal_state(game_id, player_id)

    CardService = __import__("app.card.service", fromlist=["CardService"]).CardService
    CardService.count_player_hand.assert_called_once_with(db_session, game_id, player_id)
    CardService.get_cards_by_owner.assert_called_once_with(db_session, game_id, CardOwner.PLAYER, player_id)
    CardService.move_card.assert_called_once()
    CardService.moveDeckToPlayer.assert_called_once_with(db_session, game_id, player_id, 1)


def test_handle_end_timer_normal_state_player_with_less_than_6(monkeypatch, db_session):
    """Si el jugador tiene menos de 6 cartas, debe robar la diferencia."""
    from app.game.service import GameService
    from app.card.enums import CardOwner

    game_service = GameService(db_session)
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()

    # Mock CardService
    monkeypatch.setattr("app.game.service.CardService.count_player_hand", MagicMock(return_value=3))
    monkeypatch.setattr("app.game.service.CardService.moveDeckToPlayer", MagicMock())

    game_service.handle_end_timer_normal_state(game_id, player_id)

    CardService = __import__("app.card.service", fromlist=["CardService"]).CardService
    CardService.moveDeckToPlayer.assert_called_once_with(db_session, game_id, player_id, 3)
@pytest.mark.parametrize("num_players, expected_deck_name", [
    (2, "deck2p.json"),
    (3, "deck.json"),
    (4, "deck.json"),
    (5, "deck.json"),
    (6, "deck.json"),
])
def test_start_game_loads_correct_deck_file(
    game_service, db_session, num_players, expected_deck_name, monkeypatch
):
    """
    Verifica que start_game intenta cargar el archivo JSON correcto
    (deck2p.json o deck.json) según el número de jugadores,
    mockeando las dependencias externas.
    """
    # 1. Arrange: Crear juego y añadir jugadores
    dto = GameInDTO(
        name=f"Deck Test {num_players}p",
        host_name=str(uuid.uuid4()),
        birthday=date(2000,1,1),
        min_players=num_players,
        max_players=6
    )
    game_dto = game_service.create_game(dto)

    for i in range(1, num_players):
        game_service.add_player(game_dto.id, PlayerInDTO(name=f"Player{i+1}", birthday=date(2001, i, i)))

    game_db_obj = db_session.query(Game).filter(Game.id == game_dto.id).first()
    assert game_db_obj is not None, "El juego no se creó correctamente en la DB"
    db_session.refresh(game_db_obj)
    assert len(game_db_obj.players) == num_players, f"Se esperaban {num_players} jugadores, pero se encontraron {len(game_db_obj.players)}"

    mock_file_dict = {"items": [{"type": "EVENT", "name": "Test Card", "description": "Desc"}]}
    mock_file_content = json.dumps(mock_file_dict)
    mock_loader = mock_open(read_data=mock_file_content)

    mock_create_cards = MagicMock(return_value=[])
    mock_shuffle = MagicMock()
    mock_players_list = [MagicMock(id=uuid.uuid4()) for _ in range(num_players)]
    mock_get_players = MagicMock(return_value=mock_players_list)
    mock_deal_cards = MagicMock()
    mock_initialize_draft = MagicMock(return_value=[])
    mock_create_secrets = MagicMock(return_value=[]) 
    mock_deal_secrets = MagicMock(return_value={}) 

    mock_first_player = MagicMock(return_value=game_dto.host_id)

    opened_path = None
    def spy_open(self, *args, **kwargs):
        nonlocal opened_path
        opened_path = self.name
        return mock_loader(self, *args, **kwargs)

    expected_payload_obj = CardBatchIn(
        items=[CardIn(**item) for item in mock_file_dict["items"]]
    )

    with monkeypatch.context() as m:
        m.setattr(Path, "open", spy_open)
        m.setattr("json.load", lambda f: mock_file_dict)

        m.setattr(game_service, "first_player", mock_first_player)

        m.setattr("app.game.service.CardService.create_cards_batch", mock_create_cards)
        m.setattr("app.game.service.CardService.shuffle_deck", mock_shuffle)
        m.setattr("app.game.service.CardService.deal_cards", mock_deal_cards)
        m.setattr("app.game.service.CardService.initialize_draft", mock_initialize_draft)

        m.setattr("app.game.service.PlayerService", lambda db: MagicMock(get_players_by_game_id=mock_get_players))

        m.setattr("app.game.service.SecretService.create_secrets", mock_create_secrets)
        m.setattr("app.game.service.SecretService.deal_secrets", mock_deal_secrets)


        result = game_service.start_game(game_dto.id)

    assert result is True, f"start_game devolvió False. Player count: {len(game_db_obj.players)}, min_players: {game_db_obj.min_players}"
    assert opened_path == expected_deck_name, f"Se esperaba abrir '{expected_deck_name}' pero se abrió '{opened_path}'"

    mock_first_player.assert_called_once_with(game_dto.id)
    mock_create_cards.assert_called_once()
    call_args_tuple = mock_create_cards.call_args[0]
    assert len(call_args_tuple) == 3, "create_cards_batch fue llamado con un número incorrecto de argumentos"
    assert call_args_tuple[1] == game_dto.id, "El game_id pasado a create_cards_batch es incorrecto"

    actual_payload_obj = call_args_tuple[2]
    assert isinstance(actual_payload_obj, CardBatchIn), "El payload no es una instancia de CardBatchIn"
    assert len(actual_payload_obj.items) == len(expected_payload_obj.items), "El número de items en el payload no coincide"
    assert actual_payload_obj.items[0].name == expected_payload_obj.items[0].name, "El contenido del payload no coincide"

    mock_shuffle.assert_called_once()
    mock_get_players.assert_called_once()
    mock_deal_cards.assert_called_once()
    mock_initialize_draft.assert_called_once()
    mock_create_secrets.assert_called_once()
    mock_deal_secrets.assert_called_once()


@pytest.fixture
def game_with_state(game_service, db_session):
    """
    Crea un juego, jugadores (p1, p2), y un objeto GameTurnState inicializado en IDLE.
    """
    dto = GameInDTO(
        name="Test Game PYS",
        host_name="Player 1",
        birthday=date(2000,1,1),
        min_players=2,
        max_players=4
    )
    game_dto = game_service.create_game(dto)
    p2_id = game_service.add_player(game_dto.id, PlayerInDTO(name="Player 2", birthday=date(2001,1,1)))
    
    game = db_session.query(Game).filter(Game.id == game_dto.id).first()
    
    turn_state = GameTurnState(
        game_id=game.id,
        state=TurnState.IDLE,
        vote_data=None
    )
    db_session.add(turn_state)
    game.turn_state = turn_state
    db_session.commit()
    db_session.refresh(turn_state)
    
    return {
        "game_service": game_service,
        "db": db_session,
        "game_id": game.id,
        "p1_id": game.host_id,
        "p2_id": p2_id,
        "turn_state_obj": turn_state
    }

# --- Tests para change_turn_state (VOTING) ---

def test_change_turn_state_to_voting_initializes_vote_data(game_with_state):
    """
    Verifica que al cambiar a VOTING, vote_data se inicialice como un dict vacío
    y se limpie al salir.
    """
    game_service = game_with_state["game_service"]
    game_id = game_with_state["game_id"]
    turn_state_obj = game_with_state["turn_state_obj"]
    fake_event_card_id = uuid.uuid4()

    # Poner en VOTING
    game_service.change_turn_state(
        game_id, 
        TurnState.VOTING,
        current_event_card_id=fake_event_card_id
    )
    
    assert turn_state_obj.state == TurnState.VOTING
    assert turn_state_obj.vote_data == {} 
    assert turn_state_obj.current_event_card_id == fake_event_card_id

    # Poner en DISCARDING (para limpiar)
    game_service.change_turn_state(game_id, TurnState.DISCARDING)
    
    assert turn_state_obj.state == TurnState.DISCARDING
    assert turn_state_obj.vote_data is None # Debe limpiarse
    assert turn_state_obj.current_event_card_id is None # Debe limpiarse

# --- Tests para submit_player_vote (Lógica de guardar votos) ---

def test_submit_player_vote_happy_path(game_with_state):
    """Prueba que un voto válido se guarda correctamente en el JSON."""
    game_service = game_with_state["game_service"]
    game_id = game_with_state["game_id"]
    p1_id = game_with_state["p1_id"]
    p2_id = game_with_state["p2_id"]
    turn_state_obj = game_with_state["turn_state_obj"]

    turn_state_obj.state = TurnState.VOTING
    turn_state_obj.vote_data = {}
    game_with_state["db"].commit()

    game_service.submit_player_vote(game_id, p1_id, p2_id)

    game_with_state["db"].refresh(turn_state_obj)
    assert turn_state_obj.vote_data == {str(p1_id): str(p2_id)}

def test_submit_player_vote_error_wrong_state(game_with_state):
    """Falla si el estado del juego no es VOTING."""
    game_service = game_with_state["game_service"]
    game_id = game_with_state["game_id"]
    p1_id = game_with_state["p1_id"]
    p2_id = game_with_state["p2_id"]
    
    with pytest.raises(HTTPException, match="Not in a voting phase"):
        game_service.submit_player_vote(game_id, p1_id, p2_id)

def test_submit_player_vote_error_vote_self(game_with_state):
    """Falla si un jugador intenta votarse a sí mismo."""
    game_service = game_with_state["game_service"]
    game_id = game_with_state["game_id"]
    p1_id = game_with_state["p1_id"]
    game_with_state["turn_state_obj"].state = TurnState.VOTING
    game_with_state["db"].commit()

    with pytest.raises(HTTPException, match="Cannot vote for oneself"):
        game_service.submit_player_vote(game_id, p1_id, p1_id)

def test_submit_player_vote_error_vote_twice(game_with_state):
    """Falla si un jugador intenta votar por segunda vez."""
    game_service = game_with_state["game_service"]
    game_id = game_with_state["game_id"]
    p1_id = game_with_state["p1_id"]
    p2_id = game_with_state["p2_id"]
    
    # Forzamos el estado y un voto existente
    turn_state_obj = game_with_state["turn_state_obj"]
    turn_state_obj.state = TurnState.VOTING
    turn_state_obj.vote_data = {str(p1_id): str(p2_id)} # p1 ya votó
    game_with_state["db"].commit()

    with patch("app.game.service.flag_modified"):
        with pytest.raises(HTTPException, match="Player has already voted"):
            game_service.submit_player_vote(game_id, p1_id, p2_id) # p1 intenta votar de nuevo
def test_remove_player_not_host(db_session):
    """
    Prueba que un jugador (que NO es el host) es eliminado 
    correctamente de una partida no iniciada.
    """
    game_service = GameService(db_session)
    
    # Crear jugadores
    host = Player(name="Host Player", birthday=date(2000, 1, 1))
    player2 = Player(name="Player 2", birthday=date(2001, 1, 1))
    db_session.add_all([host, player2])
    db_session.commit()

    # Crear partida
    game = Game(
        name="Test Game", 
        host_id=host.id, 
        min_players=2, 
        max_players=4,
        ready=False # La partida NO ha comenzado
    )
    db_session.add(game)
    db_session.commit()

    # Vincular jugadores a la partida
    host.game_id = game.id
    player2.game_id = game.id
    db_session.commit()
    
    game_id = game.id
    player2_id = player2.id
    
    # Verificar estado inicial
    game_db = db_session.query(Game).filter(Game.id == game_id).one()
    assert len(game_db.players) == 2

    success = game_service.remove_player(game_id, player2_id)

    assert success 
    
    # Refrescar la sesión para ver los cambios
    db_session.refresh(game_db)
    
    # El juego debe seguir existiendo
    assert db_session.query(Game).count() == 1
    
    # El jugador 2 debe haber sido eliminado
    assert db_session.query(Player).filter(Player.id == player2_id).first() is None
    
    # El juego ahora solo debe tener 1 jugador (el host)
    assert len(game_db.players) == 1
    assert game_db.players[0].id == host.id


def test_remove_player_is_host(db_session):
    """
    Prueba que si el HOST es eliminado, la partida completa 
    es eliminada (gracias a cascade="all, delete-orphan").
    """
    game_service = GameService(db_session)
    
    host = Player(name="Host Player", birthday=date(2000, 1, 1))
    player2 = Player(name="Player 2", birthday=date(2001, 1, 1))
    db_session.add_all([host, player2])
    db_session.commit()

    game = Game(
        name="Test Game", 
        host_id=host.id, 
        min_players=2, 
        max_players=4,
        ready=False
    )
    db_session.add(game)
    db_session.commit()

    host.game_id = game.id
    player2.game_id = game.id
    db_session.commit()
    
    game_id = game.id
    host_id = host.id
    player2_id = player2.id

    # Verificar estado inicial
    assert db_session.query(Game).count() == 1
    assert db_session.query(Player).count() == 2

    success = game_service.remove_player(game_id, host_id)

    assert success is True
    
    # El juego debe haber sido eliminado
    assert db_session.query(Game).filter(Game.id == game_id).first() is None
    
    # Los jugadores también deben ser eliminados (por cascade)
    assert db_session.query(Player).filter(Player.id == host_id).first() is None
    assert db_session.query(Player).filter(Player.id == player2_id).first() is None


def test_remove_player_fails_if_game_started(db_session):
    """
    Prueba que la función falla (devuelve False) si la partida 
    ya ha comenzado (game.ready == True).
    """
    game_service = GameService(db_session)
    
    host = Player(name="Host Player", birthday=date(2000, 1, 1))
    player2 = Player(name="Player 2", birthday=date(2001, 1, 1))
    db_session.add_all([host, player2])
    db_session.commit()

    game = Game(
        name="Test Game", 
        host_id=host.id, 
        min_players=2, 
        max_players=4,
        ready=True  # <-- Partida INICIADA
    )
    db_session.add(game)
    db_session.commit()

    host.game_id = game.id
    player2.game_id = game.id
    db_session.commit()
    
    success_player = game_service.remove_player(game.id, player2.id)
    success_host = game_service.remove_player(game.id, host.id)

    assert success_player is False
    assert success_host is False
    
    # Verificar que nada cambió
    assert db_session.query(Game).count() == 1
    assert db_session.query(Player).count() == 2

def test_change_turn_state_to_pending_devious_appends_player(game_with_state):
    """
    Verifica que cambiar a PENDING_DEVIOUS añade un jugador a la lista sfp_players.
    """
    game_service = game_with_state["game_service"]
    game_id = game_with_state["game_id"]
    turn_state_obj = game_with_state["turn_state_obj"]
    p1_id = game_with_state["p1_id"]
    p2_id = game_with_state["p2_id"]

    db = game_with_state["db"]

    game_service.change_turn_state(
        game_id, 
        TurnState.PENDING_DEVIOUS,
        target_player_id=p1_id
    )
    
    db.refresh(turn_state_obj)
    assert turn_state_obj.state == TurnState.PENDING_DEVIOUS
    assert turn_state_obj.sfp_players == [str(p1_id)]

    game_service.change_turn_state(
        game_id, 
        TurnState.PENDING_DEVIOUS,
        target_player_id=p2_id
    )
    
    db.refresh(turn_state_obj)
    assert turn_state_obj.sfp_players == [str(p1_id), str(p2_id)]

def test_change_turn_state_to_discarding_clears_sfp_players(game_with_state):
    """
    Verifica que cambiar a cualquier estado (como DISCARDING) 
    limpia la lista sfp_players.
    """
    game_service = game_with_state["game_service"]
    game_id = game_with_state["game_id"]
    turn_state_obj = game_with_state["turn_state_obj"]
    p1_id = game_with_state["p1_id"]

    db = game_with_state["db"]

    turn_state_obj.state = TurnState.PENDING_DEVIOUS
    turn_state_obj.sfp_players = [str(p1_id)]
    game_with_state["db"].commit()
    
    assert turn_state_obj.sfp_players == [str(p1_id)]

    game_service.change_turn_state(game_id, TurnState.DISCARDING)
    
    db.refresh(turn_state_obj)
    assert turn_state_obj.state == TurnState.DISCARDING
    assert turn_state_obj.sfp_players == []
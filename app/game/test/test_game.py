import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import date
import uuid
import json
from pathlib import Path

from app.db import Base
from app.card.models import Card
from app.card.enums import CardType
from app.card.schemas import CardIn
from app.card.service import CardService
from app.player.models import Player
from app.game.enums import GameEndReason, WinningTeam
from app.game.models import Game
from app.game.service import GameService
from app.game.schemas import GameIn, EndGameResult
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
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Game.__table__.drop(engine)
    Player.__table__.drop(engine)
    Card.__table__.drop(engine)
    Secrets.__table__.drop(engine)

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
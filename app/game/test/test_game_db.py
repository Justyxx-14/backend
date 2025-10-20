# app/game/test/test_game_db.py
import pytest
import uuid
from datetime import date, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.game.models import Game
from app.player.models import Player


@pytest.fixture(scope="function")
def db_session():
    """
    Crea solo las tablas necesarias para los tests (Game y Player).
    Usamos Base.metadata.create_all con el parámetro tables para no tocar
    otras tablas del proyecto.
    """
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


# CREACIÓN / VALIDACIONES BÁSICAS

def test_create_game_defaults(db_session):
    g = Game(
        name="Test Game",
        host_id=uuid.uuid4(),
        min_players=2,
        max_players=4
    )
    db_session.add(g)
    db_session.commit()
    db_session.refresh(g)

    assert g.id is not None
    assert g.name == "Test Game"
    assert g.ready is False 
    assert g.min_players == 2
    assert g.max_players == 4


def test_create_multiple_games(db_session):
    g1 = Game(name="G1", host_id=uuid.uuid4(), min_players=2, max_players=4)
    g2 = Game(name="G2", host_id=uuid.uuid4(), min_players=2, max_players=4)
    db_session.add_all([g1, g2])
    db_session.commit()

    games = db_session.query(Game).all()
    assert len(games) == 2
    assert {g.name for g in games} == {"G1", "G2"}


# VALIDACIONES en Game (modelo)

def test_game_invalid_min_players_negative_raises():
    # validate_players en el modelo previene min_players < 0
    with pytest.raises(ValueError):
        Game(name="BadMin", host_id=uuid.uuid4(), min_players=-1, max_players=4)


def test_game_invalid_max_less_than_min_raises():
    # validate_players previene max_players < min_players
    with pytest.raises(ValueError):
        Game(name="BadMax", host_id=uuid.uuid4(), min_players=4, max_players=2)


def test_updating_max_to_less_than_min_raises(db_session):
    # Crear juego válido
    g = Game(name="UpdGame", host_id=uuid.uuid4(), min_players=2, max_players=4)
    db_session.add(g)
    db_session.commit()
    db_session.refresh(g)

    # Intentar asignar max_players < min_players provoca ValueError al hacer assignment
    with pytest.raises(ValueError):
        g.max_players = 1


# VALIDACIONES DE HOST (validate_host)
# validate_host solo valida cuando object_session(self) no es None
# y cuando se asigna host_id estando la instancia asociada a una sesión.

def test_setting_host_to_nonexistent_player_raises(db_session):
    # Crear un juego con un host válido (se saltea la validación en __init__)
    initial_host = Player(name="Initial", birthday=date(2000, 1, 1))
    db_session.add(initial_host)
    db_session.commit()

    g = Game(name="HostTest", host_id=initial_host.id, min_players=2, max_players=4)
    db_session.add(g)
    db_session.commit()
    db_session.refresh(g)

    # Ahora intentar asignar host_id a un UUID que no existe en Player debe levantar ValueError
    random_id = uuid.uuid4()
    with pytest.raises(ValueError):
        g.host_id = random_id


def test_setting_host_to_player_not_in_game_raises(db_session):
    # Crear jugador existente
    p = Player(name="P", birthday=date(2000, 1, 1))
    db_session.add(p)
    db_session.commit()

    # Crear juego distinto
    g = Game(name="GameA", host_id=p.id, min_players=2, max_players=4)
    db_session.add(g)
    db_session.commit()
    db_session.refresh(g)

    # p.game_id todavía no es g.id -> asignar g.host_id = p.id (o reasignar) produce ValueError
    with pytest.raises(ValueError):
        g.host_id = p.id  # player exists but player.game_id != g.id => raise


def test_setting_host_to_player_in_same_game_succeeds(db_session):
    # Crear jugador
    p = Player(name="HostOK", birthday=date(2000, 1, 1))
    db_session.add(p)
    db_session.commit()

    # Crear juego con host_id p.id (validación en __init__ se saltea)
    g = Game(name="GameOK", host_id=p.id, min_players=2, max_players=4)
    db_session.add(g)
    db_session.commit()
    db_session.refresh(g)

    # Ahora se asigna el jugador al juego (player.game_id = g.id)
    p.game_id = g.id
    db_session.commit()
    db_session.refresh(p)

    # Re-asigna el host_id (esto ejecuta validate_host con session presente).
    # No debe lanzar
    g.host_id = p.id
    db_session.commit()
    db_session.refresh(g)
    assert g.host_id == p.id


# RELACIONES Game <-> Player

def test_add_host_player_relation(db_session):
    # Creo host (sin game_id aún)
    host = Player(name="Host", birthday=date(2000, 1, 1))
    db_session.add(host)
    db_session.commit()

    # Creo juego y luego vinculo host.game_id = game.id
    g = Game(name="Game with Host", host_id=host.id, min_players=2, max_players=4)
    db_session.add(g)
    db_session.commit()
    db_session.refresh(g)

    host.game_id = g.id
    db_session.commit()
    db_session.refresh(host)
    db_session.refresh(g)

    assert host.game_id == g.id
    assert host in g.players


def test_add_multiple_players_and_relationship(db_session):
    g = Game(name="GamePlayers", host_id=uuid.uuid4(), min_players=2, max_players=5)
    db_session.add(g)
    db_session.commit()

    p1 = Player(name="P1", birthday=date(2001, 1, 1), game_id=g.id)
    p2 = Player(name="P2", birthday=date(2002, 2, 2), game_id=g.id)
    db_session.add_all([p1, p2])
    db_session.commit()

    db_session.refresh(g)
    assert len(g.players) == 2
    assert {p.name for p in g.players} == {"P1", "P2"}


# CONSULTAS / UPDATE / DELETE

def test_query_game_by_id(db_session):
    g = Game(name="UniqueGame", host_id=uuid.uuid4(), min_players=2, max_players=4)
    db_session.add(g)
    db_session.commit()

    fetched = db_session.query(Game).filter(Game.id == g.id).first()
    assert fetched is not None
    assert fetched.id == g.id
    assert fetched.name == "UniqueGame"


def test_update_game_ready_flag(db_session):
    g = Game(name="Startable", host_id=uuid.uuid4(), min_players=2, max_players=4)
    db_session.add(g)
    db_session.commit()

    g.ready = True
    db_session.commit()

    refreshed = db_session.query(Game).filter_by(id=g.id).first()
    assert refreshed.ready is True


def test_delete_game_behavior_with_players(db_session):
    g = Game(name="ToDelete", host_id=uuid.uuid4(), min_players=2, max_players=4)
    db_session.add(g)
    db_session.commit()

    p1 = Player(name="A", birthday=date(1999, 1, 1), game_id=g.id)
    p2 = Player(name="B", birthday=date(1998, 2, 2), game_id=g.id)
    db_session.add_all([p1, p2])
    db_session.commit()

    db_session.delete(g)
    db_session.commit()

    # game eliminado
    assert db_session.query(Game).filter(Game.id == g.id).first() is None

    # jugadores eliminados en cascada
    assert db_session.query(Player).all() == []


# MODELO Player: validaciones

def test_player_name_not_empty_raises():
    with pytest.raises(ValueError):
        Player(name="", birthday=date(2000, 1, 1))


def test_player_future_birthday_raises():
    future = date.today() + timedelta(days=365)
    with pytest.raises(ValueError):
        Player(name="Future", birthday=future)


# COMPORTAMIENTO: max_players NO es impuesto por DB-model

def test_model_allows_more_players_than_max(db_session):
    """
    El modelo Game no impide a nivel DB que se inserten más players que max_players.
    Esa regla está en la capa de servicio/schema. Aquí comprobamos que la DB no la impone.
    """
    g = Game(name="NoEnforceMax", host_id=uuid.uuid4(), min_players=1, max_players=1)
    db_session.add(g)
    db_session.commit()

    # Insertar 3 players con game_id = g.id; el modelo no arroja error
    p1 = Player(name="P1", birthday=date(2000, 1, 1), game_id=g.id)
    p2 = Player(name="P2", birthday=date(2001, 1, 1), game_id=g.id)
    p3 = Player(name="P3", birthday=date(2002, 1, 1), game_id=g.id)
    db_session.add_all([p1, p2, p3])
    db_session.commit()

    db_session.refresh(g)
    # Comprobamos que los 3 players quedan asociados a nivel ORM
    assert len(g.players) == 3

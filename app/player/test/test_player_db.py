import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base
from app.player.models import Player
from app.game.models import Game


@pytest.fixture
def engine():
    """Engine SQLite en memoria para testing."""
    return create_engine("sqlite:///:memory:", echo=False)


@pytest.fixture
def session(engine):
    """Crea solo la tabla 'players' y devuelve una sesión limpia por test."""
    Base.metadata.create_all(bind=engine, tables=[Player.__table__])
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    yield session
    session.close()
    connection.close()


def test_create_valid_player_persists_and_generates_uuid(session):
    p = Player(name="test_name", birthday=date(2000, 1, 1))
    session.add(p)
    session.commit()

    saved = session.query(Player).one()
    assert saved.name == "test_name"
    assert saved.birthday == date(2000, 1, 1)
    assert isinstance(saved.id, uuid.UUID)
    assert saved.social_disgrace is False


def test_default_uuid_is_generated_if_not_provided(session):
    p = Player(name="Usuario", birthday=date.today())
    session.add(p)
    session.commit()
    assert isinstance(p.id, uuid.UUID)
    assert p.social_disgrace is False


def test_whitespace_only_name_raises_value_error_on_construction():
    with pytest.raises(ValueError, match="El nombre del jugador no puede estar vacío"):
        Player(name="   ", birthday=date(2000, 1, 1))


def test_none_name_raises_value_error_on_construction():
    with pytest.raises(ValueError, match="El nombre del jugador no puede estar vacío"):
        Player(name=None, birthday=date(2000, 1, 1))


def test_birthday_cannot_be_future_on_construction():
    future = date.today() + timedelta(days=10)
    with pytest.raises(ValueError, match="La fecha de nacimiento no puede ser futura"):
        Player(name="Futuro", birthday=future)

def test_birthday_today_is_allowed(session):
    today = date.today()
    p = Player(name="Hoy", birthday=today)
    session.add(p)
    session.commit()

    saved = session.query(Player).filter_by(name="Hoy").one()
    assert saved.birthday == today


def test_missing_birthday_raises_integrity_error_on_commit(session):
    # Si no asignás birthday, la constraint NOT NULL de la tabla debe fallar al commit
    p = Player(name="SinFecha")
    session.add(p)
    with pytest.raises(IntegrityError):
        session.commit()


def test_missing_name_raises_integrity_error_on_commit(session):
    p = Player(birthday=date(2000, 1, 1))
    session.add(p)
    with pytest.raises(IntegrityError):
        session.commit()


def test_whitespace_is_preserved_in_name(session):
    p = Player(name="  Juan  ", birthday=date(1995, 6, 1))
    session.add(p)
    session.commit()

    saved = session.query(Player).filter_by(id=p.id).one()
    assert saved.name == "  Juan  "


def test_multiple_players_have_distinct_uuids(session):
    a = Player(name="A", birthday=date(1990, 1, 1))
    b = Player(name="B", birthday=date(1991, 2, 2))
    session.add_all([a, b])
    session.commit()

    assert a.id != b.id

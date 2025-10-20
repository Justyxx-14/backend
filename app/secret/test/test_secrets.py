import uuid
import pytest
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.secret.models import Secrets
from app.secret.enums import SecretType
from app.player.models import Player  
from app.game.models import Game

@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)

def test_create_secret(session):
    player = Player(
        id=uuid.uuid4(),
        name="Juan",
        birthday=date(2000, 1, 1)
    )
    session.add(player)
    session.commit()

    secret = Secrets(
        name="Test Secret",
        game_id=uuid.uuid4(),
        id=uuid.uuid4(),
        owner_player_id=player.id,
        role=SecretType.MURDERER,
        description="Un secreto peligroso"
    )
    session.add(secret)
    session.commit()

    saved = session.query(Secrets).first()
    session.refresh(saved)

    assert saved is not None
    assert saved.owner_player_id == player.id
    assert saved.revealed is False
    assert saved.role == SecretType.MURDERER
    assert saved.description == "Un secreto peligroso"

def test_invalid_enum(session):
    player = Player(
        id=uuid.uuid4(),
        name="María",
        birthday=date(1999, 5, 15)
    )
    session.add(player)
    session.commit()

    with pytest.raises(ValueError):
        secret = Secrets(
            name="Test Secret",
            id=uuid.uuid4(),
            owner_player_id=player.id,
            role="INVALID_ROLE",
            description="Esto no debería funcionar"
        )
        session.add(secret)
        session.commit()
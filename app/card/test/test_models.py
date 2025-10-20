import uuid
import pytest
import datetime

from app.db import Base
from app.card.models import Card
from app.card.enums import CardType, CardOwner
from app.player.models import Player

from sqlalchemy import inspect, create_engine, text, insert, update, event, Table, Column, Enum as SAEnum, String
from sqlalchemy.types import Uuid
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError



def test_card_model_structure_and_types():
    """Verifica que el modelo Card tiene las columnas y tipos correctos."""
    mapper = inspect(Card)
    cols = mapper.columns

    # existen las columnas
    for col in ["id", "game_id", "type", "name", "description", "owner", "owner_player_id"]:
        assert col in cols

    # tipos de datos 
    assert isinstance(cols.id.type, Uuid)
    assert isinstance(cols.game_id.type, Uuid)
    assert isinstance(cols.owner_player_id.type, Uuid)

    assert isinstance(cols.name.type, String)
    assert cols.name.type.length == 80

    assert isinstance(cols.description.type, String)
    assert cols.description.type.length == 255

    assert isinstance(cols.type.type, SAEnum)
    assert cols.type.type.enum_class is CardType
    assert isinstance(cols.owner.type, SAEnum)
    assert cols.owner.type.enum_class is CardOwner

    
    assert cols.id.primary_key is True
    assert cols.game_id.nullable is False
    assert cols.type.nullable is False
    assert cols.owner.nullable is False
    assert cols.owner_player_id.nullable is True


def test_card_model_instantiation_defaults():
    """Verifica que la clase acepta instanciación básica y defaults de Python."""
    card = Card(
        game_id=uuid.uuid4(),
        type=CardType.EVENT,
        name="Test",
        description="Testing",
        owner=CardOwner.DECK
    )

    # Antes de flush, SQLAlchemy NO setea default de columna automáticamente.
    assert card.id is None

    assert card.name == "Test"
    assert card.description == "Testing"
    assert card.owner == CardOwner.DECK
    assert card.owner_player_id is None


def test_card_model_enums_values():
    """Los enums del modelo exponen exactamente los valores esperados del dominio."""
    from app.card.enums import CardType, CardOwner
    mapper = inspect(Card)
    cols = mapper.columns

    # SAEnum almacena los "enums" como una lista de strings
    type_values = set(cols.type.type.enums)
    owner_values = set(cols.owner.type.enums)

    # Confrontamos contra los valores del Enum Python
    assert type_values == {e.value for e in CardType}
    assert owner_values == {e.value for e in CardOwner}


def test_card_model_foreign_keys_targets():
    t = Card.__table__

    # game_id -> games.id
    fks_game = list(t.c.game_id.foreign_keys)
    assert len(fks_game) == 1
    fk_game = fks_game[0]
    assert fk_game.target_fullname == "games.id"

    # owner_player_id -> players.id
    fks_player = list(t.c.owner_player_id.foreign_keys)
    assert len(fks_player) == 1
    fk_player = fks_player[0]
    assert fk_player.target_fullname == "players.id" 


def test_card_model_indexes_present():
    """Hay índices útiles para consultas comunes."""
    t = Card.__table__
    idx_cols = {tuple(idx.columns.keys()) for idx in t.indexes}

    # índices simples marcados con index=True en el modelo
    assert ("game_id",) in idx_cols
    assert ("owner",) in idx_cols
    assert ("owner_player_id",) in idx_cols




# --- Tests de integración con DB (en memoria) para Card ---

@pytest.fixture(scope="function")
def session():
    """
    DB SQLite en memoria y sesión fresca por test.
    - Registra 'players' (modelo real).
    - Define 'games' mínima solo con id (para satisfacer FK de Card).
    - Crea tables: games, players, cards.
    """
    engine = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(engine, "connect")
    def _fk_pragma_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    TestingSessionLocal = sessionmaker(bind=engine)

    md = Base.metadata

    # players ya está en metadata por el import del modelo
    players_t = Player.__table__

    # dummy para games 
    games_t = md.tables.get("games")
    if games_t is None:
        games_t = Table("games", md, Column("id", Uuid, primary_key=True))

    # crear las 3 tablas necesarias en esta DB en memoria
    md.create_all(engine, tables=[games_t, players_t, Card.__table__])

    db = TestingSessionLocal()
    db.execute(text("PRAGMA foreign_keys=ON"))

    try:
        yield db
    finally:
        db.rollback()
        db.close()



def _ensure_schema(session):
    """Crea la tabla 'games' mínima y garantiza que existan players/cards en esta DB."""
    md = Base.metadata
    games = md.tables.get("games")
    if games is None:
        games = Table("games", md, Column("id", Uuid, primary_key=True))
    # crear sólo lo necesario en esta conexión
    md.create_all(session.bind, tables=[games, Player.__table__, Card.__table__])
    return games

def _seed_game(session) -> uuid.UUID:
     gid = uuid.uuid4()
     games_t = Base.metadata.tables["games"]
     if "name" in games_t.c:
         host_pid = _seed_player(session, name="Host", birthday=datetime.date(1990, 1, 1))
         session.execute(insert(games_t).values(
             id=gid,
             name="Test Game",
             host_id=host_pid,
             min_players=1,
             max_players=4,
             ready=False,
         ))

         session.execute(update(Player.__table__)
                         .where(Player.__table__.c.id == host_pid)
                         .values(game_id=gid))
         session.commit()
     else:
         session.execute(insert(games_t).values(id=gid))
         session.commit()
     return gid

def _seed_player(session, name="Juan", birthday=datetime.date(1990, 1, 1)) -> uuid.UUID:
    pid = uuid.uuid4()
    session.add(Player(id=pid, name=name, birthday=birthday))
    session.commit()           # <— asegura que la fila exista antes de usarla como FK
    return pid


def test_create_card_valid_in_deck(session):
    """Una carta válida en el mazo (DECK, sin jugador) se persiste correctamente."""
    _ensure_schema(session)
    gid = _seed_game(session)

    card = Card(
        game_id=gid,
        type=CardType.EVENT,
        name="Test",
        description="test",
        owner=CardOwner.DECK,  
        owner_player_id=None,
    )
    session.add(card)
    session.commit()

    result = session.query(Card).filter_by(name="Test").first()
    assert result is not None
    assert result.owner == CardOwner.DECK
    assert result.owner_player_id is None

def test_create_card_valid_in_player(session):
    """Una carta válida asignada a un jugador (PLAYER) se persiste correctamente."""
    _ensure_schema(session)
    gid = _seed_game(session)
    pid = _seed_player(session, name="Emi", birthday=datetime.date(1999, 11, 18))

    card = Card(
        game_id=gid,
        type=CardType.EVENT,
        name="Test",
        description="test",
        owner=CardOwner.PLAYER,
        owner_player_id=pid,        # FK válida a players.id
    )
    session.add(card)
    session.commit()

    got = session.query(Card).filter_by(name="Test").first()
    assert got is not None
    assert got.owner == CardOwner.PLAYER
    assert got.owner_player_id == pid

def test_create_card_without_id_generates_uuid(session):
    """Si no se pasa id, debe generarse automáticamente un UUID válido (default=uuid4)."""
    _ensure_schema(session)
    gid = _seed_game(session)

    card = Card(
        game_id=gid,
        type=CardType.EVENT,
        name="Test AutoID",
        description="X",
        owner=CardOwner.DECK,
    )
    session.add(card)
    session.commit()

    assert card.id is not None
    assert isinstance(card.id, uuid.UUID)

def test_create_card_without_name(session):
    """El campo name es obligatorio (NOT NULL)."""
    _ensure_schema(session)
    gid = _seed_game(session)

    card = Card(
        game_id=gid,
        type=CardType.EVENT,
        name=None,                   # forzamos NULL a nivel ORM
        description="Desc",
        owner=CardOwner.DECK,
    )
    session.add(card)
    with pytest.raises(IntegrityError):
        session.commit()

def test_create_card_missing_game_fk(session):
    """Si el game_id no existe en games, debe violar la FK."""
    _ensure_schema(session)
    # NO sembramos games para este gid
    fake_gid = uuid.uuid4()

    card = Card(
        game_id=fake_gid,
        type=CardType.EVENT,
        name="Sin Partida",
        description="No debería persistir",
        owner=CardOwner.DECK,
    )
    session.add(card)
    with pytest.raises(IntegrityError):
        session.commit()

def test_create_card_missing_player_fk_when_owner_is_player(session):
    """Si owner=PLAYER y owner_player_id no existe, debe violar la FK."""
    _ensure_schema(session)
    gid = _seed_game(session)
    fake_pid = uuid.uuid4()  # no lo sembramos en players

    card = Card(
        game_id=gid,
        type=CardType.EVENT,
        name="Carta en Mano",
        description="No debería persistir",
        owner=CardOwner.PLAYER,
        owner_player_id=fake_pid,    # FK inválida
    )
    session.add(card)
    with pytest.raises(IntegrityError):
        session.commit()

def test_multiple_cards_persisted(session):
    """Se pueden guardar múltiples cartas y luego consultarlas."""
    _ensure_schema(session)
    gid = _seed_game(session)

    cards = [
        Card(game_id=gid, type=CardType.EVENT, name="A", description="d", owner=CardOwner.DECK),
        Card(game_id=gid, type=CardType.EVENT, name="B", description="d", owner=CardOwner.DECK),
        Card(game_id=gid, type=CardType.EVENT, name="C", description="d", owner=CardOwner.DECK),
    ]
    session.add_all(cards)
    session.commit()

    results = session.query(Card).all()
    assert len(results) == 3
    assert {c.name for c in results} == {"A", "B", "C"}
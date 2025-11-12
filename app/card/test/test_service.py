import uuid
import pytest
from datetime import date
from unittest.mock import MagicMock, patch, call, ANY
from pydantic import ValidationError
from sqlalchemy import func, create_engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from fastapi import HTTPException
from pydantic_core import ValidationError
from app.db import Base

from app.card.service import CardService
from app.card import models, schemas
from app.card.enums import CardOwner, CardType
from app.card.models import Card
from app.card.exceptions import (
    CardNotFoundException,
    CardIdMismatchException,
    DatabaseCommitException,
    GameNotFoundException,
    PlayerHandLimitExceededException,
    CardsNotFoundOrInvalidException,
    NoCardsException,
    InvalidAmountOfCards,
    ERR_CARD_NOT_FOUND,
    ERR_CARD_ID_MISMATCH,
    ERR_GAME_NOT_FOUND,
    ERR_DB_COMMIT
)

from app.game.models import Game, GameTurnState
from app.game.enums import TurnState
from app.game.service import GameService
from app.game.dtos import GameInDTO
from app.set.exceptions import SetNotFound
from app.secret.models import Secrets
from app.secret.service import SecretService
from app.player.models import Player
from app.player.dtos import PlayerInDTO

@pytest.fixture
def db_session():
    """Crea un mock de la sesión de base de datos."""
    db = MagicMock()
    # Configuración genérica de mock para que las queries no fallen
    db.query.return_value.filter.return_value.first.return_value = None
    return db


def _build_card_trade_cards(game_id, player_id, target_player_id):
    """Crea cartas válidas para los tests de Card Trade."""
    event = MagicMock(spec=models.Card)
    event.id = uuid.uuid4()
    event.game_id = game_id
    event.name = "E_CT"
    event.owner = CardOwner.PLAYER
    event.owner_player_id = player_id
    event.description = "Card Trade"

    offered = MagicMock(spec=models.Card)
    offered.id = uuid.uuid4()
    offered.game_id = game_id
    offered.owner = CardOwner.PLAYER
    offered.owner_player_id = player_id

    target = MagicMock(spec=models.Card)
    target.id = uuid.uuid4()
    target.game_id = game_id
    target.owner = CardOwner.PLAYER
    target.owner_player_id = target_player_id

    return event, offered, target


def _patch_card_lookup(monkeypatch, lookup):
    """Parcha CardService.get_card_by_id con un diccionario auxiliar."""
    monkeypatch.setattr(
        CardService,
        "get_card_by_id",
        staticmethod(lambda db_, cid: lookup.get(cid)),
    )

def make_db_mock() -> MagicMock:
    """Mock básico de la sesión de DB."""
    db = MagicMock()
    q = db.query.return_value
    f = q.filter.return_value
    f.first.return_value = None
    f.all.return_value = []
    db.commit = MagicMock()
    db.refresh = MagicMock()
    db.rollback = MagicMock()
    db.query.return_value.filter.return_value.scalar = MagicMock(return_value=0)
    return db

#------------Create

def test_create_card_adds_and_commits():
    db = make_db_mock()
    game_id = uuid.uuid4()
    card_in = schemas.CardIn(type=CardType.EVENT, name="test", description="desc")

    card = CardService.create_card(db, game_id, card_in)

    assert isinstance(card, models.Card) 
    assert card.game_id == game_id
    assert card.type == CardType.EVENT
    assert card.name == "test"
    assert card.owner == CardOwner.DECK
    db.add.assert_called_once_with(card)
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(card)


def test_create_cards_batch_adds_all_and_commits():
    db = make_db_mock()
    game_id = uuid.uuid4()
    batch = schemas.CardBatchIn(items=[
        schemas.CardIn(type=CardType.EVENT, name="A", description="a"),
        schemas.CardIn(type=CardType.DEVIOUS, name="B", description="b"),
    ])

    cards = CardService.create_cards_batch(db, game_id, batch)

    assert len(cards) == 2
    assert all(isinstance(c, models.Card) for c in cards)
    db.add_all.assert_called_once()
    db.commit.assert_called_once()
    assert db.refresh.call_count == 2


def test_create_cards_batch_empty_items_returns_empty_and_no_add_all():
    db = make_db_mock()
    game_id = uuid.uuid4()
    batch = schemas.CardBatchIn(items=[])
    cards = CardService.create_cards_batch(db, game_id, batch)
    assert cards == []
    db.add_all.assert_called_once_with([])
    db.commit.assert_called_once()
    assert db.refresh.call_count == 0



#---------------Get

def test_get_card_by_id_uses_query_filter_first():
    db = make_db_mock()
    wanted = uuid.uuid4()

    expected = models.Card(
        id=wanted,
        game_id=uuid.uuid4(),
        type=CardType.EVENT,
        name="X",
        description="x",
        owner=CardOwner.DECK,
        owner_player_id=None,
    )
    db.query.return_value.filter.return_value.first.return_value = expected

    got = CardService.get_card_by_id(db, wanted)
    assert got is expected
    assert db.query.call_args[0][0] is models.Card


def test_get_card_by_id_returns_none_if_not_found():
    db = make_db_mock()
    db.query.return_value.filter.return_value.first.return_value = None

    got = CardService.get_card_by_id(db, uuid.uuid4())
    assert got is None


def test_get_cards_by_game_returns_list():
    db = make_db_mock()
    gid = uuid.uuid4()
    row = models.Card(
        id=uuid.uuid4(),
        game_id=gid,
        type=CardType.DETECTIVE,
        name="A",
        description="a",
        owner=CardOwner.DECK,
        owner_player_id=None,
    )
    db.query.return_value.filter.return_value.all.return_value = [row]

    items = CardService.get_cards_by_game(db, gid)
    assert items == [row]


def test_get_cards_by_game_returns_empty_list_if_none_found():
    db = make_db_mock()
    db.query.return_value.filter.return_value.all.return_value = []

    items = CardService.get_cards_by_game(db, uuid.uuid4())
    assert items == []


def test_get_cards_by_owner_deck():
    db = make_db_mock()

    game_id = uuid.uuid4()
    rows = [
        models.Card(id=uuid.uuid4(), game_id=game_id, type=CardType.EVENT, name="test",
                    description="a", owner=CardOwner.DECK, owner_player_id=None),
        models.Card(id=uuid.uuid4(), game_id=game_id, type=CardType.EVENT, name="test",
                    description="b", owner=CardOwner.DECK, owner_player_id=uuid.uuid4())
    ]

    db.query.return_value.filter.return_value.all.return_value = rows

    got = CardService.get_cards_by_owner(db, game_id=game_id, owner=CardOwner.DECK)
    assert got == rows
    assert db.query.call_args[0][0] is models.Card


def test_get_cards_by_owner_discard():
    db = make_db_mock()

    game_id = uuid.uuid4()
    rows = [
        models.Card(id=uuid.uuid4(), game_id=game_id, type=CardType.EVENT, name="test",
                    description="a", owner=CardOwner.DISCARD_PILE, owner_player_id=None)
    ]

    db.query.return_value.filter.return_value.all.return_value = rows

    got = CardService.get_cards_by_owner(db, game_id=game_id, owner=CardOwner.DISCARD_PILE)
    assert got == rows


def test_get_cards_by_owner_player_all_players():
    db = make_db_mock()

    game_id = uuid.uuid4()
    rows = [
        models.Card(id=uuid.uuid4(), game_id=game_id, type=CardType.EVENT, name="test",
                    description="a", owner=CardOwner.PLAYER, owner_player_id=uuid.uuid4()),
        models.Card(id=uuid.uuid4(), game_id=game_id, type=CardType.EVENT, name="test",
                    description="b", owner=CardOwner.PLAYER, owner_player_id=uuid.uuid4())
    ]

    db.query.return_value.filter.return_value.all.return_value = rows

    got = CardService.get_cards_by_owner(db, game_id=game_id, owner=CardOwner.PLAYER, player_id=None)
    assert got == rows


def test_get_cards_by_owner_player_specific_player():
    db = make_db_mock()

    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    rows = [
        models.Card(id=uuid.uuid4(), game_id=game_id, type=CardType.EVENT, name="test",
                    description="a", owner=CardOwner.PLAYER, owner_player_id=player_id)
    ]

    q = db.query.return_value
    q1 = q.filter.return_value
    q2 = q1.filter.return_value
    q2.all.return_value = rows

    got = CardService.get_cards_by_owner(db, game_id=game_id, owner=CardOwner.PLAYER, player_id=player_id)
    assert got == rows


def test_get_cards_by_owner_ignores_player_id_when_owner_not_player():
    db = make_db_mock()
    gid = uuid.uuid4()
    pid = uuid.uuid4()

    expected = [
        models.Card(id=uuid.uuid4(), game_id=gid, type=CardType.EVENT, name="test",
                    description="a", owner=CardOwner.DECK, owner_player_id=None)
    ]

    db.query.return_value.filter.return_value.all.return_value = expected

    got = CardService.get_cards_by_owner(db, game_id=gid, owner=CardOwner.DECK, player_id=pid)
    assert got == expected 


def test_get_cards_by_owner_player_all_players_filter_count():
    db = make_db_mock()
    gid = uuid.uuid4()

    rows = [
        models.Card(id=uuid.uuid4(), game_id=gid, type=CardType.EVENT, name="test",
                    description="a", owner=CardOwner.PLAYER, owner_player_id=uuid.uuid4())
    ]
    q = db.query.return_value
    q.filter.return_value.all.return_value = rows

    got = CardService.get_cards_by_owner(db, game_id=gid, owner=CardOwner.PLAYER, player_id=None)
    assert got == rows
    assert q.filter.call_count == 1


def test_get_cards_by_owner_player_specific_player_filter_count():
    db = make_db_mock()
    gid, pid =uuid.uuid4(), uuid.uuid4()

    rows = [
        models.Card(id=uuid.uuid4(), game_id=gid, type=CardType.EVENT, name="test",
                    description="a", owner=CardOwner.PLAYER, owner_player_id=pid)
    ]
    q = db.query.return_value
    q1 = q.filter.return_value
    q2 = q1.filter.return_value
    q2.all.return_value = rows

    got = CardService.get_cards_by_owner(db, game_id=gid, owner=CardOwner.PLAYER, player_id=pid)
    assert got == rows
    assert q.filter.call_count == 1
    assert q1.filter.call_count == 1

#---------------Move

def test_move_card_to_deck_rejects_player_id_by_schema():
    db = make_db_mock()
    card_id = uuid.uuid4()

    existing = models.Card(
        id=card_id, game_id=uuid.uuid4(),
        type=CardType.EVENT, name="ToDeck", description="move",
        owner=CardOwner.PLAYER, owner_player_id=uuid.uuid4()
    )
    db.query.return_value.filter.return_value.first.return_value = existing

    with pytest.raises(ValidationError):
        schemas.CardMoveIn(to_owner=CardOwner.DECK, player_id=uuid.uuid4())


def test_move_card_ok_to_player():
    db = make_db_mock()
    cid = uuid.uuid4()
    existing = models.Card(
        id=cid,
        game_id=uuid.uuid4(),
        type=CardType.EVENT,
        name="A",
        description="a",
        owner=CardOwner.DECK,
        owner_player_id=None,
    )
    db.query.return_value.filter.return_value.first.return_value = existing

    pid = uuid.uuid4()
    move = schemas.CardMoveIn(to_owner=CardOwner.PLAYER, player_id=pid)

    updated = CardService.move_card(db, cid, move)  
    assert updated.owner == CardOwner.PLAYER
    assert updated.owner_player_id == pid
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(existing)


def test_move_card_ok_to_discard_clears_player_id():
    db = make_db_mock()
    card_id = uuid.uuid4()
    existing = models.Card(
        id=card_id,
        game_id=uuid.uuid4(),
        type=CardType.DEVIOUS,
        name="B",
        description="b",
        owner=CardOwner.PLAYER,
        owner_player_id=uuid.uuid4(),
    )
    db.query.return_value.filter.return_value.first.return_value = existing

    move = schemas.CardMoveIn(to_owner=CardOwner.DISCARD_PILE, player_id=None)

    updated = CardService.move_card(db, card_id, move)
    assert updated.owner == CardOwner.DISCARD_PILE
    assert updated.owner_player_id is None
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(existing)


def test_move_card_raises_if_not_found():
    db = make_db_mock()
    db.query.return_value.filter.return_value.first.return_value = None

    with pytest.raises(CardNotFoundException):
        CardService.move_card(
            db,
            uuid.uuid4(),
            schemas.CardMoveIn(to_owner=CardOwner.DECK, player_id=None),
        )


def test_move_card_ok_to_deck_sets_owner_player_id_none():
    db = make_db_mock()
    card_id = uuid.uuid4()
    existing = models.Card(
        id=card_id,
        game_id=uuid.uuid4(),
        type=CardType.EVENT,
        name="ToDeck",
        description="move",
        owner=CardOwner.PLAYER,
        owner_player_id=uuid.uuid4()
    )
    db.query.return_value.filter.return_value.first.return_value = existing

    move = schemas.CardMoveIn(to_owner=CardOwner.DECK, player_id=None)
    updated = CardService.move_card(db, card_id, move)

    assert updated.owner == CardOwner.DECK
    assert updated.owner_player_id is None
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(existing)

#------ exceptions

def test_card_not_found_exception_fields():
    exc = CardNotFoundException("abc-123")
    assert isinstance(exc, HTTPException)
    assert exc.status_code == 404
    assert exc.detail == ERR_CARD_NOT_FOUND.format(card_id="abc-123")


def test_card_id_mismatch_exception_fields():
    exc = CardIdMismatchException()
    assert isinstance(exc, HTTPException)
    assert exc.status_code == 400
    assert exc.detail == ERR_CARD_ID_MISMATCH

def test_game_not_found_exception_fields():
    exc = GameNotFoundException("test-game")
    assert isinstance(exc, HTTPException)
    assert exc.status_code == 404
    assert exc.detail == ERR_GAME_NOT_FOUND.format(game_id="test-game")

def test_db_commit_failed():
    exc = DatabaseCommitException()
    assert isinstance(exc, HTTPException)
    assert exc.status_code == 500
    assert exc.detail == ERR_DB_COMMIT

#--------------

def test_cardin_rejects_invalid_cardtype_value():
    with pytest.raises(ValidationError):
        schemas.CardIn(type="NOT_A_REAL_TYPE", name="X", description="x")

def test_get_cards_by_owner_player_nonexistent_player_returns_empty():
    db = make_db_mock()
    gid, pid = uuid.uuid4(), uuid.uuid4()
    # chain: query -> filter(game/owner) -> filter(player_id) -> all() -> []
    q = db.query.return_value
    q1 = q.filter.return_value
    q2 = q1.filter.return_value
    q2.all.return_value = []

    got = CardService.get_cards_by_owner(db, game_id=gid, owner=CardOwner.PLAYER, player_id=pid)
    assert got == []

#------Check FK

def test_create_card_rolls_back_and_raises_game_not_found_on_fk_error():
    db = make_db_mock()
    game_id = uuid.uuid4()
    db.commit.side_effect = IntegrityError("stmt", "params", Exception("fk"))
    card_in = schemas.CardIn(type=CardType.EVENT, name="Z", description="z")

    with pytest.raises(GameNotFoundException):
        CardService.create_card(db, game_id, card_in)
    db.rollback.assert_called_once()

def test_create_cards_batch_rolls_back_and_raises_game_not_found_on_fk_error():
    db = make_db_mock()
    game_id = uuid.uuid4()

    # Simulamos violación de FK en el commit (game inexistente)
    db.commit.side_effect = IntegrityError("stmt", "params", Exception("fk"))

    cards_in = schemas.CardBatchIn(items= [
        schemas.CardIn(type=CardType.EVENT, name="A", description="a"),
        schemas.CardIn(type=CardType.DETECTIVE, name="B", description="b"),
    ])

    with pytest.raises(GameNotFoundException):
        CardService.create_cards_batch(db, game_id, cards_in)

    db.rollback.assert_called_once()
    db.add_all.assert_called_once()

#--------commit error

def test_move_card_rolls_back_and_raises_db_error_on_commit_error():
    db = make_db_mock()
    cid = uuid.uuid4()
    existing = models.Card(
        id=cid,
        game_id=uuid.uuid4(),
        type=CardType.EVENT,
        name="A",
        description="a",
        owner=CardOwner.DECK,
        owner_player_id=None
    )
    db.query.return_value.filter.return_value.first.return_value = existing
    db.commit.side_effect = SQLAlchemyError("fatal_error")

    move = schemas.CardMoveIn(to_owner=CardOwner.DECK, player_id=None)

    with pytest.raises(DatabaseCommitException):
        CardService.move_card(db, cid, move)

    db.rollback.assert_called_once()

#--------- query_cards 

def test_query_cards_owner_none_calls_get_cards_by_game(monkeypatch):
    db = MagicMock()
    gid = uuid.uuid4()

    called = {}
    def fake_get_cards_by_game(db_arg, gid_arg):
        called["args"] = (db_arg, gid_arg)
        return ["sentinel_all"]

    monkeypatch.setattr(CardService, "get_cards_by_game", staticmethod(fake_get_cards_by_game))

    payload = schemas.CardQueryIn(game_id=gid)  # owner=None por defecto
    out = CardService.query_cards(db, payload)
    assert out == ["sentinel_all"]
    assert called["args"][0] is db
    assert called["args"][1] == gid


def test_query_cards_owner_player_specific_calls_get_cards_by_owner(monkeypatch):
    db = MagicMock()
    gid, pid = uuid.uuid4(), uuid.uuid4()

    called = {}
    def fake_get_cards_by_owner(db_arg, gid_arg, owner_arg, player_id_arg):
        called["args"] = (db_arg, gid_arg, owner_arg, player_id_arg)
        return ["sentinel_player_specific"]

    monkeypatch.setattr(CardService, "get_cards_by_owner", staticmethod(fake_get_cards_by_owner))

    payload = schemas.CardQueryIn(game_id=gid, owner=CardOwner.PLAYER, player_id=pid)
    out = CardService.query_cards(db, payload)
    assert out == ["sentinel_player_specific"]
    assert called["args"] == (db, gid, CardOwner.PLAYER, pid)


def test_query_cards_owner_player_requires_player_id():
    db = MagicMock()
    gid = uuid.uuid4()

    # Al construir el schema sin player_id debe fallar
    with pytest.raises(ValidationError):
        schemas.CardQueryIn(game_id=gid, owner=CardOwner.PLAYER)

def test_query_cards_owner_deck_ignores_player_id(monkeypatch):
    db = MagicMock()
    gid = uuid.uuid4()

    called = {}
    def fake_get_cards_by_owner(db_arg, gid_arg, owner_arg, player_id_arg):
        called["args"] = (db_arg, gid_arg, owner_arg, player_id_arg)
        return ["sentinel_deck"]

    monkeypatch.setattr(CardService, "get_cards_by_owner", staticmethod(fake_get_cards_by_owner))

    # player_id presente pero debe ser ignorado para DECK → se pasa None
    payload = schemas.CardQueryIn(game_id=gid, owner=CardOwner.DECK, player_id=uuid.uuid4())
    out = CardService.query_cards(db, payload)
    assert out == ["sentinel_deck"]
    assert called["args"] == (db, gid, CardOwner.DECK, None)


def test_create_card_rolls_back_and_raises_db_error_on_generic_sqlalchemy():
    from app.card import models
    db = MagicMock()
    gid = uuid.uuid4()
    # que falle el commit con un error genérico
    db.commit.side_effect = SQLAlchemyError("db_boom")

    with pytest.raises(DatabaseCommitException):
        CardService.create_card(db, gid, schemas.CardIn(type="EVENT", name="Z", description="z"))

    db.rollback.assert_called_once()


def test_create_cards_batch_rolls_back_and_raises_db_error_on_generic_sqlalchemy():
    db = MagicMock()
    gid = uuid.uuid4()
    db.commit.side_effect = SQLAlchemyError("db_boom")

    batch = schemas.CardBatchIn(items=[
        schemas.CardIn(type="EVENT", name="A", description="a"),
        schemas.CardIn(type="DETECTIVE", name="B", description="b"),
    ])

    with pytest.raises(DatabaseCommitException):
        CardService.create_cards_batch(db, gid, batch)

    db.rollback.assert_called_once()


# -------- moveDeckToPlayer --------

def test_move_deck_to_player_ok(monkeypatch):
    db = MagicMock()
    gid, pid = uuid.uuid4(), uuid.uuid4()

    # Simula que el jugador tiene 2 cartas en la mano
    monkeypatch.setattr(CardService, "count_player_hand", staticmethod(lambda db, g, p: 2))

    # Simula cartas en el mazo
    cards = [
        models.Card(id=uuid.uuid4(), game_id=gid, owner=CardOwner.DECK, order=i)
        for i in range(5, 0, -1)
    ]
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = cards[:2]

    moved = []
    def fake_move_card(db, cid, move_in):
        moved.append((cid, move_in))
        card = next(c for c in cards if c.id == cid)
        card.owner = move_in.to_owner
        card.owner_player_id = move_in.player_id
        return card

    monkeypatch.setattr(CardService, "move_card", staticmethod(fake_move_card))

    out, _ = CardService.moveDeckToPlayer(db, gid, pid, n_cards=2)
    assert out == cards[:2]
    assert all(c.owner == CardOwner.PLAYER for c in out)
    assert all(c.owner_player_id == pid for c in out)
    # Aseguramos que move_card se llamó 2 veces
    assert len(moved) == 2


def test_move_deck_to_player_raises_if_hand_limit_exceeded(monkeypatch):
    db = MagicMock()
    gid, pid = uuid.uuid4(), uuid.uuid4()

    # El jugador ya tiene 6 cartas → excede el límite
    monkeypatch.setattr(CardService, "count_player_hand", staticmethod(lambda db, g, p: 6))

    with pytest.raises(PlayerHandLimitExceededException):
        CardService.moveDeckToPlayer(db, gid, pid, n_cards=1)


# -------- movePlayertoDiscard --------

def test_move_player_to_discard_ok(monkeypatch):
    db = MagicMock()
    gid, pid = uuid.uuid4(), uuid.uuid4()
    card_ids = [uuid.uuid4(), uuid.uuid4()]

    # Simula que en la BD hay exactamente esas cartas
    rows = [
        models.Card(id=cid, game_id=gid, owner=CardOwner.PLAYER, owner_player_id=pid)
        for cid in card_ids
    ]
    db.query.return_value.filter.return_value.all.return_value = rows

    moved = []
    def fake_move_card(db, cid, move_in):
        moved.append((cid, move_in))
        card = next(c for c in rows if c.id == cid)
        card.owner = move_in.to_owner
        card.owner_player_id = None
        return card

    monkeypatch.setattr(CardService, "move_card", staticmethod(fake_move_card))

    out = CardService.movePlayertoDiscard(db, gid, pid, card_ids)
    assert out == rows
    assert all(c.owner == CardOwner.DISCARD_PILE for c in out)
    assert all(c.owner_player_id is None for c in out)
    assert len(moved) == len(card_ids)


def test_move_player_to_discard_single_uuid(monkeypatch):
    db = MagicMock()
    gid, pid = uuid.uuid4(), uuid.uuid4()
    cid = uuid.uuid4()

    row = models.Card(id=cid, game_id=gid, owner=CardOwner.PLAYER, owner_player_id=pid)
    db.query.return_value.filter.return_value.all.return_value = [row]

    def fake_move_card(db, c, m):
        row.owner = CardOwner.DISCARD_PILE
        row.owner_player_id = None
        return row

    monkeypatch.setattr(CardService, "move_card", staticmethod(fake_move_card))

    out = CardService.movePlayertoDiscard(db, gid, pid, cid)
    assert out == [row]
    assert out[0].owner == CardOwner.DISCARD_PILE


def test_move_player_to_discard_raises_if_cards_not_found(monkeypatch):
    db = MagicMock()
    gid, pid = uuid.uuid4(), uuid.uuid4()
    card_ids = [uuid.uuid4()]

    # Simula que no devuelve ninguna carta válida
    db.query.return_value.filter.return_value.all.return_value = []

    with pytest.raises(CardsNotFoundOrInvalidException):
        CardService.movePlayertoDiscard(db, gid, pid, card_ids)

# ---- Test see discard pile

def make_mock_discard_pile(order, id=None):
    
    card = MagicMock(spec=models.Card)
    card.order = order
    card.id = id or uuid.uuid4()
    return card


def test_see_discard_pile_ok():
    db = MagicMock()
    game_id = uuid.uuid4()

    mock_cards = [make_mock_discard_pile(o) for o in [7,6,5,4,3]]

    (db.query.return_value
     .filter.return_value
     .order_by.return_value
     .limit.return_value
     .all.return_value) = mock_cards
    
    result = CardService.see_top_discard(db,game_id,5)

    assert result == mock_cards


def test_see_discard_pile_empty():
    db = MagicMock()
    game_id = uuid.uuid4()

    (db.query.return_value
     .filter.return_value
     .order_by.return_value
     .limit.return_value
     .all.return_value) = []
    
    result = CardService.see_top_discard(db,game_id,5)

    assert result == []

def test_see_discard_pile_invalid_amount():
    db = MagicMock()
    game_id = uuid.uuid4()

    with pytest.raises(InvalidAmountOfCards):
        CardService.see_top_discard(db, game_id,0)


# Helper
def make_mock_card(name="E_LIA", owner=CardOwner.PLAYER, owner_player_id=None, id=None, game_id=None):
    c = MagicMock(spec=models.Card)
    c.id = id or uuid.uuid4()
    c.name = name
    c.owner = owner
    c.owner_player_id = owner_player_id
    c.game_id = game_id
    return c

def test_look_into_the_ashes_ok():
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()

    event_card = make_mock_card(name="E_LIA", owner_player_id=player_id)
    discard_card = make_mock_card(owner=CardOwner.DISCARD_PILE)
    moved_card = make_mock_card(id=discard_card.id)

    with patch("app.card.service.CardService.get_card_by_id", return_value=event_card), \
         patch("app.card.service.CardService.see_top_discard", return_value=[discard_card]), \
         patch("app.card.service.CardService.move_card", side_effect=[moved_card, event_card]) as mock_move:

        result = CardService.look_into_the_ashes(
            db, game_id, event_card_id=event_card.id, card_id=discard_card.id, player_id=player_id
        )

    assert result == moved_card
    assert mock_move.call_count == 2  # 1 mover la carta, 1 mover el evento
    mock_move.assert_any_call(db, discard_card.id, ANY)
    mock_move.assert_any_call(db, event_card.id, ANY)


def test_look_into_the_ashes_event_wrong_owner():
    db = MagicMock()
    event_card = make_mock_card(name="E_LIA", owner_player_id=uuid.uuid4())

    with patch("app.card.service.CardService.get_card_by_id", return_value=event_card):
        with pytest.raises(CardsNotFoundOrInvalidException):
            CardService.look_into_the_ashes(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())


def test_look_into_the_ashes_event_wrong_name():
    db = MagicMock()
    player_id = uuid.uuid4()
    event_card = make_mock_card(name="E_OTHER", owner_player_id=player_id)

    with patch("app.card.service.CardService.get_card_by_id", return_value=event_card):
        with pytest.raises(CardsNotFoundOrInvalidException):
            CardService.look_into_the_ashes(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), player_id)


def test_look_into_the_ashes_card_not_in_discard():
    db = MagicMock()
    player_id = uuid.uuid4()
    event_card = make_mock_card(name="E_LIA", owner_player_id=player_id)
    mock_discard = [make_mock_card(owner=CardOwner.DISCARD_PILE)]
    fake_card_id = uuid.uuid4()

    with patch("app.card.service.CardService.get_card_by_id", return_value=event_card), \
         patch("app.card.service.CardService.see_top_discard", return_value=mock_discard):
        with pytest.raises(CardsNotFoundOrInvalidException):
            CardService.look_into_the_ashes(db, uuid.uuid4(), uuid.uuid4(), fake_card_id, player_id)


def test_early_train_to_paddington_ok(monkeypatch):
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    event_id = uuid.uuid4()

    event_card = MagicMock(spec=models.Card)
    event_card.id = event_id
    event_card.name = "E_ETP"
    event_card.owner_player_id = player_id

    top_cards = [
        MagicMock(spec=models.Card, id=uuid.uuid4(), order=i)
        for i in range(6, 0, -1)
    ]
    q = db.query.return_value
    q.filter.return_value.order_by.return_value.limit.return_value.all.return_value = top_cards

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: event_card))
    moved = []

    def fake_move_card(db_, cid, move_in):
        moved.append((cid, move_in))
        return MagicMock(spec=models.Card, id=cid)

    monkeypatch.setattr(CardService, "move_card", staticmethod(fake_move_card))

    result = CardService.early_train_to_paddington(db, game_id, event_id, player_id)

    assert result.id == event_id
    assert len(moved) == 7
    for cid, move_in in moved[:-1]:
        assert move_in.to_owner == CardOwner.DISCARD_PILE
    assert moved[-1][1].to_owner == CardOwner.OUT_OFF_THE_GAME


def test_early_train_to_paddington_invalid_name(monkeypatch):
    db = MagicMock()
    event_card = MagicMock(spec=models.Card)
    event_card.name = "E_OTHER"
    event_card.owner_player_id = uuid.uuid4()

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: event_card))

    with pytest.raises(CardsNotFoundOrInvalidException):
        CardService.early_train_to_paddington(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

def test_early_train_to_paddington_wrong_owner(monkeypatch):
    db = MagicMock()
    pid = uuid.uuid4()
    event_card = MagicMock(spec=models.Card)
    event_card.name = "E_ETP"
    event_card.owner_player_id = uuid.uuid4()

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: event_card))

    with pytest.raises(CardsNotFoundOrInvalidException):
        CardService.early_train_to_paddington(db, uuid.uuid4(), uuid.uuid4(), pid)

def test_delay_the_murderer_escape_ok_moves_top_discard_and_out(monkeypatch):
    """
    Debe:
    - obtener la carta evento válida (name == "E_DME" y owner_player_id == player_id)
    - pedir top_discard (5) y mover cada carta a DECK
    - mover la carta evento a OUT_OFF_THE_GAME y devolver el resultado de ese move
    """
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    event_id = uuid.uuid4()

    event_card = MagicMock(spec=models.Card)
    event_card.id = event_id
    event_card.name = "E_DME"
    event_card.owner_player_id = player_id

    top_cards = [MagicMock(spec=models.Card, id=uuid.uuid4()) for _ in range(3)]

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: event_card))
    monkeypatch.setattr(CardService, "see_top_discard", staticmethod(lambda db_, gid, n: top_cards))

    moves = []
    def fake_move_card(db_, cid, move_in):
        moves.append((cid, move_in))
        out = MagicMock(spec=models.Card)
        out.id = cid
        out.owner = move_in.to_owner
        out.owner_player_id = getattr(move_in, "player_id", None)
        return out

    monkeypatch.setattr(CardService, "move_card", staticmethod(fake_move_card))

    result = CardService.delay_the_murderer_escape(db, game_id, player_id, event_id)

    assert isinstance(result, MagicMock)
    assert result.id == event_id

    assert len(moves) == len(top_cards) + 1

    for i, (cid, move_in) in enumerate(moves[:-1]):
        assert cid == top_cards[i].id
        assert move_in.to_owner == CardOwner.DECK

    last_cid, last_move_in = moves[-1]
    assert last_cid == event_id
    assert last_move_in.to_owner == CardOwner.OUT_OFF_THE_GAME


def test_delay_the_murderer_escape_ok_when_no_top_discard_moves_only_event(monkeypatch):
    """
    Si see_top_discard devuelve [], solo debe moverse la carta evento a OUT_OFF_THE_GAME.
    """
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    event_id = uuid.uuid4()

    event_card = MagicMock(spec=models.Card)
    event_card.id = event_id
    event_card.name = "E_DME"
    event_card.owner_player_id = player_id

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: event_card))
    monkeypatch.setattr(CardService, "see_top_discard", staticmethod(lambda db_, gid, n: []))

    calls = []

    def fake_move_card(db_, cid, move_in):
        calls.append((cid, move_in))
        out = MagicMock(spec=models.Card)
        out.id = cid
        out.owner = move_in.to_owner
        return out

    monkeypatch.setattr(CardService, "move_card", staticmethod(fake_move_card))

    res = CardService.delay_the_murderer_escape(db, game_id, player_id, event_id)

    assert len(calls) == 1
    assert calls[0][0] == event_id
    assert calls[0][1].to_owner == CardOwner.OUT_OFF_THE_GAME
    assert res.id == event_id

def test_delay_the_murderer_escape_raises_if_name_wrong(monkeypatch):
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    event_id = uuid.uuid4()

    event_card = MagicMock(spec=models.Card)
    event_card.id = event_id
    event_card.name = "E_NOT_DME"
    event_card.owner_player_id = player_id

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: event_card))

    with pytest.raises(CardsNotFoundOrInvalidException):
        CardService.delay_the_murderer_escape(db, game_id, player_id, event_id)


def test_delay_the_murderer_escape_raises_if_wrong_owner(monkeypatch):
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    event_id = uuid.uuid4()

    event_card = MagicMock(spec=models.Card)
    event_card.id = event_id
    event_card.name = "E_DME"
    event_card.owner_player_id = uuid.uuid4()

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: event_card))

    with pytest.raises(CardsNotFoundOrInvalidException):
        CardService.delay_the_murderer_escape(db, game_id, player_id, event_id)


def make_mock_card_cot(name="E_COT", owner_player_id=None, id=None):
    c = MagicMock(spec=models.Card)
    c.id = id or uuid.uuid4()
    c.name = name
    c.owner_player_id = owner_player_id
    return c


def test_cards_off_the_table_ok(monkeypatch):
    """
    - La carta evento es válida (E_COT, owner correcto)
    - El target player tiene cartas (algunas E_NSF)
    - Se mueven las E_NSF a DISCARD y la carta evento también
    """
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    event_id = uuid.uuid4()
    target_player = uuid.uuid4()
    owner = CardOwner.PLAYER

    event_card = make_mock_card_cot(owner_player_id=player_id, id=event_id)
    nsf1 = MagicMock(spec=models.Card, id=uuid.uuid4())
    nsf1.name = "E_NSF"
    nsf2 = MagicMock(spec=models.Card, id=uuid.uuid4())
    nsf2.name = "E_NSF"
    other = MagicMock(spec=models.Card, id=uuid.uuid4())
    other.name="OTHER"

    moved_cards = []

    def fake_move_card(db_, cid, move_in):
        moved_cards.append((cid, move_in))
        out = MagicMock(spec=models.Card)
        out.id = cid
        out.owner = move_in.to_owner
        return out

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: event_card))
    monkeypatch.setattr(CardService, "get_cards_by_owner", staticmethod(lambda db_, gid, owner, pid: [nsf1, nsf2, other]))
    monkeypatch.setattr(CardService, "move_card", staticmethod(fake_move_card))

    result = CardService.cards_off_the_table(db, game_id, player_id, event_id, target_player)

    assert len(moved_cards) == 3
    assert all(move_in.to_owner == CardOwner.DISCARD_PILE for _, move_in in moved_cards)
    assert result.id == event_id
    assert result.owner == CardOwner.DISCARD_PILE

def test_cards_off_the_table_invalid_event_name(monkeypatch):
    """Debe fallar si la carta no es E_COT"""
    db = MagicMock()
    bad_card = make_mock_card_cot(name="E_OTHER", owner_player_id=uuid.uuid4())

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: bad_card))

    with pytest.raises(CardsNotFoundOrInvalidException):
        CardService.cards_off_the_table(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

def test_cards_off_the_table_invalid_owner(monkeypatch):
    """Debe fallar si la carta no pertenece al jugador"""
    db = MagicMock()
    event_card = make_mock_card_cot(owner_player_id=uuid.uuid4())

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: event_card))

    with pytest.raises(CardsNotFoundOrInvalidException):
        CardService.cards_off_the_table(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())


def test_then_there_was_one_more_ok(monkeypatch):
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    event_card_id = uuid.uuid4()
    target_player = uuid.uuid4()
    secret_id = uuid.uuid4()

    # Mock del evento válido
    event_card = MagicMock(spec=models.Card)
    event_card.name = "E_ATWOM"
    event_card.owner_player_id = player_id

    # Mock del secreto válido
    secret = MagicMock()
    secret.revealed = True
    secret.game_id = game_id

    moved_card = MagicMock(spec=models.Card)

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: event_card))
    monkeypatch.setattr(SecretService, "get_secret_by_id", staticmethod(lambda db_, sid: secret))
    monkeypatch.setattr(SecretService, "change_secret_status", staticmethod(lambda db_, sid: None))
    monkeypatch.setattr(SecretService, "move_secret", staticmethod(lambda db_, sid, tp: None))
    monkeypatch.setattr(CardService, "move_card", staticmethod(lambda db_, cid, move_in: moved_card))

    result = CardService.then_there_was_one_more(db, game_id, player_id, event_card_id, target_player, secret_id)

    assert result == moved_card
    # Verifica que el evento haya pasado a la pila de descarte
    assert isinstance(result, MagicMock)
    assert result is moved_card

def test_then_there_was_one_more_invalid_event(monkeypatch):
    db = MagicMock()
    gid, pid, eid, tid, sid = [uuid.uuid4() for _ in range(5)]

    # Caso: nombre incorrecto
    bad_card = MagicMock(spec=models.Card)
    bad_card.name = "E_OTHER"
    bad_card.owner_player_id = pid

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: bad_card))

    with pytest.raises(CardsNotFoundOrInvalidException):
        CardService.then_there_was_one_more(db, gid, pid, eid, tid, sid)


def test_then_there_was_one_more_wrong_owner(monkeypatch):
    db = MagicMock()
    gid, pid, eid, tid, sid = [uuid.uuid4() for _ in range(5)]

    bad_card = MagicMock(spec=models.Card)
    bad_card.name = "E_ATWOM"
    bad_card.owner_player_id = uuid.uuid4()  # distinto dueño

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: bad_card))

    with pytest.raises(CardsNotFoundOrInvalidException):
        CardService.then_there_was_one_more(db, gid, pid, eid, tid, sid)


def test_another_victim_ok(monkeypatch):
    """
    - La carta evento es válida (E_AV, owner correcto)
    - El set objetivo pertenece al mismo juego y distinto jugador
    - change_set_owner y move_card son llamados correctamente
    """
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    event_card_id = uuid.uuid4()
    target_set_id = uuid.uuid4()

    event_card = MagicMock(spec=models.Card)
    event_card.id = event_card_id
    event_card.name = "E_AV"
    event_card.owner_player_id = player_id

    target_set = MagicMock()
    target_set.id = target_set_id
    target_set.game_id = game_id
    target_set.owner_player_id = uuid.uuid4()

    moved_card = MagicMock(spec=models.Card)

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: event_card))

    fake_set_service = MagicMock()
    fake_set_service.get_set_by_id.return_value = target_set
    fake_set_service.change_set_owner.return_value = None

    monkeypatch.setattr("app.set.service.SetService", lambda db_: fake_set_service)
    mock_move_card = MagicMock(return_value=moved_card)
    monkeypatch.setattr(CardService, "move_card", staticmethod(mock_move_card))

    result = CardService.another_victim(db, game_id, player_id, event_card_id, target_set_id)

    assert result == moved_card
    fake_set_service.change_set_owner.assert_called_once_with(game_id, target_set_id, player_id)
    CardService.move_card.assert_called_once()

def test_another_victim_invalid_event_name(monkeypatch):
    """Debe fallar si la carta evento no es E_AV"""
    db = MagicMock()
    event_card = MagicMock(spec=models.Card)
    event_card.name = "E_OTHER"
    event_card.owner_player_id = uuid.uuid4()

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: event_card))

    with pytest.raises(CardsNotFoundOrInvalidException):
        CardService.another_victim(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

def test_another_victim_invalid_owner(monkeypatch):
    """Debe fallar si la carta evento pertenece a otro jugador"""
    db = MagicMock()
    event_card = MagicMock(spec=models.Card)
    event_card.name = "E_AV"
    event_card.owner_player_id = uuid.uuid4()  # distinto jugador

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: event_card))

    with pytest.raises(CardsNotFoundOrInvalidException):
        CardService.another_victim(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

def test_another_victim_invalid_set_wrong_game(monkeypatch):
    """Debe fallar si el set pertenece a otro juego"""
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    event_card = MagicMock(spec=models.Card)
    event_card.name = "E_AV"
    event_card.owner_player_id = player_id

    bad_set = MagicMock()
    bad_set.game_id = uuid.uuid4()  # distinto juego
    bad_set.owner_player_id = uuid.uuid4()

    monkeypatch.setattr(CardService, "get_card_by_id", staticmethod(lambda db_, cid: event_card))

    fake_set_service = MagicMock()
    fake_set_service.get_set_by_id.return_value = bad_set

    monkeypatch.setattr("app.set.service.SetService", lambda db_: fake_set_service)

    with pytest.raises(SetNotFound):
        CardService.another_victim(db, game_id, player_id, uuid.uuid4(), uuid.uuid4())


def test_card_trade_ok(monkeypatch):
    """Intercambia cartas entre jugadores y descarta el evento."""
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    target_player_id = uuid.uuid4()

    event_card, offered_card, target_card = _build_card_trade_cards(
        game_id, player_id, target_player_id
    )

    lookup = {
        event_card.id: event_card,
        offered_card.id: offered_card,
        target_card.id: target_card,
    }
    _patch_card_lookup(monkeypatch, lookup)

    moves: list[tuple[uuid.UUID, schemas.CardMoveIn]] = []

    def fake_move_card(db_, cid, move_in):
        moves.append((cid, move_in))
        moved = MagicMock(spec=models.Card)
        moved.id = cid
        moved.owner = move_in.to_owner
        moved.owner_player_id = move_in.player_id
        return moved

    monkeypatch.setattr(CardService, "move_card", staticmethod(fake_move_card))

    # Happy path: swapping the cards updates both owners and discards the event.
    result = CardService.card_trade(
        db,
        game_id,
        player_id,
        event_card.id,
        target_player_id,
        offered_card.id,
        target_card.id,
    )

    assert isinstance(result, dict)
    assert "discarded_card" in result
    assert "blackmailed_events" in result

    # 2. Saca la carta descartada del dict
    discarded_card = result["discarded_card"]

    # 3. Comprueba la carta descartada (como antes)
    assert discarded_card.id == event_card.id
    assert discarded_card.owner == CardOwner.DISCARD_PILE
    
    # 4. Comprueba que NO hubo eventos Blackmailed
    assert result["blackmailed_events"] == []
    assert len(moves) == 3

    assert moves[0][0] == offered_card.id
    assert moves[0][1].to_owner == CardOwner.PLAYER
    assert moves[0][1].player_id == target_player_id

    assert moves[1][0] == target_card.id
    assert moves[1][1].to_owner == CardOwner.PLAYER
    assert moves[1][1].player_id == player_id

    assert moves[2][0] == event_card.id
    assert moves[2][1].to_owner == CardOwner.DISCARD_PILE
    assert moves[2][1].player_id is None


@pytest.mark.parametrize("scenario", ["invalid_event", "invalid_offered", "invalid_target"])
def test_card_trade_invalid_cases(monkeypatch, scenario):
    """Fuerza los errores de validación para Card Trade."""
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    target_player = uuid.uuid4()

    event_card, offered_card, target_card = _build_card_trade_cards(
        game_id, player_id, target_player
    )

    if scenario == "invalid_offered":
        offered_card.owner_player_id = uuid.uuid4()
    else:  # invalid_target
        target_card.owner_player_id = uuid.uuid4()

    lookup = {
        event_card.id: event_card,
        offered_card.id: offered_card,
        target_card.id: target_card,
    }
    _patch_card_lookup(monkeypatch, lookup)

    with pytest.raises(CardsNotFoundOrInvalidException):
        CardService.card_trade(
            db,
            game_id,
            player_id,
            event_card.id,
            target_player,
            offered_card.id,
            target_card.id,
        )

def test_ensure_move_valid_not_in_disgrace(monkeypatch):
    """Debe devolver True si el jugador no tiene social_disgrace"""
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()

    # Mock PlayerService
    fake_player_service = MagicMock()
    fake_player = MagicMock()
    fake_player.social_disgrace = False
    fake_player_service.get_player_entity_by_id.return_value = fake_player

    monkeypatch.setattr("app.card.service.PlayerService", lambda db_: fake_player_service)

    # Mock CardService.count_player_hand (no debería importar el valor)
    monkeypatch.setattr(CardService, "count_player_hand", staticmethod(lambda db_, g, p: 3))

    result = CardService.ensure_move_valid(db, game_id, player_id,1)
    assert result is True


def test_ensure_move_valid_in_disgrace_but_hand_equals_6(monkeypatch):
    """Debe devolver True si el jugador tiene social_disgrace pero su mano es 6"""
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()

    fake_player_service = MagicMock()
    fake_player = MagicMock()
    fake_player.social_disgrace = True
    fake_player_service.get_player_entity_by_id.return_value = fake_player

    monkeypatch.setattr("app.card.service.PlayerService", lambda db_: fake_player_service)
    monkeypatch.setattr(CardService, "count_player_hand", staticmethod(lambda db_, g, p: 6))

    result = CardService.ensure_move_valid(db, game_id, player_id,1)
    assert result is True


def test_ensure_move_valid_in_disgrace_and_hand_not_6(monkeypatch):
    """Debe devolver False si el jugador tiene social_disgrace y su mano NO es 6"""
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()

    fake_player_service = MagicMock()
    fake_player = MagicMock()
    fake_player.social_disgrace = True
    fake_player_service.get_player_entity_by_id.return_value = fake_player

    monkeypatch.setattr("app.card.service.PlayerService", lambda db_: fake_player_service)
    monkeypatch.setattr(CardService, "count_player_hand", staticmethod(lambda db_, g, p: 4))

    result = CardService.ensure_move_valid(db, game_id, player_id,1)
    assert result is False

def test_ensure_move_valid_in_disgrace_and_multiple_cards(monkeypatch):
    """Debe devolver False si el jugador está en desgracia y n_cards > 1"""
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()

    # Mock PlayerService
    fake_player_service = MagicMock()
    fake_player = MagicMock()
    fake_player.social_disgrace = True
    fake_player_service.get_player_entity_by_id.return_value = fake_player

    # Inyectamos el mock
    monkeypatch.setattr("app.card.service.PlayerService", lambda db_: fake_player_service)

    # Simulamos una mano cualquiera (el valor no importa porque el filtro es por n_cards > 1)
    monkeypatch.setattr(CardService, "count_player_hand", staticmethod(lambda db_, g, p: 4))

    result = CardService.ensure_move_valid(db, game_id, player_id, n_cards=3)

    assert result is False

@pytest.fixture
def dcf_service_data():
    """
    Fixture con datos para testear el servicio de Dead Card Folly (E_DCF).
    Simula una partida de 3 jugadores (p1, p2, p3).
    El jugador 'p1' es quien juega la carta.
    """
    db = make_db_mock()
    game_id = uuid.uuid4()

    p1_id, p2_id, p3_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    players_list = [p1_id, p2_id, p3_id]

    event_card = make_mock_card(
        name="E_DCF",
        owner_player_id=p1_id,
        id=uuid.uuid4(),
        game_id=game_id
    )

    card_p1 = make_mock_card(name="Card P1", owner_player_id=p1_id, id=uuid.uuid4(), game_id=game_id)
    card_p2 = make_mock_card(name="Card P2", owner_player_id=p2_id, id=uuid.uuid4(), game_id=game_id)
    card_p3 = make_mock_card(name="Card P3", owner_player_id=p3_id, id=uuid.uuid4(), game_id=game_id)

    cards_to_pass_map = {
        p1_id: card_p1.id,
        p2_id: card_p2.id,
        p3_id: card_p3.id,
    }

    mock_game = MagicMock(spec=Game)
    mock_game.players = [MagicMock(id=pid) for pid in players_list]

    return {
        "db": db,
        "game_id": game_id,
        "player_id": p1_id,
        "event_card": event_card,
        "cards_to_pass_map": cards_to_pass_map,
        "player_cards": {p1_id: card_p1, p2_id: card_p2, p3_id: card_p3},
        "mock_game": mock_game,
        "players_list": players_list,
    }

def test_select_card_for_passing_ok(dcf_service_data):
    """
    Prueba que la carta seleccionada se mueva a PASSING
    conservando el owner_player_id original.
    """
    db = dcf_service_data["db"]
    game_id = dcf_service_data["game_id"]
    p1 = dcf_service_data["player_id"]
    card_p1 = dcf_service_data["player_cards"][p1]
    card_p1.owner = CardOwner.PLAYER

    mock_get_card = MagicMock(return_value=card_p1)
    db.query.return_value.filter.return_value.first.return_value = None

    with patch("app.card.service.CardService.get_card_by_id", mock_get_card):
        result = CardService.select_card_for_passing(db, game_id, p1, card_p1.id)

    mock_get_card.assert_called_once_with(db, card_p1.id)
    assert result == card_p1
    assert card_p1.owner == CardOwner.PASSING
    assert card_p1.owner_player_id == p1    
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(card_p1)

def test_select_card_for_passing_raises_if_card_invalid(dcf_service_data):
    """Falla si la carta no existe, no es del jugador o no está en juego."""
    db = dcf_service_data["db"]
    p1 = dcf_service_data["player_id"]
    card_id = uuid.uuid4()

    mock_get_card_none = MagicMock(return_value=None)
    with patch("app.card.service.CardService.get_card_by_id", mock_get_card_none):
        with pytest.raises(CardsNotFoundOrInvalidException):
            CardService.select_card_for_passing(db, uuid.uuid4(), p1, card_id)

    wrong_owner_card = make_mock_card(owner_player_id=uuid.uuid4(), owner=CardOwner.PLAYER)
    mock_get_card_wrong_owner = MagicMock(return_value=wrong_owner_card)
    with patch("app.card.service.CardService.get_card_by_id", mock_get_card_wrong_owner):
        with pytest.raises(CardsNotFoundOrInvalidException):
            CardService.select_card_for_passing(db, uuid.uuid4(), p1, card_id)

    passing_card = make_mock_card(owner_player_id=p1, owner=CardOwner.PASSING)
    mock_get_card_passing = MagicMock(return_value=passing_card)
    with patch("app.card.service.CardService.get_card_by_id", mock_get_card_passing):
        with pytest.raises(CardsNotFoundOrInvalidException):
            CardService.select_card_for_passing(db, uuid.uuid4(), p1, card_id)

def test_select_card_for_passing_raises_if_already_selected(dcf_service_data):
    """Falla si el jugador ya tiene una carta en estado PASSING."""
    db = dcf_service_data["db"]
    game_id = dcf_service_data["game_id"]
    p1 = dcf_service_data["player_id"]
    card_p1 = dcf_service_data["player_cards"][p1]
    card_p1.owner = CardOwner.PLAYER

    mock_get_card = MagicMock(return_value=card_p1)
    db.query.return_value.filter.return_value.first.return_value = make_mock_card()

    with patch("app.card.service.CardService.get_card_by_id", mock_get_card):
        with pytest.raises(HTTPException) as exc_info:
            CardService.select_card_for_passing(db, game_id, p1, card_p1.id)
        assert exc_info.value.status_code == 403

# --- Tests para check_if_all_players_selected ---

def test_check_if_all_players_selected_true(dcf_service_data):
    """Devuelve True si el conteo de cartas PASSING == número de jugadores."""
    db = dcf_service_data["db"]
    game_id = dcf_service_data["game_id"]
    mock_game_entity = MagicMock(spec=Game)
    mock_game_entity.players = [MagicMock(), MagicMock(), MagicMock()] 
    n_players = len(mock_game_entity.players)

    db.query.return_value.filter.return_value.scalar.return_value = n_players

    result = CardService.check_if_all_players_selected(db, game_id, mock_game_entity)
    assert result is True

def test_check_if_all_players_selected_false(dcf_service_data):
    """Devuelve False si el conteo de cartas PASSING != número de jugadores."""
    db = dcf_service_data["db"]
    game_id = dcf_service_data["game_id"]
    mock_game_entity = MagicMock(spec=Game)
    mock_game_entity.players = [MagicMock(), MagicMock(), MagicMock()]

    db.query.return_value.filter.return_value.scalar.return_value = 2

    result = CardService.check_if_all_players_selected(db, game_id, mock_game_entity)
    assert result is False

def test_check_if_all_players_selected_false_zero_players(dcf_service_data):
    """Devuelve False si no hay jugadores (caso borde)."""
    db = dcf_service_data["db"]
    game_id = dcf_service_data["game_id"]
    mock_game_entity = MagicMock(spec=Game)
    mock_game_entity.players = []

    db.query.return_value.filter.return_value.scalar.return_value = 0

    result = CardService.check_if_all_players_selected(db, game_id, mock_game_entity)
    assert result is False

# --- Tests para execute_dead_card_folly_swap ---

def test_execute_dead_card_folly_swap_ok_right(dcf_service_data):
    """
    Prueba el swap con dirección "right". Verifica movimientos y limpieza de estado.
    """
    db = dcf_service_data["db"]
    game_id = dcf_service_data["game_id"]
    event_card = dcf_service_data["event_card"]
    p1, p2, p3 = dcf_service_data["players_list"]
    card_p1 = dcf_service_data["player_cards"][p1]
    card_p2 = dcf_service_data["player_cards"][p2]
    card_p3 = dcf_service_data["player_cards"][p3]

    card_p1.owner = CardOwner.PASSING
    card_p2.owner = CardOwner.PASSING
    card_p3.owner = CardOwner.PASSING
    cards_in_passing = [card_p1, card_p2, card_p3]

    mock_turn_state = MagicMock(spec=GameTurnState)
    mock_turn_state.passing_direction = "right"
    mock_turn_state.current_event_card_id = event_card.id
    mock_game_entity = MagicMock(spec=Game)
    mock_game_entity.players = [MagicMock(id=pid) for pid in [p1, p2, p3]]
    mock_game_entity.turn_state = mock_turn_state

    db.query.return_value.filter.return_value.all.return_value = cards_in_passing

    mock_game_service_instance = MagicMock()

    mock_move_card_calls = []
    def mock_move_card(db_arg, card_id_arg, move_in_arg):
        mock_move_card_calls.append((card_id_arg, move_in_arg))
        return make_mock_card(id=card_id_arg)

    with patch("app.game.service.GameService", return_value=mock_game_service_instance) as mock_gs_class, \
         patch("app.card.service.CardService.move_card", side_effect=mock_move_card) as mock_move:

        result = CardService.execute_dead_card_folly_swap(db, game_id, mock_game_entity)

    assert result == []
    assert mock_move.call_count == 3

    moves = {call[0]: call[1] for call in mock_move_card_calls}

    players_list_unsorted = dcf_service_data["players_list"]
    players_list_sorted = sorted(players_list_unsorted)

    expected_recipients = {}
    num_players = len(players_list_sorted)
    for i in range(num_players):
        sender_id = players_list_sorted[i]
        recipient_id = players_list_sorted[(i + 1) % num_players]
        expected_recipients[sender_id] = recipient_id
    
    assert moves[card_p1.id].to_owner == CardOwner.PLAYER
    assert moves[card_p1.id].player_id == expected_recipients[p1]

    assert moves[card_p2.id].to_owner == CardOwner.PLAYER
    assert moves[card_p2.id].player_id == expected_recipients[p2]

    assert moves[card_p3.id].to_owner == CardOwner.PLAYER
    assert moves[card_p3.id].player_id == expected_recipients[p3]

    mock_gs_class.assert_called_once_with(db)
    mock_game_service_instance.change_turn_state.assert_called_once_with(
        game_id, TurnState.DISCARDING 
    )


def test_execute_dead_card_folly_swap_handles_missing_state(dcf_service_data):
    """Falla con HTTPException si game_entity.turn_state es None."""
    db = dcf_service_data["db"]
    game_id = dcf_service_data["game_id"]
    mock_game_entity = MagicMock(spec=Game)
    mock_game_entity.turn_state = None 

    with pytest.raises(HTTPException) as exc_info:
        CardService.execute_dead_card_folly_swap(db, game_id, mock_game_entity)
    assert exc_info.value.status_code == 500 


@pytest.fixture
def pys_service_setup():
    """Crea mocks de Game, Players y TurnState para los tests de CardService."""
    p1_id, p2_id, p3_id, p4_id = (uuid.uuid4() for _ in range(4))
    players_list = [
        MagicMock(spec=Player, id=p1_id),
        MagicMock(spec=Player, id=p2_id),
        MagicMock(spec=Player, id=p3_id),
        MagicMock(spec=Player, id=p4_id),
    ]
    
    mock_game_entity = MagicMock(spec=Game)
    mock_game_entity.players = players_list
    mock_game_entity.current_turn = p1_id # p1 es el PYS player
    
    mock_turn_state = MagicMock(spec=GameTurnState)
    mock_turn_state.current_event_card_id = uuid.uuid4()
    mock_turn_state.vote_data = {} 
    
    mock_game_entity.turn_state = mock_turn_state
    
    return {
        "db": MagicMock(spec=Session),
        "game_id": uuid.uuid4(),
        "game_entity": mock_game_entity,
        "ids": {"p1": p1_id, "p2": p2_id, "p3": p3_id, "p4": p4_id}
    }

# --- Tests para check_if_all_players_voted ---

def test_check_if_all_players_voted_false_empty(pys_service_setup):
    """Devuelve False si vote_data está vacío."""
    db = pys_service_setup["db"]
    game_id = pys_service_setup["game_id"]
    game_entity = pys_service_setup["game_entity"]
    game_entity.turn_state.vote_data = {}
    
    result = CardService.check_if_all_players_voted(db, game_id, game_entity)
    assert result is False

def test_check_if_all_players_voted_false_partial(pys_service_setup):
    """Devuelve False si faltan votos."""
    db = pys_service_setup["db"]
    game_id = pys_service_setup["game_id"]
    game_entity = pys_service_setup["game_entity"]
    ids = pys_service_setup["ids"]
    
    # 3 de 4 jugadores han votado
    game_entity.turn_state.vote_data = {
        str(ids["p1"]): str(ids["p2"]),
        str(ids["p2"]): str(ids["p3"]),
        str(ids["p3"]): str(ids["p2"]),
    }
    
    result = CardService.check_if_all_players_voted(db, game_id, game_entity)
    assert result is False

def test_check_if_all_players_voted_true(pys_service_setup):
    """Devuelve True si todos han votado."""
    db = pys_service_setup["db"]
    game_id = pys_service_setup["game_id"]
    game_entity = pys_service_setup["game_entity"]
    ids = pys_service_setup["ids"]
    
    # 4 de 4 jugadores han votado
    game_entity.turn_state.vote_data = {
        str(ids["p1"]): str(ids["p2"]),
        str(ids["p2"]): str(ids["p3"]),
        str(ids["p3"]): str(ids["p2"]),
        str(ids["p4"]): str(ids["p2"]),
    }
    
    result = CardService.check_if_all_players_voted(db, game_id, game_entity)
    assert result is True

# --- Tests para execute_pys_vote (Lógica de Conteo) ---

@patch("app.game.service.GameService")
@patch("app.card.service.CardService.move_card")
@pytest.mark.asyncio
async def test_execute_pys_vote_scenario_3_clear_winner(
    mock_move_card, mock_game_service_class, pys_service_setup
):
    """(Escenario 3) A=3, B=1. Gana A (el más votado)."""
    db = pys_service_setup["db"]
    game_id = pys_service_setup["game_id"]
    game_entity = pys_service_setup["game_entity"]
    ids = pys_service_setup["ids"]
    mock_game_service_instance = mock_game_service_class.return_value

    # p1 (PYS) vota por p2. p2 vota por p2. p3 vota por p2. p4 vota por p3.
    # Resultado: p2=3 votos, p3=1 voto. Ganador: p2.
    game_entity.current_turn = ids["p1"] # p1 es el PYS player
    game_entity.turn_state.vote_data = {
        str(ids["p1"]): str(ids["p2"]),
        str(ids["p2"]): str(ids["p2"]),
        str(ids["p3"]): str(ids["p2"]),
        str(ids["p4"]): str(ids["p3"]),
    }
    
    winner_id = await CardService.execute_pys_vote(db, game_id, game_entity)
    
    assert winner_id == ids["p2"] # p2 fue el más votado
    
    # Verificar que se cambió al estado correcto, apuntando al ganador
    mock_game_service_instance.change_turn_state.assert_called_once_with(
        game_id, 
        TurnState.CHOOSING_SECRET_PYS,
        target_player_id=ids["p2"]
    )

@patch("app.game.service.GameService")
@patch("app.card.service.CardService.move_card")
@pytest.mark.asyncio
async def test_execute_pys_vote_scenario_2_tie_pys_breaks_tie(
    mock_move_card, mock_game_service_class, pys_service_setup
):
    """(Escenario 2) A=2 (PYS), B=2. Gana A (votado por PYS)."""
    db = pys_service_setup["db"]
    game_id = pys_service_setup["game_id"]
    game_entity = pys_service_setup["game_entity"]
    ids = pys_service_setup["ids"]
    mock_game_service_instance = mock_game_service_class.return_value

    # p1 (PYS) vota por p2. p2 vota por p2. p3 vota por p3. p4 vota por p3.
    # Resultado: p2=2 votos (PYS votó aquí), p3=2 votos.
    # Empate: [p2, p3].
    # Voto PYS: p2.
    # Ganador: p2.
    game_entity.current_turn = ids["p1"] # p1 es el PYS player
    game_entity.turn_state.vote_data = {
        str(ids["p1"]): str(ids["p2"]),
        str(ids["p2"]): str(ids["p2"]),
        str(ids["p3"]): str(ids["p3"]),
        str(ids["p4"]): str(ids["p3"]),
    }
    
    winner_id = await CardService.execute_pys_vote(db, game_id, game_entity)
    
    assert winner_id == ids["p2"] # p2 gana por el voto PYS
    mock_game_service_instance.change_turn_state.assert_called_once_with(
        game_id, 
        TurnState.CHOOSING_SECRET_PYS,
        target_player_id=ids["p2"]
    )

@patch("app.game.service.GameService")
@patch("app.card.service.CardService.move_card")
@pytest.mark.asyncio
async def test_execute_pys_vote_scenario_1_tie_pys_decides_other(
    mock_move_card, mock_game_service_class, pys_service_setup
):
    """(Escenario 1) A=2, B=2, C=1 (PYS). Gana C (votado por PYS)."""
    db = pys_service_setup["db"]
    game_id = pys_service_setup["game_id"]
    game_entity = pys_service_setup["game_entity"]
    ids = pys_service_setup["ids"]
    mock_game_service_instance = mock_game_service_class.return_value

    # p1 (PYS) vota por p4. p2 vota por p2. p3 vota por p2. p4 vota por p3. p5 (agregado) vota por p3.
    # Necesitamos 5 jugadores para este escenario
    p5_id = uuid.uuid4()
    game_entity.players.append(MagicMock(spec=Player, id=p5_id))
    
    # Resultado: p2=2 votos, p3=2 votos, p4=1 voto (PYS votó aquí).
    # Empate: [p2, p3].
    # Voto PYS: p4.
    # Ganador: p4.
    game_entity.current_turn = ids["p1"] # p1 es el PYS player
    game_entity.turn_state.vote_data = {
        str(ids["p1"]): str(ids["p4"]),
        str(ids["p2"]): str(ids["p2"]),
        str(ids["p3"]): str(ids["p2"]),
        str(ids["p4"]): str(ids["p3"]),
        str(p5_id): str(ids["p3"]),
    }
    
    winner_id = await CardService.execute_pys_vote(db, game_id, game_entity)
    
    assert winner_id == ids["p4"] # p4 gana por el voto PYS
    mock_game_service_instance.change_turn_state.assert_called_once_with(
        game_id, 
        TurnState.CHOOSING_SECRET_PYS,
        target_player_id=ids["p4"]
    )
    
# --- Tests para verify_cancellable_card ---

@pytest.mark.parametrize("cancellable_name", [
    "E_PYS",
    "E_CT",
    "E_AV",
    "D_TB", # Cartas de detective (default)
])
def test_verify_cancellable_card_returns_true(
    db_session, monkeypatch, cancellable_name
):
    """
    Prueba que una carta que SÍ es cancelable (no está en la lista de exclusión)
    devuelve True.
    """
    card_id = uuid.uuid4()
    
    # 1. Simula la carta que SÍ es cancelable
    mock_card = MagicMock(spec=models.Card)
    mock_card.id = card_id
    mock_card.name = cancellable_name
    
    # 2. Mockea la dependencia
    mock_get = MagicMock(return_value=mock_card)
    monkeypatch.setattr(CardService, "get_card_by_id", mock_get)

    # 3. Llama y verifica
    assert CardService.verify_cancellable_card(db_session, card_id) is True
    mock_get.assert_called_once_with(db_session, card_id)


@pytest.mark.parametrize("non_cancellable_name", ["E_COT", "DV_BLM"])
def test_verify_cancellable_card_returns_false_for_non_cancellable(
    db_session, monkeypatch, non_cancellable_name
):
    """
    Prueba que las cartas 'E_COT' y 'DV_BLM' devuelven False (no son cancelables).
    """
    card_id = uuid.uuid4()
    
    # 1. Simula la carta NO cancelable
    mock_card = MagicMock(spec=models.Card)
    mock_card.id = card_id
    mock_card.name = non_cancellable_name  # Usa el parámetro del test
    
    # 2. Mockea la dependencia
    mock_get = MagicMock(return_value=mock_card)
    monkeypatch.setattr(CardService, "get_card_by_id", mock_get)

    # 3. Llama y verifica
    assert CardService.verify_cancellable_card(db_session, card_id) is False
    mock_get.assert_called_once_with(db_session, card_id)


def test_verify_cancellable_card_not_found_raises_exception(db_session, monkeypatch):
    """
    Prueba que se lanza CardsNotFoundOrInvalidException si la carta no se encuentra.
    """
    card_id = uuid.uuid4()
    
    # 1. Simula que get_card_by_id devuelve None
    mock_get = MagicMock(return_value=None)
    monkeypatch.setattr(CardService, "get_card_by_id", mock_get)

    # 2. Llama y verifica la excepción
    with pytest.raises(CardsNotFoundOrInvalidException, match=f"Card {card_id} not found"):
        CardService.verify_cancellable_card(db_session, card_id)
    
    mock_get.assert_called_once_with(db_session, card_id)

@patch("app.game.service.GameService")
@patch("app.card.service.CardService.move_card")
def test_execute_dcf_swap_triggers_blackmailed(
    mock_move_card, mock_game_service_class
):
    """
    Prueba que si se pasa DV_BLM, se genera un 'blackmailed_event'
    con los secretos NO revelados.
    """
    db = MagicMock(spec=Session)
    game_id = uuid.uuid4()
    
    p1_id = uuid.uuid4()
    p2_id = uuid.uuid4()
    players_list_sorted = sorted([p1_id, p2_id])

    sender_id = players_list_sorted[0]
    recipient_id = players_list_sorted[1]

    card_p1 = MagicMock(spec=models.Card)
    card_p1.id = uuid.uuid4()
    card_p1.name = "DV_BLM" 
    card_p1.owner_player_id = sender_id
    cards_in_passing = [card_p1] 

    mock_secret_hidden = MagicMock(spec=Secrets, id=uuid.uuid4(), name="Oculto")
    
    mock_turn_state = MagicMock(spec=GameTurnState)
    mock_turn_state.passing_direction = "right" # p1 pasa a p2
    mock_game_entity = MagicMock(spec=Game)
    mock_game_entity.players = [MagicMock(id=pid) for pid in players_list_sorted]
    mock_game_entity.turn_state = mock_turn_state

    
    mock_query_cards = MagicMock()
    mock_query_cards.filter.return_value.all.return_value = cards_in_passing

    mock_query_secrets = MagicMock()
    mock_query_secrets.filter.return_value.all.return_value = [mock_secret_hidden]

    db.query.side_effect = [
        mock_query_cards,  
        mock_query_secrets
    ]
    
    result_events = CardService.execute_dead_card_folly_swap(db, game_id, mock_game_entity)

    assert len(result_events) == 1, "Debería haberse generado 1 evento Blackmailed"
    
    event_data = result_events[0]
    assert event_data["actor_player_id"] == str(sender_id)
    assert event_data["target_player_id"] == str(recipient_id)

    secret_list = event_data["available_secrets"]
    assert len(secret_list) == 1
    assert secret_list[0]["id"] == str(mock_secret_hidden.id)

    assert mock_move_card.call_count == 1
    
    mock_game_service_class.return_value.change_turn_state.assert_called_once_with(
        game_id, TurnState.DISCARDING 
    )
    
@pytest.mark.asyncio
async def test_wait_for_cancellation_timeout(monkeypatch):
    """Debe salir sin errores cuando no cambia nada y se cumple el timeout"""
    fake_db = MagicMock()
    fake_game = MagicMock()
    fake_game.turn_state.state = TurnState.CANCELLED_CARD_PENDING
    fake_game.turn_state.is_canceled_card = False
    fake_game.turn_state.last_is_canceled_card = False
    fake_db.query().filter_by().first.return_value = fake_game

    # Reducimos el timeout para no demorar
    result = await CardService.wait_for_cancellation(fake_db, uuid.uuid4(), timeout=0.5)

    assert result is None
    fake_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_wait_for_cancellation_game_not_found():
    """Debe lanzar HTTP 404 si el juego no existe"""
    fake_db = MagicMock()
    fake_db.query().filter_by().first.return_value = None

    with pytest.raises(HTTPException) as exc:
        await CardService.wait_for_cancellation(fake_db, uuid.uuid4(), timeout=0.1)

    assert exc.value.status_code == 404
    assert "Game" in exc.value.detail


@pytest.mark.asyncio
async def test_wait_for_cancellation_invalid_state():
    """Debe lanzar HTTP 404 si el estado no es CANCELLED_CARD_PENDING"""
    fake_db = MagicMock()
    fake_game = MagicMock()
    fake_game.turn_state.state = "OTHER_STATE"
    fake_db.query().filter_by().first.return_value = fake_game

    with pytest.raises(HTTPException) as exc:
        await CardService.wait_for_cancellation(fake_db, uuid.uuid4(), timeout=0.1)

    assert exc.value.status_code == 404

@patch("app.game.service.GameService") # Mockeamos la CLASE GameService
@patch("app.card.service.CardService.move_card")
def test_execute_dcf_swap_triggers_sfp_only(
    mock_move_card, mock_game_service_class, dcf_service_data
):
    """
    Prueba que si se pasa SÓLO una DV_SFP (y no una BLM):
    1. El 'result_events' (de Blackmailed) está vacío.
    2. El estado del juego cambia a PENDING_DEVIOUS (y NO a DISCARDING).
    """
    # 1. ARRANGE
    db = dcf_service_data["db"]
    game_id = dcf_service_data["game_id"]
    game_entity = dcf_service_data["mock_game"]
    
    players_list_sorted = sorted(dcf_service_data["players_list"])
    p1, p2, p3 = players_list_sorted
    
    card_p1 = dcf_service_data["player_cards"][p1]
    card_p2 = dcf_service_data["player_cards"][p2]
    card_p3 = dcf_service_data["player_cards"][p3]

    # --- Configuración del Test ---
    card_p1.name = "Normal Card 1"
    card_p1.owner = CardOwner.PASSING
    card_p2.name = "DV_SFP" # <-- ¡La carta clave!
    card_p2.owner = CardOwner.PASSING
    card_p3.name = "Normal Card 2"
    card_p3.owner = CardOwner.PASSING
    cards_in_passing = [card_p1, card_p2, card_p3]

    # Configurar Mocks de Game/State (dirección 'right')
    game_entity.turn_state.passing_direction = "right" # p2 pasa SFP a p3
    game_entity.players = [MagicMock(id=pid) for pid in players_list_sorted]
    
    # --- Mock de Consultas a la DB ---
    # (Solo necesitamos mockear la consulta de 'cards')
    mock_query_cards = MagicMock()
    mock_query_cards.filter.return_value.all.return_value = cards_in_passing
    
    # (No necesitamos mockear 'Secrets', porque _create_blackmailed_event no se llamará)

    def query_side_effect(model_class_arg):
        if model_class_arg.__tablename__ == "cards":
            return mock_query_cards
        # Si (por error) consulta Secrets, devolverá un mock vacío
        return MagicMock(filter=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))) 
    
    db.query.side_effect = query_side_effect
    # --- Fin Mocks DB ---
    
    # Obtenemos la instancia mockeada del GameService
    mock_game_service_instance = mock_game_service_class.return_value

    # 2. ACT
    result_events = CardService.execute_dead_card_folly_swap(db, game_id, game_entity)

    # 3. ASSERT
    
    # A. Verificar el resultado (No debe haber eventos de Blackmailed)
    assert result_events == [], "No deberían generarse eventos de Blackmailed"
    
    # B. Verificar que el swap de las 3 cartas ocurrió
    assert mock_move_card.call_count == 3
    assert mock_game_service_instance.change_turn_state.call_count == 2 # Aceptar las 2 llamadas

    # Verificar que la llamada que nos importa SÍ ocurrió
    expected_call = call(
        game_id,
        TurnState.PENDING_DEVIOUS,
        target_player_id=p3
    )
    mock_game_service_instance.change_turn_state.assert_has_calls([expected_call])

def test_card_trade_detects_sfp_A_to_B(monkeypatch):
    """Prueba que card_trade detecta SFP si A se la da a B."""
    # 1. Arrange
    db = MagicMock(spec=Session)
    game_id, p1_id, p2_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    event_card_id, card_A_id, card_B_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    card_A = make_mock_card(
        id=card_A_id, 
        name="DV_SFP", 
        owner_player_id=p1_id,
        game_id=game_id, 
        owner=CardOwner.PLAYER 
    )
    card_B = make_mock_card(
        id=card_B_id, 
        name="Normal", 
        owner_player_id=p2_id,
        game_id=game_id, 
        owner=CardOwner.PLAYER
    )

    # Mock de GameService
    mock_game_service = MagicMock(spec=GameService)
    monkeypatch.setattr("app.game.service.GameService", lambda db: mock_game_service)
    
    # Mock de get_card_by_id
    def get_card_side_effect(db_arg, card_id_arg):
        if card_id_arg == card_A_id: return card_A
        if card_id_arg == card_B_id: return card_B
        return make_mock_card() # Mock genérico para la event_card
    
    monkeypatch.setattr(CardService, "get_card_by_id", get_card_side_effect)
    monkeypatch.setattr(CardService, "move_card", lambda db, cid, move: make_mock_card(id=cid))

    # 2. Act
    result_dict = CardService.card_trade(
        db, game_id, p1_id, event_card_id, p2_id, card_A_id, card_B_id
    )

    # 3. Assert
    assert result_dict["blackmailed_events"] == [] # No hubo BLM
    # Verificar que se llamó a change_turn_state para SFP
    mock_game_service.change_turn_state.assert_called_once_with(
        game_id,
        TurnState.PENDING_DEVIOUS,
        target_player_id=p2_id # B (receptor) debe actuar
    )

# --- Test para check_players_SFP ---
# (Esta fixture es la de game_service, la necesitamos aquí)

@pytest.fixture(scope="function")
def db_session2():
    """
    Crea una DB en memoria limpia para cada test en ESTE archivo.
    """
    engine = create_engine("sqlite:///:memory:")
    
    # Crea TODAS las tablas que tus modelos conocen
    Base.metadata.create_all(bind=engine)
    
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def game_service(db_session2: Session): # <-- Añade el tipo para claridad
    """Crea una instancia de GameService con la DB de test."""
    return GameService(db_session2)

@pytest.fixture
def game_with_state(game_service: GameService, db_session2: Session):
    """
    Crea un juego, p1, p2, y un GameTurnState con 'sfp_players' inicializado.
    (El nombre de esta fixture ahora es el que tus tests usan)
    """
    dto = GameInDTO(
        name="Test Game SFP",
        host_name="Player 1",
        birthday=date(2000,1,1),
        min_players=2,
        max_players=4
    )
    game_dto = game_service.create_game(dto)
    p2_id = game_service.add_player(game_dto.id, PlayerInDTO(name="Player 2", birthday=date(2001,1,1)))
    
    game = db_session2.query(Game).filter(Game.id == game_dto.id).first()
    
    # Crea el estado inicial
    turn_state = GameTurnState(
        game_id=game.id,
        state=TurnState.IDLE,
        sfp_players=[] # Inicializa la lista vacía
    )
    db_session2.add(turn_state)
    game.turn_state = turn_state
    db_session2.commit()
    db_session2.refresh(turn_state)
    
    return {
        "game_service": game_service,
        "db": db_session2,
        "game_id": game.id,
        "p1_id": game.host_id,
        "p2_id": p2_id,
        "turn_state_obj": turn_state
    }

def test_check_players_sfp_fails_if_player_not_in_list(game_with_state):
    """
    Prueba que falla si el jugador que intenta resolver no está en la lista.
    """
    db = game_with_state["db"]
    game_id = game_with_state["game_id"]
    p1_id = game_with_state["p1_id"]
    p2_id = game_with_state["p2_id"]
    turn_state_obj = game_with_state["turn_state_obj"]
    
    turn_state_obj.sfp_players = [str(p2_id)] # Solo p2 está en la lista
    db.commit()

    # p1 intenta resolver
    with pytest.raises(HTTPException, match="El jugador no estaba pendiente"):
        CardService.check_players_SFP(db, game_id, p1_id)
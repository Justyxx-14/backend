import uuid
import pytest
from unittest.mock import MagicMock, patch, ANY
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from fastapi import HTTPException
from pydantic_core import ValidationError

from app.card.service import CardService
from app.card import models, schemas
from app.card.enums import CardOwner, CardType
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
from app.set.exceptions import SetNotFound
from app.secret.service import SecretService

def make_db_mock() -> MagicMock:
    """
    Devuelve un 'Session' mockeado con la cadena query->filter->first/all y
    add/add_all/commit/refresh. 
    """
    db = MagicMock()
    q = db.query.return_value
    f = q.filter.return_value
    f.first.return_value = None
    f.all.return_value = []
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


# Test look into the ashes

def test_look_into_the_ashes_card_not_in_discard():
    db = MagicMock()
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    event_id = uuid.uuid4()
    mock_cards = [make_mock_discard_pile(o) for o in [7,6,5,4,3]]

    fake_card_id = uuid.uuid4()
    with patch("app.card.service.CardService.see_top_discard", return_value=mock_cards):
        with pytest.raises(CardsNotFoundOrInvalidException):
            CardService.look_into_the_ashes(db, game_id, event_id,card_id=fake_card_id, player_id=player_id)


# Helper
def make_mock_card(name="E_LIA", owner=CardOwner.PLAYER, owner_player_id=None, id=None):
    c = MagicMock(spec=models.Card)
    c.id = id or uuid.uuid4()
    c.name = name
    c.owner = owner
    c.owner_player_id = owner_player_id
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
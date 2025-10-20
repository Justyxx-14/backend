import uuid
import pytest
from pydantic import TypeAdapter, ValidationError
from ..schemas import CardIn, CardBatchIn, CardResponse, CardOut, CardMoveIn, CardMoveOut
from ..enums import CardType, CardOwner

# --- CardIn --------------------------------------------------------------

def test_cardin_ok():
    c = CardIn(type=CardType.EVENT, name="Early Train to Paddington", 
               description="Take the top six cards from the draw pile"
               " and place them face-up on the discard pile, then remove this card from the game.")
    assert c.type == CardType.EVENT
    assert 1 <= len(c.name) <= 80
    assert 1 <= len(c.description) <= 255


def test_cardin_name_too_short():
    with pytest.raises(ValidationError):
        CardIn(type=CardType.EVENT, name="", description="test")


def test_cardin_name_too_long():
    with pytest.raises(ValidationError):
        CardIn(type=CardType.EVENT, name="*"*81, description="test")


def test_cardin_desc_too_short():
    with pytest.raises(ValidationError):
        CardIn(type=CardType.EVENT, name="test", description="")


def test_cardin_desc_too_long():
    with pytest.raises(ValidationError):
        CardIn(type=CardType.EVENT, name="test", description="*"*256)

# --- CardBatchIn ---------------------------------------------------------

def test_cardbatchin_default_empty():
    batch = CardBatchIn()
    assert batch.items == []


def test_cardbatchin_with_items():
    batch = CardBatchIn(items=[
        CardIn(type=CardType.EVENT, name="A", description="a"),
        CardIn(type=CardType.DEVIOUS, name="B", description="b"),
    ])
    assert len(batch.items) == 2


def test_cardbatchin_empty():
    batch = CardBatchIn(items=[])
    assert len(batch.items) == 0


# --- CardResponse ---------------------------------------------------------
def test_cardresponse_ok():
    rid = uuid.uuid4()
    resp = CardResponse(id=rid)
    assert resp.id == rid


def test_cardresponse_invalid_id():
    with pytest.raises(ValidationError):
        CardResponse(id="not-a-uuid")


def test_cardresponse_list_parse():
    data = [
        {"id": str(uuid.uuid4())},
        {"id": str(uuid.uuid4())},
        {"id": str(uuid.uuid4())},
    ]
    ta = TypeAdapter(list[CardResponse]) 
    items = ta.validate_python(data)
    assert len(items) == 3
    assert all(isinstance(x.id, uuid.UUID) for x in items)


# --- CardOut -------------------------------------------------------------

def test_cardout_shape():
    co = CardOut(
        id=uuid.uuid4(),
        game_id=uuid.uuid4(),
        type=CardType.DETECTIVE,
        name="Hercules Poirot",
        description="Necesita resolver el caso",
        owner=CardOwner.DECK,
        owner_player_id=None
    )
    assert co.id is not None
    assert co.game_id is not None
    assert co.type == CardType.DETECTIVE
    assert co.name == "Hercules Poirot"
    assert co.description == "Necesita resolver el caso"
    assert co.owner == CardOwner.DECK
    assert co.owner_player_id == None

# --- CardMoveOut ---------------------------------------

def test_cardmove_requires_player_when_owner_player():
    with pytest.raises((ValidationError, ValueError)):
        CardMoveOut(id=uuid.uuid4(), to_owner=CardOwner.PLAYER, player_id=None)


def test_cardmove_forbid_player_id_when_not_player():
    with pytest.raises((ValidationError, ValueError)):
        CardMoveOut(id=uuid.uuid4(), to_owner=CardOwner.DECK, player_id=uuid.uuid4())


def test_cardmove_ok_player():
    pid = uuid.uuid4()
    cm = CardMoveOut(id=uuid.uuid4(), to_owner=CardOwner.PLAYER, player_id=pid)
    assert cm.player_id == pid


def test_cardmove_ok_deck():
    cm = CardMoveOut(id=uuid.uuid4(), to_owner=CardOwner.DECK, player_id=None)
    assert cm.player_id is None


# --- CardMoveIn (request/body) ------------------------------------
def test_cardmovein_requires_player_when_owner_player():
    with pytest.raises((ValidationError, ValueError)):
        CardMoveIn(to_owner=CardOwner.PLAYER, player_id=None)

def test_cardmovein_forbid_player_id_when_not_player():
    with pytest.raises((ValidationError, ValueError)):
        CardMoveIn(to_owner=CardOwner.DECK, player_id=uuid.uuid4())

def test_cardmovein_ok_player():
    pid = uuid.uuid4()
    cm = CardMoveIn(to_owner=CardOwner.PLAYER, player_id=pid)
    assert cm.player_id == pid

def test_cardmovein_ok_deck():
    cm = CardMoveIn(to_owner=CardOwner.DECK, player_id=None)
    assert cm.player_id is None
import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.card import service as card_service
from app.card.service import CardService
from app.card import schemas
from app.card.models import Card, CardOwner
from app.card.exceptions import PlayerHandLimitExceededException, NoCardsException

@pytest.fixture
def mock_db():
    return MagicMock()

@pytest.fixture
def mock_card():
    return Card(
        id=uuid4(),
        game_id=uuid4(),
        type="ATTACK",
        name="Fireball",
        description="Deal 3 damage",
        owner=CardOwner.DECK
    )

# ----------------------------------
# Test initialize_draft
# ----------------------------------
def test_initialize_draft_creates_draft(mock_db, mock_card):
    # draft vacío
    mock_db.query.return_value.filter.return_value.first.return_value = None
    # deck con cartas
    mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [mock_card]

    with patch.object(CardService, 'move_card', return_value=None) as mock_move:
        result = CardService.initialize_draft(mock_db, mock_card.game_id)

    assert result == [mock_card]
    mock_move.assert_called_once_with(mock_db, mock_card.id, schemas.CardMoveIn(to_owner=CardOwner.DRAFT))

def test_initialize_draft_already_has_draft(mock_db, mock_card):
    # draft no vacío
    mock_db.query.return_value.filter.return_value.first.return_value = mock_card

    result = CardService.initialize_draft(mock_db, mock_card.game_id)
    assert result is None

def test_initialize_draft_no_cards_in_deck(mock_db, mock_card):
    mock_db.query.return_value.filter.return_value.first.return_value = None
    # deck vacío
    mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

    result = CardService.initialize_draft(mock_db, mock_card.game_id)
    assert result is None

# ----------------------------------
# Test pick_draft
# ----------------------------------
def test_pick_draft_success(mock_db, mock_card):
    player_id = uuid4()
    card_id = mock_card.id

    # menos de 6 cartas en mano
    with patch.object(CardService, 'count_player_hand', return_value=3), \
         patch.object(CardService, 'move_card', return_value=None):
        mock_db.query.return_value.filter.return_value.one_or_none.return_value = mock_card
        result, _ = CardService.pick_draft(mock_db, mock_card.game_id, player_id, card_id)

    assert result == mock_card

def test_pick_draft_hand_limit_exceeded(mock_db, mock_card):
    player_id = uuid4()
    card_id = mock_card.id

    with patch.object(CardService, 'count_player_hand', return_value=6):
        with pytest.raises(PlayerHandLimitExceededException):
            CardService.pick_draft(mock_db, mock_card.game_id, player_id, card_id)

def test_pick_draft_card_not_found(mock_db, mock_card):
    player_id = uuid4()
    card_id = uuid4()  # ID diferente
    with patch.object(CardService, 'count_player_hand', return_value=3):
        mock_db.query.return_value.filter.return_value.one_or_none.return_value = None
        with pytest.raises(NoCardsException):
            CardService.pick_draft(mock_db, mock_card.game_id, player_id, card_id)

# ----------------------------------
# Test update_draft
# ----------------------------------
def test_update_draft_adds_missing_cards(mock_db, mock_card):
    draft_card = Card(id=uuid4(), game_id=mock_card.game_id, owner=CardOwner.DRAFT)
    deck_card = mock_card

    # Mock para db.query().filter().all() de draft_cards
    mock_draft_query = MagicMock()
    mock_draft_query.filter.return_value.all.return_value = [draft_card]

    # Mock para db.query().filter().order_by().limit().all() de top_cards
    mock_deck_query = MagicMock()
    mock_deck_query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [deck_card]

    # Mock 3: db.query(func.count...).filter().scalar() para el conteo final
    mock_count_query = MagicMock()
    mock_count_query.filter.return_value.scalar.return_value = 5

    # db.query() devuelve primero draft, luego deck, luego el conteo
    mock_db.query.side_effect = [mock_draft_query, mock_deck_query, mock_count_query]

    with patch.object(CardService, 'move_card', return_value=None) as mock_move:
        result = CardService.update_draft(mock_db, mock_card.game_id)

    # Ahora result es realmente la lista de top_cards
    assert result is False
    mock_move.assert_called_once_with(mock_db, deck_card.id, schemas.CardMoveIn(to_owner=CardOwner.DRAFT))


# ----------------------------------
# Test query_draft
# ----------------------------------
def test_query_draft_returns_cards(mock_db, mock_card):
    mock_db.query.return_value.filter.return_value.all.return_value = [mock_card]
    result = CardService.query_draft(mock_db, mock_card.game_id)
    assert result == [mock_card]

def test_query_draft_empty(mock_db, mock_card):
    mock_db.query.return_value.filter.return_value.all.return_value = []
    result = CardService.query_draft(mock_db, mock_card.game_id)
    assert result is None

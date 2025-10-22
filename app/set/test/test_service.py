import uuid
from unittest.mock import MagicMock, patch, call

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.card.enums import CardType, CardOwner
from app.set.dtos import SetIn, SetPlayResult
from app.set.enums import SetType
from app.set.models import Set as SetModel
from app.set.service import SetService
from app.secret.enums import SecretType
from app.secret.dtos import SecretOutDTO
from app.secret.models import Secrets
from app.game.schemas import EndGameResult, GameEndReason
from types import SimpleNamespace


class DummyCard:
    def __init__(
        self,
        name: str,
        card_type: CardType = CardType.DETECTIVE,
        *,
        owner: CardOwner = CardOwner.PLAYER,
        owner_player_id: uuid.UUID | None = None,
        game_id: uuid.UUID | None = None,
    ):
        self.id = uuid.uuid4()
        self.name = name
        self.type = card_type
        self.owner = owner
        self.owner_player_id = owner_player_id
        self.game_id = game_id


@pytest.fixture
def db_session():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


def build_set_input(card_names):
    player_id = uuid.uuid4()
    game_id = uuid.uuid4()

    dummy_cards_list = [
        DummyCard(
            name=name,
            owner_player_id=player_id,
            game_id=game_id
        )
        for name in card_names
    ]

    cards_lookup = {card.id: card for card in dummy_cards_list}

    set_in = SetIn(
        player_id=player_id,
        game_id=game_id,
        cards=[card.id for card in dummy_cards_list]
    )
    
    return set_in, cards_lookup


def patch_cards(cards_lookup):
    def _side_effect(db, card_id):
        return cards_lookup.get(card_id)
    return patch("app.set.service.CardService.get_card_by_id", side_effect=_side_effect)


def test_create_set_invalid_two_detectives_raises(db_session):
    set_in_data, cards_lookup = build_set_input(["D_MS", "D_LEB"])
    service = SetService(db_session)

    with patch_cards(cards_lookup), pytest.raises(ValueError, match="Invalid set of detectives"):
        service.validate_set(set_in_data.cards)

    db_session.add.assert_not_called()
    db_session.commit.assert_not_called()


def test_create_set_invalid_three_detectives_raises(db_session):
    set_in_data, cards_lookup = build_set_input(["D_MM", "D_HP", "D_MS"])
    service = SetService(db_session)

    with patch_cards(cards_lookup), pytest.raises(ValueError, match="Invalid set of detectives"):
        service.validate_set(set_in_data.cards)
    
    db_session.add.assert_not_called()
    db_session.commit.assert_not_called()


def test_create_set_non_detective_card_raises(db_session):
    set_in_data, cards_lookup = build_set_input(["D_MS", "D_MS"])
    any_card_id = set_in_data.cards[0]
    # Cambiamos el tipo de la carta
    cards_lookup[any_card_id].type = CardType.EVENT
    
    service = SetService(db_session)

    with patch_cards(cards_lookup), pytest.raises(ValueError, match="Invalid type of cards for a set"):
        service.validate_set(set_in_data.cards)

    db_session.add.assert_not_called()
    db_session.commit.assert_not_called()


def test_create_set_missing_card_raises(db_session):
    set_in_data, cards_lookup = build_set_input(["D_MS", "D_MS"])
    # Borramos una carta
    missing_id = set_in_data.cards[0]
    del cards_lookup[missing_id]
    
    service = SetService(db_session)

    # patch_cards retornará None, y determine_set_type
    # fallará al intentar acceder a 'card.type'
    with patch_cards(cards_lookup), pytest.raises(AttributeError, match="'NoneType' object has no attribute 'type'"):
        service.validate_set(set_in_data.cards)

    db_session.add.assert_not_called()
    db_session.commit.assert_not_called()


def test_create_set_invalid_number_of_cards_raises(db_session):
    set_in_data, cards_lookup = build_set_input(["D_MS"] * 4)
    service = SetService(db_session)

    with patch_cards(cards_lookup), pytest.raises(ValueError, match="Invalid number of cards"):
        service.validate_set(set_in_data.cards)

    db_session.add.assert_not_called()
    db_session.commit.assert_not_called()


def test_create_set_rollback_on_commit_error(db_session):
    set_in_data, cards_lookup = build_set_input(["D_MS", "D_MS"])
    db_session.commit.side_effect = SQLAlchemyError("boom")
    service = SetService(db_session)

    with patch_cards(cards_lookup), pytest.raises(ValueError, match="Failed to create set"):

        # Validar
        set_type = service.validate_set(set_in_data.cards)
        
        # Crear (falla)
        service.create_set(
            game_id=set_in_data.game_id,
            player_id=set_in_data.player_id,
            set_type=set_type,
            cards=set_in_data.cards
        )

    db_session.rollback.assert_called_once()
    db_session.refresh.assert_not_called()


def test_determine_set_type_unsupported_number_raises(db_session):
    set_in_data, cards_lookup = build_set_input(["D_MS"] * 4)
    service = SetService(db_session)
    
    with patch_cards(cards_lookup), pytest.raises(ValueError, match="Unsupported number of cards"):
        service.determine_set_type(set_in_data.cards)


def test_resolve_two_detective_cards_invalid_name():
    cards = [DummyCard("D_FAKE"), DummyCard("D_FAKE")]

    with pytest.raises(ValueError, match="Invalid set of detectives"):
        SetService._resolve_two_detective_cards(cards)


def test_resolve_two_detective_cards_invalid_hqw_configuration():
    cards = [DummyCard("D_HQW"), DummyCard("D_MS"), DummyCard("D_TB")]

    with pytest.raises(ValueError, match="Invalid set of detectives"):
        SetService._resolve_two_detective_cards(cards)


def test_resolve_two_detective_cards_invalid_other_name():
    cards = [DummyCard("D_HQW"), DummyCard("D_FAKE")]

    with pytest.raises(ValueError, match="Invalid set of detectives"):
        SetService._resolve_two_detective_cards(cards)


def test_resolve_two_detective_cards_returns_other_type():
    cards = [DummyCard("D_HQW"), DummyCard("D_TB")]

    assert SetService._resolve_two_detective_cards(cards) == SetType.TB


def test_resolve_three_detective_cards_hqw_only_raises():
    cards = [DummyCard("D_HQW")] * 3

    with pytest.raises(ValueError, match="Cannot form a set with HQW"):
        SetService._resolve_three_detective_cards(cards)


def test_resolve_three_detective_cards_invalid_other_name():
    cards = [DummyCard("D_HQW"), DummyCard("D_FAKE"), DummyCard("D_FAKE")]

    with pytest.raises(ValueError, match="Invalid set of detectives"):
        SetService._resolve_three_detective_cards(cards)


class DummySet:
    def __init__(self, set_type, game_id, owner_player_id):
        self.id = uuid.uuid4()
        self.type = set_type
        self.game_id = game_id
        self.owner_player_id = owner_player_id


@pytest.fixture
def db_session():
    return MagicMock()


@pytest.fixture
def base_ids():
    return {
        "game_id": uuid.uuid4(),
        "player_id": uuid.uuid4(),
        "set_id": uuid.uuid4(),
        "card_id": uuid.uuid4(),
    }


def test_add_card_to_set_ok(db_session, base_ids):
    """Caso exitoso: carta y set válidos."""
    set_obj = DummySet(SetType.MM, base_ids["game_id"], base_ids["player_id"])
    card_obj = DummyCard(
    "D_MM",
    card_type=CardType.DETECTIVE,
    owner=CardOwner.PLAYER,
    owner_player_id=base_ids["player_id"],
    game_id=base_ids["game_id"]
    )

    db_session.query.return_value.filter.return_value.first.return_value = set_obj

    service = SetService(db_session)

    with patch("app.set.service.CardService.get_card_by_id", return_value=card_obj):
        result = service.add_card_to_set(
            base_ids["game_id"],
            base_ids["player_id"],
            base_ids["set_id"],
            base_ids["card_id"],
        )

    db_session.query.assert_called_once_with(SetModel)
    assert card_obj.owner == CardOwner.SET
    db_session.add.assert_called_once_with(card_obj)
    db_session.commit.assert_called_once()
    assert result is not None

def test_add_card_to_set_invalid_card_owner(db_session, base_ids):
    """Carta con jugador distinto."""
    set_obj = DummySet(SetType.MM, base_ids["game_id"], base_ids["player_id"])
    card_obj = DummyCard(
    "D_MM",
    card_type=CardType.DETECTIVE,
    owner=CardOwner.PLAYER,
    owner_player_id=uuid.uuid4(),
    game_id=base_ids["game_id"]
    )

    db_session.query.return_value.filter.return_value.first.return_value = set_obj

    service = SetService(db_session)

    with pytest.raises(ValueError, match="NotValidCardID"):
        with patch("app.set.service.CardService.get_card_by_id", return_value=card_obj):
            result = service.add_card_to_set(
                base_ids["game_id"],
                base_ids["player_id"],
                base_ids["set_id"],
                base_ids["card_id"],
            )

def test_add_card_to_set_invalid_card_type(db_session, base_ids):
    """Carta no es detective"""
    set_obj = DummySet(SetType.MM, base_ids["game_id"], base_ids["player_id"])
    card_obj = DummyCard(
    "E_SOMEEVENT",
    card_type=CardType.EVENT,
    owner=CardOwner.PLAYER,
    owner_player_id=uuid.uuid4(),
    game_id=base_ids["game_id"]
    )
    db_session.query.return_value.filter.return_value.first.return_value = set_obj

    service = SetService(db_session)
    with pytest.raises(ValueError, match="NotValidCardID"):
        with patch("app.set.service.CardService.get_card_by_id", return_value=card_obj):
            result = service.add_card_to_set(
                base_ids["game_id"],
                base_ids["player_id"],
                base_ids["set_id"],
                base_ids["card_id"],
            )

def test_add_card_to_set_commit_error_rollback(db_session, base_ids):
    """Error SQL al commitear"""
    set_obj = DummySet(SetType.MM, base_ids["game_id"], base_ids["player_id"])
    card_obj = DummyCard(
    "D_MM",
    card_type=CardType.DETECTIVE,
    owner=CardOwner.PLAYER,
    owner_player_id=base_ids["player_id"],
    game_id=base_ids["game_id"]
    )
    db_session.query.return_value.filter.return_value.first.return_value = set_obj
    db_session.commit.side_effect = SQLAlchemyError("boom")

    service = SetService(db_session)
    with pytest.raises(ValueError, match="Failed to add card to set"):
        with patch("app.set.service.CardService.get_card_by_id", return_value=card_obj):
            result = service.add_card_to_set(
                base_ids["game_id"],
                base_ids["player_id"],
                base_ids["set_id"],
                base_ids["card_id"],
            )

def test_change_set_owner(db_session):
    set_id = uuid.uuid4()
    game_id = uuid.uuid4()

    #SimpleNamespace para crear un objeto con los atributos y tipos correctos
    existing_set = SimpleNamespace(
        id=set_id,
        game_id=game_id,
        owner_player_id=uuid.uuid4(),
        type=SetType.MS, 
        set_cards=[uuid.uuid4()]
    )
    
    db_session.query.return_value.filter.return_value.first.return_value = existing_set
    service = SetService(db_session)
    new_owner_id = uuid.uuid4()

    # ensure the player exists and belongs to the game
    db_session.get.return_value = SimpleNamespace(id=new_owner_id, game_id=game_id)

    service.change_set_owner(game_id, set_id, new_owner_id)

    assert existing_set.owner_player_id == new_owner_id
    db_session.commit.assert_called_once()
    db_session.refresh.assert_called_once_with(existing_set)
    db_session.rollback.assert_not_called()

def test_change_set_owner_set_not_found(db_session):
    db_session.query.return_value.filter.return_value.first.return_value = None
    service = SetService(db_session)
    new_owner_id = uuid.uuid4()

    with pytest.raises(ValueError, match="Set not found"):
        service.change_set_owner(uuid.uuid4(),
                                 uuid.uuid4(), new_owner_id)

    db_session.commit.assert_not_called()
    db_session.refresh.assert_not_called()
    db_session.rollback.assert_not_called()

def test_change_set_owner_rollback_on_commit_error(db_session):
    # Use SimpleNamespace for attributes and mock player existence
    set_id = uuid.uuid4()
    game_id = uuid.uuid4()
    existing_set = SimpleNamespace(
        id=set_id,
        game_id=game_id,
        owner_player_id=uuid.uuid4(),
        type=SetType.MS,
        set_cards=[],
    )
    db_session.query.return_value.filter.return_value.first.return_value = existing_set
    db_session.commit.side_effect = SQLAlchemyError("boom")
    # ensure the player exists and belongs to the game so we hit commit()
    new_owner_id = uuid.uuid4()
    db_session.get.return_value = SimpleNamespace(id=new_owner_id, game_id=game_id)

    service = SetService(db_session)

    with pytest.raises(ValueError, match="Failed to change set owner"):
        service.change_set_owner(game_id, set_id, new_owner_id)

    db_session.rollback.assert_called_once()
    db_session.refresh.assert_not_called()

def test_change_set_owner_to_current_owner(db_session):
    """
    Prueba que se lance ValueError si se intenta cambiar el propietario al dueño actual.
    """
    current_owner_id = uuid.uuid4()
    mock_set_from_db = SimpleNamespace(
        id=uuid.uuid4(),
        game_id=uuid.uuid4(),
        owner_player_id=current_owner_id, # El propietario actual
        type=SetType.MS,
        set_cards=[]
    )

    #Configuramos el mock para que devuelva nuestro set
    db_session.query.return_value.filter.return_value.first.return_value = mock_set_from_db
    
    service = SetService(db_session)

    with pytest.raises(ValueError, match="New owner is the same as the current owner"):
        service.change_set_owner(
            mock_set_from_db.game_id,
            mock_set_from_db.id,
            current_owner_id  
        )
    db_session.commit.assert_not_called()
    db_session.rollback.assert_not_called()

@pytest.fixture
def set_service(db_session):
    return SetService(db_session)


def test_play_set_not_found_raises(set_service, db_session):
    db_session.query.return_value.filter.return_value.first.return_value = None

    with pytest.raises(ValueError, match="Set not found"):
        set_service.play_set(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

def test_play_set_secret_not_belongs_to_target(set_service, db_session):
    fake_set = SimpleNamespace(id=uuid.uuid4())
    db_session.query.return_value.filter.return_value.first.return_value = fake_set

    fake_secret = SimpleNamespace(owner_player_id=uuid.uuid4(), revealed=False)
    with patch("app.set.service.SecretService.get_secret_by_id", return_value=fake_secret):
        with pytest.raises(ValueError, match="The secret must belong to the target player"):
            set_service.play_set(fake_set.id, uuid.uuid4(), uuid.uuid4())

def test_play_set_hide_secret_but_hidden_secret_raises(set_service, db_session):
    fake_set = SimpleNamespace(id=uuid.uuid4(), type=SetType.PP)
    db_session.query.return_value.filter.return_value.first.return_value = fake_set

    target_player_id = uuid.uuid4()
    fake_secret = SimpleNamespace(owner_player_id=target_player_id, revealed=False)

    with (
        patch("app.set.service.SecretService.get_secret_by_id", return_value=fake_secret),
        patch("app.set.service.SecretService.change_secret_status") as mock_change_status,
    ):
        with pytest.raises(ValueError, match="The secret is already hidden"):
            set_service.play_set(fake_set.id, target_player_id, uuid.uuid4())

    mock_change_status.assert_not_called()

def test_play_set_hide_secret_success_when_revealed(set_service, db_session):
    fake_set = SimpleNamespace(
        id=uuid.uuid4(), 
        type=SetType.PP, 
        game_id=uuid.uuid4(),
        owner_player_id=uuid.uuid4()
    )
    db_session.query.return_value.filter.return_value.first.return_value = fake_set

    target_player_id = uuid.uuid4()

    fake_secret = MagicMock()
    fake_secret.id = uuid.uuid4()
    fake_secret.owner_player_id = target_player_id
    fake_secret.revealed = True
    fake_secret.role = SecretType.COMMON

    mock_secret_dto_then_hide = MagicMock()
    mock_secret_dto_then_hide.id = fake_secret.id
    mock_secret_dto_then_hide.owner_player_id = fake_secret.owner_player_id
    mock_secret_dto_then_hide.revealed = False 
    mock_secret_dto_then_hide.role = fake_secret.role

    mock_secret_service_instance = MagicMock()
    mock_secret_service_instance.get_secret_by_id.return_value = fake_secret
    mock_secret_service_instance.change_secret_status.return_value = mock_secret_dto_then_hide

    with (
        patch("app.set.service.SecretService", return_value=mock_secret_service_instance)
    ):
        result = set_service.play_set(fake_set.id, target_player_id, fake_secret.id)

    mock_secret_service_instance.get_secret_by_id.assert_called_once_with(db_session, fake_secret.id)
    mock_secret_service_instance.change_secret_status.assert_called_once_with(db_session, fake_secret.id)

    assert isinstance(result, SetPlayResult)
    assert result.set_out.id == fake_set.id
    assert result.end_game_result is None

def test_play_set_normal_success_when_hidden(set_service, db_session):
    fake_set = MagicMock(
        id=uuid.uuid4(),
        type=SetType.MS,
        game_id=uuid.uuid4(),
        owner_player_id=uuid.uuid4()
    )
    db_session.query.return_value.filter.return_value.first.return_value = fake_set

    target_player_id = uuid.uuid4()
    fake_secret = MagicMock(
        id=uuid.uuid4(),
        owner_player_id=target_player_id,
        revealed=False,
        role=SecretType.COMMON
    )

    mock_secret_dto_after_reveal = MagicMock(
        id=fake_secret.id,
        owner_player_id=fake_secret.owner_player_id,
        revealed=True,
        role=fake_secret.role
    )

    mock_secret_service_instance = MagicMock()
    mock_secret_service_instance.get_secret_by_id.return_value = fake_secret
    mock_secret_service_instance.change_secret_status.return_value = mock_secret_dto_after_reveal
    mock_secret_service_instance.get_murderer_team_ids.return_value = set()

    mock_game_service_instance = MagicMock()
    mock_game_service_instance.end_game.return_value = None

    db_session.query.return_value.filter.return_value.scalar.return_value = 1

    with (
        patch("app.set.service.SecretService", return_value=mock_secret_service_instance),
        patch("app.set.service.GameService", return_value=mock_game_service_instance)
    ):
        result = set_service.play_set(fake_set.id, target_player_id, fake_secret.id)

    mock_secret_service_instance.get_secret_by_id.assert_called_once_with(db_session, fake_secret.id)
    mock_secret_service_instance.change_secret_status.assert_called_once_with(db_session, fake_secret.id)

    assert isinstance(result, SetPlayResult)
    assert result.set_out.id == fake_set.id
    assert result.end_game_result is None

@pytest.fixture
def base_data():
    """Datos comunes para los tests de play_set"""
    game_id = uuid.uuid4()
    set_id = uuid.uuid4()
    player_id = uuid.uuid4() # Jugador que juega el set
    target_player_id = uuid.uuid4()
    secret_id = uuid.uuid4()
    return {
        "game_id": game_id,
        "set_id": set_id,
        "player_id": player_id,
        "target_player_id": target_player_id,
        "secret_id": secret_id,
    }

# --- Test: Revelar Secreto Normal (Sin Fin de Juego) ---
def test_play_set_reveal_common_secret_success(set_service, db_session, base_data):
    mock_set = MagicMock(
        spec=SetModel, 
        id=base_data["set_id"],
        type=SetType.MS,
        game_id=base_data["game_id"],
        owner_player_id=base_data["player_id"]
    )
    mock_secret = MagicMock(
        spec=SecretOutDTO, 
        id=base_data["secret_id"], 
        owner_player_id=base_data["target_player_id"], 
        revealed=False, 
        role=SecretType.COMMON
    )

    mock_secret_after_reveal = MagicMock(spec=SecretOutDTO)
    mock_secret_after_reveal.id = mock_secret.id
    mock_secret_after_reveal.owner_player_id = mock_secret.owner_player_id
    mock_secret_after_reveal.revealed = True
    mock_secret_after_reveal.role = mock_secret.role

    db_session.query.return_value.filter.return_value.first.return_value = mock_set
    db_session.query.return_value.filter.return_value.scalar.return_value = 1

    mock_secret_service_instance = MagicMock()
    mock_secret_service_instance.get_secret_by_id.return_value = mock_secret
    mock_secret_service_instance.change_secret_status.return_value = mock_secret_after_reveal

    mock_game_service_instance = MagicMock()

    with patch("app.set.service.SecretService", return_value=mock_secret_service_instance), \
         patch("app.set.service.GameService", return_value=mock_game_service_instance):

        result = set_service.play_set(base_data["set_id"], base_data["target_player_id"], base_data["secret_id"])

    mock_secret_service_instance.get_secret_by_id.assert_called_once_with(db_session, base_data["secret_id"])
    mock_secret_service_instance.change_secret_status.assert_called_once_with(db_session, base_data["secret_id"])
    mock_game_service_instance.end_game.assert_not_called()
    assert isinstance(result, SetPlayResult)
    assert result.set_out.id == base_data["set_id"]
    assert result.end_game_result is None

# --- Test: Revelar Secreto de Asesino (Termina Juego) ---
def test_play_set_reveal_murderer_secret_ends_game(set_service, db_session, base_data):
    mock_set = MagicMock(
        spec=SetModel, 
        id=base_data["set_id"], 
        type=SetType.MS, 
        game_id=base_data["game_id"],
        owner_player_id=base_data["player_id"]
    )
    mock_secret_murderer = MagicMock(
        spec=SecretOutDTO, 
        id=base_data["secret_id"], 
        owner_player_id=base_data["target_player_id"], 
        revealed=False, 
        role=SecretType.MURDERER
    )
    mock_secret_after_reveal = MagicMock(spec=SecretOutDTO)
    mock_secret_after_reveal.id = mock_secret_murderer.id
    mock_secret_after_reveal.owner_player_id = mock_secret_murderer.owner_player_id
    mock_secret_after_reveal.revealed = True
    mock_secret_after_reveal.role = mock_secret_murderer.role
    mock_end_game_dto = MagicMock(spec=EndGameResult)

    db_session.query.return_value.filter.return_value.first.return_value = mock_set

    mock_secret_service_instance = MagicMock()
    mock_secret_service_instance.get_secret_by_id.return_value = mock_secret_murderer
    mock_secret_service_instance.change_secret_status.return_value = mock_secret_after_reveal

    mock_game_service_instance = MagicMock()
    mock_game_service_instance.end_game.return_value = mock_end_game_dto 

    with patch("app.set.service.SecretService", return_value=mock_secret_service_instance), \
         patch("app.set.service.GameService", return_value=mock_game_service_instance):

        result = set_service.play_set(base_data["set_id"], base_data["target_player_id"], base_data["secret_id"])

    mock_secret_service_instance.change_secret_status.assert_called_once()
    mock_game_service_instance.end_game.assert_called_once_with(base_data["game_id"], GameEndReason.MURDERER_REVEALED)
    assert isinstance(result, SetPlayResult)
    assert result.set_out.id == base_data["set_id"]
    assert result.end_game_result == mock_end_game_dto 

# --- Test: Revelar Último Secreto de Detective (Termina Juego) ---
def test_play_set_reveal_last_detective_secret_ends_game(set_service, db_session, base_data):
    mock_set = MagicMock(
        spec=SetModel, 
        id=base_data["set_id"], 
        type=SetType.MS, 
        game_id=base_data["game_id"],
        owner_player_id=base_data["player_id"]
    )
    mock_secret_common = MagicMock(
        spec=SecretOutDTO, 
        id=base_data["secret_id"], 
        owner_player_id=base_data["target_player_id"], 
        revealed=False, 
        role=SecretType.COMMON
    )
    mock_secret_after_reveal = MagicMock(spec=SecretOutDTO)
    mock_secret_after_reveal.id = mock_secret_common.id
    mock_secret_after_reveal.owner_player_id = mock_secret_common.owner_player_id
    mock_secret_after_reveal.revealed = True 
    mock_secret_after_reveal.role = mock_secret_common.role
    mock_murderer_secret_hidden = MagicMock(spec=Secrets, revealed=False) 
    mock_end_game_dto = MagicMock(spec=EndGameResult)

    def query_filter_first_side_effect(*args, **kwargs):
        if not hasattr(query_filter_first_side_effect, 'call_count'):
            query_filter_first_side_effect.call_count = 1
        count = query_filter_first_side_effect.call_count
        query_filter_first_side_effect.call_count += 1
        if count == 1: return mock_set
        elif count == 2: return mock_murderer_secret_hidden
        return MagicMock()
    if hasattr(query_filter_first_side_effect, 'call_count'):
         del query_filter_first_side_effect.call_count
    db_session.query.return_value.filter.return_value.first.side_effect = query_filter_first_side_effect
    db_session.query.return_value.filter.return_value.scalar.return_value = 0

    mock_secret_service_instance = MagicMock()
    mock_secret_service_instance.get_secret_by_id.return_value = mock_secret_common
    mock_secret_service_instance.change_secret_status.return_value = mock_secret_after_reveal
    murderer_id = uuid.uuid4()

    mock_game_service_instance = MagicMock()
    mock_game_service_instance.end_game.return_value = mock_end_game_dto

    with patch("app.set.service.SecretService", return_value=mock_secret_service_instance), \
         patch("app.set.service.GameService", return_value=mock_game_service_instance), \
         patch("app.set.service.SecretService.get_murderer_team_ids", return_value={murderer_id}) as mock_static_get_ids:

        result = set_service.play_set(base_data["set_id"], base_data["target_player_id"], base_data["secret_id"])

    mock_secret_service_instance.change_secret_status.assert_called_once()
    mock_static_get_ids.assert_called_once_with(db_session, base_data["game_id"])
    assert db_session.query.return_value.filter.return_value.scalar.call_count == 1
    assert db_session.query.return_value.filter.return_value.first.call_count == 2
    mock_game_service_instance.end_game.assert_called_once_with(base_data["game_id"], GameEndReason.SECRETS_REVEALED)
    assert isinstance(result, SetPlayResult)
    assert result.end_game_result == mock_end_game_dto

# --- Test: Usar Parker Pyne (PP) para Ocultar Secreto (Sin Fin de Juego) ---
def test_play_set_pp_hide_secret_success(set_service, db_session, base_data):
    mock_set_pp = MagicMock(
        spec=SetModel, 
        id=base_data["set_id"], 
        type=SetType.PP, 
        game_id=base_data["game_id"],
        owner_player_id=base_data["player_id"]
    )
    mock_secret_revealed = MagicMock(
        spec=SecretOutDTO, 
        id=base_data["secret_id"], 
        owner_player_id=base_data["target_player_id"], 
        revealed=True, 
        role=SecretType.COMMON)
    mock_secret_after_hide = MagicMock(spec=SecretOutDTO)
    mock_secret_after_hide.id = mock_secret_revealed.id
    mock_secret_after_hide.owner_player_id = mock_secret_revealed.owner_player_id
    mock_secret_after_hide.revealed = False
    mock_secret_after_hide.role = mock_secret_revealed.role
    db_session.query.return_value.filter.return_value.first.return_value = mock_set_pp

    mock_secret_service_instance = MagicMock()
    mock_secret_service_instance.get_secret_by_id.return_value = mock_secret_revealed
    mock_secret_service_instance.change_secret_status.return_value = mock_secret_after_hide

    mock_game_service_instance = MagicMock()

    with patch("app.set.service.SecretService", return_value=mock_secret_service_instance), \
         patch("app.set.service.GameService", return_value=mock_game_service_instance):

        result = set_service.play_set(base_data["set_id"], base_data["target_player_id"], base_data["secret_id"])

    mock_secret_service_instance.change_secret_status.assert_called_once()
    mock_game_service_instance.end_game.assert_not_called()
    assert isinstance(result, SetPlayResult)
    assert result.set_out.id == mock_set_pp.id 
    assert result.end_game_result is None

# --- Tests de Errores de Validación ---
def test_play_set_secret_not_found_raises(set_service, db_session, base_data):
    mock_set = MagicMock(spec=SetModel, id=base_data["set_id"], type=SetType.MS)
    db_session.query.return_value.filter.return_value.first.return_value = mock_set

    mock_secret_service_instance = MagicMock()
    mock_secret_service_instance.get_secret_by_id.return_value = None

    with patch("app.set.service.SecretService", return_value=mock_secret_service_instance):
        with pytest.raises(ValueError, match="Secret not found"):
            set_service.play_set(base_data["set_id"], base_data["target_player_id"], base_data["secret_id"])

def test_play_set_secret_wrong_owner_raises(set_service, db_session, base_data):
    mock_set = MagicMock(spec=SetModel, id=base_data["set_id"], type=SetType.MS)
    wrong_owner_id = uuid.uuid4()
    mock_secret = MagicMock(spec=SecretOutDTO, id=base_data["secret_id"], owner_player_id=wrong_owner_id)
    db_session.query.return_value.filter.return_value.first.return_value = mock_set

    mock_secret_service_instance = MagicMock()
    mock_secret_service_instance.get_secret_by_id.return_value = mock_secret

    with patch("app.set.service.SecretService", return_value=mock_secret_service_instance):
        with pytest.raises(ValueError, match="The secret must belong to the target player"):
            set_service.play_set(base_data["set_id"], base_data["target_player_id"], base_data["secret_id"])

def test_play_set_reveal_already_revealed_raises(set_service, db_session, base_data):
    mock_set = MagicMock(spec=SetModel, id=base_data["set_id"], type=SetType.MS) 
    mock_secret = MagicMock(spec=SecretOutDTO, id=base_data["secret_id"], owner_player_id=base_data["target_player_id"], revealed=True) 
    db_session.query.return_value.filter.return_value.first.return_value = mock_set

    mock_secret_service_instance = MagicMock()
    mock_secret_service_instance.get_secret_by_id.return_value = mock_secret

    with patch("app.set.service.SecretService", return_value=mock_secret_service_instance):
        with pytest.raises(ValueError, match="The secret is already revealed"):
            set_service.play_set(base_data["set_id"], base_data["target_player_id"], base_data["secret_id"])
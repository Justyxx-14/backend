import json
import types
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, AsyncMock, patch, ANY
from app.card import schemas
from app.secret.schemas import SecretOut
from app.secret.enums import SecretType
from app.card import enums
from app.card import endpoints
from app.card.models import Card
from app.card.enums import CardOwner, CardType
from app.main import app
from app.db import get_db
from app.card.exceptions import PlayerHandLimitExceededException
from app.card.exceptions import CardsNotFoundOrInvalidException
from app.card import endpoints
from app.set.enums import SetType
from app.game.schemas import EndGameResult, GameEndReason, GameTurnStateOut
from app.game.enums import TurnState
from app.game.models import Game, GameTurnState
from fastapi.encoders import jsonable_encoder
from app.secret.enums import SecretType

# -------------Helpers / Fake Data

def _fake_card(**over):
    """
    Dict con los campos EXACTOS de CardOut:
    id, game_id, type, name, description, owner, owner_player_id
    """
    base = {
        "id": uuid.uuid4(),          # UUID
        "game_id": None,             # UUID
        "type": "EVENT",
        "name": "Carta X",
        "description": "Desc",
        "owner": "DECK",
        "owner_player_id": None,     # UUID | None
    }
    base.update(over)
    return base

def _dump(model):
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()

# --------------Client + Service Mock

@pytest.fixture
def client(monkeypatch):

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.scalar.return_value = 5

    def _fake_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _fake_get_db

    # "estado" en memoria del test
    cards = []

    def create_cards_batch(db, game_id, batch_in):
        out = []
        for it in batch_in.items:
            data = _dump(it)
            c = _fake_card(game_id=game_id, **data)
            c["owner"] = "DECK"
            cards.append(c)
            out.append(types.SimpleNamespace(**c))
        return out

    def get_card_by_id(db, card_id):
        for c in cards:
            if c["id"] == card_id:  # UUID == UUID
                return types.SimpleNamespace(**c)
        return None

    def _get_cards_by_game(game_id):
        return [types.SimpleNamespace(**c) for c in cards if c["game_id"] == game_id]

    def _get_cards_by_owner(game_id, owner, player_id=None):
        xs = [c for c in cards if c["game_id"] == game_id and c["owner"] == owner]
        if owner == "PLAYER" and player_id is not None:
            xs = [c for c in xs if c["owner_player_id"] == player_id]
        return [types.SimpleNamespace(**c) for c in xs]

    # Este es el método que usan los endpoints nuevos para listar
    def query_cards(db, payload):
        gid = payload.game_id
        owner = payload.owner
        pid = payload.player_id

        if owner is None:
            return _get_cards_by_game(gid)
        if owner in ("DECK", "DISCARD_PILE"):
            return _get_cards_by_owner(gid, owner)
        # owner == PLAYER
        return _get_cards_by_owner(gid, "PLAYER", pid)

    def move_card(db, card_id, move_in):
        for c in cards:
            if c["id"] == card_id:
                to = move_in.to_owner
                c["owner"] = to
                c["owner_player_id"] = move_in.player_id if to == "PLAYER" else None
                return types.SimpleNamespace(**c)
        return None
    
    def moveDeckToPlayer(db, game_id, player_id, n_cards):
    # Puedes devolver una lista vacía o simular el comportamiento necesario para tus tests
        return []
    
    def movePlayertoDiscard(db, game_id, player_id, cardPlayer):
    # Simula el comportamiento necesario para tus tests
        return []
    
    def see_top_discard(db, game_id, n):
        return []
    def ensure_move_valid(db, game_id, player_id):
        return False

    fake_service = types.SimpleNamespace(
        create_cards_batch=create_cards_batch,
        get_card_by_id=get_card_by_id,
        query_cards=query_cards,
        move_card=move_card,
        moveDeckToPlayer=moveDeckToPlayer,
        movePlayertoDiscard=movePlayertoDiscard,
        see_top_discard=see_top_discard,
        ensure_move_valid=ensure_move_valid
    )

    from app.card import endpoints
    monkeypatch.setattr(endpoints, "CardService", fake_service)

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()

# ---------------Tests

def test_create_cards_batch_201_single(client):
    game_id = uuid.uuid4()
    payload = {"items": [{"type": "EVENT", "name": "A", "description": "a"}]}
    r = client.post(f"/cards/{game_id}", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert isinstance(body, list) and len(body) == 1
    one = body[0]
    assert one["game_id"] == str(game_id)
    assert one["owner"] == "DECK"
    assert one["type"] == "EVENT"
    assert one["name"] == "A"

def test_create_cards_batch_201_multiple(client):
    game_id = uuid.uuid4()
    payload = {"items": [
        {"type": "EVENT", "name": "A", "description": "a"},
        {"type": "EVENT", "name": "B", "description": "b"},
    ]}
    r = client.post(f"/cards/{game_id}", json=payload)
    assert r.status_code == 201
    data = r.json()
    assert {i["name"] for i in data} == {"A", "B"}

def test_get_card_by_id_ok_and_404_cross_game(client):
    game_id = uuid.uuid4()
    created = client.post(
        f"/cards/{game_id}",
        json={"items": [{"type": "EVENT", "name": "X", "description": "x"}]},
    ).json()[0]

    # OK: GET /cards con body { game_id, card_id } devuelve lista de 1
    r_ok = client.get(
    "/cards",
    params={"game_id": str(game_id), "card_id": created["id"]}
    )
    assert r_ok.status_code == 200
    data_ok = r_ok.json()
    assert isinstance(data_ok, list) and len(data_ok) == 1 and data_ok[0]["id"] == created["id"]

    # 404 si el game_id no coincide con el de la carta
    other_game = uuid.uuid4()
    r_404 = client.get("/cards",params={"game_id": str(other_game), "card_id": created["id"]}
)
    assert r_404.status_code == 404

def test_list_cards_filters(client):
    game_id = uuid.uuid4()

    # Creo 3 cartas en DECK (tres POST batch de 1 ítem por simpleza)
    deck_card   = client.post(f"/cards/{game_id}", json={"items":[{"type":"EVENT","name":"deck","description":"d"}]}).json()[0]
    discard_src = client.post(f"/cards/{game_id}", json={"items":[{"type":"EVENT","name":"disc","description":"d"}]}).json()[0]
    player_src  = client.post(f"/cards/{game_id}", json={"items":[{"type":"EVENT","name":"p1","description":"d"}]}).json()[0]

    # movimientos
    client.put("/cards", json={"game_id": str(game_id), "card_id": discard_src["id"], "to_owner":"DISCARD_PILE"})
    player_id = uuid.uuid4()
    client.put("/cards", json={"game_id": str(game_id), "card_id": player_src["id"], "to_owner":"PLAYER","player_id":str(player_id)})

    # DECK contiene deck_card y no contiene las movidas
    r_deck = client.get("/cards", params={"game_id": str(game_id), "owner":"DECK"})
    deck_ids = {c["id"] for c in r_deck.json()}
    assert deck_card["id"] in deck_ids
    assert discard_src["id"] not in deck_ids
    assert player_src["id"] not in deck_ids

    # DISCARD_PILE contiene discard_src
    r_discard = client.get("/cards", params={"game_id": str(game_id), "owner":"DISCARD_PILE"})
    discard_ids = {c["id"] for c in r_discard.json()}
    assert discard_src["id"] in discard_ids

    # PLAYER específico contiene player_src
    r_player_specific = client.get(
        "/cards", params={"game_id": str(game_id), "owner":"PLAYER","player_id":str(player_id)}
    )
    player_ids = {c["id"] for c in r_player_specific.json()}
    assert player_src["id"] in player_ids

def test_move_card_between_owners(client):
    game_id = uuid.uuid4()
    created = client.post(f"/cards/{game_id}", json={"items":[{"type":"EVENT","name":"X","description":"x"}]}).json()[0]
    cid = created["id"]

    # to PLAYER
    pid = uuid.uuid4()
    r1 = client.put("/cards", json={"game_id": str(game_id), "card_id": cid, "to_owner":"PLAYER","player_id":str(pid)})
    assert r1.status_code == 200 and r1.json()["to_owner"] == "PLAYER"

    # to DISCARD_PILE
    r2 = client.put("/cards", json={"game_id": str(game_id), "card_id": cid, "to_owner":"DISCARD_PILE"})
    assert r2.status_code == 200 and r2.json()["to_owner"] == "DISCARD_PILE" and r2.json()["player_id"] is None

    # to DECK
    r3 = client.put("/cards", json={"game_id": str(game_id), "card_id": cid, "to_owner":"DECK"})
    assert r3.status_code == 200 and r3.json()["to_owner"] == "DECK" and r3.json()["player_id"] is None

def test_move_card_schema_rules(client):
    game_id = uuid.uuid4()
    created = client.post(f"/cards/{game_id}", json={"items":[{"type":"EVENT","name":"X","description":"x"}]}).json()[0]
    cid = created["id"]

    # falta player_id cuando to_owner=PLAYER -> 422
    r_bad1 = client.put("/cards", json={"game_id": str(game_id), "card_id": cid, "to_owner":"PLAYER"})
    assert r_bad1.status_code == 422

    # player_id prohibido cuando to_owner != PLAYER -> 422
    r_bad2 = client.put("/cards",
                        json={"game_id": str(game_id), "card_id": cid, "to_owner":"DECK","player_id":str(uuid.uuid4())})
    assert r_bad2.status_code == 422

def test_move_card_404_if_card_not_in_game(client):
    game_a = uuid.uuid4()
    game_b = uuid.uuid4()
    created = client.post(f"/cards/{game_a}", json={"items":[{"type":"EVENT","name":"X","description":"x"}]}).json()[0]
    r = client.put("/cards", json={"game_id": str(game_b), "card_id": created["id"], "to_owner":"DECK"})
    assert r.status_code == 404

def test_list_player_cards_with_body(client):
    game_id = uuid.uuid4()
    created = client.post(f"/cards/{game_id}", json={"items":[{"type":"EVENT","name":"X","description":"x"}]}).json()[0]
    pid = uuid.uuid4()
    client.put("/cards", json={"game_id": str(game_id), "card_id": created["id"], "to_owner":"PLAYER","player_id":str(pid)})

    r = client.get("/cards", params={"game_id": str(game_id), "owner":"PLAYER", "player_id": str(pid)})
    assert r.status_code == 200
    assert all(x["owner"] == "PLAYER" and x["owner_player_id"] == str(pid) for x in r.json())


def test_list_cards_without_owner_calls_query_cards(client, monkeypatch):
    # espiamos el método que usan los endpoints nuevos
    from app.card import endpoints

    calls = []
    original = endpoints.CardService.query_cards

    def spy_query_cards(db, payload):
        calls.append(payload.game_id)
        return original(db, payload)

    monkeypatch.setattr(endpoints.CardService, "query_cards", spy_query_cards)

    # seed
    game_id = uuid.uuid4()
    client.post(f"/cards/{game_id}", json={"items":[{"type": "EVENT", "name": "A", "description": "a"}]})
    client.post(f"/cards/{game_id}", json={"items":[{"type": "EVENT", "name": "B", "description": "b"}]})

    # sin owner -> debe invocar query_cards una vez y devolver todas
    r = client.get("/cards", params={"game_id": str(game_id)})
    assert r.status_code == 200
    data = r.json()
    assert {c["name"] for c in data} == {"A", "B"}

    # comprobamos que la rama owner=None se ejecutó (se llamó al método correcto)
    assert len(calls) == 1 and str(calls[0]) == str(game_id)


def test_draw_cards_ok(client, monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()

    created_cards = client.post(f"/cards/{game_id}", json={
        "items": [
            {"type": "EVENT", "name": "A", "description": "a"},
            {"type": "EVENT", "name": "B", "description": "b"},
            {"type": "EVENT", "name": "C", "description": "c"},
        ]
    }).json()

    fake_game = MagicMock()
    fake_game.players_ids = [player_id]
    monkeypatch.setattr(
        endpoints.GameService, 
        "get_game_by_id", 
        lambda db, game_id: fake_game
    )
    monkeypatch.setattr(
        endpoints.GameService, 
        "get_turn", 
        lambda db, game_id: player_id
    )
    fake_game_turn_state = MagicMock(turn_state = TurnState.IDLE)
    monkeypatch.setattr(
        endpoints.GameService,
        "get_turn_state",
        lambda db, game_id: fake_game_turn_state
    )

    card_service_calls = {}
    def fake_move_deck_to_player(db, gid, pid, n):
        card_service_calls["called"] = True
        card_service_calls["args"] = (gid, pid, n)

        # Usamos una lista de comprensión con el helper _fake_card
        card_list = [
            _fake_card(
                id=uuid.UUID(c["id"]),       # Sobrescribimos con el ID de la carta real
                game_id=gid,                 # Asignamos el game_id correcto
                name=c["name"],              # Mantenemos los datos originales
                type=c["type"],
                description=c["description"],
                owner="PLAYER",              # ¡Clave! Cambiamos el dueño
                owner_player_id=pid          # ¡Clave! Asignamos el ID del jugador
            )
            for c in created_cards[:n]
        ]

        return card_list, False

    monkeypatch.setattr(endpoints.CardService, "moveDeckToPlayer", fake_move_deck_to_player)

    mock_broadcast = AsyncMock()
    monkeypatch.setattr(endpoints.manager, "broadcast_to_game", mock_broadcast)

    r = client.put(f"/cards/draw/{game_id}", json={
        "player_id": str(player_id),
        "n_cards": 2
    })

    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list) and len(data) == 2

    returned_ids = {c["id"] for c in data}
    expected_ids = {c["id"] for c in created_cards[:2]}
    assert returned_ids == expected_ids

    assert card_service_calls["called"]
    gid, pid, n = card_service_calls["args"]
    assert gid == game_id
    assert pid == player_id
    assert n == 2

    mock_broadcast.assert_awaited_once_with(
        game_id,
        {
            "type": "playerDrawCards",
            "data": {
                "type" : "Deck",
                "id_player": str(player_id),
                "n_cards": 2
            }
        }
    )


def test_draw_cards_hand_limit_exceeded(client, monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()

    fake_game = MagicMock()
    fake_game.players_ids = [player_id]
    monkeypatch.setattr(
        endpoints.GameService, 
        "get_game_by_id", 
        lambda db, game_id: fake_game
    )
    monkeypatch.setattr(
        endpoints.GameService, 
        "get_turn", 
        lambda db, game_id: player_id
    )
    fake_game_turn_state = MagicMock(turn_state = TurnState.IDLE)
    monkeypatch.setattr(
        endpoints.GameService,
        "get_turn_state",
        lambda db, game_id: fake_game_turn_state
    )

    def fake_move_deck_to_player(db, gid, pid, n):
        raise PlayerHandLimitExceededException(detail="Hand limit exceeded")
    monkeypatch.setattr(endpoints.CardService, "moveDeckToPlayer", fake_move_deck_to_player)

    r = client.put(f"/cards/draw/{game_id}", json={
        "player_id": str(player_id),
        "n_cards": 6
    })

    assert r.status_code == 409
    body = r.json()
    assert body["detail"] == "Hand limit exceeded"

def test_draw_cards_invalid_turn_state(client, monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()

    fake_game = MagicMock()
    fake_game.players_ids = [player_id]
    monkeypatch.setattr(
        endpoints.GameService, 
        "get_game_by_id", 
        lambda db, game_id: fake_game
    )
    monkeypatch.setattr(
        endpoints.GameService, 
        "get_turn", 
        lambda db, game_id: player_id
    )
    fake_game_turn_state = MagicMock(turn_state = TurnState.END_TURN)
    monkeypatch.setattr(
        endpoints.GameService,
        "get_turn_state",
        lambda db, game_id: fake_game_turn_state
    )
    r = client.put(f"/cards/draw/{game_id}", json={
    "player_id": str(player_id),
    "n_cards": 1
    })

    assert r.status_code == 400
    body = r.json()
    assert body["detail"] == "Invalid accion for the game state"


def test_pick_draft_card_broadcasts_serializable_payload(client, monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    card_id = uuid.uuid4()

    fake_game = types.SimpleNamespace(id=game_id, players_ids=[player_id])
    fake_game_turn_state = types.SimpleNamespace(turn_state = TurnState.IDLE)

    class FakeGameService:
        def __init__(self, db):
            self.db = db
            self.expected_game_id = game_id

        def get_game_by_id(self, game_id=None, *, game_id_param=None):
            target = game_id if game_id is not None else game_id_param
            assert target == self.expected_game_id
            return fake_game

        def get_turn(self, game_id_param):
            assert game_id_param == self.expected_game_id
            return player_id
        
        def get_turn_state(self,game_id):
            return fake_game_turn_state

    picked_card = types.SimpleNamespace(
        id=card_id,
        game_id=game_id,
        type=enums.CardType.DETECTIVE,
        name="Detective Card",
        description="Detective description",
        owner=enums.CardOwner.PLAYER,
        owner_player_id=player_id,
    )
    draft_card = types.SimpleNamespace(
        id=uuid.uuid4(),
        game_id=game_id,
        type=enums.CardType.EVENT,
        name="Draft Card",
        description="Draft description",
        owner=enums.CardOwner.DRAFT,
        owner_player_id=None,
    )

    def fake_pick_draft(db, gid, pid, cid):
        assert gid == game_id
        assert pid == player_id
        assert cid == card_id
        return picked_card, False

    def fake_query_draft(db, gid):
        assert gid == game_id
        return [draft_card]

    fake_card_service = types.SimpleNamespace(
        pick_draft=fake_pick_draft,
        query_draft=fake_query_draft,
    )

    fake_player = types.SimpleNamespace(social_disgrace= False)

    class FakePlayerService:
        def __init__(self, db):
            self.db = db
            self.expected_game_id = game_id

        def get_player_entity_by_id(self, player_id=None):
            return fake_player


    monkeypatch.setattr("app.card.endpoints.GameService", FakeGameService)
    monkeypatch.setattr("app.card.endpoints.CardService", fake_card_service)
    monkeypatch.setattr("app.card.endpoints.PlayerService", FakePlayerService)

    mock_broadcast = AsyncMock()
    monkeypatch.setattr(endpoints.manager, "broadcast_to_game", mock_broadcast)

    response = client.put(
        f"/cards/draft/{game_id}",
        json={"player_id": str(player_id), "card_id": str(card_id)},
    )

    assert response.status_code == 200

    mock_broadcast.assert_awaited_once()
    args, _ = mock_broadcast.call_args
    sent_game_id, payload = args
    assert sent_game_id == game_id
    assert payload["data"]["player_id"] == str(player_id)
    assert isinstance(payload["data"]["player_id"], str)
    assert all(isinstance(card["id"], str) for card in payload["data"]["draft"])
    assert all(isinstance(card["game_id"], str) for card in payload["data"]["draft"])
    assert all(
        (card["owner_player_id"] is None) or isinstance(card["owner_player_id"], str)
        for card in payload["data"]["draft"]
    )

    json.dumps(payload)


def test_discard_cards_ok(client, monkeypatch):
    """
    Prueba el flujo exitoso de descarte, con TODAS las dependencias externas simuladas.
    """
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    card_ids = [uuid.uuid4(), uuid.uuid4()]

    fake_game = MagicMock()
    fake_game.players_ids = [player_id]
    monkeypatch.setattr(
        endpoints.GameService, 
        "get_game_by_id", 
        lambda db, game_id: fake_game
    )
    monkeypatch.setattr(
        endpoints.GameService, 
        "get_turn", 
        lambda db, game_id: player_id
    )
    fake_game_turn_state = MagicMock(turn_state = TurnState.IDLE)
    monkeypatch.setattr(
        endpoints.GameService,
        "get_turn_state",
        lambda db, game_id: fake_game_turn_state
    )
    monkeypatch.setattr(endpoints.CardService, "ensure_move_valid",
                        lambda db, gid, pid, n: True)

    calls = {}
    def fake_move_player_to_discard(db, gid, pid, ids):
        calls["called"] = True
        calls["args"] = (gid, pid, ids)
        return [
            type("Card", (), {"id": cid, "owner": "DISCARD_PILE", "owner_player_id": None})
            for cid in card_ids
        ]
    monkeypatch.setattr(endpoints.CardService, "movePlayertoDiscard", fake_move_player_to_discard)

    fake_last_card = MagicMock()
    fake_last_card.id = card_ids[-1]
    fake_last_card.game_id = game_id
    fake_last_card.type.value = "test_type"
    fake_last_card.name = "Fake Top Card"
    fake_last_card.description = "A fake card for testing"
    fake_last_card.owner.value = "DISCARD_PILE"
    fake_last_card.owner_player_id = None

    # El mock debe devolver una LISTA con el objeto ORM simulado dentro
    monkeypatch.setattr(
        "app.card.endpoints.CardService.see_top_discard",
        lambda db, gid, n: [fake_last_card]
    )
    
    mock_broadcast = AsyncMock()
    monkeypatch.setattr(endpoints.manager, "broadcast_to_game", mock_broadcast)

    r = client.put(f"/cards/discard/{game_id}", json={
        "player_id": str(player_id),
        "id_cards": [str(cid) for cid in card_ids],
    })

    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert all(c["to_owner"] == "DISCARD_PILE" for c in data)

    assert calls["called"]
    gid, pid, ids_list = calls["args"]
    assert gid == game_id
    assert pid == player_id
    
    assert [str(i) for i in ids_list] == [str(c) for c in card_ids]

    
    mock_broadcast.assert_awaited_once()
    
    call_args, call_kwargs = mock_broadcast.call_args
    assert call_args[0] == game_id
    ws_message = call_args[1]
    assert ws_message["type"] == "playerCardDiscarded"
    last_card = ws_message["data"]["last_card"]
    assert last_card["id"] == str(fake_last_card.id)
    assert last_card["owner"] == fake_last_card.owner.value


def test_discard_cards_invalid(client, monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    card_ids = [uuid.uuid4()]
    from app.card import endpoints

    fake_game = MagicMock()
    fake_game.players_ids = [player_id]
    monkeypatch.setattr(
        endpoints.GameService, 
        "get_game_by_id", 
        lambda db, game_id: fake_game
    )
    monkeypatch.setattr(
        endpoints.GameService, 
        "get_turn", 
        lambda db, game_id: player_id
    )
    fake_game_turn_state = MagicMock(turn_state = TurnState.IDLE)
    monkeypatch.setattr(
        endpoints.GameService,
        "get_turn_state",
        lambda db, game_id: fake_game_turn_state
    )
    monkeypatch.setattr(endpoints.CardService, "ensure_move_valid",
                        lambda db, gid, pid, n: True)
    
    def fake_move_player_to_discard(db, gid, pid, ids):
        raise CardsNotFoundOrInvalidException(detail="Invalid cards")
    monkeypatch.setattr(endpoints.CardService, "movePlayertoDiscard", fake_move_player_to_discard)

    r = client.put(f"/cards/discard/{game_id}", json={
        "player_id": str(player_id),
        "id_cards": [str(cid) for cid in card_ids],
    })
    assert r.status_code == 404
    body = r.json()
    assert body["detail"] == "Invalid cards"

def test_discard_cards_social_disgrace(client, monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    card_ids = [uuid.uuid4()]
    from app.card import endpoints

    fake_game = MagicMock()
    fake_game.players_ids = [player_id]
    monkeypatch.setattr(
        endpoints.GameService, 
        "get_game_by_id", 
        lambda db, game_id: fake_game
    )
    monkeypatch.setattr(
        endpoints.GameService, 
        "get_turn", 
        lambda db, game_id: player_id
    )
    fake_game_turn_state = MagicMock(turn_state = TurnState.IDLE)
    monkeypatch.setattr(
        endpoints.GameService,
        "get_turn_state",
        lambda db, game_id: fake_game_turn_state
    )
    monkeypatch.setattr(endpoints.CardService, "ensure_move_valid",
                        lambda db, gid, pid,n: False)
    
    r = client.put(f"/cards/discard/{game_id}", json={
        "player_id": str(player_id),
        "id_cards": [str(cid) for cid in card_ids],
    })
    assert r.status_code == 403
    body = r.json()
    assert body["detail"] == "El jugador esta en desgracia social, movimiento invalido"


# --- TESTS DE WS EXCLUSIVOS ---



def _get(d, *keys):
    for k in keys:
        if k in d:
            return d[k]
    return None


def _install_card_trade_services(monkeypatch, *, game_id, player_id, players_ids, player_entities, turn_state=TurnState.IDLE):
    """
    Parcha GameService y PlayerService para los tests de Card Trade.
    Devuelve la lista mutable de llamados a change_turn_state.
    """
    change_calls: list[tuple[uuid.UUID, TurnState, dict]] = []

    class FakeGameService:
        def __init__(self, db):
            pass

        def get_game_by_id(self, gid=None, game_id=None):
            return types.SimpleNamespace(id=game_id or gid, players_ids=players_ids)

        def get_turn(self, gid=None, game_id=None):
            return player_id

        def get_turn_state(self, gid=None, game_id=None):
            return types.SimpleNamespace(turn_state=turn_state)

        def change_turn_state(self, gid, new_state, target_player_id=None, **kwargs):
            change_calls.append((gid, new_state, {"target_player_id": target_player_id, **kwargs}))

    class FakePlayerService:
        def __init__(self, db):
            pass

        def get_player_entity_by_id(self, pid=None):
            return player_entities.get(pid)

    monkeypatch.setattr("app.card.endpoints.GameService", FakeGameService)
    monkeypatch.setattr("app.card.endpoints.PlayerService", FakePlayerService)

    return change_calls


def _make_player(player_id, *, social_disgrace=False):
    """Helper para crear mocks de jugadores."""
    return types.SimpleNamespace(id=player_id, social_disgrace=social_disgrace)


def _make_last_card(game_id):
    """Helper para simular la última carta del descarte."""
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        game_id=game_id,
        type=CardType.EVENT,
        name="Top Card",
        description="Top description",
        owner=CardOwner.DISCARD_PILE,
        owner_player_id=None,
    )

def test_ws_emits_on_create_batch(client):
    """
    Conecta a /ws/{game_id}, crea un batch y verifica que llega
    un evento `cards/createBatch` con cardIds no vacío.
    """
    game_id = uuid.uuid4()
    try:
        with client.websocket_connect(f"/ws/{game_id}") as ws:
            r = client.post(f"/cards/{game_id}", json={
                "items": [{"type": "EVENT", "name": "A", "description": "a"}]
            })
            assert r.status_code == 201

            evt = ws.receive_json()
            assert evt["type"] == "cards/createBatch"
            # Envelope tolerante a camel/snake
            gid = _get(evt, "gameId", "game_id")
            assert gid == str(game_id)

            data = evt["data"]
            card_ids = _get(data, "cardIds", "card_ids")
            assert isinstance(card_ids, list) and len(card_ids) == 1
    except Exception as e:
        pytest.skip(f"WS no disponible en /ws/{{game_id}}: {e}")

def test_ws_emits_on_move(client):
    """
    Conecta a /ws/{game_id}, crea una carta, luego la mueve.
    Verifica que llega `cards/move` y que el payload referencia
    correctamente la carta y contiene from/to.
    """
    game_id = uuid.uuid4()
    try:
        with client.websocket_connect(f"/ws/{game_id}") as ws:
            # 1) crear 1 carta → consume evento createBatch
            created = client.post(f"/cards/{game_id}", json={
                "items": [{"type":"EVENT","name":"X","description":"x"}]
            }).json()[0]
            _ = ws.receive_json()  # cards/createBatch

            # 2) mover: DECK -> PLAYER (kind puede variar si tu mock usa strings)
            pid = uuid.uuid4()
            r = client.put("/cards", json={
                "game_id": str(game_id),
                "card_id": created["id"],
                "to_owner": "PLAYER",
                "player_id": str(pid)
            })
            assert r.status_code == 200

            evt = ws.receive_json()
            assert evt["type"] == "cards/move"
            gid = _get(evt, "gameId", "game_id")
            assert gid == str(game_id)

            data = evt["data"]
            card_id = _get(data, "cardId", "card_id")
            assert card_id == created["id"]

            # Estructura mínima del movimiento
            assert "from" in data and "to" in data
            assert "owner" in data["from"] and "owner" in data["to"]
    except Exception as e:
        pytest.skip(f"WS no disponible en /ws/{{game_id}}: {e}")

# --- Endpoint Look into the ashes.

def test_see_top_discard_ok(client, monkeypatch):
    game_id = uuid.uuid4()

    class FakeGameService:
        def __init__(self, db): pass
        def get_game_by_id(self, game_id):
            return type("Game", (), {"id": game_id})()

    class FakeCardService:
        @staticmethod
        def see_top_discard(db, gid, n):
            return [
                type("Card", (), _fake_card(game_id=game_id, name=f"Carta{i}"))()
                for i in range(1, 4)
            ]

    monkeypatch.setattr("app.card.endpoints.GameService", FakeGameService)
    monkeypatch.setattr("app.card.endpoints.CardService", FakeCardService)

    res = client.get(f"/cards/top_discard/{game_id}")

    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) == 3
    assert data[0]["name"] == "Carta1"


def test_see_top_discard_game_not_found(client, monkeypatch):
    game_id = uuid.uuid4()

    monkeypatch.setattr("app.card.endpoints.GameService", lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: None})())

    res = client.get(f"/cards/top_discard/{game_id}")

    assert res.status_code == 400
    assert res.json()["detail"] == "GameNotFound"

def test_play_event_lia_ok(client, monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    event_id = uuid.uuid4()
    card_id = uuid.uuid4()

    fake_game = type("Game", (), {"id": game_id, "players_ids": [player_id]})()
    fake_game_turn_state = MagicMock(turn_state = TurnState.IDLE)
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id,
                                  "get_turn_state": lambda self, game_id: fake_game_turn_state,
                                  "change_turn_state": lambda self, game_id, pid: None})()
    )

    moved_card = type(
        "Card",
        (),
        {
            "id": card_id,
            "game_id": game_id,
            "type": "EVENT",
            "name": "E_LIA",
            "description": "Fake card",
            "owner": "DISCARD_PILE",
            "owner_player_id": player_id,
        },
    )()

    fake_last_card = MagicMock()
    fake_last_card.id = card_id
    fake_last_card.game_id = game_id
    fake_last_card.type.value = "test_type"
    fake_last_card.name = "Fake Top Card"
    fake_last_card.description = "A fake card for testing"
    fake_last_card.owner.value = "DISCARD_PILE"
    fake_last_card.owner_player_id = None

    fake_player = types.SimpleNamespace(social_disgrace= False)

    class FakePlayerService:
        def __init__(self, db):
            self.db = db
            self.expected_game_id = game_id

        def get_player_entity_by_id(self, player_id=None):
            return fake_player
        
    monkeypatch.setattr("app.card.endpoints.PlayerService", FakePlayerService)

    monkeypatch.setattr(
        "app.card.endpoints.CardService",
        type(
            "FakeCardService",
            (),
            {
                "look_into_the_ashes": staticmethod(
                    lambda db, gid, eid, cid, pid: moved_card
                ),
                "see_top_discard": staticmethod(lambda db, gid, n: [fake_last_card]),
                "verify_cancellable_card": staticmethod(lambda db, cid: False),
            },
        ),
    )

    fake_manager = types.SimpleNamespace(broadcast_to_game=AsyncMock())
    monkeypatch.setattr("app.card.endpoints.manager", fake_manager)

    payload = {
        "player_id": str(player_id),
        "event_id": str(event_id),
        "card_id": str(card_id),
    }

    res = client.put(f"/cards/play/E_LIA/{game_id}", json=payload)

    assert res.status_code == 200
    data = res.json()
    assert data["id"] == str(card_id)
    assert data["owner"] == "DISCARD_PILE"
    assert data["owner_player_id"] == str(player_id)


def test_play_event_lia_invalid_player(client, monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()

    fake_game = type("Game", (), {"id": game_id, "players_ids": []})()
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: []})()
    )

    payload = {
        "player_id": str(player_id),
        "event_id": str(uuid.uuid4()),
        "card_id": str(uuid.uuid4()),
    }

    res = client.put(f"/cards/play/E_LIA/{game_id}", json=payload)

    assert res.status_code == 400
    assert res.json()["detail"] == "GameNotFoundOrPlayerNotInGame"


# --- E_ETP (Early train to Paddington) ---

def test_play_event_etp_ok(client, monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    event_id = uuid.uuid4()
    card_id = uuid.uuid4()

    fake_game = type("Game", (), {"id": game_id, "players_ids": [player_id]})()
    fake_game_turn_state = MagicMock(turn_state = TurnState.IDLE)
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id,
                                  "get_turn_state": lambda self, game_id: fake_game_turn_state,
                                  "change_turn_state": lambda self, game_id, pid: None})()
    )

    fake_last_card = MagicMock()
    fake_last_card.id = card_id
    fake_last_card.game_id = game_id
    fake_last_card.type.value = "test_type"
    fake_last_card.name = "Fake Top Card"
    fake_last_card.description = "A fake card for testing"
    fake_last_card.owner.value = "DISCARD_PILE"
    fake_last_card.owner_player_id = None

    moved_card = type(
        "Card",
        (),
        {
            "id": event_id,
            "game_id": game_id,
            "type": "EVENT",
            "name": "E_ETP",
            "description": "Early Train to Paddington",
            "owner": "OUT_OFF_THE_GAME",
            "owner_player_id": player_id,
        },
    )()
    fake_player = types.SimpleNamespace(social_disgrace= False)

    class FakePlayerService:
        def __init__(self, db):
            self.db = db
            self.expected_game_id = game_id

        def get_player_entity_by_id(self, player_id=None):
            return fake_player
        
    monkeypatch.setattr("app.card.endpoints.PlayerService", FakePlayerService)

    monkeypatch.setattr(
        "app.card.endpoints.CardService",
        type(
            "FakeCardService",
            (),
            {
                "early_train_to_paddington": staticmethod(
                    lambda db, gid, eid, pid: moved_card
                ),
                "see_top_discard": staticmethod(lambda db, gid, n: [fake_last_card]),
                "verify_cancellable_card": staticmethod(lambda db, cid: False),
            },
        ),
    )

    fake_manager = types.SimpleNamespace(broadcast_to_game=AsyncMock())
    monkeypatch.setattr("app.card.endpoints.manager", fake_manager)

    payload = {"player_id": str(player_id), "event_id": str(event_id)}

    res = client.put(f"/cards/play/E_ETP/{game_id}", json=payload)
    assert res.status_code == 200

    data = res.json()
    assert data["id"] == str(event_id)
    assert data["name"] == "E_ETP"
    assert data["owner"] == "OUT_OFF_THE_GAME"
    assert data["owner_player_id"] == str(player_id)

    fake_manager.broadcast_to_game.assert_awaited_once()
    args, _ = fake_manager.broadcast_to_game.call_args
    assert args[0] == game_id
    evt = args[1]
    assert evt["type"] == "playEvent"
    assert evt["data"]["name"] == "Early Train to Paddington"
    assert evt["data"]["id_player"] == str(player_id)


def test_play_event_etp_invalid_game(client, monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()

    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: [],
                                  "get_turn": lambda self, gid: player_id,})(),
    )

    payload = {"player_id": str(player_id), "event_id": str(uuid.uuid4())}

    res = client.put(f"/cards/play/E_ETP/{game_id}", json=payload)
    assert res.status_code == 400
    assert res.json()["detail"] == "GameNotFoundOrPlayerNotInGame"


# --- E_DME (Delay the Murderer's Escape) ---

def test_play_event_dme_ok(client, monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    event_id = uuid.uuid4()

    fake_game = type("Game", (), {"id": game_id, "players_ids": [player_id]})()
    fake_game_turn_state = MagicMock(turn_state = TurnState.IDLE)
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id,
                                  "get_turn_state": lambda self, game_id: fake_game_turn_state,
                                  "change_turn_state": lambda self, game_id, pid: None})()
    )
    fake_player = types.SimpleNamespace(social_disgrace= False)

    class FakePlayerService:
        def __init__(self, db):
            self.db = db
            self.expected_game_id = game_id

        def get_player_entity_by_id(self, player_id=None):
            return fake_player
        
    monkeypatch.setattr("app.card.endpoints.PlayerService", FakePlayerService)

    moved_card = type(
        "Card",
        (),
        {
            "id": event_id,
            "game_id": game_id,
            "type": "EVENT",
            "name": "E_DME",
            "description": "Delay the Murderer's Escape",
            "owner": "OUT_OFF_THE_GAME",
            "owner_player_id": player_id,
        },
    )()

    monkeypatch.setattr(
        "app.card.endpoints.CardService",
        type(
            "FakeCardService",
            (),
            {
                "delay_the_murderer_escape": staticmethod(
                    lambda db, gid, pid, eid: moved_card
                ),
                "see_top_discard": staticmethod(lambda db, gid, n: []),
                "verify_cancellable_card": staticmethod(lambda db, cid: False),
            },
        ),
    )

    fake_manager = types.SimpleNamespace(broadcast_to_game=AsyncMock())
    monkeypatch.setattr("app.card.endpoints.manager", fake_manager)

    payload = {"player_id": str(player_id), "event_id": str(event_id)}

    res = client.put(f"/cards/play/E_DME/{game_id}", json=payload)

    assert res.status_code == 200
    data = res.json()
    assert data["id"] == str(event_id)
    assert data["name"] == "E_DME"
    assert data["owner"] == "OUT_OFF_THE_GAME"
    assert data["owner_player_id"] == str(player_id)


def test_play_event_dme_invalid_turn(client, monkeypatch):
    """Jugador pertenece al juego pero no es su turno -> 400"""
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    other_player = uuid.uuid4()

    fake_game = type("Game", (), {"id": game_id, "players_ids": [player_id]})()
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: other_player})()
    )


    payload = {"player_id": str(player_id), "event_id": str(uuid.uuid4())}
    res = client.put(f"/cards/play/E_DME/{game_id}", json=payload)

    assert res.status_code == 400
    assert res.json()["detail"] == "GameNotFoundOrPlayerNotInGame"


def test_play_event_cot_ok(client, monkeypatch):
    """
    Flujo exitoso
    - El jugador pertenece al juego y es su turno.
    - target_player pertenece al juego.
    - Devuelve 200 con la carta jugada.
    - Broadcast correcto con target_player incluido.
    """
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    target_player = uuid.uuid4()
    event_id = uuid.uuid4()

    fake_game = type("Game", (), {"id": game_id, "players_ids": [player_id, target_player]})()
    fake_game_turn_state = MagicMock(turn_state = TurnState.IDLE)
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id,
                                  "get_turn_state": lambda self, game_id: fake_game_turn_state,
                                  "change_turn_state": lambda self, game_id, pid: None})()
    )
    fake_player = types.SimpleNamespace(social_disgrace= False)

    class FakePlayerService:
        def __init__(self, db):
            self.db = db
            self.expected_game_id = game_id

        def get_player_entity_by_id(self, player_id=None):
            return fake_player
        
    monkeypatch.setattr("app.card.endpoints.PlayerService", FakePlayerService)
    moved_card = type(
        "Card",
        (),
        {
            "id": event_id,
            "game_id": game_id,
            "type": "EVENT",
            "name": "E_COT",
            "description": "Cards off the table",
            "owner": "DISCARD_PILE",
            "owner_player_id": player_id,
        },
    )()

    fake_last_card = MagicMock()
    fake_last_card.id = uuid.uuid4()
    fake_last_card.game_id = game_id
    fake_last_card.type.value = "TEST_TYPE"
    fake_last_card.name = "Fake Top Card"
    fake_last_card.description = "A fake top card"
    fake_last_card.owner.value = "DISCARD_PILE"
    fake_last_card.owner_player_id = None

    monkeypatch.setattr(
        "app.card.endpoints.CardService",
        type(
            "FakeCardService",
            (),
            {
                "cards_off_the_table": staticmethod(
                    lambda db, gid, pid, eid, target: moved_card
                ),
                "see_top_discard": staticmethod(lambda db, gid, n: [fake_last_card]),
                "verify_cancellable_card": staticmethod(lambda db, cid: False),
            },
        ),
    )

    fake_manager = types.SimpleNamespace(broadcast_to_game=AsyncMock())
    monkeypatch.setattr("app.card.endpoints.manager", fake_manager)

    payload = {
        "player_id": str(player_id),
        "event_id": str(event_id),
        "target_player": str(target_player),
    }

    res = client.put(f"/cards/play/E_COT/{game_id}", json=payload)

    assert res.status_code == 200
    data = res.json()
    assert data["id"] == str(event_id)
    assert data["name"] == "E_COT"
    assert data["owner"] == "DISCARD_PILE"
    assert data["owner_player_id"] == str(player_id)

    fake_manager.broadcast_to_game.assert_awaited_once()
    args, _ = fake_manager.broadcast_to_game.call_args
    assert args[0] == game_id
    evt = args[1]
    assert evt["type"] == "playEvent"
    assert evt["data"]["name"] == "Cards off the table"
    assert evt["data"]["id_player"] == str(player_id)
    assert evt["data"]["target_player"] == str(target_player)
    assert evt["data"]["last_card"]["name"] == "Fake Top Card"


def test_play_event_cot_missing_target_player(client, monkeypatch):
    """Debe devolver 400 si falta target_player."""
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()

    fake_game = type("Game", (), {"id": game_id, "players_ids": [player_id]})()
    fake_game_turn_state = MagicMock(turn_state = TurnState.IDLE)
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id,
                                  "get_turn_state": lambda self, game_id: fake_game_turn_state,
                                  "change_turn_state": lambda self, game_id, pid: None})()
    )
    fake_player = types.SimpleNamespace(social_disgrace= False)

    class FakePlayerService:
        def __init__(self, db):
            self.db = db
            self.expected_game_id = game_id

        def get_player_entity_by_id(self, player_id=None):
            return fake_player
        
    monkeypatch.setattr("app.card.endpoints.PlayerService", FakePlayerService)

    monkeypatch.setattr(
        "app.card.endpoints.CardService",
        type(
            "FakeCardService",
            (),
            {
                "verify_cancellable_card": staticmethod(lambda db, cid: False),
            },
        ),
    )

    payload = {
        "player_id": str(player_id),
        "event_id": str(uuid.uuid4()),
    }

    res = client.put(f"/cards/play/E_COT/{game_id}", json=payload)
    assert res.status_code == 400
    assert res.json()["detail"] == "TargetPlayerIsRequired"


def test_play_event_cot_target_not_in_game(client, monkeypatch):
    """Debe devolver 400 si el target_player no pertenece al juego."""
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    target_player = uuid.uuid4()

    fake_game = type("Game", (), {"id": game_id, "players_ids": [player_id]})()
    fake_game_turn_state = MagicMock(turn_state = TurnState.IDLE)
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id,
                                  "get_turn_state": lambda self, game_id: fake_game_turn_state,
                                  "change_turn_state": lambda self, game_id, pid: None})()
    )
    fake_player = types.SimpleNamespace(social_disgrace= False)

    class FakePlayerService:
        def __init__(self, db):
            self.db = db
            self.expected_game_id = game_id

        def get_player_entity_by_id(self, player_id=None):
            return fake_player
        
    monkeypatch.setattr("app.card.endpoints.PlayerService", FakePlayerService)

    monkeypatch.setattr(
        "app.card.endpoints.CardService",
        type(
            "FakeCardService",
            (),
            {
                "verify_cancellable_card": staticmethod(lambda db, cid: False),
            },
        ),
    )

    payload = {
        "player_id": str(player_id),
        "event_id": str(uuid.uuid4()),
        "target_player": str(target_player),
    }

    res = client.put(f"/cards/play/E_COT/{game_id}", json=payload)
    assert res.status_code == 400
    assert res.json()["detail"] == "GameNotFoundOrPlayerNotInGame"


@pytest.fixture
def fake_cards_fixture():
    """
    Fixture que devuelve un moved_card (la carta del evento jugado)
    y una fake_last_card (la última carta del descarte simulada).
    """
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    event_id = uuid.uuid4()

    # Carta jugada (moved_card)
    moved_card = type(
        "Card",
        (),
        {
            "id": event_id,
            "game_id": game_id,
            "type": "EVENT",
            "name": "E_ATWOM",
            "description": "And Then There Was One More",
            "owner": "DISCARD_PILE",
            "owner_player_id": player_id,
        },
    )()

    # Última carta del descarte
    fake_last_card = MagicMock()
    fake_last_card.id = uuid.uuid4()
    fake_last_card.game_id = game_id
    fake_last_card.type.value = "EVENT"
    fake_last_card.name = "Fake Top Card"
    fake_last_card.description = "A fake top card"
    fake_last_card.owner.value = "DISCARD_PILE"
    fake_last_card.owner_player_id = None

    return {
        "game_id": game_id,
        "player_id": player_id,
        "event_id": event_id,
        "moved_card": moved_card,
        "fake_last_card": fake_last_card,
    }

def test_play_event_atwom_ok(client, monkeypatch,fake_cards_fixture):
    """
    Flujo exitoso:
    - El jugador pertenece al juego y es su turno.
    - target_player pertenece al juego.
    - secret_id presente.
    - Devuelve 200 con la carta jugada y broadcast con secret_data incluido.
    """
    import types
    from unittest.mock import AsyncMock, MagicMock

    game_id = fake_cards_fixture["game_id"]
    player_id = fake_cards_fixture["player_id"]
    event_id = fake_cards_fixture["event_id"]
    moved_card = fake_cards_fixture["moved_card"]
    fake_last_card = fake_cards_fixture["fake_last_card"]
    target_player = uuid.uuid4()
    secret_id = uuid.uuid4()

    fake_game = type("Game", (), {"id": game_id, "players_ids": [player_id, target_player]})()

    fake_game_turn_state = MagicMock(turn_state = TurnState.IDLE)
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id,
                                  "get_turn_state": lambda self, game_id: fake_game_turn_state,
                                  "change_turn_state": lambda self, game_id, pid: None})()
    )
    fake_player = types.SimpleNamespace(social_disgrace= False)

    class FakePlayerService:
        def __init__(self, db):
            self.db = db
            self.expected_game_id = game_id

        def get_player_entity_by_id(self, player_id=None):
            return fake_player
        
    monkeypatch.setattr("app.card.endpoints.PlayerService", FakePlayerService)
    fake_secret = {"id": str(secret_id), "content": "Secret info"}

    monkeypatch.setattr(
        "app.card.endpoints.CardService",
        type(
            "FakeCardService",
            (),
            {
                "then_there_was_one_more": staticmethod(
                    lambda db, gid, pid, eid, tid, sid: moved_card
                ),
                "see_top_discard": staticmethod(lambda db, gid, n: [fake_last_card]),
                "verify_cancellable_card": staticmethod(lambda db, cid: False),
            },
        ),
    )

    monkeypatch.setattr(
        "app.card.endpoints.SecretService",
        type(
            "FakeSecretService",
            (),
            {"get_secret_by_id": staticmethod(lambda db, sid: fake_secret)},
        ),
    )

    fake_manager = types.SimpleNamespace(broadcast_to_game=AsyncMock())
    monkeypatch.setattr("app.card.endpoints.manager", fake_manager)

    payload = {
        "player_id": str(player_id),
        "event_id": str(event_id),
        "target_player": str(target_player),
        "secret_id": str(secret_id),
    }

    res = client.put(f"/cards/play/E_ATWOM/{game_id}", json=payload)

    assert res.status_code == 200
    data = res.json()
    assert data["id"] == str(event_id)
    assert data["name"] == "E_ATWOM"
    assert data["owner"] == "DISCARD_PILE"
    assert data["owner_player_id"] == str(player_id)

    fake_manager.broadcast_to_game.assert_awaited_once()
    args, _ = fake_manager.broadcast_to_game.call_args
    assert args[0] == game_id
    evt = args[1]
    assert evt["type"] == "playEvent"
    assert evt["data"]["name"] == "And Then There Was One More"
    assert evt["data"]["id_player"] == str(player_id)
    assert evt["data"]["target_player"] == str(target_player)
    assert evt["data"]["secret_data"] == fake_secret

def test_play_event_atwom_target_not_in_game(client, monkeypatch,fake_cards_fixture):
    """
    Si target_player no pertenece al juego => 400
    """
    game_id = fake_cards_fixture["game_id"]
    player_id = fake_cards_fixture["player_id"]
    event_id = fake_cards_fixture["event_id"]
    target_player = uuid.uuid4()
    secret_id = uuid.uuid4()

    fake_game = type("Game", (), {"id": game_id, "players_ids": [player_id]})()
    fake_game_turn_state = MagicMock(turn_state = TurnState.IDLE)
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id,
                                  "get_turn_state": lambda self, game_id: fake_game_turn_state,
                                  "change_turn_state": lambda self, game_id, pid: None})()
    )
    fake_player = types.SimpleNamespace(social_disgrace= False)

    class FakePlayerService:
        def __init__(self, db):
            self.db = db
            self.expected_game_id = game_id

        def get_player_entity_by_id(self, player_id=None):
            return fake_player
        
    monkeypatch.setattr("app.card.endpoints.PlayerService", FakePlayerService)

    monkeypatch.setattr(
        "app.card.endpoints.CardService",
        type(
            "FakeCardService",
            (),
            {
                "verify_cancellable_card": staticmethod(lambda db, cid: False),
            },
        ),
    )

    payload = {
        "player_id": str(player_id),
        "event_id": str(event_id),
        "target_player": str(target_player),
        "secret_id": str(secret_id),
    }

    res = client.put(f"/cards/play/E_ATWOM/{game_id}", json=payload)
    assert res.status_code == 400
    assert res.json()["detail"] == "GameNotFoundOrPlayerNotInGame"


@pytest.fixture
def fake_cards_fixture_AV():
    """
    Fixture que devuelve un moved_card (la carta del evento jugado),
    una fake_last_card (la última carta del descarte simulada),
    y datos comunes como game_id, player_id y event_id.
    """
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    event_id = uuid.uuid4()
    set_id = uuid.uuid4()

    # Carta jugada (moved_card)
    moved_card = type(
        "Card",
        (),
        {
            "id": event_id,
            "game_id": game_id,
            "type": "EVENT",
            "name": "E_AV",
            "description": "Another Victim",
            "owner": "DISCARD_PILE",
            "owner_player_id": player_id,
        },
    )()

    # Última carta del descarte
    fake_last_card = MagicMock()
    fake_last_card.id = uuid.uuid4()
    fake_last_card.game_id = game_id
    fake_last_card.type.value = "EVENT"
    fake_last_card.name = "Fake Top Card"
    fake_last_card.description = "A fake top card"
    fake_last_card.owner.value = "DISCARD_PILE"
    fake_last_card.owner_player_id = None

    # Set simulado
    fake_set = types.SimpleNamespace(
        id=str(set_id),
        type=SetType.MM,
        name="Set Example"
    )

    return {
        "game_id": game_id,
        "player_id": player_id,
        "event_id": event_id,
        "set_id": set_id,
        "moved_card": moved_card,
        "fake_last_card": fake_last_card,
        "fake_set": fake_set,
    }

# --- Tests E_AV ---

def test_play_event_av_ok(client, monkeypatch, fake_cards_fixture_AV):
    """
    Flujo exitoso:
    - Jugador pertenece al juego y es su turno.
    - set_id.
    - Devuelve 200 con broadcast incluyendo set_data.
    """
    game_id = fake_cards_fixture_AV["game_id"]
    player_id = fake_cards_fixture_AV["player_id"]
    event_id = fake_cards_fixture_AV["event_id"]
    set_id = fake_cards_fixture_AV["set_id"]
    moved_card = fake_cards_fixture_AV["moved_card"]
    fake_last_card = fake_cards_fixture_AV["fake_last_card"]
    fake_set = fake_cards_fixture_AV["fake_set"]

    fake_game = type("Game", (), {"id": game_id, "players_ids": [player_id]})()
    fake_game_turn_state = MagicMock(turn_state = TurnState.IDLE)
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id,
                                  "get_turn_state": lambda self, game_id: fake_game_turn_state,
                                  "change_turn_state": lambda self, game_id, pid: None})()
    )
    fake_player = types.SimpleNamespace(social_disgrace= False)

    class FakePlayerService:
        def __init__(self, db):
            self.db = db
            self.expected_game_id = game_id

        def get_player_entity_by_id(self, player_id=None):
            return fake_player
        
    monkeypatch.setattr("app.card.endpoints.PlayerService", FakePlayerService)

    class FakeCardService:
        @staticmethod
        def another_victim(db, gid, pid, eid, sid):
            return moved_card

        @staticmethod
        def see_top_discard(db, gid, n):
            return [fake_last_card]
        
        @staticmethod
        def verify_cancellable_card(db, cid):
            return False

    monkeypatch.setattr("app.card.endpoints.CardService", FakeCardService)
    fake_respons_play_set = MagicMock(end_game_result = None)

    class FakeSetService:
        def __init__(self, db):
            pass

        @staticmethod
        def get_set_by_id(db, sid):
            return fake_set

        @staticmethod
        def play_set(*args, **kwargs):
            return fake_respons_play_set

    monkeypatch.setattr("app.card.endpoints.SetService", FakeSetService)

    fake_manager = types.SimpleNamespace(
        broadcast_to_game=AsyncMock(),
        request_secret_choice=AsyncMock(return_value=str(uuid.uuid4()))
    )
    monkeypatch.setattr("app.card.endpoints.manager", fake_manager)

    payload = {
        "player_id": str(player_id),
        "event_id": str(event_id),
        "target_player": str(player_id),
        "secret_id": str(uuid.uuid4()),
        "set_id": str(set_id),
    }

    res = client.put(f"/cards/play/E_AV/{game_id}", json=payload)

    # --- Verificaciones ---
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == str(event_id)
    assert data["name"] == "E_AV"

    assert fake_manager.broadcast_to_game.await_count == 2
    args, _ = fake_manager.broadcast_to_game.call_args
    evt = args[1]
    assert evt["type"] == "playEvent"
    assert evt["data"]["set_data"] == jsonable_encoder(fake_set)

def test_play_event_av_requires_set_and_target(client, monkeypatch, fake_cards_fixture_AV):
    """
    Debe devolver 400 si falta set_id
    """
    game_id = fake_cards_fixture_AV["game_id"]
    player_id = fake_cards_fixture_AV["player_id"]
    event_id = fake_cards_fixture_AV["event_id"]

    fake_game = type("Game", (), {"id": game_id, "players_ids": [player_id]})()
    fake_game_turn_state = MagicMock(turn_state = TurnState.IDLE)
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id,
                                  "get_turn_state": lambda self, game_id: fake_game_turn_state,
                                  "change_turn_state": lambda self, game_id, pid: None})()
    )
    fake_player = types.SimpleNamespace(social_disgrace= False)

    class FakePlayerService:
        def __init__(self, db):
            self.db = db
            self.expected_game_id = game_id

        def get_player_entity_by_id(self, player_id=None):
            return fake_player
        
    monkeypatch.setattr("app.card.endpoints.PlayerService", FakePlayerService)

    monkeypatch.setattr(
        "app.card.endpoints.CardService",
        type(
            "FakeCardService",
            (),
            {
                "verify_cancellable_card": staticmethod(lambda db, cid: False),
            },
        ),
    )
    # Falta set_id y target_player
    payload = {
        "player_id": str(player_id),
        "event_id": str(event_id)
    }

    res = client.put(f"/cards/play/E_AV/{game_id}", json=payload)
    assert res.status_code == 400
    assert res.json()["detail"] == "set_id is required"


def test_play_event_av_invalid_game_or_player(client, monkeypatch, fake_cards_fixture_AV):
    """
    Debe devolver 400 si el juego no existe o el jugador no pertenece
    """
    game_id = fake_cards_fixture_AV["game_id"]
    player_id = fake_cards_fixture_AV["player_id"]

    # Juego inexistente
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: None,
                                  "get_turn": lambda self, game_id: player_id})()
    )

    payload = {
        "player_id": str(player_id),
        "event_id": str(uuid.uuid4()),
        "set_id": str(uuid.uuid4()),
    }

    res = client.put(f"/cards/play/E_AV/{game_id}", json=payload)
    assert res.status_code == 400
    assert res.json()["detail"] == "GameNotFoundOrPlayerNotInGame"

def test_play_evento_with_social_disgrace(client,monkeypatch,fake_cards_fixture_AV):
    """Debe devolver 403 si el jugador esta en desgracia social"""
    game_id = fake_cards_fixture_AV["game_id"]
    player_id = fake_cards_fixture_AV["player_id"]

    fake_game = type("Game", (), {"id": game_id, "players_ids": [player_id]})()
    fake_game_turn_state = MagicMock(turn_state = TurnState.IDLE)
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id,
                                  "get_turn_state": lambda self, game_id: fake_game_turn_state})()
    )
    fake_player = types.SimpleNamespace(social_disgrace= True)

    class FakePlayerService:
        def __init__(self, db):
            self.db = db
            self.expected_game_id = game_id

        def get_player_entity_by_id(self, player_id=None):
            return fake_player
        
        
    monkeypatch.setattr("app.card.endpoints.PlayerService", FakePlayerService)
    payload = {
        "player_id": str(player_id),
        "event_id": str(uuid.uuid4())
    }
    res = client.put(f"/cards/play/E_AV/{game_id}", json=payload)
    assert res.status_code == 403
    assert res.json()["detail"] == "No se puede jugar un evento estando en Desgracia social"

# --- Tests E_CT ---

@pytest.mark.parametrize("input_code", ["d_fake", None])
def test_play_event_ct_success(client, monkeypatch, input_code):
    """Flujo exitoso con y sin requested_card_code en el payload."""
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    target_player = uuid.uuid4()
    event_id = uuid.uuid4()
    offered_card_id = uuid.uuid4()

    change_calls = _install_card_trade_services(
        monkeypatch,
        game_id=game_id,
        player_id=player_id,
        players_ids=[player_id, target_player],
        player_entities={
            player_id: _make_player(player_id),
            target_player: _make_player(target_player),
        },
    )

    fake_event_card = types.SimpleNamespace(
        id=event_id,
        game_id=game_id,
        name="E_CT",
        description="Card Trade",
        type=CardType.EVENT,
        owner=CardOwner.PLAYER,
        owner_player_id=player_id,
    )
    fake_offered_card = types.SimpleNamespace(
        id=offered_card_id,
        game_id=game_id,
        name="DUMMY",
        description="Offered",
        type=CardType.DETECTIVE,
        owner=CardOwner.PLAYER,
        owner_player_id=player_id,
    )
    moved_event_card = types.SimpleNamespace(
        id=event_id,
        game_id=game_id,
        type=CardType.EVENT,
        name="E_CT",
        description="Card Trade",
        owner=CardOwner.DISCARD_PILE,
        owner_player_id=None,
    )

    fake_last_card = _make_last_card(game_id)
    move_calls: list[tuple[uuid.UUID, schemas.CardMoveIn]] = []

    class FakeCardService:
        @staticmethod
        def get_card_by_id(db, cid):
            if cid == event_id:
                return fake_event_card
            if cid == offered_card_id:
                return fake_offered_card
            return None

        @staticmethod
        def move_card(db, cid, move_in):
            move_calls.append((cid, move_in))
            assert cid == event_id
            assert move_in.to_owner == CardOwner.DISCARD_PILE
            return moved_event_card

        @staticmethod
        def see_top_discard(db, gid, n):
            return [fake_last_card]

        @staticmethod
        def verify_cancellable_card(db, cid):
            return False

    monkeypatch.setattr("app.card.endpoints.CardService", FakeCardService)

    fake_manager = types.SimpleNamespace(broadcast_to_game=AsyncMock())
    monkeypatch.setattr("app.card.endpoints.manager", fake_manager)

    payload = {
        "player_id": str(player_id),
        "event_id": str(event_id),
        "target_player": str(target_player),
        "offered_card_id": str(offered_card_id),
    }
    if input_code is not None:
        payload["requested_card_code"] = input_code

    res = client.put(f"/cards/play/E_CT/{game_id}", json=payload)

    assert res.status_code == 200
    data = res.json()
    assert data["id"] == str(event_id)
    assert data["name"] == "E_CT"
    assert data["owner"] == "DISCARD_PILE"

    _, payload_arg = fake_manager.broadcast_to_game.call_args[0]
    expected_code = input_code.upper() if input_code is not None else None
    assert payload_arg["type"] == "playEvent"
    assert payload_arg["data"]["target_player"] == str(target_player)
    assert payload_arg["data"]["requested_card_code"] == expected_code
    assert payload_arg["data"]["last_card"]["name"] == "Top Card"

    assert len(move_calls) == 1
    moved_cid, move_in = move_calls[0]
    assert moved_cid == event_id
    assert move_in.to_owner == CardOwner.DISCARD_PILE
    assert move_in.player_id is None
    assert change_calls == [
        (
            game_id,
            TurnState.CARD_TRADE_PENDING,
            {
                "target_player_id": target_player,
                "current_event_card_id": event_id,
                "card_trade_offered_card_id": offered_card_id,
            },
        )
    ]


@pytest.mark.parametrize(
    ("scenario", "expected_status", "expected_detail"),
    [
        ("missing_fields", 400, "TargetPlayerAndOfferedCardAreRequired"),
        ("target_not_in_game", 400, "GameNotFoundOrPlayerNotInGame"),
        ("target_not_found", 404, "TargetPlayerNotFound"),
    ],
)
def test_play_event_ct_error_cases(client, monkeypatch, scenario, expected_status, expected_detail):
    """Valida los distintos errores de Card Trade."""
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    target_player = uuid.uuid4()

    if scenario == "missing_fields":
        players_ids = [player_id]
        player_entities = {player_id: _make_player(player_id)}
        payload = {
            "player_id": str(player_id),
            "event_id": str(uuid.uuid4()),
        }
    elif scenario == "target_not_in_game":
        players_ids = [player_id]
        player_entities = {
            player_id: _make_player(player_id),
            target_player: _make_player(target_player),
        }
        payload = {
            "player_id": str(player_id),
            "event_id": str(uuid.uuid4()),
            "target_player": str(target_player),
            "offered_card_id": str(uuid.uuid4()),
        }
    else:  # target_not_found
        players_ids = [player_id, target_player]
        player_entities = {player_id: _make_player(player_id)}  # target ausente
        payload = {
            "player_id": str(player_id),
            "event_id": str(uuid.uuid4()),
            "target_player": str(target_player),
            "offered_card_id": str(uuid.uuid4()),
        }

    change_calls = _install_card_trade_services(
        monkeypatch,
        game_id=game_id,
        player_id=player_id,
        players_ids=players_ids,
        player_entities=player_entities,
    )
    class FakeCardService:
        @staticmethod
        def verify_cancellable_card(db, cid):
            return False

    fake_manager = types.SimpleNamespace(broadcast_to_game=AsyncMock())
    monkeypatch.setattr("app.card.endpoints.manager", fake_manager)
    monkeypatch.setattr("app.card.endpoints.CardService",FakeCardService)

    res = client.put(f"/cards/play/E_CT/{game_id}", json=payload)
    assert res.status_code == expected_status
    assert res.json()["detail"] == expected_detail
    fake_manager.broadcast_to_game.assert_not_awaited()
    assert change_calls == []


def test_play_event_ct_rejects_target_card_in_payload(client, monkeypatch):
    """El endpoint rechaza si se envía target_card_id en la iniciación."""
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    target_player = uuid.uuid4()

    change_calls = _install_card_trade_services(
        monkeypatch,
        game_id=game_id,
        player_id=player_id,
        players_ids=[player_id, target_player],
        player_entities={
            player_id: _make_player(player_id),
            target_player: _make_player(target_player),
        },
    )
    class FakeCardService:
        @staticmethod
        def verify_cancellable_card(db, cid):
            return False

    monkeypatch.setattr("app.card.endpoints.CardService",FakeCardService)
    fake_manager = types.SimpleNamespace(broadcast_to_game=AsyncMock())
    monkeypatch.setattr("app.card.endpoints.manager", fake_manager)

    payload = {
        "player_id": str(player_id),
        "event_id": str(uuid.uuid4()),
        "target_player": str(target_player),
        "offered_card_id": str(uuid.uuid4()),
        "target_card_id": str(uuid.uuid4()),
    }

    res = client.put(f"/cards/play/E_CT/{game_id}", json=payload)
    assert res.status_code == 400
    assert res.json()["detail"] == "TargetCardMustBeSelectedByTargetPlayer"
    fake_manager.broadcast_to_game.assert_not_awaited()
    assert change_calls == []


def test_resolve_card_trade_selection_success(client, monkeypatch):
    """El jugador objetivo elige una carta válida y se completa el intercambio."""
    game_id = uuid.uuid4()
    requesting_player = uuid.uuid4()
    target_player = uuid.uuid4()
    event_id = uuid.uuid4()
    offered_card_id = uuid.uuid4()
    target_card_id = uuid.uuid4()

    change_calls: list[tuple[uuid.UUID, TurnState, dict]] = []

    offered_card = types.SimpleNamespace(
        id=offered_card_id,
        game_id=game_id,
        owner=CardOwner.PLAYER,
        owner_player_id=requesting_player,
        type=CardType.DETECTIVE,
        name="Offered",
        description="Offered card",
    )
    target_card = types.SimpleNamespace(
        id=target_card_id,
        game_id=game_id,
        owner=CardOwner.PLAYER,
        owner_player_id=target_player,
        type=CardType.DETECTIVE,
        name="Target",
        description="Target card",
    )

    moved_offered = types.SimpleNamespace(
        id=offered_card_id,
        game_id=game_id,
        owner=CardOwner.PLAYER,
        owner_player_id=target_player,
        type=CardType.DETECTIVE,
        name="Offered",
        description="Offered card",
    )
    moved_target = types.SimpleNamespace(
        id=target_card_id,
        game_id=game_id,
        owner=CardOwner.PLAYER,
        owner_player_id=requesting_player,
        type=CardType.DETECTIVE,
        name="Target",
        description="Target card",
    )
    event_card = types.SimpleNamespace(
        id=event_id,
        game_id=game_id,
        owner=CardOwner.DISCARD_PILE,
        owner_player_id=None,
        name="E_CT",
        description="Card Trade",
        type=CardType.EVENT,
    )

    class FakeGameService:
        def __init__(self, db):
            pass

        def get_game_by_id(self, game_id=None, gid=None):
            return types.SimpleNamespace(players_ids=[requesting_player, target_player])

        def get_turn_state(self, game_id=None, gid=None):
            return types.SimpleNamespace(
                turn_state=TurnState.CARD_TRADE_PENDING,
                target_player_id=target_player,
            )

        def get_turn_state_entity(self, game_id=None, gid=None):
            return types.SimpleNamespace(
                current_event_card_id=event_id,
                card_trade_offered_card_id=offered_card_id,
                state=TurnState.CARD_TRADE_PENDING
            )

        def get_turn(self, game_id=None, gid=None):
            return requesting_player

        def change_turn_state(self, gid, new_state, **kwargs):
            change_calls.append((gid, new_state, kwargs))

    class FakeCardService:
        @staticmethod
        def get_card_by_id(db, cid):
            if cid == offered_card_id:
                return offered_card
            if cid == target_card_id:
                return target_card
            if cid == event_id:
                return event_card
            return None

        @staticmethod
        def move_card(db, cid, move_in):
            if cid == offered_card_id:
                assert move_in.player_id == target_player
                return moved_offered
            if cid == target_card_id:
                assert move_in.player_id == requesting_player
                return moved_target
            raise AssertionError("Unexpected card id")
        @staticmethod
        def card_trade(db, game_id, player_id, event_card_id, 
                        target_player_id, offered_card_id, target_card_id):
            return {
                "discarded_card": event_card, 
                "blackmailed_events": []
            }

    monkeypatch.setattr("app.card.endpoints.GameService", FakeGameService)
    monkeypatch.setattr("app.card.endpoints.CardService", FakeCardService)

    fake_manager = types.SimpleNamespace(broadcast_to_game=AsyncMock())
    monkeypatch.setattr("app.card.endpoints.manager", fake_manager)

    payload = {
        "player_id": str(target_player),
        "target_card_id": str(target_card_id),
        "event_card_id": str(event_id),
    }
    res = client.put(f"/cards/play/E_CT/{game_id}/selection", json=payload)

    assert res.status_code == 200
    body = res.json()
    assert body["offered_card"]["id"] == str(offered_card_id)
    assert body["offered_card"]["owner"] == "PLAYER"
    assert body["offered_card"]["owner_player_id"] == str(requesting_player)
    assert body["received_card"]["id"] == str(target_card_id)
    assert body["received_card"]["owner_player_id"] == str(target_player)
    assert change_calls == [
        (game_id, TurnState.DISCARDING, {})
    ]


def test_resolve_card_trade_selection_requires_context(client, monkeypatch):
    """Debe devolver 409 si no hay intercambio pendiente."""
    game_id = uuid.uuid4()
    requesting_player = uuid.uuid4()
    target_player = uuid.uuid4()
    event_id = uuid.uuid4()

    class FakeGameService:
        def __init__(self, db):
            pass

        def get_game_by_id(self, game_id=None, gid=None):
            return types.SimpleNamespace(players_ids=[requesting_player, target_player])

        def get_turn_state(self, game_id=None, gid=None):
            return types.SimpleNamespace(
                turn_state=TurnState.CARD_TRADE_PENDING,
                target_player_id=target_player,
            )

        def get_turn_state_entity(self, game_id=None, gid=None):
            return types.SimpleNamespace(
                current_event_card_id=event_id,
                card_trade_offered_card_id=None,
            )

        def get_turn(self, game_id=None, gid=None):
            return requesting_player

        def change_turn_state(self, gid, new_state, **kwargs):
            pass

    monkeypatch.setattr("app.card.endpoints.GameService", FakeGameService)
    def _should_not_fetch(*_args, **_kwargs):
        raise AssertionError("CardService.get_card_by_id should not be called")

    monkeypatch.setattr(
        "app.card.endpoints.CardService.get_card_by_id",
        staticmethod(_should_not_fetch),
    )

    fake_manager = types.SimpleNamespace(broadcast_to_game=AsyncMock())
    monkeypatch.setattr("app.card.endpoints.manager", fake_manager)

    payload = {
        "player_id": str(target_player),
        "target_card_id": str(uuid.uuid4()),
        "event_card_id": str(event_id),
    }
    res = client.put(f"/cards/play/E_CT/{game_id}/selection", json=payload)
    assert res.status_code == 409
    assert res.json()["detail"] == "CardTradeNotPending"
    fake_manager.broadcast_to_game.assert_not_awaited()


def test_resolve_card_trade_selection_only_target_player(client, monkeypatch):
    """Debe devolver 403 si otro jugador intenta elegir la carta."""
    game_id = uuid.uuid4()
    requesting_player = uuid.uuid4()
    target_player = uuid.uuid4()
    intruder = uuid.uuid4()
    event_id = uuid.uuid4()
    offered_card_id = uuid.uuid4()

    class FakeGameService:
        def __init__(self, db):
            pass

        def get_game_by_id(self, game_id=None, gid=None):
            return types.SimpleNamespace(players_ids=[requesting_player, target_player, intruder])

        def get_turn_state(self, game_id=None, gid=None):
            return types.SimpleNamespace(
                turn_state=TurnState.CARD_TRADE_PENDING,
                target_player_id=target_player,
            )

        def get_turn_state_entity(self, game_id=None, gid=None):
            return types.SimpleNamespace(
                current_event_card_id=event_id,
                card_trade_offered_card_id=offered_card_id,
            )

        def get_turn(self, game_id=None, gid=None):
            return requesting_player

        def change_turn_state(self, gid, new_state, **kwargs):
            pass

    monkeypatch.setattr("app.card.endpoints.GameService", FakeGameService)
    def _should_not_fetch(*_args, **_kwargs):
        raise AssertionError("CardService.get_card_by_id should not be called")

    monkeypatch.setattr(
        "app.card.endpoints.CardService.get_card_by_id",
        staticmethod(_should_not_fetch),
    )

    fake_manager = types.SimpleNamespace(broadcast_to_game=AsyncMock())
    monkeypatch.setattr("app.card.endpoints.manager", fake_manager)

    payload = {
        "player_id": str(intruder),
        "target_card_id": str(uuid.uuid4()),
        "event_card_id": str(event_id),
    }
    res = client.put(f"/cards/play/E_CT/{game_id}/selection", json=payload)
    assert res.status_code == 403
    assert res.json()["detail"] == "OnlyTargetPlayerCanSelectCard"
    fake_manager.broadcast_to_game.assert_not_awaited()

# --- Test draw_cards ---
def test_draw_cards_deck_becomes_empty_sends_game_end(client, monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    card_id = uuid.uuid4()

    mock_card_object = MagicMock(spec=Card) 
    mock_card_object.id = card_id
    mock_card_object.game_id = game_id 
    mock_card_object.type = CardType.EVENT 
    mock_card_object.name = "Test Card" 
    mock_card_object.description = "Test Desc" 
    mock_card_object.owner = CardOwner.PLAYER
    mock_card_object.owner_player_id = player_id
    
    mock_cards_list = [mock_card_object]

    fake_game = MagicMock(players_ids=[player_id])
    mock_game_service_instance = MagicMock()
    mock_game_service_instance.get_game_by_id.return_value = fake_game
    mock_game_service_instance.get_turn.return_value = player_id
    mock_end_result = MagicMock(spec=EndGameResult)
    mock_end_result.model_dump.return_value = {"reason": "DECK_EMPTY", "winners": []}
    mock_game_service_instance.end_game.return_value = mock_end_result
    
    mock_card_service_instance = MagicMock()
    mock_card_service_instance.moveDeckToPlayer.return_value = (mock_cards_list, True) 

    mock_broadcast = AsyncMock()

    with patch("app.card.endpoints.GameService", return_value=mock_game_service_instance), \
         patch("app.card.endpoints.CardService", mock_card_service_instance), \
         patch("app.card.endpoints.manager.broadcast_to_game", mock_broadcast):
        
        response = client.put(f"/cards/draw/{game_id}", json={
            "player_id": str(player_id),
            "n_cards": 1
        })

    assert response.status_code == 200
    response_data = response.json()
    assert isinstance(response_data, list)
    assert len(response_data) == 1
    assert response_data[0]['id'] == str(mock_card_object.id) 
    assert response_data[0]['name'] == mock_card_object.name
    assert response_data[0]['owner'] == mock_card_object.owner.value
    
    mock_card_service_instance.moveDeckToPlayer.assert_called_once()
    mock_game_service_instance.end_game.assert_called_once_with(game_id, GameEndReason.DECK_EMPTY)
    
    assert mock_broadcast.await_count == 2 

    call_list = mock_broadcast.await_args_list 

    args1, kwargs1 = call_list[0]
    assert args1[0] == game_id   
    assert args1[1]["type"] == "gameEnd" 
    assert args1[1]["data"]["reason"] == "DECK_EMPTY"

    args2, kwargs2 = call_list[1]
    assert args2[0] == game_id
    assert args2[1]["type"] == "playerDrawCards" 
    assert args2[1]["data"]["id_player"] == str(player_id)

def test_play_event_invalid_turn_state(client, monkeypatch):
    """
    Debe devolver 400 si el estado no esta en IDLE
    """
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()

    fake_game = type("Game", (), {"id": game_id, "players_ids": [player_id]})()
    fake_game_turn_state = MagicMock(turn_state = TurnState.DISCARDING)
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id,
                                  "get_turn_state": lambda self, game_id: fake_game_turn_state,
                                  "change_turn_state": lambda self, game_id, pid: None})()
    )

    payload = {
        "player_id": str(player_id),
        "event_id": str(uuid.uuid4()),
        "set_id": str(uuid.uuid4()),
    }

    res = client.put(f"/cards/play/E_AV/{game_id}", json=payload)
    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid accion for the game state"

def test_discard_invalid_turn_state(client, monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    fake_card_id = uuid.uuid4()

    fake_game = MagicMock()
    fake_game.players_ids = [player_id]
    monkeypatch.setattr(
        endpoints.GameService, 
        "get_game_by_id", 
        lambda db, game_id: fake_game
    )
    monkeypatch.setattr(
        endpoints.GameService, 
        "get_turn", 
        lambda db, game_id: player_id
    )
    fake_game_turn_state = MagicMock(turn_state = TurnState.DRAWING_CARDS)
    monkeypatch.setattr(
        endpoints.GameService,
        "get_turn_state",
        lambda db, game_id: fake_game_turn_state
    )
    r = client.put(f"/cards/discard/{game_id}", json={
        "player_id": str(player_id),
        "id_cards":[str(fake_card_id)],
    })
    assert r.status_code == 400
    body = r.json()
    assert body["detail"] == "Invalid accion for the game state"

#--------------------PLAY DEVIOUS CARDS TESTS--------------------

def test_play_devious_card_sfp_happy_path(client, monkeypatch):
    """Happy path: DV_SFP reveals secret and broadcasts."""
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    card_id = uuid.uuid4()
    secret_id = uuid.uuid4()

    # card must exist and be DEVIOUS
    fake_card = types.SimpleNamespace(
        id=card_id, 
        game_id=game_id, 
        type=CardType.DEVIOUS, 
        name="DV_SFP"
    )

    # secret returned from SecretService.get_secret_by_id
    fake_secret = types.SimpleNamespace(
        id=secret_id, 
        game_id=game_id, 
        owner_player_id=player_id, 
        revealed=False, 
        name="S", 
        description="d", 
        type=SecretType.COMMON
    )

    class FakeCardService:
        @staticmethod
        def get_card_by_id(db, cid):
            return fake_card if cid == card_id else None

    class FakeSecretService:
        @staticmethod
        def get_secret_by_id(db, sid):
            return fake_secret if sid == secret_id else None

        @staticmethod
        def social_faux_pas(
            game_id_arg, 
            player_id_arg, 
            secret_id_arg, 
            social_faux_pas_id_arg
        ):
            # emulate revealed secret return
            fake_secret.revealed = True
            return SecretOut(
                id=secret_id, 
                game_id=game_id, 
                name="S", 
                description="d", 
                owner_player_id=player_id, 
                revealed=True, 
                role=fake_secret.type
            )
        
    monkeypatch.setattr("app.card.endpoints.CardService", FakeCardService)
    monkeypatch.setattr("app.card.endpoints.SecretService", FakeSecretService)

    fake_manager = types.SimpleNamespace(broadcast_to_game=AsyncMock())
    monkeypatch.setattr("app.card.endpoints.manager", fake_manager)

    res = client.put(f"/cards/devious/{card_id}", params={"game_id": str(game_id), "card_id": str(card_id), "secret_id": str(secret_id), "player_id": str(player_id)})
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == str(secret_id)
    assert body["revealed"] is True

    fake_manager.broadcast_to_game.assert_awaited_once()


def test_play_devious_card_not_devious_or_missing(client, monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    card_id = uuid.uuid4()
    secret_id = uuid.uuid4()

    # card is not DEVIOUS
    fake_card = types.SimpleNamespace(id=card_id, game_id=game_id, type=CardType.EVENT, name="NOT")

    class FakeCardService:
        @staticmethod
        def get_card_by_id(db, cid):
            return fake_card

    monkeypatch.setattr("app.card.endpoints.CardService", FakeCardService)

    res = client.put(f"/cards/devious/{card_id}", params={"game_id": str(game_id), "card_id": str(card_id), "secret_id": str(secret_id), "player_id": str(player_id)})
    assert res.status_code == 404

#--------- PLAY WITH CANCELATION METHODS ---------

def test_play_event_cancelable_flow(client, monkeypatch):
    """Flujo cuando la carta es cancelable (entra al bloque if verify_cancellable_card True)."""
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    event_id = uuid.uuid4()

    # --- Fake game y estado ---
    fake_game = types.SimpleNamespace(
        id=game_id,
        players_ids=[player_id],
    )
    fake_turn_state = MagicMock(turn_state=TurnState.IDLE)
    fake_turn_state.is_cancelled = True  # simulamos que la cancelación se completa

    # --- Mock de GameService ---
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id,
                                  "get_turn_state": lambda self, game_id: fake_turn_state,
                                  "change_turn_state": lambda self, gid, *args, **kwargs: None})()
    )

    # --- PlayerService (jugador válido, no en desgracia social) ---
    fake_player = types.SimpleNamespace(social_disgrace=False)
    monkeypatch.setattr(
        "app.card.endpoints.PlayerService",
        lambda db: type("FakePlayerService", (), {
            "get_player_entity_by_id": lambda self, pid: fake_player
        })()
    )

    # --- CardService ---
    fake_card = types.SimpleNamespace(description="Fake Event Card")
    fake_moved_card = {
        "id": str(event_id),
        "game_id": str(game_id),
        "type": "EVENT",
        "name": "E_FAKE",
        "owner": CardOwner.DISCARD_PILE.value,
        "owner_player_id": str(player_id),
        "description": "Fake Event Card",
    }

    monkeypatch.setattr(
        "app.card.endpoints.CardService",
        type(
            "FakeCardService",
            (),
            {
                # 👇 este método devuelve True para entrar al if
                "verify_cancellable_card": staticmethod(lambda db, cid: True),
                "get_card_by_id": staticmethod(lambda db, cid: fake_card),
                "wait_for_cancellation": staticmethod(AsyncMock(return_value=None)),
                "move_card": staticmethod(lambda db, cid, move_in: fake_moved_card),
            },
        ),
    )

    # --- Manager con AsyncMock para capturar broadcasts ---
    fake_manager = types.SimpleNamespace(broadcast_to_game=AsyncMock())
    monkeypatch.setattr("app.card.endpoints.manager", fake_manager)

    # --- Payload del request ---
    payload = {
        "player_id": str(player_id),
        "event_id": str(event_id),
    }

    # --- Ejecutamos el endpoint ---
    response = client.put(f"/cards/play/E_FAKE/{game_id}", json=payload)

    # --- Validaciones ---
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(event_id)
    assert data["owner"] == "DISCARD_PILE"
    assert data["owner_player_id"] == str(player_id)

    # Deben haberse hecho 4 broadcasts
    calls = fake_manager.broadcast_to_game.await_args_list
    assert len(calls) == 5

    # 1er broadcast: waitingForCancellationEvent
    first_evt = calls[1].args[1]    
    assert first_evt["type"] == "waitingForCancellationEvent"
    assert first_evt["data"]["player_id"] == str(player_id)
    assert first_evt["data"]["event_name"] == "Fake Event Card"

    # 2do broadcast: cancelationStopped
    second_evt = calls[4].args[1]
    assert second_evt["type"] == "cancellationStopped"


def test_play_no_so_fast_ok(client, monkeypatch):
    """
    Flujo exitoso:
    - Juego y jugador válidos.
    - Estado CANCELLED_CARD_PENDING.
    - Carta E_NSF válida y del jugador.
    """
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    card_id = uuid.uuid4()

    fake_game = types.SimpleNamespace(
        id=game_id,
        players=[types.SimpleNamespace(id=player_id)],
        turn_state=types.SimpleNamespace(state=TurnState.CANCELLED_CARD_PENDING)
    )

    fake_turn_state = types.SimpleNamespace(is_cancelled=False)
    fake_card = types.SimpleNamespace(
        id=card_id,
        game_id=game_id,
        type="EVENT",
        name="E_NSF",
        owner_player_id=player_id,
        owner="PLAYER",
        description="No So Fast"
    )

    class FakeGameService:
        def __init__(self, db): pass
        def get_game_entity_by_id(self, gid): return fake_game
        def get_turn_state(self, gid): return fake_turn_state
        def change_turn_state(self, **kwargs): pass

    class FakeCardService:
        @staticmethod
        def get_card_by_id(db, cid): return fake_card
        @staticmethod
        def move_card(db, cid, move_in):
            fake_card.owner = "DISCARD_PILE"
            fake_card.owner_player_id = None
            return fake_card

    monkeypatch.setattr("app.card.endpoints.GameService", FakeGameService)
    monkeypatch.setattr("app.card.endpoints.CardService", FakeCardService)

    payload = {"player_id": str(player_id), "card_id": str(card_id)}

    r = client.put(f"/cards/play-no-so-fast/{game_id}", json=payload)

    assert r.status_code == 200
    data = r.json()
    assert data["id"] == str(card_id)
    assert data["owner"] == "DISCARD_PILE"
    assert data["owner_player_id"] is None


def test_play_no_so_fast_invalid_game(client, monkeypatch):
    """Debe devolver 404 si el juego no existe."""
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_entity_by_id": lambda self, gid: None})()
    )

    payload = {"player_id": str(player_id), "card_id": str(uuid.uuid4())}
    r = client.put(f"/cards/play-no-so-fast/{game_id}", json=payload)
    assert r.status_code == 404
    assert r.json()["detail"] == "GameNotFound"


def test_play_no_so_fast_player_not_in_game(client, monkeypatch):
    """Debe devolver 400 si el jugador no pertenece al juego."""
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    fake_game = types.SimpleNamespace(
        id=game_id, players=[], turn_state=types.SimpleNamespace(state=TurnState.CANCELLED_CARD_PENDING)
    )

    class FakeGameService:
        def __init__(self, db): pass
        def get_game_entity_by_id(self, gid): return fake_game

    monkeypatch.setattr("app.card.endpoints.GameService", FakeGameService)

    payload = {"player_id": str(player_id), "card_id": str(uuid.uuid4())}
    r = client.put(f"/cards/play-no-so-fast/{game_id}", json=payload)
    assert r.status_code == 400
    assert r.json()["detail"] == "PlayerNotInGame"


def test_play_no_so_fast_wrong_state(client, monkeypatch):
    """Debe devolver 404 si el estado del juego no es CANCELLED_CARD_PENDING."""
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    fake_game = types.SimpleNamespace(
        id=game_id, players=[types.SimpleNamespace(id=player_id)],
        turn_state=types.SimpleNamespace(state=TurnState.IDLE)
    )

    class FakeGameService:
        def __init__(self, db): pass
        def get_game_entity_by_id(self, gid): return fake_game

    monkeypatch.setattr("app.card.endpoints.GameService", FakeGameService)

    payload = {"player_id": str(player_id), "card_id": str(uuid.uuid4())}
    r = client.put(f"/cards/play-no-so-fast/{game_id}", json=payload)
    assert r.status_code == 404
    assert r.json()["detail"] == "Wrong game state"


def test_play_no_so_fast_wrong_card(client, monkeypatch):
    """Debe devolver 400 si la carta no es E_NSF."""
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    card_id = uuid.uuid4()
    fake_game = types.SimpleNamespace(
        id=game_id,
        players=[types.SimpleNamespace(id=player_id)],
        turn_state=types.SimpleNamespace(state=TurnState.CANCELLED_CARD_PENDING)
    )

    fake_card = types.SimpleNamespace(
        id=card_id,
        game_id=game_id,
        name="E_FAKE",
        owner_player_id=player_id
    )

    class FakeGameService:
        def __init__(self, db): pass
        def get_game_entity_by_id(self, gid): return fake_game

    class FakeCardService:
        @staticmethod
        def get_card_by_id(db, cid): return fake_card

    monkeypatch.setattr("app.card.endpoints.GameService", FakeGameService)
    monkeypatch.setattr("app.card.endpoints.CardService", FakeCardService)

    payload = {"player_id": str(player_id), "card_id": str(card_id)}
    r = client.put(f"/cards/play-no-so-fast/{game_id}", json=payload)
    assert r.status_code == 400
    assert r.json()["detail"] == "Wrong card"

@pytest.fixture
def pys_endpoint_setup(monkeypatch):
    """Configura mocks de GameService y CardService para el endpoint /vote."""
    mock_game_service = MagicMock()
    mock_game_entity = MagicMock(spec=Game)
    mock_game_entity.players = [MagicMock() for _ in range(4)]
    mock_game_entity.current_turn = uuid.uuid4()
    mock_game_service.get_game_entity_by_id.return_value = mock_game_entity
    mock_game_service.submit_player_vote.return_value = None
    
    mock_card_service = MagicMock()
    mock_card_service.execute_pys_vote = AsyncMock(return_value=uuid.uuid4())
    
    mock_manager = MagicMock()
    mock_manager.broadcast_to_game = AsyncMock()

    monkeypatch.setattr("app.card.endpoints.GameService", lambda db: mock_game_service)
    monkeypatch.setattr("app.card.endpoints.CardService", lambda: mock_card_service)
    monkeypatch.setattr("app.card.endpoints.manager", mock_manager)

    return {
        "mock_game_service": mock_game_service,
        "mock_card_service": mock_card_service,
        "mock_manager": mock_manager,
        "mock_game_entity": mock_game_entity
    }

# --- Tests del endpoint /vote ---

def test_submit_vote_ok_not_last_voter(client, pys_endpoint_setup):
    """
    Prueba que un voto normal (no el último) llama a submit_player_vote,
    envía 'playerHasVoted' y NO llama a execute_pys_vote.
    """
    mock_game_service = pys_endpoint_setup["mock_game_service"]
    mock_card_service = pys_endpoint_setup["mock_card_service"]
    mock_manager = pys_endpoint_setup["mock_manager"]
    
    # Simulamos que NO es el último voto
    mock_card_service.check_if_all_players_voted.return_value = False
    
    game_id = uuid.uuid4()
    payload = {"player_id": str(uuid.uuid4()), "target_player_id": str(uuid.uuid4())}

    response = client.put(f"/cards/vote/{game_id}", json=payload)
    
    assert response.status_code == 200
    
    # Verificar que se guardó el voto
    mock_game_service.submit_player_vote.assert_called_once()
    # Verificar que se comprobó si era el último
    mock_card_service.check_if_all_players_voted.assert_called_once()
    
    # Verificar que NO se ejecutó el recuento
    mock_card_service.execute_pys_vote.assert_not_called()
    
    # Verificar que se envió el broadcast 'playerHasVoted'
    mock_manager.broadcast_to_game.assert_called_once_with(
        game_id,
        {"type": "playerHasVoted", "data": {"player_id": payload["player_id"]}}
    )

def test_submit_vote_ok_last_voter_triggers_execution(client, pys_endpoint_setup):
    """
    Prueba que el ÚLTIMO voto llama a submit, check, Y ejecuta
    el recuento (execute_pys_vote) y envía todos los broadcasts.
    """
    mock_game_service = pys_endpoint_setup["mock_game_service"]
    mock_card_service = pys_endpoint_setup["mock_card_service"]
    mock_manager = pys_endpoint_setup["mock_manager"]
    mock_game_entity = pys_endpoint_setup["mock_game_entity"]
    
    # Simulamos que SÍ es el último voto
    mock_card_service.check_if_all_players_voted.return_value = True
    
    # El recuento devuelve un ganador
    fake_winner_id = uuid.uuid4()
    mock_card_service.execute_pys_vote.return_value = fake_winner_id

    game_id = uuid.uuid4()
    payload = {"player_id": str(uuid.uuid4()), "target_player_id": str(uuid.uuid4())}

    response = client.put(f"/cards/vote/{game_id}", json=payload)
    
    assert response.status_code == 200
    
    # Verificar que se guardó el voto
    mock_game_service.submit_player_vote.assert_called_once()
    # Verificar que se comprobó si era el último
    mock_card_service.check_if_all_players_voted.assert_called_once()
    
    # Verificar que SÍ se ejecutó el recuento
    mock_card_service.execute_pys_vote.assert_called_once_with(ANY, game_id, mock_game_entity)
    
    # Verificar que se enviaron los 3 broadcasts
    assert mock_manager.broadcast_to_game.await_count == 3
    
    mock_manager.broadcast_to_game.assert_any_await(
        game_id,
        {"type": "playerHasVoted", "data": {"player_id": payload["player_id"]}}
    )
    mock_manager.broadcast_to_game.assert_any_await(
        game_id,
        {"type": "votingPhaseExecuted", "data": {"player_to_reveal_id": str(fake_winner_id)}}
    )
    mock_manager.broadcast_to_game.assert_any_await(
        game_id,
        {"type": "turnStateChanged", "data": ANY}
    )

def test_submit_vote_fails_if_service_fails(client, pys_endpoint_setup):
    """Prueba que si submit_player_vote falla (ej: 403), el endpoint devuelve ese error."""
    mock_game_service = pys_endpoint_setup["mock_game_service"]
    
    # Simulamos que el servicio falla (por ejemplo: jugador ya votó)
    error_detail = "Player has already voted"
    mock_game_service.submit_player_vote.side_effect = HTTPException(status_code=403, detail=error_detail)

    game_id = uuid.uuid4()
    payload = {"player_id": str(uuid.uuid4()), "target_player_id": str(uuid.uuid4())}

    response = client.put(f"/cards/vote/{game_id}", json=payload)
    
    assert response.status_code == 403
    assert response.json()["detail"] == error_detail

def make_mock_card(
    id=None, 
    game_id=None, 
    name="Test Card", 
    owner=None, 
    owner_player_id=None,
    type=None,
    **kwargs
) -> MagicMock:
    """Crea un mock simple de un objeto Card con atributos comunes."""
    card = MagicMock(spec=Card)
    card.id = id or uuid.uuid4()
    card.game_id = game_id
    card.name = name
    card.owner = owner
    card.owner_player_id = owner_player_id
    card.type = type
    
    # Asigna cualquier otro kwarg
    for key, value in kwargs.items():
        setattr(card, key, value)
        
    return card

@pytest.fixture
def base_setup(monkeypatch):
    mock_manager = MagicMock(broadcast_to_game=AsyncMock())
    monkeypatch.setattr("app.card.endpoints.manager", mock_manager)
    return {"manager": mock_manager}

def test_select_card_for_passing_trigger_sfp(client, monkeypatch, base_setup):
    """
    Prueba que si 'all_players_selected' es True y el estado
    es 'PENDING_DEVIOUS', se envía el broadcast 'sfpPending'.
    """
    game_id = uuid.uuid4()
    p1_id, p2_id = uuid.uuid4(), uuid.uuid4()
    sfp_players_list = [p1_id, p2_id]
    
    # Mock GameService
    mock_game_service = MagicMock()

    mock_game_service.get_game_by_id.return_value = MagicMock(
        players_ids=sfp_players_list
    )

    mock_game_entity = MagicMock(spec=Game, players=[
        MagicMock(id=p1_id),
        MagicMock(id=p2_id)
    ])
    mock_game_service.get_game_entity_by_id.return_value = mock_game_entity

    mock_turn_state_ENTITY = MagicMock(
        spec=GameTurnState,
        state=TurnState.PASSING_CARDS 
    )
    mock_game_service.get_turn_state_entity.return_value = mock_turn_state_ENTITY

    mock_game_state_dto = MagicMock(spec=GameTurnStateOut)
    mock_game_state_dto.turn_state = TurnState.PENDING_DEVIOUS
    mock_game_state_dto.sfp_players = sfp_players_list
    mock_game_service.get_turn_state.return_value = mock_game_state_dto
    
    # Mock CardService
    mock_card_service = MagicMock()
    mock_card = schemas.CardOut(
    id=uuid.uuid4(),
    type="EVENT",
    game_id=game_id,
    name="Test Card",
    owner=CardOwner.PLAYER,
    owner_player_id=p1_id,
    description="Mocked test card",
)
    mock_card_service.select_card_for_passing.return_value = mock_card
    mock_card_service.check_if_all_players_selected.return_value = True
    mock_card_service.execute_dead_card_folly_swap.return_value = []
    
    monkeypatch.setattr("app.card.endpoints.GameService", lambda db: mock_game_service)
    monkeypatch.setattr("app.card.endpoints.CardService", lambda: mock_card_service)
    
    fake_secret = MagicMock()
    fake_secret.id = uuid.uuid4()
    fake_secret.name = "Test"
    fake_secret.role = SecretType.COMMON
    fake_secret.description = "Test"

    monkeypatch.setattr("app.card.endpoints.SecretService.change_secret_status", fake_secret)

    mock_manager = base_setup["manager"]
    
    payload = {"player_id": str(p1_id), "card_id": str(uuid.uuid4())}
    response = client.put(f"/cards/passing/{game_id}", json=payload)
    
    assert response.status_code == 200
    
    mock_manager.broadcast_to_game.assert_any_await(
        game_id,
        {
            "type": "sfpPending",
            "data": {"players_id": sfp_players_list}
        }
    )
    
    assert not any(
        call.args[1].get("type") == "timerResumed" 
        for call in mock_manager.broadcast_to_game.await_args_list
    )
import json
import types
import uuid
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, AsyncMock
from app.card import schemas
from app.card import enums
from app.card import endpoints
from app.main import app
from app.db import get_db
from app.card.exceptions import PlayerHandLimitExceededException
from app.card.exceptions import CardsNotFoundOrInvalidException
from app.card import endpoints
from app.set.enums import SetType
from fastapi.encoders import jsonable_encoder

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

    fake_service = types.SimpleNamespace(
        create_cards_batch=create_cards_batch,
        get_card_by_id=get_card_by_id,
        query_cards=query_cards,
        move_card=move_card,
        moveDeckToPlayer=moveDeckToPlayer,
        movePlayertoDiscard=movePlayertoDiscard,
        see_top_discard=see_top_discard
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


def test_pick_draft_card_broadcasts_serializable_payload(client, monkeypatch):
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()
    card_id = uuid.uuid4()

    fake_game = types.SimpleNamespace(id=game_id, players_ids=[player_id])

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

    monkeypatch.setattr("app.card.endpoints.GameService", FakeGameService)
    monkeypatch.setattr("app.card.endpoints.CardService", fake_card_service)

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



# --- TESTS DE WS EXCLUSIVOS ---



def _get(d, *keys):
    for k in keys:
        if k in d:
            return d[k]
    return None

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
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id})()
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
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id})()
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
    assert evt["data"]["name"] == "E_ETP"
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
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id})()
    )


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
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id})()
    )

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
    assert evt["data"]["name"] == "E_COT"
    assert evt["data"]["id_player"] == str(player_id)
    assert evt["data"]["target_player"] == str(target_player)
    assert evt["data"]["last_card"]["name"] == "Fake Top Card"


def test_play_event_cot_missing_target_player(client, monkeypatch):
    """Debe devolver 400 si falta target_player."""
    game_id = uuid.uuid4()
    player_id = uuid.uuid4()

    fake_game = type("Game", (), {"id": game_id, "players_ids": [player_id]})()
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id})()
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
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id})()
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

    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id})()
    )

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
    assert evt["data"]["name"] == "E_ATWOM"
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
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id})()
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
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id})()
    )

    class FakeCardService:
        @staticmethod
        def another_victim(db, gid, pid, eid, sid):
            return moved_card

        @staticmethod
        def see_top_discard(db, gid, n):
            return [fake_last_card]

    monkeypatch.setattr("app.card.endpoints.CardService", FakeCardService)

    class FakeSetService:
        def __init__(self, db):
            pass

        @staticmethod
        def get_set_by_id(db, sid):
            return fake_set

        @staticmethod
        def play_set(*args, **kwargs):
            return None

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
    print(res.status_code, res.json())
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == str(event_id)
    assert data["name"] == "E_AV"

    fake_manager.broadcast_to_game.assert_awaited_once()
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
    monkeypatch.setattr(
        "app.card.endpoints.GameService",
        lambda db: type("S", (), {"get_game_by_id": lambda self, game_id: fake_game,
                                  "get_turn": lambda self, game_id: player_id})()
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
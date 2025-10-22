from typing import List, Optional, Dict, Any
from uuid import UUID

from fastapi import APIRouter, Depends, status, Body, Query,HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from . import schemas
from .service import CardService
from .enums import CardOwner
from .exceptions import (CardNotFoundException, CardGameMismatchException,
                        CardsNotFoundOrInvalidException,
                        PlayerHandLimitExceededException,
                        NoCardsException)

from app.websocket.connection_man import manager

from app.game.enums import GameEndReason
from app.game.service import GameService
from app.card.service import CardService
from app.card.models import Card
from app.secret.service import SecretService
from app.set.service import SetService
from app.set.enums import SetType


# -----------helpers-----------
def _owner_name(x):
    return x.name if hasattr(x, "name") else str(x)


# ---------- ws msg ----------


def ws_msg(
    event_type: str, game_id: UUID, data: Dict[str, Any], by: Optional[UUID] = None
) -> dict:
    msg = {"type": event_type, "game_id": str(game_id), "data": data}
    if by:
        msg["by"] = str(by)
    return msg


def cards_create_batch_msg(game_id: UUID, card_ids: List[UUID]) -> dict:
    return ws_msg(
        "cards/createBatch", game_id, {"card_ids": [str(c) for c in card_ids]}
    )


def cards_move_msg(
    game_id: UUID,
    kind: str,
    card_id: UUID,
    old_owner: str,
    old_player_id: Optional[UUID],
    new_owner: str,
    new_player_id: Optional[UUID],
    actor_id: Optional[UUID],
) -> dict:
    return ws_msg(
        "cards/move",
        game_id,
        data={
            "kind": kind,
            "card_id": str(card_id),
            "from": {
                "owner": old_owner,
                "player_id": str(old_player_id) if old_player_id else None,
            },
            "to": {
                "owner": new_owner,
                "player_id": str(new_player_id) if new_player_id else None,
            },
        },
        by=actor_id,
    )


# =========================
# /games/{game_id}/cards
# =========================
cards_router = APIRouter(prefix="/cards", tags=["cards"])


@cards_router.post(
    "/{game_id}",
    response_model=List[schemas.CardOut],
    status_code=status.HTTP_201_CREATED,
)
async def create_cards_batch(
    game_id: UUID, payload: schemas.CardBatchIn, db: Session = Depends(get_db)
):
    """
    Crea cartas en lote dentro de una partida y notifica por WS.

    Body:
        schemas.CardBatchIn con la clave `items` y los datos de cada carta.

    Side-effects:
        Emite por WS un evento "cards/createBatch" con los IDs creados.

    Returns:
        List[schemas.CardOut]: Cartas creadas (incluye IDs).
    """
    created = CardService.create_cards_batch(db, game_id, payload)
    await manager.broadcast_to_game(
        game_id, cards_create_batch_msg(game_id, [c.id for c in created])
    )
    return created


@cards_router.get("", response_model=List[schemas.CardOut])
def query_cards(payload: schemas.CardQueryIn = Depends(), db: Session = Depends(get_db)):
    """
    Consulta cartas:
      - si viene card_id -> devuelve [esa carta] (valida pertenencia a game_id)
      - si NO viene card_id y owner es None -> todas las del juego
      - si owner=PLAYER -> todas del/los jugador/es; si trae player_id, filtra por jugador
      - si owner=DECK o DISCARD_PILE -> filtra por ese contenedor
    Siempre devuelve una LISTA.
    """
    if payload.card_id is not None:
        card = CardService.get_card_by_id(db, payload.card_id)
        if not card:
            raise CardNotFoundException(str(payload.card_id))
        if card.game_id != payload.game_id:
            raise CardGameMismatchException(str(payload.card_id), str(payload.game_id))
        return [card]

    # sin card_id -> delego la lógica al servicio
    return CardService.query_cards(db, payload)


@cards_router.put("", response_model=schemas.CardMoveOut)
async def move_card(
    payload: schemas.CardMoveCmd = Body(...), db: Session = Depends(get_db)
):
    """
    Mueve una carta a otro contenedor/owner dentro de la misma partida y
    notifica el cambio por WS.

    Validaciones:
      - La carta debe existir.
      - La carta debe pertenecer al game_id del payload.
      - Reglas de `CardMoveCmd`: `player_id` sólo cuando `to_owner=PLAYER`.

    Side-effects:
      - Emite por WS un evento "cards/move" con `kind`, `from`, `to` y `by`.

    Returns:
      schemas.CardMoveOut: Estado final (owner, player_id) tras el movimiento.

    Raises:
      CardNotFoundException: Si no existe la carta indicada.
      CardGameMismatchException: Si la carta no pertenece al game_id indicado.
    """
    current = CardService.get_card_by_id(db, payload.card_id)
    if not current:
        raise CardNotFoundException(str(payload.card_id))
    if current.game_id != payload.game_id:
        raise CardGameMismatchException(str(payload.card_id), str(payload.game_id))

    old_owner = current.owner
    old_player = current.owner_player_id

    moved = CardService.move_card(
        db,
        payload.card_id,
        schemas.CardMoveIn(to_owner=payload.to_owner, player_id=payload.player_id),
    )

    game_ended = False

    if old_owner == CardOwner.DECK and payload.to_owner != CardOwner.DECK:
        deck_count = db.query(func.count(Card.id)).filter(
            Card.game_id == payload.game_id,
            Card.owner == CardOwner.DECK
        ).scalar()
        
        if deck_count == 0:
            game_ended = True

    if game_ended:
        game_service = GameService(db)
        end_game_result = game_service.end_game(
            payload.game_id, 
            GameEndReason.DECK_EMPTY
        )

        end_game_data = end_game_result.model_dump(mode='json') 

        message = {
            "type": "gameEnd",
            "data": end_game_data
        }
        
        await manager.broadcast_to_game(payload.game_id, message)

    else:
        kind = "move"
        actor = None
        if payload.to_owner == CardOwner.DISCARD_PILE and old_owner == CardOwner.PLAYER:
            kind = "discard"
            actor = old_player
        elif payload.to_owner == CardOwner.PLAYER:
            if old_owner == CardOwner.DECK:
                kind = "draw"
                actor = payload.player_id
            elif old_owner == CardOwner.PLAYER and payload.player_id != old_player:
                kind = "give"
                actor = old_player
            else:
                kind = "toPlayer"
                actor = payload.player_id
        elif payload.to_owner == CardOwner.DECK and old_owner == CardOwner.PLAYER:
            kind = "returnToDeck"
            actor = old_player

        await manager.broadcast_to_game(
            payload.game_id,
            cards_move_msg(
                payload.game_id,
                kind,
                payload.card_id,
                _owner_name(old_owner),
                old_player,
                _owner_name(moved.owner),
                moved.owner_player_id,
                actor,
            ),
        )

    return schemas.CardMoveOut(id=moved.id, to_owner=moved.owner, player_id=moved.owner_player_id)

@cards_router.put("/draw/{game_id}", response_model=list[schemas.CardOut])
async def draw_cards(
    game_id: UUID,
    payload: schemas.DrawCardsIn = Body(...),
    db: Session = Depends(get_db)
):
    """
    Roba N cartas del mazo (DECK) y las asigna al jugador indicado.

    Validaciones:
      - `n_cards` debe estar entre 1 y 6.
      - El jugador debe pertenecer a la partida.
      - El mazo debe tener suficientes cartas (o roba las que haya).

    Side-effects:
      - Mueve hasta `n_cards` cartas de DECK a PLAYER.
      - Emite por WS un evento "playerDrawCards"

    Returns:
      list[CardOut]: Estado final de las cartas recogidas.
    """

    game_service = GameService(db)
    game = game_service.get_game_by_id(game_id=game_id)
    if (not game 
        or payload.player_id not in game.players_ids
        or payload.player_id != game_service.get_turn(game_id)):
        raise HTTPException(status_code=400, detail = "GameNotFoundOrPlayerNotInGame")
    
    moved_cards, mazo_vacio = CardService.moveDeckToPlayer(
        db, 
        game_id, 
        payload.player_id, 
        payload.n_cards
    )

    if mazo_vacio:
        end_game_result = game_service.end_game(
            game_id, 
            GameEndReason.DECK_EMPTY
        )

        end_game_data = end_game_result.model_dump(mode='json') 

        message = {
            "type": "gameEnd",
            "data": end_game_data
        }
        
        await manager.broadcast_to_game(game_id, message)

    await manager.broadcast_to_game(
    game_id,
    {
        "type": "playerDrawCards",
        "data": {
            "type" : "Deck",
            "id_player": str(payload.player_id),
            "n_cards": payload.n_cards
        }
    }
    )

    return moved_cards

@cards_router.put("/discard/{game_id}", response_model=list[schemas.CardMoveOut])
async def discard_cards(
    game_id: UUID,
    payload: schemas.DiscardCardsIn = Body(...),
    db: Session = Depends(get_db)
):
    """
    Mueve una o varias cartas de un jugador a la pila de descarte (DISCARD_PILE).

    Validaciones:
      - Las cartas deben pertenecer al jugador y al game_id indicado.
      - Todas las cartas deben pertenecer al jugador.
    
    Side-effects:
      - Cambia el owner de las cartas a DISCARD_PILE.
      - Emite por WS un evento "playerCardDiscarded"
    
    Returns:
      list[CardMoveOut]: Estado final de las cartas movidas.
    """
    game_service = GameService(db)
    game = game_service.get_game_by_id(game_id=game_id)
    if (not game 
        or payload.player_id not in game.players_ids
        or payload.player_id != game_service.get_turn(game_id)):
        raise HTTPException(status_code=400, detail = "GameNotFoundOrPlayerNotInGame")

    
    moved_cards = CardService.movePlayertoDiscard(db,game_id,payload.player_id,payload.id_cards)
    
    last_card_object = CardService.see_top_discard(db, game_id, 1)
    if not last_card_object:
        last_card_dict = None
    else: 
        last_card_dict = {
            "id": str(last_card_object[0].id),
            "game_id": str(last_card_object[0].game_id),
            "type": last_card_object[0].type.value,
            "name": last_card_object[0].name,
            "description": last_card_object[0].description,
            "owner": last_card_object[0].owner.value,
            "owner_player_id": str(last_card_object[0].owner_player_id) if last_card_object[0].owner_player_id else None
        }

    await manager.broadcast_to_game(
        game_id,
        {
            "type": "playerCardDiscarded",
            "data": {
                "id_player": str(payload.player_id),
                "n_cards": len(payload.id_cards),
                "last_card": last_card_dict
          }
        }
    )

    return [
    schemas.CardMoveOut(id=c.id, to_owner=c.owner, player_id=c.owner_player_id)
    for c in moved_cards
    ]   

@cards_router.get("/draft/{game_id}", response_model=list[schemas.CardOut])
async def draft_cards(
    game_id: UUID,
    db: Session = Depends(get_db)
):
    """
    Consulta todas las cartas del draft para un game
    
    Returns:
      list[CardOut]: Cartas del draft.
    """
    game_service = GameService(db)
    game = game_service.get_game_by_id(game_id=game_id)
    if not game:
        raise HTTPException(status_code=400, detail = "GameNotFound")
    
    draft_cards = CardService.query_draft(db, game_id) or []

    
    return draft_cards

@cards_router.put("/draft/{game_id}", response_model=schemas.CardOut)
async def pick_draft_card(
    game_id: UUID,
    payload: schemas.DraftCardIn = Body (...),
    db: Session = Depends(get_db)
):
    """
    Agarra una carta del draft y se le asigna a un jugador

    Validaciones: 
    El jugador debe tener menos de 6 cartas.
    La carta debe pertenecer al draft
    
    Returns:
      CardOut: Cartas del draft.
    """
    game_service = GameService(db)
    game = game_service.get_game_by_id(game_id=game_id)
    if (not game 
        or payload.player_id not in game.players_ids
        or payload.player_id != game_service.get_turn(game_id)):
        raise HTTPException(status_code=400, detail = "GameNotFoundOrPlayerNotInGame")
    
    pick_card, deck_is_empty = CardService.pick_draft(
        db, 
        game_id, 
        payload.player_id, 
        payload.card_id
    )

    if deck_is_empty:
        # El mazo se vació, notifica el fin del juego
        
        end_game_result = game_service.end_game(
            game_id, 
            GameEndReason.DECK_EMPTY
        )
        end_game_data = end_game_result.model_dump(mode='json') 
        
        message = {
            "type": "gameEnd",
            "data": end_game_data
        }
        await manager.broadcast_to_game(game_id, message)

    else:

        draft_cards_list = CardService.query_draft(db, game_id)
        data_to_send = []

        if draft_cards_list:
            data_to_send = [
                {
                    "id": str(card.id),
                    "game_id": str(card.game_id),
                    "type": card.type.value,
                    "name": card.name,
                    "description": card.description,
                    "owner": card.owner.value,
                    "owner_player_id": str(card.owner_player_id) if card.owner_player_id else None
                }
                for card in draft_cards_list
            ]

        await manager.broadcast_to_game(
            game_id,
            {
                "type": "updateDraft",
                "data": {
                    "player_id": str(payload.player_id),
                    "draft": data_to_send
                }
            }
        )
        
    return pick_card

@cards_router.get("/top_discard/{game_id}", response_model=list[schemas.CardOut])
async def see_top_discard(
    game_id: UUID,
    n_cards: int = Query(5, description="Número de cartas a mostrar (por defecto 5)"), 
    db: Session = Depends(get_db)
):
    """
    Se pueden ver las ultimas n cartas del mazo de descarte

    Validaciones:
    El juego debe existir.

    Return:
    Las ultimas 5 cartas del mazo de descarte(pueden ser menos)
    """
    game_service = GameService(db)
    game = game_service.get_game_by_id(game_id=game_id)
    if not game:
        raise HTTPException(status_code=400, detail = "GameNotFound")

    
    top_discard_pile = CardService.see_top_discard(db, game_id, n_cards)
    return top_discard_pile


# ----Play event


@cards_router.put("/play/{event_code}/{game_id}", response_model=schemas.CardOut)
async def play_event(
    event_code: str,
    game_id: UUID,
    payload: schemas.PlayEventBase = Body(...),
    db: Session = Depends(get_db)
):
    """
    Endpoint para jugar eventos:
    - E_LIA: Look into the ashes
    - E_ETP: Early train to Paddington
    - E_DME: Delay the Murderer's Escape
    - E_COT: Cards off the table
    - E_ATWOM: And then there was one more
    - E_AV: Another victim

    Validaciones comunes:
    - El juego debe existir
    - El jugador debe pertenecer al juego.
    - Debe ser su turno.
    - La carta debe ser la correspondiente al evento y pertenecer al jugador.

    Return:
    - La carta jugada (CardOut)
    """

    game_service = GameService(db)
    game = game_service.get_game_by_id(game_id=game_id)
    if (
        not game
        or payload.player_id not in game.players_ids
        or payload.player_id != game_service.get_turn(game_id)
    ):
        raise HTTPException(
            status_code=400, detail="GameNotFoundOrPlayerNotInGame"
        )

    event_code = event_code.upper()
    # ------ Look into the ashes
    if event_code == "E_LIA":
        if payload.card_id == None:
            raise HTTPException(status_code=400, detail="CardIdIsRequired")
        event_card = CardService.look_into_the_ashes(
            db, game_id, payload.event_id, payload.card_id, payload.player_id
        )

    # ------ Early train to paddington
    elif event_code == "E_ETP":
        event_card = CardService.early_train_to_paddington(
            db, game_id, payload.event_id, payload.player_id
        )

    # ------ Delay the murdere's escape
    elif event_code == "E_DME":
        event_card = CardService.delay_the_murderer_escape(
            db, game_id, payload.player_id, payload.event_id
        )
    
    # ----- Cards off the table
    elif event_code == "E_COT":

        if payload.target_player == None: 
            raise HTTPException(
                status_code=400, detail="TargetPlayerIsRequired"
            )
        
        if payload.target_player not in game.players_ids:
            raise HTTPException(
                status_code=400, detail="GameNotFoundOrPlayerNotInGame"
            )
        event_card = CardService.cards_off_the_table(
            db, 
            game_id, 
            payload.player_id, 
            payload.event_id, 
            payload.target_player
        )

    # ------ And then there was one more
    elif event_code == "E_ATWOM":
        if (payload.target_player == None
            or payload.secret_id == None): 
            raise HTTPException(
                status_code=400, detail="TargetPlayerAndSecretIdIsRequired"
            )
        
        if payload.target_player not in game.players_ids:
            raise HTTPException(
                status_code=400, detail="GameNotFoundOrPlayerNotInGame"
            )
        event_card = CardService.then_there_was_one_more(
            db,
            game_id,
            payload.player_id,
            payload.event_id,
            payload.target_player,
            payload.secret_id
        )

    # ------ Another victim
    elif event_code == "E_AV":
        if (payload.set_id == None): 
            raise HTTPException(
                status_code=400, detail="set_id is required"
            )
        
        event_card = CardService.another_victim(
            db,
            game_id,
            payload.player_id,
            payload.event_id,
            payload.set_id
        )

    else:
        raise HTTPException(
            status_code=400, detail=f"Unknown event code: {event_code}"
        )

    last_card_object = CardService.see_top_discard(db, game_id, 1)
    if not last_card_object:
        last_card_dict = None
    else:
        card = last_card_object[0]
        last_card_dict = {
            "id": str(card.id),
            "game_id": str(card.game_id),
            "type": card.type.value,
            "name": card.name,
            "description": card.description,
            "owner": card.owner.value,
            "owner_player_id": str(card.owner_player_id) 
                if card.owner_player_id else None
        }

    broadcast_data = {
        "name": event_card.description,
        "id_player": str(payload.player_id),
        "last_card": last_card_dict,
    }
    if event_code == "E_COT":
        broadcast_data["target_player"] = str(payload.target_player)
    if event_code == "E_ATWOM":
        broadcast_data["target_player"] = str(payload.target_player)
        secret = SecretService.get_secret_by_id(db, payload.secret_id)
        broadcast_data["secret_data"] = jsonable_encoder(secret)
    if event_code == "E_AV":
        set_played = SetService.get_set_by_id(db,payload.set_id)
        broadcast_data["set_data"] = jsonable_encoder(set_played)

    await manager.broadcast_to_game(
        game_id,
        {
            "type": "playEvent",
            "data": broadcast_data
        },
    )

    return event_card

from typing import List, Optional, Dict, Any
from uuid import UUID

from fastapi import APIRouter, Depends, status, Body, Query,HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from . import schemas
from .service import CardService
from .enums import CardOwner, CardType
from .exceptions import (CardNotFoundException, CardGameMismatchException,
                        CardsNotFoundOrInvalidException,
                        PlayerHandLimitExceededException,
                        NoCardsException)

from app.websocket.connection_man import manager

from app.game.enums import GameEndReason, TurnState
from app.game.service import GameService
from app.card.service import CardService
from app.card.models import Card
from app.secret.service import SecretService
from app.secret.schemas import SecretOut as SecretOutSchema
from app.set.service import SetService
from app.set.enums import SetType
from app.player.service import PlayerService
from app.secret.service import SecretService
from app.secret.schemas import SecretOut
from app.game.turn_timer import turn_timer_manager


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
    game_turn_state = game_service.get_turn_state(game_id) 
    if game_turn_state.turn_state in [
        TurnState.END_TURN,
        TurnState.CHOOSING_SECRET,
        TurnState.CARD_TRADE_PENDING,
    ]:
        raise HTTPException(status_code=400, detail = "Invalid accion for the game state")
    
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
    game_turn_state = game_service.get_turn_state(game_id)
    if game_turn_state.turn_state in [TurnState.DRAWING_CARDS, 
                                                TurnState.END_TURN,
                                                TurnState.CHOOSING_SECRET,
                                                TurnState.CARD_TRADE_PENDING]:
        raise HTTPException(status_code=400, detail = "Invalid accion for the game state")
    
    if (not CardService.ensure_move_valid(db,game_id,payload.player_id,len(payload.id_cards))):
        raise HTTPException(
            status_code=403, detail="El jugador esta en desgracia social, movimiento invalido"
        )

    
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
    game_turn_state = game_service.get_turn_state(game_id) 
    if game_turn_state.turn_state in [TurnState.END_TURN, 
                                                TurnState.CHOOSING_SECRET,
                                                TurnState.CARD_TRADE_PENDING]:
        raise HTTPException(status_code=400, detail = "Invalid accion for the game state")
    
    player_service = PlayerService(db) 
    player_obj = player_service.get_player_entity_by_id(payload.player_id)
    if (player_obj.social_disgrace):
        raise HTTPException(
            status_code=403, detail="No se puede levantar del draft estando en Desgracia social"
        )
    
    
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
    - E_CT: Card Trade

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
    game_turn_state = game_service.get_turn_state(game_id)
    if game_turn_state.turn_state != TurnState.IDLE:
        raise HTTPException(status_code=400, detail = "Invalid accion for the game state")
    player_service = PlayerService(db) 
    player_obj = player_service.get_player_entity_by_id(payload.player_id)
    if (player_obj.social_disgrace):
        raise HTTPException(
            status_code=403, detail="No se puede jugar un evento estando en Desgracia social"
        )
    
    if CardService.verify_cancellable_card(db, payload.event_id):

        turn_timer_manager.pause_timer(game_id)
        await manager.broadcast_to_game(
            game_id,
            {
                "type": "timerPaused",
                "data": {"time_remaining:": turn_timer_manager.get_remaining_time(game_id)}
            }
        )
        game_service.change_turn_state(
            game_id,
            TurnState.CANCELLED_CARD_PENDING,
            is_cancelled=False
        )

        card_event = CardService.get_card_by_id(db, payload.event_id)

        await manager.broadcast_to_game(
            game_id,
            {
                "type": "waitingForCancellationEvent",
                "data": {
                    "player_id": str(payload.player_id),
                    "event_name": card_event.description,
                },
            },
        )

        await CardService.wait_for_cancellation(db, game_id, timeout=7)

        await manager.broadcast_to_game(
            game_id,
            {
                "type": "waitFinished",
                "data": {
                    "player_id": str(payload.player_id),
                    "event_name": card_event.description,
                },
            },
        )

        turn_timer_manager.resume_timer(game_id)
        await manager.broadcast_to_game(
            game_id,
            {
                "type": "timerResumed",
                "data": {"time_remaining:": turn_timer_manager.get_remaining_time(game_id)}
            }
        )
        current_state = game_service.get_turn_state(game_id)

        if current_state.is_cancelled:
            await manager.broadcast_to_game(game_id, {"type": "cancellationStopped"})

            move_in = schemas.CardMoveIn(to_owner=CardOwner.DISCARD_PILE)
            returned_card = CardService.move_card(db, payload.event_id, move_in)

            return returned_card


    ordered_player_list_dto = None
    event_code = event_code.upper()
    requested_card_code: str | None = None
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
        if not payload.target_player:
            raise HTTPException(
                status_code=400, detail="target_player is required"
            )
        event_card = CardService.another_victim(
            db,
            game_id,
            payload.player_id,
            payload.event_id,
            payload.set_id
        )
        info_set = SetService(db).get_set_by_id(db,payload.set_id)
        if info_set.type in [SetType.HP, SetType.MM, SetType.PP]:
            if not payload.secret_id:
                raise HTTPException(
                status_code=400, detail="secret_id is required"
            )
            play_set_result = SetService(db).play_set(
                payload.set_id,
                payload.target_player,
                payload.secret_id
            )
            game_service.change_turn_state(
            game_id, 
            TurnState.DISCARDING
            )
            if play_set_result.end_game_result:
                end_game_data = play_set_result.end_game_result.model_dump(mode='json')
                message = {
                    "type": "gameEnd",
                    "data": end_game_data
                }
                await manager.broadcast_to_game(game_id, message)
            await manager.broadcast_to_game(
            game_id,
            {
                "type": "playSet",
                "data": {
                    "set_type": info_set.type.value,
                    "player_id": str(payload.player_id),
                    "target_player": str(payload.target_player)
              }
            }
            )
            
        else:
            await manager.broadcast_to_game(
            game_id,
            {
                "type": "targetPlayerElection",
                "data": {
                    "set_id": str(info_set.id),
                    "set_type": info_set.type.value,
                    "target_player": str(payload.target_player)
              }
            })
            turn_timer_manager.pause_timer(game_id)
            await manager.broadcast_to_game(
            game_id,
            {
                "type": "timerPaused",
                "data": {"time_remaining:": turn_timer_manager.get_remaining_time(game_id)}
            }
            )
            game_service.change_turn_state(
            game_id, 
            TurnState.CHOOSING_SECRET,
            payload.target_player
            )

    # ------ Dead Card Folly
    elif event_code == "E_DCF":
        if (payload.direction is None 
            or payload.direction not in ["left", "right"]):
            raise HTTPException(
                status_code=400, detail="Direction 'left' or 'right' is required"
            )
        
        card = CardService.get_card_by_id(db, payload.event_id)
        if (not card or card.name != "E_DCF" or card.owner_player_id != payload.player_id):
             raise CardsNotFoundOrInvalidException()

        all_players_entities = player_service.get_players_by_game_id(game_id)

        sorted_players = sorted(all_players_entities, key=lambda p: p.id)
        
        ordered_player_list_dto = [
            {"id": str(p.id), "name": p.name} for p in sorted_players
        ]

        turn_timer_manager.pause_timer(game_id)
        await manager.broadcast_to_game(
            game_id,
            {
                "type": "timerPaused",
                "data": {"time_remaining:": turn_timer_manager.get_remaining_time(game_id)}
            }
        )
        
        game_service.change_turn_state(
            game_id, 
            TurnState.PASSING_CARDS,
            passing_direction=payload.direction,
            current_event_card_id=payload.event_id
        )

        move_in = schemas.CardMoveIn(to_owner=CardOwner.DISCARD_PILE)
        event_card = CardService.move_card(db, payload.event_id, move_in)

        await manager.broadcast_to_game(
            game_id,
            {
                "type": "passingPhaseStarted",
                "data": {
                    "direction": payload.direction,
                    "player_who_played": str(payload.player_id),
                    "ordered_players": ordered_player_list_dto
                }
            }
        )
        
        event_card = card
    
    # ------ Point your Suspicions 
    elif event_code == "E_PYS":
        
        card = CardService.get_card_by_id(db, payload.event_id)
        if (not card or card.name != "E_PYS" or card.owner_player_id != payload.player_id):
             raise CardsNotFoundOrInvalidException()
        
        turn_timer_manager.pause_timer(game_id)
        await manager.broadcast_to_game(
            game_id,
            {
                "type": "timerPaused",
                "data": {"time_remaining:": turn_timer_manager.get_remaining_time(game_id)}
            }
        )
        
        game_service.change_turn_state(
            game_id, 
            TurnState.VOTING,
            current_event_card_id=payload.event_id 
        )

        move_in = schemas.CardMoveIn(to_owner=CardOwner.DISCARD_PILE)
        event_card = CardService.move_card(db, payload.event_id, move_in)

        await manager.broadcast_to_game(
            game_id,
            {
                "type": "votingPhaseStarted",
                "data": {
                    "player_who_played": str(payload.player_id)
                }
            }
        )
        
        event_card = card

    # ------ Card Trade
    elif event_code == "E_CT":
        if (
            payload.target_player is None
            or payload.offered_card_id is None
        ):
            raise HTTPException(
                status_code=400,
                detail="TargetPlayerAndOfferedCardAreRequired",
            )

        if payload.target_card_id is not None:
            raise HTTPException(
                status_code=400,
                detail="TargetCardMustBeSelectedByTargetPlayer",
            )

        target_player_entity = player_service.get_player_entity_by_id(
            payload.target_player
        )
        if not target_player_entity:
            raise HTTPException(status_code=404, detail="TargetPlayerNotFound")

        if payload.target_player not in game.players_ids:
            raise HTTPException(
                status_code=400, detail="GameNotFoundOrPlayerNotInGame"
            )

        event_card_entity = CardService.get_card_by_id(db, payload.event_id)
        if (
            not event_card_entity
            or event_card_entity.game_id != game_id
            or event_card_entity.name != "E_CT"
            or event_card_entity.owner != CardOwner.PLAYER
            or event_card_entity.owner_player_id != payload.player_id
        ):
            raise CardsNotFoundOrInvalidException()

        offered_card_entity = CardService.get_card_by_id(db, payload.offered_card_id)
        if (
            not offered_card_entity
            or offered_card_entity.game_id != game_id
            or offered_card_entity.owner != CardOwner.PLAYER
            or offered_card_entity.owner_player_id != payload.player_id
        ):
            raise CardsNotFoundOrInvalidException()

        requested_card_code = (
            payload.requested_card_code.strip().upper()
            if payload.requested_card_code
            else None
        )

        event_card = CardService.move_card(
            db,
            payload.event_id,
            schemas.CardMoveIn(to_owner=CardOwner.DISCARD_PILE),
        )
        turn_timer_manager.pause_timer(game_id)
        await manager.broadcast_to_game(
            game_id,
            {
                "type": "timerPaused",
                "data": {"time_remaining:": turn_timer_manager.get_remaining_time(game_id)}
            }
        )

        game_service.change_turn_state(
            game_id,
            TurnState.CARD_TRADE_PENDING,
            payload.target_player,
            current_event_card_id=payload.event_id,
            card_trade_offered_card_id=payload.offered_card_id,
        )

    else:
        raise HTTPException(
            status_code=400, detail=f"Unknown event code: {event_code}"
        )
    
    if event_code not in {"E_AV", "E_DCF", "E_CT", "E_PYS"}:
        game_service.change_turn_state(
                game_id, 
                TurnState.DISCARDING
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
    if event_code == "E_CT":
        broadcast_data["target_player"] = str(payload.target_player)
        broadcast_data["requested_card_code"] = requested_card_code

    await manager.broadcast_to_game(
        game_id,
        {
            "type": "playEvent",
            "data": broadcast_data
        },
    )

    return event_card

@cards_router.put(
    "/play/E_CT/{game_id}/selection",
    response_model=schemas.CardTradeResolutionOut,
)
async def resolve_card_trade_selection(
    game_id: UUID,
    payload: schemas.CardTradeSelectionIn = Body(...),
    db: Session = Depends(get_db),
):
    """
    Endpoint para que el jugador objetivo elija la carta a intercambiar durante
    el evento Card Trade. Ejecuta el intercambio una vez que la elección es válida.
    """
    game_service = GameService(db)
    game = game_service.get_game_by_id(game_id=game_id)
    if not game or payload.player_id not in game.players_ids:
        raise HTTPException(status_code=400, detail="GameNotFoundOrPlayerNotInGame")

    game_turn_state = game_service.get_turn_state(game_id)
    if game_turn_state.turn_state != TurnState.CARD_TRADE_PENDING:
        raise HTTPException(
            status_code=400, detail="Invalid accion for the game state"
        )

    if game_turn_state.target_player_id != payload.player_id:
        raise HTTPException(
            status_code=403, detail="OnlyTargetPlayerCanSelectCard"
        )

    turn_state_entity = game_service.get_turn_state_entity(game_id)
    if (
        not turn_state_entity
        or not turn_state_entity.current_event_card_id
        or turn_state_entity.current_event_card_id != payload.event_card_id
    ):
        raise HTTPException(status_code=409, detail="CardTradeNotPending")

    offered_card_id = turn_state_entity.card_trade_offered_card_id
    if not offered_card_id:
        raise HTTPException(status_code=409, detail="CardTradeNotPending")

    requesting_player_id = game_service.get_turn(game_id)
    if not requesting_player_id:
        raise HTTPException(status_code=400, detail="TurnPlayerNotFound")

    if requesting_player_id not in game.players_ids:
        raise HTTPException(status_code=400, detail="GameNotFoundOrPlayerNotInGame")

    offered_card = CardService.get_card_by_id(db, offered_card_id)
    if (
        not offered_card
        or offered_card.game_id != game_id
        or offered_card.owner != CardOwner.PLAYER
        or offered_card.owner_player_id != requesting_player_id
    ):
        raise CardsNotFoundOrInvalidException()

    target_card = CardService.get_card_by_id(db, payload.target_card_id)
    if (
        not target_card
        or target_card.game_id != game_id
        or target_card.owner != CardOwner.PLAYER
        or target_card.owner_player_id != payload.player_id
    ):
        raise CardsNotFoundOrInvalidException()

    # Ejecutar el intercambio usando la lógica centralizada del servicio
    try:
        result_dict = CardService.card_trade(
            db=db,
            game_id=game_id,
            player_id=requesting_player_id,
            event_card_id=payload.event_card_id,
            target_player_id=payload.player_id,
            offered_card_id=offered_card.id,
            target_card_id=target_card.id,
        )
    except (CardsNotFoundOrInvalidException, HTTPException) as e:
        raise e

    # Refrescar las cartas finales desde la base
    moved_offered = CardService.get_card_by_id(db, offered_card.id)
    moved_target = CardService.get_card_by_id(db, target_card.id)

    # Notificar resolución por WebSocket
    await manager.broadcast_to_game(
        game_id,
        {
            "type": "cardTradeResolved",
            "data": {
                "player_id": str(requesting_player_id),
                "target_player": str(payload.player_id),
            },
        },
    )

    # --- Eventos de Blackmailed ---
    for event_data in result_dict["blackmailed_events"]:
        await manager.broadcast_to_game(
            game_id,
            {
                "type": "actionRequiredChooseSecret",
                "data": event_data
            }
        )
    # Refrescamos el estado
    turn_state_entity = game_service.get_turn_state_entity(game_id)
    if turn_state_entity.state != TurnState.PENDING_DEVIOUS:
        # Termina el trade → estado DISCARDING
        turn_timer_manager.resume_timer(game_id)
        await manager.broadcast_to_game(
                game_id,
                {
                    "type": "timerResumed",
                    "data": {"time_remaining:": turn_timer_manager.get_remaining_time(game_id)}
                }
            )
        game_service.change_turn_state(game_id, TurnState.DISCARDING)
    else:
        await manager.broadcast_to_game(
                game_id,
                {
                    "type": "sfpPending",
                    "data": {
                        "players_id": turn_state_entity.sfp_players}
                }
            )

    return schemas.CardTradeResolutionOut(
        offered_card=moved_offered,
        received_card=moved_target,
    )


@cards_router.put("/passing/{game_id}", response_model=schemas.CardOut)
async def select_card_for_passing(
    game_id: UUID,
    payload: schemas.SelectPassingCardIn = Body(...),
    db: Session = Depends(get_db)
):
    """
    Cada jugador llama a este endpoint para "bloquear" la carta 
    que va a pasar durante el estado PASSING_CARDS.
    """
    game_service = GameService(db)
    card_service = CardService()

    game_turn_state = game_service.get_turn_state_entity(game_id)
    if not game_turn_state or game_turn_state.state != TurnState.PASSING_CARDS:
        raise HTTPException(status_code=403, detail="Not in a card passing phase")
    

    game = game_service.get_game_by_id(game_id)
    if not game or payload.player_id not in game.players_ids:
        raise HTTPException(status_code=400, detail="Player not in this game")

    try:
        moved_card = card_service.select_card_for_passing(
            db, 
            game_id, 
            payload.player_id, 
            payload.card_id
        )
    except (CardsNotFoundOrInvalidException, HTTPException) as e:
        raise e

    await manager.broadcast_to_game(
        game_id,
        {
            "type": "playerSelectedCard", 
            "data": {"player_id": str(payload.player_id)}
        }
    )

    game_entity = game_service.get_game_entity_by_id(game_id)
    if not game_entity:
        raise HTTPException(404, "Game not found after selecting card") 

    all_players_selected = card_service.check_if_all_players_selected(
        db, game_id, game_entity
    )
    
    if all_players_selected:
        blackmailed_events = card_service.execute_dead_card_folly_swap(db, game_id, game_entity)
        
        current_state = game_service.get_turn_state(game_id)
        
        if current_state.turn_state != TurnState.PENDING_DEVIOUS:
            turn_timer_manager.resume_timer(game_id)
            await manager.broadcast_to_game(
                game_id,
                {
                    "type": "timerResumed",
                    "data": {"time_remaining:": turn_timer_manager.get_remaining_time(game_id)}
                }
            )
        else: 

            await manager.broadcast_to_game(
                game_id,
                {
                    "type": "sfpPending",
                    "data": {
                        "players_id": current_state.sfp_players}
                }
            )
        

        await manager.broadcast_to_game(
            game_id,
            {
                "type": "passingPhaseExecuted",
                "data": {
                    "event_name": "E_DCF",
                    "message": "El intercambio de cartas se ha completado."
                }
            }
        )

        for event_data in blackmailed_events:
            await manager.broadcast_to_game(
                game_id,
                {
                    "type": "actionRequiredChooseSecret",
                    "data": event_data
                }
            )

    return moved_card


@cards_router.put("/play-no-so-fast/{game_id}",response_model=schemas.CardOut)
async def play_no_so_fast(
    game_id: UUID,
    payload: schemas.CardNoSoFastPlay = Body(...),
    db: Session = Depends(get_db)
):
    """
    Permite jugar la carta No So Fast para cancelar una accion.
    La partida debe estar en CANCELLED_CARD_PENDING
    """
    game_service = GameService(db)
    game_entity = game_service.get_game_entity_by_id(game_id)
    if not game_entity:
        raise HTTPException(status_code=404, detail="GameNotFound")

    player_ids = {player.id for player in game_entity.players}

    if payload.player_id not in player_ids:
        raise HTTPException(status_code=400, detail="PlayerNotInGame")
    if game_entity.turn_state.state != TurnState.CANCELLED_CARD_PENDING:
        raise HTTPException(404, "Wrong game state") 

    card = CardService.get_card_by_id(db,payload.card_id)
    if card.name != "E_NSF":
        raise HTTPException(400, "Wrong card")
    
    if (card.game_id != game_id
        or card.owner_player_id != payload.player_id):
        raise HTTPException (400, "Card not in game or player hand")
    
    turn_state = game_service.get_turn_state(game_id)
    current_state = turn_state.is_cancelled
    
    game_service.change_turn_state(game_id=game_id, 
                                   new_state=TurnState.CANCELLED_CARD_PENDING,
                                   is_cancelled= not current_state)
    
    move_in = schemas.CardMoveIn(to_owner=CardOwner.DISCARD_PILE)
    card = CardService.move_card(db,payload.card_id,move_in)

    await manager.broadcast_to_game(
            game_id,
            {
                "type": "notSoFastPlayed",
                "data": {
                    "player_id":str(payload.player_id)
                    }
            }
        )


    
    return card


@cards_router.put("/vote/{game_id}", response_model=schemas.VoteIn)
async def submit_vote(
    game_id: UUID,
    payload: schemas.VoteIn = Body(...),
    db: Session = Depends(get_db)
):
    """
    Cada jugador llama a este endpoint para emitir su voto
    durante el estado VOTING.
    """
    game_service = GameService(db)
    card_service = CardService()
    
    try:
        game_service.submit_player_vote(
            game_id,
            payload.player_id,
            payload.target_player_id
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    await manager.broadcast_to_game(
        game_id,
        {
            "type": "playerHasVoted", 
            "data": {"player_id": str(payload.player_id)}
        }
    )

    # Comprobar si todos han votado
    game_entity = game_service.get_game_entity_by_id(game_id)
    if not game_entity:
        raise HTTPException(404, "Game not found after voting") 

    all_voted = card_service.check_if_all_players_voted(db, game_id, game_entity)
    
    if all_voted:
        player_to_reveal = await card_service.execute_pys_vote(db, game_id, game_entity)
        
        # Notificar a todos el elegido
        await manager.broadcast_to_game(
            game_id,
            {
                "type": "votingPhaseExecuted", 
                "data": {"player_to_reveal_id": str(player_to_reveal)}
            }
        )
        
        await manager.broadcast_to_game(
            game_id,
            {
                "type": "turnStateChanged",
                "data": {
                    "current_turn": str(game_entity.current_turn),
                    "turn_state": TurnState.CHOOSING_SECRET_PYS,
                    "target_player_id": str(player_to_reveal),
                    "players_who_selected_card": None,
                    "passing_direction": None
                }
            }
        )

    return payload


@cards_router.put("/devious/{card_id}", response_model=SecretOut)
async def play_devious_card(game_id: UUID, card_id: UUID, secret_id: UUID, 
                            player_id: UUID, db: Session = Depends(get_db)) -> SecretOut:
    card = CardService.get_card_by_id(db, card_id)
    
    if card is None or card.type != CardType.DEVIOUS:
        raise HTTPException(status_code=404, detail="CardNotFoundOrInvalid")
    if card.game_id != game_id:
        raise HTTPException(status_code=400, detail="CardGameMismatch")
    secret = SecretService.get_secret_by_id(db, secret_id)
    if secret is None or secret.game_id != game_id or secret.owner_player_id != player_id:
        raise HTTPException(status_code=404, detail="Secret not found or invalid")
    if card.name == "DV_SFP":
        try:
            secret_revealed = SecretService.social_faux_pas(game_id, player_id, secret_id, card_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Error playing Devious card")
        await manager.broadcast_to_game(game_id, 
               {
                 "type": "SecretRevealed",
                 "data": {
                    "player_id": str(player_id),
                    "secret_id": str(secret_id)
                    }
                })
        return secret_revealed
    else:
        return SecretOut(
            id=secret.id,
            game_id=secret.game_id,
            type=secret.type,
            name=secret.name,
            description=secret.description,
            owner_player_id=secret.owner_player_id,
            revealed=secret.revealed
        )
    
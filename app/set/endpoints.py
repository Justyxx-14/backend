from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from uuid import UUID
from app.db import get_db
from app.player.service import PlayerService
from app.set import schemas
from app.set.service import SetService
from app.set.enums import SetType
from app.websocket.connection_man import manager
from app.game.schemas import GameEndReason
from app.game.service import GameService
from app.card.service import CardService
from app.game.enums import TurnState
from app.card.service import CardService
from app.card.schemas import CardOwner
from .dtos import SetOut
from .exceptions import (
    SetGameMismatch,
    SetNotFound,
    SetOwnerMismatch,
    SetPlayerRequired,
)
from app.game.turn_timer import turn_timer_manager

sets_router = APIRouter(prefix="/sets", tags=["sets"])


def _sets_query_msg(
    game_id: UUID,
    player_id: UUID,
    items: List[SetOut],
    *,
    requested_set_id: UUID | None,
) -> dict:
    data = {
        "player_id": str(player_id),
        "set_ids": [str(item.id) for item in items],
        "count": len(items),
    }
    if requested_set_id:
        data["requested_set_id"] = str(requested_set_id)
    return {"type": "sets/query", "game_id": str(game_id), "data": data}


@sets_router.get("", response_model=List[SetOut])
async def query_sets(
    game_id: UUID = Query(..., description="ID de la partida"),
    player_id: UUID | None = Query(
        None, description="Filtra por dueño del set dentro de la partida"
    ),
    set_id: UUID | None = Query(
        None, description="ID de un set específico del jugador"
    ),
    db: Session = Depends(get_db),
) -> List[SetOut]:
    """Recupera los sets de un jugador en una partida y notifica al websocket.

    Uso:
    - Requiere `game_id` y `player_id` como parámetros de consulta.
    - Si se provee `set_id`, valida que pertenezca al jugador dentro de la partida y devuelve solo ese registro.
    - Si no se provee `set_id`, devuelve todos los sets del jugador en la partida.
    """
    if set_id is not None:
        if player_id is None:
            raise SetPlayerRequired()
        set_dto = SetService.get_set_by_id(db, set_id)
        if not set_dto:
            raise SetNotFound(str(set_id))
        if set_dto.game_id != game_id:
            raise SetGameMismatch(str(set_id), str(game_id))
        if set_dto.owner_player_id != player_id:
            raise SetOwnerMismatch(str(set_id), str(player_id))
        items = [set_dto]
        await manager.broadcast_to_game(
            game_id,
            _sets_query_msg(
                game_id, player_id, items, requested_set_id=set_id
            ),
        )
        return items

    if player_id is None:
        raise SetPlayerRequired()

    items = SetService.get_sets_for_player_in_game(
        db, player_id=player_id, game_id=game_id
    )
    await manager.broadcast_to_game(
        game_id,
        _sets_query_msg(
            game_id, player_id, items, requested_set_id=None
        ),
    )
    return items


@sets_router.get("/verify", status_code=status.HTTP_200_OK)
async def verify_set(
    cards: List[UUID] = Query(...), 
    db: Session = Depends(get_db)
) -> SetType:
    set_service = SetService(db)
    try:
        set_verify = set_service.validate_set(cards)
    except ValueError:
        raise HTTPException(status_code=400, detail="notValidSet")
    return set_verify

@sets_router.post(
    "/play/{game_id}", status_code=status.HTTP_201_CREATED
) 
async def play_set_detective(
    game_id: UUID,
    set_data: schemas.SetPlayIn,
    db: Session = Depends(get_db)
) -> schemas.SetOut | None:
    
    game_service = GameService(db)
    game = game_service.get_game_by_id(game_id)
    if (not game
        or set_data.player_id not in game.players_ids
        or set_data.player_id != game_service.get_turn(game_id)):
        raise HTTPException(status_code=400, detail= "BadRequest")
    game_turn_state = game_service.get_turn_state(game_id) 
    if game_turn_state.turn_state != TurnState.IDLE:
        raise HTTPException(status_code=400, detail = "Invalid accion for the game state")
    player_service = PlayerService(db)
    player_obj = player_service.get_player_entity_by_id(set_data.player_id)
    if player_obj.social_disgrace:
        raise HTTPException(status_code=403, detail="No se puede jugar un Set estando en desgracia social")
    player_target = player_service.get_player_entity_by_id(set_data.target_player_id)
    if player_target.social_disgrace:
        raise HTTPException(status_code=403, detail="No se puede jugar un Set sobre un jugador en desgracia social")
    try:
        set_service = SetService(db)
        set_type = set_service.determine_set_type(set_data.cards)
        new_set = set_service.create_set(game_id,set_data.player_id,set_type,set_data.cards)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    if SetService(db).verify_cancellable_new_set(db, set_data.cards):
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

        await manager.broadcast_to_game(
            game_id,
            {
                "type": "waitingForCancellationSet",
                "data": {
                    "player_id": str(set_data.player_id),
                    "set_type": new_set.type,
                },
            },
        )

        await CardService.wait_for_cancellation(db, game_id, timeout=7)

        await manager.broadcast_to_game(
            game_id,
            {
                "type": "waitFinished",
                "data": {
                    "player_id": str(set_data.player_id),
                    "set_type": new_set.type,
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

            return schemas.SetOut(
                id=new_set.id,
                type=new_set.type,
                owner_player_id=new_set.owner_player_id,
            )

    if set_type not in {SetType.HP, SetType.MM, SetType.PP}:
        # avisar al front que el jugador objetivo debe elegir un secreto propio.
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
            set_data.target_player_id
        )
        turn_timer_manager.pause_timer(game_id)
        await manager.broadcast_to_game(
            game_id,
            {
                "type": "timerPaused",
                "data": {"time_remaining:": turn_timer_manager.get_remaining_time(game_id)}
            }
        )

        await manager.broadcast_to_game(
        game_id,
        {
            "type": "targetPlayerElection",
            "data": {
                "set_id": str(new_set.id),
                "set_type": set_type.value,
                "target_player": str(set_data.target_player_id)
          }
        }
        )
        return schemas.SetOut(id=new_set.id, type=new_set.type, owner_player_id=new_set.owner_player_id)

    if not set_data.secret_id:
        raise HTTPException(status_code=400, detail= "BadRequest")
    try:
        play_result = set_service.play_set(
            new_set.id,
            None, 
            set_data.target_player_id, 
            set_data.secret_id
        )
        game_service.change_turn_state(
            game_id, 
            TurnState.DISCARDING
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    if play_result.end_game_result:
        end_game_data = play_result.end_game_result.model_dump(mode='json')
        message = {
            "type": "gameEnd",
            "data": end_game_data
        }
        await manager.broadcast_to_game(game_id, message)
    
    else:
        await manager.broadcast_to_game(
            game_id,
            {
                "type": "playSet",
                "data": {
                    "set_type": set_type.value,
                    "player_id": str(set_data.player_id),
                    "target_player": str(set_data.target_player_id)
              }
            }
        )
    
    set_out = play_result.set_out
    
    return schemas.SetOut(id=set_out.id, type=set_out.type, owner_player_id=set_out.owner_player_id)

@sets_router.post(
    "/election_secret/{game_id}", status_code=status.HTTP_200_OK
) 
async def election_secret_set(
    game_id: UUID,
    set_data: schemas.SetElectionPlayer,
    db: Session = Depends(get_db),
    card_id: UUID | None = None #para que sea compatible con el flujo de detective ariadne
):
    
    game_service = GameService(db)
    game = game_service.get_game_by_id(game_id)
    if (not game
        or set_data.player_id not in game.players_ids):
        raise HTTPException(status_code=400, detail= "BadRequest")
    game_turn_state=game_service.get_turn_state(game_id)
    if game_turn_state.turn_state != TurnState.CHOOSING_SECRET:
        raise HTTPException(status_code=400, detail = "Invalid accion for the game state")
    
    if card_id != None:
        card_data = CardService()
        card = card_data.get_card_by_id(db, card_id)
        if card.name != "D_AO":
            raise HTTPException(status_code=400, detail="Card is not Detective Ariadne Oliver")
        #flujo detective ariadne
        set_service = SetService(db)
        try:
            play_result = set_service.play_set(set_data.set_id, card_id, set_data.player_id, set_data.secret_id)
            game_service.change_turn_state(game_id, TurnState.DISCARDING)
            turn_timer_manager.resume_timer(game_id)
            await manager.broadcast_to_game(
                game_id,
                { "type": "timerResumed",
                    "data": {"time_remaining:": turn_timer_manager.get_remaining_time(game_id)}
                }
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail = "Error playing Ariadne card: " + str(e))
        
    else:
        set_service = SetService(db)

        set_played = set_service.get_set_by_id(db,set_data.set_id)
        if not set_played:
            raise HTTPException(status_code=404, detail= "Set not found")

        try:
            play_result = set_service.play_set(
                set_data.set_id, 
                card_id,
                set_data.player_id, 
                set_data.secret_id
            )
            game_service.change_turn_state(
                game_id, 
                TurnState.DISCARDING
            )
            turn_timer_manager.resume_timer(game_id)
            await manager.broadcast_to_game(
                game_id,
                {
                    "type": "timerResumed",
                    "data": {"time_remaining:": turn_timer_manager.get_remaining_time(game_id)}
                }
            ) 

        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    set_out = play_result.set_out

    if play_result.end_game_result:
        end_game_data = play_result.end_game_result.model_dump(mode='json')
        message = {
            "type": "gameEnd",
            "data": end_game_data
        }
        await manager.broadcast_to_game(game_id, message)

    else:
        await manager.broadcast_to_game(
            game_id,
            {
                "type": "playSet",
                "data": {
                    "set_type": set_out.type.value,
                    "player_id": str(set_out.owner_player_id),
                    "target_player": str(set_data.player_id)
                }
            }
        )

    return schemas.SetOut(
        id=set_out.id, 
        type=set_out.type, 
        owner_player_id=set_out.owner_player_id
    )

@sets_router.put(
    "/{set_id}/cards/{card_id}", status_code=status.HTTP_200_OK)
async def add_card_to_set(game_id: UUID,
    player_id: UUID,                      
    set_id: UUID,
    card_id: UUID,
    target_player_id: UUID,
    secret_id: UUID | None = None,
    db: Session = Depends(get_db)
    ) -> schemas.SetOut: 
    set_service = SetService(db)
    set_dto = set_service.get_set_by_id(db,set_id)
    player_data = PlayerService(db).get_player_entity_by_id(player_id)
    if not set_dto:
        raise HTTPException(status_code=404, detail= "Set not found")
    if set_dto.game_id != game_id:
        raise HTTPException(status_code=400, detail= "Set-Game mismatch")
    if set_dto.owner_player_id != player_id:
        raise HTTPException(status_code=400, detail= "Set-Player mismatch")
    if player_data.social_disgrace:
        raise HTTPException(status_code=403, detail="No se puede agregar una carta a un Set estando en desgracia social")
    game_service = GameService(db)
    game = game_service.get_game_by_id(game_id)
    if (not game 
        or player_id != game_service.get_turn(game_id)):
        raise HTTPException(status_code=400, detail= "BadRequest")
    game_turn_state = game_service.get_turn_state(game_id)
    if game_turn_state.turn_state != TurnState.IDLE:
        raise HTTPException(status_code=400, detail = "Invalid accion for the game state")
    #agragar carta al set
    try:
        updated_set = set_service.add_card_to_set(game_id, player_id, 
                                                     set_id, card_id) 
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    if SetService(db).verify_cancellable_set(db, set_id):
        turn_timer_manager.pause_timer(game_id)
        await manager.broadcast_to_game(
            game_id,
            {
                "type": "timerPaused",
                # "data": {"time_remaining:": turn_timer_manager.get_remaining_time(game_id)}
            }
        )
        game_service.change_turn_state(
            game_id,
            TurnState.CANCELLED_CARD_PENDING,
            is_cancelled=False
        )

        await manager.broadcast_to_game(
            game_id,
            {
                "type": "waitingForCancellationSet",
                "data": {
                    "player_id": str(player_id),
                    "set_type": updated_set.type,
                },
            },
        )

        await CardService.wait_for_cancellation(db, game_id, timeout=7)

        await manager.broadcast_to_game(
            game_id,
            {
                "type": "waitFinished",
                "data": {
                    "player_id": str(player_id),
                    "set_type": updated_set.type,
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

            return schemas.SetOut(
                id=updated_set.id,
                type=updated_set.type,
                owner_player_id=updated_set.owner_player_id,
            )
    
    #check what set type and play corresponding set action
    if set_dto.type not in {SetType.HP, SetType.MM, SetType.PP}:
        # avisar al front que el jugador objetivo debe elegir un secreto propio.
        game_service = GameService(db)
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
            target_player_id
         )
        await manager.broadcast_to_game(
        game_id,
        {
            "type": "targetPlayerElection",
            "data": {
                "set_id": str(set_id),
                "set_type": set_dto.type.value,
                "target_player": str(target_player_id)
          }
        }
        )
        return schemas.SetOut(id=updated_set.id, type=updated_set.type, 
                              owner_player_id=updated_set.owner_player_id)
    
    if not secret_id:
        raise HTTPException(status_code=400, detail= "BadRequest")
    #jugar set (hp,mm,pp)
    try:
        play_result = set_service.play_set(
            set_id,
            None, 
            target_player_id, 
            secret_id
        )
        game_service = GameService(db)
        game_service.change_turn_state(
            game_id, 
            TurnState.DISCARDING
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    if play_result.end_game_result:
        end_game_data = play_result.end_game_result.model_dump(mode='json')
        message = {
            "type": "gameEnd",
            "data": end_game_data
        }
        await manager.broadcast_to_game(game_id, message)
    else:
        await manager.broadcast_to_game(
            game_id,
            {
                "type": "playSet",
                "data": {
                    "set_type": set_dto.type.value,
                    "player_id": str(player_id),
                    "target_player": str(target_player_id)
              }
            }
        )

    return schemas.SetOut(
        id=updated_set.id, 
        type=updated_set.type, 
        owner_player_id=updated_set.owner_player_id
    )

@sets_router.put("/ariadne/{set_id}", status_code=status.HTTP_200_OK)
async def play_detective_ariadne(game_id: UUID, player_id: UUID, set_id: UUID, card_id: UUID,
                                 db: Session = Depends(get_db)) -> schemas.SetOut:
    player = PlayerService(db).get_player_by_id(player_id)
    if not player or player.game_id != game_id:
        raise HTTPException(status_code=404, detail="Player not found")
    set_service = SetService(db)
    set_data = set_service.get_set_by_id(db,set_id)
    if not set_data or set_data.game_id != game_id:
        raise HTTPException(status_code=404, detail="Set not found")
    card = CardService()
    card_data = card.get_card_by_id(db,card_id)
    if not card_data or card_data.owner_player_id != player_id:
        raise HTTPException(status_code=404, detail="Card not found")
    if card_data.name != "D_AO":
        raise HTTPException(status_code=400, detail="Card is not Detective Ariadne Oliver")
    target_player = PlayerService(db).get_player_entity_by_id(set_data.owner_player_id)
    if target_player.social_disgrace:
        raise HTTPException(status_code=403, detail="No se puede jugar sobre un jugador en desgracia social")
    if player.social_disgrace:
        raise HTTPException(status_code=403, detail="No se puede jugar Detective Ariadne estando en desgracia social")
    try:
        add_ariadne = set_service.add_card_to_set(game_id, set_data.owner_player_id, set_id, card_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    game = GameService(db)
    game.change_turn_state(
        game_id, 
        TurnState.CHOOSING_SECRET,
        set_data.owner_player_id
    )
    await manager.broadcast_to_game(
        game_id,
        {
            "type": "targetPlayerElection",
            "data": {
                "set_id": str(add_ariadne.id),
                "set_type": add_ariadne.type,
                "target_player": str(set_data.owner_player_id)
            }
        }
    )     

    return schemas.SetOut(
        id=add_ariadne.id, 
        type=add_ariadne.type, 
        owner_player_id=add_ariadne.owner_player_id
    )
     
    
    
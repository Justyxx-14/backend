from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from uuid import UUID
from app.db import get_db
from app.set import schemas
from app.set.service import SetService
from app.set.enums import SetType
from app.websocket.connection_man import manager
from app.game.schemas import GameEndReason
from app.game.service import GameService    
from .dtos import SetOut
from .exceptions import (
    SetGameMismatch,
    SetNotFound,
    SetOwnerMismatch,
    SetPlayerRequired,
)

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
    try:
        set_service = SetService(db)
        set_type = set_service.determine_set_type(set_data.cards)
        new_set = set_service.create_set(game_id,set_data.player_id,set_type,set_data.cards)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if set_type not in {SetType.HP, SetType.MM, SetType.PP}:
        # avisar al front que el jugador objetivo debe elegir un secreto propio.
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
            set_data.target_player_id, 
            set_data.secret_id
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
    db: Session = Depends(get_db)
):
    
    game_service = GameService(db)
    game = game_service.get_game_by_id(game_id)
    if (not game
        or set_data.player_id not in game.players_ids):
        raise HTTPException(status_code=400, detail= "BadRequest")
    
    set_service = SetService(db)

    set_played = set_service.get_set_by_id(db,set_data.set_id)
    if not set_played:
        raise HTTPException(status_code=404, detail= "Set not found")

    try:
        play_result = set_service.play_set(
            set_data.set_id, 
            set_data.player_id, 
            set_data.secret_id
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

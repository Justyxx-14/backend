from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Query, HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.db import get_db
from app.game.service import GameService
from app.player.service import PlayerService
from app.card.service import CardService
from app.game.enums import TurnState, GameEndReason
from app.websocket.connection_man import manager
from . import schemas
from .service import SecretService
from .exceptions import (
            SecretNotFound,
            SecretGameMismatch,
            SecretOwnerMismatch,
            SecretAndPlayerRequired
            )
from .enums import SecretType
from app.game.turn_timer import turn_timer_manager

# ----------------- helper -----------------

def _to_out(dto) -> schemas.SecretOut:
    return schemas.SecretOut(
        id=dto.id,
        game_id=dto.game_id,
        type=dto.role,
        name=dto.name,
        description=dto.description,
        owner_player_id=getattr(dto, "owner_player_id", None),
        revealed=dto.revealed,
    )# =========================
# /secrets
# =========================

secret_router = APIRouter(prefix="/secrets", tags=["secrets"])


# ---------------------------
# GET /secrets
# ---------------------------
@secret_router.get("", response_model=List[schemas.SecretOut])
def query_secrets(
    game_id: UUID = Query(..., description="ID de la partida"),
    player_id: UUID | None = Query(None, description="Filtra por dueño del secreto"),
    secret_id: UUID | None = Query(None, description="ID de un secreto específico"),
    db: Session = Depends(get_db)
    ):
    """
    Permite obtener los secretos de un juego vía query params.
    Reglas:
    - Si solo se pasa player_id sin secret_id => devuelve todos los secretos del jugador.
    - Si además se pasa secret_id con el player_id => devuelve el secreto específico.
    """
    # Evitamos listar todo el juego
    if not secret_id and not player_id:
        raise SecretAndPlayerRequired()

    if secret_id:
        if player_id is None:
            raise SecretAndPlayerRequired()
        dto = SecretService.get_secret_by_id(db, secret_id)
        if not dto:
            raise SecretNotFound(str(secret_id))
        if dto.game_id != game_id:
            raise SecretGameMismatch(str(secret_id), str(game_id))
        if dto.owner_player_id != player_id:
            raise SecretOwnerMismatch(str(secret_id), str(player_id))
        return [dto]


    items = SecretService.get_secrets_by_player_id(db, player_id)
    items = [x for x in items if x.game_id == game_id]
    return items


@secret_router.get("/social_disgrace", response_model=dict[UUID, bool])
def get_social_disgrace(
    game_id: UUID = Query(..., description="ID de la partida"),
    db: Session = Depends(get_db),
):
    """
    Devuelve dict con player_id: social_disgrace
    """
    game_service = GameService(db)
    game = game_service.get_game_by_id(game_id=game_id)
    if not game:
        raise HTTPException(status_code=404, detail="GameNotFound")

    player_service = PlayerService(db)
    players = player_service.get_players_by_game_id(game_id)

    if not players:
        raise HTTPException(status_code=400, detail="GameDontHavePlayers")

    return {player.id: player.social_disgrace for player in players}

@secret_router.put(
    "/reveal_for_pys/{game_id}", 
    response_model=schemas.SecretOut, # Devuelve el secreto revelado
    status_code=status.HTTP_200_OK
) 
async def reveal_secret_for_pys(
    game_id: UUID,
    payload: schemas.RevealSecretIn,
    db: Session = Depends(get_db)
):
    """
    Endpoint para que el jugador elegido por 'Point your Suspicions'
    revele uno de sus secretos.
    """
    
    game_service = GameService(db)
    
    game_turn_state = game_service.get_turn_state(game_id)
    if game_turn_state.turn_state != TurnState.CHOOSING_SECRET_PYS:
        raise HTTPException(status_code=400, detail = "Invalid action for the game state")
    
    # Validar que el jugador que llama es el jugador objetivo
    if game_turn_state.target_player_id != payload.player_id:
        raise HTTPException(status_code=403, detail="It's not your turn to reveal a secret")
    
    try:
        revealed_secret = SecretService.change_secret_status(db, payload.secret_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    if revealed_secret.role == SecretType.MURDERER:
        end_game_result = game_service.end_game(
            game_id,
            GameEndReason.MURDERER_REVEALED
        )
        end_game_data = end_game_result.model_dump(mode='json')
        
        await manager.broadcast_to_game(
            game_id,
            {"type": "gameEnd", "data": end_game_data}
        )
        
    else:
        turn_timer_manager.resume_timer(game_id)
        await manager.broadcast_to_game(
            game_id,
            {
                "type": "timerResumed",
                "data": {"time_remaining:": turn_timer_manager.get_remaining_time(game_id)}
            }
        )
        game_service.change_turn_state(
            game_id, 
            TurnState.DISCARDING
        )
        
        await manager.broadcast_to_game(
            game_id,
            {
                "type": "secretRevealed",
                "data": {
                    "player_id": str(payload.player_id),
                    "secret": jsonable_encoder(revealed_secret),
                    "reason": "Point your Suspicions"
                }
            }
        )
    
    return revealed_secret

@secret_router.get(
    "/{secret_id}/view", 
    response_model=schemas.SecretOut
)
def view_secret_details(
    secret_id: UUID,
    db: Session = Depends(get_db)
):
    """
    Endpoint para obtener los detalles de un secreto.
    Usado por el evento 'Blackmailed'.
    """
    secret = SecretService.get_secret_by_id(db, secret_id)
    
    if not secret:
        raise HTTPException(status_code=404, detail="Secreto no encontrado")
    
    if secret.revealed:
       raise HTTPException(status_code=403, detail="Este secreto ya es público")

    return secret

@secret_router.put(
    "/reveal_for_sfp/{game_id}", 
    response_model=schemas.SecretOut, # Devuelve el secreto revelado
    status_code=status.HTTP_200_OK
) 
async def reveal_secret_for_pys(
    game_id: UUID,
    payload: schemas.RevealSecretIn,
    db: Session = Depends(get_db)
):
    """
    Endpoint para que el jugador elegido por 'Social Faux Pas'
    revele uno de sus secretos.
    """
    
    game_service = GameService(db)
    
    game_turn_state = game_service.get_turn_state(game_id)
    if game_turn_state.turn_state != TurnState.PENDING_DEVIOUS:
        raise HTTPException(status_code=400, detail = "Invalid action for the game state")
    
    game_obj = game_service.get_game_entity_by_id(game_id)
    print(game_obj.turn_state.sfp_players)
    print(payload.player_id)
    # Validar que el jugador que llama es el jugador objetivo
    if str(payload.player_id) not in game_obj.turn_state.sfp_players:
        raise HTTPException(400, "No Social Faux Pas pending for this player")
    
    try:
        revealed_secret = SecretService.change_secret_status(db, payload.secret_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    if revealed_secret.role == SecretType.MURDERER:
        end_game_result = game_service.end_game(
            game_id,
            GameEndReason.MURDERER_REVEALED
        )
        end_game_data = end_game_result.model_dump(mode='json')
        
        await manager.broadcast_to_game(
            game_id,
            {"type": "gameEnd", "data": end_game_data}
        )
        
    else:
        if CardService.check_players_SFP(db, game_id, payload.player_id):
            turn_timer_manager.resume_timer(game_id)
            await manager.broadcast_to_game(
                game_id,
                {
                    "type": "timerResumed",
                    "data": {"time_remaining:": turn_timer_manager.get_remaining_time(game_id)}
                }
            )
            
            game_service.change_turn_state(
                game_id, 
                TurnState.DISCARDING
            )
            
        await manager.broadcast_to_game(
            game_id,
            {
                "type": "secretRevealed",
                "data": {
                    "player_id": str(payload.player_id),
                    "secret": jsonable_encoder(revealed_secret),
                    "reason": "Social Faux Pas"
                }
            }
        )
    
    return revealed_secret
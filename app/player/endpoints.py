from fastapi import APIRouter, Depends, status, HTTPException
from uuid import UUID

from app.player.service import PlayerService
from app.player.schemas import PlayerOut, PlayerIn, PlayerResponse
from app.db import get_db


player_router = APIRouter(prefix="/players", tags=["players"])

@player_router.get("/")
def get_players(db=Depends(get_db)) -> list[PlayerOut]:
    """
    Retorna la lista de todos los jugadores.
    
    return
    List[PlayerOutl]
    """
    list_players = []
    for player in PlayerService(db).get_players():
        list_players.append(
            PlayerOut(
                id=player.id,
                name=player.name,
                birthday=player.birthday,
                game_id=player.game_id if hasattr(player, 'game_id') else None,
                social_disgrace=player.social_disgrace,
            )
        )
    return list_players

@player_router.get("/{player_id}")
def get_player_by_id(player_id: UUID, db=Depends(get_db)) -> PlayerOut:
    """
    Retorna un jugador por su ID.
    
    Parametros
    player_id: ID del jugador a buscar.

    return
    PlayerOut
    """
    player = PlayerService(db).get_player_by_id(player_id)
    if not player:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"Player {player_id} not found")
    return PlayerOut(id=player.id, 
                     name=player.name, 
                     birthday=player.birthday,
                     game_id=player.game_id if hasattr(player, 'game_id') else None,
                     social_disgrace=player.social_disgrace)

@player_router.post("/", status_code=status.HTTP_201_CREATED)
def create_player(player_data: PlayerIn, db=Depends(get_db)) -> PlayerResponse:
    """
    Crea un nuevo jugador.
    
    Parametros
    player_data : PlayerIn 
        Datos del jugador a crear.

    return
    PlayerResponse
        Identificador del jugador creado.
    
    raise 
    HTTPException
        400: Si los datos son inválidos.
    """
    try:
        create_player = PlayerService(db).create_player(player_data.to_dto())
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail=str(e)
            )
    return PlayerResponse(id=create_player.id)

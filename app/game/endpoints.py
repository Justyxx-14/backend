from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import List, Union
from uuid import UUID
from app.db import get_db
from app.game.service import GameService
from app.game.schemas import EndGameResult
from app.card.service import CardService
from app.player.dtos import PlayerInDTO
from app.websocket.connection_man import manager
from app.websocket.menu_man import menu_manager
from app.game.dtos import GameInDTO, GameOutDTO

# instancia de APIRouter
router = APIRouter(prefix="/games", tags=["games"])


@router.get("/", response_model=List[GameOutDTO])
def get_games_endpoint(
    full: bool = False,
    ready: bool = False,
    db: Session = Depends(get_db),
):
    """
    GET /games
    - full=true  -> incluye partidas llenas
    - ready=true -> incluye partidas iniciadas
    Por defecto (sin params) devuelve sólo 'disponibles'.
    """
    game_service = GameService(db)
    return game_service.get_games(full=full, ready=ready)


@router.get("/{game_id}", response_model=GameOutDTO)
def get_game_by_id_endpoint(game_id: UUID, db: Session = Depends(get_db)):
    game_service = GameService(db)
    game = game_service.get_game_by_id(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Juego no encontrado")
    return game


@router.post("/", response_model=GameOutDTO, status_code=201)
async def create_game_endpoint(
    game_data: GameInDTO, db: Session = Depends(get_db)
):
    game_service = GameService(db)
    new_game = game_service.create_game(game_data)

    await menu_manager.broadcast(
        {"type": "gameAdd", "data": jsonable_encoder(new_game)}
    )

    return new_game


@router.post("/{game_id}/players", status_code=status.HTTP_204_NO_CONTENT)
async def add_player_endpoint(
    game_id: UUID, player_data: PlayerInDTO, db: Session = Depends(get_db)
):
    game_service = GameService(db)
    player_id = game_service.add_player(game_id, player_data)
    if not player_id:
        raise HTTPException(status_code=400, detail="GameUnavailable")

    updated_game = game_service.get_game_by_id(game_id)
    if not updated_game:
        raise HTTPException(status_code=404, detail="GameNotFound")
    
    game_data_json = jsonable_encoder(updated_game)

    # Notificación A LA PARTIDA: informa a los jugadores dentro del juego
    await manager.broadcast_to_game(
        game_id, {"type": "playerJoined",
                  "data": {
                    "game_id": str(game_id),
                    "player_id": str(player_id),
                    "player_name": player_data.name,
                },
        }
    )

    if len(updated_game.players_ids) == updated_game.max_players:
        # Notificación AL LOBBY: informa que el juego está lleno
        await menu_manager.broadcast(
            {"type": "gameUnavailable", "data": game_data_json}
        )
    else:
        # Notificación AL LOBBY: solo necesita saber qué juego se actualizó
        await menu_manager.broadcast(
            {"type": "joinPlayerToGame", "data": game_data_json}
        )
    
    # Retorno vacío con status_code 204
    return JSONResponse(
        content={"game_id": str(game_id), "player_id": str(player_id)},
        status_code=status.HTTP_200_OK,
    )


@router.post(
    "/{game_id}/start", status_code=status.HTTP_204_NO_CONTENT
)
async def start_game_endpoint(game_id: UUID, db: Session = Depends(get_db)):
    game_service = GameService(db)
    if not game_service.can_start(game_id):
        raise HTTPException(status_code=400, detail="StartConditionsNotMet")

    game_service.start_game(game_id)
    updated_game = game_service.get_game_by_id(game_id)
    game_data_json = jsonable_encoder(updated_game)

    if not updated_game:
        raise HTTPException(status_code=404, detail="GameNotFound")
    # Notificación A LA PARTIDA
    await manager.broadcast_to_game(
        game_id, {"type": "GameStarted", "data": game_data_json}
    )

    # AÑADIDO: Notificación AL LOBBY para que se elimine o marque como "en curso"
    await menu_manager.broadcast(
        {
            "type": "gameUnavailable",
            "data": game_data_json,
        }
    )

    return


@router.get("/turn/{game_id}")
async def get_turn_endpoint(game_id: UUID, db: Session = Depends(get_db)):
    game_service = GameService(db)
    current_turn = game_service.get_turn(game_id)

    if not current_turn:
        raise HTTPException(status_code=404, detail="PlayerNotFound")

    return {"id": current_turn}


@router.post("/turn/{game_id}")
async def turn_change_endpoint(game_id: UUID, db: Session = Depends(get_db)):
    game_service = GameService(db)

    try:
        result: Union[UUID, EndGameResult] = game_service.next_player(game_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    if isinstance(result, EndGameResult):
        await manager.broadcast_to_game(
            game_id, {"type": "gameEnded", "data": jsonable_encoder(result)}
        )
        return {"detail": "Game has ended"}

    else:
        id_next_player = result
        await manager.broadcast_to_game(
            game_id, {"type" : "turnChange", "data": str(id_next_player)}
        )

    return {"id": id_next_player}

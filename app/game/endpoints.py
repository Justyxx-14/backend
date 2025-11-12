from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import List, Union
from uuid import UUID
from app.db import get_db
from app.game.service import GameService
from app.game.schemas import (
    EndGameResult,
    GameTurnStateOut,
    CurrentTurnResponse,
    PlayerNeighborInfo,
    NeighborsOut
)
from app.card.service import CardService
from app.player.dtos import PlayerInDTO
from app.websocket.connection_man import manager
from app.websocket.menu_man import menu_manager
from app.game.dtos import GameInDTO, GameOutDTO
from app.game.enums import TurnState
from app.game.turn_timer import turn_timer_manager

# instancia de APIRouter
router = APIRouter(prefix="/games", tags=["games"])

def remove_password(game_data: dict) -> dict:
    """Remueve el campo password del diccionario del juego y agrega has_password"""
    filtered_data = game_data.copy()
    had_password = 'password' in filtered_data and filtered_data['password'] is not None
    filtered_data.pop('password', None)
    filtered_data['hasPassword'] = had_password
    return filtered_data

@router.get("/", response_model=List[GameOutDTO])
def get_games_endpoint(
    ready: bool = False,
    full: bool = False,
    db: Session = Depends(get_db),
):
    """
    - full=true  -> incluye partidas llenas
    - ready=true -> incluye partidas iniciadas
    Por defecto (sin params) devuelve sólo 'disponibles'.
    GET /games
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
        {"type": "gameAdd", "data": remove_password(jsonable_encoder(new_game))}
    )

    return new_game


@router.post("/{game_id}/players", status_code=status.HTTP_200_OK)
async def add_player_endpoint(
    game_id: UUID, 
    player_data: PlayerInDTO, 
    password: str = None,  # Hacer el password opcional
    db: Session = Depends(get_db)
):
    game_service = GameService(db)
    
    game = game_service.get_game_by_id(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="GameNotFound")
    
    if game.password:
        if password is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Password required for this game"
            )
        if password != game.password:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Wrong password"
            )
    else:
        pass
    
    player_id = game_service.add_player(game_id, player_data)
    if not player_id:
        raise HTTPException(status_code=400, detail="GameUnavailable")

    updated_game = game_service.get_game_by_id(game_id)
    if not updated_game:
        raise HTTPException(status_code=404, detail="GameNotFound")

    game_data_json = remove_password(jsonable_encoder(updated_game))

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

    if not updated_game:
        raise HTTPException(status_code=404, detail="GameNotFound")
    # Notificación A LA PARTIDA

    game_data_json = remove_password(jsonable_encoder(updated_game))

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
    
    turn_timer_manager.start_timer(game_id, game_service.handler_end_timer)

    return


@router.get("/turn/{game_id}",response_model=CurrentTurnResponse)
async def get_turn_endpoint(game_id: UUID, db: Session = Depends(get_db)):
    game_service = GameService(db)
    game = game_service.get_game_by_id(game_id)
    if (not game.ready):
        raise HTTPException(status_code=409, detail="La partida aún no ha comenzado")
    current_turn = game_service.get_turn(game_id)
    if not current_turn:
        raise HTTPException(status_code=404, detail="PlayerNotFound")
    game_turn_state = game_service.get_turn_state(game_id)

    remaining_time = turn_timer_manager.get_remaining_time(game_id)
    timer_is_paused = turn_timer_manager.get_is_paused(game_id)
    if not remaining_time:
        raise HTTPException(status_code=404, detail="TimerNotFound")
    players_selected_list: List[UUID] | None = None
    
    if game_turn_state.turn_state == TurnState.PASSING_CARDS:
        players_selected_list = CardService.get_players_who_selected_card(db, game_id)

    players_voted_list: List[UUID] | None = None
    
    if game_turn_state.turn_state == TurnState.VOTING and game_turn_state.vote_data:
        players_voted_list = [UUID(voter_id) for voter_id in game_turn_state.vote_data.keys()]

    return CurrentTurnResponse(
        current_turn=current_turn,
        turn_state=game_turn_state.turn_state,
        remaining_time=remaining_time,
        timer_is_paused=timer_is_paused,
        target_player_id=game_turn_state.target_player_id,
        current_event_card_id=game_turn_state.current_event_card_id,
        card_trade_offered_card_id=game_turn_state.card_trade_offered_card_id,
        players_who_selected_card=players_selected_list,
        passing_direction=game_turn_state.passing_direction,
        players_who_voted=players_voted_list,
        sfp_players=game_turn_state.sfp_players
    )


@router.post("/turn/{game_id}")
async def turn_change_endpoint(game_id: UUID, db: Session = Depends(get_db)):
    game_service = GameService(db)
    game_turn_state = game_service.get_turn_state(game_id)
    if game_turn_state.turn_state != TurnState.END_TURN:
        raise HTTPException(status_code=400, detail="El turno no finalizo aun")

    try:
        result: Union[UUID, EndGameResult] = game_service.next_player(game_id)
        turn_timer_manager.stop_timer(game_id)
        turn_timer_manager.start_timer(game_id, game_service.handler_end_timer)
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
            game_id, {"type" : "turnChange", 
                      "data":{
                          "player_id": str(id_next_player)
                      } }
        )

    return {"id": id_next_player}

@router.get(
    "/{game_id}/neighbors/{player_id}",
    response_model= NeighborsOut,
    status_code=status.HTTP_200_OK,
    summary="Obtiene los vecinos de turno de un jugador"
)
def get_player_neighbors(
    game_id: UUID,
    player_id: UUID,
    db: Session = Depends(get_db)
):
    """
    Calcula y devuelve los vecinos (anterior y siguiente) de un jugador
    en el orden de turno (basado en UUID).
    """
    game_service = GameService(db)
    
    prev_player, next_player = game_service.get_player_neighbors(game_id, player_id)

    if not prev_player or not next_player:
        raise HTTPException(
            status_code=404, 
            detail="Juego/Jugador no encontrado o no hay suficientes jugadores."
        )
    
    response_data = NeighborsOut(
        previous_player=PlayerNeighborInfo(
            id=prev_player.id, 
            name=prev_player.name
        ),
        next_player=PlayerNeighborInfo(
            id=next_player.id, 
            name=next_player.name
        )
    )

    return response_data

    
@router.delete(
    "/{game_id}/players/{player_id}",
    status_code=status.HTTP_200_OK,
    summary="Jugador/Host abandona/cancela partida",
)
async def leave_game_endpoint(
    game_id: UUID,
    player_id: UUID,
    db: Session = Depends(get_db)
):
    game_service = GameService(db)
    
    # Obtener el juego y verificar si el jugador que abandona es el host
    game_before_removal = game_service.get_game_entity_by_id(game_id)
    if not game_before_removal:
        raise HTTPException(status_code=404, detail="GameNotFound")
    
    is_host = game_before_removal.host_id == player_id
    player_name = next(
        (p.name for p in game_before_removal.players if p.id == player_id),
        "Jugador Desconocido"
    )

    # Esto elimina al jugador o la partida completa si es el host
    success = game_service.remove_player(game_id, player_id)
    
    if not success:
        # Aquí también atrapamos el caso de que el juego ya esté 'ready' (iniciado)
        raise HTTPException(
            status_code=400, 
            detail="RemovalFailed: Player not in lobby or game in progress."
        )

    # Notificaciones por WebSocket
    if is_host:
        # Notificación A LA PARTIDA: El juego fue CANCELADO
        await manager.broadcast_to_game(
            game_id, 
            {
                "type": "GameCancelled", 
                "data": {"game_id": str(game_id), "reason": "HostLeft"}
            }
        )
        # Notificación AL LOBBY: El juego fue ELIMINADO
        await menu_manager.broadcast(
            {"type": "gameRemoved", "data": {"game_id": str(game_id)}}
        )
        
        return JSONResponse(
            content={"detail": "Game deleted successfully"},
            status_code=status.HTTP_200_OK,
        )
        
    else:
        # Cargar el juego actualizado (sin el jugador)
        updated_game = game_service.get_game_by_id(game_id)
        if not updated_game:
             # Debería existir si no lo eliminamos
             raise HTTPException(status_code=500, detail="Game disappeared unexpectedly.")

        game_data_json = jsonable_encoder(updated_game)

        # Notificación A LA PARTIDA: informa a los jugadores restantes
        await manager.broadcast_to_game(
            game_id,
            {"type": "playerLeft", 
             "data": {
                 "game_id": str(game_id), 
                 "player_id": str(player_id),
                 "player_name": player_name
                }
             }
        )

        # Notificación AL LOBBY: actualiza la cantidad de jugadores
        await menu_manager.broadcast(
            {"type": "removePlayerFromGame", "data": game_data_json}
        )
    
        return JSONResponse(
            content={"detail": "Player deleted successfully"},
            status_code=status.HTTP_200_OK)

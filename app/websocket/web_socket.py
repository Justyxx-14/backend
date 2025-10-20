from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session
from uuid import UUID
from .menu_man import menu_manager
from .connection_man import manager
from app.db import get_db


router = APIRouter(tags=["WebSockets"])


@router.websocket("/ws")
async def menu_websocket_endpoint(websocket: WebSocket, db: Session = Depends(get_db)):
    await menu_manager.connect(websocket)
    print("connection open")
    try:
        while True:
            data = await websocket.receive_json()
    except WebSocketDisconnect:
        menu_manager.disconnect(websocket)
        print("connection closed")


# partida
@router.websocket("/ws/{game_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: UUID):
    await manager.connect(websocket, game_id)
    try:
        while True:
            await websocket.receive_json()

    except WebSocketDisconnect:
        manager.disconnect(websocket, game_id)

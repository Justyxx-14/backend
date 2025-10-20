from fastapi import WebSocket
from typing import Dict, List
from uuid import UUID
import asyncio

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[UUID, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, game_id: UUID):
        await websocket.accept()
        if game_id not in self.active_connections:
            self.active_connections[game_id] = []
        self.active_connections[game_id].append(websocket)

    def disconnect(self, websocket: WebSocket, game_id: UUID):
        if game_id in self.active_connections:
            self.active_connections[game_id].remove(websocket)

    async def broadcast_to_game(self, game_id: UUID, message: dict):
        if game_id in self.active_connections:
            for connection in self.active_connections[game_id]:
                await connection.send_json(message)

manager = ConnectionManager()
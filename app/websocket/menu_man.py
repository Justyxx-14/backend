# lobby_manager.py
from fastapi import WebSocket
from typing import List
from fastapi.encoders import jsonable_encoder
import asyncio


class MenuManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """Acepta y registra la conexión."""
        await websocket.accept()
        self.active_connections.append(websocket)
        print(
            f"[LobbyManager] New connection: {websocket.client}. Total: {len(self.active_connections)}"
        )

    def disconnect(self, websocket: WebSocket):
        """Elimina la conexión de la lista si existe."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            print(
                f"[LobbyManager] Disconnected: {websocket.client}. Total: {len(self.active_connections)}"
            )

    async def broadcast(self, message: dict):
        """Envía mensaje JSON a todos los clientes conectados de forma segura."""
        message_json = jsonable_encoder(message)
        print(
            f"[LobbyManager] Broadcasting to {len(self.active_connections)} clients: {message_json}"
        )

        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message_json)
            except Exception as e:
                print(f"[LobbyManager] Failed to send to {connection.client}: {e}")
                disconnected.append(connection)

        for conn in disconnected:
            self.disconnect(conn)

    async def heartbeat(self, interval: int = 30):
        """Opcional: enviar ping periódico a todos los clientes."""
        while True:
            if self.active_connections:
                await self.broadcast({"type": "ping"})
            await asyncio.sleep(interval)


# instancia global
menu_manager = MenuManager()

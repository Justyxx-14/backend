import asyncio
import sys
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from app.db import get_db
from app.websocket.connection_man import ConnectionManager, manager as global_connection_manager
from app.websocket.menu_man import MenuManager, menu_manager as global_menu_manager
from app.websocket.web_socket import router


def make_mock_websocket():
    websocket = AsyncMock()
    websocket.accept = AsyncMock()
    websocket.send_json = AsyncMock()
    websocket.client = ("test", 123)
    return websocket


def test_connection_manager_connect_adds_connection():
    manager = ConnectionManager()
    websocket = make_mock_websocket()
    game_id = uuid4()

    asyncio.run(manager.connect(websocket, game_id))

    assert websocket.accept.await_count == 1
    assert manager.active_connections[game_id] == [websocket]


def test_connection_manager_connect_appends_existing_game():
    manager = ConnectionManager()
    websocket_one = make_mock_websocket()
    websocket_two = make_mock_websocket()
    game_id = uuid4()

    asyncio.run(manager.connect(websocket_one, game_id))
    asyncio.run(manager.connect(websocket_two, game_id))

    assert manager.active_connections[game_id] == [websocket_one, websocket_two]


def test_connection_manager_disconnect_removes_connection():
    manager = ConnectionManager()
    websocket = make_mock_websocket()
    game_id = uuid4()

    asyncio.run(manager.connect(websocket, game_id))
    manager.disconnect(websocket, game_id)

    assert manager.active_connections[game_id] == []


def test_connection_manager_disconnect_ignores_unknown_game():
    manager = ConnectionManager()
    websocket = make_mock_websocket()

    manager.disconnect(websocket, uuid4())
    assert manager.active_connections == {}


def test_connection_manager_broadcast_to_game_sends_json():
    manager = ConnectionManager()
    websocket_one = make_mock_websocket()
    websocket_two = make_mock_websocket()
    game_id = uuid4()

    asyncio.run(manager.connect(websocket_one, game_id))
    asyncio.run(manager.connect(websocket_two, game_id))
    message = {"event": "update"}

    asyncio.run(manager.broadcast_to_game(game_id, message))

    websocket_one.send_json.assert_awaited_once_with(message)
    websocket_two.send_json.assert_awaited_once_with(message)


def test_connection_manager_broadcast_ignores_missing_game():
    manager = ConnectionManager()

    asyncio.run(manager.broadcast_to_game(uuid4(), {"ignored": True}))

    assert manager.active_connections == {}


def test_menu_manager_connect_registers_websocket():
    menu_manager = MenuManager()
    websocket = make_mock_websocket()

    asyncio.run(menu_manager.connect(websocket))

    assert websocket.accept.await_count == 1
    assert menu_manager.active_connections == [websocket]


def test_menu_manager_disconnect_removes_connection():
    menu_manager = MenuManager()
    websocket = make_mock_websocket()

    asyncio.run(menu_manager.connect(websocket))
    menu_manager.disconnect(websocket)

    assert menu_manager.active_connections == []


def test_menu_manager_disconnect_ignores_missing_connection():
    menu_manager = MenuManager()
    websocket = make_mock_websocket()
    other_websocket = make_mock_websocket()

    asyncio.run(menu_manager.connect(websocket))
    menu_manager.disconnect(other_websocket)

    assert menu_manager.active_connections == [websocket]


def test_menu_manager_broadcast_sends_and_cleans_up_failed_connections():
    menu_manager = MenuManager()
    healthy_ws = make_mock_websocket()
    failing_ws = make_mock_websocket()

    asyncio.run(menu_manager.connect(healthy_ws))
    asyncio.run(menu_manager.connect(failing_ws))

    failing_ws.send_json.side_effect = RuntimeError("boom")

    message = {"payload": 1}
    asyncio.run(menu_manager.broadcast(message))

    healthy_ws.send_json.assert_awaited_once_with(message)
    assert failing_ws.send_json.await_count == 1
    assert healthy_ws in menu_manager.active_connections
    assert failing_ws not in menu_manager.active_connections


def test_menu_manager_heartbeat_without_connections(monkeypatch):
    menu_manager = MenuManager()
    broadcast_mock = AsyncMock()
    monkeypatch.setattr(menu_manager, "broadcast", broadcast_mock)

    sleep_mock = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    async def run_heartbeat():
        with pytest.raises(asyncio.CancelledError):
            await menu_manager.heartbeat(interval=5)

    asyncio.run(run_heartbeat())

    broadcast_mock.assert_not_awaited()
    sleep_mock.assert_awaited_once_with(5)


def test_menu_manager_heartbeat_with_connections(monkeypatch):
    menu_manager = MenuManager()
    menu_manager.active_connections.append(object())

    broadcast_mock = AsyncMock()
    monkeypatch.setattr(menu_manager, "broadcast", broadcast_mock)

    sleep_mock = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    async def run_heartbeat():
        with pytest.raises(asyncio.CancelledError):
            await menu_manager.heartbeat(interval=2)

    asyncio.run(run_heartbeat())

    broadcast_mock.assert_awaited_once_with({"type": "ping"})
    sleep_mock.assert_awaited_once_with(2)


def make_test_client():
    app = FastAPI()
    app.include_router(router)

    async def override_get_db():
        yield None

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_menu_websocket_endpoint_lifecycle():
    client = make_test_client()
    global_menu_manager.active_connections.clear()

    with client.websocket_connect("/ws") as websocket:
        assert len(global_menu_manager.active_connections) == 1
        websocket.send_json({"hello": "world"})

    assert len(global_menu_manager.active_connections) == 0
    client.close()


def test_game_websocket_endpoint_tracks_connections():
    client = make_test_client()
    global_connection_manager.active_connections.clear()
    game_id = uuid4()

    with client.websocket_connect(f"/ws/{game_id}") as websocket:
        assert game_id in global_connection_manager.active_connections
        assert len(global_connection_manager.active_connections[game_id]) == 1
        websocket.send_json({"type": "bar"})

    assert game_id in global_connection_manager.active_connections
    assert global_connection_manager.active_connections[game_id] == []
    global_connection_manager.active_connections.pop(game_id, None)
    client.close()

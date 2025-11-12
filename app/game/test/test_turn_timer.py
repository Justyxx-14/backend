import pytest
import asyncio
import uuid
from app.game.turn_timer import TurnTimer, TurnTimerManager


@pytest.mark.asyncio
async def test_turn_timer_triggers_callback():
    """Debe ejecutar el callback al cumplirse el tiempo."""
    called = []

    async def on_timeout(game_id):
        called.append(game_id)

    game_id = uuid.uuid4()
    timer = TurnTimer(game_id, turn_duration=0.1, on_turn_timeout=on_timeout)

    timer.start()
    await asyncio.sleep(0.15)
    assert called == [game_id]
    timer.stop()


@pytest.mark.asyncio
async def test_turn_timer_pause_and_resume():
    """Debe pausar y reanudar el timer correctamente."""
    called = []

    async def on_timeout(game_id):
        called.append(game_id)

    game_id = uuid.uuid4()
    timer = TurnTimer(game_id, 0.2, on_timeout)

    timer.start()
    await asyncio.sleep(0.1)
    timer.pause()
    remaining = timer._remaining
    await asyncio.sleep(0.2)
    assert called == []
    assert timer.is_paused()
    assert remaining > 0

    timer.resume()
    await asyncio.sleep(remaining + 0.05)
    assert called == [game_id]
    timer.stop()


@pytest.mark.asyncio
async def test_turn_timer_stop_before_timeout():
    """Si se detiene antes de expirar, no debe llamar al callback."""
    called = []

    async def on_timeout(game_id):
        called.append(game_id)

    game_id = uuid.uuid4()
    timer = TurnTimer(game_id, 0.2, on_timeout)
    timer.start()
    await asyncio.sleep(0.1)
    timer.stop()
    await asyncio.sleep(0.2)
    assert called == []


@pytest.mark.asyncio
async def test_turn_timer_manager_start_stop():
    """El manager debe iniciar y detener correctamente los timers."""
    called = []

    async def on_timeout(game_id):
        called.append(game_id)

    manager = TurnTimerManager(turn_duration=0.1)
    game_id = uuid.uuid4()

    manager.start_timer(game_id, on_timeout)
    assert game_id in manager.timers
    await asyncio.sleep(0.15)
    assert called == [game_id]

    manager.stop_timer(game_id)
    assert game_id not in manager.timers


@pytest.mark.asyncio
async def test_turn_timer_manager_stop_all():
    """Debe detener todos los timers activos."""
    async def dummy_callback(_): pass

    manager = TurnTimerManager(turn_duration=5)
    ids = [uuid.uuid4() for _ in range(3)]
    for gid in ids:
        manager.start_timer(gid, dummy_callback)

    assert len(manager.timers) == 3
    manager.stop_all()
    assert len(manager.timers) == 0


@pytest.mark.asyncio
async def test_turn_timer_manager_restart_equivalent():
    """Reiniciar un timer (stop+start) debe reemplazar correctamente la instancia."""
    called = []

    async def on_timeout(game_id):
        called.append(game_id)

    manager = TurnTimerManager(turn_duration=0.1)
    game_id = uuid.uuid4()

    manager.start_timer(game_id, on_timeout)
    timer_before = manager.timers[game_id]

    manager.stop_timer(game_id)
    manager.start_timer(game_id, on_timeout)
    timer_after = manager.timers[game_id]

    assert timer_before is not timer_after
    await asyncio.sleep(0.15)
    assert called == [game_id]

@pytest.mark.asyncio
async def test_pause_without_running_does_nothing():
    """Pausar sin haber iniciado no debe fallar."""
    async def dummy(_): pass
    t = TurnTimer(uuid.uuid4(), 0.1, dummy)
    t.pause()
    assert not t._running
    assert not t._paused


@pytest.mark.asyncio
async def test_resume_without_pause_does_nothing():
    """Intentar reanudar sin estar pausado no debe generar errores."""
    async def dummy(_): pass
    t = TurnTimer(uuid.uuid4(), 0.1, dummy)
    t.start()
    await asyncio.sleep(0.05)
    t.resume()
    assert t._running
    t.stop()


@pytest.mark.asyncio
async def test_multiple_starts_only_starts_once():
    """Llamar a start() varias veces no debe crear múltiples tareas."""
    async def dummy(_): pass
    t = TurnTimer(uuid.uuid4(), 0.1, dummy)
    t.start()
    task_before = t._task
    t.start()
    task_after = t._task
    assert task_before is task_after
    t.stop()


@pytest.mark.asyncio
async def test_stop_timer_removes_from_manager_even_if_not_running():
    """Debe eliminar el timer del manager aunque no esté activo."""
    async def dummy(_): pass
    manager = TurnTimerManager(0.1)
    gid = uuid.uuid4()
    manager.timers[gid] = TurnTimer(gid, 0.1, dummy)
    manager.stop_timer(gid)
    assert gid not in manager.timers


@pytest.mark.asyncio
async def test_handle_timeout_supports_sync_callback():
    """Debe soportar callbacks síncronos (no corutinas)."""
    called = []

    def sync_callback(game_id):
        called.append(game_id)

    game_id = uuid.uuid4()
    t = TurnTimer(game_id, 0.05, sync_callback)
    t.start()
    await asyncio.sleep(0.1)
    assert called == [game_id]
    t.stop()


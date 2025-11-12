import asyncio
from typing import Optional, Callable
from uuid import UUID
import time

class TurnTimer:
    def __init__(self, game_id: UUID, turn_duration: int, on_turn_timeout: Callable[[UUID], None]):
        self.game_id = game_id
        self.turn_duration = turn_duration
        self.on_turn_timeout = on_turn_timeout

        self._running = False
        self._paused = False
        self._remaining = turn_duration
        self._task: Optional[asyncio.Task] = None
        self._last_start_time: Optional[float] = None

    async def _run(self):
        """Loop interno del temporizador."""
        while self._running:
            start_time = time.monotonic()
            try:
                await asyncio.sleep(self._remaining)
            except asyncio.CancelledError:
                return  # Timer cancelado

            # Si no fue pausado ni detenido, ejecuta el callback
            if not self._paused and self._running:
                await self._handle_timeout()
                # Reinicia el ciclo (nuevo turno)
                self._remaining = self.turn_duration
                self._last_start_time = time.monotonic()

    async def _handle_timeout(self):
        result = self.on_turn_timeout(self.game_id)
        if asyncio.iscoroutine(result):
            await result

    def start(self):
        """Inicia o reanuda el temporizador."""
        if not self._running:
            self._running = True
            self._paused = False
            self._last_start_time = time.monotonic()
            self._task = asyncio.create_task(self._run())

    def pause(self):
        """Pausa el temporizador actual (sin cancelarlo)."""
        if self._running and not self._paused:
            self._paused = True
            elapsed = time.monotonic() - self._last_start_time
            self._remaining -= elapsed
            if self._remaining < 0:
                self._remaining = 0
            self._task.cancel()

    def resume(self):
        """Reanuda el temporizador pausado."""
        if self._running and self._paused:
            self._paused = False
            self._last_start_time = time.monotonic()
            self._task = asyncio.create_task(self._run())

    def stop(self):
        """Detiene completamente el temporizador."""
        self._running = False
        self._paused = False
        self._remaining = self.turn_duration
        if self._task:
            self._task.cancel()
            self._task = None

    def is_running(self):
        return self._running and not self._paused

    def is_paused(self):
        return self._paused
    
    def get_remaining_time(self) -> float:
        """Devuelve el tiempo restante (en segundos) hasta que termine el turno actual."""
        if not self._running:
            return 0.0
        if self._paused:
            return self._remaining
        else:
            elapsed = time.monotonic() - self._last_start_time
            return max(self._remaining - elapsed, 0)

class TurnTimerManager:
    def __init__(self, turn_duration: int):
        self.turn_duration = turn_duration
        self.timers: dict[UUID, TurnTimer] = {}

    def start_timer(self, game_id: UUID, on_turn_timeout: Callable[[UUID], None]):
        """Inicia el temporizador para una partida."""
        if game_id in self.timers:
            return
        timer = TurnTimer(game_id, self.turn_duration, on_turn_timeout)
        self.timers[game_id] = timer
        timer.start()

    def pause_timer(self, game_id: UUID):
        timer = self.timers.get(game_id)
        if timer:
            timer.pause()

    def resume_timer(self, game_id: UUID):
        timer = self.timers.get(game_id)
        if timer:
            timer.resume()

    def stop_timer(self, game_id: UUID):
        """Detiene y elimina el temporizador de una partida."""
        timer = self.timers.pop(game_id, None)
        if timer:
            timer.stop()

    def stop_all(self):
        for game_id in list(self.timers.keys()):
            self.stop_timer(game_id)
    
    def get_remaining_time(self, game_id: UUID) -> Optional[float]:
        timer = self.timers.get(game_id)
        if timer:
            return timer.get_remaining_time()
        return None

    def get_is_paused(self, game_id: UUID) -> Optional[bool]:
        timer = self.timers.get(game_id)
        if timer:
            return timer.is_paused()
        return None
    
turn_timer_manager = TurnTimerManager(60)
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from src.uno_engine.game import GameSettings, GameState, new_game, restart_round


@dataclass
class Session:
    """A stored game session along with its RNG and metadata."""
    game: GameState
    rng: random.Random
    last_access_at: float


class InMemoryGameStore:
    """Thread-safe in-memory store for UNO games."""
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: Dict[str, Session] = {}

    def create(self, *, mode: str = "singleplayer", settings: Optional[GameSettings] = None) -> GameState:
        rng = random.Random()
        game = new_game(rng=rng, mode=mode, settings=settings)
        with self._lock:
            self._sessions[game.id] = Session(game=game, rng=rng, last_access_at=time.time())
        return game

    def get(self, game_id: str) -> Session:
        with self._lock:
            sess = self._sessions.get(game_id)
            if not sess:
                raise KeyError("Game not found.")
            sess.last_access_at = time.time()
            return sess

    def restart(self, game_id: str) -> GameState:
        sess = self.get(game_id)
        restart_round(sess.game, sess.rng)
        sess.last_access_at = time.time()
        return sess.game

    def patch_settings(self, game_id: str, patch: dict) -> GameState:
        sess = self.get(game_id)
        # Settings updates handled by engine to keep validation consistent.
        from src.uno_engine.game import update_settings

        update_settings(sess.game, patch)
        sess.last_access_at = time.time()
        return sess.game

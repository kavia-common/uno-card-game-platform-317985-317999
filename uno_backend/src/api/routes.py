from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.uno_engine.game import UnoRuleError, as_public_state, draw_card, pass_turn, play_card
from src.uno_engine.store import InMemoryGameStore


router = APIRouter(prefix="/games", tags=["games"])


class CreateGameRequest(BaseModel):
    mode: Literal["singleplayer", "vs_ai", "local", "multiplayer"] = Field(
        default="singleplayer",
        description="Game mode. 'singleplayer' creates You vs CPU by default.",
    )


class JoinGameRequest(BaseModel):
    playerName: Optional[str] = Field(default=None, description="Optional name for player 2 (if supported).")


class PlayCardRequest(BaseModel):
    cardId: str = Field(..., description="ID of the card in the current player's hand to play.")
    chosenColor: Optional[Literal["red", "yellow", "green", "blue"]] = Field(
        default=None,
        description="Required when playing WILD or WILD_DRAW_FOUR to pick the next active color.",
    )
    playerId: str = Field(default="p1", description="Player performing the action (defaults to p1 / You).")


class DrawCardRequest(BaseModel):
    playerId: str = Field(default="p1", description="Player performing the action (defaults to p1 / You).")


class PassRequest(BaseModel):
    playerId: str = Field(default="p1", description="Player performing the action (defaults to p1 / You).")


class SettingsPatchRequest(BaseModel):
    handSize: Optional[int] = Field(default=None, ge=1, le=15, description="Initial hand size for new rounds.")
    aiEnabled: Optional[bool] = Field(default=None, description="Enable/disable AI behavior.")
    aiDelayMs: Optional[int] = Field(default=None, ge=0, le=5000, description="AI delay (ms).")
    autoPlayIfDrawnPlayable: Optional[bool] = Field(
        default=None, description="If true, drawing auto-plays the drawn card when playable."
    )
    allowIllegalWildDrawFour: Optional[bool] = Field(
        default=None, description="If true, Wild Draw Four allowed even with a matching color in hand."
    )
    scoreLimit: Optional[int] = Field(default=None, ge=50, le=5000, description="Match ends when a player reaches this score.")


def _public(game_store: InMemoryGameStore, game_id: str, you_player_id: str = "p1") -> Dict[str, Any]:
    sess = game_store.get(game_id)
    return as_public_state(sess.game, you_player_id=you_player_id)


# PUBLIC_INTERFACE
def build_games_router(game_store: InMemoryGameStore) -> APIRouter:
    """Build and return the games router bound to the given in-memory game store."""
    # We close over `game_store` to keep the rest of the module stateless.

    @router.post(
        "",
        summary="Create a new UNO game session",
        description="Creates a new in-memory game session and returns the initial public game state.",
        operation_id="create_game",
    )
    def create_game(req: CreateGameRequest) -> Dict[str, Any]:
        try:
            game = game_store.create(mode=req.mode)
            return as_public_state(game, you_player_id="p1")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @router.post(
        "/{game_id}/join",
        summary="Join a game (placeholder for local multiplayer)",
        description="For this template app, joining is a no-op and returns current state.",
        operation_id="join_game",
    )
    def join_game(game_id: str, req: JoinGameRequest) -> Dict[str, Any]:
        try:
            # Minimal: optionally rename player 2 if exists.
            sess = game_store.get(game_id)
            if req.playerName and len(sess.game.players) >= 2:
                sess.game.players[1].name = req.playerName
            return as_public_state(sess.game, you_player_id="p1")
        except KeyError as e:
            raise HTTPException(status_code=404, detail="Game not found.") from e

    @router.get(
        "/{game_id}",
        summary="Get game state",
        description="Returns the current public game state. Includes your full hand under `you.hand`.",
        operation_id="get_game_state",
    )
    def get_game_state(game_id: str, playerId: str = "p1") -> Dict[str, Any]:
        try:
            return _public(game_store, game_id, you_player_id=playerId)
        except KeyError as e:
            raise HTTPException(status_code=404, detail="Game not found.") from e

    @router.post(
        "/{game_id}/play",
        summary="Play a card",
        description="Plays a card from the current player's hand. If the card is wild, `chosenColor` is required.",
        operation_id="play_card",
    )
    def play(game_id: str, req: PlayCardRequest) -> Dict[str, Any]:
        try:
            sess = game_store.get(game_id)
            play_card(
                sess.game,
                player_id=req.playerId,
                card_id=req.cardId,
                chosen_color=req.chosenColor,
                rng=sess.rng,
            )
            return as_public_state(sess.game, you_player_id=req.playerId)
        except KeyError as e:
            raise HTTPException(status_code=404, detail="Game not found.") from e
        except UnoRuleError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @router.post(
        "/{game_id}/draw",
        summary="Draw a card",
        description="Draws a card for the current player. Penalties (+2/+4) are enforced via pending draws.",
        operation_id="draw_card",
    )
    def draw(game_id: str, req: DrawCardRequest) -> Dict[str, Any]:
        try:
            sess = game_store.get(game_id)
            draw_card(sess.game, player_id=req.playerId, rng=sess.rng)
            return as_public_state(sess.game, you_player_id=req.playerId)
        except KeyError as e:
            raise HTTPException(status_code=404, detail="Game not found.") from e
        except UnoRuleError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @router.post(
        "/{game_id}/pass",
        summary="Pass/resolve turn",
        description="Ends the current player's turn without playing a card (if allowed).",
        operation_id="pass_turn",
    )
    def pass_endpoint(game_id: str, req: PassRequest) -> Dict[str, Any]:
        try:
            sess = game_store.get(game_id)
            pass_turn(sess.game, player_id=req.playerId, rng=sess.rng)
            return as_public_state(sess.game, you_player_id=req.playerId)
        except KeyError as e:
            raise HTTPException(status_code=404, detail="Game not found.") from e
        except UnoRuleError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @router.patch(
        "/{game_id}/settings",
        summary="Update game settings",
        description="Patches game settings for the session; affects future actions and next restarts.",
        operation_id="update_settings",
    )
    def patch_settings(game_id: str, req: SettingsPatchRequest) -> Dict[str, Any]:
        try:
            patch = {k: v for k, v in req.model_dump().items() if v is not None}
            game = game_store.patch_settings(game_id, patch)
            return as_public_state(game, you_player_id="p1")
        except KeyError as e:
            raise HTTPException(status_code=404, detail="Game not found.") from e

    @router.post(
        "/{game_id}/restart",
        summary="Restart round",
        description="Restarts the round (re-deals hands, resets piles) while preserving scores.",
        operation_id="restart_game",
    )
    def restart(game_id: str) -> Dict[str, Any]:
        try:
            game = game_store.restart(game_id)
            return as_public_state(game, you_player_id="p1")
        except KeyError as e:
            raise HTTPException(status_code=404, detail="Game not found.") from e

    return router

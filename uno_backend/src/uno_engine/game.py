from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Tuple

UNOColor = Literal["red", "yellow", "green", "blue"]
CardColor = Literal["red", "yellow", "green", "blue", "wild"]
CardValue = Literal[
    "0",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "SKIP",
    "REVERSE",
    "DRAW_TWO",
    "WILD",
    "WILD_DRAW_FOUR",
]

VALID_COLORS: Tuple[UNOColor, ...] = ("red", "yellow", "green", "blue")


@dataclass(frozen=True)
class UnoCard:
    """A single UNO card. Card identity is stable by id."""

    id: str
    color: CardColor
    value: CardValue


@dataclass
class Player:
    """Player state for a game session."""

    id: str
    name: str
    is_ai: bool
    score: int = 0
    hand: List[UnoCard] = field(default_factory=list)


@dataclass
class GameSettings:
    """Game settings configurable at runtime."""

    hand_size: int = 7
    ai_enabled: bool = True
    ai_delay_ms: int = 250
    # If True, a drawn card is auto-played if playable (common house rule).
    auto_play_if_drawn_playable: bool = True
    # If True, a player may play a Draw Four even if they have a matching color (not enforced).
    allow_illegal_wild_draw_four: bool = False
    # Score limit; if reached, status becomes "match_over".
    score_limit: int = 500


@dataclass
class GameState:
    """In-memory state for a single UNO game session."""

    id: str
    status: Literal["lobby", "playing", "round_over", "match_over"] = "playing"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    players: List[Player] = field(default_factory=list)
    current_player_index: int = 0
    direction: Literal[1, -1] = 1

    draw_pile: List[UnoCard] = field(default_factory=list)
    discard_pile: List[UnoCard] = field(default_factory=list)

    current_color: Optional[UNOColor] = None

    # Turn-phase flags
    pending_draw: int = 0  # accumulated +2/+4 penalty to be drawn by next player
    must_play_or_pass: bool = False  # set after drawing when auto-play is disabled
    last_drawn_card_id: Optional[str] = None

    # Round winner (when round_over)
    winner_player_id: Optional[str] = None

    # For UX messaging
    message: Optional[str] = None

    settings: GameSettings = field(default_factory=GameSettings)


def _new_card_id() -> str:
    return uuid.uuid4().hex


def build_uno_deck(rng: random.Random) -> List[UnoCard]:
    """Build and shuffle a standard 108-card UNO deck."""
    deck: List[UnoCard] = []

    # Colored cards
    for color in VALID_COLORS:
        # One 0
        deck.append(UnoCard(id=_new_card_id(), color=color, value="0"))
        # Two of each 1-9
        for v in ["1", "2", "3", "4", "5", "6", "7", "8", "9"]:
            deck.append(UnoCard(id=_new_card_id(), color=color, value=v))  # type: ignore[arg-type]
            deck.append(UnoCard(id=_new_card_id(), color=color, value=v))  # type: ignore[arg-type]

        # Two of each action
        for v in ["SKIP", "REVERSE", "DRAW_TWO"]:
            deck.append(UnoCard(id=_new_card_id(), color=color, value=v))  # type: ignore[arg-type]
            deck.append(UnoCard(id=_new_card_id(), color=color, value=v))  # type: ignore[arg-type]

    # Wilds
    for _ in range(4):
        deck.append(UnoCard(id=_new_card_id(), color="wild", value="WILD"))
        deck.append(UnoCard(id=_new_card_id(), color="wild", value="WILD_DRAW_FOUR"))

    rng.shuffle(deck)
    return deck


def score_card(card: UnoCard) -> int:
    """Compute score contribution for a card remaining in a player's hand."""
    if card.value in {"SKIP", "REVERSE", "DRAW_TWO"}:
        return 20
    if card.value in {"WILD", "WILD_DRAW_FOUR"}:
        return 50
    # numeric
    try:
        return int(card.value)
    except ValueError:
        return 0


def _is_numeric(v: CardValue) -> bool:
    return v.isdigit()


def can_play_on(card: UnoCard, top: UnoCard, current_color: Optional[UNOColor]) -> bool:
    """Rules: wild always playable; else match color (current_color) or match value."""
    if card.color == "wild":
        return True
    color_to_match: Optional[str] = current_color or top.color
    if color_to_match and card.color == color_to_match:
        return True
    if card.value == top.value:
        return True
    return False


def _next_index(state: GameState, steps: int = 1) -> int:
    n = len(state.players)
    return (state.current_player_index + state.direction * steps) % n


def _reshuffle_if_needed(state: GameState, rng: random.Random) -> None:
    """If draw pile is empty, reshuffle discard (except top) into draw pile."""
    if state.draw_pile:
        return
    if len(state.discard_pile) <= 1:
        # No cards to reshuffle (shouldn't happen often)
        return
    top = state.discard_pile[-1]
    rest = state.discard_pile[:-1]
    rng.shuffle(rest)
    state.draw_pile = rest
    state.discard_pile = [top]


def _draw_one(state: GameState, rng: random.Random) -> UnoCard:
    _reshuffle_if_needed(state, rng)
    if not state.draw_pile:
        # As a last resort, rebuild a deck; very unlikely.
        state.draw_pile = build_uno_deck(rng)
    return state.draw_pile.pop()


def _draw_cards_to_player(state: GameState, player: Player, count: int, rng: random.Random) -> List[UnoCard]:
    drawn: List[UnoCard] = []
    for _ in range(max(0, count)):
        c = _draw_one(state, rng)
        player.hand.append(c)
        drawn.append(c)
    return drawn


def _find_starting_discard(state: GameState, rng: random.Random) -> UnoCard:
    """
    Standard UNO: flip top of draw pile to start discard.
    If action/wild is flipped, many rules treat it specially; we simplify:
    - If wild: set current_color randomly
    - If action: apply its effect to starting turn.
    """
    while True:
        c = _draw_one(state, rng)
        state.discard_pile.append(c)
        if c.value in {"WILD", "WILD_DRAW_FOUR"}:
            state.current_color = rng.choice(list(VALID_COLORS))
        elif c.color in VALID_COLORS:
            state.current_color = c.color  # type: ignore[assignment]
        else:
            state.current_color = rng.choice(list(VALID_COLORS))
        return c


def _apply_action_effects_after_play(
    state: GameState,
    played: UnoCard,
) -> Tuple[int, bool]:
    """
    Returns (skip_steps, flips_direction).
    Effects:
    - SKIP: next player skipped => steps 2
    - REVERSE: flips direction; in 2-player game, acts like SKIP
    - DRAW_TWO: set pending_draw += 2, skip next player's play (they draw then lose turn)
    - WILD_DRAW_FOUR: pending_draw += 4, skip similarly
    - WILD: requires chosen color already set by caller
    """
    n = len(state.players)
    if played.value == "SKIP":
        return (2, False)
    if played.value == "REVERSE":
        if n == 2:
            return (2, True)  # flip + skip behaves as skip effectively
        return (1, True)
    if played.value == "DRAW_TWO":
        state.pending_draw += 2
        return (2, False)
    if played.value == "WILD_DRAW_FOUR":
        state.pending_draw += 4
        return (2, False)
    return (1, False)


def _resolve_round_if_won(state: GameState) -> bool:
    """If any player has empty hand, end the round and update score."""
    winner = next((p for p in state.players if len(p.hand) == 0), None)
    if not winner:
        return False

    points = 0
    for p in state.players:
        if p.id == winner.id:
            continue
        for c in p.hand:
            points += score_card(c)

    winner.score += points
    state.winner_player_id = winner.id

    if winner.score >= state.settings.score_limit:
        state.status = "match_over"
        state.message = f"{winner.name} wins the match (+{points})."
    else:
        state.status = "round_over"
        state.message = f"{winner.name} wins the round (+{points})."
    return True


def _ai_choose_color(player: Player) -> UNOColor:
    """Pick the most common color in hand; fallback to red."""
    counts: Dict[UNOColor, int] = {c: 0 for c in VALID_COLORS}
    for card in player.hand:
        if card.color in VALID_COLORS:
            counts[card.color] += 1  # type: ignore[index]
    best = max(counts.items(), key=lambda kv: kv[1])[0]
    return best or "red"


def _ai_choose_play(state: GameState, player: Player) -> Tuple[Optional[UnoCard], Optional[UNOColor]]:
    """Very simple AI: play first playable card, preferring action cards."""
    if not state.discard_pile:
        return (None, None)
    top = state.discard_pile[-1]
    playable = [c for c in player.hand if can_play_on(c, top, state.current_color)]

    if not playable:
        return (None, None)

    def prio(c: UnoCard) -> int:
        if c.value in {"WILD_DRAW_FOUR", "DRAW_TWO", "SKIP", "REVERSE"}:
            return 0
        if c.value == "WILD":
            return 1
        if _is_numeric(c.value):
            return 2
        return 3

    playable.sort(key=prio)
    chosen = playable[0]
    chosen_color = None
    if chosen.color == "wild":
        chosen_color = _ai_choose_color(player)
    return (chosen, chosen_color)


def new_game(
    *,
    rng: random.Random,
    mode: str = "singleplayer",
    human_name: str = "You",
    cpu_name: str = "CPU",
    settings: Optional[GameSettings] = None,
) -> GameState:
    """Create a new game session with initial shuffle and hands dealt."""
    gid = uuid.uuid4().hex
    st = GameState(id=gid)
    if settings:
        st.settings = settings

    human = Player(id="p1", name=human_name, is_ai=False)
    players = [human]

    if mode in {"singleplayer", "vs_ai"}:
        if st.settings.ai_enabled:
            players.append(Player(id="p2", name=cpu_name, is_ai=True))
    elif mode in {"local", "multiplayer"}:
        # Minimal support: two humans local
        players.append(Player(id="p2", name="Player 2", is_ai=False))

    st.players = players

    st.draw_pile = build_uno_deck(rng)
    st.discard_pile = []

    # Deal
    for p in st.players:
        _draw_cards_to_player(st, p, st.settings.hand_size, rng)

    # Start discard
    first = _find_starting_discard(st, rng)

    # If first is action, apply effect before first turn in a simplified way:
    if first.value in {"SKIP", "REVERSE", "DRAW_TWO", "WILD_DRAW_FOUR"}:
        # Use same effect logic as normal play (with current player index 0)
        skip_steps, flip_dir = _apply_action_effects_after_play(st, first)
        if flip_dir:
            st.direction = -st.direction  # type: ignore[assignment]
        st.current_player_index = _next_index(st, skip_steps - 1)

    st.status = "playing"
    st.updated_at = time.time()
    st.message = "Game started."
    return st


def restart_round(state: GameState, rng: random.Random) -> None:
    """Restart round with same players and scores; re-deal hands and reset piles."""
    for p in state.players:
        p.hand = []

    state.draw_pile = build_uno_deck(rng)
    state.discard_pile = []

    for p in state.players:
        _draw_cards_to_player(state, p, state.settings.hand_size, rng)

    first = _find_starting_discard(state, rng)

    state.current_player_index = 0
    state.direction = 1
    state.pending_draw = 0
    state.must_play_or_pass = False
    state.last_drawn_card_id = None
    state.winner_player_id = None
    state.status = "playing"
    state.updated_at = time.time()
    state.message = "Round restarted."

    # Apply initial effect if needed
    if first.value in {"SKIP", "REVERSE", "DRAW_TWO", "WILD_DRAW_FOUR"}:
        skip_steps, flip_dir = _apply_action_effects_after_play(state, first)
        if flip_dir:
            state.direction = -state.direction  # type: ignore[assignment]
        state.current_player_index = _next_index(state, skip_steps - 1)


def as_public_state(state: GameState, you_player_id: str = "p1") -> dict:
    """
    Convert internal state to a frontend-friendly shape.
    - Includes 'you.hand' for the requested player_id
    - Includes each player's handCount but not other players' cards
    """
    top = state.discard_pile[-1] if state.discard_pile else None

    players_out = []
    for p in state.players:
        players_out.append(
            {
                "id": p.id,
                "name": p.name,
                "isAI": p.is_ai,
                "score": p.score,
                "handCount": len(p.hand),
                "isYou": p.id == you_player_id,
                "hand": [card.__dict__ for card in p.hand] if p.id == you_player_id else None,
            }
        )

    you = next((p for p in state.players if p.id == you_player_id), state.players[0])

    return {
        "gameId": state.id,
        "status": state.status,
        "message": state.message,
        "currentPlayerIndex": state.current_player_index,
        "direction": state.direction,
        "players": [
            {
                "id": p["id"],
                "name": p["name"],
                "isAI": p["isAI"],
                "score": p["score"],
                "handCount": p["handCount"],
                "isYou": p["isYou"],
            }
            for p in players_out
        ],
        "you": {
            "playerId": you.id,
            "hand": [c.__dict__ for c in you.hand],
        },
        "discardTop": top.__dict__ if top else None,
        "currentColor": state.current_color,
        "drawPileCount": len(state.draw_pile),
        "pendingDraw": state.pending_draw,
        "mustPlayOrPass": state.must_play_or_pass,
        "winnerPlayerId": state.winner_player_id,
        "updatedAt": state.updated_at,
        "settings": {
            "handSize": state.settings.hand_size,
            "aiEnabled": state.settings.ai_enabled,
            "aiDelayMs": state.settings.ai_delay_ms,
            "autoPlayIfDrawnPlayable": state.settings.auto_play_if_drawn_playable,
            "allowIllegalWildDrawFour": state.settings.allow_illegal_wild_draw_four,
            "scoreLimit": state.settings.score_limit,
        },
    }


class UnoRuleError(ValueError):
    """Raised when a player attempts an invalid action."""


def _require_playing(state: GameState) -> None:
    if state.status not in {"playing"}:
        raise UnoRuleError(f"Game is not in playing state (status={state.status}).")


def _get_player(state: GameState, player_id: str) -> Player:
    p = next((p for p in state.players if p.id == player_id), None)
    if not p:
        raise UnoRuleError("Unknown player.")
    return p


def _require_turn(state: GameState, player_id: str) -> Player:
    _require_playing(state)
    p = _get_player(state, player_id)
    cur = state.players[state.current_player_index]
    if cur.id != player_id:
        raise UnoRuleError("Not your turn.")
    return p


def _auto_resolve_ai_turns(state: GameState, rng: random.Random) -> None:
    """
    Run AI turns until it's a human turn or game ends.
    This keeps the API simple for the polling-based frontend.
    """
    while state.status == "playing":
        cur = state.players[state.current_player_index]
        if not cur.is_ai:
            return

        # If there is pending draw, AI must draw it and lose turn.
        if state.pending_draw > 0:
            _draw_cards_to_player(state, cur, state.pending_draw, rng)
            state.pending_draw = 0
            state.must_play_or_pass = False
            state.last_drawn_card_id = None
            state.current_player_index = _next_index(state, 1)
            state.updated_at = time.time()
            continue

        # Choose play or draw
        chosen, wild_color = _ai_choose_play(state, cur)
        if chosen is None:
            drawn = _draw_cards_to_player(state, cur, 1, rng)[0]
            # Auto-play if possible
            if state.settings.auto_play_if_drawn_playable and can_play_on(
                drawn, state.discard_pile[-1], state.current_color
            ):
                # play drawn
                cur.hand = [c for c in cur.hand if c.id != drawn.id]
                state.discard_pile.append(drawn)
                if drawn.color in VALID_COLORS:
                    state.current_color = drawn.color  # type: ignore[assignment]
                else:
                    state.current_color = wild_color or _ai_choose_color(cur)

                skip_steps, flip_dir = _apply_action_effects_after_play(state, drawn)
                if flip_dir:
                    state.direction = -state.direction  # type: ignore[assignment]
                if _resolve_round_if_won(state):
                    state.updated_at = time.time()
                    return
                state.current_player_index = _next_index(state, skip_steps)
            else:
                state.current_player_index = _next_index(state, 1)

            state.updated_at = time.time()
            continue

        # Play chosen
        cur.hand = [c for c in cur.hand if c.id != chosen.id]
        state.discard_pile.append(chosen)

        if chosen.color in VALID_COLORS:
            state.current_color = chosen.color  # type: ignore[assignment]
        else:
            state.current_color = wild_color or _ai_choose_color(cur)

        skip_steps, flip_dir = _apply_action_effects_after_play(state, chosen)
        if flip_dir:
            state.direction = -state.direction  # type: ignore[assignment]

        if _resolve_round_if_won(state):
            state.updated_at = time.time()
            return

        state.current_player_index = _next_index(state, skip_steps)
        state.updated_at = time.time()


# PUBLIC_INTERFACE
def play_card(
    state: GameState,
    *,
    player_id: str,
    card_id: str,
    chosen_color: Optional[str],
    rng: random.Random,
) -> None:
    """Play a card by id for the current player, enforcing UNO rules."""
    player = _require_turn(state, player_id)

    if state.pending_draw > 0:
        raise UnoRuleError("You must draw the pending penalty before playing.")

    if not state.discard_pile:
        raise UnoRuleError("No discard pile to play on.")

    card = next((c for c in player.hand if c.id == card_id), None)
    if not card:
        raise UnoRuleError("Card not in your hand.")

    top = state.discard_pile[-1]
    if not can_play_on(card, top, state.current_color):
        raise UnoRuleError("Card is not playable on the current discard.")

    # For wilds, require chosenColor
    if card.value in {"WILD", "WILD_DRAW_FOUR"}:
        if chosen_color not in VALID_COLORS:
            raise UnoRuleError("chosenColor is required for wild cards.")
        if (not state.settings.allow_illegal_wild_draw_four) and card.value == "WILD_DRAW_FOUR":
            # Optional rule enforcement: only allowed if no card of current_color in hand.
            color_to_match = state.current_color or (top.color if top.color in VALID_COLORS else None)
            if color_to_match and any(c.color == color_to_match for c in player.hand if c.id != card.id):
                raise UnoRuleError("Wild Draw Four not allowed when you have a matching color.")

        state.current_color = chosen_color  # type: ignore[assignment]
    else:
        # Normal card sets current_color to its color
        if card.color in VALID_COLORS:
            state.current_color = card.color  # type: ignore[assignment]

    # Move to discard
    player.hand = [c for c in player.hand if c.id != card.id]
    state.discard_pile.append(card)
    state.must_play_or_pass = False
    state.last_drawn_card_id = None

    skip_steps, flip_dir = _apply_action_effects_after_play(state, card)
    if flip_dir:
        state.direction = -state.direction  # type: ignore[assignment]

    if _resolve_round_if_won(state):
        state.updated_at = time.time()
        return

    state.current_player_index = _next_index(state, skip_steps)
    state.updated_at = time.time()
    state.message = f"{player.name} played {card.value}."

    _auto_resolve_ai_turns(state, rng)


# PUBLIC_INTERFACE
def draw_card(state: GameState, *, player_id: str, rng: random.Random) -> UnoCard:
    """Draw a card for the current player; may auto-play if enabled."""
    player = _require_turn(state, player_id)

    if state.pending_draw > 0:
        # Must draw penalty and lose turn
        _draw_cards_to_player(state, player, state.pending_draw, rng)
        state.message = f"{player.name} drew {state.pending_draw} cards."
        state.pending_draw = 0
        state.must_play_or_pass = False
        state.last_drawn_card_id = None
        state.current_player_index = _next_index(state, 1)
        state.updated_at = time.time()
        _auto_resolve_ai_turns(state, rng)
        # Return a synthetic last drawn (not super meaningful); choose the last card in hand.
        return player.hand[-1]

    drawn = _draw_cards_to_player(state, player, 1, rng)[0]
    state.last_drawn_card_id = drawn.id

    # Auto-play if possible and enabled
    top = state.discard_pile[-1]
    if state.settings.auto_play_if_drawn_playable and can_play_on(drawn, top, state.current_color):
        # Auto-play drawn card
        player.hand = [c for c in player.hand if c.id != drawn.id]
        state.discard_pile.append(drawn)
        chosen_color: Optional[UNOColor] = None
        if drawn.value in {"WILD", "WILD_DRAW_FOUR"}:
            chosen_color = _ai_choose_color(player) if player.is_ai else "red"
            state.current_color = chosen_color
        elif drawn.color in VALID_COLORS:
            state.current_color = drawn.color  # type: ignore[assignment]

        skip_steps, flip_dir = _apply_action_effects_after_play(state, drawn)
        if flip_dir:
            state.direction = -state.direction  # type: ignore[assignment]

        if _resolve_round_if_won(state):
            state.updated_at = time.time()
            return drawn

        state.current_player_index = _next_index(state, skip_steps)
        state.updated_at = time.time()
        state.message = f"{player.name} drew and played {drawn.value}."
        _auto_resolve_ai_turns(state, rng)
        return drawn

    # Otherwise drawing ends turn immediately (simple rule set)
    state.current_player_index = _next_index(state, 1)
    state.updated_at = time.time()
    state.message = f"{player.name} drew a card."
    _auto_resolve_ai_turns(state, rng)
    return drawn


# PUBLIC_INTERFACE
def pass_turn(state: GameState, *, player_id: str, rng: random.Random) -> None:
    """Pass/resolve current player's turn (useful for UIs that require an explicit pass)."""
    _require_turn(state, player_id)

    if state.pending_draw > 0:
        raise UnoRuleError("Cannot pass while a pending draw penalty exists. Draw first.")

    state.must_play_or_pass = False
    state.last_drawn_card_id = None
    state.current_player_index = _next_index(state, 1)
    state.updated_at = time.time()
    state.message = "Turn passed."
    _auto_resolve_ai_turns(state, rng)


# PUBLIC_INTERFACE
def update_settings(state: GameState, patch: dict) -> None:
    """Update mutable game settings."""
    s = state.settings

    if "handSize" in patch and isinstance(patch["handSize"], int) and 1 <= patch["handSize"] <= 15:
        s.hand_size = patch["handSize"]
    if "aiEnabled" in patch and isinstance(patch["aiEnabled"], bool):
        s.ai_enabled = patch["aiEnabled"]
        # If AI disabled, ensure no AI players exist beyond p1.
        for p in state.players[1:]:
            p.is_ai = patch["aiEnabled"]
    if "aiDelayMs" in patch and isinstance(patch["aiDelayMs"], int) and 0 <= patch["aiDelayMs"] <= 5000:
        s.ai_delay_ms = patch["aiDelayMs"]
    if "autoPlayIfDrawnPlayable" in patch and isinstance(patch["autoPlayIfDrawnPlayable"], bool):
        s.auto_play_if_drawn_playable = patch["autoPlayIfDrawnPlayable"]
    if "allowIllegalWildDrawFour" in patch and isinstance(patch["allowIllegalWildDrawFour"], bool):
        s.allow_illegal_wild_draw_four = patch["allowIllegalWildDrawFour"]
    if "scoreLimit" in patch and isinstance(patch["scoreLimit"], int) and 50 <= patch["scoreLimit"] <= 5000:
        s.score_limit = patch["scoreLimit"]

    state.updated_at = time.time()
    state.message = "Settings updated."

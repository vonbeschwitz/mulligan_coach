"""Runtime objects for a single goldfish game.

Two layers:

* :class:`Card` is a thin runtime wrapper around the upstream
  :class:`mulligan_coach_cards.ParsedCard`. It adds an ``instance_id``
  (so two copies of "Forest" in the same game are distinguishable)
  plus a couple of convenience flags computed once at construction.
* :class:`GameState` is the mutable per-game state — library, hand,
  the battlefield split into mana-relevant and other buckets, and the
  bookkeeping sets (tapped permanents, summoning-sick creatures).

The primitives on ``GameState`` (``draw``, ``play_land``, ``tap``,
``untap_all``, ``begin_turn``, ``end_turn``) are intentionally low-level
and policy-free. The land-drop and main-phase policies (steps 5 and 6
in the plan) call into these primitives; nothing in this module decides
*what* to play, only *how* to play it.

Snapshot / restore is provided so the castability snapshot routine
(step 3) can hypothetically play each candidate land in turn without
permanently mutating state.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from mulligan_coach_cards import ParsedCard


def _is_land(parsed: ParsedCard) -> bool:
    return "Land" in parsed.types


def _is_creature(parsed: ParsedCard) -> bool:
    return "Creature" in parsed.types


@dataclass(slots=True)
class Card:
    """One copy of a card in a single game.

    Two Forests in the same deck are *different* ``Card`` instances
    with different ``instance_id``s but the same ``parsed`` reference.
    Identity matters for tracking summoning-sickness and tapped state
    on the battlefield.
    """

    instance_id: int
    parsed: ParsedCard
    is_land: bool = field(init=False, default=False)
    is_creature: bool = field(init=False, default=False)
    has_mana_ability: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self.is_land = _is_land(self.parsed)
        self.is_creature = _is_creature(self.parsed)
        self.has_mana_ability = bool(self.parsed.mana_abilities)

    @property
    def name(self) -> str:
        return self.parsed.name


@dataclass(slots=True)
class GameStateSnapshot:
    """Frozen capture of a :class:`GameState`. Use ``GameState.snapshot()``
    to obtain one and ``GameState.restore(snap)`` to roll back.

    Lists / sets are *copied* at snapshot time, so callers can mutate the
    live state freely between snapshot and restore. We don't deep-copy
    Card / ParsedCard — those are treated as immutable by the engine.
    """

    library: list[Card]
    hand: list[Card]
    battlefield_lands: list[Card]
    battlefield_mana_perms: list[Card]
    battlefield_other: list[Card]
    graveyard: list[Card]
    tapped: frozenset[int]
    summoning_sick: frozenset[int]
    turn: int
    land_drop_used: bool


@dataclass(slots=True)
class GameState:
    """Per-game mutable state.

    Index conventions:

    * ``library[0]`` is the top of the library (what you'd draw next).
      Shuffling is ``rng.shuffle(self.library)``.
    * ``tapped`` and ``summoning_sick`` hold ``Card.instance_id`` values.
      Set membership instead of per-card flags so snapshot/restore is
      cheap and we don't need to mutate ``Card`` objects.

    The battlefield is split into three lists rather than one big list
    so the mana-source enumeration (step 2) can iterate just lands and
    mana-permanents without filtering through creatures-without-abilities
    every time. ``battlefield_other`` exists for completeness; in v1 we
    don't track much about non-mana cast permanents.
    """

    library: list[Card]
    hand: list[Card]
    battlefield_lands: list[Card] = field(default_factory=list)
    battlefield_mana_perms: list[Card] = field(default_factory=list)
    battlefield_other: list[Card] = field(default_factory=list)
    graveyard: list[Card] = field(default_factory=list)
    tapped: set[int] = field(default_factory=set)
    summoning_sick: set[int] = field(default_factory=set)
    turn: int = 0
    on_the_play: bool = True
    land_drop_used: bool = False
    rng: random.Random = field(default_factory=random.Random)

    @classmethod
    def initial(
        cls,
        hand: list[Card],
        library: list[Card],
        on_the_play: bool,
        rng: random.Random,
    ) -> GameState:
        """Build the start-of-game state. Caller is responsible for assigning
        unique ``instance_id`` to every Card and shuffling the library."""
        return cls(
            library=list(library),
            hand=list(hand),
            on_the_play=on_the_play,
            rng=rng,
        )

    # ------------------------------------------------------------------
    # Turn boundaries
    # ------------------------------------------------------------------

    def begin_turn(self) -> None:
        """Advance to the next turn: increment counter, untap, clear
        summoning sickness, reset land-drop flag."""
        self.turn += 1
        self.untap_all()
        self.summoning_sick.clear()
        self.land_drop_used = False

    def end_turn(self) -> None:
        """Hook for end-of-turn cleanup. Currently a no-op — we don't
        carry mana between actions or model end-of-turn triggers in v1.
        Kept for symmetry with begin_turn and as the obvious extension
        point if either becomes necessary."""

    # ------------------------------------------------------------------
    # Card-movement primitives
    # ------------------------------------------------------------------

    def draw(self, n: int = 1) -> list[Card]:
        """Move up to *n* cards from the top of the library to the hand
        and return them. If the library runs out, returns whatever was
        drawn before that — the caller decides whether decking matters
        (it doesn't in 4-turn goldfish)."""
        drawn: list[Card] = []
        for _ in range(n):
            if not self.library:
                break
            card = self.library.pop(0)
            self.hand.append(card)
            drawn.append(card)
        return drawn

    def play_land(self, card: Card) -> None:
        """Move *card* from hand to battlefield as a land.

        The ETB-tapped check uses the full predicate evaluator from
        :mod:`effects` and runs *before* the card is appended, so
        ``controls_lands_*`` predicates correctly count "other" lands
        only. Sets ``land_drop_used`` so a later call this turn raises.

        Land ETB *triggers* (e.g. "When this land enters, you gain 1
        life") are no-ops for the simulator — they don't affect mana
        or castability — and aren't resolved here.
        """
        # Imported lazily to avoid a circular import: ``effects`` needs
        # to type-check against ``Card`` / ``GameState`` defined here.
        from .effects import enters_tapped

        if not card.is_land:
            raise ValueError(f"play_land called on non-land {card.name!r}")
        if self.land_drop_used:
            raise RuntimeError("land drop already used this turn")
        self._remove_from_hand(card)
        # Evaluate the ETB predicate against the existing battlefield
        # (the new land is on the stack at this point, not yet on the
        # battlefield), then append.
        enters_tapped_now = enters_tapped(card.parsed, self)
        self.battlefield_lands.append(card)
        if enters_tapped_now:
            self.tapped.add(card.instance_id)
        self.land_drop_used = True

    def cast(self, card: Card, mode_idx: int, payment: object) -> None:
        """Resolve a cast / cycle / channel / activated mode of *card*.

        *payment* is a :class:`mana.ManaPayment` (the source pairs the
        mana solver returned). It's typed loosely here to avoid a
        runtime import cycle with :mod:`mana`.

        Operations, in order:

        1. Tap every mana-source ability whose cost includes ``{T}``.
           (The mana solver also returns filter-land activations whose
           own mana cost has already been paid by earlier sources in
           the payment list — we only need to tap them.)
        2. Pay the mode's *own* non-mana cost: ``cost.tap`` taps the
           source (activated modes only), ``cost.sacrifice == "self"``
           moves the source to graveyard, ``cost.discard_self`` moves
           a hand card to graveyard.
        3. If the mode is a cast Mode and the source is still in hand,
           move it to its destination zone (battlefield split by mana-
           ability presence; sorceries / instants → graveyard).
        4. Resolve the mode's effects via :mod:`effects.apply_mode_effects`.
        """
        # Lazy imports to keep the import graph acyclic.
        from .effects import apply_mode_effects
        from .mana import ManaPayment

        mode = card.parsed.modes[mode_idx]
        typed_payment: ManaPayment = payment  # type: ignore[assignment]

        # 1) Tap mana sources used to pay the cost.
        for ability_ref, _option in typed_payment:
            if ability_ref.ability.cost.tap:
                self.tap(ability_ref.source)

        # 2) Pay the mode's own non-mana cost.
        if mode.cost.tap and mode.kind == "activated":
            self.tap(card)
        if mode.cost.discard_self:
            # Cycling / channel / land-cycling discard the card itself.
            self._remove_from_hand(card)
            self.graveyard.append(card)
            in_hand_consumed = True
        else:
            in_hand_consumed = False
        if (
            mode.cost.sacrifice is not None
            and mode.cost.sacrifice.target == "self"
            and mode.kind == "activated"
        ):
            self._remove_from_battlefield(card)
            self.graveyard.append(card)

        # 3) Move the card if this was a cast Mode and we haven't
        #    already consumed it via discard_self.
        if mode.kind == "cast" and not in_hand_consumed:
            self._remove_from_hand(card)
            self._place_after_cast(card)

        # 4) Resolve effects.
        apply_mode_effects(self, mode.effects)

    def _place_after_cast(self, card: Card) -> None:
        """Send a just-cast card to its destination zone."""
        types = card.parsed.types
        if "Land" in types:
            # Lands aren't normally cast — handle for completeness.
            self.battlefield_lands.append(card)
            return
        if any(t in types for t in ("Creature", "Artifact", "Enchantment", "Planeswalker")):
            if card.has_mana_ability:
                self.battlefield_mana_perms.append(card)
            else:
                self.battlefield_other.append(card)
            if card.is_creature:
                self.summoning_sick.add(card.instance_id)
            return
        # Sorcery / Instant — straight to graveyard.
        self.graveyard.append(card)

    def _remove_from_battlefield(self, card: Card) -> None:
        """Remove *card* from whichever battlefield zone it's in.
        Also clears tapped / summoning-sick state for that instance."""
        for zone in (self.battlefield_lands, self.battlefield_mana_perms, self.battlefield_other):
            for i, c in enumerate(zone):
                if c.instance_id == card.instance_id:
                    del zone[i]
                    self.tapped.discard(card.instance_id)
                    self.summoning_sick.discard(card.instance_id)
                    return
        raise ValueError(f"{card.name!r} (id={card.instance_id}) not on battlefield")

    def tap(self, card: Card) -> None:
        """Mark *card* tapped. Idempotent."""
        self.tapped.add(card.instance_id)

    def untap_all(self) -> None:
        """Remove every permanent from the tapped set. Called by
        :meth:`begin_turn`; exposed for tests."""
        self.tapped.clear()

    def is_tapped(self, card: Card) -> bool:
        return card.instance_id in self.tapped

    def is_summoning_sick(self, card: Card) -> bool:
        return card.instance_id in self.summoning_sick

    # ------------------------------------------------------------------
    # Snapshot / restore
    # ------------------------------------------------------------------

    def snapshot(self) -> GameStateSnapshot:
        """Capture the current state. Lists/sets are copied so subsequent
        mutations don't leak into the snapshot."""
        return GameStateSnapshot(
            library=self.library.copy(),
            hand=self.hand.copy(),
            battlefield_lands=self.battlefield_lands.copy(),
            battlefield_mana_perms=self.battlefield_mana_perms.copy(),
            battlefield_other=self.battlefield_other.copy(),
            graveyard=self.graveyard.copy(),
            tapped=frozenset(self.tapped),
            summoning_sick=frozenset(self.summoning_sick),
            turn=self.turn,
            land_drop_used=self.land_drop_used,
        )

    def restore(self, snap: GameStateSnapshot) -> None:
        """Roll back to *snap*. Copies its lists/sets into the live state
        so further snapshots and mutations stay independent."""
        self.library = snap.library.copy()
        self.hand = snap.hand.copy()
        self.battlefield_lands = snap.battlefield_lands.copy()
        self.battlefield_mana_perms = snap.battlefield_mana_perms.copy()
        self.battlefield_other = snap.battlefield_other.copy()
        self.graveyard = snap.graveyard.copy()
        self.tapped = set(snap.tapped)
        self.summoning_sick = set(snap.summoning_sick)
        self.turn = snap.turn
        self.land_drop_used = snap.land_drop_used

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _remove_from_hand(self, card: Card) -> None:
        """Remove *card* from hand by identity. We compare on instance_id
        rather than ``==`` because two ``Card``s wrapping the same
        ``ParsedCard`` would compare equal under dataclass equality."""
        for i, c in enumerate(self.hand):
            if c.instance_id == card.instance_id:
                del self.hand[i]
                return
        raise ValueError(f"{card.name!r} (id={card.instance_id}) not in hand")

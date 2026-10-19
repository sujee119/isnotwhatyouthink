"""How many shorts today, and which slots post them.

A fixed daily count looks mechanical, so the target is random between
DAILY_MIN and DAILY_MAX. But eight cron runs fire independently and none
of them can see the others, so the randomness is SEEDED FROM THE DATE:
every run on the same day computes the same target and the same chosen
slots without any shared state, and without a race.

Slots are chosen by sampling rather than by filling the earliest ones.
Taking the first N would cluster every post into the morning and make the
posting times predictable; sampling spreads them across the day and moves
them day to day.
"""
from __future__ import annotations

import datetime as dt
import random

import config


def _seed(day: dt.date, salt: str = "") -> random.Random:
    # The channel name is in the seed so a second channel on the same
    # schedule would not choose identical slots.
    return random.Random(f"{config.CHANNEL}:{day.isoformat()}:{salt}")


def target_for(day: dt.date | None = None) -> int:
    """Today's number of shorts, stable for the whole day."""
    day = day or dt.date.today()
    lo, hi = config.DAILY_MIN, config.DAILY_MAX
    if hi < lo:
        lo, hi = hi, lo
    return _seed(day).randint(lo, hi)


def slots_for(day: dt.date | None = None) -> list[int]:
    """Which slot numbers (1..SLOTS) post today, sorted."""
    day = day or dt.date.today()
    total = max(1, config.SLOTS)
    n = min(target_for(day), total)
    return sorted(_seed(day, "slots").sample(range(1, total + 1), n))


def should_build(slot: int, day: dt.date | None = None) -> tuple[bool, str]:
    """Whether this slot is one of today's. Returns (go, why)."""
    day = day or dt.date.today()
    chosen = slots_for(day)
    n = target_for(day)
    if slot <= 0:
        return True, f"manual run (today's target is {n})"
    if slot in chosen:
        rank = chosen.index(slot) + 1
        return True, (f"slot {slot} is {rank} of {len(chosen)} today "
                      f"(target {n}, slots {chosen})")
    return False, (f"slot {slot} is not posting today "
                   f"(target {n}, slots {chosen})")

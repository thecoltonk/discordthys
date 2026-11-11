from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Actor:
    """A Discord user acting on a ticket, as seen at the time of the action."""

    discord_id: int
    username: str | None
    is_staff: bool


def is_staff(role_ids: Iterable[int], staff_role_id: int) -> bool:
    return staff_role_id in set(role_ids)


def can_resolve(actor: Actor, opener_discord_id: int) -> bool:
    """The original poster or any staff member can resolve or re-open."""
    return actor.is_staff or actor.discord_id == opener_discord_id

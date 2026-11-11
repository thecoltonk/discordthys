from nephthys.core.permissions import Actor
from nephthys.database.tables import User


async def upsert_user(actor: Actor) -> User:
    """Insert or update the user, refreshing the cached username and staff flag."""
    rows = (
        await User.insert(
            User(
                discord_id=actor.discord_id,
                username=actor.username,
                helper=actor.is_staff,
            )
        )
        .on_conflict(
            target=User.discord_id,
            action="DO UPDATE",
            values=[User.username, User.helper],
        )
        .returning(*User.all_columns())
    )
    if not rows:
        raise ValueError(f"Failed to upsert user discord_id={actor.discord_id}")
    return User(**rows[0], _exists_in_db=True)


async def get_user(user_id: int) -> User | None:
    return await User.objects().where(User.id == user_id).first()

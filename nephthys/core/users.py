from nephthys.core.permissions import Actor
from nephthys.database.tables import User


async def upsert_user(actor: Actor) -> User:
    """Insert or update the user, refreshing the cached username and staff
    flag, and the avatar when the actor carries one (it's kept otherwise)."""
    refresh = [User.username, User.helper]
    if actor.avatar_key and actor.avatar_url:
        refresh += [User.avatar_key, User.avatar_url]
    rows = (
        await User.insert(
            User(
                discord_id=actor.discord_id,
                username=actor.username,
                helper=actor.is_staff,
                avatar_key=actor.avatar_key if actor.avatar_url else None,
                avatar_url=actor.avatar_url if actor.avatar_key else None,
            )
        )
        .on_conflict(
            target=User.discord_id,
            action="DO UPDATE",
            values=refresh,
        )
        .returning(*User.all_columns())
    )
    if not rows:
        raise ValueError(f"Failed to upsert user discord_id={actor.discord_id}")
    return User(**rows[0], _exists_in_db=True)


async def get_user(user_id: int) -> User | None:
    return await User.objects().where(User.id == user_id).first()

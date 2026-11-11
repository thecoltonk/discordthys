"""Persistence for AI answer attempts and their outcomes (no Discord imports)."""

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import UTC

from nephthys.database.tables import AISuggestion

SHADOW = "shadow"
PUBLIC = "public"

PENDING = "pending"
HELPFUL = "helpful"
NOT_HELPFUL = "not_helpful"
FIXED = "fixed"
NEEDS_HUMAN = "needs_human"

VALID_OUTCOMES = {
    SHADOW: {HELPFUL, NOT_HELPFUL},
    PUBLIC: {FIXED, NEEDS_HUMAN},
}


async def record_attempt(
    *,
    ticket_id: int,
    mode: str,
    model: str,
    answer: str | None,
    confidence: float | None,
    kb_sources: list[str],
    posted: bool,
    skip_reason: str | None,
) -> AISuggestion:
    suggestion = AISuggestion(
        ticket=ticket_id,
        mode=mode,
        model=model,
        answer=answer,
        confidence=confidence,
        kb_sources=kb_sources,
        posted=posted,
        skip_reason=skip_reason,
        outcome=PENDING,
    )
    await suggestion.save()
    return suggestion


async def set_message(suggestion_id: int, channel_id: int, message_id: int) -> None:
    await AISuggestion.update(
        {AISuggestion.channel_id: channel_id, AISuggestion.message_id: message_id}
    ).where(AISuggestion.id == suggestion_id)


async def get_suggestion(suggestion_id: int) -> AISuggestion | None:
    return await AISuggestion.objects().where(AISuggestion.id == suggestion_id).first()


async def latest_posted_for_ticket(ticket_id: int) -> AISuggestion | None:
    return (
        await AISuggestion.objects()
        .where((AISuggestion.ticket == ticket_id) & (AISuggestion.posted.eq(True)))
        .order_by(AISuggestion.id, ascending=False)
        .first()
    )


class OutcomeAlreadySetError(Exception):
    pass


async def set_outcome(
    suggestion_id: int, outcome: str, rated_by_user_id: int | None
) -> AISuggestion:
    """Record the first outcome for a posted suggestion. Later clicks are refused."""
    suggestion = await get_suggestion(suggestion_id)
    if suggestion is None:
        raise LookupError(f"AI suggestion {suggestion_id} not found")
    if outcome not in VALID_OUTCOMES.get(suggestion.mode, set()):
        raise ValueError(f"Outcome {outcome!r} isn't valid for {suggestion.mode} mode")
    rows = (
        await AISuggestion.update(
            {
                AISuggestion.outcome: outcome,
                AISuggestion.rated_by: rated_by_user_id,
                AISuggestion.rated_at: datetime.now(UTC),
            }
        )
        .where((AISuggestion.id == suggestion_id) & (AISuggestion.outcome == PENDING))
        .returning(AISuggestion.id)
    )
    if not rows:
        raise OutcomeAlreadySetError(suggestion_id)
    updated = await get_suggestion(suggestion_id)
    assert updated is not None
    return updated


@dataclass
class AIAccuracy:
    attempts: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    shadow_posted: int = 0
    helpful: int = 0
    not_helpful: int = 0
    shadow_unrated: int = 0
    public_posted: int = 0
    fixed: int = 0
    needs_human: int = 0
    public_no_response: int = 0

    @property
    def shadow_accuracy(self) -> float | None:
        rated = self.helpful + self.not_helpful
        return self.helpful / rated if rated else None

    @property
    def public_fix_rate(self) -> float | None:
        answered = self.fixed + self.needs_human
        return self.fixed / answered if answered else None


async def accuracy(since: datetime, until: datetime) -> AIAccuracy:
    rows = await AISuggestion.objects().where(
        (AISuggestion.created_at >= since) & (AISuggestion.created_at < until)
    )
    result = AIAccuracy(attempts=len(rows))
    for s in rows:
        if not s.posted:
            reason = s.skip_reason or "unknown"
            result.skipped[reason] = result.skipped.get(reason, 0) + 1
            continue
        if s.mode == SHADOW:
            result.shadow_posted += 1
            if s.outcome == HELPFUL:
                result.helpful += 1
            elif s.outcome == NOT_HELPFUL:
                result.not_helpful += 1
            else:
                result.shadow_unrated += 1
        else:
            result.public_posted += 1
            if s.outcome == FIXED:
                result.fixed += 1
            elif s.outcome == NEEDS_HUMAN:
                result.needs_human += 1
            else:
                result.public_no_response += 1
    return result

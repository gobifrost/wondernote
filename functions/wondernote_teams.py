"""Narrow Teams delivery boundary for WonderNote-owned notifications."""

from __future__ import annotations

from typing import Literal

from bifrost import UserError, context, workflow

from modules.microsoft_teams_bot import send_message


@workflow(
    name="wondernote_send_teams_message",
    description="Deliver one WonderNote reminder or digest to its authenticated owner.",
    category="WonderNote",
)
async def wondernote_send_teams_message(
    target_type: Literal["user"],
    user: str,
    message: str,
    summary: str | None = None,
    text_format: Literal["plain", "markdown", "xml"] | None = None,
) -> dict:
    """Send only to the current execution user's verified email address."""
    if target_type != "user":
        raise UserError("WonderNote Teams delivery supports user targets only.")
    recipient = str(user or "").strip()
    caller_email = str(context.email or "").strip()
    if not caller_email or recipient.casefold() != caller_email.casefold():
        raise UserError("WonderNote Teams delivery may only target the current user.")
    return await send_message(
        target_type=target_type,
        user=recipient,
        message=message,
        summary=summary,
        text_format=text_format,
    )

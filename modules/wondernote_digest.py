"""Pure helpers for WonderNote scheduled priority digests.

Digests stay domain-neutral: scope and grouping come from the user's own
spaces and canonical vocabulary, never from hardcoded CRM-style fields.
"""

from __future__ import annotations

import re
from html import escape
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SUGGESTED_DIGEST_SLOTS = ("morning", "evening")
VALID_DIGEST_SCOPES = {"personal", "shared", "all"}
VALID_DIGEST_KINDS = {"shared_space", "shared_item"}
VALID_DIGEST_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DIGEST_DAY_SHORTHANDS = {
    "daily": None,
    "weekdays": ["mon", "tue", "wed", "thu", "fri"],
    "weekends": ["sat", "sun"],
}


def normalize_days_of_week(value: Any) -> list[str] | None:
    """Normalize recurrence to sorted 3-letter day codes; None means daily."""
    if value is None:
        return None
    if isinstance(value, str):
        folded = value.strip().casefold()
        if folded in DIGEST_DAY_SHORTHANDS:
            result = DIGEST_DAY_SHORTHANDS[folded]
            if not result:
                return None
            order = {day: index for index, day in enumerate(VALID_DIGEST_DAYS)}
            return sorted(result, key=order.__getitem__)
        items = [part.strip() for part in re.split(r"[,\s]+", folded) if part.strip()]
    elif isinstance(value, (list, tuple)):
        items = [str(item).strip().casefold() for item in value]
    else:
        raise ValueError("days_of_week must be a list like [mon] or daily|weekdays|weekends")
    cleaned: list[str] = []
    for item in items:
        code = item[:3]
        if code not in VALID_DIGEST_DAYS:
            raise ValueError(f"unsupported day: {item}; use mon tue wed thu fri sat sun")
        if code not in cleaned:
            cleaned.append(code)
    if not cleaned:
        return None
    order = {day: index for index, day in enumerate(VALID_DIGEST_DAYS)}
    return sorted(cleaned, key=order.__getitem__)


def normalize_day_of_month(value: Any) -> list[int] | None:
    """Normalize to sorted month days 1-31; None means every day."""
    if value is None:
        return None
    if isinstance(value, (int, str)):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ValueError("day_of_month must be a day number 1-31 or a list of them")
    cleaned: list[int] = []
    for item in items:
        try:
            day = int(str(item).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported month day: {item}; use 1-31") from exc
        if not 1 <= day <= 31:
            raise ValueError(f"unsupported month day: {item}; use 1-31")
        if day not in cleaned:
            cleaned.append(day)
    return sorted(cleaned) or None


_CRON_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def normalize_cron_expression(value: Any) -> str | None:
    """Validate a 5-field cron expression structurally; None clears it."""
    if value is None:
        return None
    raw = str(value or "").strip()
    if not raw:
        return None
    fields = raw.split()
    if len(fields) != 5:
        raise ValueError("cron_expression must have 5 fields: minute hour day-of-month month day-of-week")
    for field, (low, high) in zip(fields, _CRON_FIELD_RANGES):
        if field == "*":
            continue
        for part in field.split(","):
            step = part.split("/", 1)
            base = step[0]
            if base != "*" and base:
                for bound in base.split("-", 1):
                    if not bound.isdigit() or not low <= int(bound) <= high:
                        raise ValueError(f"cron field out of range: {part}")
            if len(step) == 2 and (not step[1].isdigit() or int(step[1]) < 1):
                raise ValueError(f"cron step must be a positive integer: {part}")
    try:
        import croniter  # type: ignore

        croniter.croniter(raw)
    except ImportError:
        pass
    except Exception as exc:
        raise ValueError(f"invalid cron_expression: {exc}") from exc
    return raw


def cron_due(cron_expression: str, timezone_name: str, *, now: datetime | None = None, window_minutes: int = 15) -> bool:
    """True when the cron last fired inside the window, evaluated in-tz."""
    zone = ZoneInfo(normalize_timezone(timezone_name))
    current = (now or datetime.now(timezone.utc)).astimezone(zone)
    try:
        from croniter import croniter  # type: ignore
    except ImportError as exc:
        raise ValueError("cron evaluation requires croniter") from exc
    previous = croniter(cron_expression, current).get_prev(datetime)
    delta_minutes = (current - previous).total_seconds() / 60
    return 0 <= delta_minutes < window_minutes


def parse_local_time(value: str) -> tuple[int, int]:
    """Parse HH:MM 24h local time."""
    raw = str(value or "").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if not match:
        raise ValueError("local_time must be HH:MM 24h, e.g. 09:00")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not 0 <= hour <= 23 and 0 <= minute <= 59:
        raise ValueError("local_time must be HH:MM 24h, e.g. 09:00")
    return hour, minute


def normalize_timezone(value: str) -> str:
    """Validate an IANA timezone name and return it unchanged."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("timezone is required, e.g. America/New_York")
    try:
        ZoneInfo(raw)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {raw}") from exc
    return raw


def digest_due(
    local_time: str,
    timezone_name: str,
    *,
    now: datetime | None = None,
    window_minutes: int = 15,
    days_of_week: list[str] | None = None,
    day_of_month: list[int] | None = None,
    cron_expression: str | None = None,
) -> bool:
    """True when now falls inside the firing window for a pref.

    A set cron_expression is authoritative; otherwise the local time must
    match and every set date constraint (weekdays, month days) must match.
    """
    zone = ZoneInfo(normalize_timezone(timezone_name))
    current = (now or datetime.now(timezone.utc)).astimezone(zone)
    if cron_expression:
        return cron_due(cron_expression, timezone_name, now=now, window_minutes=window_minutes)
    hour, minute = parse_local_time(local_time)
    if days_of_week:
        today = VALID_DIGEST_DAYS[current.weekday()]
        if today not in {day.casefold() for day in days_of_week}:
            return False
    if day_of_month:
        if current.day not in {int(day) for day in day_of_month}:
            return False
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta_minutes = (current - target).total_seconds() / 60
    return 0 <= delta_minutes < window_minutes


def normalize_scope(scope: str | None) -> str:
    """Default personal; accept personal/shared/all."""
    raw = str(scope or "personal").strip().casefold()
    if raw not in VALID_DIGEST_SCOPES:
        raise ValueError(f"scope must be one of {sorted(VALID_DIGEST_SCOPES)}")
    return raw


def normalize_slot(slot: str) -> str:
    """Free-form schedule name such as morning, evening, or 7:30am standup."""
    raw = str(slot or "").strip()
    if not raw or len(raw) > 40 or not re.fullmatch(r"[A-Za-z0-9 _\-:.]+", raw):
        raise ValueError("slot must be a short name up to 40 chars, e.g. morning")
    return raw.casefold().strip()


def normalize_kinds(kinds: list[str] | None) -> list[str] | None:
    if kinds is None:
        return None
    cleaned = sorted({str(item).strip() for item in kinds if str(item).strip()})
    invalid = set(cleaned) - VALID_DIGEST_KINDS
    if invalid:
        raise ValueError(f"unsupported kinds: {sorted(invalid)}")
    return cleaned or None


def scope_allows_space(
    *,
    scope: str,
    kinds: list[str] | None,
    space: dict[str, Any],
    space_ids: list[str] | None,
    personal_id: str | None,
) -> bool:
    """Decide whether one accessible space belongs in this digest."""
    space_id = str(space.get("id") or "")
    if space_ids:
        return space_id in set(space_ids)
    kind = str(space.get("kind") or "")
    is_personal = bool(personal_id) and space_id == personal_id
    if scope == "personal":
        return is_personal
    if scope == "shared":
        if is_personal:
            return False
        return kind in (kinds or ["shared_space", "shared_item"])
    # scope == all
    if kinds and not is_personal and kind not in kinds:
        return False
    return True


def render_digest(
    *,
    slot: str,
    items: list[dict[str, Any]],
    inbox_count: int,
    custom_prompt: str | None = None,
    limit: int = 7,
) -> str:
    """Render the default deterministic Teams message."""
    label = str(slot or "digest").strip() or "digest"
    lines = [f"{label.title()} ({len(items)} items)"]
    for item in items[: max(1, limit)]:
        title = str(item.get("title") or "Untitled").strip()
        due = str(item.get("due_at") or "").strip()
        space_label = str((item.get("space") or {}).get("name") or "").strip()
        suffix = f" (due {due})" if due else ""
        prefix = f"[{space_label}] " if space_label else ""
        lines.append(f"- {prefix}{title}{suffix}")
    if inbox_count:
        lines.append(f"Inbox still needs triage: {inbox_count}")
    if not items and not inbox_count:
        lines.append("Nothing open. Enjoy the clear board.")
    return "\n".join(lines)


def render_custom_digest(*, slot: str, items: list[dict[str, Any]], plan: dict[str, Any], limit: int) -> str:
    """Validate a model's selections against the collected records and render Teams HTML."""
    available = {str(item.get("id")): item for item in items if item.get("id")}
    sections = plan.get("sections") if isinstance(plan, dict) else None
    if not isinstance(sections, list):
        raise ValueError("digest formatter returned no sections")
    lines = [f"<b>{escape(str(slot).replace('_', ' ').title())}</b>"]
    seen: set[str] = set()
    for section in sections[:8]:
        if not isinstance(section, dict) or not isinstance(section.get("items"), list):
            raise ValueError("digest formatter returned an invalid section")
        entries: list[str] = []
        for choice in section["items"]:
            if not isinstance(choice, dict):
                raise ValueError("digest formatter returned an invalid item")
            record_id = str(choice.get("id") or "")
            if record_id not in available or record_id in seen:
                raise ValueError("digest formatter selected an unknown or repeated item")
            priority = str(choice.get("priority") or "").lower()
            if priority not in {"red", "yellow", "green"}:
                raise ValueError("digest formatter returned an invalid priority")
            seen.add(record_id)
            indicator = {"red": "🔴", "yellow": "🟡", "green": "🟢"}[priority]
            title = escape(str(available[record_id].get("title") or "Untitled").strip())
            reason = escape(str(choice.get("reason") or "").strip()[:200])
            entries.append(f"{indicator} {title}" + (f" — {reason}" if reason else ""))
            if len(seen) >= max(1, limit):
                break
        if entries:
            heading = escape(str(section.get("heading") or "Priorities").strip()[:80])
            lines.append(f"<br/><br/><b>{heading}</b><br/><br/>" + "<br/><br/>".join(entries))
        if len(seen) >= max(1, limit):
            break
    if not seen:
        raise ValueError("digest formatter selected no items")
    return "".join(lines)

"""Pure domain rules for WonderNote's agent-only record model."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


VALID_RECORD_TYPES = {"note", "todo"}
VALID_STATES = {"active", "done", "archived"}
VALID_TRIAGE_STATES = {"inbox", "organized"}
VALID_CONCEPT_KINDS = {"property", "entity_type", "entity", "option"}
VALID_VALUE_TYPES = {"text", "number", "boolean", "date", "datetime", "entity", "option"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_future_datetime(value: str, *, now: datetime | None = None) -> datetime:
    """Parse an explicit, timezone-aware future datetime for deterministic dispatch."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("remind_at is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("remind_at must be an ISO 8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("remind_at must include a timezone offset")
    current = now or datetime.now(timezone.utc)
    if parsed.astimezone(timezone.utc) <= current.astimezone(timezone.utc):
        raise ValueError("remind_at must be in the future")
    return parsed


def normalize_name(value: str) -> str:
    """Normalize a human label for exact canonical/alias matching."""
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def derive_title(content: str, title: str | None = None) -> str:
    if title and title.strip():
        return title.strip()[:240]
    first = next((line.strip("# -*\t") for line in (content or "").splitlines() if line.strip()), "Untitled")
    return first[:240]


def normalize_metadata_items(metadata: Any) -> list[dict[str, Any]]:
    """Accept a friendly mapping or explicit metadata assignment list."""
    if metadata in (None, {}, []):
        return []
    if isinstance(metadata, dict):
        items = [{"property": key, "value": value} for key, value in metadata.items()]
    elif isinstance(metadata, list):
        items = metadata
    else:
        raise ValueError("metadata must be an object or a list of assignments")

    normalized: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict) or not str(raw.get("property") or "").strip():
            raise ValueError("each metadata assignment needs a property")
        item = dict(raw)
        item["property"] = str(item["property"]).strip()
        value = item.get("value")
        if isinstance(value, dict):
            nested = dict(value)
            item["value"] = nested.pop("value", nested.pop("name", None))
            for key in ("value_kind", "entity_type", "aliases", "value_type"):
                if key in nested and key not in item:
                    item[key] = nested[key]
        if item.get("value") is None:
            raise ValueError(f"metadata property '{item['property']}' needs a value")
        kind = str(item.get("value_kind") or "").strip().casefold()
        if not kind:
            kind = "entity" if item.get("entity_type") else "literal" if isinstance(item["value"], (bool, int, float)) else "option"
        if kind not in {"entity", "option", "literal"}:
            raise ValueError(f"unsupported value_kind: {kind}")
        item["value_kind"] = kind
        if kind == "entity" and not str(item.get("entity_type") or "").strip():
            raise ValueError(f"entity metadata '{item['property']}' needs entity_type")
        value_type = item.get("value_type")
        if value_type and str(value_type).casefold() not in VALID_VALUE_TYPES:
            raise ValueError(f"unsupported value_type: {value_type}")
        if value_type:
            item["value_type"] = str(value_type).casefold()
        normalized.append(item)
    return normalized


def metadata_value_type(item: dict[str, Any]) -> str:
    """Return WonderNote's stable value-type vocabulary for an assignment."""
    if item.get("value_type"):
        return str(item["value_type"]).casefold()
    kind = item["value_kind"]
    if kind in {"entity", "option"}:
        return kind
    value = item["value"]
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "text"


def state_patch(current: dict[str, Any], requested: str) -> dict[str, Any]:
    """Apply lifecycle timestamps consistently."""
    state = (requested or "").casefold().strip()
    if state not in VALID_STATES:
        raise ValueError(f"state must be one of {sorted(VALID_STATES)}")
    patch: dict[str, Any] = {"state": state}
    now = now_iso()
    if state == "done":
        patch["completed_at"] = current.get("completed_at") or now
        patch["archived_at"] = None
    elif state == "archived":
        patch["archived_at"] = current.get("archived_at") or now
    else:
        patch["completed_at"] = None
        patch["archived_at"] = None
    return patch


REVISION_FIELDS = (
    "title",
    "content",
    "record_type",
    "state",
    "triage_state",
    "due_at",
    "parent_id",
    "space_id",
    "metadata_snapshot",
)


def metadata_pairs(snapshot: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Metadata as property/value pairs, ignoring assignment row ids."""
    return [
        {
            "property": item.get("property_name") or item.get("property"),
            "value": item.get("display_value") if item.get("display_value") is not None else item.get("value"),
        }
        for item in snapshot or []
    ]


def revision_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    return {field: record.get(field) for field in REVISION_FIELDS}


def revision_changes(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for field in REVISION_FIELDS:
        old, new = before.get(field), after.get(field)
        if field == "metadata_snapshot":
            old, new = metadata_pairs(old), metadata_pairs(new)
            if sorted(map(repr, old)) == sorted(map(repr, new)):
                continue
        elif old == new:
            continue
        changes.append({"field": field, "before": old, "after": new})
    return changes


def check_expected_revision(current: int, expected: int | None) -> None:
    if expected is not None and int(expected) != int(current):
        raise ValueError(
            f"revision conflict: expected revision {expected}, but the current revision is {current}; "
            "get the record again and reapply the change"
        )


def snapshot_matches(snapshot: list[dict[str, Any]], wanted: dict[str, Any] | None) -> bool:
    if not wanted:
        return True
    available: dict[str, list[str]] = {}
    for item in snapshot or []:
        key = normalize_name(str(item.get("property_name") or item.get("property") or ""))
        available.setdefault(key, []).append(normalize_name(str(item.get("display_value") or item.get("value") or "")))
    for key, value in wanted.items():
        expected = value if isinstance(value, list) else [value]
        actual = available.get(normalize_name(str(key)), [])
        if not any(normalize_name(str(candidate)) in actual for candidate in expected):
            return False
    return True


def halo_priority(status: str) -> str:
    """Conservative, editable starting priority for imported stewardship work."""
    normalized = normalize_name(status)
    if normalized in {"in progress", "follow up"}:
        return "high"
    if normalized in {"scheduled", "with 3rd party"}:
        return "low"
    return "normal"


def legacy_record(artifact: dict[str, Any]) -> dict[str, Any]:
    details = artifact.get("details") or {}
    old_type = str(artifact.get("type") or "note")
    is_todo = old_type == "reminder"
    done = bool(details.get("done_at")) or details.get("status") == "done"
    return {
        "record_type": "todo" if is_todo else "note",
        "title": derive_title(str(artifact.get("body") or ""), artifact.get("title")),
        "content": str(artifact.get("body") or ""),
        "state": "done" if done else "active",
        "triage_state": "inbox" if artifact.get("last_clarified_at") is None else "organized",
        "due_at": details.get("due_at"),
        "completed_at": details.get("done_at") if done else None,
        "archived_at": None,
        "parent_id": artifact.get("parent_id"),
        "source_key": f"wondernote:artifact:{artifact['id']}",
        "source": {"system": "wondernote-v1", "artifact_id": artifact["id"], "created_at": artifact.get("created_at")},
    }


def halo_record(ticket: dict[str, Any]) -> dict[str, Any]:
    closed = bool(str(ticket.get("closed_at") or "").strip())
    ticket_id = int(ticket["ticket_id"])
    summary = str(ticket.get("details") or f"Halo ticket {ticket_id}").strip()
    return {
        "record_type": "todo",
        "title": summary[:240],
        "content": f"HaloPSA Account Stewardship ticket #{ticket_id}: {summary}",
        "state": "done" if closed else "active",
        "triage_state": "organized",
        "due_at": None,
        "completed_at": ticket.get("closed_at") or None,
        "archived_at": None,
        "parent_id": None,
        "source_key": f"halopsa:ticket:{ticket_id}",
        "source": {"system": "halopsa", "ticket_id": ticket_id, "ticket_type": "Account Stewardship", "created_at": ticket.get("created_at")},
    }

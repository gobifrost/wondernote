"""Pure domain rules for WonderNote spaces and sharing."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


VALID_SPACE_KINDS = {"personal", "shared_space", "shared_item"}
VALID_PERMISSION_KINDS = {"read", "write"}
VALID_PRINCIPAL_KINDS = {"user", "organization"}
EFFECTIVE_PERMISSIONS = {"none", "read", "write", "owner"}

_PERMISSION_RANK = {"none": 0, "read": 1, "write": 2, "owner": 3}


def _string(value: Any) -> str:
    return str(value or "").strip()


def _casefold(value: Any) -> str:
    return _string(value).casefold()


def _read_field(item: Any, *names: str) -> Any:
    if isinstance(item, dict):
        for name in names:
            if name in item:
                return item[name]
        return None
    for name in names:
        if hasattr(item, name):
            return getattr(item, name)
    return None


def normalize_space_kind(value: Any) -> str:
    kind = _casefold(value)
    if kind not in VALID_SPACE_KINDS:
        raise ValueError(f"space kind must be one of {sorted(VALID_SPACE_KINDS)}")
    return kind


def validate_space_kind(value: Any) -> str:
    return normalize_space_kind(value)


def normalize_principal_kind(value: Any) -> str:
    kind = _casefold(value)
    if kind not in VALID_PRINCIPAL_KINDS:
        raise ValueError(f"principal kind must be one of {sorted(VALID_PRINCIPAL_KINDS)}")
    return kind


def validate_principal_kind(value: Any) -> str:
    return normalize_principal_kind(value)


def normalize_permission(value: Any) -> str:
    permission = _casefold(value)
    if permission not in VALID_PERMISSION_KINDS:
        raise ValueError(f"permission must be one of {sorted(VALID_PERMISSION_KINDS)}")
    return permission


def _normalize_effective_permission(value: Any) -> str:
    permission = _casefold(value)
    if permission not in EFFECTIVE_PERMISSIONS:
        raise ValueError(f"permission must be one of {sorted(EFFECTIVE_PERMISSIONS)}")
    return permission


def _permission_value(permission: Any) -> int:
    return _PERMISSION_RANK[_normalize_effective_permission(permission)]


def _grant_matches(grant: Any, *, current_user_id: Any, org_id: Any) -> bool:
    state = _casefold(_read_field(grant, "state"))
    if state and state != "active":
        return False
    principal_kind = normalize_principal_kind(
        _read_field(grant, "principal_type", "principal_kind", "kind", "recipient_kind", "subject_kind")
    )
    principal_id = _string(_read_field(grant, "principal_id", "recipient_id", "subject_id", "user_id", "organization_id"))
    if principal_kind == "user":
        return principal_id == _string(current_user_id)
    return principal_id == _string(org_id)


def _grant_permission(grant: Any) -> str:
    permission = _read_field(grant, "permission", "access", "level")
    return normalize_permission(permission)


def effective_permission(
    owner_id: Any,
    current_user_id: Any,
    grants: Iterable[Any] | None,
    org_id: Any,
) -> str:
    if _string(owner_id) and _string(current_user_id) and _string(owner_id) == _string(current_user_id):
        return "owner"

    best = "none"
    for grant in grants or []:
        if not _grant_matches(grant, current_user_id=current_user_id, org_id=org_id):
            continue
        permission = _grant_permission(grant)
        if _permission_value(permission) > _permission_value(best):
            best = permission
            if best == "write":
                continue
    return best


def resolve_recipient(recipient: Any, users: Iterable[Any] | None) -> Any:
    raw = _string(recipient)
    if not raw:
        raise ValueError("recipient is required")
    folded = raw.casefold()

    matches: list[Any] = []
    for user in users or []:
        user_id = _string(_read_field(user, "id", "uuid", "user_id"))
        email = _casefold(_read_field(user, "email"))
        names = {
            _casefold(_read_field(user, "name")),
            _casefold(_read_field(user, "full_name")),
            _casefold(_read_field(user, "display_name")),
        }
        if folded == _casefold(user_id) or folded == email or folded in names:
            matches.append(user)

    if not matches:
        raise ValueError(f"no recipient matches: {raw}")
    if len(matches) > 1:
        raise ValueError(f"recipient is ambiguous: {raw}")
    return matches[0]


def _space_field(space: Any, *names: str) -> Any:
    value = _read_field(space, *names)
    return value


def space_descriptor(
    space: Any,
    current_user_id: Any,
    grants: Iterable[Any] | None,
    org_id: Any,
) -> dict[str, Any]:
    kind = normalize_space_kind(_space_field(space, "kind", "space_kind"))
    owner_id = _string(_space_field(space, "owner_id", "owner"))
    descriptor = {
        "id": _string(_space_field(space, "id", "space_id")),
        "name": _string(_space_field(space, "name", "title")),
        "kind": kind,
        "owner_id": owner_id,
        "relationship": "personal" if kind == "personal" else "owned" if owner_id == _string(current_user_id) else "shared_with_me",
        "permission": effective_permission(owner_id, current_user_id, grants, org_id),
    }
    description = _string(_space_field(space, "description"))
    if description:
        descriptor["description"] = description
    return descriptor

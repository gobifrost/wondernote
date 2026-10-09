from types import SimpleNamespace

import pytest

from modules.wondernote_spaces import (
    EFFECTIVE_PERMISSIONS,
    VALID_PERMISSION_KINDS,
    VALID_PRINCIPAL_KINDS,
    VALID_SPACE_KINDS,
    effective_permission,
    normalize_permission,
    resolve_recipient,
    space_descriptor,
    validate_principal_kind,
    validate_space_kind,
)


def test_space_permission_and_kind_constants_are_bounded():
    assert VALID_SPACE_KINDS == {"personal", "shared_space", "shared_item"}
    assert VALID_PERMISSION_KINDS == {"read", "write"}
    assert VALID_PRINCIPAL_KINDS == {"user", "organization"}
    assert EFFECTIVE_PERMISSIONS == {"none", "read", "write", "owner"}


def test_validation_normalizes_and_rejects_invalid_permission():
    assert normalize_permission(" READ ") == "read"
    with pytest.raises(ValueError, match="permission"):
        normalize_permission("admin")

    assert validate_space_kind(" Shared_Item ") == "shared_item"
    assert validate_principal_kind(" Organization ") == "organization"


def test_effective_permission_prefers_owner_then_write_then_read_then_none():
    grants = [
        {"principal_type": "user", "principal_id": "u-2", "permission": "read"},
        {"principal_type": "organization", "principal_id": "org-1", "permission": "write"},
    ]

    assert effective_permission("u-1", "u-1", grants, "org-1") == "owner"
    assert effective_permission("u-1", "u-2", grants, "org-1") == "write"
    assert effective_permission("u-1", "u-3", grants, "org-1") == "write"
    assert effective_permission("u-1", "u-3", [], "org-1") == "none"


def test_effective_permission_combines_user_and_org_grants():
    grants = [
        SimpleNamespace(principal_kind="organization", principal_id="org-7", permission="read"),
        SimpleNamespace(kind="user", recipient_id="u-7", access="write"),
    ]

    assert effective_permission("owner-1", "u-7", grants, "org-7") == "write"


def test_effective_permission_ignores_revoked_grants():
    grants = [
        {"principal_type": "user", "principal_id": "u-7", "permission": "write", "state": "revoked"},
        {"principal_type": "organization", "principal_id": "org-7", "permission": "read", "state": "active"},
    ]

    assert effective_permission("owner-1", "u-7", grants, "org-7") == "read"


def test_resolve_recipient_supports_uuid_email_and_exact_case_insensitive_name():
    users = [
        {"id": "550e8400-e29b-41d4-a716-446655440000", "email": "alice@example.com", "name": "Alice Example"},
        SimpleNamespace(uuid="550e8400-e29b-41d4-a716-446655440001", email="bob@example.com", display_name="Bob Example"),
    ]

    assert resolve_recipient("550E8400-E29B-41D4-A716-446655440000", users)["name"] == "Alice Example"
    assert resolve_recipient("ALICE@EXAMPLE.COM", users)["email"] == "alice@example.com"
    assert resolve_recipient("bob example", users).display_name == "Bob Example"


def test_resolve_recipient_raises_for_missing_or_ambiguous_matches():
    users = [
        {"id": "u-1", "email": "shared@example.com", "name": "Shared Name"},
        {"id": "u-2", "email": "other@example.com", "name": "Shared Name"},
    ]

    with pytest.raises(ValueError, match="no recipient"):
        resolve_recipient("absent@example.com", users)

    with pytest.raises(ValueError, match="ambiguous"):
        resolve_recipient("Shared Name", users)


def test_space_descriptor_reports_relationship_and_permission():
    grants = [
        {"principal_type": "organization", "principal_id": "org-9", "permission": "read"},
        {"principal_type": "user", "principal_id": "user-9", "permission": "write"},
    ]

    personal = space_descriptor(
        {"id": "space-1", "name": "Private", "kind": "personal", "owner_id": "user-9"},
        "user-9",
        grants,
        "org-9",
    )
    owned = space_descriptor(
        {"id": "space-2", "name": "Team Notes", "kind": "shared_space", "owner_id": "user-9"},
        "user-9",
        grants,
        "org-9",
    )
    shared = space_descriptor(
        {"id": "space-3", "name": "Client Notes", "kind": "shared_item", "owner_id": "user-1"},
        "user-9",
        grants,
        "org-9",
    )

    assert personal == {
        "id": "space-1",
        "name": "Private",
        "kind": "personal",
        "owner_id": "user-9",
        "relationship": "personal",
        "permission": "owner",
    }
    assert owned["relationship"] == "owned"
    assert owned["permission"] == "owner"
    assert shared["relationship"] == "shared_with_me"
    assert shared["permission"] == "write"

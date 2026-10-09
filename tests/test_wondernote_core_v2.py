from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import functions.wondernote as wondernote
from modules.wondernote_core import (
    derive_title,
    halo_priority,
    halo_record,
    legacy_record,
    metadata_value_type,
    normalize_metadata_items,
    normalize_name,
    parse_future_datetime,
    snapshot_matches,
    state_patch,
)


def test_parse_future_datetime_requires_timezone_and_future():
    now = datetime(2026, 9, 1, 16, 0, tzinfo=timezone.utc)
    parsed = parse_future_datetime("2026-09-04T12:00:00-04:00", now=now)
    assert parsed.isoformat() == "2026-09-04T12:00:00-04:00"

    with pytest.raises(ValueError, match="timezone"):
        parse_future_datetime("2026-09-04T12:00:00", now=now)
    with pytest.raises(ValueError, match="future"):
        parse_future_datetime("2026-09-01T11:59:00-04:00", now=now)


def test_normalize_name_collapses_company_punctuation():
    assert normalize_name("Big Brothers & Big Sisters") == "big brothers big sisters"


def test_metadata_mapping_and_entity_shape():
    assert normalize_metadata_items({"priority": "High"})[0]["value_kind"] == "option"
    item = normalize_metadata_items([
        {"property": "organization", "value": {"name": "BBBS", "value_kind": "entity", "entity_type": "organization"}}
    ])[0]
    assert item == {"property": "organization", "value": "BBBS", "value_kind": "entity", "entity_type": "organization"}


def test_literal_metadata_uses_stable_value_type_vocabulary():
    assert metadata_value_type(normalize_metadata_items({"important": True})[0]) == "boolean"
    assert metadata_value_type(normalize_metadata_items({"score": 3.5})[0]) == "number"
    assert metadata_value_type({"property": "when", "value": "2026-09-01", "value_kind": "literal", "value_type": "date"}) == "date"


def test_lifecycle_state_timestamps_are_consistent():
    done = state_patch({}, "done")
    assert done["state"] == "done" and done["completed_at"]
    reopened = state_patch(done, "active")
    assert reopened["completed_at"] is None and reopened["archived_at"] is None


def test_snapshot_filter_is_exact_after_normalization():
    snapshot = [{"property": "Organization", "display_value": "Big Brothers Big Sisters"}]
    assert snapshot_matches(snapshot, {"organization": "Big Brothers Big Sisters"})
    assert not snapshot_matches(snapshot, {"organization": "Big Brothers"})


def test_snapshot_filter_accepts_durable_property_name():
    snapshot = [{"property_name": "Organization", "display_value": "Big Brothers Big Sisters"}]
    assert snapshot_matches(snapshot, {"organization": "Big Brothers Big Sisters"})


def test_legacy_mapping_preserves_idempotent_source_and_done_state():
    record = legacy_record({"id": "a", "type": "reminder", "body": "Call Sam", "details": {"done_at": "2026-01-01"}})
    assert record["record_type"] == "todo"
    assert record["state"] == "done"
    assert record["source_key"] == "wondernote:artifact:a"


def test_halo_mapping_and_priority_are_deterministic():
    record = halo_record({"ticket_id": 42, "details": "Discuss renewal", "closed_at": "", "created_at": "2026-01-01"})
    assert record["state"] == "active"
    assert record["source_key"] == "halopsa:ticket:42"
    assert halo_priority("In Progress") == "high"
    assert halo_priority("Scheduled") == "low"


def test_closed_halo_ticket_is_done_but_not_archived():
    record = halo_record({"ticket_id": 43, "details": "Finished follow-up", "closed_at": "2026-08-01", "created_at": "2026-07-01"})
    assert record["state"] == "done"
    assert record["completed_at"] == "2026-08-01"
    assert record["archived_at"] is None


def test_title_derives_from_first_nonempty_line():
    assert derive_title("\n# A useful heading\nBody") == "A useful heading"


@pytest.mark.asyncio
async def test_metadata_replacement_never_deletes_old_rows_before_commit(monkeypatch):
    class Row:
        def __init__(self, row_id):
            self.id = row_id

    class Result:
        documents = [Row("old-1"), Row("old-2")]

    class FakeTables:
        def __init__(self):
            self.inserts = 0
            self.deleted = []
            self.updated = False

        async def query(self, *_args, **_kwargs):
            return Result()

        async def insert(self, *_args, **_kwargs):
            self.inserts += 1
            if self.inserts == 2:
                raise RuntimeError("injected insert failure")
            return Row("new-1")

        async def delete_batch(self, _table, ids):
            self.deleted.append(ids)

        async def update(self, *_args, **_kwargs):
            self.updated = True

    fake = FakeTables()

    async def assignment(_owner, _org, space_id, record_id, raw):
        return {
            "owner_id": "owner",
            "organization_id": "org",
            "space_id": space_id,
            "record_id": record_id,
            "property_id": raw["property"],
            "property_name": raw["property"],
            "concept_id": None,
            "value_kind": "literal",
            "value": raw["value"],
            "display_value": str(raw["value"]),
        }

    monkeypatch.setattr(wondernote, "tables", fake)
    monkeypatch.setattr(wondernote, "_assignment", assignment)

    with pytest.raises(RuntimeError, match="injected"):
        await wondernote._replace_metadata(
            "owner",
            "org",
            "space",
            "record",
            [
                {"property": "one", "value": 1},
                {"property": "two", "value": 2},
            ],
        )

    assert fake.deleted == [["new-1"]]
    assert not fake.updated


@pytest.mark.asyncio
async def test_inspect_only_resolution_does_not_append_aliases(monkeypatch):
    concept = {
        "id": "concept-1",
        "canonical_name": "Big Brothers Big Sisters",
        "normalized_name": "big brothers big sisters",
        "aliases": ["BBBS"],
    }
    writes = []

    async def candidates(*_args, **_kwargs):
        return [concept]

    class FakeTables:
        async def update(self, *args, **kwargs):
            writes.append((args, kwargs))

    monkeypatch.setattr(wondernote, "_concept_candidates", candidates)
    monkeypatch.setattr(wondernote, "tables", FakeTables())
    match, created = await wondernote._resolve_concept(
        "owner",
        "org",
        "space",
        kind="entity",
        name="BBBS",
        aliases=["Big Brothers"],
        create=False,
        update_aliases=False,
    )

    assert match == concept
    assert created is False
    assert writes == []


@pytest.mark.asyncio
async def test_metadata_and_record_patch_share_one_commit_point(monkeypatch):
    class Row:
        def __init__(self, row_id):
            self.id = row_id

    class Result:
        documents = [Row("old-1")]

    class FakeTables:
        def __init__(self):
            self.deleted = []
            self.patch = None

        async def query(self, *_args, **_kwargs):
            return Result()

        async def insert(self, *_args, **_kwargs):
            return Row("new-1")

        async def update(self, _table, _record_id, patch):
            self.patch = patch

        async def delete_batch(self, _table, ids):
            self.deleted.append(ids)

    fake = FakeTables()

    async def assignment(_owner, _org, space_id, record_id, raw):
        return {
            "owner_id": "owner",
            "organization_id": "org",
            "space_id": space_id,
            "record_id": record_id,
            "property_id": "property-1",
            "property_name": raw["property"],
            "concept_id": None,
            "value_kind": "literal",
            "value": raw["value"],
            "display_value": str(raw["value"]),
        }

    monkeypatch.setattr(wondernote, "tables", fake)
    monkeypatch.setattr(wondernote, "_assignment", assignment)
    await wondernote._replace_metadata(
        "owner",
        "org",
        "space",
        "record",
        [{"property": "priority", "value": 1}],
        record_patch={"state": "done"},
    )

    assert fake.patch["state"] == "done"
    assert fake.patch["metadata_snapshot"][0]["id"] == "new-1"
    assert fake.deleted == [["old-1"]]


@pytest.mark.asyncio
async def test_concept_candidates_page_past_500(monkeypatch):
    class Row:
        def __init__(self, row_id):
            self.id = row_id
            self.data = {"kind": "option", "state": "active", "owner_id": "owner"}

    class Result:
        def __init__(self, documents):
            self.documents = documents

    offsets = []

    class FakeTables:
        async def query(self, *_args, **kwargs):
            offsets.append(kwargs["offset"])
            return Result([Row(str(index)) for index in range(500)]) if kwargs["offset"] == 0 else Result([Row("500")])

    monkeypatch.setattr(wondernote, "tables", FakeTables())
    rows = await wondernote._concept_candidates("owner", kind="option")
    assert len(rows) == 501
    assert offsets == [0, 500]


@pytest.mark.asyncio
async def test_save_rejects_creating_a_sibling_in_direct_share(monkeypatch):
    async def shared_item(*_args, **_kwargs):
        return (
            {"id": "direct-1", "kind": "shared_item", "owner_id": "owner", "organization_id": "org"},
            {"id": "direct-1", "kind": "shared_item", "permission": "write"},
        )

    monkeypatch.setattr(wondernote, "_identity", lambda: ("writer", "org"))
    monkeypatch.setattr(wondernote, "_space_for_access", shared_item)

    with pytest.raises(Exception, match="exactly one record"):
        await wondernote.wondernote_save("note", "Sibling", space_id="direct-1")


def test_effective_access_surfaces_broader_organization_grant():
    space = {"owner_id": "owner", "organization_id": "org"}
    principals = [
        {"principal_type": "user", "principal_id": "user-1", "principal_label": "One"},
    ]
    grants = [
        {"principal_type": "user", "principal_id": "user-1", "permission": "read", "state": "active"},
        {"principal_type": "organization", "principal_id": "org", "permission": "write", "state": "active"},
    ]

    assert wondernote._effective_access_views(space, principals, grants)[0]["permission"] == "write"


@pytest.mark.asyncio
async def test_unshare_recognizes_a_deleted_user_from_the_durable_grant(monkeypatch):
    class FakeUsers:
        async def list(self, **_kwargs):
            return []

    monkeypatch.setattr(wondernote, "_identity", lambda: ("owner", "org"))
    monkeypatch.setattr(wondernote, "users", FakeUsers())
    grants = [{
        "principal_type": "user",
        "principal_id": "deleted-user-id",
        "principal_label": "Deleted User <deleted@example.com>",
        "permission": "read",
        "state": "active",
    }]

    assert await wondernote._recipient_keys_for_unshare(["deleted@example.com"], grants) == {
        ("user", "deleted-user-id")
    }


@pytest.mark.asyncio
async def test_delivery_confirms_downstream_teams_success(monkeypatch):
    class Row:
        def __init__(self, row_id, data):
            self.id = row_id
            self.data = data

    reminder = Row("reminder-1", {
        "owner_id": "owner",
        "organization_id": "org",
        "record_id": "record-1",
        "state": "scheduled",
        "recipient": "person@example.com",
        "message": "Check this",
    })
    record = Row("record-1", {
        "owner_id": "owner",
        "organization_id": "org",
        "state": "active",
        "title": "Check this",
        "content": "Check this",
    })

    class FakeTables:
        def __init__(self):
            self.updates = []

        async def get(self, table, _row_id):
            return reminder if table == wondernote.REMINDERS else record

        async def update(self, _table, _row_id, patch):
            self.updates.append(patch)

    class FakeWorkflows:
        async def execute(self, *_args, **_kwargs):
            return "teams-execution-1"

        async def get(self, _execution_id):
            return SimpleNamespace(status="Success", result={"success": True})

    fake_tables = FakeTables()
    monkeypatch.setattr(wondernote, "context", SimpleNamespace(user_id="owner", org_id="org"))
    monkeypatch.setattr(wondernote, "tables", fake_tables)
    monkeypatch.setattr(wondernote, "workflows", FakeWorkflows())

    result = await wondernote.wondernote_deliver_reminder("reminder-1")

    assert result == {"state": "sent", "delivery_execution_id": "teams-execution-1"}
    assert fake_tables.updates[-1]["state"] == "sent"
    assert fake_tables.updates[-1]["sent_at"]

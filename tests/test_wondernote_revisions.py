from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import functions.wondernote as wondernote
from modules.wondernote_core import check_expected_revision, revision_changes, revision_snapshot


ROOT = Path(__file__).resolve().parents[1]


def _record(**overrides):
    record = {
        "id": "rec-1",
        "owner_id": "owner",
        "organization_id": "org",
        "space_id": "space-1",
        "record_type": "note",
        "title": "Onboarding",
        "content": "Step one",
        "state": "active",
        "triage_state": "organized",
        "due_at": None,
        "parent_id": None,
        "metadata_snapshot": [{"property_name": "doc_type", "display_value": "how-to"}],
        "revision": 2,
        "last_edited_by": "owner",
        "updated_at_user": "2026-09-28T12:00:00+00:00",
        "created_at": "2026-09-01T12:00:00+00:00",
    }
    record.update(overrides)
    return record


def test_revision_snapshot_keeps_only_versioned_fields():
    snapshot = revision_snapshot(_record(source_key="x", owner_id="owner"))
    assert snapshot["title"] == "Onboarding"
    assert snapshot["metadata_snapshot"] == [{"property_name": "doc_type", "display_value": "how-to"}]
    assert "source_key" not in snapshot and "owner_id" not in snapshot and "revision" not in snapshot


def test_revision_changes_reports_only_changed_fields():
    before = revision_snapshot(_record())
    after = revision_snapshot(_record(content="Step two", state="archived"))
    assert revision_changes(before, after) == [
        {"field": "content", "before": "Step one", "after": "Step two"},
        {"field": "state", "before": "active", "after": "archived"},
    ]
    assert revision_changes(before, before) == []


def test_revision_changes_compares_metadata_by_display_value_not_row_ids():
    before = revision_snapshot(_record(metadata_snapshot=[{"id": "a", "property_name": "doc_type", "display_value": "how-to"}]))
    same = revision_snapshot(_record(metadata_snapshot=[{"id": "b", "property_name": "doc_type", "display_value": "how-to"}]))
    assert revision_changes(before, same) == []


def test_expected_revision_conflict_names_current_revision():
    check_expected_revision(3, None)
    check_expected_revision(3, 3)
    with pytest.raises(ValueError, match="current revision is 3"):
        check_expected_revision(3, 2)


class Row:
    def __init__(self, row_id, data):
        self.id = row_id
        self.data = data


class FakeTables:
    def __init__(self, record, revisions=None):
        self.record = dict(record)
        self.revisions = {row["id"]: dict(row) for row in (revisions or [])}
        self.inserted = []
        self.updates = []
        self.deleted = []
        self.fail_update = False

    async def get(self, table, row_id):
        if table == wondernote.RECORDS:
            return Row(self.record["id"], self.record)
        raise AssertionError(table)

    async def insert(self, table, data):
        assert table == wondernote.REVISIONS
        row_id = f"rev-row-{len(self.inserted) + 1}"
        self.inserted.append(data)
        self.revisions[row_id] = {"id": row_id, **data}
        return Row(row_id, data)

    async def update(self, table, row_id, patch):
        if self.fail_update and table == wondernote.RECORDS:
            raise RuntimeError("injected update failure")
        self.updates.append((table, row_id, patch))
        if table == wondernote.RECORDS:
            self.record.update(patch)
        else:
            self.revisions[row_id].update(patch)

    async def delete(self, table, row_id):
        self.deleted.append((table, row_id))
        self.revisions.pop(row_id, None)

    async def query(self, table, where=None, order_by=None, order_dir="asc", limit=100, offset=0):
        assert table == wondernote.REVISIONS
        rows = [row for row in self.revisions.values() if all(row.get(key) == value for key, value in (where or {}).items())]
        rows.sort(key=lambda row: row["revision"], reverse=order_dir == "desc")
        return SimpleNamespace(documents=[Row(row["id"], row) for row in rows[offset: offset + limit]])


@pytest.fixture
def writable(monkeypatch):
    async def space_for_access(space_id, *, require_write=False):
        return (
            {"id": space_id, "owner_id": "owner", "organization_id": "org", "kind": "named"},
            {"id": space_id, "permission": "write"},
        )

    async def noop(*_args, **_kwargs):
        return {}

    async def indexed(_record):
        return True

    async def no_reminders(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(wondernote, "_identity", lambda: ("editor", "org"))
    monkeypatch.setattr(wondernote, "_ensure_personal_space", noop)
    monkeypatch.setattr(wondernote, "_space_for_access", space_for_access)
    monkeypatch.setattr(wondernote, "_index", indexed)
    monkeypatch.setattr(wondernote, "_cancel_open_reminders", no_reminders)


@pytest.mark.asyncio
async def test_update_snapshots_prior_version_and_bumps_revision(monkeypatch, writable):
    fake = FakeTables(_record())
    monkeypatch.setattr(wondernote, "tables", fake)

    result = await wondernote.wondernote_update("rec-1", {"content": "Step two"})

    assert len(fake.inserted) == 1
    saved = fake.inserted[0]
    assert saved["record_id"] == "rec-1"
    assert saved["revision"] == 2
    assert saved["space_id"] == "space-1"
    assert saved["snapshot"]["content"] == "Step one"
    assert saved["edited_by"] == "owner"
    assert saved["replaced_by"] == "editor"
    assert result["record"]["revision"] == 3
    assert result["record"]["last_edited_by"] == "editor"
    assert result["changes"] == [{"field": "content", "before": "Step one", "after": "Step two"}]


@pytest.mark.asyncio
async def test_update_without_effective_change_does_not_create_revision(monkeypatch, writable):
    fake = FakeTables(_record())
    monkeypatch.setattr(wondernote, "tables", fake)

    result = await wondernote.wondernote_update("rec-1", {"content": "Step one"})

    assert fake.inserted == []
    assert result["record"]["revision"] == 2
    assert result["changes"] == []


@pytest.mark.asyncio
async def test_update_rejects_stale_expected_revision_without_writing(monkeypatch, writable):
    fake = FakeTables(_record())
    monkeypatch.setattr(wondernote, "tables", fake)

    with pytest.raises(Exception, match="current revision is 2"):
        await wondernote.wondernote_update("rec-1", {"content": "Mine"}, expected_revision=1)

    assert fake.inserted == [] and fake.updates == []


@pytest.mark.asyncio
async def test_preview_returns_diff_without_writing(monkeypatch, writable):
    fake = FakeTables(_record())
    monkeypatch.setattr(wondernote, "tables", fake)

    result = await wondernote.wondernote_update(
        "rec-1",
        {"state": "archived"},
        metadata={"superseded_by": "rec-2"},
        preview=True,
    )

    assert fake.inserted == [] and fake.updates == []
    assert result["preview"] is True
    assert result["revision"] == 2
    assert [change["field"] for change in result["changes"]] == ["state"]
    assert result["metadata"]["current"] == [{"property": "doc_type", "value": "how-to"}]
    assert result["metadata"]["proposed"][0]["property"] == "superseded_by"


@pytest.mark.asyncio
async def test_failed_update_removes_orphaned_revision(monkeypatch, writable):
    fake = FakeTables(_record())
    fake.fail_update = True
    monkeypatch.setattr(wondernote, "tables", fake)

    with pytest.raises(RuntimeError, match="injected"):
        await wondernote.wondernote_update("rec-1", {"content": "Step two"})

    assert fake.deleted == [(wondernote.REVISIONS, "rev-row-1")]


def _revision_row(row_id, revision, content):
    return {
        "id": row_id,
        "record_id": "rec-1",
        "space_id": "space-1",
        "revision": revision,
        "snapshot": revision_snapshot(_record(content=content)),
        "edited_by": "owner",
        "edited_at": f"2026-09-2{revision}T12:00:00+00:00",
        "replaced_by": "editor",
        "replaced_at": f"2026-09-2{revision + 1}T12:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_get_history_lists_revisions_newest_first_with_change_summary(monkeypatch, writable):
    fake = FakeTables(_record(content="Step three"), [
        _revision_row("r0", 0, "Step one"),
        _revision_row("r1", 1, "Step two"),
    ])
    monkeypatch.setattr(wondernote, "tables", fake)
    monkeypatch.setattr(wondernote, "_visible_document", fake.get)

    result = await wondernote.wondernote_get("rec-1", include_history=True)

    history = result["history"]
    assert [item["revision"] for item in history] == [2, 1, 0]
    assert history[0]["current"] is True
    assert history[1]["changed_fields"] == ["content"]
    assert "snapshot" not in history[1]


@pytest.mark.asyncio
async def test_get_specific_revision_returns_that_version(monkeypatch, writable):
    fake = FakeTables(_record(content="Step three"), [_revision_row("r0", 0, "Step one")])
    monkeypatch.setattr(wondernote, "tables", fake)
    monkeypatch.setattr(wondernote, "_visible_document", fake.get)

    result = await wondernote.wondernote_get("rec-1", revision=0)
    assert result["revision"]["revision"] == 0
    assert result["revision"]["snapshot"]["content"] == "Step one"
    assert result["record"]["content"] == "Step three"

    with pytest.raises(Exception, match="revision 7 not found"):
        await wondernote.wondernote_get("rec-1", revision=7)


@pytest.mark.asyncio
async def test_revision_rows_follow_a_moved_record(monkeypatch):
    fake = FakeTables(_record(), [_revision_row("r0", 0, "Step one"), _revision_row("r1", 1, "Step two")])
    monkeypatch.setattr(wondernote, "tables", fake)

    await wondernote._move_revisions("rec-1", "space-2")

    assert {row["space_id"] for row in fake.revisions.values()} == {"space-2"}


def test_revisions_table_mirrors_record_read_access():
    tables = {item["name"]: item for item in yaml.safe_load((ROOT / ".bifrost" / "tables.yaml").read_text())["tables"].values()}
    revisions = tables["wondernote_record_revisions"]
    columns = {column["name"] for column in revisions["schema"]["columns"]}
    assert {"record_id", "space_id", "revision", "snapshot", "edited_by", "replaced_by"} <= columns
    policies = {policy["name"]: policy for policy in revisions["policies"]}
    assert policies["shared_space_read"]["actions"] == ["read"]
    assert policies["shared_space_read"]["when"]["in"][1]["claims"] == "wondernote_readable_space_ids"
    record_columns = {column["name"] for column in tables["wondernote_records"]["schema"]["columns"]}
    assert {"revision", "last_edited_by"} <= record_columns

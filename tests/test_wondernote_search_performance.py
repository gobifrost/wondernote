import asyncio
from types import SimpleNamespace

import pytest

import functions.wondernote as wondernote
from test_wondernote_search import SearchTables, record, search_env as shared_search_env


@pytest.fixture
def search_env(monkeypatch):
    return shared_search_env.__wrapped__(monkeypatch)


@pytest.mark.asyncio
async def test_semantic_hits_reuse_authoritative_rows_without_individual_reads(monkeypatch, search_env):
    tables = SearchTables([
        record("related", title="Different title", content="Different body"),
        record("deferred", title="Needle", content="Not indexed yet"),
        record("moved", space_id="inaccessible", title="Different title"),
        record("archived", state="archived", title="Different title"),
    ])
    calls = []

    async def search(_query, **kwargs):
        calls.append(kwargs)
        return [SimpleNamespace(key=i) for i in ["related", "related", "moved", "archived", "deleted"]]

    async def forbidden(*_args):
        pytest.fail("search must not hydrate each semantic hit separately")

    monkeypatch.setattr(wondernote, "tables", tables)
    monkeypatch.setattr(wondernote, "knowledge", SimpleNamespace(search=search))
    monkeypatch.setattr(wondernote, "_visible_document", forbidden)
    result = await wondernote.wondernote_find(query="Needle")
    assert [row["id"] for row in result["items"]] == ["deferred", "related"]
    assert calls[0]["metadata_filter"] == {"space_id": "personal"}
    assert result["search_incomplete"] is False


@pytest.mark.asyncio
async def test_semantic_search_and_complete_table_fallback_overlap(monkeypatch, search_env):
    started = asyncio.Event()

    class Tables(SearchTables):
        async def query(self, *args, **kwargs):
            started.set()
            assert kwargs["skip_count"] is True
            return await super().query(*args, **kwargs)

    async def search(_query, **kwargs):
        await started.wait()
        return []

    monkeypatch.setattr(wondernote, "tables", Tables([record("deferred", title="Needle")]))
    monkeypatch.setattr(wondernote, "knowledge", SimpleNamespace(search=search))
    result = await asyncio.wait_for(wondernote.wondernote_find(query="Needle"), timeout=1)
    assert [row["id"] for row in result["items"]] == ["deferred"]


@pytest.mark.asyncio
async def test_accessible_spaces_batches_grants_and_rechecks_revocation(monkeypatch):
    spaces = [
        {"id": "own", "owner_id": "caller", "kind": "personal", "name": "Personal", "state": "active"},
        {"id": "shared", "owner_id": "other", "kind": "shared_space", "name": "Shared", "state": "active"},
        {"id": "private", "owner_id": "other", "kind": "personal", "name": "Private", "state": "active"},
    ]
    grants = [{"space_id": "shared", "principal_type": "user", "principal_id": "caller", "permission": "read", "state": "active"}]
    calls = []

    async def ensure():
        return spaces[0]

    async def query(table, **kwargs):
        calls.append((table, kwargs))
        return spaces if table == wondernote.SPACES else list(grants)

    monkeypatch.setattr(wondernote, "_identity", lambda: ("caller", "org"))
    monkeypatch.setattr(wondernote, "_ensure_personal_space", ensure)
    monkeypatch.setattr(wondernote, "_query_all", query)
    first = await wondernote._accessible_spaces()
    assert [space["id"] for space, _ in first] == ["own", "shared"]
    assert len([call for call in calls if call[0] == wondernote.GRANTS]) == 1
    grant_where = calls[-1][1]["where"]
    assert grant_where["state"] == "active"
    assert set(grant_where["space_id"]["in_"]) == {"own", "shared", "private"}
    grants[0]["state"] = "revoked"
    second = await wondernote._accessible_spaces()
    assert [space["id"] for space, _ in second] == ["own"]


@pytest.mark.asyncio
async def test_deferred_index_match_beyond_first_table_page(monkeypatch, search_env):
    rows = [record(f"row-{i}") for i in range(501)]
    rows[-1]["title"] = "Needle"
    tables = SearchTables(rows)

    async def search(*args, **kwargs):
        return []

    monkeypatch.setattr(wondernote, "tables", tables)
    monkeypatch.setattr(wondernote, "knowledge", SimpleNamespace(search=search))
    result = await wondernote.wondernote_find(query="Needle")
    assert [row["id"] for row in result["items"]] == ["row-500"]


@pytest.mark.asyncio
async def test_authoritative_table_failure_is_not_reported_as_empty_success(monkeypatch, search_env):
    async def query(*args, **kwargs):
        raise RuntimeError("authoritative table unavailable")

    async def search(*args, **kwargs):
        return [SimpleNamespace(key="stale-index-entry")]

    monkeypatch.setattr(wondernote, "tables", SimpleNamespace(query=query))
    monkeypatch.setattr(wondernote, "knowledge", SimpleNamespace(search=search))
    with pytest.raises(RuntimeError, match="authoritative table unavailable"):
        await wondernote.wondernote_find(query="Needle")


@pytest.mark.asyncio
async def test_exhaustive_open_todos_use_authoritative_pages_without_semantic_search(monkeypatch, search_env):
    rows = [record(f"todo-{i}", record_type="todo", created_at=f"{i:06d}") for i in range(503)]
    rows += [record("note"), record("finished", record_type="todo", state="done"), record("foreign", record_type="todo", space_id="inaccessible")]
    monkeypatch.setattr(wondernote, "tables", SearchTables(rows))

    async def forbidden(*_args, **_kwargs):
        pytest.fail("listing must not issue semantic searches or individual record reads")

    monkeypatch.setattr(wondernote, "knowledge", SimpleNamespace(search=forbidden))
    monkeypatch.setattr(wondernote, "_visible_document", forbidden)
    arguments = {"record_type": "todo", "states": ["active"], "limit": 100}
    found = []
    while arguments is not None:
        page = await wondernote.wondernote_find(**arguments)
        assert page["count"] == 503 and page["count_is_exact"] is True
        assert page["search_incomplete"] is False
        found.extend(row["id"] for row in page["items"])
        arguments = page["next_page"]
    assert len(found) == len(set(found)) == 503
    assert set(found) == {f"todo-{i}" for i in range(503)}

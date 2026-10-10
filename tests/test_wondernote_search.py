from types import SimpleNamespace

import pytest
import yaml

import functions.wondernote as wondernote


class Row:
    def __init__(self, row_id, data):
        self.id = row_id
        self.data = data


class SearchTables:
    def __init__(self, records):
        self.records = {record["id"]: dict(record) for record in records}

    async def query(self, table, *, limit, offset=0, **_kwargs):
        assert table == wondernote.RECORDS
        rows = list(self.records.values())
        return SimpleNamespace(documents=[Row(row["id"], row) for row in rows[offset:offset + limit]])


class SearchKnowledge:
    def __init__(self, hit_ids=(), failure=None):
        self.hit_ids = list(hit_ids)
        self.failure = failure
        self.limits = []

    async def search(self, _query, *, namespace, limit, metadata_filter=None):
        self.limits.append((namespace, limit))
        if self.failure:
            raise self.failure
        return [SimpleNamespace(key=record_id) for record_id in self.hit_ids[:limit]]


def record(record_id, **overrides):
    value = {
        "id": record_id,
        "owner_id": "owner-private",
        "organization_id": "org-private",
        "source_key": "internal-import-key",
        "space_id": "personal",
        "record_type": "note",
        "title": "General note",
        "content": "A short body.",
        "state": "active",
        "triage_state": "organized",
        "due_at": None,
        "revision": 4,
        "metadata_snapshot": [{
            "id": "metadata-row",
            "property_name": "Priority",
            "value": "high",
            "display_value": "High",
            "value_kind": "option",
            "concept_id": "secret-concept",
        }],
    }
    value.update(overrides)
    return value


@pytest.fixture
def search_env(monkeypatch):
    personal = {"id": "personal", "name": "Personal", "kind": "personal", "owner_id": "owner-private", "permission": "owner"}
    shared = {"id": "shared", "name": "Documentation", "kind": "shared_space", "owner_id": "other-owner", "permission": "read"}

    async def noop(*_args, **_kwargs):
        return None

    async def accessible_spaces():
        return [({"id": "personal", "kind": "personal"}, personal), ({"id": "shared", "kind": "shared_space"}, shared)]

    async def space_for_access(space_id, **_kwargs):
        descriptor = personal if space_id == "personal" else shared
        return {"id": space_id, "kind": descriptor["kind"]}, descriptor

    monkeypatch.setattr(wondernote, "_ensure_personal_space", noop)
    monkeypatch.setattr(wondernote, "_accessible_spaces", accessible_spaces)
    monkeypatch.setattr(wondernote, "_space_for_access", space_for_access)
    return personal, shared


@pytest.mark.asyncio
async def test_find_defaults_to_compact_bounded_previews_and_content_is_opt_in(monkeypatch, search_env):
    huge = record("giant", content="x" * 5000)
    tables = SearchTables([huge])
    monkeypatch.setattr(wondernote, "tables", tables)
    monkeypatch.setattr(wondernote, "knowledge", SearchKnowledge())
    monkeypatch.setattr(wondernote, "_visible_document", lambda _table, record_id: _visible(tables, record_id))

    compact = await wondernote.wondernote_find()
    preview = compact["items"][0]

    assert compact["limit"] == 10
    assert set(preview) == {"id", "title", "record_type", "state", "triage_state", "due_at", "revision", "space_id", "space", "excerpt", "excerpt_truncated", "metadata_snapshot"}
    assert len(preview["excerpt"]) <= 600
    assert preview["excerpt_truncated"] is True
    assert "content" not in preview and "owner_id" not in preview and "source_key" not in preview
    assert "owner_id" not in preview["space"]
    assert preview["metadata_snapshot"] == [{"property_name": "Priority", "value": "high", "display_value": "High", "value_kind": "option"}]

    full = await wondernote.wondernote_find(include_content=True)
    assert full["items"][0]["content"] == huge["content"]
    assert full["items"][0]["owner_id"] == "owner-private"


@pytest.mark.asyncio
async def test_find_excerpt_prefers_the_matching_part_of_long_content(monkeypatch, search_env):
    content = "before " * 300 + "Needle Procedure" + " after" * 300
    tables = SearchTables([record("needle", content=content)])
    monkeypatch.setattr(wondernote, "tables", tables)
    monkeypatch.setattr(wondernote, "knowledge", SearchKnowledge())
    monkeypatch.setattr(wondernote, "_visible_document", lambda _table, record_id: _visible(tables, record_id))

    result = await wondernote.wondernote_find(query="Needle Procedure")

    assert "Needle Procedure" in result["items"][0]["excerpt"]
    assert len(result["items"][0]["excerpt"]) <= 600


async def _visible(tables, record_id):
    row = tables.records.get(record_id)
    return Row(record_id, row) if row else None


@pytest.mark.asyncio
async def test_find_paginates_deduplicated_ranked_results_and_rejects_negative_offset(monkeypatch, search_env):
    first = record("title", title="Project Atlas")
    second = record("semantic", title="A related document", content="Project Atlas background")
    third = record("lexical", title="Another note", content="Project Atlas notes")
    tables = SearchTables([third, second, first])
    monkeypatch.setattr(wondernote, "tables", tables)
    monkeypatch.setattr(wondernote, "_visible_document", lambda _table, record_id: _visible(tables, record_id))
    monkeypatch.setattr(wondernote, "knowledge", SearchKnowledge(["semantic", "title", "semantic"]))

    page_one = await wondernote.wondernote_find(query="Project Atlas", limit=1)
    page_two = await wondernote.wondernote_find(**page_one["next_page"])
    page_three = await wondernote.wondernote_find(**page_two["next_page"])

    assert [page["items"][0]["id"] for page in (page_one, page_two, page_three)] == ["title", "semantic", "lexical"]
    assert page_one["next_offset"] == 1 and page_one["has_more"] is True
    assert all(value is not None for value in page_one["next_page"].values())
    assert page_three["next_offset"] is None and page_three["next_page"] is None and page_three["has_more"] is False
    with pytest.raises(Exception, match="offset"):
        await wondernote.wondernote_find(offset=-1)
    with pytest.raises(Exception, match="offset"):
        await wondernote.wondernote_find(offset=1.5)
    with pytest.raises(Exception, match="limit"):
        await wondernote.wondernote_find(limit=True)


@pytest.mark.asyncio
async def test_find_discloses_fixed_semantic_cap_without_claiming_unretrievable_pages(monkeypatch, search_env):
    records = [record(f"r{index}", title=f"Result {index}") for index in range(100)]
    tables = SearchTables(records)
    semantic = SearchKnowledge([row["id"] for row in records])
    monkeypatch.setattr(wondernote, "tables", tables)
    monkeypatch.setattr(wondernote, "knowledge", semantic)
    monkeypatch.setattr(wondernote, "_visible_document", lambda _table, record_id: _visible(tables, record_id))

    result = await wondernote.wondernote_find(query="result", limit=100)

    assert semantic.limits[0][1] == 100
    assert result["semantic_search_truncated"] is True
    assert result["search_incomplete"] is True
    assert result["has_more"] is False


@pytest.mark.asyncio
async def test_find_preserves_personal_default_filters_and_explicit_shared_space(monkeypatch, search_env):
    personal_match = record("personal-match")
    personal_wrong_metadata = record("personal-low", metadata_snapshot=[{"property_name": "Priority", "display_value": "Low"}])
    archived = record("archived", state="archived")
    shared_match = record("shared-match", space_id="shared")
    tables = SearchTables([personal_match, personal_wrong_metadata, archived, shared_match])
    monkeypatch.setattr(wondernote, "tables", tables)
    monkeypatch.setattr(wondernote, "knowledge", SearchKnowledge())
    monkeypatch.setattr(wondernote, "_visible_document", lambda _table, record_id: _visible(tables, record_id))

    personal = await wondernote.wondernote_find(metadata={"priority": "high"})
    shared = await wondernote.wondernote_find(space_id="shared", metadata={"priority": "high"})

    assert [item["id"] for item in personal["items"]] == ["personal-match"]
    assert [item["id"] for item in shared["items"]] == ["shared-match"]
    assert shared["items"][0]["space"]["name"] == "Documentation"


@pytest.mark.asyncio
async def test_find_uses_text_fallback_and_discloses_semantic_failure(monkeypatch, search_env):
    tables = SearchTables([record("fallback", content="A documented procedure")])
    monkeypatch.setattr(wondernote, "tables", tables)
    monkeypatch.setattr(wondernote, "knowledge", SearchKnowledge(failure=RuntimeError("index unavailable")))
    monkeypatch.setattr(wondernote, "_visible_document", lambda _table, record_id: _visible(tables, record_id))

    result = await wondernote.wondernote_find(query="documented procedure")

    assert [item["id"] for item in result["items"]] == ["fallback"]
    assert result["semantic_search_truncated"] is False
    assert result["search_incomplete"] is True


@pytest.mark.asyncio
async def test_find_omits_semantic_hits_outside_selected_space(monkeypatch, search_env):
    personal = record("personal", title="Shared guidance")
    shared = record("shared", title="Shared guidance", space_id="shared")
    tables = SearchTables([personal, shared])
    monkeypatch.setattr(wondernote, "tables", tables)
    monkeypatch.setattr(wondernote, "knowledge", SearchKnowledge(["shared", "personal"]))
    monkeypatch.setattr(wondernote, "_visible_document", lambda _table, record_id: _visible(tables, record_id))

    result = await wondernote.wondernote_find(query="shared guidance")

    assert [item["id"] for item in result["items"]] == ["personal"]


@pytest.mark.asyncio
async def test_find_denied_exact_space_does_not_query_records(monkeypatch, search_env):
    class NoReads:
        async def query(self, *_args, **_kwargs):
            raise AssertionError("records must not be queried after a denied space lookup")

    async def denied_space(*_args, **_kwargs):
        raise wondernote.UserError("space not found")

    monkeypatch.setattr(wondernote, "tables", NoReads())
    monkeypatch.setattr(wondernote, "_space_for_access", denied_space)

    with pytest.raises(Exception, match="space not found"):
        await wondernote.wondernote_find(space_id="not-visible")


def test_find_and_get_manifest_descriptions_and_agent_search_guidance_are_explicit():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    workflows = yaml.safe_load((root / ".bifrost" / "workflows.yaml").read_text())["workflows"]
    by_name = {workflow["name"]: workflow for workflow in workflows.values()}
    assert all("source" not in workflow for workflow in workflows.values())
    assert by_name["wondernote_find"]["description"]
    assert by_name["wondernote_get"]["description"]
    assert by_name["wondernote_find"]["tool_description"] == by_name["wondernote_find"]["description"]
    assert by_name["wondernote_get"]["tool_description"] == by_name["wondernote_get"]["description"]
    assert "organizational documentation" in by_name["wondernote_find"]["description"]

    agent = next(iter(yaml.safe_load((root / ".bifrost" / "agents.yaml").read_text())["agents"].values()))
    prompt = " ".join(agent["system_prompt"].casefold().split())
    assert "get selected records before summarizing authoritative procedures" in prompt
    assert "next_page" in prompt
    assert "search_incomplete=true" in prompt and "count_is_exact=false" in prompt
    assert "requery the first page" in prompt
    assert "documentation" in prompt and "wondernote_list_spaces" in prompt
    assert "8acc56a0-4e83-4598-8c22-234b75d68bc6" not in prompt
    assert "policies, standards, and procedures" in agent["description"]

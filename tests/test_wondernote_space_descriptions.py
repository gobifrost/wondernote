from pathlib import Path

import pytest
import yaml

import functions.wondernote as wondernote
from modules.wondernote_spaces import space_descriptor


ROOT = Path(__file__).resolve().parents[1]


def _manifest(name: str):
    return yaml.safe_load((ROOT / ".bifrost" / name).read_text())


def test_descriptor_includes_description_only_when_set():
    base = {"id": "s1", "name": "Documentation", "kind": "shared_space", "owner_id": "owner"}
    assert "description" not in space_descriptor(base, "owner", [], "org")
    described = space_descriptor({**base, "description": "How-tos and reference"}, "owner", [], "org")
    assert described["description"] == "How-tos and reference"


class Row:
    def __init__(self, row_id, data):
        self.id = row_id
        self.data = data


class FakeTables:
    def __init__(self, space=None):
        self.space = space
        self.inserted = None
        self.updated = None

    async def insert(self, _table, data):
        self.inserted = data
        return Row("new-space", data)

    async def get(self, _table, row_id):
        return Row(row_id, self.space)

    async def update(self, _table, row_id, patch):
        self.updated = patch
        self.space = {**self.space, **patch}


@pytest.fixture
def owner(monkeypatch):
    async def noop(*_args, **_kwargs):
        return {}

    async def no_grants(_space_id):
        return []

    monkeypatch.setattr(wondernote, "_identity", lambda: ("owner", "org"))
    monkeypatch.setattr(wondernote, "_ensure_personal_space", noop)
    monkeypatch.setattr(wondernote, "_active_grants", no_grants)


@pytest.mark.asyncio
async def test_create_space_stores_trimmed_description(monkeypatch, owner):
    fake = FakeTables()
    monkeypatch.setattr(wondernote, "tables", fake)

    result = await wondernote.wondernote_create_space("Integration Services", description="  Vendor integrations  ")

    assert fake.inserted["description"] == "Vendor integrations"
    assert result["space"]["description"] == "Vendor integrations"


def _space(**overrides):
    return {"id": "s1", "name": "Documentation", "kind": "shared_space", "owner_id": "owner", "state": "active", **overrides}


@pytest.mark.asyncio
async def test_update_space_sets_description_and_name(monkeypatch, owner):
    fake = FakeTables(_space())
    monkeypatch.setattr(wondernote, "tables", fake)
    monkeypatch.setattr(wondernote, "_visible_document", fake.get)

    result = await wondernote.wondernote_update_space("s1", name="Docs", description="Shared team knowledge")

    assert fake.updated["name"] == "Docs"
    assert fake.updated["description"] == "Shared team knowledge"
    assert result["space"]["description"] == "Shared team knowledge"


@pytest.mark.asyncio
async def test_update_space_empty_description_clears_it(monkeypatch, owner):
    fake = FakeTables(_space(description="Old"))
    monkeypatch.setattr(wondernote, "tables", fake)
    monkeypatch.setattr(wondernote, "_visible_document", fake.get)

    await wondernote.wondernote_update_space("s1", description="")

    assert fake.updated["description"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("space", "message"),
    [
        (_space(owner_id="someone-else"), "only the space owner"),
        (_space(kind="personal"), "named shared spaces"),
        (_space(kind="shared_item"), "named shared spaces"),
    ],
)
async def test_update_space_is_owner_only_and_named_spaces_only(monkeypatch, owner, space, message):
    fake = FakeTables(space)
    monkeypatch.setattr(wondernote, "tables", fake)
    monkeypatch.setattr(wondernote, "_visible_document", fake.get)

    with pytest.raises(Exception, match=message):
        await wondernote.wondernote_update_space("s1", description="x")
    assert fake.updated is None


@pytest.mark.asyncio
async def test_update_space_requires_a_change(monkeypatch, owner):
    fake = FakeTables(_space())
    monkeypatch.setattr(wondernote, "tables", fake)
    monkeypatch.setattr(wondernote, "_visible_document", fake.get)

    with pytest.raises(Exception, match="name or description"):
        await wondernote.wondernote_update_space("s1")


def test_manifest_declares_description_and_attaches_update_space():
    tables = {item["name"]: item for item in _manifest("tables.yaml")["tables"].values()}
    columns = {column["name"] for column in tables["wondernote_spaces"]["schema"]["columns"]}
    assert "description" in columns
    workflows = _manifest("workflows.yaml")["workflows"]
    agent = next(iter(_manifest("agents.yaml")["agents"].values()))
    attached = {workflows[tool_id]["name"] for tool_id in agent["tool_ids"]}
    assert "wondernote_update_space" in attached
    assert "wondernote_list_spaces" in agent["system_prompt"]
    assert "suggest a new space name" in agent["system_prompt"]

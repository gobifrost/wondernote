from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _manifest(name: str):
    return yaml.safe_load((ROOT / ".bifrost" / name).read_text())


def _policy(table: dict, name: str) -> dict:
    return next(item for item in table["policies"] if item["name"] == name)


def test_space_scoped_tables_and_private_operational_tables_have_expected_policies():
    tables = {item["name"]: item for item in _manifest("tables.yaml")["tables"].values()}
    for name in ("wondernote_records", "wondernote_concepts", "wondernote_record_metadata"):
        columns = {column["name"]: column for column in tables[name]["schema"]["columns"]}
        assert columns["space_id"]["required"] is True
        assert _policy(tables[name], "shared_space_read")["actions"] == ["read"]

    assert _policy(tables["wondernote_records"], "shared_space_create")["actions"] == ["create"]
    assert _policy(tables["wondernote_records"], "shared_space_update")["actions"] == ["update"]
    create_claim = _policy(tables["wondernote_records"], "shared_space_create")["when"]["in"][1]["claims"]
    assert create_claim == "wondernote_writable_named_space_ids"
    assert _policy(tables["wondernote_concepts"], "shared_space_write")["actions"] == ["create", "update"]
    assert _policy(tables["wondernote_record_metadata"], "shared_space_write")["actions"] == [
        "create",
        "update",
        "delete",
    ]
    for name in ("wondernote_import_runs", "wondernote_reminders"):
        assert {item["name"] for item in tables[name]["policies"]} == {"admin_bypass", "owner_full_access"}


def test_claims_and_agent_surface_cover_sharing_without_attaching_migration():
    claims = _manifest("claims.yaml")["claims"]
    assert {item["name"] for item in claims.values()} == {
        "wondernote_owned_space_ids",
        "wondernote_readable_space_ids",
        "wondernote_writable_space_ids",
        "wondernote_writable_named_space_ids",
    }
    workflows = _manifest("workflows.yaml")["workflows"]
    by_name = {item["name"]: item for item in workflows.values()}
    agent = next(iter(_manifest("agents.yaml")["agents"].values()))
    attached_names = {workflows[tool_id]["name"] for tool_id in agent["tool_ids"]}
    assert {
        "wondernote_list_spaces",
        "wondernote_create_space",
        "wondernote_share",
        "wondernote_unshare",
        "wondernote_move",
    } <= attached_names
    assert by_name["wondernote_migrate_spaces"]["id"] not in agent["tool_ids"]
    assert "Search Personal by default" in agent["system_prompt"]
    assert "sharing one item never exposes" in agent["system_prompt"]

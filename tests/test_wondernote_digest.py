import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import yaml

from modules.wondernote_digest import (
    digest_due,
    normalize_cron_expression,
    normalize_day_of_month,
    normalize_days_of_week,
    normalize_scope,
    normalize_slot,
    parse_local_time,
    render_digest,
    render_custom_digest,
    scope_allows_space,
)


ROOT = Path(__file__).resolve().parents[1]


def _manifest(name: str):
    return yaml.safe_load((ROOT / ".bifrost" / name).read_text())


def test_digest_time_and_scope_validation():
    assert parse_local_time("09:00") == (9, 0)
    assert normalize_slot("Morning") == "morning"
    assert normalize_slot("7:30am standup") == "7:30am standup"
    assert normalize_scope(None) == "personal"
    try:
        parse_local_time("9am")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for 9am")


def test_digest_due_window():
    now = datetime(2026, 9, 18, 13, 5, tzinfo=timezone.utc)  # 09:05 America/New_York, a Friday
    assert digest_due("09:00", "America/New_York", now=now) is True
    assert digest_due("17:00", "America/New_York", now=now) is False
    assert digest_due("09:00", "America/New_York", now=now, days_of_week=["fri"]) is True
    assert digest_due("09:00", "America/New_York", now=now, days_of_week=["mon"]) is False
    assert normalize_days_of_week(None) is None
    assert normalize_days_of_week("weekdays") == ["mon", "tue", "wed", "thu", "fri"]
    assert normalize_days_of_week(["Monday"]) == ["mon"]
    assert normalize_day_of_month(None) is None
    assert normalize_day_of_month(1) == [1]
    assert normalize_cron_expression(None) is None
    assert normalize_cron_expression("30 7 1 * *") == "30 7 1 * *"
    try:
        normalize_cron_expression("not a cron")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for bad cron")


def test_digest_due_first_of_month():
    first = datetime(2026, 9, 1, 11, 35, tzinfo=timezone.utc)  # 07:35 America/New_York, Sep 1
    second = datetime(2026, 9, 2, 11, 35, tzinfo=timezone.utc)  # Sep 2
    assert digest_due("07:30", "America/New_York", now=first, day_of_month=[1]) is True
    assert digest_due("07:30", "America/New_York", now=second, day_of_month=[1]) is False


def test_digest_scope_allow_list():
    personal = {"id": "p1", "kind": "personal"}
    sales = {"id": "s1", "kind": "shared_space"}
    assert scope_allows_space(scope="personal", kinds=None, space=personal, space_ids=None, personal_id="p1") is True
    assert scope_allows_space(scope="personal", kinds=None, space=sales, space_ids=None, personal_id="p1") is False
    assert scope_allows_space(scope="all", kinds=None, space=sales, space_ids=None, personal_id="p1") is True
    assert scope_allows_space(scope="all", kinds=None, space=sales, space_ids=["p1"], personal_id="p1") is False
    assert scope_allows_space(scope="shared", kinds=["shared_space"], space=sales, space_ids=None, personal_id="p1") is True


def test_digest_default_render():
    message = render_digest(
        slot="morning",
        items=[{"title": "Call Acme", "due_at": "2026-09-18", "space": {"name": "Sales"}}],
        inbox_count=2,
        limit=7,
    )
    assert "[Sales] Call Acme" in message
    assert "Inbox still needs triage: 2" in message


def test_custom_digest_renders_selected_records_as_safe_html():
    items = [
        {"id": "a", "title": "Call Acme <today>"},
        {"id": "b", "title": "Prepare proposal"},
        {"id": "c", "title": "Routine follow-up"},
    ]
    message = render_custom_digest(
        slot="daily_priorities", items=items, limit=3,
        plan={"sections": [{"heading": "Do today", "items": [
            {"id": "a", "priority": "red", "reason": "Due & blocked"},
            {"id": "b", "priority": "yellow", "reason": "Next action"},
        ]}]},
    )
    assert "<b>Daily Priorities</b><br/><br/><b>Do today</b><br/><br/>" in message
    assert "🔴 Call Acme &lt;today&gt; — Due &amp; blocked" in message
    assert "Due &amp; blocked<br/><br/>🟡 Prepare proposal" in message
    assert "Routine follow-up" not in message
    assert "Focus:" not in message


def test_custom_digest_rejects_uncollected_record():
    try:
        render_custom_digest(
            slot="morning", items=[{"id": "a", "title": "Known"}], limit=3,
            plan={"sections": [{"heading": "Do today", "items": [
                {"id": "invented", "priority": "red", "reason": "Imaginary"},
            ]}]},
        )
    except ValueError:
        pass
    else:
        raise AssertionError("uncollected digest record should fail validation")


def test_digest_manifest_surface():
    tables = {item["name"]: item for item in _manifest("tables.yaml")["tables"].values()}
    assert "wondernote_digest_prefs" in tables
    assert "wondernote_digest_runs" in tables
    pref_cols = {c["name"] for c in tables["wondernote_digest_prefs"]["schema"]["columns"]}
    assert {"slot", "local_time", "timezone", "days_of_week", "day_of_month", "cron_expression", "scope", "custom_prompt"} <= pref_cols
    assert {item["name"] for item in tables["wondernote_digest_prefs"]["policies"]} == {"admin_bypass", "owner_full_access"}
    workflows = _manifest("workflows.yaml")["workflows"]
    by_name = {item["name"]: item for item in workflows.values()}
    assert "wondernote_get_digest_prefs" in by_name
    assert "wondernote_set_digest_pref" in by_name
    assert "wondernote_preview_digest" in by_name
    assert by_name["wondernote_digest_tick"]["type"] == "workflow"
    agent = next(iter(_manifest("agents.yaml")["agents"].values()))
    attached = {workflows[tool_id]["name"] for tool_id in agent["tool_ids"]}
    assert {"wondernote_get_digest_prefs", "wondernote_set_digest_pref", "wondernote_preview_digest", "wondernote_send_digest"} <= attached
    assert "wondernote_digest_tick" not in attached
    events = _manifest("events.yaml")["events"]
    ticker = next(iter(events.values()))
    assert ticker["source_type"] == "schedule"
    assert ticker["subscriptions"][0]["workflow_id"] == by_name["wondernote_digest_tick"]["id"]


def test_send_digest_uses_only_current_users_saved_slot(monkeypatch):
    from functions import wondernote

    calls = []
    monkeypatch.setattr(wondernote, "_identity", lambda: ("owner-1", "org-1"))

    async def query(table, **kwargs):
        calls.append(kwargs["where"])
        return [{"owner_id": "owner-1", "organization_id": "org-1", "slot": "morning"}]

    async def deliver(pref):
        calls.append(pref)
        return {"state": "queued", "slot": "morning", "item_count": 2, "delivery_execution_id": "delivery-1"}

    monkeypatch.setattr(wondernote, "_query_all", query)
    monkeypatch.setattr(wondernote, "_deliver_digest_pref", deliver)
    result = asyncio.run(wondernote.wondernote_send_digest("Morning"))
    assert calls[0] == {"owner_id": "owner-1", "organization_id": "org-1", "slot": "morning"}
    assert calls[1]["owner_id"] == "owner-1"
    assert result["delivery_execution_id"] == "delivery-1"
    assert result["state"] == "queued"


def test_delivery_queues_real_teams_workflow_and_records_receipt(monkeypatch):
    from functions import wondernote

    class Tables:
        def __init__(self):
            self.rows = []

        async def insert(self, table, row):
            assert table == wondernote.DIGEST_RUNS
            self.rows.append(row)

    table_client = Tables()
    calls = []

    async def collect(owner_id, organization_id, pref):
        return [{"id": "record-1", "title": "Act today"}], 0

    async def message(slot, items, inbox_count, pref):
        return "<b>Do today</b><br/>🔴 Act today", "xml"

    async def send(owner_id, organization_id, title, body, text_format):
        calls.append((owner_id, organization_id, title, body, text_format))
        return "teams-execution-1"

    monkeypatch.setattr(wondernote, "tables", table_client)
    monkeypatch.setattr(wondernote, "_digest_collect", collect)
    monkeypatch.setattr(wondernote, "_digest_message", message)
    monkeypatch.setattr(wondernote, "_digest_send_teams", send)
    result = asyncio.run(wondernote._deliver_digest_pref({
        "id": "pref-1", "owner_id": "owner-1", "organization_id": "org-1", "slot": "morning",
    }))
    assert calls == [("owner-1", "org-1", "WonderNote Morning", "<b>Do today</b><br/>🔴 Act today", "xml")]
    assert result == {"state": "queued", "slot": "morning", "item_count": 1, "delivery_execution_id": "teams-execution-1"}
    assert table_client.rows[0]["state"] == "queued"


def test_digest_identity_resolves_verified_teams_sender(monkeypatch):
    from functions import wondernote

    monkeypatch.setattr(wondernote, "_identity", lambda: (wondernote.SYSTEM_USER_ID, "org-1"))
    monkeypatch.setattr(wondernote, "context", SimpleNamespace(artifact_workspace_id="parent-run"))

    async def get_run(run_id):
        assert run_id == "parent-run"
        return SimpleNamespace(
            agent_name="Teams Concierge", trigger_type="api",
            caller_user_id=wondernote.SYSTEM_USER_ID, org_id=None,
            input={"activity_id": "activity-1", "conversation_id": "conversation-1", "sender_aad_id": "aad-1"},
        )

    async def profile(aad_id):
        assert aad_id == "aad-1"
        return {"id": "aad-1", "mail": "jack@example.com"}

    async def get_user(email):
        assert email == "jack@example.com"
        return SimpleNamespace(id="jack-1", email=email, is_active=True, organization_id="org-1")

    monkeypatch.setattr(wondernote.agents, "get_run", get_run)
    monkeypatch.setattr(wondernote, "_teams_sender_profile", profile)
    monkeypatch.setattr(wondernote.users, "get", get_user)
    assert asyncio.run(wondernote._digest_identity()) == ("jack-1", "org-1")


def test_digest_identity_rejects_unverified_agent_run(monkeypatch):
    from functions import wondernote

    monkeypatch.setattr(wondernote, "_identity", lambda: (wondernote.SYSTEM_USER_ID, "org-1"))
    monkeypatch.setattr(wondernote, "context", SimpleNamespace(artifact_workspace_id="untrusted-run"))

    async def get_run(run_id):
        return SimpleNamespace(
            agent_name="Other Agent", trigger_type="api",
            caller_user_id=wondernote.SYSTEM_USER_ID, org_id="org-1",
            input={"activity_id": "activity-1", "conversation_id": "conversation-1", "sender_aad_id": "aad-1"},
        )

    monkeypatch.setattr(wondernote.agents, "get_run", get_run)
    try:
        asyncio.run(wondernote._digest_identity())
    except wondernote.UserError:
        pass
    else:
        raise AssertionError("unverified agent run must not select a digest owner")

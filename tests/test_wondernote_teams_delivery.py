import asyncio
from types import SimpleNamespace

import httpx
import pytest
import yaml
from pathlib import Path

from bifrost._context import clear_execution_context, set_execution_context
from bifrost._execution_context import ExecutionContext, Organization


def _manifest(name: str) -> dict:
    with open(f".bifrost/{name}") as source:
        return yaml.safe_load(source)


def test_delivery_wrapper_only_sends_to_the_executing_user(monkeypatch):
    from functions import wondernote_teams

    calls = []

    async def send_message(**kwargs):
        calls.append(kwargs)
        return {"success": True}

    monkeypatch.setattr(
        wondernote_teams,
        "context",
        SimpleNamespace(email="owner@example.com"),
    )
    monkeypatch.setattr(wondernote_teams, "send_message", send_message)

    result = asyncio.run(
        wondernote_teams.wondernote_send_teams_message(
            target_type="user",
            user="OWNER@example.com",
            message="Reminder: follow up",
            summary="WonderNote reminder",
            text_format="plain",
        )
    )

    assert result == {"success": True}
    assert calls == [
        {
            "target_type": "user",
            "user": "OWNER@example.com",
            "message": "Reminder: follow up",
            "summary": "WonderNote reminder",
            "text_format": "plain",
        }
    ]

    with pytest.raises(wondernote_teams.UserError, match="current user"):
        asyncio.run(
            wondernote_teams.wondernote_send_teams_message(
                target_type="user",
                user="other@example.com",
                message="not allowed",
            )
        )

    with pytest.raises(wondernote_teams.UserError, match="user targets"):
        asyncio.run(
            wondernote_teams.wondernote_send_teams_message(
                target_type="channel",
                user="owner@example.com",
                message="not allowed",
            )
        )


def test_delivery_wrapper_reads_email_from_the_sdk_execution_context(monkeypatch):
    from functions import wondernote_teams

    calls = []

    async def send_message(**kwargs):
        calls.append(kwargs)
        return {"success": True}

    monkeypatch.setattr(wondernote_teams, "send_message", send_message)
    set_execution_context(
        ExecutionContext(
            user_id="owner-id",
            email="owner@example.com",
            name="Owner",
            scope="org-id",
            organization=Organization(id="org-id", name="Synthetic organization"),
            is_platform_admin=False,
            is_function_key=False,
            execution_id="execution-id",
        )
    )
    try:
        result = asyncio.run(
            wondernote_teams.wondernote_send_teams_message(
                target_type="user",
                user="owner@example.com",
                message="Synthetic reminder",
            )
        )
    finally:
        clear_execution_context()

    assert result == {"success": True}
    assert calls[0]["user"] == "owner@example.com"


def test_delivery_wrapper_and_integration_are_solution_owned():
    workflows = _manifest("workflows.yaml")["workflows"]
    delivery = next(
        item
        for item in workflows.values()
        if item["function_name"] == "wondernote_send_teams_message"
    )
    assert delivery["path"] == "functions/wondernote_teams.py"
    assert delivery["type"] == "workflow"
    assert delivery["access_level"] == "authenticated"
    assert delivery["role_names"] == []
    agent = next(iter(_manifest("agents.yaml")["agents"].values()))
    assert delivery["id"] not in agent["tool_ids"]

    connections = _manifest("connections.yaml")["connections"]
    teams = connections["Microsoft Teams Bot"]
    assert teams["integration_name"] == "Microsoft Teams Bot"
    assert teams["position"] == 0
    fields = {item["key"]: item for item in teams["template"]["config_schema"]}
    assert {"tenant_id", "client_id", "client_secret", "bot_handle"} <= fields.keys()
    assert all(fields[key]["required"] for key in ("tenant_id", "client_id", "client_secret", "bot_handle"))
    assert fields["client_secret"]["type"] == "secret"
    assert {
        "teams_app_id",
        "bot_name",
        "default_team_id",
        "default_channel_id",
        "support_channel_id",
        "announcements_channel_id",
    } <= fields.keys()
    assert fields["teams_app_id"]["required"] is True
    assert all(not fields[key]["required"] for key in (
        "bot_name",
        "default_team_id",
        "default_channel_id",
        "support_channel_id",
        "announcements_channel_id",
    ))
    assert "mappings" not in teams
    assert "config" not in teams["template"]


def test_public_bundle_is_sealed_and_has_no_instance_delivery_dependency():
    solution = yaml.safe_load(open("bifrost.solution.yaml"))
    source = open("functions/wondernote.py").read()
    wrapper = open("functions/wondernote_teams.py").read()
    module = open("modules/microsoft_teams_bot.py").read()

    assert solution["allow_outbound_access"] is False
    assert solution["allow_inbound_access"] is True
    assert "global_repo_access" not in solution
    assert "functions/wondernote_teams.py::wondernote_send_teams_message" in source
    assert "modules.extensions" not in wrapper
    assert "from modules.microsoft_teams_bot import send_message" in wrapper
    assert "INTEGRATION_NAME = \"Microsoft Teams Bot\"" in module
    assert "teams_conversation_messages" not in open("README.md").read()
    assert not Path("requirements.txt").exists()
    assert "from modules._vendor import markdown" in module


def test_markdown_renderer_is_vendored_with_its_license_and_provenance():
    """Sealed Solutions cannot depend on the platform-global requirements file."""
    module = Path("modules/microsoft_teams_bot.py").read_text()

    assert "from modules._vendor import markdown" in module
    assert not Path("requirements.txt").exists()
    assert Path("modules/_vendor/markdown/LICENSE.md").is_file()
    notice = Path("THIRD_PARTY_NOTICES.md").read_text()
    assert "Python-Markdown 3.10.2" in notice
    assert "BSD" in notice


def test_distributable_docs_have_no_instance_specific_references():
    forbidden_markers = ("C" + "ovi", "goc" + "ovi.com")
    for path in ("README.md", *[str(item) for item in __import__("pathlib").Path("docs").rglob("*.md")]):
        source = open(path).read()
        assert all(marker not in source for marker in forbidden_markers)


def test_bundled_teams_module_uses_synthetic_http_delivery(monkeypatch):
    from modules import microsoft_teams_bot

    requests = []
    class Integration:
        config = {
            "tenant_id": "tenant-synthetic",
            "client_id": "client-synthetic",
            "client_secret": "secret-synthetic",
            "bot_handle": "bot-synthetic",
            "teams_app_id": "app-synthetic",
        }

    class Integrations:
        async def get(self, name):
            assert name == "Microsoft Teams Bot"
            return Integration()

    class MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, url, **kwargs):
            request = httpx.Request(method, url)
            requests.append((request, kwargs))
            if url.endswith("/oauth2/v2.0/token"):
                return httpx.Response(200, json={"access_token": "token-synthetic"}, request=request)
            if url.endswith("/users/owner%40example.com"):
                return httpx.Response(200, json={"id": "owner-id"}, request=request)
            if url.endswith("/users/owner-id/teamwork/installedApps") and method == "GET":
                return httpx.Response(200, json={"value": []}, request=request)
            if url.endswith("/users/owner-id/teamwork/installedApps") and method == "POST":
                return httpx.Response(201, request=request)
            if url.endswith("/v3/conversations"):
                return httpx.Response(200, json={"id": "conversation-1"}, request=request)
            if url.endswith("/v3/conversations/conversation-1/activities"):
                return httpx.Response(200, json={"id": "activity-1"}, request=request)
            raise AssertionError(f"unexpected synthetic request: {method} {url}")

    monkeypatch.setattr(microsoft_teams_bot, "integrations", Integrations())
    monkeypatch.setattr(microsoft_teams_bot.httpx, "AsyncClient", lambda **_kwargs: MockClient())

    result = asyncio.run(
        microsoft_teams_bot.send_message(
            target_type="user",
            user="owner@example.com",
            message="Synthetic reminder\nSecond line",
            summary="WonderNote reminder",
            text_format=None,
        )
    )

    assert result["success"] is True
    assert result["conversation_id"] == "conversation-1"
    assert result["activity_id"] == "activity-1"
    assert {request.url.host for request, _ in requests} == {
        "login.microsoftonline.com",
        "graph.microsoft.com",
        "smba.trafficmanager.net",
    }
    conversation_request = next(
        kwargs["json"]
        for request, kwargs in requests
        if request.url.path.endswith("/v3/conversations/conversation-1/activities")
    )
    assert conversation_request["textFormat"] == "xml"
    assert conversation_request["text"] == "Synthetic reminder<br>\nSecond line"


def test_bundled_module_has_no_unowned_history_table_dependency():
    source = open("modules/microsoft_teams_bot.py").read()

    assert "teams_conversation_messages" not in source
    assert "from bifrost import UserError, integrations" in source
    assert "async def update_message" not in source
    assert "async def delete_message" not in source
    assert 'target_type: Literal["user"]' in source


def test_scheduled_owner_payload_reaches_the_owner_only_delivery_boundary(monkeypatch):
    from functions import wondernote, wondernote_teams

    queued = []
    delivered = []

    async def get_user(user_id):
        assert user_id == "owner-id"
        return SimpleNamespace(email="owner@example.com")

    async def execute(ref, input_data, org_id, run_as):
        queued.append((ref, input_data, org_id, run_as))
        return "delivery-execution"

    async def send_message(**kwargs):
        delivered.append(kwargs)
        return {"success": True}

    monkeypatch.setattr(wondernote.users, "get", get_user)
    monkeypatch.setattr(wondernote.workflows, "execute", execute)
    monkeypatch.setattr(wondernote_teams, "send_message", send_message)

    execution_id = asyncio.run(
        wondernote._digest_send_teams(
            "owner-id",
            "org-id",
            "WonderNote Morning",
            "Synthetic scheduled digest",
            "markdown",
        )
    )
    ref, payload, org_id, run_as = queued[0]
    assert execution_id == "delivery-execution"
    assert ref == "functions/wondernote_teams.py::wondernote_send_teams_message"
    assert org_id == "org-id"
    assert run_as == "owner-id"

    set_execution_context(
        ExecutionContext(
            user_id="owner-id",
            email="owner@example.com",
            name="Owner",
            scope="org-id",
            organization=Organization(id="org-id", name="Synthetic organization"),
            is_platform_admin=False,
            is_function_key=False,
            execution_id="scheduled-execution-id",
        )
    )
    try:
        result = asyncio.run(wondernote_teams.wondernote_send_teams_message(**payload))
    finally:
        clear_execution_context()

    assert result == {"success": True}
    assert delivered == [payload]

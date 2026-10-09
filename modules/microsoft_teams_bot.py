"""Microsoft Teams Bot Framework helpers for proactive messages."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any, Literal
from urllib.parse import quote

import httpx
import markdown
from bifrost import UserError, integrations

INTEGRATION_NAME = "Microsoft Teams Bot"
DEFAULT_SERVICE_URL = "https://smba.trafficmanager.net/amer/"
logger = logging.getLogger(__name__)
# Match the bounded transient-error policy used by other outbound modules.
_MAX_RETRIES = 10
_BASE_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 30.0


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    """Honor Retry-After, otherwise use bounded exponential backoff with jitter."""
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), _MAX_BACKOFF_SECONDS)
        except ValueError:
            pass
    backoff = min(_BASE_BACKOFF_SECONDS * (2 ** attempt), _MAX_BACKOFF_SECONDS)
    return backoff + random.uniform(0, backoff * 0.25)


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    """Retry transient Microsoft HTTP responses using the shared module policy."""
    for attempt in range(_MAX_RETRIES + 1):
        response = await client.request(method, url, **kwargs)
        if response.status_code == 429 or 500 <= response.status_code < 600:
            if attempt >= _MAX_RETRIES:
                return response
            delay = _retry_delay(response, attempt)
            logger.warning(
                "Microsoft %s %s -> %s, retrying in %.1fs (attempt %d/%d)",
                method,
                response.request.url.host,
                response.status_code,
                delay,
                attempt + 1,
                _MAX_RETRIES,
            )
            await asyncio.sleep(delay)
            continue
        return response
    raise RuntimeError("Microsoft request retry loop exited unexpectedly")


async def load_config() -> dict[str, Any]:
    """Load the Teams bot integration mapped to the executing organization."""
    integration = await integrations.get(INTEGRATION_NAME)
    if not integration:
        raise UserError(f"{INTEGRATION_NAME} integration is not configured")
    cfg = dict(integration.config or {})
    missing = [
        key
        for key in ("tenant_id", "client_id", "client_secret")
        if not str(cfg.get(key) or "").strip()
    ]
    if missing:
        raise UserError(
            f"{INTEGRATION_NAME} is missing required configuration: "
            + ", ".join(missing)
        )
    return cfg


async def _oauth_token(
    client: httpx.AsyncClient,
    cfg: dict[str, Any],
    scope: str,
    tenant_id: str | None = None,
) -> str:
    response = await _request_with_retry(
        client,
        "POST",
        "https://login.microsoftonline.com/"
        f"{tenant_id or cfg['tenant_id']}/oauth2/v2.0/token",
        data={
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "grant_type": "client_credentials",
            "scope": scope,
        },
    )
    if response.is_error:
        raise UserError(
            f"Microsoft authentication failed ({response.status_code}); "
            "check the Teams bot integration credentials"
        )
    token = str(response.json().get("access_token") or "")
    if not token:
        raise UserError("Microsoft authentication returned no access token")
    return token


def _markdown_to_teams_html(text: str) -> str:
    """Render Markdown as Teams XML while preserving bare line breaks."""
    rendered = markdown.markdown(str(text), extensions=["nl2br"])
    rendered = re.sub(r"<br\s*/?>", "<br>", rendered, flags=re.IGNORECASE)
    rendered = re.sub(r"</p>\s*<p>", "<br><br>", rendered, flags=re.IGNORECASE)
    rendered = re.sub(r"</?p>", "", rendered, flags=re.IGNORECASE)
    return rendered.strip()


async def _resolve_user_id(
    client: httpx.AsyncClient,
    graph_token: str,
    user: str,
) -> str:
    response = await _request_with_retry(
        client,
        "GET",
        f"https://graph.microsoft.com/v1.0/users/{quote(user, safe='')}",
        params={"$select": "id"},
        headers={"Authorization": f"Bearer {graph_token}"},
    )
    if response.status_code == 404:
        raise UserError(f"Microsoft Teams user was not found: {user}")
    if response.is_error:
        raise UserError(
            f"Microsoft Graph could not resolve the Teams user ({response.status_code})"
        )
    return str(response.json()["id"])


async def get_user_profile(user_id: str) -> dict[str, Any]:
    """Resolve a Teams/AAD sender to its Microsoft Graph identity."""
    value = str(user_id or "").strip()
    if not value:
        raise UserError("user_id is required")
    cfg = await load_config()
    async with httpx.AsyncClient(timeout=30.0) as client:
        graph_token = await _oauth_token(
            client, cfg, "https://graph.microsoft.com/.default"
        )
        response = await _request_with_retry(
            client,
            "GET",
            f"https://graph.microsoft.com/v1.0/users/{quote(value, safe='')}",
            params={"$select": "id,displayName,mail,userPrincipalName"},
            headers={"Authorization": f"Bearer {graph_token}"},
        )
    if response.status_code == 404:
        raise UserError("The Teams sender was not found in Microsoft Graph")
    if response.is_error:
        raise UserError(
            f"Microsoft Graph could not resolve the Teams sender ({response.status_code})"
        )
    return dict(response.json())


async def _ensure_installed(
    client: httpx.AsyncClient,
    graph_token: str,
    teams_app_id: str,
    user_id: str,
) -> None:
    """Ensure this bot is installed for the one owner who will receive a message."""
    url = (
        "https://graph.microsoft.com/v1.0/users/"
        f"{quote(user_id, safe='')}/teamwork/installedApps"
    )
    headers = {"Authorization": f"Bearer {graph_token}"}

    async def find_installation_id() -> str:
        listed = await _request_with_retry(
            client,
            "GET",
            url,
            headers=headers,
            params={"$expand": "teamsApp"},
        )
        if listed.is_error:
            raise UserError(
                "Microsoft Teams could not inspect the bot installation "
                f"({listed.status_code})"
            )
        for item in list(listed.json().get("value") or []):
            app = item.get("teamsApp") or {}
            if str(app.get("id") or "") == teams_app_id:
                return str(item.get("id") or "")
        return ""

    if await find_installation_id():
        return

    response = await _request_with_retry(
        client,
        "POST",
        url,
        headers=headers,
        json={
            "teamsApp@odata.bind": (
                "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps/"
                f"{teams_app_id}"
            ),
        },
    )
    if response.status_code == 409 and await find_installation_id():
        return
    if response.status_code not in {200, 201, 204}:
        raise UserError(
            "Microsoft Teams could not install the bot "
            f"({response.status_code}); confirm the Teams catalog app ID "
            "and tenant installation permissions"
        )


async def send_message(
    *,
    target_type: Literal["user"],
    user: str,
    message: str,
    summary: str | None = None,
    text_format: Literal["plain", "markdown", "xml"] | None = None,
) -> dict[str, Any]:
    """Send a proactive message only to one Microsoft Graph user."""
    if target_type != "user":
        raise UserError("WonderNote Teams delivery supports user targets only.")
    text = str(message or "").strip()
    if not text:
        raise UserError("message is required")
    recipient = str(user or "").strip()
    if not recipient:
        raise UserError("user is required for a user target")

    cfg = await load_config()
    teams_app_id = str(cfg.get("teams_app_id") or "").strip()
    if not teams_app_id:
        raise UserError(
            "The Teams catalog app ID must be added to the Microsoft Teams Bot integration"
        )
    resolved_text_format = text_format
    if resolved_text_format is None:
        text = _markdown_to_teams_html(text)
        resolved_text_format = "xml"
    activity: dict[str, Any] = {
        "type": "message",
        "text": text,
        "textFormat": resolved_text_format,
    }
    if summary:
        activity["summary"] = str(summary)

    async with httpx.AsyncClient(timeout=30.0) as client:
        bot_token = await _oauth_token(
            client,
            cfg,
            "https://api.botframework.com/.default",
            tenant_id=str(cfg.get("bot_tenant_id") or "botframework.com"),
        )
        graph_token = await _oauth_token(client, cfg, "https://graph.microsoft.com/.default")
        user_id = await _resolve_user_id(client, graph_token, recipient)
        await _ensure_installed(client, graph_token, teams_app_id, user_id)
        conversation_response = await _request_with_retry(
            client,
            "POST",
            f"{DEFAULT_SERVICE_URL.rstrip('/')}/v3/conversations",
            headers={"Authorization": f"Bearer {bot_token}"},
            json={
                "bot": {
                    "id": f"28:{cfg['client_id']}",
                    "name": str(cfg.get("bot_name") or "Bifrost"),
                },
                "members": [{"id": user_id}],
                "channelData": {"tenant": {"id": cfg["tenant_id"]}},
                "tenantId": cfg["tenant_id"],
                "isGroup": False,
            },
        )
        if conversation_response.is_error:
            raise UserError(
                "Microsoft Teams rejected the message "
                f"({conversation_response.status_code}): {conversation_response.text[:500]}"
            )
        conversation = conversation_response.json() if conversation_response.content else {}
        conversation_id = str(conversation.get("id") or "")
        if not conversation_id:
            raise UserError("Microsoft Teams created no conversation ID")
        activity_response = await _request_with_retry(
            client,
            "POST",
            f"{DEFAULT_SERVICE_URL.rstrip('/')}/v3/conversations/"
            f"{quote(conversation_id, safe='')}/activities",
            headers={"Authorization": f"Bearer {bot_token}"},
            json=activity,
        )
        if activity_response.is_error:
            raise UserError(
                "Microsoft Teams created the conversation but rejected the message "
                f"({activity_response.status_code}): {activity_response.text[:500]}"
            )
        activity_response_body = (
            activity_response.json() if activity_response.content else {}
        )
        return {
            "success": True,
            "target_type": "user",
            "service_url": DEFAULT_SERVICE_URL.rstrip("/"),
            "conversation_id": conversation_id,
            "activity_id": activity_response_body.get("id"),
            "response": {
                "conversation": conversation,
                "activity": activity_response_body,
            },
        }

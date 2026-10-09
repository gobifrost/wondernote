# WonderNote

WonderNote is an agent-only, personal-and-shared notes and todos workspace for
Bifrost. People save a `note` or `todo`, then build a shared vocabulary from
canonical metadata and aliases. It does not assume a particular industry,
customer type, or work process.

## What it provides

- An authenticated WonderNote agent with tools for saving, updating, finding,
  resolving, sharing, moving, and organizing notes and todos.
- Personal spaces, named shared spaces, and isolated direct-item shares with
  owner, write, and read permissions.
- Custom claims and table policies backed by workflow-level permission checks.
- Record revisions, stale-write protection, previews, and archiving rather than
  hard deletion.
- Deterministic reminders and scheduled priority digests. The schedule runs
  every 15 minutes; it never calls an agent to deliver a notification.

Unscoped writes and searches use Personal. Shared writes require an explicit
writable space. Each returned record includes its space and effective permission.

## Install and develop

WonderNote is a sealed Bifrost Solution. It owns its workflows and Teams delivery
module, so runtime module or workflow fallback to a loose workspace is disabled.

Use the CLI that matches your Bifrost instance. This release is tested with
Bifrost `1.4.2-dev.1791521206` and the platform's matching Python SDK.

```bash
git clone https://github.com/gobifrost/wondernote.git
cd wondernote
bifrost solution deploy . --global
```

Choose `--org <organization>` instead of `--global` for a single organization.
The release source ZIP can be installed with `bifrost solution install
<zip-path> --global`. Credentials, table rows, and user records are not included.

When upgrading an existing installation, select its install ID with `--solution`
to preserve its scope. Access gates are install-local; set
`bifrost solution update . --solution <install-id> --no-allow-outbound-access`
after upgrading an older installation that allowed shared workspace fallback.

The agent is available to authenticated users in its installed scope. The
package supplies no additional access role; each workflow checks record ownership
and explicit sharing permissions. To restrict who can chat with the agent,
change its access configuration in `.bifrost/agents.yaml` and redeploy through
the Solution lifecycle.

For development, run `PYTHONPATH=. pytest -q`. Markdown formatting is bundled with the Solution under `modules/_vendor`; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
A Git-connected installation is updated with the platform's Solution Git sync;
pushing a commit alone does not update the installed Solution.

Deploy replaces this Solution's managed definitions. It does not move runtime
records, notification preferences, integration mappings, or credentials. Review
the install target and current managed entities before deploying.

## Optional Microsoft Teams delivery

Native authenticated WonderNote use works without Microsoft Teams. Reminders and
digests need the optional `Microsoft Teams Bot` integration in the receiving
organization. An administrator configures the integration and its organization
mapping outside this repository; the Solution carries only the schema, never
values, mappings, or secrets.

If your instance already has this bot integration configured, reuse its existing
organization mapping. WonderNote does not require a second bot and its deployment
does not replace the shared Teams workflows or Teams Concierge.

For inbound bot activities, follow the website's [Microsoft Teams event-source
setup](https://gobifrost.com/docs/how-to-guides/events/microsoft-event-sources/#microsoft-teams)
and [event subscription guide](https://gobifrost.com/docs/how-to-guides/events/subscriptions/).
These pages cover Bifrost event routing; the delivery configuration below still
requires an existing Microsoft bot registration and published Teams app.

Required configuration keys are `tenant_id`, `client_id`, `client_secret`,
`bot_handle`, and `teams_app_id`. Optional keys are `bot_name`,
`default_team_id`, `default_channel_id`, `support_channel_id`, and
`announcements_channel_id`.

The Solution-owned delivery workflow accepts only a `user` target and verifies
that target against the executing user's email. A reminder or digest therefore
runs as, and can notify, its owner only. It is not an agent tool and cannot be
used as a general recipient-messaging surface.

An inbound Teams agent may optionally target this Solution because
`allow_inbound_access` is enabled. That external setup is separate from
WonderNote. When a trusted system caller reaches a digest preference through an
inbound Teams path, WonderNote verifies the parent agent run and sender against
Microsoft Graph before resolving an active Bifrost user in the same organization.

The bundled Teams module depends only on its configured integration. It does
not write delivery history to a table outside this Solution.

## Security model

Table policies deny direct access unless a rule allows it. Workflows use an
execution-scoped platform credential for data transport, then independently
check the caller's owner, read, or write permission before each operation. Keep
both layers aligned when adding a tool, table, or workflow.

Digest formatting receives only the selected records and treats their content as
data. Scheduled delivery uses the stored owner identity rather than a caller
supplied as workflow input.

## License

WonderNote is released under the [MIT License](LICENSE).

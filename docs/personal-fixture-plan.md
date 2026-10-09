# Personal installation verification plan

Use an isolated personal Bifrost installation after a reviewer has approved this
candidate. Do not use a production organization, a real Microsoft Teams bot, or
real recipient data.

1. Install the sealed Solution and confirm that its owned workflows and
   `Microsoft Teams Bot` configuration schema are present without configuration
   values or organization mappings.
2. Create temporary users and a temporary organization. Verify personal notes,
   shared-space permissions, reminder ownership, and digest ownership with the
   existing workflow test contracts.
3. Exercise the delivery boundary only with the synthetic HTTP test fixture in
   `tests/test_wondernote_teams_delivery.py`. It intercepts every token, Graph,
   and Bot Framework request and never sends a Teams message.
4. Confirm that invoking the owned delivery workflow for another email address
   or a channel target is rejected, while an owner-scoped reminder or digest
   retains the owner's identity.
5. If an inbound Teams agent is configured separately, verify its trusted
   system-caller and Graph identity checks using temporary identities. Do not
   attach the delivery workflow as an agent tool.
6. Remove every temporary organization, user, record, schedule, mapping, and
   integration value created for the check.

---
name: hivemq-migration-planner
description: Plans a migration from HiveMQ (Community Edition, Enterprise/Platform or HiveMQ Cloud, including Serverless before its December 31, 2026 retirement) to the ConnectMQ managed MQTT broker. Audits config.xml, installed extensions and File RBAC credentials.xml, inventories clients through the REST API or Control Center, checks the workload against ConnectMQ plan limits, converts RBAC roles to ConnectMQ access control rules, maps client IDs and writes a phased migration plan with waves and rollback. Use when someone wants to leave HiveMQ, replace HiveMQ Cloud Serverless, cut HiveMQ license or operations cost, or asks how HiveMQ settings, extensions, permissions or bridges translate to ConnectMQ.
license: MIT
metadata:
  source-broker: hivemq
  target-broker: connectmq
  companion-skill: hivemq-migration-executor
---

# HiveMQ to ConnectMQ: Migration Planner

You are an MQTT migration engineer. Your job is to produce a migration plan from HiveMQ to ConnectMQ that the team
can execute without losing messages or stranding devices. You produce files, not just advice: an audit, converted
access control rules, a client ID map and a filled-in plan.

Read `references/connectmq-target.md` before mapping anything: its constraints (generated credentials, issued
client ID prefix, no client certificates, no broker extensions, plan limits) drive most of the plan.
`references/hivemq-mapping.md` holds the setting-by-setting and extension-by-extension mapping and the
HiveMQ-specific pitfalls.

## Principles

- **Identify the HiveMQ flavour first.** Self-hosted CE, self-hosted Enterprise/Platform and HiveMQ Cloud differ in
  what you can read (config files vs console), how you inventory clients, and how the parallel run is built.
- **Parallel run, never big bang.** Enterprise installations can bridge to ConnectMQ with the HiveMQ Enterprise
  Bridge Extension; CE and HiveMQ Cloud use the `relay.py` script from the executor skill. Either way both brokers run
  while clients move in waves.
- **Extensions are the hidden scope.** Every enabled extension (Kafka, databases, ESE, custom Java) is a feature the
  migration must replace with a service or drop explicitly.
- **Credentials and client IDs change for every client.** Plan the rollout of new host + credentials + client ID as
  one atomic config change per client.
- **Watch the calendar.** HiveMQ Cloud Serverless retires on December 31, 2026; plan the last wave with buffer
  before that date, not on it.

## Workflow

### Step 1: Collect the facts

Ask for (or locate) and do not proceed on assumptions for the starred items:

- HiveMQ flavour and version; for HiveMQ Cloud the plan (Serverless, Starter, Professional, Enterprise) *
- Self-hosted: `conf/config.xml` and the `extensions/` folder listing (with `DISABLED` markers) *
- File RBAC `credentials.xml` (only names, roles and permissions are read) or the ESE configuration if used *
- HiveMQ Cloud: the credentials list and permission per credential from the console *
- Client groups: backend services, dashboards, gateways, field devices; who can change each group's config and how
  (OTA, redeploy, manual visit) *
- Downtime tolerance per group, contract or retirement deadline
- Integrations: Kafka, databases, data pipelines, Control Center users, monitoring (Prometheus extension)

### Step 2: Run the audit (self-hosted)

```bash
python scripts/hivemq_audit.py --config /opt/hivemq/conf/config.xml --extensions /opt/hivemq/extensions --out migration/
```

It writes `migration/audit.md` (blockers, actions, plan checks), `migration/acl-rules.{md,csv,json}` from File RBAC
and `migration/users.csv`. Read the audit and discuss every blocker with the user. For HiveMQ Cloud, build the same
outputs by hand from the console using the table in `references/hivemq-mapping.md`.

### Step 3: Measure the workload

Collect at least 24 hours, including the busiest period:

- **Enterprise/Platform**: Control Center dashboard (connections, inbound/outbound message rates, retained
  messages, subscriptions) and the REST API (`rest-api` enabled in config.xml) for the client list:
  `curl -s http://HIVEMQ_HOST:8888/api/v1/mqtt/clients` (paginated, follow the cursor links) -> `clients.csv`.
  Port and auth depend on your REST API configuration.
- **CE**: no Control Center or REST API. Use the Prometheus extension if installed, the `hivemq.log` connect lines,
  or subscribe to `#` with a monitoring client for a day and count topics, rates and payload sizes.
- **HiveMQ Cloud**: the cluster's console metrics and the usage page; count credentials and client IDs from your
  provisioning system.

Also record the largest payload, the fastest single publisher and the longest time devices stay offline.

### Step 4: Fit check and plan choice

Fill section 2 of `assets/migration-plan-template.md`:

- Concurrent connections = peak connections + bridge/relay (1) + dashboards/tools + headroom 20 %.
- Session minutes per month = sum over clients of connected minutes (always-on client ~ 43,200).
- Retained count vs plan limit; largest payload vs plan max message size (HiveMQ often allows 256 MB).
- `session-expiry/max-interval` and real offline periods vs plan max session expiry.
- Shared subscriptions or per-topic permissions in use -> Basic or Scale.
- Above Scale limits -> contact support@connectmq.com for a custom plan before planning waves.

### Step 5: Map identities

1. Decide the credential scope (per device / per group / per site) with the user; explain revocation trade-offs.
2. Build the client map: `python scripts/client_id_map.py clients.csv --issued-id <ISSUED_ID> -o migration/client-map.csv`
   (`--credential-column` when there are several credentials). Resolve every `error` row.
3. After credentials are issued, fill `new_username` in `migration/users.csv` and re-run the audit with
   `--user-map migration/users.csv --client-map migration/client-map.csv` to get final rules.

### Step 6: Design the parallel run

- Enterprise/Platform with the Enterprise Bridge Extension licensed: HiveMQ bridges to ConnectMQ.
- CE, HiveMQ Cloud, or no bridge license: `relay.py` on a small VM or container next to the clients.
- Forward topics (HiveMQ -> ConnectMQ): everything devices publish. Reverse topics (ConnectMQ -> HiveMQ): commands
  and config for devices not yet migrated. No overlap, or messages loop.
- Retained state to copy before wave 1 (`copy_retained.py`).

### Step 7: Write the plan

Copy `assets/migration-plan-template.md` to `migration/plan.md` and fill every section with real numbers and the
outputs above, including a replacement for every extension and the waves with go criteria and rollback triggers
(`references/cutover-and-rollback.md`). Finish with a short summary for the user: plan chosen, blockers and their
resolutions, number of waves, estimated duration, deadline buffer, open questions.

## Output checklist

- [ ] HiveMQ flavour, version and deadline recorded
- [ ] `migration/audit.md` reviewed (or the Cloud permission table filled); every blocker has a resolution
- [ ] Every enabled extension has a replacement or an explicit "drop"
- [ ] Workload numbers with peak values; plan selected with headroom, or custom plan requested
- [ ] `migration/client-map.csv` without errors
- [ ] `migration/acl-rules.md` with final usernames
- [ ] Bridge or relay chosen; forward/reverse topics defined without overlap
- [ ] `migration/plan.md` complete, including rollback per wave

Hand over to the **hivemq-migration-executor** skill to carry out the plan.

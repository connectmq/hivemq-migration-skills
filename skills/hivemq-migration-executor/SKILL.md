---
name: hivemq-migration-executor
description: Executes a planned migration from HiveMQ (CE, Enterprise/Platform or HiveMQ Cloud) to the ConnectMQ managed MQTT broker step by step. Verifies the ConnectMQ broker, sets up the parallel run with the HiveMQ Enterprise Bridge Extension or the relay script, copies retained messages, validates message flow on both brokers, moves clients in waves with go/no-go checks, handles rollback and decommissions HiveMQ, keeping a migration log. Use when a HiveMQ-to-ConnectMQ plan exists and the team is ready to bridge, run a wave, cut over before a deadline such as the HiveMQ Cloud Serverless retirement, roll back or troubleshoot connection errors during the move.
license: MIT
metadata:
  source-broker: hivemq
  target-broker: connectmq
  companion-skill: hivemq-migration-planner
---

# HiveMQ to ConnectMQ: Migration Executor

You carry out a HiveMQ to ConnectMQ migration safely, one verified step at a time. You change configuration, run
the scripts, read the results, and stop for a human decision at every go/no-go point.

Before starting, read:

- `migration/plan.md` (from the planner skill). If it does not exist, offer to run the
  **hivemq-migration-planner** skill first; do not improvise waves without a plan.
- `references/connectmq-target.md` for limits and reason codes.
- `references/hivemq-bridge.md` for the bridge (Enterprise) or relay (CE / HiveMQ Cloud) setup.
- `references/cutover-and-rollback.md` for wave rules, go/no-go and rollback.

## Rules

- **Ask before every production change** (bridge extension config, relay start, ACL change, client wave, stopping
  HiveMQ), and say what will change and how to undo it.
- Never put passwords in files that get committed. Use environment variables (`CONNECTMQ_USERNAME`,
  `CONNECTMQ_PASSWORD`, `CONNECTMQ_CLIENT_ID`, `SOURCE_USERNAME`, `SOURCE_PASSWORD`) or a secrets store.
- After each step append an entry to `migration/log.md`: time, step, command, result, numbers, decision.
- If a check fails, stop, diagnose using the reason-code table, and propose a fix or a rollback.
- Track the deadline in the plan (contract end, Serverless retirement). If a wave slips, tell the user what the
  remaining buffer is.

## Scripts

All in `scripts/` (Python 3.9+, `pip install "paho-mqtt>=2.0"`):

| Script | Purpose |
| --- | --- |
| `verify_connectmq.py` | Connect, QoS 0/1/2, retained, will, session, ACL deny checks |
| `relay.py` | Two-way bridge with loop protection: the parallel run for CE and HiveMQ Cloud |
| `copy_retained.py` | Copy retained messages from HiveMQ to ConnectMQ (or export/import JSON) |
| `compare_streams.py` | Subscribe on both brokers and compare topics, counts and payloads |
| `client_id_map.py` | Old -> new client ID mapping (if not done in planning) |

## Phase 1: Prepare ConnectMQ

1. Confirm the plan's ConnectMQ plan matches the console (limits, ACL availability).
2. Create the credentials listed in the plan, plus one for the bridge/relay (client ID `<ISSUED_ID>_bridge` or
   `_relay`) and one for tests.
3. Enter the rules from `migration/acl-rules.md` in order, set the no-match policy, and **add the bridge/relay
   rules first** (publish forward topics, subscribe reverse topics). Enable ACL only after the rules are in.
4. Smoke test:

   ```bash
   export CONNECTMQ_USERNAME=... CONNECTMQ_PASSWORD=... CONNECTMQ_CLIENT_ID=...
   python scripts/verify_connectmq.py --url mqtts://BROKER_HOST:8883 --prefix <a topic branch the test credential may use>
   ```

   Add `--expect-deny-publish` / `--expect-deny-subscribe` for topics the ACL must block. If the persistent-session
   check fails, check the plan's session expiry before relying on offline queueing.

## Phase 2: Parallel run

1. Build the bridge or relay from `references/hivemq-bridge.md` with the plan's forward/reverse topics.
   - Enterprise/Platform: configure the Enterprise Bridge Extension on one HiveMQ node first (staging if you have
     it), check its log, then roll it out.
   - CE / HiveMQ Cloud: run `relay.py` under a supervisor, with a dedicated HiveMQ credential for the relay.
2. Check the connection: HiveMQ log or Control Center shows the bridge/relay client; the ConnectMQ console shows
   `<ISSUED_ID>_bridge` (or `_relay`) online.
3. Copy retained state: `python scripts/copy_retained.py --source mqtts://HIVEMQ_HOST:8883 --filter '#' --export migration/retained-<date>.json --target mqtts://BROKER_HOST:8883 --limit <plan retained limit>`
   (`--dry-run` first; leave out test and junk topics).
4. Validate for at least one business cycle: `python scripts/compare_streams.py --source ... --target ... --filter '<forward filters>' --duration 600`.
   Expect MATCH. Investigate any "only on source" topic (ACL deny on the bridge, oversized payload, filter typo).

## Phase 3: Waves

For each wave in the plan:

1. Run the go/no-go checklist from `references/cutover-and-rollback.md` and get an explicit "go" from the user.
2. Change the clients of the wave: host `BROKER_HOST`, port 8883 (TLS on), new username/password, client ID from
   `migration/client-map.csv`. Show the user the exact config diff for their client type.
3. Watch for 15 minutes: ConnectMQ connected count rises by the wave size, HiveMQ count falls, no
   `0x85/0x86/0x87/0x8A/0x97` in client logs, `compare_streams.py` still MATCH.
4. Soak for the agreed time, log the numbers, then propose the next wave.
5. On a rollback trigger, follow the rollback actions and log the cause.

Typical client changes:

```text
host: xxxx.s1.eu.hivemq.cloud   -> BROKER_HOST
port: 8883                      -> 8883 (TLS, verify hostname, system CA store)
username/password: HiveMQ        -> ConnectMQ credential
client_id: sensor-0042          -> <ISSUED_ID>_sensor-0042
client certificate (mTLS)       -> removed; username/password instead
keepalive: 0 or >600            -> <= 600
reconnect: fixed delay          -> exponential backoff with jitter, max 60 s
```

Replacement services from the plan (Kafka producer, database writer, Data Hub logic) must be live and verified in
wave 1, before devices move: they read from ConnectMQ while the bridge still feeds it.

## Phase 4: Cutover and decommission

Follow "Final cutover" in `references/cutover-and-rollback.md`: final retained copy, remove the bridge
configuration (or stop the relay), watch HiveMQ for stragglers (Control Center client list, REST API, or log
connect lines), keep HiveMQ available for the agreed rollback window, then decommission: cancel the HiveMQ Cloud
cluster or license, delete credentials and certificates, remove extensions' downstream jobs.

## Troubleshooting quick reference

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Bridge/relay `0x86` | Wrong ConnectMQ username/password | Copy from console |
| Bridge/relay connects and drops at once | Client ID not `<ISSUED_ID>_<suffix>` (0x85), or two instances with one ID (0x8E) | Fix client ID; one instance per ID (one bridge per cluster, not per node) |
| Nothing arrives on ConnectMQ | ACL denies bridge publish, or local subscription filter wrong | Add bridge rule; check forward filters |
| Messages loop / duplicate | Forward and reverse filters overlap | Split topics by direction |
| Relay cannot subscribe on HiveMQ Cloud | Relay credential is publish-only, or topic permission too narrow | Give the relay credential publish and subscribe on forward/reverse topics |
| Devices `0x85` | Old client ID kept | Use the ID from `client-map.csv` |
| Devices `0x8A` banned | Wrong credentials retried in a loop from one NAT | Stop the wave, fix config, wait for the ban to expire |
| `0x97` on publish | Payload above plan max size (common after HiveMQ's 256 MB default) or per-client rate | Shrink payloads, move files to object storage, or custom plan |
| Consumers miss enriched fields | A HiveMQ extension modified messages | Add the logic to the publisher or a subscriber service |

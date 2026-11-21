# HiveMQ Migration Skills for ConnectMQ

<p align="center"><img src="assets/cover.jpg" alt="HiveMQ to ConnectMQ migration skills for AI agents" width="820"></p>

**AI agent skills for planning and executing a migration from HiveMQ (Community Edition, Enterprise/Platform or HiveMQ Cloud) to the [ConnectMQ](https://connectmq.com/?utm_source=github&utm_medium=readme&utm_campaign=migration-skills&utm_content=hivemq) managed MQTT broker.**

Give your AI coding agent (Claude Code, Claude.ai, or any agent that reads `SKILL.md` skills) the knowledge and the
tools of an MQTT migration engineer: it audits `config.xml` and your extensions, converts File RBAC roles to
ConnectMQ access control rules, maps client IDs, sizes the ConnectMQ plan, writes a phased plan, then sets up the
bridge or relay, runs the client waves and handles rollback. Leaving HiveMQ Cloud Serverless before its
December 31, 2026 retirement? Start here.

> Part of the **MQTT Broker Migration Skills** collection:
> **HiveMQ** ·
> [Mosquitto](https://github.com/connectmq/mosquitto-migration-skills) ·
> [EMQX](https://github.com/connectmq/emqx-migration-skills) ·
> [VerneMQ](https://github.com/connectmq/vernemq-migration-skills) ·
> [AWS IoT Core](https://github.com/connectmq/aws-iot-core-migration-skills) ·
> [Azure IoT Hub](https://github.com/connectmq/azure-iot-hub-migration-skills)

## What's inside

| Skill | What it does |
| --- | --- |
| [`hivemq-migration-planner`](skills/hivemq-migration-planner/SKILL.md) | Identifies the HiveMQ flavour, audits `config.xml`, extensions and File RBAC `credentials.xml`, inventories clients (REST API, Control Center, Cloud console), checks the workload against ConnectMQ plan limits, converts permissions, maps client IDs, writes `migration/plan.md` |
| [`hivemq-migration-executor`](skills/hivemq-migration-executor/SKILL.md) | Verifies the ConnectMQ broker, sets up the Enterprise Bridge Extension or the relay, copies retained messages, compares message flow on both brokers, runs client waves with go/no-go gates, rolls back, decommissions; keeps `migration/log.md` |

### Scripts (Python 3.9+)

| Script | Purpose |
| --- | --- |
| `hivemq_audit.py` | `config.xml` + extensions folder + File RBAC `credentials.xml` -> audit report, ConnectMQ ACL rules, user list (standard library only) |
| `client_id_map.py` | Old client IDs -> `<ISSUED_ID>_<old-id>`, flags unsafe characters and collisions |
| `verify_connectmq.py` | Smoke test: TLS connect, QoS 0/1/2, retained, Last Will, persistent session, ACL denies |
| `relay.py` | Two-way bridge with loop protection for HiveMQ CE and HiveMQ Cloud |
| `copy_retained.py` | Copies retained messages from HiveMQ to ConnectMQ (or exports JSON) |
| `compare_streams.py` | Subscribes on both brokers and compares topics, counts and payloads |

MQTT scripts need `pip install "paho-mqtt>=2.0"`.

## Install

### Claude Code (plugin)

```text
/plugin marketplace add connectmq/hivemq-migration-skills
/plugin install hivemq-migration-skills@connectmq-hivemq-migration
```

### Claude Code (manual)

Copy the folders in `skills/` to `~/.claude/skills/` (all projects) or `.claude/skills/` (one project):

```bash
git clone https://github.com/connectmq/hivemq-migration-skills
cp -r hivemq-migration-skills/skills/* ~/.claude/skills/
```

### Claude.ai

Zip a skill folder (e.g. `skills/hivemq-migration-planner`) and upload it under Settings > Capabilities > Skills.

### Other agents

Each skill is a self-contained folder with a `SKILL.md` (YAML front matter + instructions), `references/` and
`scripts/`. Agents that support the Agent Skills format can load the folders directly; for others, add the
`SKILL.md` content to the agent's instructions.

## Example prompts

- "We're on HiveMQ Cloud Serverless with 40 devices. Plan a move to ConnectMQ before the retirement date."
- "Audit /opt/hivemq and list every extension we would have to replace."
- "Convert our File RBAC credentials.xml to ConnectMQ access control rules."
- "Set up a relay between our HiveMQ Cloud cluster and ConnectMQ for telemetry and commands."
- "Run wave 2 of the migration plan and check that nothing is lost."
- "Devices get 0x97 on publish after switching from HiveMQ. Why?"

## How the migration works

1. **Plan**: identify the HiveMQ flavour, audit config and extensions, measure the workload, map credentials,
   client IDs and permissions, replace extensions, choose waves.
2. **Parallel run**: the Enterprise Bridge Extension (or `relay.py` for CE and HiveMQ Cloud) forwards device topics
   to ConnectMQ and commands back.
3. **Consumers first**: backends, dashboards and extension replacements read from ConnectMQ while devices are still
   on HiveMQ.
4. **Devices in waves**: canary, then growing waves, each with go/no-go and rollback.
5. **Cutover**: remove the bridge/relay, keep HiveMQ for the rollback window, decommission.

Key ConnectMQ facts the skills plan around: credentials are generated in the console and come with an issued client
ID (devices connect as `<ISSUED_ID>_<suffix>`), authentication is username/password over TLS (no client
certificates, no broker extensions), per-topic ACL and shared subscriptions are on the Basic and Scale plans, and
each plan caps connections, session minutes, connection rate, message size, retained messages and session expiry.
See [plans and limits](https://github.com/connectmq/connectmq/wiki/Plans-and-Limits).

## Repository layout

```
.claude-plugin/              plugin + marketplace manifests (Claude Code)
skills/
  hivemq-migration-planner/
    SKILL.md
    references/              connectmq-target.md, hivemq-mapping.md, cutover-and-rollback.md
    assets/                  migration-plan-template.md
    scripts/                 hivemq_audit.py, client_id_map.py, connectmq_acl.py
  hivemq-migration-executor/
    SKILL.md
    references/              connectmq-target.md, hivemq-bridge.md, cutover-and-rollback.md
    scripts/                 verify_connectmq.py, relay.py, copy_retained.py, compare_streams.py, ...
```

## Learn more

- Blog: [HiveMQ Migration Skills: Migrate from HiveMQ to ConnectMQ with an AI Agent](https://connectmq.com/blog/hivemq-migration-skills/?utm_source=github&utm_medium=readme&utm_campaign=migration-skills&utm_content=hivemq)
- [HiveMQ migration checklist](https://connectmq.com/blog/hivemq-migration-checklist/?utm_source=github&utm_medium=readme&utm_campaign=migration-skills&utm_content=hivemq)
- [ConnectMQ docs (wiki)](https://github.com/connectmq/connectmq/wiki) · [MQTT code samples](https://github.com/connectmq/mqtt-code-samples)
- Start free (no credit card): [app.connectmq.com/signup](https://app.connectmq.com/signup?utm_source=github&utm_medium=readme&utm_campaign=migration-skills&utm_content=hivemq)

## Contributing

Issues and pull requests are welcome: migration patterns, HiveMQ settings or extensions the audit does not know
yet, fixes to the scripts. Please do not include real credentials or customer data in examples.

## License

MIT. HiveMQ is a trademark of HiveMQ GmbH; this project is not affiliated with or endorsed by HiveMQ GmbH.

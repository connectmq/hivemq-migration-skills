# HiveMQ to ConnectMQ: Feature and Configuration Mapping

## Which HiveMQ are you leaving?

| Flavour | What you can read | Inventory | Parallel run |
| --- | --- | --- | --- |
| HiveMQ Community Edition (self-hosted) | `conf/config.xml`, `extensions/` | Logs, Prometheus extension, a monitoring subscriber | `relay.py` |
| HiveMQ Enterprise / Platform (self-hosted) | `conf/config.xml`, `extensions/`, Control Center | REST API `/api/v1/mqtt/clients`, Control Center | Enterprise Bridge Extension, or `relay.py` |
| HiveMQ Cloud (Serverless, Starter, Professional, Enterprise) | Console only | Console metrics, your provisioning records | `relay.py` |

HiveMQ Cloud Serverless retires on December 31, 2026. Anything still connected to a Serverless cluster after that
date stops working, so the migration must finish with buffer before it.

## config.xml mapping

| HiveMQ config.xml | ConnectMQ | Notes |
| --- | --- | --- |
| `<tcp-listener>` port 1883 | `mqtt://HOST:1883` | Tests only; production uses 8883 |
| `<tls-tcp-listener>` + keystore | `mqtts://HOST:8883` | Public CA: clients use the OS CA store; remove custom truststores from clients unless their TLS stack has none |
| `<tls>` `<client-authentication-mode>REQUIRED` | not available | mTLS devices move to username/password over TLS |
| `<websocket-listener>` / `<tls-websocket-listener>` | `wss://HOST:8084/mqtt` | Path is always `/mqtt` (HiveMQ default is `/mqtt` too, but check custom paths) |
| `<mqtt><session-expiry><max-interval>` | Plan max session expiry | 1 h / 2 h / 24 h; HiveMQ default is ~136 years |
| `<mqtt><message-expiry><max-interval>` | MQTT 5 message expiry | Supported; queued messages also end with the session |
| `<mqtt><queued-messages>` (max-queue-size, strategy) | Broker-managed | Queue lives only as long as the session |
| `<mqtt><packets><max-packet-size>` | Plan max message size | 256 KB on Free/Basic; HiveMQ default is 256 MB |
| `<mqtt><keep-alive>` (max-keep-alive, allow-unlimited) | 600 s cap | Keep-alive 0 (unlimited) clients get the cap |
| `<mqtt><topic-alias>` | Supported | MQTT 5 topic aliases |
| `<mqtt><shared-subscriptions>` | Basic and Scale | `$share/<group>/<filter>` syntax is the same |
| `<mqtt><retained-messages>` | Every plan | Count limited per plan |
| `<mqtt><receive-maximum>` | Set by the broker per plan | No client change |
| `<mqtt><quality-of-service><max-qos>` | QoS 0/1/2 | Every plan |
| `<restrictions><max-connections>` | Plan concurrent connections | Free 5, Basic 100, Scale 1,000 |
| `<restrictions><max-client-id-length>` | `<ISSUED_ID>_<old-id>` | Check device SDK limits after prefixing |
| `<restrictions><incoming-bandwidth-throttling>` | Per-client message rate + monthly traffic fair use | See pricing page |
| `<security><allow-empty-client-id>` | not available | Clients must send `<ISSUED_ID>_<suffix>` |
| `<cluster>` | Managed | Nothing to migrate |
| `<persistence>` | Managed | Copy retained messages; sessions are not copied |
| `<control-center>` | ConnectMQ console | Dashboard, topic monitor, sessions, rules and alerts |
| `<rest-api>` | not available | Use it for inventory before the cutover |

## Extension mapping

| HiveMQ extension | ConnectMQ replacement |
| --- | --- |
| File RBAC (`credentials.xml`) | Credentials + access control rules (`hivemq_audit.py` converts) |
| Enterprise Security Extension (SQL, LDAP, JWT/OAuth, HTTP realms) | Credentials in ConnectMQ; rewrite permissions as rules by hand |
| Allow-all extension | Every client gets credentials |
| Enterprise Bridge Extension | Use it to bridge HiveMQ -> ConnectMQ during the parallel run; then remove |
| Kafka extension | Consumer service: subscribe (shared subscription) and produce to Kafka |
| PostgreSQL / MySQL / MongoDB / other data extensions | Subscriber service that writes to the database |
| Amazon Kinesis / Google Pub/Sub extensions | Subscriber service that forwards |
| Data Hub (data policies, transformations, schemas) | Validate/transform in the publisher or in a subscriber service |
| Prometheus extension | ConnectMQ dashboard and usage meters; device-side metrics |
| MQTT message log extension | Topic monitor in the console, or a logging subscriber |
| Sparkplug-aware extension | Sparkplug topics work as plain MQTT; no broker-side Sparkplug state |
| Custom Java extensions (interceptors, auth, enrichment) | Move the logic into a client-side service; interceptors that rewrite payloads become a subscriber that republishes |

## File RBAC to ConnectMQ rules

| File RBAC | ConnectMQ |
| --- | --- |
| `<user><name>` | Credential (new generated username); subject `username` |
| `<role>` with `<permissions>` | One rule per permission per user |
| `<activity>PUBLISH` / `SUBSCRIBE` / `ALL` | action `publish` / `subscribe` / `all` |
| `<topic>` with `+` / `#` | Same filter |
| `${{clientid}}` | `${clientid}` (full new ID) or expanded per client with `--client-map` |
| `${{username}}` | Replaced with the old username literally, so topics keep their shape |
| `<qos>`, `<retained-messages>`, `<shared-subscription>`, `<shared-group>` | No equivalent; enforce in clients (flagged as warnings) |
| Not granted = denied | No-match policy `deny` |

## HiveMQ Cloud permissions

HiveMQ Cloud credentials carry a permission (for example publish and subscribe, publish only, subscribe only), and
higher plans add topic-level permissions. Build a table from the console:

| HiveMQ Cloud credential | Permission | Topics | Clients using it | ConnectMQ credential | ConnectMQ rules |
| --- | --- | --- | --- | --- | --- |

A credential with "publish and subscribe" on everything needs no rule when the no-match policy is `allow`; if you
enable ACL with `deny`, give it an `all` rule on `#`.

## Behavioural differences that bite

- **Very long sessions.** HiveMQ keeps sessions and queues effectively forever by default. On ConnectMQ, offline
  sessions end at the plan's session expiry; sleepy devices must resubscribe on connect and fetch config from
  retained "desired state" topics.
- **Huge payloads.** HiveMQ accepts up to 256 MB by default; firmware images and camera frames sent over MQTT hit the
  ConnectMQ plan's message size. Move large files to object storage and send a link.
- **Client IDs.** HiveMQ accepts any ID (and can assign one). ConnectMQ only accepts `<ISSUED_ID>_<suffix>`.
- **Extension side effects.** Interceptors that silently modify messages, add user properties or drop topics stop
  at cutover. Consumers that rely on those changes break even though "the MQTT part works".
- **Overload protection and throttling.** HiveMQ throttles noisy clients; ConnectMQ answers with `0x97` (rate) or
  `0x89` (connection storm). Clients need backoff.
- **Cluster-level ordering.** Like HiveMQ, ConnectMQ guarantees order per client and topic as the spec requires, not
  across publishers; consumers that infer cross-device order must use payload timestamps.
- **$SYS and Control Center consumers.** Scripts reading Control Center or REST API data need a replacement.

## Sizing example

HiveMQ Cloud Serverless with 60 devices that connect every 10 minutes for 30 seconds, 1 dashboard and 1 backend
always on, plus the relay: peak concurrency is about 10 + 3 = 13 -> Basic. Session minutes:
60 x 144 connects/day x 0.5 min x 30 days = 129,600 + 3 x 43,200 = 259,200 per month -> within Basic's 1 M.

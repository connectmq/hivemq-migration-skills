# Parallel Run: HiveMQ to ConnectMQ

ConnectMQ does not open bridges itself. The bridge runs on the HiveMQ side (Enterprise Bridge Extension) or outside
both brokers (`relay.py`). Either way it is one MQTT client connection to ConnectMQ and counts toward the plan's
concurrent connections.

| HiveMQ flavour | Parallel run |
| --- | --- |
| Enterprise / Platform with the Enterprise Bridge Extension | Option A: bridge extension |
| Community Edition | Option B: relay |
| HiveMQ Cloud (all plans) | Option B: relay |
| Enterprise without a bridge license | Option B: relay |

## Option A: HiveMQ Enterprise Bridge Extension

The extension lives in `extensions/hivemq-bridge-extension/`. Its configuration is
`extensions/hivemq-bridge-extension/bridge-configuration.xml` in the 4.x documentation; newer releases move it to
`conf/config.xml` inside the extension folder and still read the old path for a while. Check the docs of your
version: <https://docs.hivemq.com/bridge>.

| Setting | Value for ConnectMQ |
| --- | --- |
| `remote-broker/connection/static/host` / `port` | `BROKER_HOST` / `8883` (the default port is 1883, so set it) |
| `remote-broker/tls/enabled` | `true` (default is `false`). ConnectMQ uses a public CA; the truststore is optional, add one only if your JVM trust store lacks the root |
| `remote-broker/authentication/mqtt-simple-authentication` | `username` / `password` of the ConnectMQ bridge credential |
| `client-identifier` (in the simple-authentication block) | `<ISSUED_ID>_bridge`. Without it the extension picks its own ID and ConnectMQ refuses it with `0x85` |
| `remote-broker/mqtt/version` | `5` (default). Needed for `SUB` topics |
| `remote-broker/mqtt/clean-start` / `session-expiry` | `false` / within the plan's max session expiry, so QoS 1 messages survive short reconnects |
| `topics/topic` with `mode` `PUB` | HiveMQ -> ConnectMQ: the forward filters (telemetry, status, events) |
| `topics/topic` with `mode` `SUB` | ConnectMQ -> HiveMQ: the reverse filters (commands for devices still on HiveMQ). Requires an MQTT 5 remote, which ConnectMQ is |
| `destination` | Optional topic rewrite (`site1/{#}`) when several clusters merge into one ConnectMQ broker |
| `preserve-retained` | `true` on state topics so retained flags survive the bridge (default `false`) |

Example, based on the configuration examples in the HiveMQ Enterprise Bridge Extension documentation:

```xml
<hivemq-bridge-extension>
  <bridges>
    <bridge>
      <enabled>true</enabled>
      <name>connectmq</name>
      <remote-broker>
        <mqtt>
          <version>5</version>
          <clean-start>false</clean-start>
          <session-expiry>3600</session-expiry>
          <keep-alive>60</keep-alive>
        </mqtt>
        <authentication>
          <mqtt-simple-authentication>
            <client-identifier>ISSUED_ID_bridge</client-identifier>
            <username>${ENV:CONNECTMQ_USERNAME}</username>
            <password>${ENV:CONNECTMQ_PASSWORD}</password>
          </mqtt-simple-authentication>
        </authentication>
        <connection>
          <static>
            <host>BROKER_HOST</host>
            <port>8883</port>
          </static>
        </connection>
        <tls>
          <enabled>true</enabled>
        </tls>
      </remote-broker>
      <topics>
        <!-- Forward: HiveMQ -> ConnectMQ -->
        <topic>
          <filter>devices/+/telemetry</filter>
          <mode>PUB</mode>
        </topic>
        <topic>
          <filter>devices/+/status</filter>
          <mode>PUB</mode>
          <preserve-retained>true</preserve-retained>
        </topic>
        <!-- Reverse: ConnectMQ -> HiveMQ -->
        <topic>
          <filter>devices/+/commands/#</filter>
          <mode>SUB</mode>
        </topic>
      </topics>
    </bridge>
  </bridges>
</hivemq-bridge-extension>
```

Notes:

- The documentation lists the client ID as `client-identifier` in the simple-authentication options; confirm the
  element placement for your version if the extension rejects the file, and check the bridge's client ID in the
  ConnectMQ sessions view after it connects.
- The `${ENV:...}` placeholders keep the password out of the file.
- QoS 2 messages are bridged as QoS 1.
- Apply changes at runtime by adding a `DISABLED` file to the extension folder, editing the file, then removing
  `DISABLED`. The HiveMQ Platform Operator does not hot-reload the bridge configuration.
- `PUB` and `SUB` filters must not overlap, or messages loop between the brokers.

In a HiveMQ cluster the bridge must connect **once** to ConnectMQ, not once per node with the same client ID
(nodes would take over each other's session, DISCONNECT `0x8E`). Check how your extension version distributes bridges
across nodes.

## Option B: relay.py (CE, HiveMQ Cloud)

Create a dedicated HiveMQ credential for the relay with **publish and subscribe** permission on the forward and
reverse topics (on HiveMQ Cloud: the credential's permission in the console). Then:

```bash
export SOURCE_USERNAME=relay SOURCE_PASSWORD='***'
export CONNECTMQ_USERNAME=... CONNECTMQ_PASSWORD='***' CONNECTMQ_CLIENT_ID=<ISSUED_ID>
python scripts/relay.py \
  --source mqtts://xxxx.s1.eu.hivemq.cloud:8883 --source-client-id connectmq-relay \
  --target mqtts://BROKER_HOST:8883 \
  --forward 'devices/+/telemetry' --forward 'devices/+/status' \
  --reverse 'devices/+/commands/#'
```

- HiveMQ Cloud requires TLS on 8883 with a public certificate: no CA flags needed.
- Use a fixed `--source-client-id` so HiveMQ keeps the relay's session across restarts.
- Run it under systemd or Docker with automatic restart; it prints counters every 30 s.
- Both brokers support MQTT 5, so the relay also tags relayed messages (`x-relay` user property) and uses no-local
  subscriptions as a second guard against loops.
- On HiveMQ Cloud Serverless the relay counts as one of your connections there as well.

## ACL rules for the bridge/relay on ConnectMQ

Add these before enabling ACL (replace `ISSUED_ID_bridge` with the bridge or relay client ID):

| Permission | Action | Topic filter | Subject | Value |
| --- | --- | --- | --- | --- |
| allow | publish | `devices/+/telemetry` | clientid | `ISSUED_ID_bridge` |
| allow | publish | `devices/+/status` | clientid | `ISSUED_ID_bridge` |
| allow | subscribe | `devices/+/commands/#` | clientid | `ISSUED_ID_bridge` |

## Checking the parallel run

```bash
# Both sides, same filters, live traffic only
python scripts/compare_streams.py --source mqtts://HIVEMQ_HOST:8883 --target mqtts://BROKER_HOST:8883 \
  --filter 'devices/#' --duration 300
```

Retained messages published before the bridge/relay started are not forwarded: copy them with `copy_retained.py`.

## Removing the bridge/relay at cutover

1. Confirm no device publishes forward topics on HiveMQ any more (`compare_streams.py` shows no source traffic, or
   the HiveMQ client list only contains the bridge/relay).
2. Remove the bridge from the extension configuration (or disable the extension with a `DISABLED` file), or stop the relay.
3. Remove the bridge/relay ACL rules and credential on ConnectMQ.

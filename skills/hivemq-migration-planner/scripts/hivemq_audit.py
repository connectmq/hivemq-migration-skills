#!/usr/bin/env python3
"""Audits a self-hosted HiveMQ broker (Community Edition or Enterprise/Platform) for a migration to ConnectMQ.

Reads HiveMQ's conf/config.xml, the extensions folder and the File RBAC extension's credentials.xml (usernames,
roles and permissions; passwords are never read), then writes:

  <out>/audit.md          findings: every setting and extension mapped to ConnectMQ, with blockers and actions
  <out>/acl-rules.md/.csv/.json   ConnectMQ access control rules converted from File RBAC roles
  <out>/users.csv         old usernames, with an empty column for the new ConnectMQ username (fill in, then pass
                          back with --user-map to get rules for the new usernames)

Examples:
  python hivemq_audit.py --config /opt/hivemq/conf/config.xml --extensions /opt/hivemq/extensions --out migration/
  python hivemq_audit.py --rbac /opt/hivemq/extensions/hivemq-file-rbac-extension/conf/credentials.xml --out migration/
  python hivemq_audit.py --config config.xml --rbac credentials.xml --client-map client-map.csv --user-map users.csv

--client-map: CSV old_client_id,new_client_id (from client_id_map.py). Permissions using ${{clientid}} are expanded
into one rule per client so topics keep the old client ID.
--user-map:   CSV old_username,new_username. Rules get the new ConnectMQ username as subject; ${{username}} in a
user's permission topic is replaced by the OLD username, so the topic tree does not change.

HiveMQ Cloud has no config files: use the permission table in references/hivemq-mapping.md instead.
Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from connectmq_acl import Rule, check, dedupe, expand_pattern, load_map, render  # noqa: E402

SEVERITIES = ("blocker", "action", "check", "info", "unknown")

# Extension folder name (prefix) -> (severity, ConnectMQ mapping)
EXTENSIONS = {
    "hivemq-file-rbac-extension": ("action", "Users and roles convert to ConnectMQ credentials + access control rules (acl-rules.md). Per-topic ACL needs Basic or Scale."),
    "hivemq-enterprise-security-extension": ("blocker", "ESE realms (SQL, LDAP, JWT/OAuth, HTTP) are not supported. Move clients to ConnectMQ credentials and rewrite permissions as access control rules by hand."),
    "hivemq-allow-all-extension": ("action", "Allow-all means no authentication today. Every client needs ConnectMQ credentials."),
    "hivemq-bridge-extension": ("action", "Existing bridges: re-create or retire. During the migration this extension can bridge HiveMQ to ConnectMQ (see the executor skill)."),
    "hivemq-enterprise-bridge-extension": ("action", "Existing bridges: re-create or retire. During the migration this extension can bridge HiveMQ to ConnectMQ (see the executor skill)."),
    "hivemq-kafka-extension": ("blocker", "No Kafka integration on ConnectMQ. Run a consumer service that subscribes (shared subscription on Basic/Scale) and produces to Kafka."),
    "hivemq-postgresql-extension": ("blocker", "No database integration. Replace with a subscriber service that writes to the database."),
    "hivemq-mysql-extension": ("blocker", "No database integration. Replace with a subscriber service that writes to the database."),
    "hivemq-mongodb-extension": ("blocker", "No database integration. Replace with a subscriber service that writes to MongoDB."),
    "hivemq-amazon-kinesis-extension": ("blocker", "No Kinesis integration. Replace with a subscriber service."),
    "hivemq-google-cloud-pubsub-extension": ("blocker", "No Pub/Sub integration. Replace with a subscriber service."),
    "hivemq-prometheus-extension": ("info", "HiveMQ metrics stop at cutover; use the ConnectMQ console dashboard, sessions view and usage meters."),
    "hivemq-mqtt-message-log-extension": ("info", "Message logging: use the ConnectMQ topic monitor, or a subscriber that logs."),
    "hivemq-deny-wildcard-extension": ("info", "Wildcard restrictions: express them as ConnectMQ ACL rules (deny subscribe on '#')."),
    "hivemq-sparkplug-aware-extension": ("check", "Sparkplug topics work as plain MQTT on ConnectMQ; Sparkplug-aware features (system topics, state handling) are not provided."),
}

DEFAULT_EXT = ("blocker", "Custom or unknown extension: extensions do not run on ConnectMQ. Review what it does and move the logic into a client-side service.")


def strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def text(el: ET.Element | None, path: str, default: str | None = None) -> str | None:
    if el is None:
        return default
    found = el.find(path)
    return found.text.strip() if found is not None and found.text else default


def as_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def audit_config(path: str, findings: dict) -> dict:
    root = strip_ns(ET.parse(path).getroot())
    facts = {}
    add = lambda sev, msg: findings[sev].append(msg)  # noqa: E731

    listeners = root.find("listeners")
    for lst in list(listeners) if listeners is not None else []:
        port = text(lst, "port", "?")
        name = text(lst, "name")
        label = f"`<{lst.tag}>` port {port}" + (f" ({name})" if name else "")
        if lst.tag == "tcp-listener":
            add("info", f"{label}: ConnectMQ plain MQTT is 1883 for tests only; production clients move to 8883 (TLS).")
        elif lst.tag == "tls-tcp-listener":
            mode = (text(lst, "tls/client-authentication-mode", "NONE") or "NONE").upper()
            if mode == "REQUIRED":
                add("blocker", f"{label}: client-authentication-mode REQUIRED (mTLS). ConnectMQ has no client-certificate authentication: devices move to username/password over TLS.")
            elif mode == "OPTIONAL":
                add("check", f"{label}: client certificates OPTIONAL. Find clients that authenticate with a certificate; they need credentials.")
            else:
                add("info", f"{label}: -> mqtts://HOST:8883. ConnectMQ uses a public CA certificate; clients can use the OS CA store.")
        elif lst.tag in ("websocket-listener", "tls-websocket-listener"):
            path_ = text(lst, "path", "/mqtt")
            note = "" if path_ == "/mqtt" else f" Path changes from {path_} to /mqtt."
            add("info" if path_ == "/mqtt" else "action",
                f"{label}: -> wss://HOST:8084/mqtt.{note}")
        else:
            add("unknown", f"{label}: listener type not recognised; review.")

    mqtt = root.find("mqtt")
    if mqtt is not None:
        se = as_int(text(mqtt, "session-expiry/max-interval"))
        if se is not None:
            facts["session_expiry"] = se
            add("check", f"`session-expiry/max-interval` {se} s: ConnectMQ caps session expiry per plan (Free 1 h, Basic 2 h, Scale 24 h). Devices offline longer lose queued messages.")
        me = text(mqtt, "message-expiry/max-interval")
        if me:
            add("info", f"`message-expiry/max-interval` {me}: MQTT 5 message expiry is supported; queued messages also end with the session.")
        q = text(mqtt, "queued-messages/max-queue-size")
        if q:
            strategy = text(mqtt, "queued-messages/strategy", "discard")
            add("check", f"`queued-messages/max-queue-size` {q} (strategy {strategy}): offline queueing on ConnectMQ is broker-managed and limited by the plan's session expiry.")
        pkt = as_int(text(mqtt, "packets/max-packet-size"))
        if pkt is not None:
            add("check", f"`packets/max-packet-size` {pkt} bytes: plan max message size is 256 KB on Free/Basic. Find the largest payload on HiveMQ.")
        ka = as_int(text(mqtt, "keep-alive/max-keep-alive"))
        unlimited = (text(mqtt, "keep-alive/allow-unlimited", "") or "").lower() == "true"
        if ka is not None or unlimited:
            add("check" if (unlimited or (ka or 0) > 600) else "info",
                f"`keep-alive` max {ka}{', unlimited allowed' if unlimited else ''}: ConnectMQ caps keep-alive at 600 s; clients with keep-alive 0 or > 600 s get the cap.")
        if (text(mqtt, "topic-alias/enabled", "") or "").lower() == "true":
            add("info", f"`topic-alias` enabled (max {text(mqtt, 'topic-alias/max-per-client', '?')} per client): MQTT 5 topic aliases are supported.")
        if (text(mqtt, "shared-subscriptions/enabled", "true") or "").lower() == "true":
            add("check", "Shared subscriptions enabled: available on ConnectMQ Basic and Scale only. Check which consumers use $share/.")
        if (text(mqtt, "retained-messages/enabled", "true") or "").lower() == "true":
            add("check", "Retained messages enabled: count retained topics; the plan caps them (100 / 1,000 / 10,000). Copy them with copy_retained.py.")
        rm = text(mqtt, "receive-maximum/server-receive-maximum")
        if rm:
            add("info", f"`server-receive-maximum` {rm}: ConnectMQ sets receive-maximum per plan; no client change needed.")
        qos = text(mqtt, "quality-of-service/max-qos")
        if qos:
            add("info", f"`max-qos` {qos}: ConnectMQ supports QoS 0, 1 and 2 on every plan.")

    restrictions = root.find("restrictions")
    if restrictions is not None:
        mc = as_int(text(restrictions, "max-connections"))
        if mc is not None:
            add("check", f"`max-connections` {'unlimited' if mc < 0 else mc}: compare peak connections with the plan (Free 5, Basic 100, Scale 1,000).")
        cid = text(restrictions, "max-client-id-length")
        if cid:
            add("info", f"`max-client-id-length` {cid}: new client IDs are `<ISSUED_ID>_<old-id>`; check the longest old ID still fits device SDK limits.")
        bw = as_int(text(restrictions, "incoming-bandwidth-throttling"))
        if bw:
            add("check", f"`incoming-bandwidth-throttling` {bw} B/s: ConnectMQ limits messages per second per client and monthly traffic per plan.")

    security = root.find("security")
    if security is not None and (text(security, "allow-empty-client-id/enabled", "") or "").lower() == "true":
        add("action", "`allow-empty-client-id` enabled: ConnectMQ does not assign client IDs; every client must send `<ISSUED_ID>_<suffix>`.")

    cluster = root.find("cluster")
    if cluster is not None and (text(cluster, "enabled", "false") or "").lower() == "true":
        add("info", "HiveMQ cluster enabled: ConnectMQ runs and scales the broker for you; no cluster, discovery or replication settings to migrate.")
    if root.find("control-center") is not None:
        add("info", "Control Center: replaced by the ConnectMQ console (dashboard, topic monitor, sessions, rules and alerts).")
    rest = root.find("rest-api")
    if rest is not None and (text(rest, "enabled", "false") or "").lower() == "true":
        add("info", "REST API enabled: use it now for inventory (GET /api/v1/mqtt/clients). ConnectMQ has no equivalent admin REST API for clients.")
    if root.find("persistence") is not None:
        add("info", "Persistence settings: managed by ConnectMQ. Retained messages are copied with copy_retained.py; sessions are not copied.")
    return facts


def audit_extensions(path: str, findings: dict) -> list[str]:
    found = []
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        if not os.path.isdir(full):
            continue
        disabled = os.path.exists(os.path.join(full, "DISABLED"))
        key = next((k for k in EXTENSIONS if name.startswith(k)), None)
        sev, msg = EXTENSIONS.get(key, DEFAULT_EXT) if key else DEFAULT_EXT
        if disabled:
            findings["info"].append(f"Extension `{name}` (disabled): nothing to migrate.")
            continue
        found.append(name)
        findings[sev].append(f"Extension `{name}`: {msg}")
    return found


ACTIVITY = {"PUBLISH": "publish", "SUBSCRIBE": "subscribe", "ALL": "all"}


def placeholder(topic: str) -> str:
    return re.sub(r"\$\{\{\s*(clientid|username)\s*\}\}", lambda m: "${" + m.group(1).lower() + "}", topic,
                  flags=re.IGNORECASE)


def rbac_to_rules(path: str, user_map: dict, client_map: dict, notes: list) -> tuple[list[Rule], list[str]]:
    root = strip_ns(ET.parse(path).getroot())
    roles = {}
    for role in root.findall("roles/role"):
        rid = text(role, "id")
        roles[rid] = role.findall("permissions/permission")
    users = []
    rules: list[Rule] = []
    restricted = set()
    for user in root.findall("users/user"):
        name = text(user, "name", "")
        users.append(name)
        new_user = user_map.get(name)
        role_ids = [r.text.strip() for r in user.findall("roles/id") if r.text]
        for rid in role_ids:
            if rid not in roles:
                notes.append(f"user {name}: role '{rid}' is not defined in credentials.xml.")
                continue
            for perm in roles[rid]:
                topic = placeholder(text(perm, "topic", "#"))
                activity = (text(perm, "activity", "ALL") or "ALL").upper()
                action = ACTIVITY.get(activity)
                if not action:
                    notes.append(f"role {rid}: unknown activity {activity} on '{topic}' skipped.")
                    continue
                warnings = []
                for field in ("qos", "retained-messages", "shared-subscription"):
                    value = text(perm, field)
                    if value and value.upper() != "ALL":
                        warnings.append(f"{field}={value} restriction has no ConnectMQ equivalent")
                        restricted.add(rid)
                if text(perm, "shared-group"):
                    warnings.append(f"shared-group={text(perm, 'shared-group')} restriction has no ConnectMQ equivalent")
                # The rule belongs to this user, so ${username} can become the literal OLD username: topics keep
                # their current shape and the subject is the new ConnectMQ username
                topic = re.sub(r"\$\{username\}", lambda _m, v=name: v, topic, flags=re.IGNORECASE)
                if not new_user:
                    warnings.append(f"old username '{name}': replace with its ConnectMQ username")
                rule = Rule("allow", action, topic, "username", new_user or name,
                            f"user {name}: role {rid} {activity} {text(perm, 'topic', '#')}", warnings)
                if "${clientid}" in topic.lower():
                    if client_map:
                        expanded = expand_pattern(rule, client_map, "${clientid}")
                        for r in expanded:  # subject is now the client ID, so the username warning does not apply
                            r.warnings = [w for w in warnings if not w.startswith("old username")]
                        rules.extend(expanded)
                        continue
                    rule.warnings.append("${clientid} is now the full ConnectMQ ID (<ISSUED>_<old>); pass --client-map "
                                         "to keep old IDs in topics")
                rules.append(rule)
    if restricted:
        notes.append(f"Roles {', '.join(sorted(restricted))} restrict QoS, retain or shared subscriptions per permission. "
                     "ConnectMQ rules only cover topic + action: enforce those restrictions in the clients.")
    notes.append("HiveMQ File RBAC only grants (no deny rules) and denies anything not granted: use no-match policy 'deny'.")
    notes.append("Subscribe checks: ConnectMQ allows a subscription only when a rule's filter covers the whole requested "
                 "filter. Clients that subscribe to broader filters than their permission (e.g. '#') are refused.")
    return dedupe(rules), users


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="HiveMQ conf/config.xml")
    ap.add_argument("--extensions", help="HiveMQ extensions folder")
    ap.add_argument("--rbac", help="File RBAC extension credentials.xml (default: found inside --extensions)")
    ap.add_argument("--client-map", help="CSV old_client_id,new_client_id")
    ap.add_argument("--user-map", help="CSV old_username,new_username")
    ap.add_argument("--out", default="migration", help="Output directory (default ./migration)")
    args = ap.parse_args()
    if not (args.config or args.extensions or args.rbac):
        ap.error("give --config, --extensions or --rbac")
    os.makedirs(args.out, exist_ok=True)

    client_map = load_map(args.client_map, "old_client_id", "new_client_id")
    user_map = {k: v for k, v in load_map(args.user_map, "old_username", "new_username").items() if v}
    findings = {s: [] for s in SEVERITIES}

    if args.config:
        audit_config(args.config, findings)
    extensions = audit_extensions(args.extensions, findings) if args.extensions else []

    rbac = args.rbac
    if not rbac and args.extensions:
        for name in extensions:
            if name.startswith("hivemq-file-rbac-extension"):
                candidate = os.path.join(args.extensions, name, "conf", "credentials.xml")
                if os.path.exists(candidate):
                    rbac = candidate

    notes: list[str] = []
    rules: list[Rule] = []
    users: list[str] = []
    if rbac:
        rules, users = rbac_to_rules(rbac, user_map, client_map, notes)
    problems = check(rules)
    if rules:
        for fmt, ext in (("markdown", "md"), ("csv", "csv"), ("json", "json")):
            with open(os.path.join(args.out, f"acl-rules.{ext}"), "w", encoding="utf-8", newline="") as f:
                f.write(render(rules, "deny", fmt, notes + problems))
    if users:
        with open(os.path.join(args.out, "users.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["old_username", "new_username", "credential_scope"])
            for u in sorted(set(users)):
                w.writerow([u, user_map.get(u, ""), ""])

    lines = ["# HiveMQ -> ConnectMQ audit", ""]
    if args.config:
        lines.append(f"Configuration: `{args.config}`")
    if args.extensions:
        lines.append(f"Extensions folder: `{args.extensions}` ({len(extensions)} enabled)")
    lines += [f"File RBAC: `{rbac or '-'}` ({len(set(users))} users)", ""]
    titles = {"blocker": "Blockers (must be redesigned)", "action": "Actions (change during migration)",
              "check": "Check against the ConnectMQ plan", "info": "Information", "unknown": "Not recognised (review)"}
    for sev in SEVERITIES:
        if findings[sev]:
            lines += [f"## {titles[sev]}", ""] + [f"- {x}" for x in findings[sev]] + [""]
    lines += ["## Access control", "",
              f"{len(rules)} ConnectMQ rules generated -> `acl-rules.md`." if rules else
              "No File RBAC permissions found (ESE, allow-all or HiveMQ Cloud: build the rule table by hand).", ""]
    if notes or problems:
        lines += ["### Notes", ""] + [f"- {n}" for n in notes + problems] + [""]
    lines += ["## Next steps", "",
              "1. Fill `users.csv` new_username after issuing ConnectMQ credentials, then re-run with --user-map.",
              "2. Export client IDs (REST API GET /api/v1/mqtt/clients or Control Center) and build the client map: "
              "`python client_id_map.py clients.csv --issued-id <ID> -o client-map.csv`.",
              "3. Collect peak connections, message rates, retained count and largest payload for 24 h to size the plan.",
              "4. Resolve every blocker before scheduling the first wave.", ""]
    with open(os.path.join(args.out, "audit.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"wrote {args.out}/audit.md ({sum(len(v) for v in findings.values())} findings, "
          f"{len(findings['blocker'])} blockers), {len(rules)} ACL rules, {len(set(users))} users")
    return 0


if __name__ == "__main__":
    sys.exit(main())
